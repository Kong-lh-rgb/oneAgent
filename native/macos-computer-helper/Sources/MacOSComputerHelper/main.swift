// macOS Computer Helper V2 —— 长驻 subprocess。
//
// 职责：从 stdin 按行读 JSON，向 stdout 按行写 JSON（JSON Lines 协议）。
// 本轮实现 ping / system_info / open_app / accessibility_status /
// basic_observe。
//
// 关键边界：
// - stdout 只允许输出协议 JSON；日志一律写 stderr；
// - 非法 JSON / unknown method / 缺 method 都返回 error，进程不退出；
// - stdin EOF 时正常退出（exit 0）；
// - open_app 用 NSWorkspace 原生 API 打开应用，不用 shell / osascript /
//   subprocess，不模拟鼠标点 Dock；
// - basic_observe 只读 frontmost app + focused window（NSWorkspace +
//   AXUIElement），不读完整 AX Tree、不截图、不点击；未授权返回
//   accessibility_permission_required。

import AppKit
import ApplicationServices
import Foundation

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

/// 把应用名称 / bundle id 解析为 .app 的 URL。
///
/// 先按名称（NSWorkspace.fullPath(forApplication:)），找不到再按 bundle id
/// （urlForApplication(withBundleIdentifier:)）。不解析到则返回 nil。
func resolveAppURL(_ app: String) -> URL? {
    if let path = NSWorkspace.shared.fullPath(forApplication: app),
       !path.isEmpty {
        return URL(fileURLWithPath: path)
    }
    if let url = NSWorkspace.shared.urlForApplication(
        withBundleIdentifier: app
    ) {
        return url
    }
    return nil
}

/// 处理 open_app 请求。
///
/// 成功 → {"app":..., "bundle_id":..., "process_id":...}
/// 参数缺失/空 → invalid_params；找不到应用 → app_not_found；
/// 启动失败 → app_launch_failed。不返回假的 success。
func handleOpenApp(params: Any?, id: Any?) {
    guard
        let params = params as? [String: Any],
        let app = params["app"] as? String,
        !app.trimmingCharacters(in: .whitespaces).isEmpty
    else {
        writeResponse(
            makeError(
                id: id,
                code: "invalid_params",
                message: "missing or empty 'app'"
            )
        )
        return
    }

    guard let url = resolveAppURL(app) else {
        writeResponse(
            makeError(
                id: id,
                code: "app_not_found",
                message: "application not found: \(app)"
            )
        )
        return
    }

    do {
        let running = try NSWorkspace.shared.launchApplication(
            at: url,
            options: [],
            configuration: [:]
        )
        // bundle id 缺省时用 NSNull，避免在 [String: Any] 里塞 nil。
        var bundleID: Any = NSNull()
        if let resolved =
            running.bundleIdentifier ?? Bundle(url: url)?.bundleIdentifier
        {
            bundleID = resolved
        }
        writeResponse([
            "id": id ?? NSNull(),
            "result": [
                "app": app,
                "bundle_id": bundleID,
                "process_id": running.processIdentifier,
            ],
        ])
    } catch {
        writeResponse(
            makeError(
                id: id,
                code: "app_launch_failed",
                message: "failed to launch \(app): \(error)"
            )
        )
    }
}

/// 处理 accessibility_status 请求。
///
/// 只检查 AXIsProcessTrusted()；默认不弹系统授权提示。仅当
/// params["prompt"] == true 且未授权时，才用 AXIsProcessTrustedWithOptions
/// 触发一次系统授权提示（不自动反复弹窗）。
func handleAccessibilityStatus(params: Any?, id: Any?) {
    let shouldPrompt = (params as? [String: Any])?["prompt"] as? Bool ?? false
    var trusted = AXIsProcessTrusted()
    if shouldPrompt && !trusted {
        let options = [
            kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true
        ] as CFDictionary
        trusted = AXIsProcessTrustedWithOptions(options)
    }
    writeResponse([
        "id": id ?? NSNull(),
        "result": ["trusted": trusted],
    ])
}

/// 读取 frontmost application 的结构化信息（无需 Accessibility 权限）。
func frontmostAppDict(_ app: NSRunningApplication) -> [String: Any] {
    var dict: [String: Any] = ["name": app.localizedName ?? "unknown"]
    if let bundleID = app.bundleIdentifier {
        dict["bundle_id"] = bundleID
    }
    dict["process_id"] = app.processIdentifier
    return dict
}

/// 通过 AX 读取 focused window 的 title / bounds。
/// 读不到 focused window 时返回 nil（不返回假数据）。
func focusedWindowDict(pid: pid_t) -> [String: Any]? {
    let appElement = AXUIElementCreateApplication(pid)
    var windowRef: CFTypeRef?
    guard
        AXUIElementCopyAttributeValue(
            appElement,
            kAXFocusedWindowAttribute as CFString,
            &windowRef
        ) == .success,
        let windowElement = windowRef
    else {
        return nil
    }

    // AXTitle
    var title = ""
    var titleRef: CFTypeRef?
    if AXUIElementCopyAttributeValue(
        windowElement as! AXUIElement,
        kAXTitleAttribute as CFString,
        &titleRef
    ) == .success, let value = titleRef as? String {
        title = value
    }

    // AXPosition
    var position = CGPoint.zero
    var positionRef: CFTypeRef?
    if AXUIElementCopyAttributeValue(
        windowElement as! AXUIElement,
        kAXPositionAttribute as CFString,
        &positionRef
    ) == .success,
        let value = positionRef,
        AXValueGetType(value as! AXValue) == .cgPoint
    {
        AXValueGetValue(value as! AXValue, .cgPoint, &position)
    }

    // AXSize
    var size = CGSize.zero
    var sizeRef: CFTypeRef?
    if AXUIElementCopyAttributeValue(
        windowElement as! AXUIElement,
        kAXSizeAttribute as CFString,
        &sizeRef
    ) == .success,
        let value = sizeRef,
        AXValueGetType(value as! AXValue) == .cgSize
    {
        AXValueGetValue(value as! AXValue, .cgSize, &size)
    }

    return [
        "title": title,
        "bounds": [
            "x": Int(position.x.rounded()),
            "y": Int(position.y.rounded()),
            "width": Int(size.width.rounded()),
            "height": Int(size.height.rounded()),
        ],
    ]
}

/// 处理 basic_observe 请求。
///
/// 需要 Accessibility 权限（AXIsProcessTrusted）；未授权返回
/// accessibility_permission_required。成功返回：
/// {"active_app": {...}, "active_window": {...} | null}
func handleBasicObserve(params: Any?, id: Any?) {
    guard AXIsProcessTrusted() else {
        writeResponse(
            makeError(
                id: id,
                code: "accessibility_permission_required",
                message: "macOS Accessibility permission is required"
            )
        )
        return
    }

    guard let frontmost = NSWorkspace.shared.frontmostApplication else {
        writeResponse([
            "id": id ?? NSNull(),
            "result": [
                "active_app": NSNull(),
                "active_window": NSNull(),
            ] as [String: Any],
        ])
        return
    }

    let activeWindow: Any =
        focusedWindowDict(pid: frontmost.processIdentifier) ?? NSNull()
    writeResponse([
        "id": id ?? NSNull(),
        "result": [
            "active_app": frontmostAppDict(frontmost),
            "active_window": activeWindow,
        ],
    ])
}

/// 处理一条已解析的请求，返回响应（或 nil 表示不响应）。
func handleRequest(_ payload: [String: Any]) {
    let id = payload["id"]

    guard let method = payload["method"] as? String, !method.isEmpty else {
        writeResponse(makeError(id: id, code: "invalid_request", message: "missing method"))
        return
    }

    switch method {
    case "ping":
        writeResponse(["id": id ?? NSNull(), "result": ["ok": true]])

    case "system_info":
        let info = ProcessInfo.processInfo
        let version = info.operatingSystemVersion
        let macosVersion =
            "\(version.majorVersion).\(version.minorVersion).\(version.patchVersion)"
        writeResponse([
            "id": id ?? NSNull(),
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

    case "basic_observe":
        handleBasicObserve(params: payload["params"], id: id)

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
