"""验证审批 store 旧库迁移：无 ui_scope 列时 ALTER 补列且幂等。"""
import asyncio
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.approval.store import SQLiteApprovalStore  # noqa: E402

# 造一个「旧结构」库：没有 ui_scope 列
tmp = tempfile.mkdtemp()
db = Path(tmp) / "old.db"
conn = sqlite3.connect(db)
conn.execute(
    """
    CREATE TABLE approvals (
        id TEXT PRIMARY KEY, run_id TEXT, conversation_id TEXT,
        tool_name TEXT NOT NULL, tool_call_id TEXT NOT NULL,
        arguments_json TEXT NOT NULL DEFAULT '{}', reason TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL, created_at TEXT NOT NULL, resolved_at TEXT
    )
    """
)
conn.commit()
conn.close()


async def main() -> None:
    store = SQLiteApprovalStore(db)
    await store.initialize()  # 应执行 ALTER 加 ui_scope
    r = await store.create(
        run_id=None,
        conversation_id=None,
        tool_name="computer_type",
        tool_call_id="c1",
        arguments={"text": "x"},
        ui_scope="desktop",
    )
    print("created ui_scope:", r.ui_scope)
    assert r.ui_scope == "desktop"

    r2 = await store.create(
        run_id=None,
        conversation_id=None,
        tool_name="run_shell_command",
        tool_call_id="c2",
        arguments={},
    )
    print("default ui_scope:", r2.ui_scope)
    assert r2.ui_scope == "sandbox"

    # 再次 initialize 幂等
    await store.initialize()
    print("migration idempotent OK")


asyncio.run(main())
print("OK")
