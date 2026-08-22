// macOS Computer Helper V7 —— 长驻 subprocess。
//
// 职责：从 stdin 按行读 JSON，向 stdout 按行写 JSON（JSON Lines 协议）。
// 本轮实现 ping / system_info / open_app / accessibility_status /
// basic_observe / observe（AX Element Observation）、click_element（AXPress）、
// type_text、key_press、scroll、focus_window、ScreenCaptureKit 截图与坐标点击。
//
// 关键边界：
// - stdout 只允许输出协议 JSON；日志一律写 stderr；
// - 非法 JSON / unknown method / 缺 method 都返回 error，进程不退出；
// - stdin EOF 时正常退出（exit 0）；
// - open_app 用 NSWorkspace 原生 API 打开应用，不用 shell / osascript /
//   subprocess，不模拟鼠标点 Dock；
// - basic_observe / observe 只读 frontmost app + focused window + 其 AX
//   children（NSWorkspace + AXUIElement），不截图、不 OCR、不点击；未授权
//   返回 accessibility_permission_required；
// - observe 返回可交互/有信息元素（role normalize + ref 归属当前
//   observation_id），限制 max_elements / max_depth 并防循环。

import AppKit
import ApplicationServices
import CoreGraphics
import Foundation
import MacOSComputerCore

/// helper 版本号（system_info 返回）。
private let helperVersion = "0.0.1"

/// 向 stdout 写一条 JSON Lines 响应并保证落盘（FileHandle 直接写，无缓冲）。
func writeResponse(_ object: [String: Any]) {
    do {
        let data = try JSONSerialization.data(withJSONObject: object)
        FileHandle.standardOutput.write(data)
        FileHandle.standardOutput.write(Data("\n".utf8))
    } catch {
        // 序列化失败不应导致崩溃；写 stderr 日志。
        FileHandle.standardError.write(
            Data("failed to serialize response: \(error)\n".utf8)
        )
    }
}

/// 构造一条 error 响应（id 缺省时用 null）。
func makeError(id: Any?, code: String, message: String) -> [String: Any] {
    return [
        "id": id ?? NSNull(),
        "error": [
            "code": code,
            "message": message,
        ],
    ]
}

func handleRequest(_ payload: [String: Any]) {
    let rawID = payload["id"]
    guard let rawID,
          CFGetTypeID(rawID as CFTypeRef) != CFBooleanGetTypeID(),
          let id = rawID as? Int else {
        writeResponse(makeError(id: nil, code: "invalid_request",
                                message: "request id must be an integer"))
        return
    }

    guard let method = payload["method"] as? String, !method.isEmpty else {
        writeResponse(makeError(id: id, code: "invalid_request", message: "missing method"))
        return
    }

    switch method {
    case "ping":
        writeResponse(["id": id, "result": ["ok": true]])

    case "system_info":
        let info = ProcessInfo.processInfo
        let version = info.operatingSystemVersion
        let macosVersion =
            "\(version.majorVersion).\(version.minorVersion).\(version.patchVersion)"
        writeResponse([
            "id": id,
            "result": [
                "platform": "macos",
                "helper_version": helperVersion,
                "process_id": info.processIdentifier,
                "macos_version": macosVersion,
            ],
        ])

    case "open_app":
        handleOpenApp(params: payload["params"], id: id)

    case "accessibility_status":
        handleAccessibilityStatus(params: payload["params"], id: id)

    case "screen_capture_status":
        handleScreenCaptureStatus(params: payload["params"], id: id)

    case "basic_observe":
        handleBasicObserve(params: payload["params"], id: id)

    case "observe":
        handleObserve(params: payload["params"], id: id)

    case "begin_session":
        handleBeginSession(params: payload["params"], id: id)

    case "end_session":
        handleEndSession(params: payload["params"], id: id)

    case "__test_ax_logic":
        handleTestAXLogic(params: payload["params"], id: id)

    case "__test_target_cache":
        handleTestTargetCache(params: payload["params"], id: id)

    case "__test_semantic_budget":
        handleTestSemanticBudget(params: payload["params"], id: id)

    case "__test_element_mapping":
        handleTestElementMapping(params: payload["params"], id: id)

    case "click_element":
        handleClickElement(params: payload["params"], id: id)

    case "click_coordinate":
        handleClickCoordinate(params: payload["params"], id: id)

    case "scroll":
        handleScroll(params: payload["params"], id: id)

    case "focus_window":
        handleFocusWindow(params: payload["params"], id: id)

    case "__test_v7_logic":
        handleTestV7Logic(params: payload["params"], id: id)

    case "__test_click_element":
        handleTestClickElement(params: payload["params"], id: id)

    case "type_text":
        handleTypeText(params: payload["params"], id: id)

    case "__test_type_logic":
        handleTestTypeLogic(params: payload["params"], id: id)

    case "key_press":
        handleKeyPress(params: payload["params"], id: id)

    case "__test_key_logic":
        handleTestKeyLogic(params: payload["params"], id: id)

    case "__test_type_success":
        handleTestTypeSuccess(params: payload["params"], id: id)

    case "__test_key_success":
        handleTestKeySuccess(params: payload["params"], id: id)

    case "__test_observation_cache":
        handleTestObservationCache(params: payload["params"], id: id)

    case "__test_fresh_guard":
        handleTestFreshGuard(params: payload["params"], id: id)

    case "__test_restore_target":
        handleTestRestoreTarget(params: payload["params"], id: id)

    case "__test_focus_element":
        handleTestFocusElement(params: payload["params"], id: id)

    default:
        writeResponse(
            makeError(
                id: id,
                code: "unknown_method",
                message: "unknown method: \(method)"
            )
        )
    }
}

// 主循环：逐行读 stdin，处理 JSON。
//
// 命令行进程必须显式初始化 CoreGraphics 会话：否则 ScreenCaptureKit 的
// SCContentFilter(desktopIndependentWindow:) 会触发
// “Assertion failed: (did_initialize), CGS_REQUIRE_INIT” 断言导致进程 abort。
// CGMainDisplayID() 会建立到 WindowServer 的 CGS 连接；失败时返回 0 但不崩溃。
_ = CGMainDisplayID()

while let raw = readLine(strippingNewline: true) {
    let trimmed = raw.trimmingCharacters(in: .whitespaces)
    if trimmed.isEmpty { continue }
    guard let data = trimmed.data(using: .utf8) else { continue }

    do {
        guard
            let json = try JSONSerialization.jsonObject(with: data)
                as? [String: Any]
        else {
            writeResponse(
                makeError(
                    id: nil,
                    code: "invalid_request",
                    message: "request must be a JSON object"
                )
            )
            continue
        }
        handleRequest(json)
    } catch {
        writeResponse(
            makeError(id: nil, code: "invalid_request", message: "invalid JSON")
        )
    }
}

// stdin EOF → 正常退出。
exit(0)
