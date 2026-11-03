from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


class DocumentSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ATLAS_KNOWLEDGE_", env_file=".env", extra="ignore"
    )
    database_url: SecretStr

    def engine(self) -> AsyncEngine:
        return create_async_engine(
            self.database_url.get_secret_value(),
            pool_pre_ping=True,
            pool_size=4,
            max_overflow=0,
            pool_timeout=5,
            connect_args={"connect_timeout": 3},
        )
