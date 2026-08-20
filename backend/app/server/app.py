"""oneAgent Host 的本地 transport。

FastAPI 不是业务架构，只负责：

- process lifespan（``application.start()`` / ``application.close()``）
- WebSocket upgrade（``WS /rpc``）
- ``GET /health``（供 Electron / 开发环境判断 Host 是否启动）
- ``GET /computer/screenshots/{observation_id}.png``（只读 media transport，
  不是业务 API：供 Desktop Computer View 读取本地截图）
- ``GET /artifacts/{artifact_id}/content``（只读 media transport：供 Desktop
  下载 / 打开 Agent 发布的 file Artifact）

Renderer 的正常业务全部走 ``WS /rpc``（JSON-RPC 2.0，一条连接双向通信），
不再使用 REST CRUD。
"""

from __future__ import annotations

import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response, WebSocket
from fastapi.responses import FileResponse

from app.application import Application

from .rpc import (
    RpcBroadcastEventHandler,
    RpcConnection,
    RpcHub,
    build_dispatcher,
)
from .version import __version__

logger = logging.getLogger("oneagent.server")

# 只允许 loopback client 读取截图（防止 --host 0.0.0.0 后把桌面截图暴露出去）。
_ALLOWED_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
_OBSERVATION_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_ARTIFACT_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def computer_screenshot(observation_id: str, request: Request) -> Response:
    """只读媒体 transport：把 {observation_id}.png 安全地交给本机 Desktop。

    不做业务 CRUD；observation_id 必须严格是 32 位 hex，且解析后仍位于
    runtime.screenshot_dir 内。拒绝任意文件 / 目录遍历 / 非 loopback client。
    """

    client_host = request.client.host if request.client is not None else ""
    if client_host not in _ALLOWED_LOOPBACK_HOSTS:
        raise HTTPException(status_code=403, detail="forbidden")

    if not _OBSERVATION_ID_RE.fullmatch(observation_id):
        raise HTTPException(status_code=404, detail="not found")

    application = request.app.state.application
    runtime = application.computer_runtime
    if runtime is None or not hasattr(runtime, "screenshot_dir"):
        raise HTTPException(status_code=404, detail="not found")
    screenshot_dir = Path(runtime.screenshot_dir).expanduser().resolve()

    candidate = screenshot_dir / f"{observation_id}.png"
    try:
        resolved = candidate.resolve()
    except OSError:
        raise HTTPException(status_code=404, detail="not found")
    if not str(resolved).startswith(str(screenshot_dir) + "/"):
        raise HTTPException(status_code=404, detail="not found")
    if not resolved.is_file():
        raise HTTPException(status_code=404, detail="not found")

    # 截图可能包含敏感桌面内容：no-store，禁止缓存。
    return Response(
        content=resolved.read_bytes(),
        media_type="image/png",
        headers={"Cache-Control": "no-store"},
    )


async def artifact_content(artifact_id: str, request: Request) -> FileResponse:
    """只读 media transport：下载 file Artifact（绝不接受 filename/path 参数）。

    artifact_id 严格校验（32 位 hex）→ ArtifactService.file_path → 再确认仍位于
    managed_dir 内 → 返回。URL artifact / 不存在 → 404；非 loopback → 403。
    """

    client_host = request.client.host if request.client is not None else ""
    if client_host not in _ALLOWED_LOOPBACK_HOSTS:
        raise HTTPException(status_code=403, detail="forbidden")

    if not _ARTIFACT_ID_RE.fullmatch(artifact_id):
        raise HTTPException(status_code=404, detail="not found")

    application = request.app.state.application
    service = application.artifact_service
    if service is None:
        raise HTTPException(status_code=404, detail="not found")

    file_path = await service.file_path(artifact_id)
    if file_path is None:
        raise HTTPException(status_code=404, detail="not found")
    try:
        resolved = file_path.resolve()
        resolved.relative_to(service.managed_dir)
    except (OSError, ValueError):
        raise HTTPException(status_code=404, detail="not found")
    if not resolved.is_file():
        raise HTTPException(status_code=404, detail="not found")

    artifact = await service.store.get(artifact_id)
    mime = (
        artifact.mime_type
        if artifact and artifact.mime_type
        else "application/octet-stream"
    )
    filename = artifact.filename if artifact and artifact.filename else "artifact"
    return FileResponse(
        path=resolved,
        media_type=mime,
        filename=filename,
        content_disposition_type="attachment",
        headers={
            "Cache-Control": "no-store",
        },
    )


def create_app(application: Application | None = None) -> FastAPI:
    """构造 oneAgent Host 应用。

    ``application`` 为 None 时自动用默认配置创建（provider 从 .env 选择）。
    调用方也可传入已配置的 Application（例如测试注入离线 fake registry）。
    Server 是 Desktop 的 Host：无论哪种方式都启用 DesktopApprovalGate
    （Async Approval V1），CLI 仍用 ConsoleApprovalGate。
    """

    if application is None:
        application = Application(desktop_approval=True)
    else:
        application.desktop_approval = True

    # 全局共享事件观察者：在 application.start() 之前注入，
    # ConversationService 构造时会把 RPC 广播与 Trace 一起组合。
    hub = RpcHub()
    broadcast = RpcBroadcastEventHandler(hub)
    application.shared_event_handler = broadcast

    dispatcher = build_dispatcher()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await application.start()
        # Async Approval：把 WebSocket hub 注入审批门作为通知广播器
        # （approval.required / approval.resolved）。
        approval_gate = application.desktop_approval_gate
        if approval_gate is not None:
            approval_gate.set_broadcaster(hub.broadcast)
        artifact_service = application.artifact_service
        if artifact_service is not None:
            artifact_service.set_broadcaster(hub.broadcast)
        logger.info(
            "oneAgent Host started · provider=%s · model=%s",
            application.provider,
            application.model,
        )
        try:
            yield
        finally:
            await application.close()
            logger.info("oneAgent Host stopped")

    app = FastAPI(
        title="oneAgent Host",
        version=__version__,
        lifespan=lifespan,
    )
    app.state.application = application
    app.state.hub = hub
    app.state.dispatcher = dispatcher

    @app.get("/health")
    async def health(request: Request) -> dict[str, object]:
        current: Application = request.app.state.application
        return {
            "status": "ok",
            "provider": current.provider,
            "model": current.model,
            "version": __version__,
        }

    @app.get("/computer/screenshots/{observation_id}.png")
    async def computer_screenshot_route(
        observation_id: str, request: Request
    ) -> Response:
        return computer_screenshot(observation_id, request)

    @app.get("/artifacts/{artifact_id}/content")
    async def artifact_content_route(
        artifact_id: str, request: Request
    ) -> Response:
        return await artifact_content(artifact_id, request)

    @app.websocket("/rpc")
    async def rpc_endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        connection = RpcConnection(websocket, dispatcher, application, hub)
        await hub.register(connection)
        try:
            await connection.run()
        finally:
            await hub.unregister(connection)

    return app


__all__ = ["__version__", "create_app"]
