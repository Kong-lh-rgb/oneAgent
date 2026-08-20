// macOS Computer Helper V1 —— 长驻 subprocess。
//
// 职责：从 stdin 按行读 JSON，向 stdout 按行写 JSON（JSON Lines 协议）。
// 本轮实现 ping / system_info / open_app。
//
// 关键边界：
// - stdout 只允许输出协议 JSON；日志一律写 stderr；
// - 非法 JSON / unknown method / 缺 method 都返回 error，进程不退出；
// - stdin EOF 时正常退出（exit 0）；
// - open_app 用 NSWorkspace 原生 API 打开应用，不用 shell / osascript /
//   subprocess，不模拟鼠标点 Dock。

import AppKit
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
