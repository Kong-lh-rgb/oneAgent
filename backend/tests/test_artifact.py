"""Artifact V10 测试：store / publish file+url / tool / RPC / media / broadcast。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.artifact import (
    Artifact,
    ArtifactKind,
    ArtifactPublishTool,
    ArtifactService,
    ArtifactTooLargeError,
    SQLiteArtifactStore,
    register_artifact_tools,
)
from app.models.types import ToolCall
from app.server.app import artifact_content
from app.server.rpc.dispatcher import RpcContext
from app.server.rpc.methods import artifacts as artifacts_rpc
from app.server.rpc.protocol import JsonRpcError, RpcErrorCode
from app.tools.hooks import ToolExecutionContext
from app.tools.registry import ToolRegistry


async def _make_service(tmp_path, *, workspace_name="workspace") -> ArtifactService:
    store = SQLiteArtifactStore(tmp_path / "artifacts.db")
    await store.initialize()
    workspace = tmp_path / workspace_name
    workspace.mkdir(parents=True, exist_ok=True)
    service = ArtifactService(
        store, workspace, managed_dir=tmp_path / "managed-artifacts"
    )
    return service


def _write(path: Path, content: bytes | str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content if isinstance(content, bytes) else content.encode())
    return path


def _ctx(app) -> RpcContext:
    return RpcContext(application=app, connection=None)  # type: ignore[arg-type]


def _tool_context(run_id="run-1", conversation_id="conv-1") -> ToolExecutionContext:
    return ToolExecutionContext(
        tool_call=ToolCall(id="t1", name="artifact_publish", arguments={}),
        run_id=run_id,
        conversation_id=conversation_id,
    )


# ---------------------------------------------------------------------------
# ArtifactStore
# ---------------------------------------------------------------------------


async def test_store_create_get_list(tmp_path) -> None:
    store = SQLiteArtifactStore(tmp_path / "a.db")
    await store.initialize()
    a = Artifact(
        kind=ArtifactKind.FILE,
        title="Report",
        filename="report.md",
        run_id="run-1",
        conversation_id="conv-1",
        sha256="abc",
        size_bytes=3,
    )
    await store.create(a)
    assert (await store.get(a.id)) is not None
    items = await store.list()
    assert len(items) == 1
    assert items[0].id == a.id


async def test_store_filters_and_durable(tmp_path) -> None:
    store = SQLiteArtifactStore(tmp_path / "a.db")
    await store.initialize()
    a1 = Artifact(
        kind=ArtifactKind.FILE, title="A", run_id="run-1", conversation_id="conv-1"
    )
    a2 = Artifact(
        kind=ArtifactKind.URL, title="B", run_id="run-1", conversation_id="conv-2"
    )
    a3 = Artifact(
        kind=ArtifactKind.FILE, title="C", run_id="run-2", conversation_id="conv-1"
    )
    for a in (a1, a2, a3):
        await store.create(a)

    assert {a.id for a in await store.list(run_id="run-1")} == {a1.id, a2.id}
    assert {a.id for a in await store.list(conversation_id="conv-1")} == {
        a1.id,
        a3.id,
    }
    assert {
        a.id for a in await store.list(run_id="run-1", conversation_id="conv-1")
    } == {a1.id}

    # restart 后 metadata 仍 durable。
    store2 = SQLiteArtifactStore(tmp_path / "a.db")
    await store2.initialize()
    assert (await store2.get(a1.id)) is not None
    assert len(await store2.list()) == 3


# ---------------------------------------------------------------------------
# publish_file
# ---------------------------------------------------------------------------


async def test_publish_file_ok_and_immutable(tmp_path) -> None:
    service = await _make_service(tmp_path)
    source = _write(
        tmp_path / "workspace" / "reports" / "report.md", "hello artifact"
    )
    artifact = await service.publish_file(
        path="reports/report.md",
        title="Report",
        run_id="run-1",
        conversation_id="conv-1",
    )
    assert artifact.kind is ArtifactKind.FILE
    assert artifact.title == "Report"
    assert artifact.filename == "report.md"
    assert artifact.run_id == "run-1"
    assert artifact.size_bytes == len("hello artifact")

    # 复制进 managed 目录。
    copied = await service.file_path(artifact.id)
    assert copied is not None
    assert copied.read_text() == "hello artifact"
    assert str(copied).startswith(str(service.managed_dir))

    # 原文件修改不影响 Artifact copy。
    source.write_text("changed later")
    assert copied.read_text() == "hello artifact"


async def test_publish_file_rejects_bad_paths(tmp_path) -> None:
    service = await _make_service(tmp_path)
    _write(tmp_path / "workspace" / "ok.txt", "ok")
    _write(tmp_path / "outside.txt", "outside")  # workspace 外

    with pytest.raises(ValueError, match="relative"):
        await service.publish_file(path=str(tmp_path / "workspace" / "ok.txt"))
    with pytest.raises(ValueError, match="escapes"):
        await service.publish_file(path="../outside.txt")
    # "." 解析到 workspace root（不允许引用 root 本身）。
    with pytest.raises(ValueError):
        await service.publish_file(path=".")
    with pytest.raises(ValueError, match="file"):
        await service.publish_file(path="reports")  # 不存在 → resolve 后 is_file False

    # symlink 逃逸。
    link = tmp_path / "workspace" / "escape.txt"
    link.symlink_to(tmp_path / "outside.txt")
    with pytest.raises(ValueError, match="escapes"):
        await service.publish_file(path="escape.txt")


async def test_publish_file_sha256_and_mime(tmp_path) -> None:
    service = await _make_service(tmp_path)
    _write(tmp_path / "workspace" / "data.csv", "a,b\n1,2\n")
    artifact = await service.publish_file(path="data.csv", run_id="run-1")
    import hashlib

    assert artifact.sha256 == hashlib.sha256(b"a,b\n1,2\n").hexdigest()
    assert artifact.mime_type == "text/csv"


async def test_publish_file_too_large(monkeypatch, tmp_path) -> None:
    import app.artifact.service as service_module

    monkeypatch.setattr(service_module, "MAX_ARTIFACT_BYTES", 10)
    service = await _make_service(tmp_path)
    _write(tmp_path / "workspace" / "big.bin", "x" * 20)
    with pytest.raises(ArtifactTooLargeError):
        await service.publish_file(path="big.bin")


# ---------------------------------------------------------------------------
# publish_url
# ---------------------------------------------------------------------------


async def test_publish_url_ok(tmp_path) -> None:
    service = await _make_service(tmp_path)
    a = await service.publish_url(
        url="https://example.com/result", title="Result", run_id="run-1"
    )
    assert a.kind is ArtifactKind.URL
    assert a.source_url == "https://example.com/result"
    assert a.size_bytes == 0
    # URL artifact 没有内部文件。
    assert await service.file_path(a.id) is None


@pytest.mark.parametrize(
    "url", ["file:///etc/passwd", "javascript:alert(1)", "data:text/plain,hi"]
)
async def test_publish_url_rejects_bad_schemes(tmp_path, url: str) -> None:
    service = await _make_service(tmp_path)
    with pytest.raises(ValueError, match="http"):
        await service.publish_url(url=url)


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------


async def test_tool_binds_context_and_rejects_forged_ids(tmp_path) -> None:
    service = await _make_service(tmp_path)
    tool = ArtifactPublishTool(service)
    _write(tmp_path / "workspace" / "f.txt", "data")

    result = await tool.execute_with_context(
        {"path": "f.txt"},
        _tool_context(run_id="real-run", conversation_id="real-conv"),
    )
    assert result["run_id"] == "real-run"
    assert result["conversation_id"] == "real-conv"
    assert result["kind"] == "file"

    with pytest.raises(ValueError, match="unsupported"):
        await tool.execute_with_context(
            {"path": "f.txt", "run_id": "forged-run"},
            _tool_context(run_id="real-run", conversation_id="real-conv"),
        )


async def test_tool_missing_run_context_rejected(tmp_path) -> None:
    service = await _make_service(tmp_path)
    tool = ArtifactPublishTool(service)
    _write(tmp_path / "workspace" / "f.txt", "data")
    with pytest.raises(ValueError, match="run context"):
        await tool.execute({"path": "f.txt"})
    with pytest.raises(ValueError, match="run context"):
        await tool.execute_with_context({"path": "f.txt"}, _tool_context(run_id=None))


async def test_tool_path_xor_url(tmp_path) -> None:
    service = await _make_service(tmp_path)
    tool = ArtifactPublishTool(service)
    _write(tmp_path / "workspace" / "f.txt", "data")
    with pytest.raises(ValueError, match="exactly one"):
        await tool.execute_with_context({}, _tool_context())
    with pytest.raises(ValueError, match="exactly one"):
        await tool.execute_with_context(
            {"path": "f.txt", "url": "https://x"}, _tool_context()
        )


async def test_register_artifact_tools(tmp_path) -> None:
    service = await _make_service(tmp_path)
    registry = ToolRegistry()
    register_artifact_tools(registry, service)
    assert "artifact_publish" in registry.names()


# ---------------------------------------------------------------------------
# broadcast
# ---------------------------------------------------------------------------


async def test_publish_broadcasts_artifact_created(tmp_path) -> None:
    service = await _make_service(tmp_path)
    events: list[tuple[str, dict]] = []

    async def broadcaster(method: str, params: dict) -> None:
        events.append((method, params))

    service.set_broadcaster(broadcaster)
    _write(tmp_path / "workspace" / "f.txt", "data")
    artifact = await service.publish_file(path="f.txt", run_id="run-1")
    assert events and events[0][0] == "artifact.created"
    assert events[0][1]["artifact"]["id"] == artifact.id
    assert "storage_path" not in events[0][1]["artifact"]


async def test_publish_without_broadcaster_still_saves(tmp_path) -> None:
    service = await _make_service(tmp_path)
    _write(tmp_path / "workspace" / "f.txt", "data")
    artifact = await service.publish_file(path="f.txt", run_id="run-1")
    assert (await service.store.get(artifact.id)) is not None


async def test_broadcast_failure_does_not_rollback_artifact(tmp_path) -> None:
    service = await _make_service(tmp_path)

    async def broken_broadcaster(method: str, params: dict) -> None:
        raise RuntimeError("offline")

    service.set_broadcaster(broken_broadcaster)
    _write(tmp_path / "workspace" / "f.txt", "data")
    artifact = await service.publish_file(path="f.txt", run_id="run-1")
    assert (await service.store.get(artifact.id)) is not None


async def test_store_failure_cleans_managed_copy(tmp_path, monkeypatch) -> None:
    service = await _make_service(tmp_path)
    _write(tmp_path / "workspace" / "f.txt", "data")

    async def broken_create(artifact: Artifact) -> Artifact:
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(service.store, "create", broken_create)
    with pytest.raises(RuntimeError, match="database unavailable"):
        await service.publish_file(path="f.txt", run_id="run-1")
    assert list(service.managed_dir.glob("*")) == []


# ---------------------------------------------------------------------------
# RPC
# ---------------------------------------------------------------------------


async def test_rpc_artifact_list_and_get(tmp_path) -> None:
    store = SQLiteArtifactStore(tmp_path / "a.db")
    await store.initialize()
    a = Artifact(kind=ArtifactKind.FILE, title="R", filename="r.md", run_id="run-1")
    await store.create(a)
    app = SimpleNamespace(artifact_store=store)

    result = await artifacts_rpc.artifact_list({"run_id": "run-1"}, _ctx(app))
    assert result["count"] == 1
    assert result["artifacts"][0]["id"] == a.id
    assert "storage_path" not in result["artifacts"][0]

    got = await artifacts_rpc.artifact_get({"id": a.id}, _ctx(app))
    assert got["artifact"]["title"] == "R"

    with pytest.raises(JsonRpcError) as exc:
        await artifacts_rpc.artifact_get({}, _ctx(app))
    assert exc.value.code == RpcErrorCode.INVALID_PARAMS
    with pytest.raises(JsonRpcError) as exc:
        await artifacts_rpc.artifact_get({"id": "missing"}, _ctx(app))
    assert exc.value.code == RpcErrorCode.INVALID_PARAMS
    with pytest.raises(JsonRpcError) as exc:
        await artifacts_rpc.artifact_get({"id": "f" * 32}, _ctx(app))
    assert exc.value.code == -32000


# ---------------------------------------------------------------------------
# media endpoint
# ---------------------------------------------------------------------------


def _fake_request(fake_app, host="127.0.0.1"):
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(application=fake_app)),
        client=SimpleNamespace(host=host),
    )


async def test_media_endpoint_file_ok(tmp_path) -> None:
    service = await _make_service(tmp_path)
    _write(tmp_path / "workspace" / "notes.txt", "artifact body")
    a = await service.publish_file(path="notes.txt")
    fake_app = SimpleNamespace(artifact_service=service)

    response = await artifact_content(a.id, _fake_request(fake_app))
    assert response.status_code == 200
    assert Path(response.path).read_bytes() == b"artifact body"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-disposition"] == 'attachment; filename="notes.txt"'
    assert response.media_type in ("text/plain", "text/plain; charset=utf-8")


async def test_media_endpoint_url_rejected(tmp_path) -> None:
    service = await _make_service(tmp_path)
    a = await service.publish_url(url="https://example.com/x")
    fake_app = SimpleNamespace(artifact_service=service)
    with pytest.raises(HTTPException) as exc:
        await artifact_content(a.id, _fake_request(fake_app))
    assert exc.value.status_code == 404


async def test_media_endpoint_missing_and_invalid(tmp_path) -> None:
    service = await _make_service(tmp_path)
    fake_app = SimpleNamespace(artifact_service=service)
    with pytest.raises(HTTPException) as exc:
        await artifact_content("f" * 32, _fake_request(fake_app))
    assert exc.value.status_code == 404
    with pytest.raises(HTTPException) as exc:
        await artifact_content("../evil", _fake_request(fake_app))
    assert exc.value.status_code in (403, 404)


async def test_media_endpoint_non_loopback_rejected(tmp_path) -> None:
    service = await _make_service(tmp_path)
    _write(tmp_path / "workspace" / "f.txt", "data")
    a = await service.publish_file(path="f.txt")
    fake_app = SimpleNamespace(artifact_service=service)
    with pytest.raises(HTTPException) as exc:
        await artifact_content(a.id, _fake_request(fake_app, host="10.0.0.5"))
    assert exc.value.status_code == 403


async def test_media_endpoint_unavailable_service(tmp_path) -> None:
    fake_app = SimpleNamespace(artifact_service=None)
    with pytest.raises(HTTPException) as exc:
        await artifact_content("a" * 32, _fake_request(fake_app))
    assert exc.value.status_code == 404
