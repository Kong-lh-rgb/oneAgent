#!/usr/bin/env python3
"""测试用假 Computer Helper：实现与 Swift helper 相同的 JSON Lines 协议。

用于 MacOSHelperClient 的 pytest，不依赖 Swift 构建。

隐藏测试方法（供测试验证错误处理路径）：
- ``__echo_id``     把收到的请求 id 回显到 result（验证 id correlation）；
- ``__bad_json``    向 stdout 写一行非 JSON，再回一个 error（验证非 JSON 不崩）；
- ``__unknown_id``  先回一条无主响应，再回 error（验证未知 id 被丢弃）；
- ``__malformed``   回一条有 id 但既无 result 也无 error 的响应；
- ``__crash``       直接 os._exit(1)（验证进程意外退出时 pending 被 reject）。
"""

import json
import os
import sys
import time

HELPER_VERSION = "0.0.1-test"


def write_response(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def error(msg_id, code: str, message: str) -> None:
    write_response({"id": msg_id, "error": {"code": code, "message": message}})


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            error(None, "invalid_request", "invalid json")
            continue
        if not isinstance(payload, dict):
            error(None, "invalid_request", "not an object")
            continue

        msg_id = payload.get("id")
        method = payload.get("method")
        if not isinstance(method, str) or not method:
            error(msg_id, "invalid_request", "missing method")
            continue

        if method == "ping":
            write_response({"id": msg_id, "result": {"ok": True}})
        elif method == "__echo_id":
            write_response(
                {"id": msg_id, "result": {"received_id": msg_id}}
            )
        elif method == "system_info":
            write_response(
                {
                    "id": msg_id,
                    "result": {
                        "platform": "macos",
                        "helper_version": HELPER_VERSION,
                        "process_id": os.getpid(),
                    },
                }
            )
        elif method == "open_app":
            # 模拟 Swift helper：不启动真实 App，只回结构化的成功结果。
            params = payload.get("params")
            app = params.get("app") if isinstance(params, dict) else None
            if not isinstance(app, str) or not app.strip():
                error(msg_id, "invalid_params", "missing or empty app")
            else:
                write_response(
                    {
                        "id": msg_id,
                        "result": {
                            "app": app,
                            "bundle_id": f"com.example.{app}",
                            "process_id": 4242,
                        },
                    }
                )
        elif method == "observe":
            write_response(
                {
                    "id": msg_id,
                    "result": {
                        "active_app": {
                            "name": "FakeApp",
                            "bundle_id": "com.example.fake",
                            "process_id": 4242,
                        },
                        "active_window": None,
                        "windows": [],
                        "elements": [],
                    },
                }
            )
        elif method == "__bad_json":
            sys.stdout.write("this is not json\n")
            sys.stdout.flush()
            error(msg_id, "bad_json_test", "sent bad json line")
        elif method == "__unknown_id":
            # 先发一条无主响应（id 不对应任何 pending），再正常回 error。
            write_response({"id": 999999, "result": {"ok": True}})
            error(msg_id, "unknown_id_test", "sent dangling response")
        elif method == "__malformed":
            # 有 id 但既无 result 也无 error。
            write_response({"id": msg_id})
        elif method == "__invalid_id":
            write_response({"id": "bad", "result": {"ok": True}})
        elif method == "__non_object":
            sys.stdout.write("[]\n")
            sys.stdout.flush()
        elif method == "__delay":
            delay = float((payload.get("params") or {}).get("seconds", 0.05))
            time.sleep(delay)
            write_response({"id": msg_id, "result": {"delayed": True}})
        elif method == "__crash":
            os._exit(1)
        else:
            error(msg_id, "unknown_method", f"unknown method: {method}")


if __name__ == "__main__":
    main()
