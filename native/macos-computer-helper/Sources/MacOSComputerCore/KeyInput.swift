import CoreGraphics

/// 按键规范化：enter/return 统一为 return，其余转小写。
public func normalizeKey(_ raw: String) -> String {
    let key = raw.lowercased()
    return key == "enter" ? "return" : key
}

/// V1 明确支持的 ANSI 键盘虚拟键码。
/// macOS CGKeyCode 不是 ASCII，也不是连续编号，因此必须显式映射。
private let supportedKeyCodes: [String: CGKeyCode] = [
    "return": 36,
    "tab": 48,
    "escape": 53,
    "space": 49,
    "backspace": 51,
    "delete": 117,
    "left": 123,
    "right": 124,
    "up": 126,
    "down": 125,
    "a": 0,
    "b": 11,
    "c": 8,
    "d": 2,
    "e": 14,
    "f": 3,
    "g": 5,
    "h": 4,
    "i": 34,
    "j": 38,
    "k": 40,
    "l": 37,
    "m": 46,
    "n": 45,
    "o": 31,
    "p": 35,
    "q": 12,
    "r": 15,
    "s": 1,
    "t": 17,
    "u": 32,
    "v": 9,
    "w": 13,
    "x": 7,
    "y": 16,
    "z": 6,
    "0": 29,
    "1": 18,
    "2": 19,
    "3": 20,
    "4": 21,
    "5": 23,
    "6": 22,
    "7": 26,
    "8": 28,
    "9": 25,
]

/// key → CGKeyCode；未知键返回 nil，不做静默降级。
public func keyCode(for raw: String) -> CGKeyCode? {
    supportedKeyCodes[normalizeKey(raw)]
}

/// modifier 规范化，公开稳定值为 command/shift/option/control。
public func normalizeModifier(_ raw: String) -> String? {
    switch raw.lowercased() {
    case "command", "cmd": return "command"
    case "shift": return "shift"
    case "option", "alt": return "option"
    case "control", "ctrl": return "control"
    default: return nil
    }
}

/// 规范化后的 modifiers 与对应 CGEvent flags。
public struct NormalizedModifiers {
    public let normalized: [String]
    public let flags: CGEventFlags
}

/// modifier → CGEventFlags。
public func modifierFlag(_ normalized: String) -> CGEventFlags? {
    switch normalized {
    case "command": return .maskCommand
    case "shift": return .maskShift
    case "option": return .maskAlternate
    case "control": return .maskControl
    default: return nil
    }
}

/// 规范化 modifier、按首次出现顺序去重并合并 flags；未知值返回 nil。
public func normalizeModifiers(_ raw: [String]) -> NormalizedModifiers? {
    var seen = Set<String>()
    var normalized: [String] = []
    var flags: CGEventFlags = []
    for item in raw {
        guard let value = normalizeModifier(item) else { return nil }
        if seen.insert(value).inserted {
            normalized.append(value)
            if let flag = modifierFlag(value) {
                flags.insert(flag)
            }
        }
    }
    return NormalizedModifiers(normalized: normalized, flags: flags)
}
