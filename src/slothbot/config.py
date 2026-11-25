"""Environment-backed settings."""

from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from slothbot.paths import heartbeat_path


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SB_", env_file=".env", extra="ignore")

    token: SecretStr = Field(..., description="Discord bot token")
    data: Path = Field(Path("data"), description="Directory holding the JSON state files")
    log_level: str = Field("info", description="Root log level")
    guild: int | None = Field(None, description="Sync commands to this guild only when set")
    heartbeat_file: Path | None = Field(
        None, description="Liveness file touched while connected; defaults to <data>/heartbeat"
    )

    @property
    def heartbeat_path(self) -> Path:
        return heartbeat_path(self.data, self.heartbeat_file)
