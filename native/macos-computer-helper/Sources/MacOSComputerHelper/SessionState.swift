import AppKit
import ApplicationServices
import Foundation
import MacOSComputerCore

// MARK: - Run-scoped Session
//
// Machine Lease 保证同一时间只有一个 Run 控制 Computer，helper 只维护一个
// active session。所有请求必须携带 session_id，旧 session 的请求 fail closed。
var activeSessionID: String? = nil

// MARK: - Target（Agent 正在操作的应用；与 User Frontmost 分离）
var computerTargetPID: pid_t? = nil
var computerTargetBundleID: String? = nil
var computerTargetName: String? = nil

// MARK: - 最近一次 Snapshot（AX 原生对象，不向 Python 暴露 pointer）
var currentObservationID: String? = nil
var currentElements: [String: AXUIElement] = [:]
var currentWindows: [String: AXUIElement] = [:]
var currentScreenshotMapping: ScreenshotMapping? = nil
/// 保存的是最近一次 observe 的 Target PID（曾误命名 currentFrontmostPID）。
/// Target 不随 macOS frontmost App 漂移。
var currentTargetPID: pid_t? = nil
var currentFocusedWindow: AXUIElement? = nil
var currentFocusedWindowBounds: CGRect? = nil
var currentFocusedElementRef: String? = nil

// MARK: - Session lifecycle

/// 首次收到某个 session_id 时建立它，并清空上一个 Run 的 Target / Snapshot。
/// 同 session 的后续请求幂等。
func beginSessionIfNeeded(_ sessionID: String) {
    if activeSessionID == sessionID { return }
    clearComputerTarget()
    clearObservationCache()
    activeSessionID = sessionID
}

/// 结束 session：清除 Native Target / Snapshot。session 不匹配返回 false。
func endSession(_ sessionID: String) -> Bool {
    guard activeSessionID == sessionID else { return false }
    clearComputerTarget()
    clearObservationCache()
    activeSessionID = nil
    return true
}

/// 请求携带的 session_id 必须是当前 active session，否则 fail closed。
func validateSession(_ sessionID: String) -> Bool {
    activeSessionID == sessionID
}

// MARK: - Target

func setComputerTarget(_ application: NSRunningApplication) {
    computerTargetPID = application.processIdentifier
    computerTargetBundleID = application.bundleIdentifier
    computerTargetName = application.localizedName
}

func clearComputerTarget() {
    clearObservationCache()
    computerTargetPID = nil
    computerTargetBundleID = nil
    computerTargetName = nil
}

func runningComputerTarget() -> NSRunningApplication? {
    guard let pid = computerTargetPID,
          let application = NSRunningApplication(processIdentifier: pid),
          !application.isTerminated else { return nil }
    return application
}

func clearObservationCache() {
    currentObservationID = nil
    currentElements = [:]
    currentWindows = [:]
    currentScreenshotMapping = nil
    currentTargetPID = nil
    currentFocusedWindow = nil
    currentFocusedWindowBounds = nil
    currentFocusedElementRef = nil
}

// MARK: - 两种 freshness
//
// V2：Target 是 Agent 正在操作的应用，User Frontmost 是用户当前使用的应用，
// 两者可以不同。observe / background action 只要求 Target 与 Snapshot 一致，
// 不要求 Target 位于最前台。

private func nearlyEqual(_ lhs: CGFloat, _ rhs: CGFloat) -> Bool {
    abs(lhs - rhs) < 1
}

/// 对明确 PID 的应用发出 best-effort 前台请求；AXFrontmost 用于补强
/// NSRunningApplication.activate 在 Electron 审批交互后的失败场景。
func requestApplicationFrontmost(
    _ application: NSRunningApplication,
    window: AXUIElement? = nil
) {
    application.activate(options: [.activateAllWindows, .activateIgnoringOtherApps])
    let appElement = AXUIElementCreateApplication(application.processIdentifier)
    _ = AXUIElementSetAttributeValue(
        appElement,
        kAXFrontmostAttribute as CFString,
        kCFBooleanTrue
    )
    if let window {
        _ = AXUIElementSetAttributeValue(
            window, kAXMainAttribute as CFString, kCFBooleanTrue
        )
        _ = AXUIElementSetAttributeValue(
            window, kAXFocusedAttribute as CFString, kCFBooleanTrue
        )
        _ = AXUIElementPerformAction(window, kAXRaiseAction as CFString)
    }
}

/// Freshness A —— targetSnapshotIsFresh：不要求 frontmost。
/// 校验 session / observation / target 存活 / target 窗口仍然有效。
func targetSnapshotIsFresh(
    expectedObservationID: String,
    sessionID: String
) -> Bool {
    guard validateSession(sessionID),
          expectedObservationID == currentObservationID,
          let expectedPID = currentTargetPID,
          computerTargetPID == expectedPID,
          runningComputerTarget() != nil,
          let expectedWindow = currentFocusedWindow,
          let focused = focusedWindow(pid: expectedPID),
          CFEqual(focused.element, expectedWindow) else { return false }
    return true
}

/// Freshness B —— foregroundTargetIsFresh：在 A 基础上再要求 target 是 frontmost。
func foregroundTargetIsFresh(
    expectedObservationID: String,
    sessionID: String
) -> Bool {
    guard targetSnapshotIsFresh(
              expectedObservationID: expectedObservationID,
              sessionID: sessionID
          ),
          let expectedPID = currentTargetPID,
          let frontmost = NSWorkspace.shared.frontmostApplication else {
        return false
    }
    return frontmost.processIdentifier == expectedPID
}

/// 兼容旧协议：严格 freshness（target + frontmost + bounds 三者一致）。
func desktopStateIsFresh(
    expectedObservationID: String,
    requireStableBounds: Bool = false
) -> Bool {
    guard targetSnapshotIsFresh(
              expectedObservationID: expectedObservationID,
              sessionID: activeSessionID ?? ""
          ) else { return false }
    guard let expectedPID = currentTargetPID,
          let frontmost = NSWorkspace.shared.frontmostApplication,
          frontmost.processIdentifier == expectedPID else { return false }
    if !requireStableBounds { return true }
    guard let expectedBounds = currentFocusedWindowBounds,
          let focused = focusedWindow(pid: expectedPID) else { return false }
    let info = focused.info["bounds"] as? [String: Int]
    guard let info else { return false }
    return nearlyEqual(CGFloat(info["x"] ?? 0), expectedBounds.minX)
        && nearlyEqual(CGFloat(info["y"] ?? 0), expectedBounds.minY)
        && nearlyEqual(CGFloat(info["width"] ?? 0), expectedBounds.width)
        && nearlyEqual(CGFloat(info["height"] ?? 0), expectedBounds.height)
}

/// 把本次 Observation 记录的“已批准目标”App/Window 恢复到前台，并确认仍是该目标。
///
/// 场景：computer_type / click / key 需要人工审批，Vesta 审批 UI 会抢走 macOS
/// 焦点；批准后执行前先把之前观察到的 App/Window 恢复到前台，再重新验证仍是
/// 同一个目标。确认不了（App 已退出 / 窗口已关闭 / 无法恢复）返回 false，
/// 调用方按 stale 安全失败，绝不盲目操作。
func restoreRecordedTarget(requireStableBounds: Bool = false) -> Bool {
    guard let pid = currentTargetPID,
          computerTargetPID == pid,
          runningComputerTarget() != nil,
          let window = currentFocusedWindow,
          let observationID = currentObservationID else {
        return false
    }
    // 1) 把记录的 App 激活到前台（激活过程异步生效）。
    NSRunningApplication(processIdentifier: pid)?.activate(options: [])
    // 2) 把记录的 focused window 抬到最前（幂等；窗口已关闭时失败无害）。
    _ = AXUIElementPerformAction(window, kAXRaiseAction as CFString)
    // 3) 短轮询等待前台切换，并确认仍是记录的目标 App/Window。
    for _ in 0..<10 {
        if desktopStateIsFresh(expectedObservationID: observationID,
                               requireStableBounds: requireStableBounds) {
            return true
        }
        Thread.sleep(forTimeInterval: 0.05)
    }
    return false
}

/// 副作用执行前的严格 freshness（兼容旧调用）：目标、窗口与 macOS 前台一致。
func requireFreshObservation(
    _ observationID: String,
    id: Any?,
    restoreOnDrift: Bool = false,
    requireStableBounds: Bool = false
) -> Bool {
    if desktopStateIsFresh(
        expectedObservationID: observationID,
        requireStableBounds: requireStableBounds
    ) {
        return true
    }
    if restoreOnDrift && restoreRecordedTarget(
        requireStableBounds: requireStableBounds
    ) {
        return true
    }
    clearObservationCache()
    writeResponse(makeError(id: id, code: "stale_observation",
                            message: "desktop state changed since observation"))
    return false
}

/// Background-first freshness gate（V2）：
/// 只要求 Session Target 与 Snapshot 一致，不要求 Target 位于 macOS 前台。
/// Snapshot 漂移时允许 foreground restore 兜底（需已人工批准的动作）。
/// 失败返回结构化 stale_snapshot，并清空本地 Observation 缓存。
func requireBackgroundFresh(
    _ observationID: String,
    sessionID: String,
    id: Any?,
    allowForegroundRestore: Bool
) -> Bool {
    if targetSnapshotIsFresh(
        expectedObservationID: observationID,
        sessionID: sessionID
    ) {
        return true
    }
    if allowForegroundRestore && restoreRecordedTarget() {
        return true
    }
    clearObservationCache()
    writeResponse(makeError(id: id, code: "stale_snapshot",
                            message: "target snapshot changed since observation; observe again"))
    return false
}
