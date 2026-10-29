from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class SimulatorSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ATLAS_SIM_", env_file=".env", extra="ignore")
    database_url: SecretStr
    private_dir: Path = Path(".atlas-private")
    kafka_bootstrap: str = "127.0.0.1:9092"
    kafka_topic: str = "atlas.simulator.events.v1"
