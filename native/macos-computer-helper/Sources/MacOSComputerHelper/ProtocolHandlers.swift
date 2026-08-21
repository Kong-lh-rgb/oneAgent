import AppKit
import ApplicationServices
import CoreGraphics
import Foundation
import MacOSComputerCore

// ProtocolHandlers.swift

func handleTestAXLogic(params: Any?, id: Any?) {
    writeResponse([
        "id": id ?? NSNull(),
        "result": [
            "roles": [
                ["AXButton", normalizeRole("AXButton")],
                ["AXTextField", normalizeRole("AXTextField")],
                ["AXTextArea", normalizeRole("AXTextArea")],
                ["AXCheckBox", normalizeRole("AXCheckBox")],
                ["AXRadioButton", normalizeRole("AXRadioButton")],
                ["AXPopUpButton", normalizeRole("AXPopUpButton")],
                ["AXMenuItem", normalizeRole("AXMenuItem")],
                ["AXLink", normalizeRole("AXLink")],
                ["AXUnknownWidget", normalizeRole("AXUnknownWidget")],
            ],
            "actions": [
                ["AXPress", normalizeActionName("AXPress")],
                ["AXShowMenu", normalizeActionName("AXShowMenu")],
            ],
            "value_truncation": [
                "plain": truncateText("hello", limit: kMaxValueLength),
                "long_len": truncateText(
                    String(repeating: "x", count: 1200),
                    limit: kMaxValueLength
                ).count,
            ],
            "limits": [
                "max_elements": kMaxElements,
                "max_depth": kMaxDepth,
                "max_visited_nodes": kMaxVisitedNodes,
                "max_repetitive_elements": kMaxRepetitiveElements,
            ],
            "semantic_priorities": [
                "focused": 0,
                "text_entry": 1,
                "editable": 2,
                "actionable": 3,
                "meaningful": 4,
                "repetitive": 5,
            ],
            "focus_resolution": [
                "real_focused": resolvedElementFocus(
                    forceFocused: true,
                    reportedFocused: false,
                    allowReportedFocus: false
                ),
                "pseudo_focus_suppressed": resolvedElementFocus(
                    forceFocused: false,
                    reportedFocused: true,
                    allowReportedFocus: false
                ),
                "reported_focus_fallback": resolvedElementFocus(
                    forceFocused: false,
                    reportedFocused: true,
                    allowReportedFocus: true
                ),
            ],
            "refs": ["e1", "e2", "e3"],
        ],
    ])
}

/// 测试辅助：验证清空 Observation 不会丢失稳定 Target。
func handleTestTargetCache(params: Any?, id: Any?) {
    computerTargetPID = 321
    computerTargetBundleID = "com.example.Target"
    computerTargetName = "Target"
    currentObservationID = "obs-target"
    currentTargetPID = 321
    clearObservationCache()
    let targetPreserved = computerTargetPID == 321
        && computerTargetBundleID == "com.example.Target"
        && computerTargetName == "Target"
    clearComputerTarget()
    writeResponse([
        "id": id ?? NSNull(),
        "result": [
            "target_preserved_after_observation_clear": targetPreserved,
            "target_cleared_explicitly": computerTargetPID == nil
                && computerTargetBundleID == nil && computerTargetName == nil,
        ],
    ])
}

/// 测试辅助：用纯数据验证大量重复行不会挤掉末尾的可编辑控件。
func handleTestSemanticBudget(params: Any?, id: Any?) {
    let fake = AXUIElementCreateApplication(1)
    var candidates: [AXElementInfo] = (1...350).map { index in
        AXElementInfo(
            ref: "e\(index)", role: "row", title: "row \(index)",
            value: nil, enabled: true, focused: false, editable: false,
            bounds: [:], actions: [], axElement: fake
        )
    }
    candidates.append(AXElementInfo(
        ref: "e351", role: "text_area", title: nil, value: nil,
        enabled: true, focused: false, editable: true,
        bounds: [:], actions: [], axElement: fake
    ))
    let selected = selectSemanticElements(
        candidates,
        maxElements: kMaxElements,
        maxRepetitiveElements: kMaxRepetitiveElements
    )
    writeResponse([
        "id": id ?? NSNull(),
        "result": [
            "returned": selected.elements.count,
            "editable_retained": selected.elements.contains { $0.editable },
            "repetitive_returned": selected.elements.filter {
                kRepetitiveRoles.contains($0.role)
            }.count,
            "repetitive_dropped": selected.repetitiveDropped,
        ],
    ])
}

/// 测试辅助：模拟两次 observation 的缓存替换（不需要真实 AX）。
func handleTestElementMapping(params: Any?, id: Any?) {
    guard
        let params = params as? [String: Any],
        let observationID = params["observation_id"] as? String
    else {
        writeResponse(
            makeError(
                id: id,
                code: "invalid_params",
                message: "missing 'observation_id'"
            )
        )
        return
    }
    let refs = (params["refs"] as? [String]) ?? []
    let fake = AXUIElementCreateApplication(1)
    var mapping: [String: AXUIElement] = [:]
    for ref in refs { mapping[ref] = fake }
    currentObservationID = observationID
    currentElements = mapping
    writeResponse([
        "id": id ?? NSNull(),
        "result": [
            "observation_id": observationID,
            "refs": currentElements.keys.sorted(),
        ],
    ])
}

// ---------------------------------------------------------------------------
// V4：Semantic Element Click（ElementTarget → AXPress）
// ---------------------------------------------------------------------------

/// 清空当前 observation 的 element 映射（成功点击后调用）。
/// 成功点击后的响应 payload（同时使旧 Observation 失效）。
func successPressPayload(
    observationID: String,
    elementRef: String,
    method: String = "ax_press",
    executionMode: String = "background"
) -> [String: Any] {
    clearObservationCache()
    return [
        "observation_id": observationID,
        "element_ref": elementRef,
        "action": "press",
        "method": method,
        "execution_mode": executionMode,
    ]
}

/// click 目标校验错误（V4）。
private enum ClickTargetError: Error {
    case staleObservation
    case elementNotFound

    var code: String {
        switch self {
        case .staleObservation: return "stale_observation"
        case .elementNotFound: return "element_not_found"
        }
    }
}

/// 校验 click 目标；返回 AXUIElement 或错误。
/// - observation_id 不匹配 → stale_observation；
/// - element_ref 不存在 → element_not_found。
fileprivate func resolveClickTarget(
    observationID: String,
    elementRef: String
) -> Result<AXUIElement, ClickTargetError> {
    guard observationID == currentObservationID else {
        return .failure(.staleObservation)
    }
    guard let element = currentElements[elementRef] else {
        return .failure(.elementNotFound)
    }
    return .success(element)
}

/// 处理 click_element 请求（V4，语义点击：只做 AXPress）。
///
/// params: {"observation_id": "...", "element_ref": "e1"}
/// 使用当前缓存（currentObservationID / currentElements），不重新搜索 AX Tree。
/// 成功：{"observation_id":..., "element_ref":..., "action":"press"} 并清空缓存；
/// 失败：stale_observation / element_not_found / action_not_supported /
///       ax_action_failed。
func handleClickElement(params: Any?, id: Any?) {
    guard
        let params = params as? [String: Any],
        let observationID = params["observation_id"] as? String,
        !observationID.isEmpty,
        let elementRef = params["element_ref"] as? String,
        !elementRef.isEmpty,
        let sessionID = params["session_id"] as? String,
        !sessionID.isEmpty
    else {
        writeResponse(
            makeError(
                id: id,
                code: "invalid_params",
                message: "missing or empty 'observation_id' / 'element_ref' / 'session_id'"
            )
        )
        return
    }
    switch checkSession(sessionID) {
    case .ok:
        break
    case .notActive:
        writeResponse(makeError(id: id, code: "session_not_active",
                                message: "no active computer session; begin_session required")); return
    case .mismatch:
        writeResponse(makeError(id: id, code: "session_mismatch",
                                message: "session mismatch; does not match the active run")); return
    }

    // Background-first：AXPress 只要求 Target Snapshot 一致，不强制 Target 在
    // 最前台；漂移时允许恢复已批准目标后执行。
    guard requireBackgroundFresh(
        observationID,
        sessionID: sessionID,
        id: id,
        allowForegroundRestore: true
    ) else { return }

    switch resolveClickTarget(
        observationID: observationID,
        elementRef: elementRef
    ) {
    case .failure(let error):
        writeResponse(
            makeError(id: id, code: error.code, message: error.code)
        )
        return
    case .success(let element):
        // 优先检查元素是否支持 AXPress。
        var namesRef: CFArray?
        let supportsPress =
            AXUIElementCopyActionNames(element, &namesRef) == .success
                && (namesRef as? [String])?.contains("AXPress") == true
        guard supportsPress else {
            writeResponse(
                makeError(
                    id: id,
                    code: "action_not_supported",
                    message: "element \(elementRef) does not support AXPress"
                )
            )
            return
        }

        if AXUIElementPerformAction(element, kAXPressAction as CFString)
            == .success {
            // 后台语义点击成功。
            writeResponse([
                "id": id ?? NSNull(),
                "result": successPressPayload(
                    observationID: observationID,
                    elementRef: elementRef,
                    method: "ax_press",
                    executionMode: "background"
                ),
            ])
            return
        }

        // Background AXPress 失败 → foreground fallback：恢复已批准目标后重试。
        guard let target = runningComputerTarget(),
              restoreRecordedTarget() else {
            writeResponse(
                makeError(
                    id: id,
                    code: "background_action_failed",
                    message: "AXPress failed and target could not be brought to foreground"
                )
            )
            return
        }
        _ = target
        if AXUIElementPerformAction(element, kAXPressAction as CFString)
            == .success {
            writeResponse([
                "id": id ?? NSNull(),
                "result": successPressPayload(
                    observationID: observationID,
                    elementRef: elementRef,
                    method: "ax_press",
                    executionMode: "foreground_fallback"
                ),
            ])
            return
        }
        writeResponse(
            makeError(
                id: id,
                code: "background_action_failed",
                message: "AXUIElementPerformAction failed for \(elementRef) in both modes"
            )
        )
    }
}

/// 测试辅助：复用真实校验与成功清理逻辑，验证生命周期（不需要真实 AX）。
/// 真实 AXPress 不在自动测试中执行。
func handleTestClickElement(params: Any?, id: Any?) {
    guard
        let params = params as? [String: Any],
        let observationID = params["observation_id"] as? String,
        let elementRef = params["element_ref"] as? String
    else {
        writeResponse(
            makeError(
                id: id,
                code: "invalid_params",
                message: "missing 'observation_id' / 'element_ref'"
            )
        )
        return
    }

    switch resolveClickTarget(
        observationID: observationID,
        elementRef: elementRef
    ) {
    case .failure(let error):
        writeResponse(
            makeError(id: id, code: error.code, message: error.code)
        )
    case .success:
        // 模拟成功点击后的清理与响应（复用真实 post-press 逻辑）。
        writeResponse([
            "id": id ?? NSNull(),
            "result": successPressPayload(
                observationID: observationID,
                elementRef: elementRef
            ),
        ])
    }
}

// ---------------------------------------------------------------------------
// V5：Text Input（CGEvent Unicode）
// ---------------------------------------------------------------------------

/// 计算文本的字符数（grapheme count，用于 characters 响应）。
func characterCount(_ text: String) -> Int {
    return text.count
}

/// 按 UTF-16 code units 切块；每块最多 chunkSize 个 code units。
func chunkUTF16(_ text: String, chunkSize: Int) -> [[UniChar]] {
    guard !text.isEmpty else { return [] }
    let units = Array(text.utf16)
    var result: [[UniChar]] = []
    var index = 0
    while index < units.count {
        let end = min(index + chunkSize, units.count)
        result.append(Array(units[index..<end]))
        index = end
    }
    return result
}

/// 通过 CGEvent 发送一块 Unicode 文本（keyboardSetUnicodeString）。
/// 失败返回 false；不抛异常、不崩溃。
func postUnicodeChunk(_ chunk: [UniChar], pid: pid_t) -> Bool {
    guard let source = CGEventSource(stateID: .combinedSessionState) else {
        return false
    }
    guard
        let down = CGEvent(
            keyboardEventSource: source,
            virtualKey: 0,
            keyDown: true
        )
    else {
        return false
    }
    chunk.withUnsafeBufferPointer { buffer in
        down.keyboardSetUnicodeString(
            stringLength: buffer.count,
            unicodeString: buffer.baseAddress
        )
    }
    down.postToPid(pid)

    // 补一个 keyUp（不携带 unicode），让目标输入框正常收尾。
    if let up = CGEvent(
        keyboardEventSource: source,
        virtualKey: 0,
        keyDown: false
    ) {
        up.postToPid(pid)
    }
    return true
}

/// 成功输入文本后的响应 payload（同时使旧 Observation 失效）。
/// 只有实际发送了事件才调用；空字符串不调用。
func successfulTypePayload(_ text: String) -> [String: Any] {
    clearObservationCache()
    return ["characters": characterCount(text)]
}

/// focusElement 的失败分类与协议错误码。
private enum FocusError: Error {
    case staleObservation
    case elementNotFound
    case focusFailed
    case elementNotEditable

    var code: String {
        switch self {
        case .staleObservation: return "stale_observation"
        case .elementNotFound: return "element_not_found"
        case .focusFailed: return "focus_failed"
        case .elementNotEditable: return "element_not_editable"
        }
    }
}

/// 把本次 Observation 中的 element_ref 聚焦（用于 type/key 先聚焦目标再输入）。
/// 失败返回错误码：stale_observation / element_not_found / focus_failed。
fileprivate func focusElement(
    observationID: String,
    elementRef: String,
    requireEditable: Bool = false
) -> Result<AXUIElement, FocusError> {
    guard observationID == currentObservationID else {
        return .failure(.staleObservation)
    }
    guard let element = currentElements[elementRef] else {
        return .failure(.elementNotFound)
    }
    guard !requireEditable || isEditableElement(element) else {
        return .failure(.elementNotEditable)
    }
    guard AXUIElementSetAttributeValue(
        element,
        kAXFocusedAttribute as CFString,
        kCFBooleanTrue
    ) == .success else {
        return .failure(.focusFailed)
    }
    return .success(element)
}

/// 解析可选的 element_ref 参数。
/// 返回 nil 表示参数非法（错误响应已写出，调用方应立即 return）；
/// 返回 (provided=false) 表示未提供；返回 (provided=true, value) 表示有效值。
func parseOptionalElementRef(
    _ params: [String: Any],
    id: Any?
) -> (provided: Bool, value: String)? {
    guard let raw = params["element_ref"] else {
        return (provided: false, value: "")
    }
    guard let value = raw as? String,
          !value.trimmingCharacters(in: .whitespaces).isEmpty else {
        writeResponse(
            makeError(
                id: id,
                code: "invalid_params",
                message: "'element_ref' must be a non-empty string"
            )
        )
        return nil
    }
    return (provided: true, value: value.trimmingCharacters(in: .whitespaces))
}

/// 处理 type_text 请求（V5）。
///
/// params: {"text": "...", "element_ref": "e123"（可选）}。向当前 macOS
/// keyboard focus 输入文本；若指定 element_ref，先把该元素聚焦再输入
/// （CGEvent Unicode，非 clipboard / pbcopy / Cmd+V / osascript）。
///
/// - 缺/非字符串 text → invalid_params；
/// - element_ref 非法 → invalid_params；聚焦失败 → focus_failed；
/// - 空字符串 → {"characters": 0}（不制造系统事件，不清缓存）；
/// - 未授权 Accessibility → accessibility_permission_required；
/// - CGEvent 创建失败 → input_event_failed。
func handleTypeText(params: Any?, id: Any?) {
    guard
        let params = params as? [String: Any],
        let text = params["text"] as? String,
        let expectedObservationID = params["expected_observation_id"] as? String,
        !expectedObservationID.isEmpty,
        let sessionID = params["session_id"] as? String,
        !sessionID.isEmpty
    else {
        writeResponse(
            makeError(
                id: id,
                code: "invalid_params",
                message: "missing 'text' / 'expected_observation_id' / 'session_id'"
            )
        )
        return
    }
    guard let parsedElementRef = parseOptionalElementRef(params, id: id) else {
        // 非法 element_ref 已由解析函数写响应。
        return
    }
    switch checkSession(sessionID) {
    case .ok:
        break
    case .notActive:
        writeResponse(makeError(id: id, code: "session_not_active",
                                message: "no active computer session; begin_session required")); return
    case .mismatch:
        writeResponse(makeError(id: id, code: "session_mismatch",
                                message: "session mismatch; does not match the active run")); return
    }

    // Background-first：只要求 Target Snapshot 一致，不强制 Target 在最前台。
    guard requireBackgroundFresh(
        expectedObservationID,
        sessionID: sessionID,
        id: id,
        allowForegroundRestore: true
    ) else { return }

    // 可选：先把目标元素聚焦（避免输入到错误位置）。
    if parsedElementRef.provided {
        if case .failure(let error) = focusElement(
            observationID: expectedObservationID,
            elementRef: parsedElementRef.value,
            requireEditable: true
        ) {
            writeResponse(makeError(id: id, code: error.code, message: error.code))
            return
        }
    }

    // 空字符串：直接成功，不发送任何事件。
    if text.isEmpty {
        writeResponse([
            "id": id ?? NSNull(),
            "result": ["characters": 0, "method": "none", "execution_mode": "background"],
        ])
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

    guard let targetPID = currentTargetPID,
          let targetElement = currentElements[parsedElementRef.value] else {
        writeResponse(makeError(
            id: id,
            code: "editable_target_required",
            message: "type_text requires an editable element_ref"
        ))
        return
    }

    // 注意：不使用 AXSetValue。AXSetValue 会整体替换控件内容，与 computer_type
    // 的 “在当前光标位置 insert/type 文本” 语义不一致（P0）。保持
    // CGEventPostToPid 插入语义；如需 “替换整个字段” 应作为独立 semantic
    // capability，而不是悄悄改变 computer_type。
    let beforeValue = readAXFullString(
        targetElement, kAXValueAttribute as CFString
    )
    let chunks = chunkUTF16(text, chunkSize: 100)
    for chunk in chunks {
        guard postUnicodeChunk(chunk, pid: targetPID) else {
            writeResponse(
                makeError(
                    id: id,
                    code: "input_event_failed",
                    message: "failed to create/post CGEvent"
                )
            )
            return
        }
    }

    // Immediate evidence（仅调试参考）：不以此作为 verified 的 ground truth。
    // 最终验证必须来自 fresh observe 之后的 Snapshot 对比（Snapshot A → action
    // → invalidate A → fresh observe B → verify A→B）。
    let afterValue = readAXFullString(
        targetElement, kAXValueAttribute as CFString
    )
    let evidence: [String: Any] = [
        "value_readable": beforeValue != nil && afterValue != nil,
        "value_changed": beforeValue != nil && afterValue != beforeValue,
        "before_characters": beforeValue.map { $0.count } ?? NSNull(),
        "after_characters": afterValue.map { $0.count } ?? NSNull(),
        "note": "immediate_evidence_only; final verification requires fresh observe",
    ]
    clearObservationCache()

    writeResponse([
        "id": id ?? NSNull(),
        "result": [
            "characters": characterCount(text),
            "element_ref": parsedElementRef.value,
            "delivery_status": "delivered",
            "verification_status": "unverified",
            "evidence": evidence,
            "method": "cg_event_pid",
            "execution_mode": "background",
        ],
    ])
}

/// 测试辅助：返回可独立验证的纯逻辑（chunking / character count，不发送事件）。
func handleTestTypeLogic(params: Any?, id: Any?) {
    writeResponse([
        "id": id ?? NSNull(),
        "result": [
            "characters": [
                "empty": characterCount(""),
                "hello": characterCount("Hello Vesta"),
                "cn": characterCount("你好 Vesta"),
            ],
            "chunks": [
                "empty": chunkUTF16("", chunkSize: 100).count,
                "short": chunkUTF16("Hello Vesta", chunkSize: 100).count,
                "long_250": chunkUTF16(
                    String(repeating: "a", count: 250), chunkSize: 100
                ).count,
                "chunk_size": 100,
            ],
        ],
    ])
}

// ---------------------------------------------------------------------------
// V6：Keyboard Key Input（CGEvent keyDown/keyUp）
// ---------------------------------------------------------------------------

/// 成功按键后的响应 payload（同时使旧 Observation 失效）。
func successfulKeyPayload(
    normalizedKey: String,
    modifiers: [String],
    method: String = "cg_event_pid",
    executionMode: String = "background"
) -> [String: Any] {
    clearObservationCache()
    return [
        "key": normalizedKey,
        "modifiers": modifiers,
        "method": method,
        "execution_mode": executionMode,
    ]
}

/// 处理 key_press 请求（V6）。
///
/// params: {"key": "...", "modifiers": [...], "element_ref": "e123"（可选）}。
/// 发送 CGEvent keyDown/keyUp；若指定 element_ref，先把该元素聚焦再发送。
func handleKeyPress(params: Any?, id: Any?) {
    guard
        let params = params as? [String: Any],
        let key = params["key"] as? String,
        !key.isEmpty,
        let expectedObservationID = params["expected_observation_id"] as? String,
        !expectedObservationID.isEmpty,
        let sessionID = params["session_id"] as? String,
        !sessionID.isEmpty
    else {
        writeResponse(
            makeError(
                id: id,
                code: "invalid_params",
                message: "missing or empty 'key' / 'expected_observation_id' / 'session_id'"
            )
        )
        return
    }
    guard let parsedElementRef = parseOptionalElementRef(params, id: id) else {
        // 非法 element_ref 已由解析函数写响应。
        return
    }
    switch checkSession(sessionID) {
    case .ok:
        break
    case .notActive:
        writeResponse(makeError(id: id, code: "session_not_active",
                                message: "no active computer session; begin_session required")); return
    case .mismatch:
        writeResponse(makeError(id: id, code: "session_mismatch",
                                message: "session mismatch; does not match the active run")); return
    }

    // Background-first：CGEventPostToPid 只要求 Target Snapshot 一致，不强制前台。
    guard requireBackgroundFresh(
        expectedObservationID,
        sessionID: sessionID,
        id: id,
        allowForegroundRestore: true
    ) else { return }

    // 可选：先把目标元素聚焦，再发送按键。
    if parsedElementRef.provided {
        if case .failure(let error) = focusElement(
            observationID: expectedObservationID,
            elementRef: parsedElementRef.value
        ) {
            writeResponse(makeError(id: id, code: error.code, message: error.code))
            return
        }
    }

    guard let code = keyCode(for: key) else {
        writeResponse(
            makeError(
                id: id,
                code: "unsupported_key",
                message: "unsupported key: \(key)"
            )
        )
        return
    }

    let rawModifiers: [String]
    if let value = params["modifiers"] {
        guard let parsed = value as? [String] else {
            writeResponse(
                makeError(
                    id: id,
                    code: "invalid_params",
                    message: "'modifiers' must be an array of strings"
                )
            )
            return
        }
        rawModifiers = parsed
    } else {
        rawModifiers = []
    }
    guard let mods = normalizeModifiers(rawModifiers) else {
        writeResponse(
            makeError(
                id: id,
                code: "invalid_modifier",
                message: "unknown modifier in \(rawModifiers)"
            )
        )
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

    guard let source = CGEventSource(stateID: .combinedSessionState) else {
        writeResponse(
            makeError(
                id: id,
                code: "input_event_failed",
                message: "failed to create CGEventSource"
            )
        )
        return
    }
    guard let down = CGEvent(
        keyboardEventSource: source,
        virtualKey: code,
        keyDown: true
    ), let up = CGEvent(
        keyboardEventSource: source,
        virtualKey: code,
        keyDown: false
    ) else {
        writeResponse(
            makeError(
                id: id,
                code: "input_event_failed",
                message: "failed to create keyDown/keyUp CGEvent"
            )
        )
        return
    }
    down.flags = mods.flags
    up.flags = mods.flags
    guard let targetPID = currentTargetPID else {
        writeResponse(makeError(
            id: id, code: "stale_observation", message: "missing target pid"
        ))
        return
    }
    down.postToPid(targetPID)
    up.postToPid(targetPID)

    writeResponse([
        "id": id ?? NSNull(),
        "result": successfulKeyPayload(
            normalizedKey: normalizeKey(key),
            modifiers: mods.normalized,
            method: "cg_event_pid",
            executionMode: "background"
        ),
    ])
}

/// 测试辅助：返回可独立验证的 key / modifier 纯逻辑（不发送真实键盘事件）。
func handleTestKeyLogic(params: Any?, id: Any?) {
    let namedKeys = [
        "enter", "return", "tab", "escape", "space", "backspace", "delete",
        "left", "right", "up", "down",
    ]
    let letterKeys = "abcdefghijklmnopqrstuvwxyz".map(String.init)
    let digitKeys = "0123456789".map(String.init)
    let supportedKeys = namedKeys + letterKeys + digitKeys
    let supported: [[String: Any]] = [
        ["key": "enter", "supported": keyCode(for: "enter") != nil],
        ["key": "a", "supported": keyCode(for: "a") != nil],
        ["key": "0", "supported": keyCode(for: "0") != nil],
        ["key": "f5", "supported": keyCode(for: "f5") != nil],
    ]
    var keyCodes: [String: Int] = [:]
    for key in supportedKeys {
        if let code = keyCode(for: key) {
            keyCodes[key] = Int(code)
        }
    }
    let modifiers: [[String: Any]] = [
        ["input": "cmd", "normalized": normalizeModifier("cmd") ?? ""],
        ["input": "ctrl", "normalized": normalizeModifier("ctrl") ?? ""],
        ["input": "alt", "normalized": normalizeModifier("alt") ?? ""],
        ["input": "command", "normalized": normalizeModifier("command") ?? ""],
        ["input": "bogus", "normalized": normalizeModifier("bogus") ?? ""],
    ]
    let keyNormalize: [[String: Any]] = [
        ["input": "enter", "normalized": normalizeKey("enter")],
        ["input": "RETURN", "normalized": normalizeKey("RETURN")],
        ["input": "SPACE", "normalized": normalizeKey("SPACE")],
    ]

    var result: [String: Any] = [:]
    result["supported"] = supported
    result["key_codes"] = keyCodes
    result["modifiers"] = modifiers
    result["dedupe"] = normalizeModifiers(["command", "cmd", "shift"])?
        .normalized ?? []
    result["all_supported"] = keyCodes.count == supportedKeys.count
    result["key_normalize"] = keyNormalize
    writeResponse(["id": id ?? NSNull(), "result": result])
}

/// 用假的 AXUIElement 为测试建立 observation 映射（可选）。
func seedFakeMapping(observationID: String?, refs: [String]) {
    guard let observationID = observationID else { return }
    let fake = AXUIElementCreateApplication(1)
    var mapping: [String: AXUIElement] = [:]
    for ref in refs { mapping[ref] = fake }
    currentObservationID = observationID
    currentElements = mapping
}

/// 测试辅助：模拟 type_text 成功后的清理（复用成功 payload，不发送事件）。
func handleTestTypeSuccess(params: Any?, id: Any?) {
    guard
        let params = params as? [String: Any],
        let text = params["text"] as? String
    else {
        writeResponse(
            makeError(id: id, code: "invalid_params", message: "missing 'text'")
        )
        return
    }
    if let observationID = params["observation_id"] as? String {
        seedFakeMapping(
            observationID: observationID,
            refs: (params["refs"] as? [String]) ?? []
        )
    }
    var result: [String: Any]
    if text.isEmpty {
        result = ["characters": 0]
    } else {
        result = successfulTypePayload(text)
    }
    result["cache_cleared"] =
        currentObservationID == nil && currentElements.isEmpty
    writeResponse(["id": id ?? NSNull(), "result": result])
}

/// 测试辅助：模拟 key_press 成功后的清理（复用成功 payload，不发送事件）。
func handleTestKeySuccess(params: Any?, id: Any?) {
    guard
        let params = params as? [String: Any],
        let key = params["key"] as? String
    else {
        writeResponse(
            makeError(id: id, code: "invalid_params", message: "missing 'key'")
        )
        return
    }
    if let observationID = params["observation_id"] as? String {
        seedFakeMapping(
            observationID: observationID,
            refs: (params["refs"] as? [String]) ?? []
        )
    }
    let modifiers = normalizeModifiers(
        (params["modifiers"] as? [String]) ?? []
    )?.normalized ?? []
    var result = successfulKeyPayload(
        normalizedKey: normalizeKey(key),
        modifiers: modifiers
    )
    result["cache_cleared"] =
        currentObservationID == nil && currentElements.isEmpty
    writeResponse(["id": id ?? NSNull(), "result": result])
}

/// 测试辅助：只读当前 Observation cache 状态，不修改缓存。
func handleTestObservationCache(params: Any?, id: Any?) {
    let observationID: Any = currentObservationID.map { $0 as Any } ?? NSNull()
    writeResponse([
        "id": id ?? NSNull(),
        "result": [
            "observation_id": observationID,
            "refs": Array(currentElements.keys).sorted(),
            "cache_cleared":
                currentObservationID == nil && currentElements.isEmpty
                && currentWindows.isEmpty && currentScreenshotMapping == nil
                && currentTargetPID == nil && currentFocusedWindow == nil,
        ],
    ])
}

func handleTestFreshGuard(params: Any?, id: Any?) {
    let stateMatches = (params as? [String: Any])?["state_matches"] as? Bool ?? false
    seedFakeMapping(observationID: "obs-fresh", refs: ["e1"])
    currentTargetPID = 123
    if !stateMatches { clearObservationCache() }
    writeResponse(["id": id ?? NSNull(), "result": [
        "accepted": stateMatches,
        "cache_cleared": currentObservationID == nil && currentElements.isEmpty
            && currentTargetPID == nil
    ]])
}

/// 测试辅助：验证“恢复已批准目标”的安全失败路径（不真正抢焦点）。
/// 用不存在的 PID + fake window 模拟无法恢复的目标：restoreRecordedTarget()
/// 必须返回 false 且不崩溃、不清空缓存（是否 stale 由调用方决定）。
func handleTestRestoreTarget(params: Any?, id: Any?) {
    seedFakeMapping(observationID: "obs-restore", refs: ["e1"])
    currentTargetPID = 999_999  // 不存在的 PID → activate 无效果
    computerTargetPID = 999_999
    computerTargetBundleID = "com.example.Exited"
    computerTargetName = "Exited"
    currentFocusedWindow = AXUIElementCreateApplication(1)  // fake window
    currentFocusedWindowBounds = CGRect(x: 0, y: 0, width: 100, height: 100)
    let restored = restoreRecordedTarget()
    let cacheKept = currentObservationID == "obs-restore"
    let targetExited = runningComputerTarget() == nil
    clearComputerTarget()
    writeResponse(["id": id ?? NSNull(), "result": [
        "restored": restored,
        "cache_kept": cacheKept,
        "target_exited": targetExited,
    ]])
}

/// 测试辅助：验证 focusElement 的判定逻辑（不依赖真实 AX 可聚焦控件）。
/// seed_id 用于种缓存（缺省等于 observation_id，便于测 stale 用不同 id）。
/// - observation_id 与种子不匹配 → stale_observation；
/// - element_ref 不存在 → element_not_found；
/// - 元素存在但 fake 元素不可聚焦 → focus_failed（真实控件无法自动测）。
func handleTestFocusElement(params: Any?, id: Any?) {
    guard let params = params as? [String: Any],
          let observationID = params["observation_id"] as? String,
          let elementRef = params["element_ref"] as? String else {
        writeResponse(makeError(id: id, code: "invalid_params",
                                message: "missing observation_id/element_ref")); return
    }
    let seedID = (params["seed_id"] as? String) ?? observationID
    seedFakeMapping(observationID: seedID,
                    refs: (params["refs"] as? [String]) ?? [])
    switch focusElement(observationID: observationID, elementRef: elementRef) {
    case .success:
        writeResponse(["id": id ?? NSNull(), "result": ["focused": true]])
    case .failure(let error):
        writeResponse(["id": id ?? NSNull(),
                       "result": ["focused": false, "error": error.code]])
    }
}

// ---------------------------------------------------------------------------
// V7：窗口、滚动、截图坐标点击
// ---------------------------------------------------------------------------

func handleScreenCaptureStatus(params: Any?, id: Any?) {
    let prompt = (params as? [String: Any])?["prompt"] as? Bool ?? false
    if #available(macOS 10.15, *) {
        writeResponse(["id": id ?? NSNull(), "result": [
            "granted": screenCaptureGranted(prompt: prompt)
        ]])
    } else {
        writeResponse(makeError(id: id, code: "screen_capture_unavailable",
                                message: "Screen Recording API is unavailable"))
    }
}

func handleFocusWindow(params: Any?, id: Any?) {
    guard let params = params as? [String: Any],
          let observationID = params["observation_id"] as? String,
          let windowRef = params["window_ref"] as? String,
          let sessionID = params["session_id"] as? String,
          !observationID.isEmpty, !windowRef.isEmpty, !sessionID.isEmpty else {
        writeResponse(makeError(id: id, code: "invalid_params",
                                message: "missing observation_id/window_ref/session_id")); return
    }
    switch checkSession(sessionID) {
    case .ok:
        break
    case .notActive:
        writeResponse(makeError(id: id, code: "session_not_active",
                                message: "no active computer session; begin_session required")); return
    case .mismatch:
        writeResponse(makeError(id: id, code: "session_mismatch",
                                message: "session mismatch; does not match the active run")); return
    }
    guard observationID == currentObservationID else {
        writeResponse(makeError(id: id, code: "stale_observation",
                                message: "stale observation")); return
    }
    guard requireFreshObservation(
        observationID, id: id, restoreOnDrift: true
    ) else { return }
    guard AXIsProcessTrusted() else {
        writeResponse(makeError(id: id, code: "accessibility_permission_required",
                                message: "macOS Accessibility permission is required")); return
    }
    guard let window = currentWindows[windowRef] else {
        writeResponse(makeError(id: id, code: "window_not_found",
                                message: "window not found: \(windowRef)")); return
    }
    guard AXUIElementPerformAction(window, kAXRaiseAction as CFString) == .success else {
        writeResponse(makeError(id: id, code: "focus_window_failed",
                                message: "AXRaise failed")); return
    }
    if let pid = currentTargetPID {
        NSRunningApplication(processIdentifier: pid)?.activate(options: [])
    }
    clearObservationCache()
    writeResponse(["id": id ?? NSNull(), "result": [
        "window_ref": windowRef,
        "method": "ax_raise",
        "execution_mode": "foreground_fallback",
    ]])
}

func handleScroll(params: Any?, id: Any?) {
    guard let params = params as? [String: Any],
          let dx = params["delta_x"] as? Int,
          let dy = params["delta_y"] as? Int,
          let expectedObservationID = params["expected_observation_id"] as? String,
          let sessionID = params["session_id"] as? String,
          !expectedObservationID.isEmpty, !sessionID.isEmpty,
          validScroll(deltaX: dx, deltaY: dy) else {
        writeResponse(makeError(id: id, code: "invalid_params",
                                message: "scroll deltas must contain a non-zero integer")); return
    }
    switch checkSession(sessionID) {
    case .ok:
        break
    case .notActive:
        writeResponse(makeError(id: id, code: "session_not_active",
                                message: "no active computer session; begin_session required")); return
    case .mismatch:
        writeResponse(makeError(id: id, code: "session_mismatch",
                                message: "session mismatch; does not match the active run")); return
    }
    guard requireBackgroundFresh(
        expectedObservationID,
        sessionID: sessionID,
        id: id,
        allowForegroundRestore: true
    ) else { return }
    guard AXIsProcessTrusted() else {
        writeResponse(makeError(id: id, code: "accessibility_permission_required",
                                message: "macOS Accessibility permission is required")); return
    }
    guard let event = CGEvent(scrollWheelEvent2Source: nil, units: .pixel,
                              wheelCount: 2, wheel1: Int32(dy), wheel2: Int32(-dx), wheel3: 0) else {
        writeResponse(makeError(id: id, code: "input_event_failed",
                                message: "failed to create scroll event")); return
    }
    event.post(tap: .cghidEventTap); clearObservationCache()
    writeResponse(["id": id ?? NSNull(), "result": ["delta_x": dx, "delta_y": dy,
        "method": "scroll_event", "execution_mode": "background"]])
}

func handleClickCoordinate(params: Any?, id: Any?) {
    guard let params = params as? [String: Any],
          let observationID = params["observation_id"] as? String,
          let x = params["x"] as? Int, let y = params["y"] as? Int,
          let sessionID = params["session_id"] as? String,
          !sessionID.isEmpty else {
        writeResponse(makeError(id: id, code: "invalid_params",
                                message: "missing observation_id/x/y/session_id")); return
    }
    switch checkSession(sessionID) {
    case .ok:
        break
    case .notActive:
        writeResponse(makeError(id: id, code: "session_not_active",
                                message: "no active computer session; begin_session required")); return
    case .mismatch:
        writeResponse(makeError(id: id, code: "session_mismatch",
                                message: "session mismatch; does not match the active run")); return
    }
    guard observationID == currentObservationID else {
        writeResponse(makeError(id: id, code: "stale_observation",
                                message: "stale observation")); return
    }
    // click_coordinate 依赖真实窗口几何与全局指针语义，执行前必须 foreground verify。
    guard requireFreshObservation(
        observationID,
        id: id,
        restoreOnDrift: true,
        requireStableBounds: true
    ) else { return }
    guard let mapping = currentScreenshotMapping else {
        writeResponse(makeError(id: id, code: "screenshot_unavailable",
                                message: "current observation has no screenshot mapping")); return
    }
    guard let point = mapping.globalPoint(x: x, y: y) else {
        writeResponse(makeError(id: id, code: "coordinate_out_of_bounds",
                                message: "coordinate is outside screenshot")); return
    }
    guard AXIsProcessTrusted(),
          let source = CGEventSource(stateID: .combinedSessionState),
          let down = CGEvent(mouseEventSource: source, mouseType: .leftMouseDown,
                             mouseCursorPosition: CGPoint(x: point.x, y: point.y), mouseButton: .left),
          let up = CGEvent(mouseEventSource: source, mouseType: .leftMouseUp,
                           mouseCursorPosition: CGPoint(x: point.x, y: point.y), mouseButton: .left) else {
        writeResponse(makeError(id: id, code: AXIsProcessTrusted()
            ? "input_event_failed" : "accessibility_permission_required",
                                message: "cannot create coordinate click")); return
    }
    down.post(tap: .cghidEventTap); up.post(tap: .cghidEventTap)
    clearObservationCache()
    writeResponse(["id": id ?? NSNull(), "result": [
        "method": "coordinate", "x": x, "y": y
    ]])
}

func handleTestV7Logic(params: Any?, id: Any?) {
    let mapping = ScreenshotMapping(pixelWidth: 1600, pixelHeight: 1200,
        bounds: LogicalBounds(x: 100, y: 80, width: 800, height: 600))
    let point = mapping.globalPoint(x: 800, y: 600)!
    writeResponse(["id": id ?? NSNull(), "result": [
        "mapped_x": point.x, "mapped_y": point.y,
        "out_of_bounds": mapping.globalPoint(x: 1600, y: 0) == nil,
        "window_order": focusedFirstIndices(count: 3, focusedIndex: 1),
        "valid_scroll": validScroll(deltaX: 0, deltaY: -1),
        "invalid_scroll": validScroll(deltaX: 0, deltaY: 0)
    ]])
}

/// 处理一条已解析的请求，返回响应（或 nil 表示不响应）。
