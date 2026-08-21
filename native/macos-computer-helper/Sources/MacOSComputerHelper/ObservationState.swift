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

func clearObservationCache() {
    currentObservationID = nil
    currentElements = [:]
    currentWindows = [:]
    currentScreenshotMapping = nil
    currentFrontmostPID = nil
    currentFocusedWindow = nil
    currentFocusedWindowBounds = nil
}

private func nearlyEqual(_ lhs: CGFloat, _ rhs: CGFloat) -> Bool {
    abs(lhs - rhs) < 1
}

/// action 前重新读取真实 frontmost app / focused window，防止审批期间桌面漂移。
func desktopStateIsFresh(expectedObservationID: String) -> Bool {
    guard expectedObservationID == currentObservationID,
          let expectedPID = currentFrontmostPID,
          let expectedWindow = currentFocusedWindow,
          let expectedBounds = currentFocusedWindowBounds,
          let frontmost = NSWorkspace.shared.frontmostApplication,
          frontmost.processIdentifier == expectedPID,
          let focused = focusedWindow(pid: expectedPID),
          CFEqual(focused.element, expectedWindow) else { return false }
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
func restoreRecordedTarget() -> Bool {
    guard let pid = currentFrontmostPID,
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
        if desktopStateIsFresh(expectedObservationID: observationID) {
            return true
        }
        Thread.sleep(forTimeInterval: 0.05)
    }
    return false
}

/// freshness 失败说明缓存可能已过期。
/// 若这是需要人工审批的副作用（type/key/click），先尝试把“已批准目标”恢复到
/// 前台并确认一致；确认不了再统一返回 stale_observation（绝不盲目操作）。
/// 低风险自动操作（scroll/focus_window）保持严格失败（restoreOnDrift=false）。
func requireFreshObservation(
    _ observationID: String,
    id: Any?,
    restoreOnDrift: Bool = false
) -> Bool {
    if desktopStateIsFresh(expectedObservationID: observationID) {
        return true
    }
    if restoreOnDrift && restoreRecordedTarget() {
        return true
    }
    clearObservationCache()
    writeResponse(makeError(id: id, code: "stale_observation",
                            message: "desktop state changed since observation"))
    return false
}
