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
def set_transform(
    name: str,
    location: list[float] | None = None,
    rotation: list[float] | None = None,
    scale: list[float] | None = None,
) -> dict:
    """Set location, rotation (euler radians), and/or scale on an object.
    Any argument left as None is not changed. All values are absolute, not deltas."""
    return _send({
        "tool": "set_transform",
        "name": name,
        "location": location,
        "rotation": rotation,
        "scale": scale,
    })


@mcp.tool()
def move_object(name: str, delta: list[float]) -> dict:
    """Translate an object by a delta vector (additive, world-space)."""
    return _send({"tool": "move_object", "name": name, "delta": delta})


@mcp.tool()
def rotate_object(name: str, axis: str, radians: float) -> dict:
    """Rotate an object around X, Y, or Z by the given radians (additive)."""
    return _send({"tool": "rotate_object", "name": name, "axis": axis, "radians": radians})


@mcp.tool()
def scale_object(name: str, factor: float | list[float]) -> dict:
    """Multiply an object's scale by a uniform number or a 3-element list (multiplicative)."""
    return _send({"tool": "scale_object", "name": name, "factor": factor})


@mcp.tool()
def set_origin(name: str, to: str = "GEOMETRY") -> dict:
    """Move an object's origin. `to` is one of GEOMETRY (median point), BOUNDS
    (bounding-box center), CURSOR (3D cursor), VOLUME (center of volume), MASS."""
    return _send({"tool": "set_origin", "name": name, "to": to})


@mcp.tool()
def parent_to(child: str, parent: str | None) -> dict:
    """Parent `child` to `parent`, preserving the child's world transform.
    Pass parent=None to clear parenting."""
    return _send({"tool": "parent_to", "child": child, "parent": parent})


@mcp.tool()
def join_objects(names: list[str]) -> dict:
    """Join multiple same-type objects (typically meshes) into one. The first
    name in the list is the target — the other objects are merged into it and
    removed. All objects must share the same type."""
    return _send({"tool": "join_objects", "names": names})


@mcp.tool()
def add_modifier(
    name: str,
    type: str,
    params: dict | None = None,
    mod_name: str | None = None,
) -> dict:
    """Add a non-destructive modifier to an object. Common `type` values:
    SUBSURF, BEVEL, BOOLEAN, MIRROR, ARRAY, SOLIDIFY, DECIMATE, DISPLACE,
    SCREW, SHRINKWRAP, SIMPLE_DEFORM, WELD, REMESH, TRIANGULATE.
    `params` is a dict of modifier attributes — for example
    `{"levels": 2, "render_levels": 3}` for SUBSURF, or
    `{"operation": "DIFFERENCE", "object": "Cutter"}` for BOOLEAN (object-ref
    params accept the string name of another object). `mod_name` optionally
    overrides the generated modifier name."""
    return _send({
        "tool": "add_modifier",
        "name": name,
        "type": type,
        "params": params or {},
        "mod_name": mod_name,
    })


@mcp.tool()
def apply_modifier(name: str, modifier_name: str) -> dict:
    """Permanently apply a modifier, baking its result into the mesh and
    removing it from the stack. Destructive — undoable in Blender itself."""
    return _send({
        "tool": "apply_modifier",
        "name": name,
        "modifier_name": modifier_name,
    })


@mcp.tool()
def remove_modifier(name: str, modifier_name: str) -> dict:
    """Remove a modifier from an object's stack without applying it."""
    return _send({
        "tool": "remove_modifier",
        "name": name,
        "modifier_name": modifier_name,
    })


@mcp.tool()
def set_modifier_param(name: str, modifier_name: str, params: dict) -> dict:
    """Update one or more parameters on an existing modifier. Same param rules
    as add_modifier (object-ref params accept string object names)."""
    return _send({
        "tool": "set_modifier_param",
        "name": name,
        "modifier_name": modifier_name,
        "params": params,
    })


@mcp.tool()
def reorder_modifier(name: str, modifier_name: str, index: int) -> dict:
    """Move a modifier to a specific position in the stack. Lower index = earlier
    in the evaluation order. Use get_object to inspect the current stack."""
    return _send({
        "tool": "reorder_modifier",
        "name": name,
        "modifier_name": modifier_name,
        "index": index,
    })


@mcp.tool()
def render_image(output_path: str) -> dict:
    """Render the current scene through the active camera to a file on disk.
    `output_path` should end in .png or .jpg. Uses the scene's current resolution
    and render engine. Blocks until the render finishes (up to 5 minutes).

    IMPORTANT — do NOT call this automatically as a "final step" after finishing
    a modeling, materials, lighting, or scene-setup task. Only call it when the
    user explicitly asks to render or export an image."""
    return _send({"tool": "render_image", "output_path": output_path}, timeout=300.0)


@mcp.tool()
def render_and_show(resolution: int = 512) -> Image:
    """Render the current scene at a low preview resolution and return the PNG
    so you can actually see the result. Use this for vision-in-the-loop iteration:
    make a change, render, look at it, decide what to fix, repeat. `resolution` is
    the longest edge in pixels (default 512) — keep it small for fast feedback.

    IMPORTANT — do NOT call this automatically as a "final step" after finishing
    a modeling, materials, lighting, or scene-setup task. Only call it when the
    user explicitly asks to render, preview, or see the result. Rendering is
    expensive and the user wants to control when it happens. Treat task
    completion as the last step unless a render was requested."""
    response = _send({"tool": "render_and_show", "resolution": resolution}, timeout=300.0)
    data = base64.b64decode(response["data_base64"])
    return Image(data=data, format="png")


# ---------- Phase 7 — Materials + shader node graph ----------


@mcp.tool()
def create_material(
    name: str,
    base_color: list[float] | None = None,
    roughness: float = 0.5,
    metallic: float = 0.0,
) -> dict:
    """Create a new PBR material with a Principled BSDF. `base_color` is an RGB
    or RGBA list in linear 0–1 space. Use this for quick opaque surfaces —
    paint, plastic, metal. For glass use create_glass_material. Returns the
    new material's summary."""
    return _send({
        "tool": "create_material",
        "name": name,
        "base_color": base_color,
        "roughness": roughness,
        "metallic": metallic,
    })


@mcp.tool()
def create_pbr_material(
    name: str,
    base_color: list[float] | None = None,
    roughness: float = 0.5,
    metallic: float = 0.0,
    emission: list[float] | None = None,
    emission_strength: float = 0.0,
) -> dict:
    """Create a PBR material with an emission layer on top of the base PBR
    slot. For normal/ao/roughness/base-color *image textures*, call
    load_image_texture + set_material_texture after this."""
    return _send({
        "tool": "create_pbr_material",
        "name": name,
        "base_color": base_color,
        "roughness": roughness,
        "metallic": metallic,
        "emission": emission,
        "emission_strength": emission_strength,
    })


@mcp.tool()
def create_glass_material(
    name: str,
    color: list[float] | None = None,
    ior: float = 1.45,
    roughness: float = 0.0,
    transmission: float = 1.0,
) -> dict:
    """Create a glass material using Principled BSDF transmission. Typical IOR:
    window glass 1.45, water 1.33, diamond 2.42. `roughness` 0.0 for clear,
    higher for frosted."""
    return _send({
        "tool": "create_glass_material",
        "name": name,
        "color": color,
        "ior": ior,
        "roughness": roughness,
        "transmission": transmission,
    })


@mcp.tool()
def assign_material(object_name: str, material_name: str, slot_index: int = 0) -> dict:
    """Assign a material to an object's material slot. Creates slots up to
    `slot_index` if they don't yet exist. Returns the updated slot layout."""
    return _send({
        "tool": "assign_material",
        "object_name": object_name,
        "material_name": material_name,
        "slot_index": slot_index,
    })


@mcp.tool()
def assign_material_to_faces(
    object_name: str,
    material_name: str,
    face_indices: list[int],
) -> dict:
    """Assign a material to specific polygon faces of a mesh. Used for multi-
    material objects (e.g. a label on a bottle). Appends the material as a new
    slot if it isn't already attached. `face_indices` are zero-based polygon
    indices — use get_object to inspect the polygon count."""
    return _send({
        "tool": "assign_material_to_faces",
        "object_name": object_name,
        "material_name": material_name,
        "face_indices": face_indices,
    })


@mcp.tool()
def list_materials() -> dict:
    """List every material in the current blend file with name, user count, and
    node count. Use this to see what's already defined before creating duplicates."""
    return _send({"tool": "list_materials"})


@mcp.tool()
def duplicate_material(name: str, new_name: str) -> dict:
    """Deep-copy an existing material under a new name, including its node
    graph. Use when you want a variant (e.g. weathered vs clean paint)."""
    return _send({
        "tool": "duplicate_material",
        "name": name,
        "new_name": new_name,
    })


@mcp.tool()
def set_world_hdri(
    path: str,
    strength: float = 1.0,
    rotation: float | list[float] = 0.0,
) -> dict:
    """Set the world environment to an HDRI image from a local file path.
    `path` must be a .hdr or .exr file on disk — no URLs. `strength` scales
    brightness. `rotation` is either a Z-axis rotation in radians, or a
    3-element euler. Replaces the entire world node graph."""
    return _send(
        {
            "tool": "set_world_hdri",
            "path": path,
            "strength": strength,
            "rotation": rotation,
        },
        timeout=60.0,
    )


@mcp.tool()
def load_image_texture(name: str, path: str) -> dict:
    """Load an image from disk into Blender's image datablock so it can be used
    as a texture. `path` must be a local file. `name` is optional — if given,
    the loaded image is renamed. Use the returned name with set_material_texture."""
    return _send(
        {"tool": "load_image_texture", "name": name, "path": path},
        timeout=60.0,
    )


@mcp.tool()
def set_material_texture(
    material_name: str,
    socket: str,
    texture_name: str,
    uv_map: str | None = None,
) -> dict:
    """Wire a loaded image texture into a material's Principled BSDF slot.
    `socket` is one of BASE_COLOR, ROUGHNESS, METALLIC, EMISSION, ALPHA, NORMAL.
    NORMAL automatically inserts a Normal Map node. If `uv_map` is given, a
    UVMap node is inserted so the texture uses the named UV layer. Call
    load_image_texture first so `texture_name` resolves."""
    return _send({
        "tool": "set_material_texture",
        "material_name": material_name,
        "socket": socket,
        "texture_name": texture_name,
        "uv_map": uv_map,
    })


@mcp.tool()
def add_shader_node(
    material_name: str,
    node_type: str,
    location: list[float] | None = None,
    name: str | None = None,
) -> dict:
    """Add a shader node to a material's node graph. Common `node_type` values:
    BSDF_PRINCIPLED, BSDF_GLASS, BSDF_TRANSPARENT, BSDF_DIFFUSE, EMISSION,
    MIX_SHADER, ADD_SHADER, TEX_IMAGE, TEX_NOISE, TEX_VORONOI, TEX_WAVE,
    TEX_CHECKER, TEX_GRADIENT, TEX_BRICK, TEX_ENVIRONMENT, MAPPING,
    TEXTURE_COORD, COLOR_RAMP, MATH, VECTOR_MATH, HUE_SAT, MIX_RGB, MIX, BUMP,
    NORMAL_MAP, RGB, VALUE, INVERT, GAMMA, BRIGHT_CONTRAST, OUTPUT_MATERIAL.
    `location` is [x, y] in the shader editor. If `name` is given, the node is
    renamed so you can reference it from connect_shader_nodes and
    set_shader_node_param. Returns node metadata including available socket names."""
    return _send({
        "tool": "add_shader_node",
        "material_name": material_name,
        "node_type": node_type,
        "location": location,
        "name": name,
    })


@mcp.tool()
def connect_shader_nodes(
    material_name: str,
    from_node: str,
    from_socket: str,
    to_node: str,
    to_socket: str,
) -> dict:
    """Link one shader node's output socket to another's input socket, by node
    and socket *name*. Socket names come from add_shader_node's return payload."""
    return _send({
        "tool": "connect_shader_nodes",
        "material_name": material_name,
        "from_node": from_node,
        "from_socket": from_socket,
        "to_node": to_node,
        "to_socket": to_socket,
    })


@mcp.tool()
def disconnect_shader_nodes(
    material_name: str,
    from_node: str,
    from_socket: str,
    to_node: str,
    to_socket: str,
) -> dict:
    """Remove a specific link between two shader nodes. Matches on node and
    socket name — any link with the given from/to ends is removed."""
    return _send({
        "tool": "disconnect_shader_nodes",
        "material_name": material_name,
        "from_node": from_node,
        "from_socket": from_socket,
        "to_node": to_node,
        "to_socket": to_socket,
    })


@mcp.tool()
def set_shader_node_param(
    material_name: str,
    node: str,
    params: dict,
) -> dict:
    """Update parameters on an existing shader node. `params` is a dict where
    keys are either input socket names (setting the default_value) or node
    attributes (e.g. `operation` on MATH, `blend_type` on MIX_RGB,
    `interpolation` on TEX_IMAGE). Color sockets accept 3- or 4-element lists."""
    return _send({
        "tool": "set_shader_node_param",
        "material_name": material_name,
        "node": node,
        "params": params,
    })


# ---------- Phase 8 — Lights, cameras, studio presets ----------


@mcp.tool()
def add_light(
    type: str = "AREA",
    name: str | None = None,
    location: list[float] | None = None,
    energy: float | None = None,
    color: list[float] | None = None,
) -> dict:
    """Create a new light in the scene. `type` is SUN (directional),
    AREA (softbox), POINT (omni), or SPOT (cone). `energy` is in watts for
    POINT/SPOT/AREA or W/m² for SUN — typical values: 10–100 for interior
    POINT, 500–2000 for AREA softbox, 3–7 for SUN. `color` is RGB 0–1."""
    return _send({
        "tool": "add_light",
        "type": type,
        "name": name,
        "location": location,
        "energy": energy,
        "color": color,
    })


@mcp.tool()
def set_light_properties(
    name: str,
    energy: float | None = None,
    color: list[float] | None = None,
    size: float | list[float] | None = None,
    temperature_kelvin: float | None = None,
) -> dict:
    """Update an existing light. `temperature_kelvin` converts a black-body
    color temperature to RGB (e.g. 3200 warm, 5600 daylight, 10000 cool) and
    overrides `color` if both are given. `size` controls AREA light size
    (number or [w, h] for rectangle), SUN disc angle (radians), or POINT/SPOT
    soft-shadow radius."""
    return _send({
        "tool": "set_light_properties",
        "name": name,
        "energy": energy,
        "color": color,
        "size": size,
        "temperature_kelvin": temperature_kelvin,
    })


@mcp.tool()
def add_camera(
    name: str | None = None,
    location: list[float] | None = None,
    target: str | list[float] | None = None,
    lens_mm: float | None = None,
) -> dict:
    """Create a new camera. If `target` is given, the camera is rotated so its
    -Z axis points at the target — either an object name or a 3-element world-
    space point. `lens_mm` sets focal length (35 wide, 50 normal, 85 portrait,
    100+ tight product macro). Call set_active_camera to render through it."""
    return _send({
        "tool": "add_camera",
        "name": name,
        "location": location,
        "target": target,
        "lens_mm": lens_mm,
    })


@mcp.tool()
def set_active_camera(name: str) -> dict:
    """Set which camera the scene renders through. Required before render_image
    / render_and_show pick up a newly-created camera."""
    return _send({"tool": "set_active_camera", "name": name})


@mcp.tool()
def set_camera_properties(
    name: str,
    lens_mm: float | None = None,
    dof_distance: float | None = None,
    fstop: float | None = None,
) -> dict:
    """Tune an existing camera's focal length and depth of field. Setting
    `dof_distance` or `fstop` enables DoF automatically. Lower f-stop = shallower
    focus (f/2.0 macro, f/8 product shot, f/16 deep focus)."""
    return _send({
        "tool": "set_camera_properties",
        "name": name,
        "lens_mm": lens_mm,
        "dof_distance": dof_distance,
        "fstop": fstop,
    })


@mcp.tool()
def setup_three_point_lighting(
    subject_name: str,
    distance: float = 5.0,
    energy: float = 500.0,
) -> dict:
    """Build a classic three-point lighting rig (key, fill, rim) around an
    object, all aimed at its bounding-box center. Returns the three new light
    names. `distance` is the base radius in meters; `energy` is the key
    light's base energy — fill and rim are scaled off it."""
    return _send({
        "tool": "setup_three_point_lighting",
        "subject_name": subject_name,
        "distance": distance,
        "energy": energy,
    })


@mcp.tool()
def setup_product_studio(
    subject_name: str,
    style: str = "SOFTBOX",
    distance: float = 4.0,
) -> dict:
    """Drop a product-photography lighting preset around an object. `style` is
    SOFTBOX (three soft area lights), HARD_LIGHT (directional spot + fill),
    HIGH_KEY (bright, shadow-less), or LOW_KEY (dark with a rim accent).
    `distance` scales the entire rig."""
    return _send({
        "tool": "setup_product_studio",
        "subject_name": subject_name,
        "style": style,
        "distance": distance,
    })


# ---------- Phase 12 — edit-mode / hard-surface modeling ----------


@mcp.tool()
def enter_edit_mode(name: str) -> dict:
    """Enter Edit Mode on a mesh object. Most modeling tools below don't
    actually require edit mode (they work on mesh data directly), but use this
    if you want to inspect selections interactively in Blender."""
    return _send({"tool": "enter_edit_mode", "name": name})


@mcp.tool()
def exit_edit_mode() -> dict:
    """Exit Edit Mode on whichever object is currently being edited."""
    return _send({"tool": "exit_edit_mode"})


@mcp.tool()
def set_select_mode(mode: str) -> dict:
    """Set the edit-mode selection mode. `mode` is VERT, EDGE, or FACE. An
    object must already be in edit mode."""
    return _send({"tool": "set_select_mode", "mode": mode})


@mcp.tool()
def select_all(name: str) -> dict:
    """Enter edit mode on a mesh object and select all geometry."""
    return _send({"tool": "select_all", "name": name})


@mcp.tool()
def deselect_all() -> dict:
    """Deselect all geometry on the object currently in edit mode."""
    return _send({"tool": "deselect_all"})


@mcp.tool()
def extrude_faces(
    name: str,
    face_indices: list[int],
    vector: list[float],
) -> dict:
    """Extrude a set of mesh faces and translate the new geometry by `vector`
    (world space, 3 floats). Works without entering edit mode."""
    return _send({
        "tool": "extrude_faces",
        "name": name,
        "face_indices": face_indices,
        "vector": vector,
    })


@mcp.tool()
def extrude_edges(
    name: str,
    edge_indices: list[int],
    vector: list[float],
) -> dict:
    """Extrude a set of mesh edges as new edges and translate them by `vector`."""
    return _send({
        "tool": "extrude_edges",
        "name": name,
        "edge_indices": edge_indices,
        "vector": vector,
    })


@mcp.tool()
def extrude_vertices(
    name: str,
    vertex_indices: list[int],
    vector: list[float],
) -> dict:
    """Extrude individual vertices as new edges to the translated positions."""
    return _send({
        "tool": "extrude_vertices",
        "name": name,
        "vertex_indices": vertex_indices,
        "vector": vector,
    })


@mcp.tool()
def inset_faces(
    name: str,
    face_indices: list[int],
    thickness: float = 0.05,
    depth: float = 0.0,
    individual: bool = False,
) -> dict:
    """Inset a set of faces. `thickness` is the inset amount, `depth` pushes the
    inset geometry along the normal. Set `individual=True` to inset each face
    separately (vs. as a shared region)."""
    return _send({
        "tool": "inset_faces",
        "name": name,
        "face_indices": face_indices,
        "thickness": thickness,
        "depth": depth,
        "individual": individual,
    })


@mcp.tool()
def bevel_edges(
    name: str,
    edge_indices: list[int],
    width: float = 0.05,
    segments: int = 1,
    profile: float = 0.5,
) -> dict:
    """Bevel a set of edges. `width` is the offset distance, `segments` is the
    subdivision count (more = rounder), `profile` is 0–1 (0.5 = circular)."""
    return _send({
        "tool": "bevel_edges",
        "name": name,
        "edge_indices": edge_indices,
        "width": width,
        "segments": segments,
        "profile": profile,
    })


@mcp.tool()
def bevel_vertices(
    name: str,
    vertex_indices: list[int],
    width: float = 0.05,
    segments: int = 1,
) -> dict:
    """Bevel a set of vertices (rounds each vertex corner)."""
    return _send({
        "tool": "bevel_vertices",
        "name": name,
        "vertex_indices": vertex_indices,
        "width": width,
        "segments": segments,
    })


@mcp.tool()
def subdivide(
    name: str,
    edge_indices: list[int] | None = None,
    cuts: int = 1,
    smoothness: float = 0.0,
) -> dict:
    """Subdivide edges on a mesh. Pass `edge_indices` to subdivide only those
    edges, or omit to subdivide every edge. `smoothness` (0–1) applies a
    smoothing offset like Blender's default subdivide slider."""
    return _send({
        "tool": "subdivide",
        "name": name,
        "edge_indices": edge_indices,
        "cuts": cuts,
        "smoothness": smoothness,
    })


@mcp.tool()
def bridge_edge_loops(name: str, edge_indices: list[int]) -> dict:
    """Bridge two or more open edge loops with new faces. `edge_indices` must
    list edges from *every* loop you want to bridge."""
    return _send({
        "tool": "bridge_edge_loops",
        "name": name,
        "edge_indices": edge_indices,
    })


@mcp.tool()
def merge_vertices(
    name: str,
    mode: str = "DISTANCE",
    vertex_indices: list[int] | None = None,
    distance: float = 0.0001,
) -> dict:
    """Merge vertices on a mesh. `mode` is DISTANCE (merges within `distance`),
    CENTER (collapses to their average), FIRST, or LAST (collapses to the first/
    last listed vertex). Omit `vertex_indices` to operate on the whole mesh
    (only useful with DISTANCE)."""
    return _send({
        "tool": "merge_vertices",
        "name": name,
        "mode": mode,
        "vertex_indices": vertex_indices,
        "distance": distance,
    })


@mcp.tool()
def dissolve(name: str, type: str, indices: list[int]) -> dict:
    """Dissolve a set of mesh elements, keeping the surrounding topology. `type`
    is VERTS, EDGES, or FACES."""
    return _send({
        "tool": "dissolve",
        "name": name,
        "type": type,
        "indices": indices,
    })


@mcp.tool()
def delete_elements(name: str, type: str, indices: list[int]) -> dict:
    """Delete mesh elements. `type` is VERTS, EDGES, FACES, ONLY_FACES
    (keep vertices/edges), FACES_KEEP_BOUNDARY, or EDGES_FACES."""
    return _send({
        "tool": "delete_elements",
        "name": name,
        "type": type,
        "indices": indices,
    })


@mcp.tool()
def recalculate_normals(name: str, inside: bool = False) -> dict:
    """Recalculate the face normals of a mesh. Set `inside=True` to flip
    them inward (rarely useful)."""
    return _send({
        "tool": "recalculate_normals",
        "name": name,
        "inside": inside,
    })


@mcp.tool()
def shade_smooth(name: str, angle_degrees: float | None = None) -> dict:
    """Mark all polygons as smooth-shaded. If `angle_degrees` is provided,
    additionally apply a Smooth-by-Angle modifier so edges sharper than that
    angle stay faceted (Blender 4.1+ only)."""
    return _send({
        "tool": "shade_smooth",
        "name": name,
        "angle_degrees": angle_degrees,
    })


@mcp.tool()
def shade_flat(name: str) -> dict:
    """Mark all polygons as flat-shaded."""
    return _send({"tool": "shade_flat", "name": name})


@mcp.tool()
def mark_sharp(
    name: str,
    edge_indices: list[int],
    clear: bool = False,
) -> dict:
    """Mark edges as sharp (for bevel / autosmooth workflows). Set `clear=True`
    to unmark them instead."""
    return _send({
        "tool": "mark_sharp",
        "name": name,
        "edge_indices": edge_indices,
        "clear": clear,
    })


@mcp.tool()
def mark_seam(
    name: str,
    edge_indices: list[int],
    clear: bool = False,
) -> dict:
    """Mark edges as UV seams (for unwrap workflows). Set `clear=True` to
    unmark them instead."""
    return _send({
        "tool": "mark_seam",
        "name": name,
        "edge_indices": edge_indices,
        "clear": clear,
    })


@mcp.tool()
def boolean_operation(
    target: str,
    cutter: str,
    op: str = "DIFFERENCE",
    solver: str = "EXACT",
) -> dict:
    """Perform a direct boolean between two mesh objects — adds a temporary
    Boolean modifier to `target` and applies it immediately. `op` is UNION,
    DIFFERENCE, or INTERSECT. `solver` is FAST or EXACT (EXACT is slower but
    handles overlapping geometry). The cutter object is left untouched."""
    return _send({
        "tool": "boolean_operation",
        "target": target,
        "cutter": cutter,
        "op": op,
        "solver": solver,
    })


# ---------- Phase 13 — UV editing & unwrapping ----------


@mcp.tool()
def list_uv_maps(name: str) -> dict:
    """List every UV map on a mesh object and which one is active."""
    return _send({"tool": "list_uv_maps", "name": name})


@mcp.tool()
def create_uv_map(name: str, map_name: str = "UVMap", active: bool = True) -> dict:
    """Create a new UV map on a mesh object. If `active=True` (default), the
    new layer becomes the active one that unwrap/pack ops target."""
    return _send({
        "tool": "create_uv_map",
        "name": name,
        "map_name": map_name,
        "active": active,
    })


@mcp.tool()
def set_active_uv_map(name: str, map_name: str) -> dict:
    """Set which UV map is active on a mesh object. Must already exist."""
    return _send({
        "tool": "set_active_uv_map",
        "name": name,
        "map_name": map_name,
    })


@mcp.tool()
def uv_unwrap(
    name: str,
    method: str = "UNWRAP",
    angle_limit: float = 66.0,
    island_margin: float = 0.001,
    correct_aspect: bool = True,
    scale_to_bounds: bool = False,
    cube_size: float = 2.0,
) -> dict:
    """Unwrap a mesh's UVs. `method` is one of:

    - UNWRAP / ANGLE_BASED (default) — seam-based, best general-purpose unwrap.
      Mark seams with `mark_seam` first for clean results.
    - CONFORMAL — less distortion on curved surfaces, worse on flat ones.
    - MINIMUM_STRETCH — Blender 4.3+ unwrap solver.
    - SMART_PROJECT — automatic projection based on face normals. Use
      `angle_limit` (degrees) to control how aggressively faces are grouped.
    - CUBE_PROJECTION — box projection. `cube_size` sets the projection extent.
    - CYLINDER_PROJECTION / SPHERE_PROJECTION — great for cylinders (bottles,
      canisters) and spheres. Unwraps around the current view direction.

    `island_margin` is the gap between UV islands (0–1), `correct_aspect`
    preserves texture aspect ratio, `scale_to_bounds` stretches the UVs to
    fill [0,1]. Creates a default UV map if the mesh has none."""
    return _send({
        "tool": "uv_unwrap",
        "name": name,
        "method": method,
        "angle_limit": angle_limit,
        "island_margin": island_margin,
        "correct_aspect": correct_aspect,
        "scale_to_bounds": scale_to_bounds,
        "cube_size": cube_size,
    })


@mcp.tool()
def pack_islands(name: str, margin: float = 0.001, rotate: bool = True) -> dict:
    """Pack UV islands into [0,1] UV space. `margin` is the gap between
    islands. `rotate=True` allows the packer to rotate islands for a tighter
    layout."""
    return _send({
        "tool": "pack_islands",
        "name": name,
        "margin": margin,
        "rotate": rotate,
    })


@mcp.tool()
def average_islands_scale(name: str) -> dict:
    """Rescale each UV island so every island has roughly the same texel
    density. Run this before `pack_islands` when you want uniform texture
    resolution across the whole mesh."""
    return _send({"tool": "average_islands_scale", "name": name})


@mcp.tool()
def get_uv_layout(
    name: str,
    uv_map: str | None = None,
    max_polygons: int = 5000,
) -> dict:
    """Return the UV coordinates for a mesh's active (or named) UV map.
    Each entry has the polygon index, the mesh-vertex indices for that face,
    and one UV per loop. Returned output is capped at `max_polygons` to
    avoid flooding the MCP channel — the `truncated` flag tells you when
    the cap was hit."""
    return _send({
        "tool": "get_uv_layout",
        "name": name,
        "uv_map": uv_map,
        "max_polygons": max_polygons,
    })


# ---------- Phase 14 — Texture painting + baking ----------


@mcp.tool()
def create_paint_texture(
    name: str,
    image_name: str | None = None,
    size: int = 1024,
    color: list[float] | None = None,
    alpha: bool = True,
) -> dict:
    """Create a new paintable image texture and wire it into an object's
    material as its Base Color. If the object has no material, a new
    Principled BSDF material is created. The image is flood-filled with
    `color` (default white). Returns the new image and material names."""
    return _send({
        "tool": "create_paint_texture",
        "name": name,
        "image_name": image_name,
        "size": size,
        "color": color or [1.0, 1.0, 1.0, 1.0],
        "alpha": alpha,
    })


@mcp.tool()
def fill_texture(image_name: str, color: list[float]) -> dict:
    """Flood-fill an existing Blender image with an RGBA color."""
    return _send({
        "tool": "fill_texture",
        "image_name": image_name,
        "color": color,
    })


@mcp.tool()
def save_paint_texture(image_name: str, path: str) -> dict:
    """Save a Blender image to disk. Format is picked from the file
    extension (.png / .jpg / .exr / .tga)."""
    return _send({
        "tool": "save_paint_texture",
        "image_name": image_name,
        "path": path,
    })


@mcp.tool()
def set_bake_settings(
    samples: int | None = None,
    margin: int | None = None,
    use_cage: bool | None = None,
    cage_extrusion: float | None = None,
    max_ray_distance: float | None = None,
) -> dict:
    """Tweak Cycles bake settings. `samples` is render samples per pixel
    (64 is a good default for normal maps, 256+ for AO). `margin` dilates
    the bake past UV island edges to avoid seams when mipmapping. `use_cage`
    + `cage_extrusion` control the low→high poly projection envelope."""
    return _send({
        "tool": "set_bake_settings",
        "samples": samples,
        "margin": margin,
        "use_cage": use_cage,
        "cage_extrusion": cage_extrusion,
        "max_ray_distance": max_ray_distance,
    })


@mcp.tool()
def bake_texture(
    name: str,
    bake_type: str = "COMBINED",
    resolution: int = 1024,
    image_name: str | None = None,
    output_path: str | None = None,
) -> dict:
    """Bake a texture map on a single object. `bake_type` is one of:
    COMBINED, DIFFUSE, GLOSSY, TRANSMISSION, EMIT, ENVIRONMENT, AO, SHADOW,
    POSITION, NORMAL, UV, ROUGHNESS. The bake target is a new (or existing)
    image texture plugged into the object's first material — the object must
    already have a UV map (call `uv_unwrap` first). The render engine is
    temporarily switched to Cycles for the bake. If `output_path` is given,
    the baked image is also saved to that file."""
    return _send(
        {
            "tool": "bake_texture",
            "name": name,
            "bake_type": bake_type,
            "resolution": resolution,
            "image_name": image_name,
            "output_path": output_path,
        },
        timeout=600.0,
    )


@mcp.tool()
def bake_from_selected(
    low_poly: str,
    high_poly: str,
    bake_type: str = "NORMAL",
    resolution: int = 1024,
    cage_extrusion: float = 0.05,
    image_name: str | None = None,
    output_path: str | None = None,
) -> dict:
    """Bake detail from a high-poly mesh onto a low-poly mesh's UV map. This
    is the game-asset pipeline — model a high-detail version, retopologize to
    a low-poly, then bake NORMAL / AO / POSITION / etc. from the former into
    the latter's texture. The low-poly is the bake target (must have a UV
    map); the high-poly is only sampled. `cage_extrusion` widens the
    projection envelope so rays reliably hit the high-poly surface."""
    return _send(
        {
            "tool": "bake_from_selected",
            "low_poly": low_poly,
            "high_poly": high_poly,
            "bake_type": bake_type,
            "resolution": resolution,
            "cage_extrusion": cage_extrusion,
            "image_name": image_name,
            "output_path": output_path,
        },
        timeout=900.0,
    )


# ---------- Phase 15 — Geometry nodes ----------


@mcp.tool()
def create_geometry_nodes_modifier(
    name: str,
    group_name: str | None = None,
    modifier_name: str = "GeometryNodes",
) -> dict:
    """Attach a new Geometry Nodes modifier to a mesh object, backed by a new
    node tree that has a Geometry → Geometry passthrough (one `Group Input`
    and one `Group Output` node, already linked). Use `add_geo_node` + the
    connect/set_param tools to build out the tree. Returns the created group
    name so subsequent calls can target it."""
    return _send({
        "tool": "create_geometry_nodes_modifier",
        "name": name,
        "group_name": group_name,
        "modifier_name": modifier_name,
    })


@mcp.tool()
def add_geo_node(
    group_name: str,
    node_type: str,
    location: list[float] | None = None,
    name: str | None = None,
) -> dict:
    """Add a node to a Geometry Nodes tree. `node_type` is one of the curated
    keys: GROUP_INPUT / GROUP_OUTPUT; mesh primitives (MESH_CUBE, MESH_UV_SPHERE,
    MESH_ICO_SPHERE, MESH_CYLINDER, MESH_CONE, MESH_CIRCLE, MESH_GRID,
    MESH_TORUS, MESH_LINE); curve primitives (CURVE_CIRCLE, CURVE_LINE,
    CURVE_SPIRAL, CURVE_BEZIER_SEGMENT); scatter/instancing
    (DISTRIBUTE_POINTS_ON_FACES, INSTANCE_ON_POINTS, MESH_TO_POINTS,
    POINTS_TO_VERTICES, REALIZE_INSTANCES, ROTATE_INSTANCES, SCALE_INSTANCES,
    TRANSLATE_INSTANCES); mesh↔curve (MESH_TO_CURVE, CURVE_TO_MESH); geometry
    ops (JOIN_GEOMETRY, TRANSFORM_GEOMETRY, SET_POSITION, SET_MATERIAL,
    DELETE_GEOMETRY, SEPARATE_GEOMETRY, BOUNDING_BOX, CONVEX_HULL,
    EXTRUDE_MESH, MESH_BOOLEAN, SUBDIVIDE_MESH, SUBDIVISION_SURFACE, DUAL_MESH,
    FLIP_FACES, MERGE_BY_DISTANCE); object/collection input (OBJECT_INFO,
    COLLECTION_INFO, SELF_OBJECT); attribute inputs (INPUT_POSITION,
    INPUT_NORMAL, INPUT_INDEX, INPUT_ID, INPUT_RADIUS, INPUT_SCENE_TIME,
    NAMED_ATTRIBUTE, STORE_NAMED_ATTRIBUTE); math/utility (MATH, VECTOR_MATH,
    COMBINE_XYZ, SEPARATE_XYZ, VALUE, COLOR_RAMP, COMPARE, BOOLEAN_MATH,
    RANDOM_VALUE, SWITCH, NOISE_TEXTURE, VORONOI_TEXTURE, GRADIENT_TEXTURE,
    MAP_RANGE, CLAMP). Returns the node's socket names so follow-up calls can
    pick the right ones to connect."""
    return _send({
        "tool": "add_geo_node",
        "group_name": group_name,
        "node_type": node_type,
        "location": location,
        "name": name,
    })


@mcp.tool()
def connect_geo_nodes(
    group_name: str,
    from_node: str,
    from_socket: str,
    to_node: str,
    to_socket: str,
) -> dict:
    """Connect two nodes in a Geometry Nodes tree. Socket names are the
    human-readable labels Blender shows in the node editor (e.g. `Geometry`,
    `Points`, `Value`)."""
    return _send({
        "tool": "connect_geo_nodes",
        "group_name": group_name,
        "from_node": from_node,
        "from_socket": from_socket,
        "to_node": to_node,
        "to_socket": to_socket,
    })


@mcp.tool()
def disconnect_geo_nodes(
    group_name: str,
    from_node: str,
    from_socket: str,
    to_node: str,
    to_socket: str,
) -> dict:
    """Remove a specific link from a Geometry Nodes tree."""
    return _send({
        "tool": "disconnect_geo_nodes",
        "group_name": group_name,
        "from_node": from_node,
        "from_socket": from_socket,
        "to_node": to_node,
        "to_socket": to_socket,
    })


@mcp.tool()
def set_geo_node_param(
    group_name: str,
    node: str,
    params: dict,
) -> dict:
    """Set default values on a Geometry Nodes node. Keys in `params` can be
    socket input names (sets the socket's `default_value`) or node attribute
    names (e.g. `operation` on MATH, `data_type` on RANDOM_VALUE,
    `distribute_method` on DISTRIBUTE_POINTS_ON_FACES). String values on
    Object-type input sockets are resolved to the matching `bpy.data.objects`
    reference server-side."""
    return _send({
        "tool": "set_geo_node_param",
        "group_name": group_name,
        "node": node,
        "params": params,
    })


@mcp.tool()
def list_geo_nodes(group_name: str) -> dict:
    """Inspect a Geometry Nodes tree: returns all nodes (with sockets) and
    all links. Useful when picking up an existing scatter / procedural group
    whose node names you don't remember."""
    return _send({"tool": "list_geo_nodes", "group_name": group_name})


@mcp.tool()
def scatter_objects(
    target: str,
    instance: str,
    density: float = 10.0,
    seed: int = 0,
    scale_min: float = 0.8,
    scale_max: float = 1.2,
    align_to_normal: bool = True,
    poisson: bool = True,
    group_name: str | None = None,
) -> dict:
    """High-level wrapper: attach a Geometry Nodes scatter modifier to
    `target` that distributes points over its surface (Poisson-disc by
    default) and instances `instance` at each point. Scale is randomized in
    the [`scale_min`, `scale_max`] range. If `align_to_normal` is True the
    instances orient to the target's surface normal. Use this for scattering
    rocks, bolts, grass, rivets — anything where you'd otherwise hand-wire a
    distribute-points-on-faces → instance-on-points node chain."""
    return _send({
        "tool": "scatter_objects",
        "target": target,
        "instance": instance,
        "density": density,
        "seed": seed,
        "scale_min": scale_min,
        "scale_max": scale_max,
        "align_to_normal": align_to_normal,
        "poisson": poisson,
        "group_name": group_name,
    })


@mcp.tool()
def apply_voxel_remesh(
    name: str,
    voxel_size: float = 0.1,
    adaptivity: float = 0.0,
) -> dict:
    """Unify a messy mesh via voxel remesh (adds a REMESH modifier in VOXEL
    mode and applies it immediately). Smaller `voxel_size` = more detail,
    more polygons. `adaptivity` simplifies flat regions."""
    return _send({
        "tool": "apply_voxel_remesh",
        "name": name,
        "voxel_size": voxel_size,
        "adaptivity": adaptivity,
    })


@mcp.tool()
def apply_decimate(
    name: str,
    decimate_type: str = "COLLAPSE",
    ratio: float = 0.5,
    iterations: int = 2,
    angle_degrees: float = 5.0,
) -> dict:
    """Reduce a mesh's polygon count in-place via a DECIMATE modifier that is
    applied immediately. `decimate_type` is COLLAPSE (use `ratio` ∈ (0, 1]),
    UNSUBDIV (use `iterations`), or DISSOLVE (use `angle_degrees`)."""
    return _send({
        "tool": "apply_decimate",
        "name": name,
        "decimate_type": decimate_type,
        "ratio": ratio,
        "iterations": iterations,
        "angle_degrees": angle_degrees,
    })


# ---------- Phase 16 — Collections + file I/O ----------


@mcp.tool()
def list_collections() -> dict:
    """Return the scene's collection tree, with every collection's hide
    flags, the objects it contains, and its child collections. Use this when
    you need to organize a multi-part scene or find where an object lives."""
    return _send({"tool": "list_collections"})


@mcp.tool()
def create_collection(collection_name: str, parent: str | None = None) -> dict:
    """Create a new collection. If `parent` is given, the new collection is
    nested under that collection; otherwise it hangs off the scene root."""
    return _send({
        "tool": "create_collection",
        "collection_name": collection_name,
        "parent": parent,
    })


@mcp.tool()
def move_to_collection(object_name: str, collection_name: str) -> dict:
    """Move an object into a collection. The object is unlinked from every
    other collection it was in, then linked into the target."""
    return _send({
        "tool": "move_to_collection",
        "object_name": object_name,
        "collection_name": collection_name,
    })


@mcp.tool()
def set_collection_visibility(
    collection_name: str,
    hide_viewport: bool | None = None,
    hide_render: bool | None = None,
) -> dict:
    """Toggle a collection's viewport and/or render visibility. Either
    argument left as None is unchanged."""
    return _send({
        "tool": "set_collection_visibility",
        "collection_name": collection_name,
        "hide_viewport": hide_viewport,
        "hide_render": hide_render,
    })


@mcp.tool()
def delete_collection(
    collection_name: str,
    delete_objects: bool = False,
) -> dict:
    """Delete a collection. By default its objects are moved up to the scene
    root so they don't vanish. Set `delete_objects=True` to also delete every
    object that was in the collection."""
    return _send({
        "tool": "delete_collection",
        "collection_name": collection_name,
        "delete_objects": delete_objects,
    })


@mcp.tool()
def save_blend_file(path: str, compress: bool = True) -> dict:
    """Save the current Blender session to a .blend file at `path`. Creates
    parent directories if needed. `compress=True` uses Blender's default
    compression."""
    return _send({
        "tool": "save_blend_file",
        "path": path,
        "compress": compress,
    })


@mcp.tool()
def open_blend_file(path: str) -> dict:
    """Open a .blend file, **replacing the current session**. All unsaved
    work in the current file is lost. After the load the JustThreed server
    keeps running (a persistent `load_post` handler re-registers the
    dispatcher timer), so you can immediately continue issuing tool calls
    against the new scene."""
    return _send({
        "tool": "open_blend_file",
        "path": path,
    }, timeout=60.0)


@mcp.tool()
def append_from_blend(
    path: str,
    names: list[str],
    data_type: str = "objects",
    collection_name: str | None = None,
) -> dict:
    """Append (copy) datablocks from another .blend file into the current
    session. `data_type` is one of `objects`, `materials`, `meshes`,
    `node_groups`, `collections`, `worlds`. `names` is the list of datablock
    names to copy. For `objects` and `collections`, `collection_name`
    controls which target collection they get linked into (defaults to the
    scene root)."""
    return _send({
        "tool": "append_from_blend",
        "path": path,
        "names": names,
        "data_type": data_type,
        "collection_name": collection_name,
    }, timeout=60.0)


@mcp.tool()
def link_from_blend(
    path: str,
    names: list[str],
    data_type: str = "objects",
    collection_name: str | None = None,
) -> dict:
    """Link (reference) datablocks from another .blend file. Unlike append,
    linked data stays tied to the source file — edits there flow through on
    reload, and the linked blocks can't be modified from this session. Same
    `data_type` / `names` / `collection_name` semantics as append."""
    return _send({
        "tool": "link_from_blend",
        "path": path,
        "names": names,
        "data_type": data_type,
        "collection_name": collection_name,
    }, timeout=60.0)


# ---------- Phase 10b — Render settings / compositor / export ----------


@mcp.tool()
def set_render_engine(engine: str) -> dict:
    """Switch the render engine. Accepts `CYCLES` (photoreal, slower),
    `EEVEE` / `EEVEE_NEXT` / `BLENDER_EEVEE_NEXT` (fast real-time), or
    `WORKBENCH` (solid-shaded previews). `EEVEE` is aliased to EEVEE Next on
    Blender 4.2+."""
    return _send({"tool": "set_render_engine", "engine": engine})


@mcp.tool()
def set_render_settings(
    width: int | None = None,
    height: int | None = None,
    samples: int | None = None,
    denoise: bool | None = None,
    device: str | None = None,
    resolution_percentage: int | None = None,
) -> dict:
    """Set render output dimensions and quality. `samples` applies to the
    current engine (Cycles → `cycles.samples`, EEVEE → `eevee.taa_render_samples`).
    `denoise` and `device` are Cycles-only (`device` is CPU or GPU).
    `resolution_percentage` is the final-output downscale (1–100)."""
    return _send({
        "tool": "set_render_settings",
        "width": width,
        "height": height,
        "samples": samples,
        "denoise": denoise,
        "device": device,
        "resolution_percentage": resolution_percentage,
    })


@mcp.tool()
def set_color_management(
    view_transform: str | None = None,
    look: str | None = None,
    exposure: float | None = None,
    gamma: float | None = None,
) -> dict:
    """Set the scene's color management. `view_transform` is STANDARD,
    FILMIC, FILMIC_LOG, AGX (Blender 4.0+), KHRONOS_PBR_NEUTRAL, RAW, or
    FALSE_COLOR. `look` is a contrast preset — Blender 4.x namespaces
    looks by view transform ("AgX - Medium High Contrast", "Filmic - High
    Contrast", etc.), but you can also pass the bare tail ("Medium High
    Contrast", "Punchy") and the server will auto-prefix with the active
    view transform. On a miss the server returns the full valid list.
    `exposure` is in stops, `gamma` is a post-gamma."""
    return _send({
        "tool": "set_color_management",
        "view_transform": view_transform,
        "look": look,
        "exposure": exposure,
        "gamma": gamma,
    })


@mcp.tool()
def enable_compositor() -> dict:
    """Turn on the scene compositor. If the compositor tree is empty, a
    default Render Layers → Composite passthrough is built so you can start
    wiring effects in immediately."""
    return _send({"tool": "enable_compositor"})


@mcp.tool()
def disable_compositor() -> dict:
    """Turn off the scene compositor (renders skip post-processing)."""
    return _send({"tool": "disable_compositor"})


@mcp.tool()
def add_compositor_node(
    node_type: str,
    location: list[float] | None = None,
    name: str | None = None,
    params: dict | None = None,
) -> dict:
    """Add a compositor node. `node_type` is one of:
    RENDER_LAYERS, COMPOSITE, VIEWER, OUTPUT_FILE, GLARE, BLUR, DEFOCUS,
    LENS_DISTORTION, DENOISE, COLOR_BALANCE, COLOR_CORRECTION, CURVE_RGB,
    HUE_SAT, BRIGHT_CONTRAST, GAMMA, INVERT, FILTER, MIX_RGB, MATH,
    ELLIPSE_MASK, BOX_MASK, VIGNETTE (= ELLIPSE_MASK shortcut), ALPHA_OVER,
    Z_COMBINE, SET_ALPHA, PREMUL_KEY, TONEMAP. `params` is a dict that
    sets input socket defaults (e.g. `{"Fac": 0.5}`) or node attributes
    (e.g. `{"glare_type": "FOG_GLOW"}`)."""
    return _send({
        "tool": "add_compositor_node",
        "node_type": node_type,
        "location": location,
        "name": name,
        "params": params,
    })


@mcp.tool()
def connect_compositor_nodes(
    from_node: str,
    from_socket: str,
    to_node: str,
    to_socket: str,
) -> dict:
    """Connect two compositor nodes by name + socket label."""
    return _send({
        "tool": "connect_compositor_nodes",
        "from_node": from_node,
        "from_socket": from_socket,
        "to_node": to_node,
        "to_socket": to_socket,
    })


@mcp.tool()
def export_scene(
    format: str,
    path: str,
    selected_only: bool = False,
    apply_modifiers: bool = True,
) -> dict:
    """Export the scene to a 3D asset file. `format` is GLB, GLTF, FBX, OBJ,
    USD, USDA, USDC, USDZ, or STL. `selected_only=True` exports only the
    current selection; `apply_modifiers=True` bakes modifier stacks into the
    exported geometry (the usual game-engine handoff)."""
    return _send({
        "tool": "export_scene",
        "format": format,
        "path": path,
        "selected_only": selected_only,
        "apply_modifiers": apply_modifiers,
    }, timeout=180.0)


@mcp.tool()
def export_collection(
    collection_name: str,
    format: str,
    path: str,
    apply_modifiers: bool = True,
) -> dict:
    """Export only the objects in a named collection (recursive through
    sub-collections). Same `format` set as `export_scene`. Selection state is
    saved and restored so this is non-destructive to the user's Blender UI."""
    return _send({
        "tool": "export_collection",
        "collection_name": collection_name,
        "format": format,
        "path": path,
        "apply_modifiers": apply_modifiers,
    }, timeout=180.0)


# ---------- Phase 17 — Python escape hatch ----------


@mcp.tool()
def execute_python(code: str, undo_label: str | None = None) -> dict:
    """Run arbitrary Python code inside Blender. **Danger mode.**

    The code runs with `bpy`, `bmesh`, `math`, and `mathutils.Vector` already
    in scope. Both expressions (`bpy.context.scene.name`) and multi-line
    scripts (`for obj in bpy.data.objects: ...`) are supported — if the first
    line parses as an expression its value is returned, otherwise the whole
    block is executed as statements. A script that sets a top-level `_result`
    variable will surface that value in the response.

    Safety:
    - **Per-session consent.** This tool returns an error unless the user has
      clicked "Enable Python" in the JustThreed N-panel inside Blender. Every
      session starts with Python execution disabled.
    - **Undo wrapping.** The call is bracketed by two `bpy.ops.ed.undo_push`
      calls, so a single Ctrl+Z inside Blender reverts the entire block.
    - **Full logging.** The submitted code is printed to Blender's console
      (prefixed with `[JustThreed]`) before it runs, regardless of success.
    - **stdout/stderr capture.** Anything the script prints is returned in
      the response, not dropped.

    Use this for operations the curated tool set hasn't covered: obscure
    modifiers, multi-scene management, add-on APIs, preference tweaks, or
    quick experiments. For anything you'll want to reproduce later, ask
    Claude to wrap the logic in a proper tool instead."""
    return _send(
        {
            "tool": "execute_python",
            "code": code,
            "undo_label": undo_label,
        },
        timeout=120.0,
    )


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
