import AppKit
import QuickLookThumbnailing

/// Gives the Finder the picture a `.pv` file carries of itself, without the render service.
@objc(PVQLThumbnailProvider)
final class ThumbnailProvider: QLThumbnailProvider {
    /// Draws the embedded picture fitted to the requested size, or declines when there is none.
    override func provideThumbnail(
        for request: QLFileThumbnailRequest,
        _ handler: @escaping (QLThumbnailReply?, Error?) -> Void
    ) {
        guard let png = EmbeddedThumbnail.read(request.fileURL, allowStale: false),
              let image = NSImage(data: png), image.size.width > 0, image.size.height > 0
        else {
            handler(nil, CocoaError(.fileReadCorruptFile))
            return
        }
        let scale = min(
            request.maximumSize.width / image.size.width, request.maximumSize.height / image.size.height
        )
        let size = CGSize(width: image.size.width * scale, height: image.size.height * scale)
        let reply = QLThumbnailReply(contextSize: size, currentContextDrawing: { () -> Bool in
            image.draw(in: CGRect(origin: .zero, size: size))
            return true
        })
        handler(reply, nil)
    }
}
