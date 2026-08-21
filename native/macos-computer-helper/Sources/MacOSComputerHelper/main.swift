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
    beginSessionIfNeeded(sessionID)
    guard validateSession(sessionID) else {
        writeResponse(makeError(
            id: id,
            code: "session_mismatch",
            message: "session is not active; start a new run"
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

/// 全局元素映射：当前 observation_id → element_ref → AXUIElement。
/// 只保留最近一次 observation 的映射；新 observe 到来时整体替换。
/// 不要把 AX pointer/address 暴露给 Python。

/// AX 遍历上限。
private let kMaxElements = 300
private let kMaxDepth = 12
private let kMaxVisitedNodes = 3000
private let kMaxRepetitiveElements = 80
/// value 字符串截断上限。
private let kMaxValueLength = 1000

/// 大型列表里最容易淹没有效控件的重复角色。
private let kRepetitiveRoles: Set<String> = [
    "row", "cell", "list_item", "outline_row", "static_text",
]
private let kTextEntryRoles: Set<String> = [
    "text_area", "text_field", "combo_box",
]

/// 纯容器角色：本身没有信息就不返回，但仍继续遍历 children。
private let kContainerRoles: Set<String> = [
    "group", "split_group", "scroll_area", "splitter", "layout_area",
    "drawer", "tab_group",
]

/// 截断长字符串。
func truncateText(_ text: String, limit: Int) -> String {
    if text.count <= limit { return text }
    return String(text.prefix(limit))
}

/// "AXPress" → "press"，"AXShowMenu" → "show_menu"。
func normalizeActionName(_ raw: String) -> String {
    let stripped = raw.hasPrefix("AX") ? String(raw.dropFirst(2)) : raw
    return snakeCase(stripped)
}

/// "AXButton" → "button"；未知 role 做简单 normalize（去 AX 前缀 + snake_case）。
func normalizeRole(_ raw: String) -> String {
    switch raw {
    case "AXButton": return "button"
    case "AXTextField": return "text_field"
    case "AXTextArea": return "text_area"
    case "AXCheckBox": return "checkbox"
    case "AXRadioButton": return "radio_button"
    case "AXPopUpButton": return "pop_up_button"
    case "AXComboBox": return "combo_box"
    case "AXMenu": return "menu"
    case "AXMenuItem": return "menu_item"
    case "AXTab", "AXTabGroup": return "tab"
    case "AXLink": return "link"
    case "AXSlider": return "slider"
    case "AXTable": return "table"
    case "AXRow": return "row"
    case "AXGroup": return "group"
    case "AXSplitGroup": return "split_group"
    case "AXScrollArea": return "scroll_area"
    default:
        let stripped = raw.hasPrefix("AX") ? String(raw.dropFirst(2)) : raw
        return snakeCase(stripped)
    }
}

/// "AXButton" → "button"；大写字母前插下划线并小写。
func snakeCase(_ raw: String) -> String {
    var result = ""
    for (index, char) in raw.enumerated() {
        if char.isUppercase && index > 0 {
            result.append("_")
        }
        result.append(char.lowercased())
    }
    return result
}

/// 读取 AX 字符串属性（title/description/value）；复杂对象不进入字符串。
func readAXString(_ element: AXUIElement, _ attribute: CFString) -> String? {
    var valueRef: CFTypeRef?
    guard
        AXUIElementCopyAttributeValue(element, attribute, &valueRef) == .success,
        let value = valueRef
    else { return nil }
    if let string = value as? String {
        return truncateText(string, limit: kMaxValueLength)
    }
    if let number = value as? NSNumber {
        return truncateText(number.stringValue, limit: kMaxValueLength)
    }
    return nil
}

/// 输入验证读取完整文本，只在内存中比较，不把正文写入协议或日志。
func readAXFullString(_ element: AXUIElement, _ attribute: CFString) -> String? {
    var valueRef: CFTypeRef?
    guard AXUIElementCopyAttributeValue(
        element, attribute, &valueRef
    ) == .success, let value = valueRef else { return nil }
    if let string = value as? String { return string }
    if let number = value as? NSNumber { return number.stringValue }
    return nil
}

/// 读取 AX 布尔属性（enabled/focused）。
func readAXBool(_ element: AXUIElement, _ attribute: CFString) -> Bool? {
    var valueRef: CFTypeRef?
    guard
        AXUIElementCopyAttributeValue(element, attribute, &valueRef) == .success,
        let value = valueRef
    else { return nil }
    return value as? Bool ?? (value as? NSNumber)?.boolValue
}

/// 读取 AX CGPoint 属性（position）。
func readAXPoint(_ element: AXUIElement, _ attribute: CFString) -> CGPoint? {
    var valueRef: CFTypeRef?
    guard
        AXUIElementCopyAttributeValue(element, attribute, &valueRef) == .success,
        let value = valueRef,
        CFGetTypeID(value) == AXValueGetTypeID(),
        AXValueGetType(value as! AXValue) == .cgPoint
    else { return nil }
    var point = CGPoint.zero
    AXValueGetValue(value as! AXValue, .cgPoint, &point)
    return point
}

/// 读取 AX CGSize 属性（size）。
func readAXSize(_ element: AXUIElement, _ attribute: CFString) -> CGSize? {
    var valueRef: CFTypeRef?
    guard
        AXUIElementCopyAttributeValue(element, attribute, &valueRef) == .success,
        let value = valueRef,
        CFGetTypeID(value) == AXValueGetTypeID(),
        AXValueGetType(value as! AXValue) == .cgSize
    else { return nil }
    var size = CGSize.zero
    AXValueGetValue(value as! AXValue, .cgSize, &size)
    return size
}

/// 读取 AXActionNames 并规范化（排序保证输出稳定）。
func readAXActions(_ element: AXUIElement) -> [String] {
    var namesRef: CFArray?
    guard
        AXUIElementCopyActionNames(element, &namesRef) == .success,
        let names = namesRef as? [String]
    else { return [] }
    return names.map { normalizeActionName($0) }.sorted()
}

/// AXValue 可写才视为可编辑，避免只依赖 role 猜测输入目标。
func isEditableElement(_ element: AXUIElement) -> Bool {
    var settable = DarwinBoolean(false)
    guard AXUIElementIsAttributeSettable(
        element,
        kAXValueAttribute as CFString,
        &settable
    ) == .success else { return false }
    return settable.boolValue
}

/// 已取得真实 AXFocusedUIElement 时，忽略其它节点不可靠的 AXFocused 标记。
func resolvedElementFocus(
    forceFocused: Bool,
    reportedFocused: Bool,
    allowReportedFocus: Bool
) -> Bool {
    forceFocused || (allowReportedFocus && reportedFocused)
}

/// 直接读取应用当前真实的键盘焦点元素，避免 DFS 截断前找不到编辑器。
func focusedUIElement(pid: pid_t) -> AXUIElement? {
    let appElement = AXUIElementCreateApplication(pid)
    var elementRef: CFTypeRef?
    guard AXUIElementCopyAttributeValue(
        appElement,
        kAXFocusedUIElementAttribute as CFString,
        &elementRef
    ) == .success, let elementRef else { return nil }
    return (elementRef as! AXUIElement)
}

/// 判断一个元素是否值得保留。
/// - 有 title/description/value/focused → 保留；
/// - 纯容器（group/scroll_area 等）无信息 → 不保留（但仍遍历 children）；
/// - 其它非容器角色 → 保留。
func isUsefulElement(
    role: String,
    title: String?,
    description: String?,
    value: String?,
    focused: Bool
) -> Bool {
    if focused { return true }
    let hasText =
        !(title ?? "").isEmpty || !(description ?? "").isEmpty
            || !(value ?? "").isEmpty
    if hasText { return true }
    if kContainerRoles.contains(role) { return false }
    return true
}

/// 遍历中的一个元素（ref 由本次观察内编号生成）。
struct AXElementInfo {
    let ref: String
    let role: String
    let title: String?
    let value: String?
    let enabled: Bool
    let focused: Bool
    let editable: Bool
    let bounds: [String: Int]
    let actions: [String]
    let axElement: AXUIElement

    func toJSON() -> [String: Any] {
        var dict: [String: Any] = [
            "ref": ref,
            "role": role,
            "enabled": enabled,
            "focused": focused,
            "editable": editable,
            "bounds": bounds,
            "actions": actions,
        ]
        if let title = title { dict["title"] = title }
        if let value = value { dict["value"] = value }
        return dict
    }
}

/// 供 visited 集合使用的 AX 元素包装（按 CFEqual/CFHash 判等，防循环）。
private struct AXNode: Hashable {
    let element: AXUIElement

    static func == (lhs: AXNode, rhs: AXNode) -> Bool {
        return CFEqual(lhs.element, rhs.element)
    }

    func hash(into hasher: inout Hasher) {
        hasher.combine(CFHash(element))
    }
}

/// 构造单个元素（带 ref）；不适合保留则返回 nil。
func buildElementInfo(
    _ element: AXUIElement,
    ref: String,
    forceFocused: Bool = false,
    allowReportedFocus: Bool = true
) -> AXElementInfo? {
    let role = normalizeRole(
        readAXString(element, kAXRoleAttribute as CFString) ?? ""
    )
    let title = readAXString(element, kAXTitleAttribute as CFString)
    let description = readAXString(
        element, kAXDescriptionAttribute as CFString
    )
    let value = readAXString(element, kAXValueAttribute as CFString)
    let enabled = readAXBool(element, kAXEnabledAttribute as CFString) ?? true
    let focused = resolvedElementFocus(
        forceFocused: forceFocused,
        reportedFocused: (
            readAXBool(element, kAXFocusedAttribute as CFString) ?? false
        ),
        allowReportedFocus: allowReportedFocus
    )
    let editable = isEditableElement(element)

    guard isUsefulElement(
        role: role,
        title: title,
        description: description,
        value: value,
        focused: focused
    ) else { return nil }

    let position = readAXPoint(element, kAXPositionAttribute as CFString)
    let size = readAXSize(element, kAXSizeAttribute as CFString)
    return AXElementInfo(
        ref: ref,
        role: role,
        title: title,
        value: value,
        enabled: enabled,
        focused: focused,
        editable: editable,
        bounds: [
            "x": Int(position?.x ?? 0),
            "y": Int(position?.y ?? 0),
            "width": Int(size?.width ?? 0),
            "height": Int(size?.height ?? 0),
        ],
        actions: readAXActions(element),
        axElement: element
    )
}

/// 模型消费顺序：真实焦点 > 文本输入 > 其它可写控件 > 可操作 > 其它 > 重复项。
func elementPriority(_ element: AXElementInfo) -> Int {
    if element.focused { return 0 }
    if element.editable && kTextEntryRoles.contains(element.role) { return 1 }
    if element.editable { return 2 }
    if !element.actions.isEmpty { return 3 }
    if kRepetitiveRoles.contains(element.role) { return 5 }
    return 4
}

func semanticElementOrder(_ elements: [AXElementInfo]) -> [AXElementInfo] {
    elements.sorted { lhs, rhs in
        let left = elementPriority(lhs)
        let right = elementPriority(rhs)
        if left != right { return left < right }
        let leftNumber = Int(lhs.ref.dropFirst()) ?? Int.max
        let rightNumber = Int(rhs.ref.dropFirst()) ?? Int.max
        return leftNumber < rightNumber
    }
}

struct AXCollectionResult {
    let elements: [AXElementInfo]
    let truncated: Bool
    let observed: Int
    let editableCount: Int
    let actionableCount: Int
    let repetitiveElementsDropped: Int
}

/// 在最终输出预算内优先保留焦点、可编辑和可操作元素，并限制重复列表项。
func selectSemanticElements(
    _ candidates: [AXElementInfo],
    maxElements: Int,
    maxRepetitiveElements: Int
) -> (elements: [AXElementInfo], repetitiveDropped: Int) {
    let ordered = semanticElementOrder(candidates)
    var selected: [AXElementInfo] = []
    var repetitiveKept = 0
    var repetitiveDropped = 0
    for candidate in ordered {
        let repetitive = kRepetitiveRoles.contains(candidate.role)
        if repetitive && repetitiveKept >= maxRepetitiveElements {
            repetitiveDropped += 1
            continue
        }
        if selected.count >= maxElements {
            if repetitive { repetitiveDropped += 1 }
            continue
        }
        selected.append(candidate)
        if repetitive { repetitiveKept += 1 }
    }
    return (selected, repetitiveDropped)
}

/// 递归遍历 AX children。遍历预算与输出预算分离，避免前 300 个列表行直接
/// 截断树；真实焦点元素会预留进入候选集，再按语义优先级和重复角色配额输出。
func collectElements(
    root: AXUIElement,
    focusedElement: AXUIElement?,
    maxElements: Int,
    maxDepth: Int,
    maxVisitedNodes: Int = kMaxVisitedNodes
) -> AXCollectionResult {
    var candidates: [AXElementInfo] = []
    var visited = Set<AXNode>()
    var traversalTruncated = false
    var nextRef = 1

    func appendCandidate(_ element: AXUIElement, forceFocused: Bool = false) {
        let ref = "e\(nextRef)"
        nextRef += 1
        if let info = buildElementInfo(
            element,
            ref: ref,
            forceFocused: forceFocused,
            // 能直接读到 AXFocusedUIElement 时，只信这一份真实焦点证据。
            // Notes 等 App 会让大量 cell 同时报告 AXFocused=true。
            allowReportedFocus: focusedElement == nil
        ) {
            candidates.append(info)
        }
    }

    if let focusedElement {
        appendCandidate(focusedElement, forceFocused: true)
    }

    func visit(_ element: AXUIElement, _ depth: Int) {
        if depth > maxDepth { return }
        if visited.count >= maxVisitedNodes {
            traversalTruncated = true
            return
        }

        let node = AXNode(element: element)
        if visited.contains(node) { return }
        visited.insert(node)
        if let focusedElement, CFEqual(element, focusedElement) {
            // 已作为最高优先级候选加入，但仍继续遍历它的 children。
        } else {
            appendCandidate(element)
        }

        var childrenRef: CFTypeRef?
        guard
            AXUIElementCopyAttributeValue(
                element,
                kAXChildrenAttribute as CFString,
                &childrenRef
            ) == .success,
            let children = childrenRef as? [AXUIElement]
        else { return }
        for child in children {
            visit(child, depth + 1)
            if traversalTruncated { return }
        }
    }

    visit(root, 0)
    let selected = selectSemanticElements(
        candidates,
        maxElements: maxElements,
        maxRepetitiveElements: kMaxRepetitiveElements
    )
    return AXCollectionResult(
        elements: selected.elements,
        truncated: traversalTruncated || candidates.count > selected.elements.count,
        observed: candidates.count,
        editableCount: candidates.lazy.filter(\.editable).count,
        actionableCount: candidates.lazy.filter { !$0.actions.isEmpty }.count,
        repetitiveElementsDropped: selected.repetitiveDropped
    )
}

/// 处理 observe 请求（V3）。
///
/// params: {"observation_id": "..."}。Python 先生成 observation_id，
/// Swift 用它在本次遍历中生成 e1/e2/e3... 并缓存 observation_id →
/// element_ref → AXUIElement。新 observe 到来时替换旧缓存。
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
    // 首次见到该 session_id 时建立 Session 并清空上一个 Run 的 Target/Snapshot。
    beginSessionIfNeeded(sessionID)
    guard validateSession(sessionID) else {
        writeResponse(makeError(
            id: id,
            code: "session_mismatch",
            message: "session is not active; start a new run"
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
    beginSessionIfNeeded(sessionID)
    guard validateSession(sessionID) else {
        writeResponse(makeError(id: id, code: "session_mismatch",
                                message: "session is not active; start a new run")); return
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
    beginSessionIfNeeded(sessionID)
    guard validateSession(sessionID) else {
        writeResponse(makeError(id: id, code: "session_mismatch",
                                message: "session is not active; start a new run")); return
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

    // 1) Background semantic：AXValue settable → AXSetValue（不切前台）。
    var settable = DarwinBoolean(false)
    if AXUIElementIsAttributeSettable(
        targetElement, kAXValueAttribute as CFString, &settable
    ) == .success, settable.boolValue {
        let beforeValue = readAXFullString(
            targetElement, kAXValueAttribute as CFString
        )
        let setResult = AXUIElementSetAttributeValue(
            targetElement,
            kAXValueAttribute as CFString,
            text as CFString
        )
        if setResult == .success {
            var afterValue = readAXFullString(
                targetElement, kAXValueAttribute as CFString
            )
            for _ in 0..<8 where beforeValue != nil && afterValue == beforeValue {
                Thread.sleep(forTimeInterval: 0.05)
                afterValue = readAXFullString(
                    targetElement, kAXValueAttribute as CFString
                )
            }
            let verificationStatus: String
            if let beforeValue, let afterValue {
                verificationStatus = beforeValue == afterValue ? "mismatch" : "verified"
            } else {
                verificationStatus = "unverified"
            }
            let evidence: [String: Any] = [
                "value_readable": beforeValue != nil && afterValue != nil,
                "value_changed": beforeValue != nil && afterValue != beforeValue,
                "before_characters": beforeValue.map { $0.count } ?? NSNull(),
                "after_characters": afterValue.map { $0.count } ?? NSNull(),
            ]
            clearObservationCache()
            writeResponse([
                "id": id ?? NSNull(),
                "result": [
                    "characters": characterCount(text),
                    "element_ref": parsedElementRef.value,
                    "delivery_status": "delivered",
                    "verification_status": verificationStatus,
                    "evidence": evidence,
                    "method": "ax_set_value",
                    "execution_mode": "background",
                ],
            ])
            return
        }
        // AXSetValue 失败 → 回退 Targeted background input（CGEventPostToPid）。
    }

    // 2) Targeted background input：CGEventPostToPid（不强制前台）。
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

    var afterValue = readAXFullString(
        targetElement, kAXValueAttribute as CFString
    )
    for _ in 0..<8 where beforeValue != nil && afterValue == beforeValue {
        Thread.sleep(forTimeInterval: 0.05)
        afterValue = readAXFullString(
            targetElement, kAXValueAttribute as CFString
        )
    }
    let verificationStatus: String
    if let beforeValue, let afterValue {
        verificationStatus = beforeValue == afterValue ? "mismatch" : "verified"
    } else {
        verificationStatus = "unverified"
    }
    let beforeCharacters: Any = beforeValue.map { $0.count } ?? NSNull()
    let afterCharacters: Any = afterValue.map { $0.count } ?? NSNull()
    let evidence: [String: Any] = [
        "value_readable": beforeValue != nil && afterValue != nil,
        "value_changed": beforeValue != nil && afterValue != beforeValue,
        "before_characters": beforeCharacters,
        "after_characters": afterCharacters,
    ]
    clearObservationCache()

    writeResponse([
        "id": id ?? NSNull(),
        "result": [
            "characters": characterCount(text),
            "element_ref": parsedElementRef.value,
            "delivery_status": "delivered",
            "verification_status": verificationStatus,
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
    beginSessionIfNeeded(sessionID)
    guard validateSession(sessionID) else {
        writeResponse(makeError(id: id, code: "session_mismatch",
                                message: "session is not active; start a new run")); return
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
    beginSessionIfNeeded(sessionID)
    guard validateSession(sessionID) else {
        writeResponse(makeError(id: id, code: "session_mismatch",
                                message: "session is not active; start a new run")); return
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
    beginSessionIfNeeded(sessionID)
    guard validateSession(sessionID) else {
        writeResponse(makeError(id: id, code: "session_mismatch",
                                message: "session is not active; start a new run")); return
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
    beginSessionIfNeeded(sessionID)
    guard validateSession(sessionID) else {
        writeResponse(makeError(id: id, code: "session_mismatch",
                                message: "session is not active; start a new run")); return
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
