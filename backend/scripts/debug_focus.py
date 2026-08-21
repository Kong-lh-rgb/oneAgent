"""检查 observe 输出中是否有 focused 元素，以及 e99/e100 等元素类型。"""
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

print("== focused:true 出现次数:", out.count('"focused":true'))
# 打印包含 focused:true 的片段
for m in re.finditer(r'"focused":true', out):
    start = max(0, m.start() - 120)
    print("  ...", out[start : m.end() + 20], "...")

# 打印 e99 / e100 / e90-e110 元素
print("\n== 元素片段查找 ==")
for ref in ("e99", "e100", "e101", "e102"):
    # 找 "ref":"e99" 的位置，截取该元素对象
    idx = out.find(f'"ref":"{ref}"')
    if idx == -1:
        print(f"{ref}: 未找到")
        continue
    print(f"{ref}:", out[idx - 30 : idx + 200])
