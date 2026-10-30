import asyncio
import hashlib
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import uuid4

import jsonschema
from atlas_contracts.analytics import AnalyticsError
from atlas_contracts.tools import RequestContext, ToolError, ToolReply
from mcp import types
from pydantic import ValidationError

from atlas_mcp.audit import AuditLog
from atlas_mcp.security import Authorizer, Permission


class ToolTransport(Protocol):
    """A remote implementation must authenticate its endpoint outside MCP tool arguments."""

    async def list_tools(self) -> list[types.Tool]: ...
    async def call_tool(self, name: str, arguments: dict[str, Any]) -> types.CallToolResult: ...


@dataclass(frozen=True)
class ToolSpec:
    name: str
    permission: Permission
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    execute: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
    row_reservation: int = 1000

    def descriptor(self) -> types.Tool:
        return types.Tool(
            name=self.name,
            description=f"Authorized {self.permission.value} operation",
            inputSchema=self.input_schema,
            outputSchema=self.output_schema,
            annotations=types.ToolAnnotations(
                readOnlyHint=True, destructiveHint=False, openWorldHint=False
            ),
            _meta={"atlas_permission": self.permission.value},
        )


REMOTE_ERROR_CODES = frozenset(
    {
        "unauthorized",
        "permission_denied",
        "unknown_tool",
        "invalid_input",
        "invalid_output",
        "sql_denied",
        "table_denied",
        "column_denied",
        "invalid_parameters",
        "query_timeout",
        "tool_timeout",
        "budget_exceeded",
        "concurrency_limited",
        "duplicate_request",
        "not_found",
        "database_error",
        "unsupported_capability",
        "backend_unavailable",
        "unsafe_role",
        "readonly_role_unverified",
        "driver_unavailable",
        "result_too_large",
        "audit_unavailable",
        "transport_error",
        "cancelled",
        "shutting_down",
        "internal_error",
    }
)


class Gateway:
    def __init__(self, authorizer: Authorizer, audit: AuditLog) -> None:
        self.authorizer, self.audit = authorizer, audit
        self.registry: dict[str, ToolSpec] = {}
        self._semaphore = asyncio.Semaphore(audit.budget.concurrency)
        self._tasks: set[asyncio.Task[Any]] = set()
        self._closed = False

    @staticmethod
    def _check_schema(schema: dict[str, Any]) -> None:
        if len(json.dumps(schema).encode()) > 128000:
            raise ValueError("Tool schema exceeds registry limits")

        def local_references(value: Any) -> None:
            if isinstance(value, dict):
                if (
                    "$schema" in value
                    and value["$schema"] != "https://json-schema.org/draft/2020-12/schema"
                ):
                    raise ValueError("Only JSON Schema 2020-12 is supported")
                if "$id" in value:
                    raise ValueError("Tool schemas cannot change reference resolution scope")
                for key, item in value.items():
                    if key in {"$ref", "$dynamicRef", "$recursiveRef"} and (
                        not isinstance(item, str) or not item.startswith("#")
                    ):
                        raise ValueError("Only local schema references are permitted")
                    local_references(item)
            elif isinstance(value, list):
                for item in value:
                    local_references(item)

        local_references(schema)
        jsonschema.Draft202012Validator.check_schema(schema)

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self.registry:
            raise ValueError(f"Duplicate tool: {spec.name}")
        self._check_schema(spec.input_schema)
        self._check_schema(spec.output_schema)
        self.registry[spec.name] = spec

    async def discover(
        self, namespace: str, transport: ToolTransport, permissions: dict[str, Permission]
    ) -> list[str]:
        """Only operator-approved names receive permissions; server hints cannot grant them."""
        names: list[str] = []
        for tool in await transport.list_tools():
            if tool.name not in permissions or tool.outputSchema is None:
                continue

            output_schema = dict(tool.outputSchema)

            async def execute(
                arguments: dict[str, Any],
                tool_name: str = tool.name,
                schema: dict[str, Any] = output_schema,
            ) -> dict[str, Any]:
                response = await transport.call_tool(tool_name, arguments)
                payload = response.structuredContent
                if not payload:
                    raise AnalyticsError(
                        "transport_error", "Remote tool omitted structured output", True
                    )
                try:
                    if len(json.dumps(payload).encode()) > 1_000_000:
                        raise AnalyticsError(
                            "result_too_large", "Remote output exceeds response budget"
                        )
                    jsonschema.Draft202012Validator(schema).validate(payload)
                    reply = ToolReply.model_validate(payload)
                except (ValidationError, jsonschema.ValidationError) as exc:
                    raise AnalyticsError(
                        "transport_error", "Remote output violates its contract"
                    ) from exc
                expected = RequestContext.model_validate(arguments["context"])
                if (
                    reply.request_id != expected.request_id
                    or reply.investigation_id != expected.investigation_id
                ):
                    raise AnalyticsError("transport_error", "Remote request correlation mismatch")
                if response.isError or reply.status == "error":
                    error = reply.error or ToolError(
                        code="remote_error", message="Remote tool failed"
                    )
                    code = error.code if error.code in REMOTE_ERROR_CODES else "remote_error"
                    raise AnalyticsError(code, "Remote tool reported a failure", error.retryable)
                return reply.result or {}

            name = f"{namespace}.{tool.name}"
            self.register(
                ToolSpec(
                    name=name,
                    permission=permissions[tool.name],
                    input_schema=tool.inputSchema,
                    output_schema=tool.outputSchema,
                    execute=execute,
                )
            )
            names.append(name)
        return names

    def tools(self) -> list[types.Tool]:
        return [spec.descriptor() for spec in self.registry.values()]

    async def invoke(self, name: str, arguments: dict[str, Any], token: str | None) -> ToolReply:
        trace_id, fallback_request = uuid4(), uuid4()
        context: RequestContext | None = None
        principal_id: str | None = None
        started = time.monotonic()
        reserved = False
        rows = 0
        status = "error"
        validating_output = False
        error: ToolError | None = None
        result: dict[str, Any] | None = None
        task = asyncio.current_task()
        if task:
            self._tasks.add(task)
        base: dict[str, Any] = {
            "trace_id": str(trace_id),
            "tool": name if name in self.registry else "unregistered",
            "requested_tool_sha256": hashlib.sha256(name.encode()).hexdigest(),
        }
        try:
            context = RequestContext.model_validate(arguments.get("context"))
            deadline = started + context.timeout_ms / 1000
            principal = self.authorizer.authenticate(token)
            principal_id = principal.id
            if self._closed:
                raise AnalyticsError("shutting_down", "Gateway is shutting down", True)
            spec = self.registry.get(name)
            if spec is None:
                raise AnalyticsError("unknown_tool", "Tool is not registered")
            if spec.permission not in principal.permissions:
                raise AnalyticsError(
                    "permission_denied", "Principal lacks the required tool permission"
                )
            if len(json.dumps(arguments, default=str).encode()) > 64000:
                raise AnalyticsError("invalid_input", "Tool input exceeds its size limit")
            jsonschema.Draft202012Validator(spec.input_schema).validate(arguments)
            digest = hashlib.sha256(
                json.dumps(arguments, sort_keys=True, default=str).encode()
            ).hexdigest()
            base.update(
                investigation_id=str(context.investigation_id),
                request_id=str(context.request_id),
                principal=principal_id,
                input_sha256=digest,
            )
            row_reservation = spec.row_reservation
            if isinstance(arguments.get("query"), dict):
                row_reservation = min(
                    row_reservation, int(arguments["query"].get("row_limit", 100))
                )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            async with asyncio.timeout(remaining):
                async with self._semaphore:
                    # File operations are bounded and locked. Reservation completes before
                    # execution and cannot outlive a cancelled background thread.
                    self.audit.reserve(base, context.timeout_ms, row_reservation)
                    reserved = True
                    if time.monotonic() >= deadline:
                        raise TimeoutError
                    validating_output = True
                    result = await spec.execute(arguments)
                    result = json.loads(json.dumps(result, allow_nan=False))
                    if len(json.dumps(result).encode()) > 1_000_000:
                        raise AnalyticsError(
                            "result_too_large", "Tool result exceeds response budget"
                        )
                    rows = next(
                        (
                            len(result[key])
                            for key in (
                                "rows",
                                "edges",
                                "executions",
                                "incidents",
                                "matches",
                                "tables",
                            )
                            if isinstance(result.get(key), list)
                        ),
                        1,
                    )
                    if rows > row_reservation:
                        raise AnalyticsError(
                            "result_too_large", "Tool exceeded reserved result rows"
                        )
                    provisional = ToolReply(
                        status="ok",
                        investigation_id=context.investigation_id,
                        request_id=context.request_id,
                        trace_id=trace_id,
                        result=result,
                    )
                    jsonschema.Draft202012Validator(spec.output_schema).validate(
                        provisional.model_dump(mode="json")
                    )
                    if time.monotonic() >= deadline:
                        raise TimeoutError
                    status = "ok"
        except (ValidationError, jsonschema.ValidationError, ValueError, TypeError):
            error = ToolError(
                code="invalid_output" if validating_output else "invalid_input",
                message="Tool output validation failed"
                if validating_output
                else "Tool input validation failed",
            )
        except AnalyticsError as exc:
            error = ToolError(code=exc.code, message=exc.message, retryable=exc.retryable)
        except TimeoutError:
            error = ToolError(
                code="tool_timeout", message="Tool request exceeded its deadline", retryable=True
            )
        except asyncio.CancelledError:
            error = ToolError(code="cancelled", message="Tool request cancelled", retryable=True)
            raise
        except Exception:
            error = ToolError(code="internal_error", message="Tool execution failed")
        finally:
            base.update(
                investigation_id=str(context.investigation_id) if context else None,
                request_id=str(context.request_id) if context else str(fallback_request),
                principal=principal_id,
                event="finished" if reserved else "rejected",
                status=status,
                error_code=error.code if error else None,
                elapsed_ms=round((time.monotonic() - started) * 1000),
                rows=rows if status == "ok" else 0,
                result_sha256=(
                    hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()
                    if status == "ok"
                    else None
                ),
            )
            try:
                self.audit.append(base)
            except Exception:
                # Fail closed: never return successful output when final auditing failed.
                status = "error"
                error = ToolError(
                    code="audit_unavailable", message="Execution audit could not be persisted"
                )
            if task:
                self._tasks.discard(task)
        return ToolReply(
            status=status,
            investigation_id=context.investigation_id if context else None,
            request_id=context.request_id if context else fallback_request,
            trace_id=trace_id,
            result=result if status == "ok" else None,
            error=error,
        )

    async def close(self) -> None:
        self._closed = True
        tasks = [task for task in self._tasks if task is not asyncio.current_task()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
