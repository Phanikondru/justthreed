"""Manual smoke test for the JustThreed Blender extension socket server.

Usage:
    1. Start Blender, enable the JustThreed extension.
    2. Press N in the viewport, open the JustThreed tab, click "Start MCP Server".
    3. Run this script: python scripts/test_socket.py
    4. Watch Blender: a sphere appears, then gets renamed, inspected, rendered,
       and deleted. This script prints the responses.
"""
from __future__ import annotations

import json
import socket
import sys

HOST = "localhost"
PORT = 9876


def send(command: dict, timeout: float = 15.0) -> dict:
    payload = {**command, "_timeout": timeout}
    with socket.create_connection((HOST, PORT), timeout=timeout + 5.0) as s:
        s.settimeout(timeout + 5.0)
        s.sendall((json.dumps(payload) + "\n").encode("utf-8"))
        buf = b""
        while b"\n" not in buf:
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
        line, _, _ = buf.partition(b"\n")
        return json.loads(line.decode("utf-8"))


def check(label: str, result: dict) -> None:
    # Don't dump huge base64 blobs to stdout.
    preview = {k: v for k, v in result.items() if k != "data_base64"}
    if "data_base64" in result:
        preview["data_base64_len"] = len(result["data_base64"])
    print(f"{label:20s}->", preview)
    if not result.get("ok"):
        raise SystemExit(f"FAILED: {label}")


def main() -> int:
    print(f"Connecting to {HOST}:{PORT} ...")
    try:
        check("ping", send({"tool": "ping"}))
        check("create_cube", send({"tool": "create_cube"}))

        check("create_primitive", send({
            "tool": "create_primitive",
            "type": "SPHERE",
            "name": "TestSphere",
            "location": [2.0, 0.0, 0.0],
            "scale": [0.5, 0.5, 0.5],
        }))
        check("get_scene_info", send({"tool": "get_scene_info"}))
        check("get_object", send({"tool": "get_object", "name": "TestSphere"}))

        check("render_and_show", send(
            {"tool": "render_and_show", "resolution": 256},
            timeout=300.0,
        ))

        check("delete_object", send({"tool": "delete_object", "name": "TestSphere"}))
        return 0
    except ConnectionRefusedError:
        print("ERROR: connection refused.")
        print("       Is Blender running with the JustThreed MCP Server started?")
        return 1


if __name__ == "__main__":
    sys.exit(main())
