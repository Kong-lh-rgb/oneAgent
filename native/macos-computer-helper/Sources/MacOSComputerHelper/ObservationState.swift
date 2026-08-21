import AppKit
import ApplicationServices
import Foundation
import MacOSComputerCore

/// helper 只保留最近一次 Observation 的原生对象，不向 Python 暴露 pointer。
var currentObservationID: String? = nil
var currentElements: [String: AXUIElement] = [:]
var currentWindows: [String: AXUIElement] = [:]
var currentScreenshotMapping: ScreenshotMapping? = nil
var currentFrontmostPID: pid_t? = nil
var currentFocusedWindow: AXUIElement? = nil
var currentFocusedWindowBounds: CGRect? = nil
var currentFocusedElementRef: String? = nil

/// Computer target 跨 Observation 与副作用失效保留；不会随前台 App 漂移。
var computerTargetPID: pid_t? = nil
var computerTargetBundleID: String? = nil
var computerTargetName: String? = nil

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
    currentFrontmostPID = nil
    currentFocusedWindow = nil
    currentFocusedWindowBounds = nil
    currentFocusedElementRef = nil
}

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

/// 检查 Observation 仍绑定同一个存活 Target 与窗口。
///
/// 这里故意不要求 Target 当前位于最前台：Vesta 的审批浮窗或其它系统窗口可以
/// 暂时抢走前台，但不能因此把后续 observe 漂移到另一个 App。
func observationTargetIsFresh(
    expectedObservationID: String,
    requireStableBounds: Bool = false
) -> Bool {
    guard expectedObservationID == currentObservationID,
          let expectedPID = currentFrontmostPID,
          computerTargetPID == expectedPID,
          runningComputerTarget() != nil,
          let expectedWindow = currentFocusedWindow,
          let focused = focusedWindow(pid: expectedPID),
          CFEqual(focused.element, expectedWindow) else { return false }
    if !requireStableBounds { return true }
    guard let expectedBounds = currentFocusedWindowBounds else { return false }
    let info = focused.info["bounds"] as? [String: Int]
    guard let info else { return false }
    return nearlyEqual(CGFloat(info["x"] ?? 0), expectedBounds.minX)
        && nearlyEqual(CGFloat(info["y"] ?? 0), expectedBounds.minY)
        && nearlyEqual(CGFloat(info["width"] ?? 0), expectedBounds.width)
        && nearlyEqual(CGFloat(info["height"] ?? 0), expectedBounds.height)
}

/// 副作用执行前的严格 freshness：目标、窗口与 macOS 前台三者都必须一致。
func desktopStateIsFresh(
    expectedObservationID: String,
    requireStableBounds: Bool = false
) -> Bool {
    guard observationTargetIsFresh(
              expectedObservationID: expectedObservationID,
              requireStableBounds: requireStableBounds
          ),
          let expectedPID = currentFrontmostPID,
          let frontmost = NSWorkspace.shared.frontmostApplication else {
        return false
    }
    return frontmost.processIdentifier == expectedPID
}

/// 把本次 Observation 记录的“已批准目标”App/Window 恢复到前台，并确认仍是该目标。
///
/// 场景：computer_type / click / key 需要人工审批，Vesta 审批 UI 会抢走 macOS
/// 焦点；批准后执行前先把之前观察到的 App/Window 恢复到前台，再重新验证仍是
/// 同一个目标。确认不了（App 已退出 / 窗口已关闭 / 无法恢复）返回 false，
/// 调用方按 stale 安全失败，绝不盲目操作。
func restoreRecordedTarget(requireStableBounds: Bool = false) -> Bool {
    guard let pid = currentFrontmostPID,
          computerTargetPID == pid,
          runningComputerTarget() != nil,
          let window = currentFocusedWindow,
          let observationID = currentObservationID else {
        return false
    }
    guard let application = NSRunningApplication(processIdentifier: pid) else {
        return false
    }
    // 同时请求 App 前台、Window main/focused 与 AXRaise；所有请求只作用于
    // Observation 已绑定的 PID/window，不会落到当前任意前台 App。
    requestApplicationFrontmost(application, window: window)
    // 短轮询等待前台切换，并确认仍是记录的目标 App/Window。
    for _ in 0..<20 {
        if desktopStateIsFresh(
            expectedObservationID: observationID,
            requireStableBounds: requireStableBounds
        ) {
            return true
        }
        Thread.sleep(forTimeInterval: 0.05)
    }
    return false
}

/// freshness 失败说明缓存可能已过期。
/// 若这是需要人工审批的副作用（type/key/click），先尝试把“已批准目标”恢复到
/// 前台并确认一致；确认不了再统一返回 stale_observation（绝不盲目操作）。
/// 调用方可选择恢复 Target；所有会产生桌面副作用的正式 handler 都应开启恢复。
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
    writeResponse(makeError(
        id: id,
        code: "stale_observation",
        message: "target or window changed since observation; observe the target again"
    ))
    return false
}
