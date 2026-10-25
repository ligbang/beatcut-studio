import AppKit

let folder = URL(fileURLWithPath: CommandLine.arguments[1])
try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
for size in [16, 32, 128, 256, 512] {
    for scale in [1, 2] {
        let pixels = size * scale
        let image = NSImage(size: NSSize(width: pixels, height: pixels))
        image.lockFocus()
        let p = CGFloat(pixels)
        let background = NSBezierPath(roundedRect: NSRect(x: p * 0.06, y: p * 0.06, width: p * 0.88, height: p * 0.88), xRadius: p * 0.2, yRadius: p * 0.2)
        NSColor(calibratedRed: 0.16, green: 0.22, blue: 0.30, alpha: 1).setFill()
        background.fill()
        for (i, height) in [0.18, 0.36, 0.58, 0.30, 0.46, 0.24, 0.12].enumerated() {
            NSColor(calibratedRed: 0.68, green: 0.83, blue: 1, alpha: 1).setFill()
            NSBezierPath(roundedRect: NSRect(x: p * (0.22 + Double(i) * 0.085), y: p * (0.5 - height / 2), width: p * 0.04, height: p * height), xRadius: p * 0.02, yRadius: p * 0.02).fill()
        }
        NSColor(calibratedRed: 1, green: 0.79, blue: 0.32, alpha: 1).setFill()
        NSBezierPath(rect: NSRect(x: p * 0.49, y: p * 0.17, width: p * 0.015, height: p * 0.66)).fill()
        image.unlockFocus()
        let bitmap = NSBitmapImageRep(data: image.tiffRepresentation!)!
        let name = "icon_\(size)x\(size)\(scale == 2 ? "@2x" : "").png"
        try bitmap.representation(using: .png, properties: [:])!.write(to: folder.appendingPathComponent(name))
    }
}
