"""Computer Runtime V0 领域模型（纯数据结构，不依赖真实 macOS）。

这些类型只表达"一次屏幕观察 / 一个交互目标 / 一次操作结果"的契约，
不包含任何 AXUIElement / ScreenCaptureKit / CGEvent 等系统实现。

关键约定：
- ``Bounds`` 是统一的矩形结构（x/y/width/height），不用 tuple/list 表达；
- ``CoordinateTarget.x/y`` 是 observation screenshot coordinate（截图坐标系），
  V0 不做 Retina / macOS 坐标换算；
- ``ElementTarget`` 必须绑定 ``observation_id`` —— element ref 只在对应
  Observation 内有效，不能被当成永久 UI ID。
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ActionName(StrEnum):
    """ComputerRuntime 支持的动作名称（稳定，供上层复用）。"""

    CLICK = "click"
    TYPE = "type"
    KEY = "key"
    SCROLL = "scroll"
    OPEN_APP = "open_app"
    FOCUS_WINDOW = "focus_window"


class DeliveryStatus(StrEnum):
    """系统输入事件是否已经投递到目标进程。"""

    NOT_APPLICABLE = "not_applicable"
    DELIVERED = "delivered"
    FAILED = "failed"


class VerificationStatus(StrEnum):
    """动作对界面造成的效果是否已经通过 AX 状态确认。"""

    NOT_APPLICABLE = "not_applicable"
    VERIFIED = "verified"
    UNVERIFIED = "unverified"
    MISMATCH = "mismatch"


class Bounds(BaseModel):
    """统一的矩形区域（整数坐标与尺寸，避免浮点抖动）。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    x: int
    y: int
    width: int
    height: int

    @field_validator("width", "height")
    @classmethod
    def non_negative_size(cls, value: int) -> int:
        """宽度 / 高度不能为负数。"""

        if value < 0:
            raise ValueError("bounds width/height cannot be negative")
        return value


class ActiveApp(BaseModel):
    """当前活动应用。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    bundle_id: str | None = None
    pid: int | None = None

    @field_validator("name")
    @classmethod
    def name_required(cls, value: str) -> str:
        normalized = " ".join(value.split()).strip()
        if not normalized:
            raise ValueError("active app name cannot be empty")
        return normalized

    @field_validator("pid")
    @classmethod
    def pid_valid(cls, value: int | None) -> int | None:
        """pid 如果存在必须是正整数。"""

        if value is not None and value <= 0:
            raise ValueError("app pid must be a positive integer")
        return value


class Window(BaseModel):
    """一次观察中的一个窗口。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    ref: str
    title: str = ""
    bounds: Bounds

    @field_validator("ref")
    @classmethod
    def ref_required(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("window ref cannot be empty")
        return normalized


class Element(BaseModel):
    """一次观察中的一个 UI 元素。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    ref: str
    role: str = ""
    title: str | None = None
    value: str | None = None
    enabled: bool = True
    focused: bool = False
    editable: bool = False
    bounds: Bounds | None = None
    # 元素可执行的动作（AX 语义，如 "press" / "select"），与
    # ComputerRuntime 的 ActionName 是不同词汇表，因此用字符串表达。
    actions: tuple[str, ...] = ()

    @field_validator("ref")
    @classmethod
    def ref_required(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("element ref cannot be empty")
        return normalized


class Observation(BaseModel):
    """一次屏幕观察的结构化快照。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(default_factory=lambda: uuid4().hex)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    active_app: ActiveApp | None = None
    active_window: Window | None = None
    windows: tuple[Window, ...] = ()
    elements: tuple[Element, ...] = ()
    focused_element_ref: str | None = None
    truncated: bool = False
    screenshot_ref: str | None = None

    @field_validator("id")
    @classmethod
    def id_required(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("observation id cannot be empty")
        return normalized

    @field_validator("created_at")
    @classmethod
    def created_at_valid(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must include timezone information")
        return value.astimezone(UTC)


class ElementTarget(BaseModel):
    """引用某次 Observation 中的元素。

    语义：element ref 只在对应 Observation 内有效，必须同时绑定
    ``observation_id``；不允许只有 element_ref（不能把 ref 当永久 UI ID）。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    observation_id: str
    element_ref: str

    @field_validator("observation_id", "element_ref")
    @classmethod
    def required_non_empty(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("observation_id and element_ref cannot be empty")
        return normalized


class CoordinateTarget(BaseModel):
    """引用某次 Observation 截图坐标。

    语义：``x / y`` 是 observation screenshot coordinate（截图坐标系），
    不是全局屏幕坐标。V0 不实现 Retina / macOS 坐标换算。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    observation_id: str
    x: int
    y: int

    @field_validator("observation_id")
    @classmethod
    def observation_id_required(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("observation_id cannot be empty")
        return normalized


# V0 支持的两类交互目标。
Target = ElementTarget | CoordinateTarget


class ActionResult(BaseModel):
    """一次 ComputerRuntime 动作的结果（轻量，不做复杂异常体系）。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    success: bool
    action: ActionName
    observation_id: str | None = None
    error: str | None = None
    delivery_status: DeliveryStatus = DeliveryStatus.NOT_APPLICABLE
    verification_status: VerificationStatus = VerificationStatus.NOT_APPLICABLE
    metadata: dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "ActionName",
    "ActionResult",
    "ActiveApp",
    "Bounds",
    "CoordinateTarget",
    "DeliveryStatus",
    "Element",
    "ElementTarget",
    "Observation",
    "Target",
    "VerificationStatus",
    "Window",
]
