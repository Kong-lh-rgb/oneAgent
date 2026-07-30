import asyncio
from pathlib import Path

from app.models.types import ToolCall
from app.tools import ToolExecutor, ToolRegistry, WriteFileTool


async def main() -> None:
    workspace = Path(__file__).resolve().parents[2] / "workspace"

    registry = ToolRegistry()
    registry.register(WriteFileTool(workspace))

    executor = ToolExecutor(registry)

    result = await executor.execute(
        ToolCall(
            id="test-1",
            name="write_file",
            arguments={
                "path": "hello.txt",
                "content": "Hello OneAgent",
            },
        )
    )

    print(result.model_dump_json(indent=2))


asyncio.run(main())