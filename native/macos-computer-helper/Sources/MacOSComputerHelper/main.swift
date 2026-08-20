// macOS Computer Helper V3 —— 长驻 subprocess。
//
// 职责：从 stdin 按行读 JSON，向 stdout 按行写 JSON（JSON Lines 协议）。
// 本轮实现 ping / system_info / open_app / accessibility_status /
// basic_observe / observe（AX Element Observation）。
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
var currentObservationID: String? = nil
var currentElements: [String: AXUIElement] = [:]

/// AX 遍历上限。
private let kMaxElements = 300
private let kMaxDepth = 12
/// value 字符串截断上限。
private let kMaxValueLength = 1000

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
    let bounds: [String: Int]
    let actions: [String]
    let axElement: AXUIElement

    func toJSON() -> [String: Any] {
        var dict: [String: Any] = [
            "ref": ref,
            "role": role,
            "enabled": enabled,
            "focused": focused,
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
func buildElementInfo(_ element: AXUIElement, ref: String) -> AXElementInfo? {
    let role = normalizeRole(
        readAXString(element, kAXRoleAttribute as CFString) ?? ""
    )
    let title = readAXString(element, kAXTitleAttribute as CFString)
    let description = readAXString(
        element, kAXDescriptionAttribute as CFString
    )
    let value = readAXString(element, kAXValueAttribute as CFString)
    let enabled = readAXBool(element, kAXEnabledAttribute as CFString) ?? true
    let focused = readAXBool(element, kAXFocusedAttribute as CFString) ?? false

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

/// 递归遍历 AX children，保留有用元素，限制数量与深度，防循环。
func collectElements(
    root: AXUIElement,
    maxElements: Int,
    maxDepth: Int
) -> (elements: [AXElementInfo], truncated: Bool) {
    var result: [AXElementInfo] = []
    var visited = Set<AXNode>()
    var truncated = false

    func visit(_ element: AXUIElement, _ depth: Int) {
        if truncated { return }
        if depth > maxDepth { return }

        let node = AXNode(element: element)
        if visited.contains(node) { return }
        visited.insert(node)

        if result.count < maxElements {
            let ref = "e\(result.count + 1)"
            if let info = buildElementInfo(element, ref: ref) {
                result.append(info)
            }
        } else {
            truncated = true
            return
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
            if truncated { return }
        }
    }

    visit(root, 0)
    return (result, truncated)
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
        !observationID.isEmpty
    else {
        writeResponse(
            makeError(
                id: id,
                code: "invalid_params",
                message: "missing or empty 'observation_id'"
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

    var activeApp: Any = NSNull()
    var activeWindow: Any = NSNull()
    var elements: [[String: Any]] = []
    var truncated = false
    var newMapping: [String: AXUIElement] = [:]

    if let frontmost = NSWorkspace.shared.frontmostApplication {
        activeApp = frontmostAppDict(frontmost)
        if let window = focusedWindow(pid: frontmost.processIdentifier) {
            activeWindow = window.info
            let collected = collectElements(
                root: window.element,
                maxElements: kMaxElements,
                maxDepth: kMaxDepth
            )
            elements = collected.elements.map { $0.toJSON() }
            truncated = collected.truncated
            newMapping = Dictionary(
                uniqueKeysWithValues: collected.elements.map {
                    ($0.ref, $0.axElement)
                }
            )
        }
    }

    // 新 observe 替换旧缓存。
    currentObservationID = observationID
    currentElements = newMapping

    writeResponse([
        "id": id ?? NSNull(),
        "result": [
            "active_app": activeApp,
            "active_window": activeWindow,
            "elements": elements,
            "truncated": truncated,
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
            ],
            "refs": ["e1", "e2", "e3"],
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
func clearObservationCache() {
    currentObservationID = nil
    currentElements = [:]
}

/// 成功点击后的响应 payload（同时使旧 Observation 失效）。
func successPressPayload(observationID: String, elementRef: String) -> [String: Any] {
    clearObservationCache()
    return [
        "observation_id": observationID,
        "element_ref": elementRef,
        "action": "press",
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
        !elementRef.isEmpty
    else {
        writeResponse(
            makeError(
                id: id,
                code: "invalid_params",
                message: "missing or empty 'observation_id' / 'element_ref'"
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

        guard AXUIElementPerformAction(element, kAXPressAction as CFString)
            == .success else {
            writeResponse(
                makeError(
                    id: id,
                    code: "ax_action_failed",
                    message: "AXUIElementPerformAction failed for \(elementRef)"
                )
            )
            return
        }

        writeResponse([
            "id": id ?? NSNull(),
            "result": successPressPayload(
                observationID: observationID,
                elementRef: elementRef
            ),
        ])
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

    case "observe":
        handleObserve(params: payload["params"], id: id)

    case "__test_ax_logic":
        handleTestAXLogic(params: payload["params"], id: id)

    case "__test_element_mapping":
        handleTestElementMapping(params: payload["params"], id: id)

    case "click_element":
        handleClickElement(params: payload["params"], id: id)

    case "__test_click_element":
        handleTestClickElement(params: payload["params"], id: id)

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
