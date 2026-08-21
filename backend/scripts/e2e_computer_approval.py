"""E2E 验收 v2：自动放行普通审批，捕获并解决 computer 审批。

连 ws://127.0.0.1:8000/rpc：
1. 清理遗留 pending 普通审批（approve 放行）
2. conversation.send("打开备忘录，输入测试两个字")
3. 监听 approval.required：
   - 普通审批 → approve（放行，验证浮窗忽略它）
   - computer_* → 捕获
4. approval.approve 解决 computer 审批
5. 打印 approval.resolved
"""

import asyncio
import json
import sys

try:
    import websockets
except ImportError:
    print("websockets 未安装")
    sys.exit(2)

URL = "ws://127.0.0.1:8000/rpc"
_request_id = 0

# 默认提示词；可用命令行参数覆盖（argv[1]）。
DEFAULT_CONTENT = "打开备忘录，输入测试两个字"


def req(method: str, params: dict | None = None) -> dict:
    global _request_id
    _request_id += 1
    return {
        "jsonrpc": "2.0",
        "id": _request_id,
        "method": method,
        "params": params or {},
    }


async def call(ws, method, params=None, timeout=15):
    await ws.send(json.dumps(req(method, params)))
    while True:
        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))
        if "id" in msg:
            return msg


async def main() -> int:
    content = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_CONTENT
    async with websockets.connect(URL) as ws:
        # 1. 清理遗留 pending 普通审批（避免旧 Run 阻塞）
        listing = await call(ws, "approval.list", {"status": "pending"})
        pending = listing.get("result", {}).get("approvals", [])
        for appr in pending:
            if appr.get("tool_name", "").startswith("computer_"):
                print("遗留 computer pending:", appr.get("tool_name"))
            else:
                await call(ws, "approval.approve", {"approval_id": appr["id"]})
                print("已放行遗留普通审批:", appr.get("tool_name"))

        created = await call(ws, "conversation.create", {"title": "E2E 验收 v2"})
        conv_id = created.get("result", {}).get("conversation", {}).get("id")
        print("conversation:", conv_id)

        await ws.send(
            json.dumps(
                req(
                    "conversation.send",
                    {"conversation_id": conv_id, "content": content},
                )
            )
        )
        print(f"已发送任务（{content[:40]}...），等待审批事件……")

        approval_id = None
        start = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start < 120:
            try:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=20))
            except TimeoutError:
                print("20s 无事件，继续等待……")
                continue
            method = msg.get("method")
            params = msg.get("params") or {}
            if method == "approval.required":
                appr = params.get("approval") or {}
                print(
                    "approval.required:",
                    appr.get("tool_name"),
                    appr.get("id", "")[:8],
                )
                if appr.get("tool_name", "").startswith("computer_"):
                    approval_id = appr.get("id")
                    break
                # 普通审批：放行让 Run 继续，验证浮窗忽略它。
                await call(ws, "approval.approve", {"approval_id": appr["id"]})
                print("  已放行普通审批（浮窗应忽略）")
            elif method == "run.status":
                print("run.status:", params.get("status"))

        if not approval_id:
            print("未在超时内等到 computer approval")
            return 1

        print(">>> 捕获到 computer approval，approve 中……")
        result = await call(ws, "approval.approve", {"approval_id": approval_id})
        status = result.get("result", {}).get("approval", {}).get("status")
        print("approval.approve ->", status)

        resolved_deadline = asyncio.get_event_loop().time() + 10
        while asyncio.get_event_loop().time() < resolved_deadline:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
            if msg.get("method") == "approval.resolved":
                print(
                    "approval.resolved:",
                    (msg.get("params") or {}).get("approval", {}).get("status"),
                )
                return 0
        print("未等到 approval.resolved")
        return 1


if __name__ == "__main__":
    try:
        code = asyncio.run(main())
    except Exception as exc:  # noqa: BLE001
        print("E2E 异常:", repr(exc))
        code = 1
    sys.exit(code)
