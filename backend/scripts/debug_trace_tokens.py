"""解析 Run 7c775080 的 model_started 预算字段，核对 trace 数字。"""
import json
import sqlite3
import sys

DB = "/Users/linghang/agent/vesta/backend/.vesta/vesta.db"
RUN = "7c77508048e34a07a03bb477df96f6d2"

con = sqlite3.connect(DB)
rows = con.execute(
    "SELECT sequence, type, payload_json FROM agent_events "
    "WHERE run_id=? AND type IN ('model_started','model_completed') ORDER BY sequence",
    (RUN,),
).fetchall()

for seq, typ, payload in rows:
    p = json.loads(payload)
    if typ == "model_started":
        print(
            f"seq={seq:>3} {typ:<15} step={p.get('step')} "
            f"input_after≈{p.get('prepared_input_tokens')} "
            f"orig≈{p.get('original_estimated_input_tokens')} "
            f"schemas≈{p.get('tool_schema_tokens')} "
            f"tr_before≈{p.get('tool_result_tokens_before')} "
            f"tr_after≈{p.get('tool_result_tokens_after')} "
            f"budget≈{p.get('tool_result_budget_tokens')} "
            f"stage={p.get('compaction_stage')} "
            f"compacted={p.get('compacted_tool_results')} "
            f"removed={p.get('removed_tool_rounds')} "
            f"msgs_before≈{p.get('message_tokens_before')} "
            f"msgs_after≈{p.get('message_tokens_after')} "
            f"requires={p.get('requires_compaction')} "
            f"reached={p.get('reached_target')} "
            f"force_final={p.get('force_final_answer')}"
        )
    else:
        msg = p.get("message") or {}
        tc = msg.get("tool_calls") or []
        print(
            f"seq={seq:>3} {typ:<15} step={p.get('step')} "
            f"tool_calls={len(tc)} content={(msg.get('content') or '')[:60]!r}"
        )
