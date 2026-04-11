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

        # Phase 6 — transforms
        check("move_object", send({
            "tool": "move_object", "name": "TestSphere", "delta": [0.0, 1.5, 0.0],
        }))
        check("rotate_object", send({
            "tool": "rotate_object", "name": "TestSphere", "axis": "Z", "radians": 0.7854,
        }))
        check("scale_object", send({
            "tool": "scale_object", "name": "TestSphere", "factor": 1.5,
        }))
        check("set_transform", send({
            "tool": "set_transform",
            "name": "TestSphere",
            "location": [2.0, 0.0, 1.0],
        }))
        check("set_origin", send({
            "tool": "set_origin", "name": "TestSphere", "to": "GEOMETRY",
        }))

        # Phase 6 — modifiers
        check("add_modifier(SUBSURF)", send({
            "tool": "add_modifier",
            "name": "TestSphere",
            "type": "SUBSURF",
            "params": {"levels": 2, "render_levels": 3},
        }))
        check("add_modifier(BEVEL)", send({
            "tool": "add_modifier",
            "name": "TestSphere",
            "type": "BEVEL",
            "params": {"width": 0.05, "segments": 4},
        }))
        check("set_modifier_param", send({
            "tool": "set_modifier_param",
            "name": "TestSphere",
            "modifier_name": "Subsurf",
            "params": {"levels": 3},
        }))
        check("reorder_modifier", send({
            "tool": "reorder_modifier",
            "name": "TestSphere",
            "modifier_name": "Bevel",
            "index": 0,
        }))
        check("get_object(stack)", send({"tool": "get_object", "name": "TestSphere"}))
        check("remove_modifier", send({
            "tool": "remove_modifier",
            "name": "TestSphere",
            "modifier_name": "Bevel",
        }))

        # Phase 7 — materials + shader node graph
        check("create_material", send({
            "tool": "create_material",
            "name": "TestRed",
            "base_color": [0.8, 0.05, 0.05],
            "roughness": 0.3,
            "metallic": 0.0,
        }))
        check("create_glass_material", send({
            "tool": "create_glass_material",
            "name": "TestGlass",
            "color": [0.9, 0.95, 1.0],
            "ior": 1.45,
            "roughness": 0.0,
            "transmission": 1.0,
        }))
        check("assign_material", send({
            "tool": "assign_material",
            "object_name": "TestSphere",
            "material_name": "TestRed",
            "slot_index": 0,
        }))
        check("list_materials", send({"tool": "list_materials"}))
        check("duplicate_material", send({
            "tool": "duplicate_material",
            "name": "TestRed",
            "new_name": "TestRed_Copy",
        }))
        check("add_shader_node", send({
            "tool": "add_shader_node",
            "material_name": "TestRed",
            "node_type": "TEX_NOISE",
            "location": [-400.0, 0.0],
            "name": "Noise1",
        }))
        check("set_shader_node_param", send({
            "tool": "set_shader_node_param",
            "material_name": "TestRed",
            "node": "Noise1",
            "params": {"Scale": 12.0, "Detail": 3.0},
        }))
        check("connect_shader_nodes", send({
            "tool": "connect_shader_nodes",
            "material_name": "TestRed",
            "from_node": "Noise1",
            "from_socket": "Fac",
            "to_node": "Principled BSDF",
            "to_socket": "Roughness",
        }))
        check("disconnect_shader_nodes", send({
            "tool": "disconnect_shader_nodes",
            "material_name": "TestRed",
            "from_node": "Noise1",
            "from_socket": "Fac",
            "to_node": "Principled BSDF",
            "to_socket": "Roughness",
        }))

        # Phase 8 — lights + cameras
        check("add_light", send({
            "tool": "add_light",
            "type": "AREA",
            "name": "TestAreaLight",
            "location": [3.0, -3.0, 4.0],
            "energy": 400.0,
            "color": [1.0, 0.95, 0.9],
        }))
        check("set_light_properties", send({
            "tool": "set_light_properties",
            "name": "TestAreaLight",
            "energy": 500.0,
            "size": 2.0,
            "temperature_kelvin": 5600,
        }))
        check("add_camera", send({
            "tool": "add_camera",
            "name": "TestCamera",
            "location": [6.0, -6.0, 4.0],
            "target": "TestSphere",
            "lens_mm": 85.0,
        }))
        check("set_active_camera", send({"tool": "set_active_camera", "name": "TestCamera"}))
        check("set_camera_properties", send({
            "tool": "set_camera_properties",
            "name": "TestCamera",
            "lens_mm": 100.0,
            "dof_distance": 8.0,
            "fstop": 2.8,
        }))
        check("setup_three_point", send({
            "tool": "setup_three_point_lighting",
            "subject_name": "TestSphere",
            "distance": 4.0,
            "energy": 600.0,
        }))
        check("setup_product_studio", send({
            "tool": "setup_product_studio",
            "subject_name": "TestSphere",
            "style": "SOFTBOX",
            "distance": 3.5,
        }))

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
