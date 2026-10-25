"""Private loopback service, started and stopped by the macOS app."""
import json
import os
import socket
import threading
import time
from pathlib import Path

import uvicorn
from beatcut.server import app


if __name__ == "__main__":
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", loop="asyncio", http="h11"))
    parent = os.getppid()

    def supervise():
        while not server.started and not server.should_exit:
            time.sleep(.05)
        if server.started:
            Path(os.environ["BEATCUT_READY_FILE"]).write_text(json.dumps({"port": port}))
        while not server.should_exit:
            if os.getppid() != parent:
                server.should_exit = True
            time.sleep(1)

    threading.Thread(target=supervise, daemon=True).start()
    server.run(sockets=[listener])
