import Foundation

/// 不依赖系统 API 的截图坐标映射，便于离线测试。
public struct LogicalBounds: Equatable {
    public let x: Double, y: Double, width: Double, height: Double
    public init(x: Double, y: Double, width: Double, height: Double) {
        self.x = x; self.y = y; self.width = width; self.height = height
    }
}

public struct ScreenshotMapping: Equatable {
    public let pixelWidth: Int, pixelHeight: Int
    public let bounds: LogicalBounds
    public init(pixelWidth: Int, pixelHeight: Int, bounds: LogicalBounds) {
        self.pixelWidth = pixelWidth; self.pixelHeight = pixelHeight; self.bounds = bounds
    }

    public func globalPoint(x: Int, y: Int) -> (x: Double, y: Double)? {
        guard pixelWidth > 0, pixelHeight > 0,
              x >= 0, y >= 0, x < pixelWidth, y < pixelHeight else { return nil }
        return (bounds.x + Double(x) * bounds.width / Double(pixelWidth),
                bounds.y + Double(y) * bounds.height / Double(pixelHeight))
    }
}

/// focused 窗口优先，剩余窗口保持 AXWindows 顺序。
public func focusedFirstIndices(count: Int, focusedIndex: Int?) -> [Int] {
    guard count > 0 else { return [] }
    guard let focusedIndex, (0..<count).contains(focusedIndex) else {
        return Array(0..<count)
    }
    return [focusedIndex] + (0..<count).filter { $0 != focusedIndex }
}

public func validScroll(deltaX: Int, deltaY: Int) -> Bool {
    deltaX != 0 || deltaY != 0
}
