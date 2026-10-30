import os
from pathlib import Path

import pytest

from scripts.bootstrap_simulator import write_private_env


def test_credentials_are_private_before_atomic_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / ".env"
    target.write_text("old nonsecret config")
    target.chmod(0o644)
    replace = os.replace
    inspected: list[bool] = []

    def inspect(source: str | Path, destination: str | Path) -> None:
        assert Path(source).stat().st_mode & 0o777 == 0o600
        assert target.read_text() == "old nonsecret config"
        inspected.append(True)
        replace(source, destination)

    monkeypatch.setattr(os, "replace", inspect)
    write_private_env(target, "private-secret")
    assert inspected and target.read_text() == "private-secret"
    assert target.stat().st_mode & 0o777 == 0o600
    assert not list(tmp_path.glob("*.tmp"))


def test_setup_refuses_linked_env(tmp_path: Path) -> None:
    victim = tmp_path / "other"
    victim.write_text("preserve")
    linked = tmp_path / ".env"
    linked.symlink_to(victim)
    with pytest.raises(ValueError):
        write_private_env(linked, "secret")
    assert victim.read_text() == "preserve"
