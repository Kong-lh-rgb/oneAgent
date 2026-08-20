// swift-tools-version: 5.9
// macOS Computer Helper —— 长驻 subprocess，JSON Lines 协议。
//
// 纯键位映射放在 MacOSComputerCore；本机 Command Line Tools 不提供
// XCTest/Testing 模块，因此自动测试由 Tests/protocol_check.swift 通过
// JSON Lines 测试入口执行，全程不发送真实系统事件。

import PackageDescription

let package = Package(
    name: "MacOSComputerHelper",
    targets: [
        .target(
            name: "MacOSComputerCore",
            path: "Sources/MacOSComputerCore"
        ),
        .executableTarget(
            name: "MacOSComputerHelper",
            dependencies: ["MacOSComputerCore"],
            path: "Sources/MacOSComputerHelper"
        ),
    ]
)
