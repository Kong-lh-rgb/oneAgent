"""Computer bootstrap 测试：helper 解析顺序 / 开关 / 非 macOS / 缺 helper。"""

from __future__ import annotations

import sys
from pathlib import Path

from app.computer.bootstrap import (
    build_macos_computer,
    computer_enabled,
    current_platform,
    resolve_helper_path,
)


def _make_executable(tmp_path: Path, name: str) -> Path:
    path = tmp_path / name
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def _patch_dev_helper(monkeypatch, tmp_path: Path) -> Path:
    dev = _make_executable(tmp_path, "dev-helper")
    import app.computer.bootstrap as bootstrap

    monkeypatch.setattr(bootstrap, "_DEFAULT_DEV_HELPER", dev)
    return dev


def test_explicit_helper_takes_priority(monkeypatch, tmp_path) -> None:
    explicit = _make_executable(tmp_path, "explicit-helper")
    env_helper = _make_executable(tmp_path, "env-helper")
    monkeypatch.setenv("VESTA_MACOS_HELPER_PATH", str(env_helper))

    assert resolve_helper_path(explicit) == explicit.resolve()
    # 显式 > 环境变量。
    assert resolve_helper_path(explicit) != env_helper.resolve()


def test_env_helper_fallback(monkeypatch, tmp_path) -> None:
    env_helper = _make_executable(tmp_path, "env-helper")
    monkeypatch.setenv("VESTA_MACOS_HELPER_PATH", str(env_helper))

    assert resolve_helper_path(None) == env_helper.resolve()


def test_dev_path_fallback(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("VESTA_MACOS_HELPER_PATH", raising=False)
    dev = _patch_dev_helper(monkeypatch, tmp_path)

    assert resolve_helper_path(None) == dev.resolve()


def test_missing_helper_is_unavailable_but_returns_none(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.delenv("VESTA_MACOS_HELPER_PATH", raising=False)
    # 屏蔽 dev 自动发现路径，避免本机已 build 的 helper 命中。
    import app.computer.bootstrap as bootstrap

    monkeypatch.setattr(
        bootstrap, "_DEFAULT_DEV_HELPER", tmp_path / "no-dev-helper"
    )
    runtime, status = build_macos_computer(
        helper_path=tmp_path / "does-not-exist"
    )
    assert runtime is None
    assert status.enabled is True
    assert status.available is False
    assert status.reason == "helper_not_found"


def test_disabled_does_not_build_runtime(monkeypatch, tmp_path) -> None:
    helper = _make_executable(tmp_path, "helper")
    runtime, status = build_macos_computer(helper_path=helper, enabled=False)
    assert runtime is None
    assert status.enabled is False
    assert status.available is False
    assert status.reason == "disabled"


def test_non_macos_unavailable(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    helper = _make_executable(tmp_path, "helper")
    runtime, status = build_macos_computer(helper_path=helper)
    assert runtime is None
    assert status.enabled is True
    assert status.available is False
    assert status.reason == "unsupported_platform"


def test_build_runtime_when_available(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    helper = _make_executable(tmp_path, "helper")
    runtime, status = build_macos_computer(helper_path=helper)
    assert runtime is not None
    assert status.enabled is True
    assert status.available is True
    assert status.reason is None
    assert status.runtime == "macos"
    assert status.helper_path == str(helper.resolve())
    # 注入真实 MacOSComputerRuntime（不启动，仅构造）。
    from app.computer import MacOSComputerRuntime

    assert isinstance(runtime, MacOSComputerRuntime)


def test_computer_enabled_switch_and_env(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    assert computer_enabled() is True
    assert computer_enabled(enabled=False) is False
    assert computer_enabled(enabled=True) is True

    monkeypatch.setenv("VESTA_COMPUTER_ENABLED", "false")
    assert computer_enabled() is False
    monkeypatch.setenv("VESTA_COMPUTER_ENABLED", "true")
    assert computer_enabled() is True


def test_current_platform_reports(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    assert current_platform() == "macos"
    monkeypatch.setattr(sys, "platform", "win32")
    assert current_platform() == "win32"
