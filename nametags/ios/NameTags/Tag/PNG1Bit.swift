import Foundation

/// A 1-bit greyscale PNG (colour type 0, bit depth 1: 0 = black, 1 = white) from 8-bit grey pixels, thresholded at
/// the middle. The image data goes in stored (uncompressed) deflate blocks: a 848×590 tag is 63 KB, and this needs
/// no compression library.
enum PNG1Bit {
    static func encode(width: Int, height: Int, gray: [UInt8], threshold: UInt8 = 128) -> Data {
        precondition(gray.count == width * height)
        let rowBytes = (width + 7) / 8
        var raw = [UInt8](repeating: 0, count: (rowBytes + 1) * height)
        for y in 0..<height {
            let o = y * (rowBytes + 1)
            raw[o] = 0   // filter: none
            for bx in 0..<rowBytes {
                var b: UInt8 = 0
                for bit in 0..<8 {
                    let x = bx * 8 + bit
                    let white = x >= width || gray[y * width + x] >= threshold   // padding bits white
                    if white { b |= 0x80 >> UInt8(bit) }
                }
                raw[o + 1 + bx] = b
            }
        }
        var png = Data([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A])
        var ihdr = [UInt8]()
        ihdr += be32(UInt32(width)); ihdr += be32(UInt32(height))
        ihdr += [1, 0, 0, 0, 0]   // bit depth 1, greyscale, deflate, filter 0, no interlace
        chunk(&png, "IHDR", ihdr)
        chunk(&png, "IDAT", zlibStored(raw))
        chunk(&png, "IEND", [])
        return png
    }

    static func zlibStored(_ data: [UInt8]) -> [UInt8] {
        var out: [UInt8] = [0x78, 0x01]
        var i = 0
        repeat {
            let n = min(65535, data.count - i)
            let last: UInt8 = i + n >= data.count ? 1 : 0
            out.append(last)
            out += [UInt8(n & 0xFF), UInt8(n >> 8), UInt8(~n & 0xFF), UInt8((~n >> 8) & 0xFF)]
            out += data[i..<(i + n)]
            i += n
        } while i < data.count
        out += be32(adler32(data))
        return out
    }

    static func adler32(_ d: [UInt8]) -> UInt32 {
        var a: UInt32 = 1, b: UInt32 = 0
        for x in d { a = (a + UInt32(x)) % 65521; b = (b + a) % 65521 }
        return (b << 16) | a
    }

    private static let crcTable: [UInt32] = (0..<256).map { n -> UInt32 in
        var c = UInt32(n)
        for _ in 0..<8 { c = (c & 1) != 0 ? 0xEDB88320 ^ (c >> 1) : c >> 1 }
        return c
    }

    static func crc32(_ d: [UInt8]) -> UInt32 {
        var c: UInt32 = 0xFFFFFFFF
        for x in d { c = crcTable[Int((c ^ UInt32(x)) & 0xFF)] ^ (c >> 8) }
        return c ^ 0xFFFFFFFF
    }

    private static func be32(_ v: UInt32) -> [UInt8] {
        [UInt8(v >> 24), UInt8((v >> 16) & 0xFF), UInt8((v >> 8) & 0xFF), UInt8(v & 0xFF)]
    }

    private static func chunk(_ png: inout Data, _ type: String, _ body: [UInt8]) {
        let t = Array(type.utf8)
        png.append(contentsOf: be32(UInt32(body.count)))
        png.append(contentsOf: t + body)
        png.append(contentsOf: be32(crc32(t + body)))
    }
}
