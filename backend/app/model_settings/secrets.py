"""模型API Key的安全存储边界。"""

from __future__ import annotations

import subprocess
import sys
from typing import Protocol


class ModelSecretStore(Protocol):
    """测试可替换的最小密钥存储接口。"""

    def get(self, provider: str) -> str | None: ...

    def set(self, provider: str, value: str) -> None: ...


class MacOSKeychainSecretStore:
    """通过macOS Keychain保存密钥，不把密钥写入项目或JSON。"""

    service = "com.vesta.desktop.model-api-key"

    def get(self, provider: str) -> str | None:
        if sys.platform != "darwin":
            return None
        result = subprocess.run(  # noqa: S603 - 固定绝对路径与参数数组
            [
                "/usr/bin/security",
                "find-generic-password",
                "-a",
                provider,
                "-s",
                self.service,
                "-w",
            ],
            capture_output=True,
            check=False,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return None
        return result.stdout.strip() or None

    def set(self, provider: str, value: str) -> None:
        if sys.platform != "darwin":
            raise RuntimeError("model API keys require macOS Keychain")
        normalized = value.strip()
        if not normalized:
            raise ValueError("api key cannot be empty")
        result = subprocess.run(  # noqa: S603 - 固定绝对路径且不经过shell
            [
                "/usr/bin/security",
                "add-generic-password",
                "-U",
                "-a",
                provider,
                "-s",
                self.service,
                "-w",
                normalized,
            ],
            capture_output=True,
            check=False,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            message = result.stderr.strip() or "macOS Keychain write failed"
            raise RuntimeError(message)


__all__ = ["MacOSKeychainSecretStore", "ModelSecretStore"]
