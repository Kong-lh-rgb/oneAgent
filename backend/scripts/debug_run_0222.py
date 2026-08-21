"""解析 Run 0222e525 的工具结果与模型消息，定位输入失败原因。"""
import json
import sqlite3

DB = "/Users/linghang/agent/vesta/backend/.vesta/vesta.db"
RUN = "0222e525bf4845d4aa44280df35a8dfc"

con = sqlite3.connect(DB)
rows = con.execute(
    "SELECT sequence, type, payload_json FROM agent_events "
    "WHERE run_id=? ORDER BY sequence",
    (RUN,),
).fetchall()

for seq, typ, payload in rows:
    p = json.loads(payload)
    step = p.get("step")
    if typ == "tool_started":
        tc = p.get("tool_call") or {}
        arguments = json.dumps(tc.get("arguments"), ensure_ascii=False)
        print(
            f"seq={seq:>3} {typ:<20} step={step} "
            f"tool={tc.get('name')} args={arguments}"
        )
    elif typ == "tool_completed":
        tr = p.get("tool_result") or {}
        out = (tr.get("output") or "")
        if isinstance(out, str):
            out = out[:300]
        print(
            f"seq={seq:>3} {typ:<20} step={step} "
            f"success={tr.get('success')} error={tr.get('error')} output={out!r}"
        )
    elif typ in ("model_completed",):
        msg = p.get("message") or {}
        content = (msg.get("content") or "")[:400]
        print(f"seq={seq:>3} {typ:<20} step={step} content={content!r}")
    elif typ in (
        "agent_failed",
        "agent_completed",
        "memory_reflection_skipped",
        "memory_reflection_started",
    ):
        print(f"seq={seq:>3} {typ:<20} step={step} err={p.get('error')}")
