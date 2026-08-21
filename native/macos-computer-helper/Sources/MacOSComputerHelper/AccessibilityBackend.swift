import AppKit
import ApplicationServices
import CoreGraphics
import Foundation
import MacOSComputerCore

// AccessibilityBackend.swift

/// AX 遍历上限（AX 后端共享；跨文件引用因此为 internal）。
let kMaxElements = 300
let kMaxDepth = 12
let kMaxVisitedNodes = 3000
let kMaxRepetitiveElements = 80
/// value 字符串截断上限。
let kMaxValueLength = 1000

/// 大型列表里最容易淹没有效控件的重复角色。
let kRepetitiveRoles: Set<String> = [
    "row", "cell", "list_item", "outline_row", "static_text",
]
let kTextEntryRoles: Set<String> = [
    "text_area", "text_field", "combo_box",
]

/// 纯容器角色：本身没有信息就不返回，但仍继续遍历 children。
let kContainerRoles: Set<String> = [
    "group", "split_group", "scroll_area", "splitter", "layout_area",
    "drawer", "tab_group",
]

func truncateText(_ text: String, limit: Int) -> String {
    if text.count <= limit { return text }
    return String(text.prefix(limit))
}

/// "AXPress" → "press"，"AXShowMenu" → "show_menu"。
/// 非 AX 前缀（custom action display）：只保留干净名称，去掉 Native 实现细节
/// （_target/_selector/指针/对象 description）。
func normalizeActionName(_ raw: String) -> String {
    if raw.hasPrefix("AX") {
        return snakeCase(String(raw.dropFirst(2)))
    }
    return cleanCustomActionName(raw)
}

/// 从 AX custom action 的 description 里提取干净 display name。
/// 例："name:共享\n_target:0x0\n_selector:(null)" → "共享"
private func cleanCustomActionName(_ raw: String) -> String {
    let text = raw.trimmingCharacters(in: .whitespacesAndNewlines)
    if text.isEmpty { return raw }

    // AX 对 custom action 常用 "name:显示名\n..." 前缀，优先提取。
    if let marker = text.range(of: "name:") {
        let after = text[marker.upperBound...]
        if let name = after.split(whereSeparator: { $0 == "\n" }).first,
           !name.isEmpty {
            return String(name).trimmingCharacters(in: .whitespaces)
        }
    }

    // 去掉 Native implementation detail 行（指针 / 选择器 / 对象地址 / description）。
    let lines = text.split(separator: "\n").filter { line -> Bool in
        let l = line.trimmingCharacters(in: .whitespaces)
        if l.hasPrefix("_target:") || l.hasPrefix("_selector:")
            || l.hasPrefix("_returnValue:") {
            return false
        }
        if l.hasPrefix("0x") || l.hasPrefix("<") || l.hasPrefix("NS") {
            return false
        }
        return true
    }
    let cleaned = lines.joined(separator: " ").trimmingCharacters(in: .whitespaces)
    return cleaned.isEmpty ? raw : cleaned
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
