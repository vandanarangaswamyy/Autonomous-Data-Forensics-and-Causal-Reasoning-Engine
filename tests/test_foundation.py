from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from atlas_api.main import create_app
from atlas_contracts.models import AgentState, Evidence, Hypothesis, Investigation, ToolExecution
from atlas_core.config import Settings
from atlas_core.errors import AtlasError
from fastapi.testclient import TestClient
from pydantic import ValidationError


def test_probes_and_lifecycle() -> None:
    app = create_app(Settings(environment="test"))
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/ready").json() == {"status": "ready"}
        app.state.ready = False
        assert client.get("/ready").status_code == 503
        response = client.get("/missing")
        assert response.status_code == 404
        assert response.json()["request_id"] == response.headers["x-request-id"]
    assert not app.state.ready


def test_errors_are_sanitized() -> None:
    app = create_app()

    @app.get("/fail")
    async def fail() -> None:
        raise RuntimeError("private connection string")

    @app.get("/domain")
    async def domain() -> None:
        raise AtlasError("conflict", "Resource conflict", 409)

    @app.get("/validate/{value}")
    async def validate(value: int) -> int:
        return value

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/fail")
        assert response.status_code == 500
        assert "private" not in response.text
        assert response.json()["request_id"] == response.headers["x-request-id"]
        assert client.get("/domain").status_code == 409
        assert client.get("/validate/private").json()["code"] == "validation_error"


def test_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ATLAS_ENVIRONMENT", "test")
    assert Settings(_env_file=None).environment == "test"
    monkeypatch.setenv("ATLAS_ENVIRONMENT", "bad")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_investigation_times_and_roundtrip() -> None:
    now = datetime.now(UTC)
    value = Investigation(title="Revenue decline", created_at=now, updated_at=now)
    assert Investigation.model_validate_json(value.model_dump_json()) == value
    for timestamp in [now - timedelta(seconds=1), now.replace(tzinfo=None)]:
        with pytest.raises(ValidationError):
            Investigation(title="Revenue", created_at=now, updated_at=timestamp)


@pytest.mark.parametrize("status", ["supported", "rejected"])
def test_hypotheses_require_evidence(status: str) -> None:
    data = dict(investigation_id=uuid4(), statement="Late job", confidence=0.7, status=status)
    with pytest.raises(ValidationError):
        Hypothesis.model_validate(data)
    assert Hypothesis.model_validate({**data, "evidence_ids": [uuid4()]}).evidence_ids


def test_evidence_provenance() -> None:
    now = datetime.now(UTC)
    data = dict(
        investigation_id=uuid4(),
        source_uri="s3://evidence/log",
        summary="Late job",
        content_sha256="a" * 64,
        observed_at=now,
        recorded_at=now,
    )
    value = Evidence.model_validate(data)
    assert Evidence.model_validate_json(value.model_dump_json()) == value
    with pytest.raises(ValidationError):
        Evidence.model_validate({**data, "content_sha256": "bad"})


def test_execution_policy_and_limits() -> None:
    data = dict(
        investigation_id=uuid4(),
        tool_name="diagnostic",
        requested_at=datetime.now(UTC),
        status="running",
        policy_decision="allow",
        timeout_seconds=10,
        result_limit=100,
        input_digest="a" * 64,
    )
    assert ToolExecution.model_validate(data).status == "running"
    changes: list[dict[str, object]] = [
        {"policy_decision": "deny"},
        {"timeout_seconds": 0},
        {"result_limit": 10001},
    ]
    for change in changes:
        with pytest.raises(ValidationError):
            ToolExecution.model_validate({**data, **change})


def test_state_serialization_and_isolation() -> None:
    first = AgentState(investigation_id=uuid4())
    second = AgentState(investigation_id=uuid4())
    first.context["key"] = "value"
    assert not second.context
    assert AgentState.model_validate_json(first.model_dump_json()) == first
    with pytest.raises(ValidationError):
        AgentState.model_validate({"investigation_id": uuid4(), "unknown": True})
