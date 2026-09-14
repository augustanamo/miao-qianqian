// swift-tools-version:5.7
import PackageDescription

let package = Package(
    name: "AutoCheck",
    platforms: [.macOS(.v13)],
    targets: [
        .executableTarget(name: "AutoCheck", path: "Sources/AutoCheck")
    ]
)
