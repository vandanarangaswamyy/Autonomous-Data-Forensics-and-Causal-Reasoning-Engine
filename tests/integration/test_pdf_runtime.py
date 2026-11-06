"""Actual isolated Linux parser; timeout test uses an explicitly labeled attach pause seam."""

import asyncio
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from atlas_contracts.documents import IngestionError
from atlas_contracts.extraction import ExtractionConfig
from atlas_documents.extraction import SourceExtractor, pdf_pages

pytestmark = pytest.mark.integration
ROOT = Path("evaluation/nlp/source-v1")


@pytest.fixture(autouse=True)
def require_parser() -> None:
    if os.environ.get("ATLAS_TEST_PDF_PARSER") != "1":
        pytest.skip("Set ATLAS_TEST_PDF_PARSER=1 after make pdf-parser-build")


@pytest.mark.parametrize(
    "config,code",
    [(ExtractionConfig(max_pages=1), "page_limit"), (ExtractionConfig(max_chars=2), "text_limit")],
)
async def test_actual_pdf_limits(config: ExtractionConfig, code: str) -> None:
    content = await asyncio.to_thread(ROOT.joinpath("retail.pdf").read_bytes)
    with pytest.raises(IngestionError) as error:
        await SourceExtractor().extract(uuid4(), 1, content, "application/pdf", config)
    assert error.value.code == code


async def test_parser_cancellation_reaps_container_and_filters_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = asyncio.create_subprocess_exec
    launched: list[asyncio.subprocess.Process] = []
    names: list[str] = []
    entered = asyncio.Event()

    async def paused(*args: str, **kwargs: Any) -> asyncio.subprocess.Process:
        # Explicit application launcher seam: keep the actual restricted container alive
        # on a valid long-lived process so cancellation can be deterministically exercised.
        if "run" in args:
            values = list(args)
            index = values.index("atlas-pdf-parser:6.21.4")
            # Override entrypoint for this cleanup test only, using the same resource flags.
            values[index:index] = ["--entrypoint", "sleep"]
            values[-1] = "30"
            env = kwargs["env"]
            assert isinstance(env, dict) and set(env) == {"HOME", "PATH"}
            assert "--network" in args and "none" in args and "--memory" in args
            process = await original(*values, **kwargs)
            launched.append(process)
            names.append(values[values.index("--name") + 1])
            entered.set()
            return process
        return await original(*args, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", paused)
    task = asyncio.create_task(pdf_pages(b"fixture", ExtractionConfig()))
    await asyncio.wait_for(entered.wait(), 5)
    await asyncio.sleep(0.1)
    task.cancel()
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert launched[0].returncode is not None
    process = await original(
        "docker",
        "ps",
        "--all",
        "--filter",
        f"name=^/{names[0]}$",
        "--format",
        "{{.ID}}",
        stdout=asyncio.subprocess.PIPE,
    )
    output, _ = await process.communicate()
    assert process.returncode == 0 and not output.strip()


async def test_actual_pdf_deadline_returns_safe_timeout() -> None:
    # 50 ms includes cold container startup; regardless of hardware this must return
    # success or a safe bounded timeout. Dedicated cancellation seam above tests cleanup.
    content = await asyncio.to_thread(ROOT.joinpath("incident.pdf").read_bytes)
    started = asyncio.get_running_loop().time()
    try:
        await pdf_pages(content, ExtractionConfig(timeout_seconds=0.05))
    except IngestionError as exc:
        assert exc.code == "parser_timeout" and "Traceback" not in exc.message
    assert asyncio.get_running_loop().time() - started < 12
