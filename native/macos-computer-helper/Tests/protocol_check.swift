// macOS Computer Helper —— 可重复执行的协议测试（替代 XCTest）。
//
// 本机只有 Command Line Tools、没有 Xcode，SDK 里没有 XCTest.framework，
// 因此用 Foundation 直接驱动 helper 二进制，验证 JSON Lines 往返。
//
// 运行（在包根目录）：
//     swift build
//     swift Tests/protocol_check.swift
// 退出码 0 = 全部通过；非 0 = 存在失败。
//
// 覆盖：
// - ping / system_info 能解析且字段正确；
// - 请求 id 与响应 id 一一对应（顺序请求）；
// - unknown method / 非法 JSON / 缺 method 返回 error 且 helper 不崩溃；
// - stdin EOF 后 helper 正常退出（exit 0）。

import Foundation

// MARK: - 断言

var failures = 0

func check(_ name: String, _ condition: Bool, _ detail: String = "") {
    if condition {
        print("PASS  \(name)")
    } else {
        failures += 1
        print("FAIL  \(name)\(detail.isEmpty ? "" : "  ←  " + detail)")
    }
}

// MARK: - 定位 helper 二进制

func packageRoot() -> URL {
    // Tests/protocol_check.swift → 包根目录
    URL(fileURLWithPath: #filePath)
        .deletingLastPathComponent()
        .deletingLastPathComponent()
}

func helperBinaryPath() -> String {
    packageRoot()
        .appendingPathComponent(".build/debug/MacOSComputerHelper")
        .path
}

// MARK: - 子进程封装

let helperPath = helperBinaryPath()
guard FileManager.default.isExecutableFile(atPath: helperPath) else {
    print("FAIL  找不到 helper 二进制：\(helperPath)")
    print("      请先在包根目录执行 swift build")
    exit(2)
}

let process = Process()
let stdinPipe = Pipe()
let stdoutPipe = Pipe()
let stderrPipe = Pipe()
process.executableURL = URL(fileURLWithPath: helperPath)
process.standardInput = stdinPipe
process.standardOutput = stdoutPipe
process.standardError = stderrPipe

do {
    try process.run()
} catch {
    print("FAIL  helper 启动失败：\(error)")
    exit(2)
}

func sendLine(_ object: [String: Any]) throws {
    let data = try JSONSerialization.data(withJSONObject: object)
    stdinPipe.fileHandleForWriting.write(data)
    stdinPipe.fileHandleForWriting.write(Data("\n".utf8))
}

func readLineData(timeout: TimeInterval = 5) throws -> Data {
    let fh = stdoutPipe.fileHandleForReading
    let semaphore = DispatchSemaphore(value: 0)
    var result = Data()

    fh.readabilityHandler = { handle in
        let chunk = handle.availableData
        if chunk.isEmpty {
            fh.readabilityHandler = nil
            semaphore.signal()
            return
        }
        result.append(chunk)
        if chunk.contains(0x0A) {
            fh.readabilityHandler = nil
            semaphore.signal()
        }
    }

    let waitResult = semaphore.wait(timeout: .now() + timeout)
    fh.readabilityHandler = nil
    if waitResult == .timedOut {
        throw NSError(
            domain: "protocol_check",
            code: 1,
            userInfo: [NSLocalizedDescriptionKey: "读取响应超时"]
        )
    }
    if result.last == 0x0A { result.removeLast() }
    return result
}

func request(_ object: [String: Any]) throws -> [String: Any] {
    try sendLine(object)
    let line = try readLineData()
    guard
        let json = try JSONSerialization.jsonObject(with: line) as? [String: Any]
    else {
        throw NSError(
            domain: "protocol_check",
            code: 2,
            userInfo: [
                NSLocalizedDescriptionKey:
                    "helper 返回的不是 JSON 对象: \(String(data: line, encoding: .utf8) ?? "?")"
            ]
        )
    }
    return json
}

func sendRaw(_ text: String) {
    stdinPipe.fileHandleForWriting.write(Data(text.utf8))
    stdinPipe.fileHandleForWriting.write(Data("\n".utf8))
}

// MARK: - 用例

do {
    // 1. ping
    let ping = try request(["id": 1, "method": "ping", "params": [:]])
    check("ping id == 1", ping["id"] as? Int == 1)
    check(
        "ping result.ok == true",
        (ping["result"] as? [String: Any])?["ok"] as? Bool == true,
        "\(ping)"
    )

    // 2. system_info
    let info = try request(["id": 2, "method": "system_info", "params": [:]])
    check("system_info id == 2", info["id"] as? Int == 2)
    let sys = info["result"] as? [String: Any]
    check("system_info.platform == macos", sys?["platform"] as? String == "macos")
    check(
        "system_info.helper_version 非空",
        !((sys?["helper_version"] as? String) ?? "").isEmpty
    )
    check(
        "system_info.process_id > 0",
        (sys?["process_id"] as? Int) ?? 0 > 0
    )

    // 3. 顺序请求 id 一一对应
    let a = try request(["id": 10, "method": "ping", "params": [:]])
    let b = try request(["id": 11, "method": "system_info", "params": [:]])
    let c = try request(["id": 12, "method": "ping", "params": [:]])
    check("顺序请求 id=10 对应", a["id"] as? Int == 10)
    check("顺序请求 id=11 对应", b["id"] as? Int == 11)
    check("顺序请求 id=12 对应", c["id"] as? Int == 12)

    // 4. unknown method → error，且 helper 存活
    let unknown = try request(["id": 3, "method": "bogus_method", "params": [:]])
    check("unknown method 返回 error", unknown["error"] != nil)
    check(
        "unknown method code == unknown_method",
        (unknown["error"] as? [String: Any])?["code"] as? String
            == "unknown_method"
    )
    let afterUnknown = try request(["id": 4, "method": "ping", "params": [:]])
    check(
        "unknown method 后 helper 仍存活",
        (afterUnknown["result"] as? [String: Any])?["ok"] as? Bool == true
    )

    // 5. 非法 JSON → error，且 helper 存活
    sendRaw("this is not json")
    let bad = try readLineData()
    let badJson = try JSONSerialization.jsonObject(with: bad) as? [String: Any]
    check(
        "非法 JSON code == invalid_request",
        (badJson?["error"] as? [String: Any])?["code"] as? String
            == "invalid_request"
    )
    let afterBad = try request(["id": 5, "method": "ping", "params": [:]])
    check(
        "非法 JSON 后 helper 仍存活",
        (afterBad["result"] as? [String: Any])?["ok"] as? Bool == true
    )

    // 6. 缺少 method → error
    let noMethod = try request(["id": 6, "params": [:]])
    check(
        "缺少 method code == invalid_request",
        (noMethod["error"] as? [String: Any])?["code"] as? String
            == "invalid_request"
    )

    let missingID = try request(["method": "ping", "params": [:]])
    check("缺少 id → invalid_request",
          (missingID["error"] as? [String: Any])?["code"] as? String == "invalid_request")
    let boolID = try request(["id": true, "method": "ping", "params": [:]])
    check("bool id → invalid_request",
          (boolID["error"] as? [String: Any])?["code"] as? String == "invalid_request")
    let stringID = try request(["id": "1", "method": "ping", "params": [:]])
    check("string id → invalid_request",
          (stringID["error"] as? [String: Any])?["code"] as? String == "invalid_request")

    // 7. open_app 缺 app → invalid_params（不启动任何 App）
    let noApp = try request(["id": 20, "method": "open_app", "params": [:]])
    check(
        "open_app 缺 app → invalid_params",
        (noApp["error"] as? [String: Any])?["code"] as? String
            == "invalid_params"
    )

    // 8. open_app 空 app → invalid_params
    let emptyApp = try request([
        "id": 21, "method": "open_app", "params": ["app": ""]
    ])
    check(
        "open_app 空 app → invalid_params",
        (emptyApp["error"] as? [String: Any])?["code"] as? String
            == "invalid_params"
    )

    // 9. 不存在的 app → app_not_found（不会真正启动任何东西）
    let missingApp = try request([
        "id": 22,
        "method": "open_app",
        "params": ["app": "VestaDefinitelyMissingApp_9f3a2b"],
    ])
    check(
        "open_app 不存在 → app_not_found",
        (missingApp["error"] as? [String: Any])?["code"] as? String
            == "app_not_found"
    )

    // 10. open_app 出错后 helper 仍能处理下一条 ping
    let afterOpen = try request(["id": 23, "method": "ping", "params": [:]])
    check(
        "open_app 出错后仍能 ping",
        (afterOpen["result"] as? [String: Any])?["ok"] as? Bool == true
    )

    // 11. accessibility_status 返回 trusted bool（默认不弹授权提示）
    let status = try request([
        "id": 30, "method": "accessibility_status", "params": [:]
    ])
    let trusted = (status["result"] as? [String: Any])?["trusted"] as? Bool
    check("accessibility_status 返回 trusted bool", trusted != nil)

    // 12. basic_observe：
    //   - 已授权 → 校验返回 JSON 结构合法（不要求固定前台 App，不断言具体值）；
    //   - 未授权 → 必须返回 accessibility_permission_required。
    let observe = try request(["id": 31, "method": "basic_observe", "params": [:]])
    if trusted == true {
        let result = observe["result"] as? [String: Any]
        check("basic_observe(已授权) 返回 result 对象", result != nil)
        let appOK =
            result?["active_app"] is [String: Any]
                || result?["active_app"] is NSNull
        let winOK =
            result?["active_window"] is [String: Any]
                || result?["active_window"] is NSNull
        check("basic_observe active_app 结构合法", appOK)
        check("basic_observe active_window 结构合法", winOK)
    } else {
        check(
            "basic_observe 未授权 → accessibility_permission_required",
            (observe["error"] as? [String: Any])?["code"] as? String
                == "accessibility_permission_required"
        )
    }

    // 13. basic_observe 出错后 helper 仍能处理下一条 ping
    let afterObserve = try request(["id": 32, "method": "ping", "params": [:]])
    check(
        "basic_observe 后仍能 ping",
        (afterObserve["result"] as? [String: Any])?["ok"] as? Bool == true
    )

    // 14. observe 缺 observation_id → invalid_params（不依赖权限，先校验参数）
    let observeNoId = try request(["id": 40, "method": "observe", "params": [:]])
    check(
        "observe 缺 observation_id → invalid_params",
        (observeNoId["error"] as? [String: Any])?["code"] as? String
            == "invalid_params"
    )

    // 15. observe：未授权 → accessibility_permission_required；
    //     已授权 → 校验返回结构合法（elements 数组 / truncated bool）。
    let observeRes = try request([
        "id": 41, "method": "observe",
        "params": ["observation_id": "test-obs-1"],
    ])
    if trusted == true {
        let result = observeRes["result"] as? [String: Any]
        check("observe(已授权) 返回 result 对象", result != nil)
        check("observe elements 是数组", result?["elements"] is [Any])
        check("observe truncated 是 bool", result?["truncated"] is Bool)
    } else {
        check(
            "observe 未授权 → accessibility_permission_required",
            (observeRes["error"] as? [String: Any])?["code"] as? String
                == "accessibility_permission_required"
        )
    }

    // 16. __test_ax_logic：role / action normalize、value 截断、限制、ref 顺序
    let axLogic = try request(["id": 42, "method": "__test_ax_logic", "params": [:]])
    let logic = axLogic["result"] as? [String: Any]
    let roles = logic?["roles"] as? [[String]]
    check("role AXButton → button", roles?.contains(["AXButton", "button"]) == true)
    check(
        "role AXTextField → text_field",
        roles?.contains(["AXTextField", "text_field"]) == true
    )
    check(
        "role AXMenuItem → menu_item",
        roles?.contains(["AXMenuItem", "menu_item"]) == true
    )
    check(
        "role AXUnknownWidget → unknown_widget",
        roles?.contains(["AXUnknownWidget", "unknown_widget"]) == true
    )
    let axActions = logic?["actions"] as? [[String]]
    check("action AXPress → press", axActions?.contains(["AXPress", "press"]) == true)
    check(
        "action AXShowMenu → show_menu",
        axActions?.contains(["AXShowMenu", "show_menu"]) == true
    )
    let truncation = logic?["value_truncation"] as? [String: Any]
    check("value 短文本保留", truncation?["plain"] as? String == "hello")
    check("value 长文本截断到 1000", truncation?["long_len"] as? Int == 1000)
    let limits = logic?["limits"] as? [String: Any]
    check("max_elements == 300", limits?["max_elements"] as? Int == 300)
    check("max_depth == 12", limits?["max_depth"] as? Int == 12)
    check("ref 顺序 e1/e2/e3", (logic?["refs"] as? [String]) == ["e1", "e2", "e3"])

    // 17. __test_element_mapping：新 observation 替换旧 mapping
    let mapA = try request([
        "id": 43, "method": "__test_element_mapping",
        "params": ["observation_id": "obs-a", "refs": ["e1", "e2", "e3"]],
    ])
    check(
        "mapping obs-a 生效",
        (mapA["result"] as? [String: Any])?["observation_id"] as? String
            == "obs-a"
    )
    check(
        "mapping obs-a refs == [e1,e2,e3]",
        (mapA["result"] as? [String: Any])?["refs"] as? [String]
            == ["e1", "e2", "e3"]
    )
    let mapB = try request([
        "id": 44, "method": "__test_element_mapping",
        "params": ["observation_id": "obs-b", "refs": ["e4", "e5"]],
    ])
    check(
        "mapping 替换为 obs-b",
        (mapB["result"] as? [String: Any])?["observation_id"] as? String
            == "obs-b"
    )
    check(
        "mapping obs-b refs == [e4,e5]（旧 refs 失效）",
        (mapB["result"] as? [String: Any])?["refs"] as? [String]
            == ["e4", "e5"]
    )

    // 18. click_element 生命周期（复用真实校验与成功清理，不需要真实 AX）
    _ = try request([
        "id": 50, "method": "__test_element_mapping",
        "params": ["observation_id": "obs-a", "refs": ["e1", "e2", "e3"]],
    ])

    // 18a. 旧 observation_id → stale_observation
    let stale = try request([
        "id": 51, "method": "__test_click_element",
        "params": ["observation_id": "obs-old", "element_ref": "e1"],
    ])
    check(
        "click 旧 observation_id → stale_observation",
        (stale["error"] as? [String: Any])?["code"] as? String
            == "stale_observation"
    )

    // 18b. 正确 observation_id 但 ref 不存在 → element_not_found
    let notFound = try request([
        "id": 52, "method": "__test_click_element",
        "params": ["observation_id": "obs-a", "element_ref": "e99"],
    ])
    check(
        "click element_ref 不存在 → element_not_found",
        (notFound["error"] as? [String: Any])?["code"] as? String
            == "element_not_found"
    )

    // 18c. 成功后旧 mapping 被清空（obs-a/e1 成功 → 再点同 obs → stale）
    let clickOk = try request([
        "id": 53, "method": "__test_click_element",
        "params": ["observation_id": "obs-a", "element_ref": "e1"],
    ])
    let clickResult = clickOk["result"] as? [String: Any]
    check("click 成功 action == press", clickResult?["action"] as? String == "press")
    check(
        "click 成功回显 element_ref",
        clickResult?["element_ref"] as? String == "e1"
    )
    let afterSuccess = try request([
        "id": 54, "method": "__test_click_element",
        "params": ["observation_id": "obs-a", "element_ref": "e1"],
    ])
    check(
        "click 成功后旧 observation 失效 → stale_observation",
        (afterSuccess["error"] as? [String: Any])?["code"] as? String
            == "stale_observation"
    )

    // 18d. 一次错误后 helper 仍能 ping
    let afterClickPing = try request(["id": 55, "method": "ping", "params": [:]])
    check(
        "click 错误后仍能 ping",
        (afterClickPing["result"] as? [String: Any])?["ok"] as? Bool == true
    )

    // 19. type_text：真实入口必须绑定 fresh observation。
    let typeNoText = try request(["id": 60, "method": "type_text", "params": [:]])
    check(
        "type_text 缺 text → invalid_params",
        (typeNoText["error"] as? [String: Any])?["code"] as? String
            == "invalid_params"
    )
    let typeEmpty = try request([
        "id": 61, "method": "type_text", "params": ["text": ""]
    ])
    check(
        "type_text 空串缺 observation → invalid_params",
        (typeEmpty["error"] as? [String: Any])?["code"] as? String == "invalid_params"
    )

    // 19b. type_text 非空：未授权时验证权限错误；已授权时只走测试成功入口，
    //      默认自动测试绝不向用户当前焦点发送真实文本。
    if trusted == true {
        let typeRes = try request([
            "id": 62,
            "method": "__test_type_success",
            "params": ["text": "Hi"],
        ])
        check(
            "type_text 测试成功入口 characters == 2",
            (typeRes["result"] as? [String: Any])?["characters"] as? Int == 2
        )
    } else {
        let typeRes = try request([
            "id": 62, "method": "type_text", "params": ["text": "Hi"]
        ])
        check(
            "type_text 缺 observation → invalid_params",
            (typeRes["error"] as? [String: Any])?["code"] as? String
                == "invalid_params"
        )
    }

    // 20. __test_type_logic：chunking / character count（不发送真实键盘事件）
    let typeLogic = try request([
        "id": 63, "method": "__test_type_logic", "params": [:]
    ])
    let tLogic = typeLogic["result"] as? [String: Any]
    let tChars = tLogic?["characters"] as? [String: Any]
    check("chars empty == 0", tChars?["empty"] as? Int == 0)
    check("chars Hello Vesta == 11", tChars?["hello"] as? Int == 11)
    check("chars 你好 Vesta == 8", tChars?["cn"] as? Int == 8)
    let tChunks = tLogic?["chunks"] as? [String: Any]
    check("chunks empty == 0", tChunks?["empty"] as? Int == 0)
    check("chunks short == 1", tChunks?["short"] as? Int == 1)
    check("chunks long_250 == 3", tChunks?["long_250"] as? Int == 3)
    check("chunk_size == 100", tChunks?["chunk_size"] as? Int == 100)

    // 21. type_text 出错后 helper 仍能 ping
    let afterTypePing = try request(["id": 64, "method": "ping", "params": [:]])
    check(
        "type_text 后仍能 ping",
        (afterTypePing["result"] as? [String: Any])?["ok"] as? Bool == true
    )

    // 22. __test_key_logic：纯 key / modifier 映射，不发送真实键盘事件。
    let keyLogic = try request([
        "id": 70, "method": "__test_key_logic", "params": [:]
    ])
    let kLogic = keyLogic["result"] as? [String: Any]
    check("全部 V1 key 都有映射", kLogic?["all_supported"] as? Bool == true)
    let keyCodes = kLogic?["key_codes"] as? [String: Any]
    let expectedKeyCodes: [String: Int] = [
        "enter": 36, "return": 36, "tab": 48, "escape": 53,
        "space": 49, "backspace": 51, "delete": 117,
        "left": 123, "right": 124, "up": 126, "down": 125,
        "a": 0, "b": 11, "c": 8, "d": 2, "e": 14, "f": 3,
        "g": 5, "h": 4, "i": 34, "j": 38, "k": 40, "l": 37,
        "m": 46, "n": 45, "o": 31, "p": 35, "q": 12, "r": 15,
        "s": 1, "t": 17, "u": 32, "v": 9, "w": 13, "x": 7,
        "y": 16, "z": 6,
        "0": 29, "1": 18, "2": 19, "3": 20, "4": 21,
        "5": 23, "6": 22, "7": 26, "8": 28, "9": 25,
    ]
    check(
        "key → CGKeyCode 显式映射正确",
        expectedKeyCodes.allSatisfy { key, code in
            keyCodes?[key] as? Int == code
        }
    )
    let supportRows = kLogic?["supported"] as? [[String: Any]]
    let f5Row = supportRows?.first { $0["key"] as? String == "f5" }
    check("F5 不受支持", f5Row?["supported"] as? Bool == false)
    let modifierRows = kLogic?["modifiers"] as? [[String: Any]]
    func normalizedModifier(_ input: String) -> String? {
        modifierRows?.first { $0["input"] as? String == input }?["normalized"]
            as? String
    }
    check("cmd → command", normalizedModifier("cmd") == "command")
    check("ctrl → control", normalizedModifier("ctrl") == "control")
    check("alt → option", normalizedModifier("alt") == "option")
    check("未知 modifier 不规范化", normalizedModifier("bogus") == "")
    check(
        "重复 modifier 去重并保序",
        kLogic?["dedupe"] as? [String] == ["command", "shift"]
    )

    // 23. key_press 参数/错误语义。全部错误都在权限检查和事件发送之前返回。
    let keyMissing = try request([
        "id": 71, "method": "key_press", "params": [:]
    ])
    check(
        "key_press 缺 key → invalid_params",
        (keyMissing["error"] as? [String: Any])?["code"] as? String
            == "invalid_params"
    )
    let keyUnsupported = try request([
        "id": 72, "method": "key_press", "params": ["key": "f5"]
    ])
    check(
        "key_press 缺 observation → invalid_params",
        (keyUnsupported["error"] as? [String: Any])?["code"] as? String
            == "invalid_params"
    )
    let invalidModifier = try request([
        "id": 73,
        "method": "key_press",
        "params": ["key": "a", "modifiers": ["fn"]],
    ])
    check(
        "key_press 缺 observation → invalid_params",
        (invalidModifier["error"] as? [String: Any])?["code"] as? String
            == "invalid_params"
    )
    let invalidModifierShape = try request([
        "id": 74,
        "method": "key_press",
        "params": ["key": "a", "modifiers": "command"],
    ])
    check(
        "key_press modifiers 非数组 → invalid_params",
        (invalidModifierShape["error"] as? [String: Any])?["code"] as? String
            == "invalid_params"
    )
    if trusted == false {
        let keyPermission = try request([
            "id": 75,
            "method": "key_press",
            "params": ["key": "enter", "modifiers": []],
        ])
        check(
            "key_press 缺 observation → invalid_params",
            (keyPermission["error"] as? [String: Any])?["code"] as? String
                == "invalid_params"
        )
    } else {
        check("已授权环境跳过真实 key_press，避免自动按键", true)
    }

    // 24. UI action cache 生命周期：成功清理、空 type/失败 action 不清理。
    let typeCache = try request([
        "id": 76,
        "method": "__test_type_success",
        "params": [
            "text": "hello", "observation_id": "obs-type", "refs": ["e1"],
        ],
    ])
    check(
        "type_text 成功后清空 Observation cache",
        (typeCache["result"] as? [String: Any])?["cache_cleared"] as? Bool
            == true
    )
    let emptyTypeCache = try request([
        "id": 77,
        "method": "__test_type_success",
        "params": [
            "text": "", "observation_id": "obs-empty", "refs": ["e1"],
        ],
    ])
    check(
        "空 type_text 不清空 Observation cache",
        (emptyTypeCache["result"] as? [String: Any])?["cache_cleared"] as? Bool
            == false
    )
    let keyCache = try request([
        "id": 78,
        "method": "__test_key_success",
        "params": [
            "key": "a", "modifiers": ["command", "cmd"],
            "observation_id": "obs-key", "refs": ["e1"],
        ],
    ])
    let keyCacheResult = keyCache["result"] as? [String: Any]
    check(
        "key_press 成功后清空 Observation cache",
        keyCacheResult?["cache_cleared"] as? Bool == true
    )
    check(
        "key_press 成功返回 normalized modifiers",
        keyCacheResult?["modifiers"] as? [String] == ["command"]
    )

    _ = try request([
        "id": 79, "method": "__test_element_mapping",
        "params": ["observation_id": "obs-failure", "refs": ["e1"]],
    ])
    _ = try request([
        "id": 80, "method": "key_press", "params": ["key": "f5"]
    ])
    let cacheAfterFailure = try request([
        "id": 81, "method": "__test_observation_cache", "params": [:]
    ])
    let cacheState = cacheAfterFailure["result"] as? [String: Any]
    check(
        "失败 key_press 不清空 Observation cache",
        cacheState?["observation_id"] as? String == "obs-failure"
            && cacheState?["refs"] as? [String] == ["e1"]
    )

    let afterKeyPing = try request([
        "id": 82, "method": "ping", "params": [:]
    ])
    check(
        "key_press 错误后 helper 仍能 ping",
        (afterKeyPing["result"] as? [String: Any])?["ok"] as? Bool == true
    )

    // 25. V7 纯逻辑：Retina 映射、窗口排序、滚动校验。
    let v7 = try request(["id": 83, "method": "__test_v7_logic", "params": [:]])
    let v7Result = v7["result"] as? [String: Any]
    check("Retina 坐标映射 X", v7Result?["mapped_x"] as? Double == 500)
    check("Retina 坐标映射 Y", v7Result?["mapped_y"] as? Double == 380)
    check("截图边界校验", v7Result?["out_of_bounds"] as? Bool == true)
    check("focused window 优先", v7Result?["window_order"] as? [Int] == [1, 0, 2])
    check("scroll 非零校验", v7Result?["valid_scroll"] as? Bool == true
        && v7Result?["invalid_scroll"] as? Bool == false)

    let changed = try request([
        "id": 84, "method": "__test_fresh_guard",
        "params": ["state_matches": false],
    ])
    let changedResult = changed["result"] as? [String: Any]
    check("desktop state changed 后拒绝", changedResult?["accepted"] as? Bool == false)
    check("desktop state changed 后清 cache",
          changedResult?["cache_cleared"] as? Bool == true)
} catch {
    check("协议用例执行无异常", false, "\(error)")
}

// 26. stdin EOF → 正常退出
try? stdinPipe.fileHandleForWriting.close()
process.waitUntilExit()
check("stdin EOF 后 exit code == 0", process.terminationStatus == 0)

// MARK: - 汇总

if failures == 0 {
    print("")
    print("全部协议检查通过 ✅")
    exit(0)
} else {
    print("")
    print("\(failures) 项检查失败 ❌")
    exit(1)
}
