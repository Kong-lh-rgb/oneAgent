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

/// freshness 失败说明缓存已经过期，立即清除后统一返回 stale_observation。
func requireFreshObservation(_ observationID: String, id: Any?) -> Bool {
    guard desktopStateIsFresh(expectedObservationID: observationID) else {
        clearObservationCache()
        writeResponse(makeError(id: id, code: "stale_observation",
                                message: "desktop state changed since observation"))
        return false
    }
    return true
}
