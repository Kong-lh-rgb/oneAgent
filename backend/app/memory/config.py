"""Memory 运行配置。"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class MemorySettings(BaseSettings):
    """通过环境变量显式启用长期记忆。"""

    model_config = SettingsConfigDict(
        env_file=_BACKEND_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    memory_enabled: bool = False
    memory_embedding_api_key: SecretStr | None = None
    memory_embedding_base_url: str | None = None
    memory_embedding_model: str = "text-embedding-3-small"
    memory_embedding_dimensions: int = Field(default=1536, gt=0)
    memory_namespaces: str = "global,user:local,project:oneagent"
    memory_write_namespace: str = "user:local"

    def api_key_value(self) -> str:
        if self.memory_embedding_api_key is None:
            raise ValueError(
                "MEMORY_ENABLED=true requires MEMORY_EMBEDDING_API_KEY"
            )
        value = self.memory_embedding_api_key.get_secret_value().strip()
        if not value:
            raise ValueError(
                "MEMORY_ENABLED=true requires MEMORY_EMBEDDING_API_KEY"
            )
        return value

    def parsed_namespaces(self) -> tuple[str, ...]:
        namespaces = tuple(
            item.strip() for item in self.memory_namespaces.split(",") if item.strip()
        )
        if not namespaces:
            raise ValueError("MEMORY_NAMESPACES cannot be empty")
        return namespaces


__all__ = ["MemorySettings"]
