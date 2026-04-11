"""JustThreed — control Blender from any MCP-capable AI client.

This module is loaded by Blender when the extension is enabled. It runs a
small TCP server on localhost:9876 that accepts JSON commands from the
JustThreed MCP server (a separate Python process launched by the AI client).

IMPORTANT: bpy is not thread-safe. The socket server runs on a background
thread, but every bpy call happens inside _process_jobs(), which is registered
with bpy.app.timers and therefore always runs on Blender's main thread.
"""
from __future__ import annotations

import base64
import json
import os
import queue
import socket
import tempfile
import threading
import traceback

import bpy

HOST = "localhost"
PORT = 9876

# Each job: (command_dict, result_dict, done_event)
# The socket-handler thread enqueues a job and waits on the event.
# The main-thread timer drains jobs, fills the result dict, and sets the event.
_job_queue: "queue.Queue[tuple[dict, dict, threading.Event]]" = queue.Queue()

_server_socket: socket.socket | None = None
_server_thread: threading.Thread | None = None
_running = False


# ---------- Main-thread dispatcher ----------

_PRIMITIVE_OPS = {
    "CUBE": "primitive_cube_add",
    "SPHERE": "primitive_uv_sphere_add",
    "ICOSPHERE": "primitive_ico_sphere_add",
    "CYLINDER": "primitive_cylinder_add",
    "CONE": "primitive_cone_add",
    "TORUS": "primitive_torus_add",
    "PLANE": "primitive_plane_add",
    "MONKEY": "primitive_monkey_add",
}


def _object_summary(obj) -> dict:
    info = {
        "name": obj.name,
        "type": obj.type,
        "location": [round(v, 6) for v in obj.location],
        "dimensions": [round(v, 6) for v in obj.dimensions],
        "visible": obj.visible_get(),
    }
    if obj.type == "MESH" and obj.data is not None:
        info["polycount"] = len(obj.data.polygons)
    return info


def _dispatch(cmd: dict) -> dict:
    tool = cmd.get("tool")

    if tool == "ping":
        return {"ok": True, "message": "pong"}

    if tool == "create_cube":
        bpy.ops.mesh.primitive_cube_add()
        obj = bpy.context.active_object
        return {"ok": True, "message": f"Created {obj.name}" if obj else "Cube created"}

    if tool == "get_scene_info":
        scene = bpy.context.scene
        objects = [_object_summary(o) for o in scene.objects]
        return {
            "ok": True,
            "scene": scene.name,
            "frame_current": scene.frame_current,
            "object_count": len(objects),
            "objects": objects,
        }

    if tool == "get_object":
        name = cmd.get("name")
        if not name:
            return {"ok": False, "error": "'name' is required"}
        obj = bpy.data.objects.get(name)
        if obj is None:
            return {"ok": False, "error": f"No object named {name!r}"}
        info = {
            "name": obj.name,
            "type": obj.type,
            "location": [round(v, 6) for v in obj.location],
            "rotation_euler": [round(v, 6) for v in obj.rotation_euler],
            "scale": [round(v, 6) for v in obj.scale],
            "dimensions": [round(v, 6) for v in obj.dimensions],
            "visible": obj.visible_get(),
            "parent": obj.parent.name if obj.parent else None,
            "collections": [c.name for c in obj.users_collection],
            "materials": [ms.material.name for ms in obj.material_slots if ms.material],
            "modifiers": [{"name": m.name, "type": m.type} for m in getattr(obj, "modifiers", [])],
        }
        if obj.type == "MESH" and obj.data is not None:
            mesh = obj.data
            info["mesh"] = {
                "vertices": len(mesh.vertices),
                "edges": len(mesh.edges),
                "polygons": len(mesh.polygons),
                "uv_layers": [layer.name for layer in mesh.uv_layers],
            }
        return {"ok": True, "object": info}

    if tool == "create_primitive":
        ptype = (cmd.get("type") or "CUBE").upper()
        op_name = _PRIMITIVE_OPS.get(ptype)
        if op_name is None:
            return {
                "ok": False,
                "error": f"Unknown primitive type {ptype!r}. Valid: {sorted(_PRIMITIVE_OPS)}",
            }
        location = tuple(cmd.get("location") or (0.0, 0.0, 0.0))
        rotation = tuple(cmd.get("rotation") or (0.0, 0.0, 0.0))
        scale = tuple(cmd.get("scale") or (1.0, 1.0, 1.0))
        op = getattr(bpy.ops.mesh, op_name)
        op(location=location)
        obj = bpy.context.active_object
        if obj is None:
            return {"ok": False, "error": "Primitive was created but no active object"}
        obj.rotation_euler = rotation
        obj.scale = scale
        desired = cmd.get("name")
        if desired:
            obj.name = desired
        return {"ok": True, "name": obj.name, "type": obj.type}

    if tool == "delete_object":
        name = cmd.get("name")
        if not name:
            return {"ok": False, "error": "'name' is required"}
        obj = bpy.data.objects.get(name)
        if obj is None:
            return {"ok": False, "error": f"No object named {name!r}"}
        bpy.data.objects.remove(obj, do_unlink=True)
        return {"ok": True, "message": f"Deleted {name}"}

    if tool == "render_image":
        output_path = cmd.get("output_path")
        if not output_path:
            return {"ok": False, "error": "'output_path' is required"}
        scene = bpy.context.scene
        abs_path = os.path.abspath(os.path.expanduser(output_path))
        os.makedirs(os.path.dirname(abs_path) or ".", exist_ok=True)
        orig_path = scene.render.filepath
        orig_format = scene.render.image_settings.file_format
        try:
            ext = os.path.splitext(abs_path)[1].lower()
            scene.render.image_settings.file_format = {
                ".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG",
            }.get(ext, "PNG")
            scene.render.filepath = abs_path
            bpy.ops.render.render(write_still=True)
        finally:
            scene.render.filepath = orig_path
            scene.render.image_settings.file_format = orig_format
        return {
            "ok": True,
            "path": abs_path,
            "width": scene.render.resolution_x,
            "height": scene.render.resolution_y,
        }

    if tool == "render_and_show":
        resolution = int(cmd.get("resolution") or 512)
        scene = bpy.context.scene
        orig_x = scene.render.resolution_x
        orig_y = scene.render.resolution_y
        orig_pct = scene.render.resolution_percentage
        orig_path = scene.render.filepath
        orig_format = scene.render.image_settings.file_format
        tmp_path = None
        try:
            aspect = (orig_x / orig_y) if orig_y else 1.0
            if aspect >= 1.0:
                scene.render.resolution_x = resolution
                scene.render.resolution_y = max(1, int(round(resolution / aspect)))
            else:
                scene.render.resolution_y = resolution
                scene.render.resolution_x = max(1, int(round(resolution * aspect)))
            scene.render.resolution_percentage = 100
            scene.render.image_settings.file_format = "PNG"

            fd, tmp_path = tempfile.mkstemp(prefix="justthreed_render_", suffix=".png")
            os.close(fd)
            scene.render.filepath = tmp_path

            bpy.ops.render.render(write_still=True)

            with open(tmp_path, "rb") as f:
                data = f.read()
            return {
                "ok": True,
                "format": "png",
                "width": scene.render.resolution_x,
                "height": scene.render.resolution_y,
                "data_base64": base64.b64encode(data).decode("ascii"),
            }
        finally:
            scene.render.resolution_x = orig_x
            scene.render.resolution_y = orig_y
            scene.render.resolution_percentage = orig_pct
            scene.render.filepath = orig_path
            scene.render.image_settings.file_format = orig_format
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    return {"ok": False, "error": f"Unknown tool: {tool!r}"}


def _process_jobs():
    """Runs on Blender's main thread via bpy.app.timers — safe to touch bpy."""
    while True:
        try:
            cmd, result, done = _job_queue.get_nowait()
        except queue.Empty:
            break
        try:
            result.update(_dispatch(cmd))
        except Exception as exc:
            traceback.print_exc()
            result.update({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
        finally:
            done.set()
    return 0.1 if _running else None


# ---------- Socket server (background thread) ----------

def _read_line(conn: socket.socket, max_bytes: int = 1_000_000) -> bytes:
    buf = b""
    while b"\n" not in buf:
        chunk = conn.recv(4096)
        if not chunk:
            break
        buf += chunk
        if len(buf) > max_bytes:
            raise ValueError("Request too large")
    line, _, _ = buf.partition(b"\n")
    return line


def _handle_connection(conn: socket.socket) -> None:
    try:
        conn.settimeout(15.0)
        line = _read_line(conn)
        if not line:
            return
        cmd = json.loads(line.decode("utf-8"))

        wait_timeout = float(cmd.get("_timeout", 15.0))
        conn.settimeout(max(wait_timeout + 5.0, 15.0))

        result: dict = {}
        done = threading.Event()
        _job_queue.put((cmd, result, done))
        if not done.wait(timeout=wait_timeout):
            result = {"ok": False, "error": "Timed out waiting for Blender main thread"}

        conn.sendall((json.dumps(result) + "\n").encode("utf-8"))
    except Exception as exc:
        err = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        try:
            conn.sendall((json.dumps(err) + "\n").encode("utf-8"))
        except OSError:
            pass
    finally:
        try:
            conn.close()
        except OSError:
            pass


def _server_loop() -> None:
    global _server_socket
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.settimeout(1.0)
    try:
        srv.bind((HOST, PORT))
    except OSError as exc:
        print(f"[JustThreed] Failed to bind {HOST}:{PORT}: {exc}")
        return
    srv.listen(5)
    _server_socket = srv
    print(f"[JustThreed] Listening on {HOST}:{PORT}")

    while _running:
        try:
            conn, _addr = srv.accept()
        except socket.timeout:
            continue
        except OSError:
            break
        threading.Thread(target=_handle_connection, args=(conn,), daemon=True).start()

    print("[JustThreed] Server stopped")
    try:
        srv.close()
    except OSError:
        pass
    _server_socket = None


def _start_server() -> bool:
    global _server_thread, _running
    if _running:
        return False
    _running = True
    _server_thread = threading.Thread(target=_server_loop, daemon=True)
    _server_thread.start()
    if not bpy.app.timers.is_registered(_process_jobs):
        bpy.app.timers.register(_process_jobs)
    return True


def _stop_server() -> bool:
    global _running
    if not _running:
        return False
    _running = False
    if _server_socket is not None:
        try:
            _server_socket.close()
        except OSError:
            pass
    if bpy.app.timers.is_registered(_process_jobs):
        try:
            bpy.app.timers.unregister(_process_jobs)
        except ValueError:
            pass
    return True


# ---------- Operators ----------

class JUSTTHREED_OT_hello(bpy.types.Operator):
    """Verify the JustThreed extension is installed and working"""
    bl_idname = "justthreed.hello"
    bl_label = "Say Hello"

    def execute(self, context):
        self.report({'INFO'}, "Hello from JustThreed!")
        return {'FINISHED'}


class JUSTTHREED_OT_start_server(bpy.types.Operator):
    """Start the JustThreed MCP socket server on localhost:9876"""
    bl_idname = "justthreed.start_server"
    bl_label = "Start MCP Server"

    def execute(self, context):
        if _start_server():
            self.report({'INFO'}, f"JustThreed MCP Server started on port {PORT}")
        else:
            self.report({'WARNING'}, "Server is already running")
        return {'FINISHED'}


class JUSTTHREED_OT_stop_server(bpy.types.Operator):
    """Stop the JustThreed MCP socket server"""
    bl_idname = "justthreed.stop_server"
    bl_label = "Stop MCP Server"

    def execute(self, context):
        if _stop_server():
            self.report({'INFO'}, "JustThreed MCP Server stopped")
        else:
            self.report({'WARNING'}, "Server is not running")
        return {'FINISHED'}


# ---------- Panel ----------

class JUSTTHREED_PT_panel(bpy.types.Panel):
    bl_label = "JustThreed"
    bl_idname = "JUSTTHREED_PT_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'JustThreed'

    def draw(self, context):
        layout = self.layout
        col = layout.column(align=True)
        col.label(text="Control Blender via AI")
        col.separator()
        if _running:
            col.operator("justthreed.stop_server", icon='PAUSE')
            col.label(text=f"Listening on :{PORT}", icon='CHECKMARK')
        else:
            col.operator("justthreed.start_server", icon='PLAY')
            col.label(text="Server stopped", icon='X')
        col.separator()
        col.operator("justthreed.hello", icon='QUESTION')


# ---------- Registration ----------

classes = (
    JUSTTHREED_OT_hello,
    JUSTTHREED_OT_start_server,
    JUSTTHREED_OT_stop_server,
    JUSTTHREED_PT_panel,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    _stop_server()
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
