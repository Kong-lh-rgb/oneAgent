"""模型设置的非敏感JSON存储。"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .models import StoredModelSettings

DEFAULT_MODEL_SETTINGS_PATH = (
    Path(__file__).resolve().parents[2] / ".vesta" / "settings" / "models.json"
)


class ModelSettingsStore:
    """用临时文件+原子替换保存设置，文件中禁止出现密钥。"""

    def __init__(self, path: str | Path = DEFAULT_MODEL_SETTINGS_PATH) -> None:
        self.path = Path(path).expanduser().resolve()

    def load(self) -> StoredModelSettings | None:
        if not self.path.is_file():
            return None
        if self.path.is_symlink():
            raise ValueError("model settings file cannot be a symbolic link")
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        return StoredModelSettings.model_validate(payload)

    def save(self, settings: StoredModelSettings) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.tmp-{os.getpid()}")
        serialized = json.dumps(
            settings.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
        )
        temporary.write_text(serialized + "\n", encoding="utf-8")
        os.replace(temporary, self.path)


__all__ = ["DEFAULT_MODEL_SETTINGS_PATH", "ModelSettingsStore"]
