"""分析 observe 存储输出的长度与截断位置。"""
import json
import re
import sqlite3

DB = "/Users/linghang/agent/vesta/backend/.vesta/vesta.db"
RUN = "0222e525bf4845d4aa44280df35a8dfc"
SEQ = 20

con = sqlite3.connect(DB)
row = con.execute(
    "SELECT payload_json FROM agent_events WHERE run_id=? AND sequence=?",
    (RUN, SEQ),
).fetchone()
p = json.loads(row[0])
out = (p.get("tool_result") or {}).get("output") or ""
print("stored output len:", len(out))
print("windows count by ref:", len(re.findall(r'"ref": "w\d+"', out)))
print("elements field present:", '"elements"' in out)
print("screenshot_ref present:", '"screenshot_ref"' in out)
print("truncated marker present:", '"truncated"' in out)
print("last 300 chars:", repr(out[-300:]))
