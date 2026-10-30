"""Read-only application services depend only on the AnalyticsBackend protocol."""

import re
from collections import Counter
from pathlib import Path
from typing import Protocol

from atlas_contracts.analytics import AnalyticsError, QueryRequest, TableRef
from atlas_contracts.tools import (
    DocumentMatch,
    DocumentResult,
    Incident,
    IncidentInput,
    IncidentResult,
    LineageEdge,
    LineageInput,
    LineageResult,
    PipelineExecution,
    PipelineInput,
    PipelineResult,
)
from atlas_core.analytics import AnalyticsBackend


class KnowledgeSearch(Protocol):
    async def search(self, query: str, limit: int) -> DocumentResult: ...


class PublicDocumentSearch:
    """Small real lexical index over an explicit corpus; no evaluator paths or vector claims."""

    def __init__(self, documents: dict[str, Path]) -> None:
        self._documents: list[tuple[str, str, str, Counter[str]]] = []
        for document_id, path in documents.items():
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 65536:
                raise ValueError("Public documents must be regular, bounded files")
            content = path.read_text()
            title = content.splitlines()[0].lstrip("# ")
            self._documents.append((document_id, title, content, Counter(self._terms(content))))
        if len(self._documents) > 100:
            raise ValueError("Public corpus exceeds local index limit")

    @staticmethod
    def _terms(text: str) -> list[str]:
        return re.findall(r"[a-z0-9_]+", text.lower())

    async def search(self, query: str, limit: int) -> DocumentResult:
        terms = set(self._terms(query))
        matches: list[DocumentMatch] = []
        for document_id, title, content, tokens in self._documents:
            score = sum(tokens[term] / (1 + sum(tokens.values())) for term in terms)
            if score:
                matches.append(
                    DocumentMatch(
                        document_id=document_id,
                        title=title,
                        source_uri=f"atlas-public://technical/{document_id}",
                        excerpt=content[:800],
                        score=score,
                    )
                )
        return DocumentResult(
            matches=sorted(matches, key=lambda match: (-match.score, match.document_id))[:limit]
        )


class EvidenceServices:
    def __init__(self, backend: AnalyticsBackend | None, knowledge: KnowledgeSearch) -> None:
        self._backend, self.knowledge = backend, knowledge

    @property
    def backend(self) -> AnalyticsBackend:
        if self._backend is None:
            raise AnalyticsError("backend_unavailable", "This server has no analytics backend")
        return self._backend

    async def lineage(self, request: LineageInput, upstream: bool) -> LineageResult:
        await self.backend.get_table_metadata(TableRef(table=request.dataset))
        query = QueryRequest(
            sql="""
            SELECT pipeline_id, source_dataset_id, destination_dataset_id,
                   source_column, destination_column, expression
            FROM column_lineage ORDER BY id
        """,
            row_limit=1000,
            timeout_ms=min(3000, request.context.timeout_ms),
        )
        raw = await self.backend.execute_readonly_query(query)
        edges = [
            LineageEdge(
                pipeline_id=str(p),
                source_dataset=str(s),
                destination_dataset=str(d),
                source_column=str(sc),
                destination_column=str(dc),
                expression=str(e),
            )
            for p, s, d, sc, dc, e in raw.rows
        ]
        frontier = {(request.dataset, request.column)}
        visited = set(frontier)
        selected: dict[str, LineageEdge] = {}
        truncated = raw.truncated
        for _ in range(request.max_depth):
            following: set[tuple[str, str | None]] = set()
            for dataset, column in frontier:
                for edge in edges:
                    target, target_column = (
                        (edge.destination_dataset, edge.destination_column)
                        if upstream
                        else (edge.source_dataset, edge.source_column)
                    )
                    if target != dataset or (column is not None and target_column != column):
                        continue
                    selected[edge.model_dump_json()] = edge
                    next_node = (
                        (edge.source_dataset, edge.source_column if column else None)
                        if upstream
                        else (edge.destination_dataset, edge.destination_column if column else None)
                    )
                    if next_node not in visited:
                        following.add(next_node)
            visited.update(following)
            if len(selected) > 300 or len(visited) > 100:
                truncated = True
                break
            frontier = following
            if not frontier:
                break
        if frontier:
            # Depth cap may leave more reachable edges. Report conservative truncation.
            truncated = True
        return LineageResult(edges=list(selected.values())[:300], truncated=truncated)

    async def pipeline(self, request: PipelineInput) -> PipelineResult:
        sql = """SELECT id, pipeline_id, schema_version_id, started_at, finished_at,
                        status, input_rows, output_rows, quality
                 FROM pipeline_runs WHERE pipeline_id = :pipeline_id"""
        parameters: dict[str, str] = {"pipeline_id": request.pipeline_id}
        if request.execution_id:
            sql += " AND id = :execution_id"
            parameters["execution_id"] = str(request.execution_id)
        sql += " ORDER BY started_at DESC, id"
        result = await self.backend.execute_readonly_query(
            QueryRequest(
                sql=sql,
                parameters=parameters,
                timeout_ms=min(3000, request.context.timeout_ms),
                row_limit=request.limit,
            )
        )
        if not result.rows:
            raise AnalyticsError("not_found", "Pipeline execution was not found")
        rows = [
            PipelineExecution.model_validate(dict(zip(result.columns, row, strict=True)))
            for row in result.rows
        ]
        return PipelineResult(executions=rows, truncated=result.truncated)

    async def incidents(self, request: IncidentInput) -> IncidentResult:
        pattern = (
            "%"
            + request.query.lower().replace("!", "!!").replace("%", "!%").replace("_", "!_")
            + "%"
        )
        sql = """SELECT id, created_at AS occurred_at, pipeline_id, severity, code, details
                 FROM operational_events WHERE LOWER(code) LIKE :pattern ESCAPE '!'"""
        parameters: dict[str, str] = {"pattern": pattern}
        if request.severity:
            sql += " AND severity = :severity"
            parameters["severity"] = request.severity
        sql += " ORDER BY created_at DESC, id"
        result = await self.backend.execute_readonly_query(
            QueryRequest(
                sql=sql,
                parameters=parameters,
                timeout_ms=min(3000, request.context.timeout_ms),
                row_limit=request.limit,
            )
        )
        return IncidentResult(
            incidents=[
                Incident.model_validate(dict(zip(result.columns, row, strict=True)))
                for row in result.rows
            ],
            truncated=result.truncated,
        )
