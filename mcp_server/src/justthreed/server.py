"""FastMCP server exposing Blender control tools.

This process is launched by an MCP-capable AI client (Claude Desktop, Cursor,
Ollama, etc.) and relays tool calls over a TCP socket to the JustThreed
Blender extension, which runs inside Blender on localhost:9876.
"""
from __future__ import annotations

import base64
import json
import socket

from mcp.server.fastmcp import FastMCP, Image

HOST = "localhost"
PORT = 9876

mcp = FastMCP("justthreed")


class BlenderError(RuntimeError):
    """Raised when the Blender extension returns an error or is unreachable."""


def _send(command: dict, timeout: float = 15.0) -> dict:
    payload = {**command, "_timeout": timeout}
    sock_timeout = timeout + 5.0
    try:
        with socket.create_connection((HOST, PORT), timeout=sock_timeout) as s:
            s.settimeout(sock_timeout)
            s.sendall((json.dumps(payload) + "\n").encode("utf-8"))
            buf = b""
            while b"\n" not in buf:
                chunk = s.recv(65536)
                if not chunk:
                    break
                buf += chunk
    except ConnectionRefusedError as exc:
        raise BlenderError(
            f"Could not connect to Blender on {HOST}:{PORT}. "
            "Make sure Blender is running, the JustThreed extension is enabled, "
            "and you clicked 'Start MCP Server' in the N-panel."
        ) from exc
    except socket.timeout as exc:
        raise BlenderError("Timed out waiting for Blender to respond.") from exc

    line, _, _ = buf.partition(b"\n")
    try:
        response = json.loads(line.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise BlenderError(f"Invalid response from Blender: {line!r}") from exc

    if not response.get("ok"):
        raise BlenderError(response.get("error", "Unknown error from Blender"))
    return response


@mcp.tool()
def ping() -> str:
    """Check that the Blender JustThreed extension is reachable and responding."""
    return _send({"tool": "ping"}).get("message", "ok")


@mcp.tool()
def create_cube() -> str:
    """Create a default cube at the origin of the current Blender scene."""
    return _send({"tool": "create_cube"}).get("message", "Cube created")


@mcp.tool()
def get_scene_info() -> dict:
    """List every object in the current Blender scene with name, type, location,
    dimensions, visibility, and polycount (for meshes). Use this first whenever
    you need to understand what is already in the scene before acting."""
    return _send({"tool": "get_scene_info"})


@mcp.tool()
def get_object(name: str) -> dict:
    """Return detailed state for a single object: transform, dimensions, parent,
    collections, material slots, modifier stack, and mesh statistics."""
    return _send({"tool": "get_object", "name": name})


@mcp.tool()
def create_primitive(
    type: str = "CUBE",
    name: str | None = None,
    location: list[float] | None = None,
    rotation: list[float] | None = None,
    scale: list[float] | None = None,
) -> dict:
    """Create a primitive mesh object. `type` is one of CUBE, SPHERE, ICOSPHERE,
    CYLINDER, CONE, TORUS, PLANE, MONKEY. `location`, `rotation` (euler radians),
    and `scale` are 3-element lists. If `name` is given, the new object is renamed."""
    return _send({
        "tool": "create_primitive",
        "type": type,
        "name": name,
        "location": location or [0.0, 0.0, 0.0],
        "rotation": rotation or [0.0, 0.0, 0.0],
        "scale": scale or [1.0, 1.0, 1.0],
    })


@mcp.tool()
def delete_object(name: str) -> str:
    """Delete an object by name. Returns a confirmation message."""
    return _send({"tool": "delete_object", "name": name}).get("message", "deleted")


@mcp.tool()
def render_image(output_path: str) -> dict:
    """Render the current scene through the active camera to a file on disk.
    `output_path` should end in .png or .jpg. Uses the scene's current resolution
    and render engine. Blocks until the render finishes (up to 5 minutes)."""
    return _send({"tool": "render_image", "output_path": output_path}, timeout=300.0)


@mcp.tool()
def render_and_show(resolution: int = 512) -> Image:
    """Render the current scene at a low preview resolution and return the PNG
    so you can actually see the result. Use this for vision-in-the-loop iteration:
    make a change, render, look at it, decide what to fix, repeat. `resolution` is
    the longest edge in pixels (default 512) — keep it small for fast feedback."""
    response = _send({"tool": "render_and_show", "resolution": resolution}, timeout=300.0)
    data = base64.b64decode(response["data_base64"])
    return Image(data=data, format="png")


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
