import AppKit
import CoreGraphics
import Foundation
import ImageIO
import ScreenCaptureKit
import UniformTypeIdentifiers
import MacOSComputerCore

struct WindowScreenshot {
    let path: String
    let mapping: ScreenshotMapping
}

enum WindowScreenshotError: Error {
    case unavailable(String)
    case permissionRequired
    case captureFailed(String)
}

func screenCaptureGranted(prompt: Bool) -> Bool {
    if #available(macOS 10.15, *) {
        if CGPreflightScreenCaptureAccess() { return true }
        return prompt ? CGRequestScreenCaptureAccess() : false
    }
    return false
}

/// ScreenCaptureKit 的 async API 在 helper 的同步 JSONL 主循环外执行并等待单帧。
@available(macOS 14.0, *)
func captureWindow(pid: pid_t, bounds: CGRect, title: String,
                   outputPath: String) -> Result<WindowScreenshot, WindowScreenshotError> {
    guard screenCaptureGranted(prompt: false) else { return .failure(.permissionRequired) }
    let semaphore = DispatchSemaphore(value: 0)
    var captured: Result<WindowScreenshot, WindowScreenshotError>!
    Task {
        do {
            let content = try await SCShareableContent.excludingDesktopWindows(
                false, onScreenWindowsOnly: true)
            let candidates = content.windows.filter {
                $0.owningApplication?.processID == pid
            }
            let window = candidates.min { lhs, rhs in
                func score(_ item: SCWindow) -> Double {
                    let frame = item.frame
                    var value = abs(frame.minX - bounds.minX) + abs(frame.minY - bounds.minY)
                        + abs(frame.width - bounds.width) + abs(frame.height - bounds.height)
                    if !title.isEmpty && item.title == title { value -= 1 }
                    return value
                }
                return score(lhs) < score(rhs)
            }
            guard let window else { throw WindowScreenshotError.captureFailed("window not found") }
            let filter = SCContentFilter(desktopIndependentWindow: window)
            let configuration = SCStreamConfiguration()
            let scale = NSScreen.screens.first(where: { $0.frame.intersects(bounds) })?
                .backingScaleFactor ?? 1
            configuration.width = max(1, Int(bounds.width * scale))
            configuration.height = max(1, Int(bounds.height * scale))
            configuration.showsCursor = false
            let image = try await SCScreenshotManager.captureImage(
                contentFilter: filter, configuration: configuration)
            let url = URL(fileURLWithPath: outputPath)
            guard let destination = CGImageDestinationCreateWithURL(
                url as CFURL, UTType.png.identifier as CFString, 1, nil) else {
                throw WindowScreenshotError.captureFailed("cannot create PNG destination")
            }
            CGImageDestinationAddImage(destination, image, nil)
            guard CGImageDestinationFinalize(destination) else {
                throw WindowScreenshotError.captureFailed("cannot write PNG")
            }
            captured = .success(WindowScreenshot(path: outputPath,
                mapping: ScreenshotMapping(pixelWidth: image.width, pixelHeight: image.height,
                    bounds: LogicalBounds(x: bounds.minX, y: bounds.minY,
                                          width: bounds.width, height: bounds.height))))
        } catch let error as WindowScreenshotError {
            captured = .failure(error)
        } catch {
            captured = .failure(.captureFailed(String(describing: error)))
        }
        semaphore.signal()
    }
    semaphore.wait()
    return captured
}
