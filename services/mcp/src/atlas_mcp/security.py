import hashlib
import hmac
from enum import StrEnum

from atlas_contracts.analytics import AnalyticsError, AnalyticsModel
from pydantic import Field, SecretStr


class Permission(StrEnum):
    SCHEMA = "analytics:schema"
    QUERY = "analytics:query"
    PROFILE = "analytics:profile"
    PLAN = "analytics:plan"
    LINEAGE = "lineage:read"
    TELEMETRY = "telemetry:read"
    KNOWLEDGE = "knowledge:read"


class Principal(AnalyticsModel):
    id: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_.-]+$")
    permissions: frozenset[Permission] = frozenset()


class Authorizer:
    def __init__(self, token: SecretStr, principal: Principal) -> None:
        if len(token.get_secret_value()) < 32:
            raise ValueError("MCP session tokens require at least 32 characters")
        self._digest = hashlib.sha256(token.get_secret_value().encode()).digest()
        self.principal = principal

    def authenticate(self, token: str | None) -> Principal:
        if (
            not token
            or len(token) > 4096
            or not hmac.compare_digest(self._digest, hashlib.sha256(token.encode()).digest())
        ):
            raise AnalyticsError("unauthorized", "A valid application session token is required")
        return self.principal
