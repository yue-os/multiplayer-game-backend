"""Simple UDP discovery responder.

Clients can broadcast the ASCII message "DISCOVER" to the UDP port (default 9999).
The responder will reply with a JSON payload: {"host": "<ip>", "port": <http_port>}.

Run alongside the Flask/FastAPI app. It is safe to run in a background thread.
"""
from __future__ import annotations

import os
import socket
import json
import threading
import time

from app.server.utils import get_local_ip


DISCOVERY_PORT = int(os.getenv("DISCOVERY_UDP_PORT", "9999"))
BACKEND_PORT = int(os.getenv("BACKEND_HTTP_PORT", os.getenv("PORT", "8000")))
LISTEN_ADDR = "0.0.0.0"


def _handle_requests() -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((LISTEN_ADDR, DISCOVERY_PORT))
    except Exception as e:
        print(f"udp_discovery: failed to bind UDP {LISTEN_ADDR}:{DISCOVERY_PORT}: {e}")
        return

    print(f"udp_discovery: listening on UDP {LISTEN_ADDR}:{DISCOVERY_PORT}")

    while True:
        try:
            data, addr = sock.recvfrom(2048)
            if not data:
                continue
            try:
                text = data.decode("utf-8", errors="ignore").strip()
            except Exception:
                continue

            if text.upper() != "DISCOVER":
                # ignore other messages
                continue

            host_ip = get_local_ip()
            payload = {"host": host_ip, "port": BACKEND_PORT}
            resp = json.dumps(payload).encode("utf-8")
            try:
                sock.sendto(resp, addr)
            except Exception:
                pass
        except Exception:
            # prevent tight loop on unexpected errors
            time.sleep(0.5)


def start_background_discovery():
    thread = threading.Thread(target=_handle_requests, daemon=True, name="udp-discovery")
    thread.start()


if __name__ == "__main__":
    start_background_discovery()
    # keep main thread alive
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
