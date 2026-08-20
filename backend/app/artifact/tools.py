"""artifact_publish Agent Tool：Agent 显式发布用户交付物。"""

from __future__ import annotations

from typing import Any

from app.models.types import ToolDefinition, ToolPermission
from app.tools.base import BaseTool
from app.tools.hooks import ToolExecutionContext
from app.tools.registry import ToolRegistry

from .service import ArtifactService


class ArtifactPublishTool(BaseTool):
    """把真正交付给用户的文件 / 链接发布为不可变 Artifact。

    必须通过 ``execute_with_context`` 绑定真实 Run（run_id / conversation_id
    来自 ToolExecutionContext，模型无法伪造到其它 Run）。
    """

    def __init__(self, service: ArtifactService) -> None:
        self._service = service

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="artifact_publish",
            description=(
                "把真正交付给用户的文件或链接发布为 Artifact（不可变结果）。"
                "path（workspace 相对路径）或 url（http/https）二选一。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "workspace 相对文件路径。",
                    },
                    "url": {
                        "type": "string",
                        "description": "http(s) 结果链接（只存元数据，不下载）。",
                    },
                    "title": {"type": "string", "description": "标题。"},
                    "description": {"type": "string", "description": "说明。"},
                },
                "required": [],
                "additionalProperties": False,
            },
            permission=ToolPermission.ALLOWED,
            strict=False,
        )

    async def execute(self, arguments: dict[str, Any]) -> Any:
        # artifact_publish 需要运行上下文（run_id）；直接调用视为缺上下文。
        raise ValueError("artifact_publish requires run context")

    async def execute_with_context(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        if not context.run_id:
            raise ValueError("artifact_publish requires run context")

        allowed_arguments = {"path", "url", "title", "description"}
        unexpected = sorted(set(arguments) - allowed_arguments)
        if unexpected:
            raise ValueError(
                "unsupported artifact_publish arguments: " + ", ".join(unexpected)
            )

        path = arguments.get("path")
        url = arguments.get("url")
        if (path is None) == (url is None):
            raise ValueError("provide exactly one of 'path' or 'url'")

        title = str(arguments.get("title") or "")
        description = arguments.get("description")
        description = str(description) if description is not None else None

        if path is not None:
            artifact = await self._service.publish_file(
                path=str(path),
                title=title,
                description=description,
                run_id=context.run_id,
                conversation_id=context.conversation_id,
            )
        else:
            artifact = await self._service.publish_url(
                url=str(url),
                title=title,
                description=description,
                run_id=context.run_id,
                conversation_id=context.conversation_id,
            )
        return artifact.public_dict()


def register_artifact_tools(registry: ToolRegistry, service: ArtifactService) -> None:
    registry.register(ArtifactPublishTool(service))


__all__ = ["ArtifactPublishTool", "register_artifact_tools"]
