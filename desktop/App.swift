import AppKit
import WebKit

final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate, WKUIDelegate, WKNavigationDelegate, WKDownloadDelegate, WKScriptMessageHandlerWithReply {
    var window: NSWindow!
    var web: WKWebView!
    var engine: Process?
    var readyFile: URL!
    var poll: Timer?
    var downloadFiles: [ObjectIdentifier: URL] = [:]
    var origin: URL?
    var helpWindow: NSWindow?

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        NSApp.appearance = NSAppearance(named: .darkAqua)
        makeMenu()
        let config = WKWebViewConfiguration()
        config.mediaTypesRequiringUserActionForPlayback = []
        config.userContentController.addScriptMessageHandler(self, contentWorld: .page, name: "desktop")
        web = WKWebView(frame: .zero, configuration: config)
        web.uiDelegate = self
        web.navigationDelegate = self
        web.allowsBackForwardNavigationGestures = false
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1120, height: 900), styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        window.title = "BeatCut Studio · 拍点工坊"
        window.minSize = NSSize(width: 860, height: 660)
        window.contentView = web
        window.delegate = self
        window.setFrameAutosaveName("BeatCutMainWindow")
        window.center()
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        web.loadHTMLString("<body style='background:#202225;color:#cbd0d7;font:15px -apple-system;display:grid;place-items:center;height:90vh'><p>正在启动拍点工坊…</p></body>", baseURL: nil)
        startEngine()
    }

    func makeMenu() {
        let bar = NSMenu()
        let appItem = NSMenuItem()
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "关于拍点工坊", action: #selector(about), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "退出拍点工坊", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu
        bar.addItem(appItem)
        let fileItem = NSMenuItem(title: "文件", action: nil, keyEquivalent: "")
        let file = NSMenu(title: "文件")
        for (title, id, key) in [("导入音乐…", "music-input", "i"), ("添加视频 / 图片…", "clips-input", ""), ("打开项目…", "open-project", "o"), ("保存项目…", "save-project", "s"), ("导出到 Final Cut Pro", "export", "e")] {
            let item = NSMenuItem(title: title, action: #selector(editorAction(_:)), keyEquivalent: key)
            item.representedObject = id
            item.target = self
            file.addItem(item)
        }
        fileItem.submenu = file
        bar.addItem(fileItem)
        let editItem = NSMenuItem(title: "编辑", action: nil, keyEquivalent: "")
        let edit = NSMenu(title: "编辑")
        for (title, selector, key) in [("撤销", "undo:", "z"), ("剪切", "cut:", "x"), ("复制", "copy:", "c"), ("粘贴", "paste:", "v"), ("全选", "selectAll:", "a")] {
            edit.addItem(withTitle: title, action: Selector(selector), keyEquivalent: key)
        }
        editItem.submenu = edit
        bar.addItem(editItem)
        NSApp.mainMenu = bar
    }

    @objc func editorAction(_ item: NSMenuItem) {
        guard let id = item.representedObject as? String else { return }
        web.evaluateJavaScript("document.getElementById('\(id)').click()")
    }

    @objc func about() {
        NSApp.orderFrontStandardAboutPanel(options: [.applicationName: "BeatCut Studio · 拍点工坊", .applicationVersion: "0.1.0", .credits: NSAttributedString(string: "本地音乐节拍标记与 Final Cut Pro 卡点剪辑\nMIT 开源 · 所有媒体在本机处理")])
    }

    func startEngine() {
        do {
            let fm = FileManager.default
            let support = fm.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0].appendingPathComponent("BeatCut Studio")
            let cache = fm.urls(for: .cachesDirectory, in: .userDomainMask)[0].appendingPathComponent("BeatCut Studio")
            try fm.createDirectory(at: support, withIntermediateDirectories: true)
            try fm.createDirectory(at: cache, withIntermediateDirectories: true)
            readyFile = cache.appendingPathComponent("\(UUID().uuidString).ready.json")
            let resources = Bundle.main.resourceURL!
            let backend = resources.appendingPathComponent("beatcut-engine")
            let process = Process()
            process.executableURL = backend.appendingPathComponent("beatcut-engine")
            var env = ProcessInfo.processInfo.environment
            env["BEATCUT_DATA_DIR"] = support.path
            env["BEATCUT_READY_FILE"] = readyFile.path
            env["NUMBA_CACHE_DIR"] = cache.appendingPathComponent("numba").path
            env["PATH"] = backend.appendingPathComponent("_internal/bin").path + ":/usr/bin:/bin:/usr/sbin:/sbin"
            process.environment = env
            let log = cache.appendingPathComponent("engine.log")
            fm.createFile(atPath: log.path, contents: nil)
            let output = try FileHandle(forWritingTo: log)
            process.standardOutput = output
            process.standardError = output
            try process.run()
            engine = process
            var attempts = 0
            poll = Timer.scheduledTimer(withTimeInterval: 0.15, repeats: true) { [weak self] timer in
                guard let self else { timer.invalidate(); return }
                attempts += 1
                if let data = try? Data(contentsOf: self.readyFile),
                   let json = try? JSONSerialization.jsonObject(with: data) as? [String: Int],
                   let port = json["port"], let url = URL(string: "http://127.0.0.1:\(port)") {
                    timer.invalidate()
                    self.origin = url
                    self.web.load(URLRequest(url: url))
                } else if !process.isRunning || attempts > 400 {
                    timer.invalidate()
                    self.error("分析引擎启动失败。可在「~/Library/Caches/BeatCut Studio/engine.log」查看详细记录。")
                }
            }
        } catch { self.error(error.localizedDescription) }
    }

    func error(_ text: String) {
        let alert = NSAlert()
        alert.messageText = "拍点工坊"
        alert.informativeText = text
        alert.beginSheetModal(for: window)
    }

    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage, replyHandler: @escaping (Any?, String?) -> Void) {
        guard message.frameInfo.isMainFrame, message.frameInfo.request.url?.host == "127.0.0.1", message.frameInfo.request.url?.port == origin?.port,
              let body = message.body as? [String: String], body["action"] == "saveProject", let text = body["text"] else {
            replyHandler(nil, "Unsupported action"); return
        }
        let panel = NSSavePanel()
        panel.nameFieldStringValue = body["name"] ?? "project.beatcut.json"
        panel.beginSheetModal(for: window) { response in
            guard response == .OK, let url = panel.url else { replyHandler(false, nil); return }
            do { try text.write(to: url, atomically: true, encoding: .utf8); replyHandler(true, nil) }
            catch { replyHandler(nil, error.localizedDescription) }
        }
    }

    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping ([URL]?) -> Void) {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = parameters.allowsMultipleSelection
        panel.beginSheetModal(for: window) { response in completionHandler(response == .OK ? panel.urls : nil) }
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = navigationAction.request.url else { decisionHandler(.cancel); return }
        if url.scheme == "about" { decisionHandler(.allow); return }
        guard url.host == "127.0.0.1", url.port == origin?.port else {
            if ["https", "http"].contains(url.scheme ?? "") { NSWorkspace.shared.open(url) }
            decisionHandler(.cancel); return
        }
        if navigationAction.shouldPerformDownload { decisionHandler(.download) }
        else if navigationAction.targetFrame == nil {
            let help = WKWebView(frame: .zero)
            let panel = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 760, height: 700), styleMask: [.titled, .closable, .resizable], backing: .buffered, defer: false)
            panel.title = "拍点工坊 · 使用说明"
            panel.contentView = help
            panel.isReleasedWhenClosed = false
            panel.center()
            panel.makeKeyAndOrderFront(nil)
            help.load(URLRequest(url: url))
            helpWindow = panel
            decisionHandler(.cancel)
        }
        else { decisionHandler(.allow) }
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationResponse: WKNavigationResponse, decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        let response = navigationResponse.response as? HTTPURLResponse
        decisionHandler(response?.value(forHTTPHeaderField: "Content-Disposition")?.contains("attachment") == true ? .download : .allow)
    }

    func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) { download.delegate = self }
    func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) { download.delegate = self }
    func download(_ download: WKDownload, decideDestinationUsing response: URLResponse, suggestedFilename: String, completionHandler: @escaping (URL?) -> Void) {
        let panel = NSSavePanel()
        panel.nameFieldStringValue = suggestedFilename
        panel.beginSheetModal(for: window) { result in
            guard result == .OK, let url = panel.url else { completionHandler(nil); return }
            // WebKit requires a new destination; preserve an existing file until the download succeeds.
            let temporary = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
            self.downloadFiles[ObjectIdentifier(download)] = url
            download.progress.setUserInfoObject(temporary, forKey: .fileURLKey)
            completionHandler(temporary)
        }
    }
    func downloadDidFinish(_ download: WKDownload) {
        guard let target = downloadFiles.removeValue(forKey: ObjectIdentifier(download)),
              let temporary = download.progress.userInfo[.fileURLKey] as? URL else { return }
        do {
            if FileManager.default.fileExists(atPath: target.path) {
                _ = try FileManager.default.replaceItemAt(target, withItemAt: temporary)
            } else { try FileManager.default.moveItem(at: temporary, to: target) }
        } catch { self.error(error.localizedDescription) }
    }
    func download(_ download: WKDownload, didFailWithError error: Error, resumeData: Data?) {
        downloadFiles.removeValue(forKey: ObjectIdentifier(download))
        self.error(error.localizedDescription)
    }

    func windowShouldClose(_ sender: NSWindow) -> Bool { NSApp.terminate(nil); return false }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        web.evaluateJavaScript("window.beatcutCanClose ? window.beatcutCanClose() : true") { value, _ in
            if value as? Bool == false {
                let alert = NSAlert()
                alert.messageText = "项目还有未保存的修改"
                alert.informativeText = "退出前可取消并按 ⌘S 保存项目。已导入的素材仍会保存在本机。"
                alert.addButton(withTitle: "取消")
                alert.addButton(withTitle: "退出，不保存")
                alert.beginSheetModal(for: self.window) { response in
                    NSApp.reply(toApplicationShouldTerminate: response == .alertSecondButtonReturn)
                }
            } else { NSApp.reply(toApplicationShouldTerminate: true) }
        }
        return .terminateLater
    }
    func applicationWillTerminate(_ notification: Notification) {
        poll?.invalidate()
        if engine?.isRunning == true { engine?.terminate() }
        if let readyFile { try? FileManager.default.removeItem(at: readyFile) }
    }
}

let delegate = AppDelegate()
NSApplication.shared.delegate = delegate
NSApplication.shared.run()
