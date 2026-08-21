import AppKit
import ApplicationServices
import CoreGraphics
import Foundation
import MacOSComputerCore

// ObservationBuilder.swift

func handleObserve(params: Any?, id: Any?) {
    guard
        let params = params as? [String: Any],
        let observationID = params["observation_id"] as? String,
        !observationID.isEmpty,
        let sessionID = params["session_id"] as? String,
        !sessionID.isEmpty
    else {
        writeResponse(
            makeError(
                id: id,
                code: "invalid_params",
                message: "missing or empty 'observation_id' / 'session_id'"
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

    var activeApp: Any = NSNull()
    var activeWindow: Any = NSNull()
    var activeWindowRef: Any = NSNull()
    var windows: [[String: Any]] = []
    var elements: [[String: Any]] = []
    var focusedElementRef: Any = NSNull()
    var truncated = false
    var newMapping: [String: AXUIElement] = [:]
    var newWindows: [String: AXUIElement] = [:]
    var screenshotRef: Any = NSNull()
    var screenshotError: Any = NSNull()
    var newScreenshotMapping: ScreenshotMapping? = nil
    var observedPID: pid_t? = nil
    var targetIsFrontmost = false
    var elementStats: [String: Int] = [
        "observed": 0,
        "returned": 0,
        "editable_count": 0,
        "actionable_count": 0,
        "repetitive_elements_dropped": 0,
    ]

    let targetApplication: NSRunningApplication?
    if computerTargetPID != nil {
        targetApplication = runningComputerTarget()
        if targetApplication == nil {
            clearComputerTarget()
            writeResponse(makeError(
                id: id,
                code: "target_not_running",
                message: "computer target exited; open the target app again"
            ))
            return
        }
    } else {
        // Session 尚无 target：以当前 frontmost 作为初始 target，但必须显式
        // 写入 Session（computerTargetPID），之后不随 frontmost 漂移。
        targetApplication = NSWorkspace.shared.frontmostApplication
        if let targetApplication { setComputerTarget(targetApplication) }
    }

    // User Frontmost：用户当前正在使用的 App，与 Session Target 分离。
    let userFrontmostApp: Any
    if let frontmost = NSWorkspace.shared.frontmostApplication {
        userFrontmostApp = frontmostAppDict(frontmost)
    } else {
        userFrontmostApp = NSNull()
    }

    if let target = targetApplication {
        observedPID = target.processIdentifier
        targetIsFrontmost = NSWorkspace.shared.frontmostApplication?
            .processIdentifier == target.processIdentifier
        activeApp = frontmostAppDict(target)
        let appElement = AXUIElementCreateApplication(target.processIdentifier)
        var windowsRef: CFTypeRef?
        let axWindows = (AXUIElementCopyAttributeValue(
            appElement, kAXWindowsAttribute as CFString, &windowsRef) == .success
            ? windowsRef as? [AXUIElement] : nil) ?? []
        let focused = focusedWindow(pid: target.processIdentifier)
        var sourceWindows = axWindows
        if let focused, !sourceWindows.contains(where: { CFEqual($0, focused.element) }) {
            sourceWindows.insert(focused.element, at: 0)
        }
        let sourceFocusedIndex = focused.flatMap { focus in
            sourceWindows.firstIndex(where: { CFEqual($0, focus.element) })
        }
        let ordered = focusedFirstIndices(
            count: sourceWindows.count, focusedIndex: sourceFocusedIndex
        ).map { sourceWindows[$0] }
        for (index, element) in ordered.enumerated() {
            let ref = "w\(index + 1)"
            var info = windowInfoDict(element); info["ref"] = ref
            windows.append(info); newWindows[ref] = element
        }
        if let windowElement = ordered.first {
            var info = windowInfoDict(windowElement); info["ref"] = "w1"
            activeWindow = info; activeWindowRef = "w1"
            let collected = collectElements(
                root: windowElement,
                focusedElement: focusedUIElement(pid: target.processIdentifier),
                maxElements: kMaxElements,
                maxDepth: kMaxDepth
            )
            let semanticElements = collected.elements
            if let focusedRef = semanticElements.first(where: \.focused)?.ref {
                focusedElementRef = focusedRef
            }
            elements = semanticElements.map { $0.toJSON() }
            truncated = collected.truncated
            elementStats = [
                "observed": collected.observed,
                "returned": semanticElements.count,
                "editable_count": collected.editableCount,
                "actionable_count": collected.actionableCount,
                "repetitive_elements_dropped": collected.repetitiveElementsDropped,
            ]
            newMapping = Dictionary(
                uniqueKeysWithValues: semanticElements.map {
                    ($0.ref, $0.axElement)
                }
            )
            let includeScreenshot = params["include_screenshot"] as? Bool ?? false
            if includeScreenshot, let path = params["screenshot_path"] as? String {
                let position = readAXPoint(windowElement, kAXPositionAttribute as CFString) ?? .zero
                let size = readAXSize(windowElement, kAXSizeAttribute as CFString) ?? .zero
                if #available(macOS 14.0, *) {
                    switch captureWindow(pid: target.processIdentifier,
                                         bounds: CGRect(origin: position, size: size),
                                         title: readAXString(windowElement, kAXTitleAttribute as CFString) ?? "",
                                         outputPath: path) {
                    case .success(let shot):
                        screenshotRef = shot.path; newScreenshotMapping = shot.mapping
                    case .failure(.permissionRequired):
                        screenshotError = ["code": "screen_recording_permission_required"]
                    case .failure(.unavailable(let message)):
                        screenshotError = ["code": "screen_capture_unavailable", "message": message]
                    case .failure(.captureFailed(let message)):
                        screenshotError = ["code": "screenshot_capture_failed", "message": message]
                    }
                } else {
                    screenshotError = ["code": "screen_capture_unavailable"]
                }
            }
        }
    }

    // 新 observe 替换旧缓存。
    currentObservationID = observationID
    currentElements = newMapping
    currentWindows = newWindows
    currentScreenshotMapping = newScreenshotMapping
    currentTargetPID = observedPID
    currentFocusedWindow = newWindows["w1"]
    currentFocusedElementRef = focusedElementRef as? String
    if let active = windows.first, let bounds = active["bounds"] as? [String: Int] {
        currentFocusedWindowBounds = CGRect(
            x: bounds["x"] ?? 0, y: bounds["y"] ?? 0,
            width: bounds["width"] ?? 0, height: bounds["height"] ?? 0
        )
    } else {
        currentFocusedWindowBounds = nil
    }

    writeResponse([
        "id": id ?? NSNull(),
        "result": [
            "active_app": activeApp,
            "target": activeApp,
            "target_is_frontmost": targetIsFrontmost,
            "user_frontmost_app": userFrontmostApp,
            "active_window": activeWindow,
            "active_window_ref": activeWindowRef,
            "windows": windows,
            "elements": elements,
            "focused_element_ref": focusedElementRef,
            "truncated": truncated,
            "element_stats": elementStats,
            "screenshot_ref": screenshotRef,
            "screenshot_error": screenshotError,
        ],
    ])
}

/// 测试辅助：返回可独立验证的纯逻辑结果（不需要真实 AX 权限）。
