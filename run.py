"""Launch on loopback only; open the interface after the server is ready."""
import threading
import time
import urllib.request
import webbrowser

import uvicorn

URL = "http://127.0.0.1:8765"


def open_when_ready():
    for _ in range(100):
        try:
            with urllib.request.urlopen(URL + "/api/status", timeout=.3) as response:
                if response.status == 200:
                    webbrowser.open(URL)
                    return
        except OSError:
            time.sleep(.1)


if __name__ == "__main__":
    print("BeatCut Studio · 拍点工坊\n" + URL + "\n关闭此终端或按 Ctrl+C 停止。")
    threading.Thread(target=open_when_ready, daemon=True).start()
    uvicorn.run("beatcut.server:app", host="127.0.0.1", port=8765)
