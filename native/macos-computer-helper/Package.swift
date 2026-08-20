// swift-tools-version: 5.9
// macOS Computer Helper —— 长驻 subprocess，JSON Lines 协议。
//
// 仅提供可执行文件；协议测试见 Tests/protocol_check.swift
// （本机只有 Command Line Tools、无 Xcode，XCTest 不可用，
//  因此用 Foundation 驱动的独立脚本代替 swift test）。
//
// 不包含任何真实电脑控制逻辑
// （AXUIElement / ScreenCaptureKit / CGEvent 均不在本轮）。

import PackageDescription

let package = Package(
    name: "MacOSComputerHelper",
    targets: [
        .executableTarget(
            name: "MacOSComputerHelper",
            path: "Sources/MacOSComputerHelper"
        ),
    ]
)
