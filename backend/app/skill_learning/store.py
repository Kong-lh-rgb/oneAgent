"""Skill Learning 的轻量持久化：Candidate 与 Mining Watermark。

采用与 FileTaskStore 一致的"JSON 文件 + 临时文件原子替换"风格：
- Candidate 每个 ``<id>.json`` 一个文件；
- Watermark 单独一个 ``skill_learning_watermark.json``。

Watermark 保证：同一个 Completed Task 不会因为重启或重复扫描被反复学习。
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

from .models import SkillCandidate, SkillCandidateStatus

logger = logging.getLogger("oneagent.skill_learning.store")

WATERMARK_FILE_NAME = "skill_learning_watermark.json"
WATERMARK_VERSION = 1
MAX_CANDIDATE_FILE_BYTES = 500_000


class MiningWatermark(BaseModel):
    """Mining 扫描水位（持久化）。

    - ``processed_task_ids``：已进入过扫描的 Completed Task，永不重复计数；
    - ``pending_task_ids``：已累计但尚未凑满 batch 的 Completed Task；
    - ``last_mining_at``：最近一次实际触发 mining 的时间。
    """

    model_config = ConfigDict(extra="forbid")

    version: int = WATERMARK_VERSION
    processed_task_ids: tuple[str, ...] = ()
    pending_task_ids: tuple[str, ...] = ()
    last_mining_at: datetime | None = None
    last_error: str | None = None

    @field_validator("last_mining_at")
    @classmethod
    def normalize_datetime(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("watermark datetimes must include timezone information")
        return value.astimezone(UTC)

    def model_dump_json(self) -> str:
        return super().model_dump_json()

    @classmethod
    def model_validate_json(cls, json_data: str) -> MiningWatermark:
        return super().model_validate_json(json_data)


class SkillCandidateStore:
    """SkillCandidate 与 Mining Watermark 的本地 JSON 持久化。"""

    def __init__(
        self,
        data_dir: str | Path,
    ) -> None:
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.candidates_dir = self.data_dir / "candidates"
        self.watermark_path = self.data_dir / WATERMARK_FILE_NAME
        self._locks: dict[str, asyncio.Lock] = {}

    async def initialize(self) -> None:
        """创建数据目录。"""

        await asyncio.to_thread(self.candidates_dir.mkdir, parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Candidates
    # ------------------------------------------------------------------

    def _candidate_path(self, candidate_id: str) -> Path:
        return self.candidates_dir / f"{candidate_id}.json"

    async def create(self, candidate: SkillCandidate) -> SkillCandidate:
        """写入候选；ID 冲突时抛 ValueError。"""

        async with self._lock_for(candidate.id):
            path = self._candidate_path(candidate.id)
            if await asyncio.to_thread(path.is_file):
                raise ValueError(f"candidate already exists: {candidate.id}")
            await asyncio.to_thread(
                _write_json,
                path,
                candidate.model_dump(mode="json"),
            )
        return candidate

    async def get(self, candidate_id: str) -> SkillCandidate | None:
        """按完整 ID 读取候选。"""

        normalized = candidate_id.strip().lower()
        if not normalized:
            return None
        path = self._candidate_path(normalized)
        if not await asyncio.to_thread(path.is_file):
            return None
        if await asyncio.to_thread(path.is_symlink):
            return None
        try:
            return await asyncio.to_thread(_read_candidate, path)
        except (ValueError, OSError, UnicodeError):
            return None

    async def list(
        self,
        *,
        status: SkillCandidateStatus | None = None,
    ) -> tuple[SkillCandidate, ...]:
        """按创建时间倒序列出候选，可按状态过滤。"""

        candidates: list[SkillCandidate] = []
        for path in await asyncio.to_thread(self._list_candidate_files):
            candidate = await self.get(path.stem)
            if candidate is None:
                continue
            if status is not None and candidate.status is not status:
                continue
            candidates.append(candidate)
        candidates.sort(key=lambda item: item.created_at, reverse=True)
        return tuple(candidates)

    def _list_candidate_files(self) -> list[Path]:
        if not self.candidates_dir.is_dir():
            return []
        return sorted(self.candidates_dir.glob("*.json"))

    async def update(self, candidate: SkillCandidate) -> SkillCandidate:
        """覆盖写入候选（用于 accept / reject 状态推进）。"""

        async with self._lock_for(candidate.id):
            await asyncio.to_thread(
                _write_json,
                self._candidate_path(candidate.id),
                candidate.model_dump(mode="json"),
            )
        return candidate

    async def find_duplicate_source(
        self,
        source_task_ids: tuple[str, ...],
    ) -> SkillCandidate | None:
        """查找来源 Task 集合完全相同的已存在候选（避免重复创建）。"""

        source_set = set(source_task_ids)
        for candidate in await self.list():
            if set(candidate.source_task_ids) == source_set:
                return candidate
        return None

    # ------------------------------------------------------------------
    # Watermark
    # ------------------------------------------------------------------

    async def load_watermark(self) -> MiningWatermark:
        """读取水位；文件缺失或损坏时返回空水位。"""

        if not await asyncio.to_thread(self.watermark_path.is_file):
            return MiningWatermark()
        try:
            raw = await asyncio.to_thread(
                self.watermark_path.read_text,
                encoding="utf-8",
            )
            return MiningWatermark.model_validate_json(raw)
        except (ValueError, OSError, UnicodeError):
            logger.warning("skill learning watermark is unreadable; resetting")
            return MiningWatermark()

    async def save_watermark(self, watermark: MiningWatermark) -> None:
        """原子写入水位。"""

        async with self._lock_for("watermark"):
            await asyncio.to_thread(
                _write_json,
                self.watermark_path,
                watermark.model_dump(mode="json"),
            )

    # ------------------------------------------------------------------
    # Proposal（UPDATE accept 的 replacement 内容，不覆盖正式 Skill）
    # ------------------------------------------------------------------

    def proposal_path(self, candidate_id: str) -> Path:
        return self.data_dir / "proposals" / f"{candidate_id}.md"

    async def write_proposal(self, candidate_id: str, markdown: str) -> Path:
        """把 UPDATE 的 replacement SKILL.md 写入 proposals 目录。"""

        path = self.proposal_path(candidate_id)
        await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(path.write_text, markdown, encoding="utf-8")
        return path

    # ------------------------------------------------------------------
    # 基础设施
    # ------------------------------------------------------------------

    def _lock_for(self, key: str) -> asyncio.Lock:
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        return self._locks[key]


def _read_candidate(path: Path) -> SkillCandidate:
    if path.stat().st_size > MAX_CANDIDATE_FILE_BYTES:
        raise ValueError("candidate file too large")
    return SkillCandidate.model_validate_json(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(f".tmp.{datetime.now(UTC).strftime('%Y%m%d%H%M%S%f')}")
    temp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp.replace(path)


__all__ = ["MiningWatermark", "SkillCandidateStore", "WATERMARK_VERSION"]
