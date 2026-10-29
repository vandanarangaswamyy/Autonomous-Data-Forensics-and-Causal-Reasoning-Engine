"""Storage-independent configuration and deterministic identifiers."""

import hashlib
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from typing import Literal, Self
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Profile(StrEnum):
    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"


class GenerationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    seed: int = Field(default=42, ge=0)
    start: date = date(2025, 11, 1)
    days: int = Field(default=28, ge=7, le=730)
    customers: int = Field(default=80, ge=12, le=100000)
    orders_per_day: int = Field(default=18, ge=3, le=10000)
    late_probability: float = Field(default=0.04, ge=0, le=0.5)

    @classmethod
    def profile(cls, profile: Profile, seed: int = 42) -> Self:
        sizes = {
            Profile.SMALL: (28, 80, 18),
            Profile.MEDIUM: (90, 800, 80),
            Profile.LARGE: (365, 6000, 300),
        }
        days, customers, orders = sizes[profile]
        return cls(seed=seed, days=days, customers=customers, orders_per_day=orders)

    @property
    def fixture_id(self) -> str:
        return str(uuid5(NAMESPACE_URL, self.model_dump_json()))

    @property
    def cutoff(self) -> datetime:
        return datetime.combine(self.start + timedelta(days=self.days), time(), UTC)


class FaultKind(StrEnum):
    SCHEMA_DRIFT = "schema_drift"
    DELAYED_INGESTION = "delayed_ingestion"
    DUPLICATE_TRANSACTIONS = "duplicate_transactions"
    INCORRECT_JOIN = "incorrect_join"
    MISSING_PARTITIONS = "missing_partitions"
    CURRENCY_CONVERSION = "currency_conversion"


class FaultConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: FaultKind
    day: date
    duration_days: int = Field(default=2, ge=1, le=30)
    copies: int = Field(default=1, ge=1, le=5)
    delay_days: int = Field(default=60, ge=1, le=365)
    currency: Literal["EUR", "GBP", "JPY"] = "EUR"
    rate_multiplier: float = Field(default=0.5, gt=0, le=10, allow_inf_nan=False)

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    config: GenerationConfig = Field(default_factory=GenerationConfig)
    fault: FaultConfig
    metric: Literal["revenue_usd", "payment_count", "churned"]
    direction: Literal["increase", "decrease"]
    affected_datasets: list[str]
    expected_evidence: list[str]

    @model_validator(mode="after")
    def in_range(self) -> Self:
        end = self.config.start + timedelta(days=self.config.days)
        if not self.config.start <= self.fault.day < end:
            raise ValueError("Fault day must lie within the fixture")
        return self
