import json
import sys
from pathlib import Path

import pytest
from atlas_simulator.cli import main
from sqlalchemy import Engine


def test_operator_cli_workflow(
    db: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ATLAS_SIM_DATABASE_URL", str(db.url))
    monkeypatch.setenv("ATLAS_SIM_PRIVATE_DIR", str(tmp_path / "oracle"))
    monkeypatch.setattr(
        sys, "argv", ["atlas-sim", "generate", "--profile", "small", "--seed", "42"]
    )
    main()
    baseline = json.loads(capsys.readouterr().out)
    monkeypatch.setattr(
        sys,
        "argv",
        ["atlas-sim", "inject", "--config", "evaluation/faults/duplicate_transactions.json"],
    )
    main()
    result = json.loads(capsys.readouterr().out)
    assert int(result["affected_metrics"]["payment_count"]) > int(baseline["payment_count"])
    monkeypatch.setattr(sys, "argv", ["atlas-sim", "replay", result["fault_id"]])
    main()
    assert json.loads(capsys.readouterr().out)["affected_metrics"] == result["affected_metrics"]
    monkeypatch.setattr(sys, "argv", ["atlas-sim", "reset"])
    main()
    assert json.loads(capsys.readouterr().out) == baseline
    monkeypatch.setattr(
        sys, "argv", ["atlas-sim", "scenario", "evaluation/scenarios/a_missing_partitions.json"]
    )
    main()
    assert json.loads(capsys.readouterr().out)["scenario"]
