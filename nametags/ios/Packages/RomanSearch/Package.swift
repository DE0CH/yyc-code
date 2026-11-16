// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "RomanSearch",
    platforms: [.iOS(.v17), .macOS(.v14)],
    products: [
        .library(name: "RomanSearch", targets: ["RomanSearch"]),
    ],
    targets: [
        .target(
            name: "RomanSearch",
            resources: [.copy("Resources/romanisation.tsv")]
        ),
        .testTarget(
            name: "RomanSearchTests",
            dependencies: ["RomanSearch"]
        ),
    ]
)
