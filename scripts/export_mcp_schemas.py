"""Export public tool contracts without connecting to a database or reading secrets."""

import json
import secrets
import tempfile
from pathlib import Path

from atlas_mcp.catalog import GROUPS
from atlas_mcp.server import MCPSettings, make_gateway
from pydantic import SecretStr


def export(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temporary:
        for group in GROUPS:
            gateway = make_gateway(
                group,
                MCPSettings(
                    access_token=SecretStr(secrets.token_urlsafe(32)),
                    reader_url=SecretStr("postgresql://observer@127.0.0.1/unconfigured"),
                    audit_dir=Path(temporary) / "audit",
                ),
            )
            payload = {
                "contract_version": 1,
                "server": group,
                "tools": [
                    tool.model_dump(mode="json", by_alias=True, exclude_none=True)
                    for tool in gateway.tools()
                ],
            }
            (directory / f"{group}-v1.json").write_text(json.dumps(payload, indent=2) + "\n")


if __name__ == "__main__":
    export(Path("schemas/mcp"))
