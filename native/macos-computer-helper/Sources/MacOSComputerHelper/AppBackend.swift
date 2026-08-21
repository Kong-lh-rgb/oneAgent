import AppKit
import ApplicationServices
import CoreGraphics
import Foundation
import MacOSComputerCore

// AppBackend.swift

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

/// 激活目标应用并确认它确实成为 macOS frontmost application。
///
/// `launchApplication` 对已经运行的 App 只保证返回进程，不保证把窗口带到前台；
/// Target 与 frontmost 已分离；这里的确认结果只描述激活状态，不决定启动成功。
func activateAndConfirmFrontmost(_ application: NSRunningApplication) -> Bool {
    if NSWorkspace.shared.frontmostApplication?.processIdentifier
        == application.processIdentifier {
        return true
    }
    requestApplicationFrontmost(application)
    for _ in 0..<30 {
        if NSWorkspace.shared.frontmostApplication?.processIdentifier
            == application.processIdentifier {
            return true
        }
        Thread.sleep(forTimeInterval: 0.05)
    }
    return false
}

/// 处理 open_app 请求。
///
/// 启动成功 → 返回进程身份以及独立的 launch_status / activation_status。
/// 参数缺失/空 → invalid_params；找不到应用 → app_not_found；
/// 启动失败 → app_launch_failed。不返回假的 success。
func handleOpenApp(params: Any?, id: Any?) {
    guard
        let params = params as? [String: Any],
        let app = params["app"] as? String,
        !app.trimmingCharacters(in: .whitespaces).isEmpty,
        let sessionID = params["session_id"] as? String,
        !sessionID.isEmpty
    else {
        writeResponse(
            makeError(
                id: id,
                code: "invalid_params",
                message: "missing or empty 'app' / 'session_id'"
            )
        )
        return
    }
    switch checkSession(sessionID) {
    case .ok:
        break
    case .notActive:
        writeResponse(makeError(
            id: id,
            code: "session_not_active",
            message: "no active computer session; begin_session required"
        ))
        return
    case .mismatch:
        writeResponse(makeError(
            id: id,
            code: "session_mismatch",
            message: "session mismatch; does not match the active run"
        ))
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
        clearObservationCache()
        setComputerTarget(running)
        let activationConfirmed = activateAndConfirmFrontmost(running)
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
                "launch_status": "running",
                "activation_status": (
                    activationConfirmed ? "frontmost" : "not_frontmost"
                ),
                "frontmost_verified": activationConfirmed,
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

/// 处理 begin_session 请求：唯一可以创建 Native Session 的入口。
/// params: {"session_id": "..."}。已有其它 session 时拒绝（不接管）。
func handleBeginSession(params: Any?, id: Any?) {
    guard let params = params as? [String: Any],
          let sessionID = params["session_id"] as? String,
          !sessionID.isEmpty else {
        writeResponse(makeError(id: id, code: "invalid_params",
                                message: "missing 'session_id'")); return
    }
    let accepted = beginSession(sessionID)
    writeResponse(["id": id ?? NSNull(), "result": ["accepted": accepted]])
}

/// 处理 end_session 请求：结束 Run 的 Session，清除 Native Target / Snapshot。
/// params: {"session_id": "..."}。session 不匹配返回 ended=false（幂等）。
func handleEndSession(params: Any?, id: Any?) {
    guard let params = params as? [String: Any],
          let sessionID = params["session_id"] as? String,
          !sessionID.isEmpty else {
        writeResponse(makeError(id: id, code: "invalid_params",
                                message: "missing 'session_id'")); return
    }
    let ended = endSession(sessionID)
    writeResponse(["id": id ?? NSNull(), "result": ["ended": ended]])
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

/// 读取 focused window 的 AX 信息（dict）+ AX 元素本身。
/// 读不到 focused window 时返回 nil（不返回假数据）。
func focusedWindow(pid: pid_t) -> (info: [String: Any], element: AXUIElement)? {
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
    let element = windowElement as! AXUIElement
    return (windowInfoDict(element), element)
}

/// 通过 AX 读取 focused window 的 title / bounds（仅 dict 形式）。
func focusedWindowDict(pid: pid_t) -> [String: Any]? {
    return focusedWindow(pid: pid)?.info
}

/// 从 AX 窗口元素读取 title / bounds。
func windowInfoDict(_ windowElement: AXUIElement) -> [String: Any] {
    var title = ""
    if let value = readAXString(windowElement, kAXTitleAttribute as CFString) {
        title = value
    }
    let position = readAXPoint(windowElement, kAXPositionAttribute as CFString)
    let size = readAXSize(windowElement, kAXSizeAttribute as CFString)
    return [
        "title": title,
        "bounds": [
            "x": Int(position?.x ?? 0),
            "y": Int(position?.y ?? 0),
            "width": Int(size?.width ?? 0),
            "height": Int(size?.height ?? 0),
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

// ---------------------------------------------------------------------------
// V3：AX Element Observation
// ---------------------------------------------------------------------------

/// 截断长字符串。
