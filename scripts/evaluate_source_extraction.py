"""Evaluate source-v1 authored PDF/page/mapping labels. No entity/relationship predictions."""

import asyncio
import hashlib
import json
import platform
import statistics
import subprocess
import time
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from uuid import UUID

from atlas_contracts.documents import IngestionError
from atlas_contracts.extraction import ExtractionConfig, normalize
from atlas_documents.extraction import SourceExtractor

ROOT = Path("evaluation/nlp/source-v1")


async def evaluate() -> dict[str, object]:
    raw_labels = ROOT.joinpath("labels.json").read_bytes()
    labels = json.loads(raw_labels)
    digest = hashlib.sha256(raw_labels)
    for path in await asyncio.to_thread(lambda: sorted(ROOT.glob("*.pdf"))):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    correct_pages = expected_pages = correct_failures = 0
    latencies: list[float] = []
    details: list[dict[str, object]] = []
    extractor = SourceExtractor()
    for case in labels["pdfs"]:
        content = ROOT.joinpath(case["file"]).read_bytes()
        start = time.perf_counter()
        artifact = await extractor.extract(
            UUID("d2c133b4-1b49-4c06-9d14-46f16f0543b5"),
            1,
            content,
            "application/pdf",
            ExtractionConfig(),
        )
        latencies.append((time.perf_counter() - start) * 1000)
        correct = 0
        for number, expected in enumerate(case["pages"], 1):
            expected_pages += 1
            if number <= len(artifact.pages):
                page = artifact.pages[number - 1]
                text = artifact.extracted_text[page.start : page.end]
                correct += page.number == number and " ".join(text.split()) == " ".join(
                    expected.split()
                )
        correct_pages += correct
        details.append(
            {
                "file": case["file"],
                "correct_pages": correct,
                "expected_pages": len(case["pages"]),
                "actual_pages": len(artifact.pages),
            }
        )
    for case in labels["unsupported"]:
        try:
            await extractor.extract(
                UUID("d2c133b4-1b49-4c06-9d14-46f16f0543b5"),
                1,
                ROOT.joinpath(case["file"]).read_bytes(),
                "application/pdf",
                ExtractionConfig(),
            )
        except IngestionError as exc:
            correct_failures += exc.code == case["code"]
    correct_spans = 0
    for case in labels["normalization"]:
        result = normalize(case["source"], "line-endings-v1")
        correct_spans += result.text == case["normalized"] and result.source_span(
            case["start"], case["end"]
        ) == (case["source_start"], case["source_end"])
    image = await asyncio.to_thread(
        subprocess.check_output,
        ["docker", "image", "inspect", "atlas-pdf-parser:6.21.4", "--format", "{{.Id}}"],
    )
    return {
        "dataset": labels["version"],
        "dataset_sha256": digest.hexdigest(),
        "recorded_at": datetime.now(UTC).isoformat(),
        "pdfs": len(labels["pdfs"]),
        "labeled_pages": expected_pages,
        "labeled_spans": len(labels["normalization"]),
        "unsupported_cases": len(labels["unsupported"]),
        "page_text_accuracy_whitespace_normalized": correct_pages / expected_pages,
        "source_span_accuracy": correct_spans / len(labels["normalization"]),
        "unsupported_status_accuracy": correct_failures / len(labels["unsupported"]),
        "extraction_latency_ms_p50": statistics.median(latencies),
        "extraction_latency_ms_max": max(latencies),
        "details": details,
        "runtime": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "pypdf": version("pypdf"),
            "parser_image_id": image.decode().strip(),
            "normalizer": "line-endings-v1",
            "models": [],
            "config": ExtractionConfig().model_dump(),
        },
        "unavailable": [
            "entity precision/recall/F1",
            "relationship precision/recall/F1",
            "resolution accuracy",
            "ambiguity accuracy",
            "per-entity-type metrics",
        ],
        "limitations": (
            "Small authored source-extraction corpus; "
            "no NLP/entity extractor or learned model evaluated."
        ),
    }


def main() -> None:
    result = asyncio.run(evaluate())
    path = Path("evaluation/results/source-extraction-v1.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
