"""容错解析 observe 输出，找聚焦元素与界面结构。"""
import json
import sqlite3

DB = "/Users/linghang/agent/vesta/backend/.vesta/vesta.db"
RUN = "0222e525bf4845d4aa44280df35a8dfc"
SEQS = (20, 49, 97, 111)

con = sqlite3.connect(DB)
for seq in SEQS:
    row = con.execute(
        "SELECT payload_json FROM agent_events WHERE run_id=? AND sequence=?",
        (RUN, seq),
    ).fetchone()
    if row is None:
        continue
    p = json.loads(row[0])
    tr = p.get("tool_result") or {}
    out = tr.get("output") or ""
    dec = json.JSONDecoder()
    try:
        data, _ = dec.raw_decode(out)
    except Exception:
        print(f"=== seq={seq} (raw truncated) ===\n{out[:300]}\n")
        continue
    print(f"=== seq={seq} ===")
    print("active_app:", data.get("active_app"))
    aw = data.get("active_window") or {}
    print("active_window:", aw.get("title"), "ref:", aw.get("ref"), "bounds:", aw.get("bounds"))
    elems = data.get("elements") or []
    print(f"elements={len(elems)} truncated={data.get('truncated')}")
    focused = [e for e in elems if e.get("focused")]
    for e in focused:
        print("  FOCUSED:", json.dumps(e, ensure_ascii=False))
    for e in elems[:12]:
        print(
            "  ",
            json.dumps(
                {k: e.get(k) for k in ("ref", "role", "title", "value", "focused")},
                ensure_ascii=False,
            ),
        )
    print()
