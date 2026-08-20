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
        "params": ["app": "OneAgentDefinitelyMissingApp_9f3a2b"],
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
} catch {
    check("协议用例执行无异常", false, "\(error)")
}

// 11. stdin EOF → 正常退出
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
