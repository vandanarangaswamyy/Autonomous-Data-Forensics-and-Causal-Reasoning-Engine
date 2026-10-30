import json
from pathlib import Path
from uuid import uuid4

import pytest
from atlas_contracts.tools import ToolReply
from atlas_mcp.catalog import GROUPS
from atlas_mcp.server import MCPSettings, make_gateway
from pydantic import SecretStr, ValidationError


@pytest.mark.parametrize("group", tuple(GROUPS))
def test_checked_in_tool_contracts_and_explicit_permissions(tmp_path: Path, group: str) -> None:
    settings = MCPSettings(
        access_token=SecretStr("schema-test-token-" + "x" * 32),
        reader_url=SecretStr("postgresql://observer@127.0.0.1/unconfigured"),
        audit_dir=tmp_path / "audit",
    )
    gateway = make_gateway(group, settings)
    payload = json.loads(Path(f"schemas/mcp/{group}-v1.json").read_text())
    assert payload["contract_version"] == 1 and payload["server"] == group
    assert payload["tools"] == [
        tool.model_dump(mode="json", by_alias=True, exclude_none=True) for tool in gateway.tools()
    ]
    assert all(tool.meta and "atlas_permission" in tool.meta for tool in gateway.tools())


def test_reply_consistency() -> None:
    with pytest.raises(ValidationError):
        ToolReply(status="ok", investigation_id=uuid4(), request_id=uuid4(), trace_id=uuid4())


async def test_public_document_search_does_not_index_oracle(tmp_path: Path) -> None:
    from atlas_mcp.security import Permission

    token = "corpus-test-token-" + "x" * 32
    gateway = make_gateway(
        "knowledge",
        MCPSettings(
            access_token=SecretStr(token),
            permissions=frozenset({Permission.KNOWLEDGE}),
            audit_dir=tmp_path / "audit",
        ),
    )
    for query in ["ground_truth", "missing_partitions", "root_cause"]:
        result = await gateway.invoke(
            "search_technical_documents",
            {
                "context": {"investigation_id": str(uuid4()), "request_id": str(uuid4())},
                "query": query,
            },
            token,
        )
        assert result.status == "ok" and result.result and not result.result["matches"]
