"""ArtifactService：publish file/url + 管理 managed artifact 目录 + 广播。"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import mimetypes
import os
import re
import uuid
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from app.tools.builtin._workspace import (
    resolve_workspace_path,
    workspace_root_path,
)

from .models import Artifact, ArtifactKind
from .store import SQLiteArtifactStore

logger = logging.getLogger("vesta.artifact")

# V1 单文件上限：100 MB（用 mock 测试边界，不真的生成大文件）。
MAX_ARTIFACT_BYTES = 100 * 1024 * 1024
_CHUNK_SIZE = 1024 * 1024
_UNSAFE_FILENAME_RE = re.compile(r"[\x00-\x1f\x7f/\\]")

Broadcaster = Callable[[str, Any], Awaitable[None]]


class ArtifactTooLargeError(ValueError):
    """publish 的文件超过单文件上限。"""


class ArtifactService:
    """创建不可变 Artifact（file 复制进 managed 目录 / url 只存 metadata）。"""

    def __init__(
        self,
        store: SQLiteArtifactStore,
        workspace_root: str | Path | None = None,
        *,
        managed_dir: str | Path | None = None,
    ) -> None:
        self.store = store
        self.workspace_root = workspace_root_path(workspace_root)
        if managed_dir is None:
            managed_dir = self.workspace_root.parent / "artifacts"
        self.managed_dir = Path(managed_dir).expanduser().resolve()
        self._broadcaster: Broadcaster | None = None

    def set_broadcaster(self, broadcaster: Broadcaster) -> None:
        """注入通知广播器（Server 在 application.start() 后注入 hub.broadcast）。"""

        self._broadcaster = broadcaster

    # ------------------------------------------------------------------
    # publish
    # ------------------------------------------------------------------

    async def publish_file(
        self,
        *,
        path: str,
        title: str = "",
        description: str | None = None,
        run_id: str | None = None,
        conversation_id: str | None = None,
        task_id: str | None = None,
    ) -> Artifact:
        """把 workspace 内的文件复制成不可变 Artifact。

        - 只允许 workspace-relative path（拒绝 absolute / ../ / symlink 逃逸）；
        - 复制进 managed 目录，原文件后续修改/删除不影响 Artifact。
        """

        source = resolve_workspace_path(self.workspace_root, path)
        if not await asyncio.to_thread(source.is_file):
            raise ValueError("artifact path must reference a file in the workspace")
        size_bytes = (await asyncio.to_thread(source.stat)).st_size
        if size_bytes > MAX_ARTIFACT_BYTES:
            raise ArtifactTooLargeError(
                f"artifact exceeds size limit ({size_bytes} > {MAX_ARTIFACT_BYTES})"
            )

        filename = _safe_filename(Path(path).name)
        mime_type = (
            mimetypes.guess_type(filename)[0] or "application/octet-stream"
        )

        artifact_id = uuid.uuid4().hex
        artifact_dir = self.managed_dir / artifact_id
        await asyncio.to_thread(artifact_dir.mkdir, parents=True, exist_ok=False)
        final_path = artifact_dir / filename
        tmp_path = artifact_dir / f".{artifact_id}.tmp"
        stored = False

        try:
            size_bytes, sha256 = await asyncio.to_thread(
                self._copy_and_digest,
                source,
                tmp_path,
            )
            await asyncio.to_thread(os.replace, tmp_path, final_path)
            artifact = Artifact(
                id=artifact_id,
                kind=ArtifactKind.FILE,
                title=title or filename,
                description=description,
                filename=filename,
                mime_type=mime_type,
                size_bytes=size_bytes,
                sha256=sha256,
                run_id=run_id,
                conversation_id=conversation_id,
                task_id=task_id,
                source_url=None,
                created_at=datetime.now(UTC),
            )
            try:
                await self.store.create(artifact)
                stored = True
            except Exception:
                # DB 写失败：清理刚复制的文件，避免半成品。
                await asyncio.to_thread(_cleanup_artifact_dir, artifact_dir)
                raise
        finally:
            await asyncio.to_thread(_cleanup_temp_file, tmp_path)
            if not stored:
                await asyncio.to_thread(_cleanup_artifact_dir, artifact_dir)

        await self._notify(artifact)
        return artifact

    async def publish_url(
        self,
        *,
        url: str,
        title: str = "",
        description: str | None = None,
        run_id: str | None = None,
        conversation_id: str | None = None,
        task_id: str | None = None,
    ) -> Artifact:
        """保存一个 http/https URL Artifact（只存 metadata，不下载内容）。"""

        parsed = urlsplit(url.strip())
        if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
            raise ValueError("artifact url must be http(s)")
        normalized_url = url.strip()

        artifact = Artifact(
            kind=ArtifactKind.URL,
            title=title or normalized_url,
            description=description,
            filename=None,
            mime_type=None,
            size_bytes=0,
            sha256=None,
            run_id=run_id,
            conversation_id=conversation_id,
            task_id=task_id,
            source_url=normalized_url,
            created_at=datetime.now(UTC),
        )
        await self.store.create(artifact)
        await self._notify(artifact)
        return artifact

    # ------------------------------------------------------------------
    # 读取
    # ------------------------------------------------------------------

    async def file_path(self, artifact_id: str) -> Path | None:
        """file Artifact 的内部文件路径（调用方负责再次校验在 managed_dir 内）。"""

        artifact = await self.store.get(artifact_id)
        if artifact is None or artifact.kind is not ArtifactKind.FILE:
            return None
        if not artifact.filename:
            return None
        return self.managed_dir / artifact.id / artifact.filename

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    @staticmethod
    def _copy_and_digest(source: Path, destination: Path) -> tuple[int, str]:
        """单次流式复制并计算摘要，同时防止复制期间文件增长越过上限。"""

        digest = hashlib.sha256()
        size_bytes = 0
        with source.open("rb") as src, destination.open("xb") as dst:
            while True:
                block = src.read(_CHUNK_SIZE)
                if not block:
                    break
                size_bytes += len(block)
                if size_bytes > MAX_ARTIFACT_BYTES:
                    raise ArtifactTooLargeError(
                        "artifact exceeds size limit "
                        f"({size_bytes} > {MAX_ARTIFACT_BYTES})"
                    )
                digest.update(block)
                dst.write(block)
            dst.flush()
            os.fsync(dst.fileno())
        return size_bytes, digest.hexdigest()

    async def _notify(self, artifact: Artifact) -> None:
        if self._broadcaster is not None:
            try:
                await self._broadcaster(
                    "artifact.created", {"artifact": artifact.public_dict()}
                )
            except Exception as exc:
                logger.warning("artifact.created broadcast failed: %s", exc)


def _safe_filename(filename: str) -> str:
    """生成 managed directory 内使用的安全文件名。"""

    cleaned = _UNSAFE_FILENAME_RE.sub("_", filename).strip().strip(".")
    return cleaned[:240] or "artifact"


def _cleanup_temp_file(path: Path) -> None:
    with suppress(OSError):
        path.unlink(missing_ok=True)


def _cleanup_artifact_dir(path: Path) -> None:
    """清理尚未写入 metadata 的单个 Artifact 目录。"""

    for child in path.iterdir() if path.exists() else ():
        with suppress(OSError):
            child.unlink()
    with suppress(OSError):
        path.rmdir()
