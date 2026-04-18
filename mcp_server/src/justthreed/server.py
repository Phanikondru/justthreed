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
def create_capsule(
    name: str | None = None,
    length: float = 2.0,
    height: float = 1.0,
    depth: float = 1.0,
    segments: int = 32,
    location: list[float] | None = None,
    rotation: list[float] | None = None,
) -> dict:
    """Create a capsule/pill/stadium-prism mesh: a rectangle capped by two
    hemispheres along its long axis, extruded to `depth`. Perfect for dynamic
    islands, AirPod cases, pill-shaped buttons, remote controls, and anything
    that needs a true stadium outline (which a cube+bevel only approximates).

    - `length` is the longer dimension (X), `height` is the shorter (Y), and
      `length` must be >= `height`. `depth` is the Z extrusion.
    - `segments` is the vertex count per hemisphere (higher = smoother)."""
    return _send({
        "tool": "create_capsule",
        "name": name,
        "length": length,
        "height": height,
        "depth": depth,
        "segments": segments,
        "location": location or [0.0, 0.0, 0.0],
        "rotation": rotation or [0.0, 0.0, 0.0],
    })


@mcp.tool()
def create_rounded_rect(
    name: str | None = None,
    width: float = 2.0,
    height: float = 1.0,
    depth: float = 0.2,
    corner_radius: float = 0.1,
    corner_segments: int = 16,
    location: list[float] | None = None,
    rotation: list[float] | None = None,
) -> dict:
    """Create a rounded-rectangle prism: a rectangle with rounded corners
    extruded to `depth`. The go-to primitive for phone bodies, tablet/laptop
    shells, card mockups, nav bars, key caps, and any cushion-shaped form
    factor. `corner_radius` must satisfy 0 < r <= min(width, height)/2.
    `corner_segments` controls corner smoothness (16 is plenty for most uses)."""
    return _send({
        "tool": "create_rounded_rect",
        "name": name,
        "width": width,
        "height": height,
        "depth": depth,
        "corner_radius": corner_radius,
        "corner_segments": corner_segments,
        "location": location or [0.0, 0.0, 0.0],
        "rotation": rotation or [0.0, 0.0, 0.0],
    })


@mcp.tool()
def create_empty(
    name: str | None = None,
    display_type: str = "PLAIN_AXES",
    size: float = 1.0,
    location: list[float] | None = None,
    rotation: list[float] | None = None,
) -> dict:
    """Create an Empty object — an invisible transform node used as a rig
    anchor or group parent. `display_type` is one of PLAIN_AXES, ARROWS,
    SINGLE_ARROW, CUBE, SPHERE, CONE, IMAGE, CIRCLE. Pair with `parent_to`
    to rotate or reposition a group of meshes as a unit."""
    return _send({
        "tool": "create_empty",
        "name": name,
        "display_type": display_type,
        "size": size,
        "location": location or [0.0, 0.0, 0.0],
        "rotation": rotation or [0.0, 0.0, 0.0],
    })


@mcp.tool()
def set_light_camera_visibility(name: str, visible: bool = True) -> dict:
    """Toggle whether a light's emissive panel is directly visible to the
    camera. Set `visible=False` to hide a large area light from the frame
    while keeping its full lighting contribution — the fix for "there's a
    bright rectangle floating next to my product shot"."""
    return _send({
        "tool": "set_light_camera_visibility",
        "name": name,
        "visible": visible,
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


_RENDER_CONSENT_PHRASE = "user explicitly asked to render"


def _check_render_consent(user_request_quote: str) -> None:
    """Gate render calls behind a verbatim confirmation string.

    The model must pass the exact phrase `_RENDER_CONSENT_PHRASE` as
    `user_request_quote`, AND include a short verbatim quote from the
    user's most recent message showing the render was asked for.
    This prevents auto-rendering as a silent "final step"."""
    if not user_request_quote or _RENDER_CONSENT_PHRASE not in user_request_quote.lower():
        raise ValueError(
            "Render blocked: this tool only runs when the user explicitly asks for a render. "
            f"Pass `user_request_quote` containing the phrase '{_RENDER_CONSENT_PHRASE}' "
            "followed by a short verbatim quote from the user's latest message that asked "
            "for the render (e.g. \"render it\", \"show me\", \"preview\"). "
            "Do NOT call this as a final step after finishing modeling/materials/lighting work."
        )


@mcp.tool()
def render_image(output_path: str, user_request_quote: str, timeout_seconds: int = 1800) -> dict:
    """Render the current scene through the active camera to a file on disk.
    `output_path` should end in .png or .jpg. Uses the scene's current resolution
    and render engine.

    `user_request_quote` is REQUIRED — it must contain the literal phrase
    "user explicitly asked to render" followed by a short verbatim quote of
    the user's request (e.g. "user explicitly asked to render — they said
    'render it now'"). If the user did not ask for a render, do not call
    this tool.

    `timeout_seconds` controls how long to wait for the render to finish
    (default 1800 = 30 minutes). Complex scenes with high sample counts can
    take much longer than simple previews — set a higher value if needed,
    or reduce samples / resolution first.

    IMPORTANT — do NOT call this automatically as a "final step" after finishing
    a modeling, materials, lighting, or scene-setup task. Only call it when the
    user explicitly asks to render or export an image."""
    _check_render_consent(user_request_quote)
    return _send({"tool": "render_image", "output_path": output_path}, timeout=float(timeout_seconds))


@mcp.tool()
def render_and_show(user_request_quote: str, resolution: int = 512, timeout_seconds: int = 600) -> Image:
    """Render the current scene at a low preview resolution and return the PNG
    so you can actually see the result. Use this for vision-in-the-loop iteration:
    make a change, render, look at it, decide what to fix, repeat. `resolution` is
    the longest edge in pixels (default 512) — keep it small for fast feedback.

    `user_request_quote` is REQUIRED — it must contain the literal phrase
    "user explicitly asked to render" followed by a short verbatim quote of
    the user's request. If the user did not ask for a render, do not call
    this tool.

    `timeout_seconds` controls how long to wait (default 600 = 10 minutes).
    Preview renders are usually fast, but complex scenes with subsurface
    scattering or volumetrics may take longer even at low resolution.

    IMPORTANT — do NOT call this automatically as a "final step" after finishing
    a modeling, materials, lighting, or scene-setup task. Only call it when the
    user explicitly asks to render, preview, or see the result. Rendering is
    expensive and the user wants to control when it happens. Treat task
    completion as the last step unless a render was requested."""
    _check_render_consent(user_request_quote)
    response = _send({"tool": "render_and_show", "resolution": resolution}, timeout=float(timeout_seconds))
    data = base64.b64decode(response["data_base64"])
    return Image(data=data, format="png")


_CRITIQUE_CONSENT_PHRASE = "user asked to match a reference image"


@mcp.tool()
def critique_render(
    user_request_quote: str,
    resolution: int = 512,
    samples: int = 16,
    timeout_seconds: int = 120,
) -> Image:
    """Fast preview render for image-match critique loops.

    Use this when the user provided a reference image and wants the scene to
    match it. Switches temporarily to EEVEE with low samples and a small
    resolution so the render takes a few seconds instead of minutes. Restores
    the original engine, samples, resolution, and output settings afterward.

    Workflow:
      1. Build the scene with primitives/modifiers.
      2. Call `critique_render` to get a quick look from the active camera.
      3. Visually compare the returned image against the user's reference
         (proportions, silhouette, materials, lighting direction).
      4. Apply targeted fixes (move, scale, bevel, swap material) and loop.
      5. Stop when the match is close enough or the user is satisfied.

    Keep iterations bounded — aim for 3-5 critique cycles, not 30. For the
    final beauty render use `render_image` / `render_and_show` with the
    scene's real engine and samples.

    `user_request_quote` is REQUIRED — must contain the literal phrase
    "user asked to match a reference image" followed by a short verbatim
    quote from the user's request (e.g. "user asked to match a reference
    image — they said 'make it look like this bottle photo'").
    """
    quote = (user_request_quote or "").lower()
    if _CRITIQUE_CONSENT_PHRASE not in quote and _RENDER_CONSENT_PHRASE not in quote:
        raise ValueError(
            "critique_render blocked: this tool runs only during an explicit "
            f"image-match workflow. Pass `user_request_quote` containing "
            f"'{_CRITIQUE_CONSENT_PHRASE}' followed by a short verbatim quote "
            "from the user's message showing they asked to match a reference image."
        )
    response = _send(
        {"tool": "critique_render", "resolution": resolution, "samples": samples},
        timeout=float(timeout_seconds),
    )
    data = base64.b64decode(response["data_base64"])
    return Image(data=data, format="png")


@mcp.tool()
def cancel_render() -> dict:
    """Cancel the currently running render in Blender. This sends an interrupt
    signal equivalent to pressing ESC in Blender. Use this when:
    - A render is taking too long and the user wants to stop it
    - The user wants to adjust settings and re-render
    - The user wants to render manually via Blender's UI instead

    Safe to call even if no render is running — Blender ignores the signal."""
    return _send({"tool": "cancel_render"}, timeout=10.0)


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
def create_dispersion_glass_material(
    name: str,
    base_ior: float = 1.45,
    dispersion: float = 0.05,
    roughness: float = 0.0,
    fresnel_ior: float = 1.45,
    glossy_roughness: float = 0.1,
    glossy_mix: float = 0.5,
    glass_color: list[float] | None = None,
    glossy_color: list[float] | None = None,
) -> dict:
    """Build a chromatic-dispersion glass material (rainbow/prism refraction) by
    wiring three Glass BSDFs (R/G/B) at offset IORs, summed and Fresnel-mixed
    with a Glossy BSDF. Produces the classic prism dispersion look (e.g. Whitney
    Spark album cover). `base_ior` is the middle (green) IOR; `dispersion` is
    the IOR delta for red (base-d) and blue (base+d). Higher dispersion = more
    pronounced rainbow. `glossy_mix` controls the Mix Shader factor (0=pure
    glass, 1=pure glossy). Cycles engine strongly recommended. Requires render
    samples ≥256 and Cycles light-path Transmission/Glossy bounces ≥8 to
    resolve the rainbow without noise."""
    return _send({
        "tool": "create_dispersion_glass_material",
        "name": name,
        "base_ior": base_ior,
        "dispersion": dispersion,
        "roughness": roughness,
        "fresnel_ior": fresnel_ior,
        "glossy_roughness": glossy_roughness,
        "glossy_mix": glossy_mix,
        "glass_color": glass_color,
        "glossy_color": glossy_color,
    })


@mcp.tool()
def enable_cycles_dispersion(material_name: str, dispersion: float = 0.25) -> dict:
    """Enable Blender 4.2+ built-in dispersion on any Glass/Principled BSDF
    nodes inside an existing material. Simpler alternative to the 3-BSDF manual
    graph — one value, no wiring. `dispersion` is 0..1 (0.25 is a strong,
    visible rainbow). Only works on Blender 4.2 or newer with Cycles."""
    return _send({
        "tool": "enable_cycles_dispersion",
        "material_name": material_name,
        "dispersion": dispersion,
    })


@mcp.tool()
def create_prism_array(
    count: int = 6,
    radius: float = 2.0,
    prism_length: float = 3.0,
    prism_radius: float = 0.6,
    tilt: float = 0.0,
    center_gap: float = 0.3,
    name_prefix: str = "Prism",
) -> dict:
    """Create N triangular prisms arranged radially in the XY plane, each
    pointing outward from the center — the layout used in the Whitney Spark
    album cover. `count` prisms evenly spaced around a circle of `radius`.
    Each prism is a 3-sided cylinder of `prism_length` (along its axis) and
    `prism_radius` (cross-section). `tilt` rotates each prism around its radial
    axis in degrees (try 15–30 for a fan effect). `center_gap` pushes prisms
    outward so they don't intersect at the origin. Returns the created object
    names — apply a dispersion glass material to all of them afterward."""
    return _send({
        "tool": "create_prism_array",
        "count": count,
        "radius": radius,
        "prism_length": prism_length,
        "prism_radius": prism_radius,
        "tilt": tilt,
        "center_gap": center_gap,
        "name_prefix": name_prefix,
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
    reflector: bool = True,
) -> dict:
    """Build a classic three-point lighting rig (key, fill, rim) around an
    object, all aimed at its bounding-box center. Returns the new light names.
    `distance` is the base radius in meters; `energy` is the key light's base
    energy — fill and rim are scaled off it. Key is at 45° left (warm tint),
    fill at 90° right (soft, half power), rim behind at 135° (edge separation).
    When `reflector` is True (default), adds a white emission bounce card on the
    left side, invisible to camera but visible to reflection rays."""
    return _send({
        "tool": "setup_three_point_lighting",
        "subject_name": subject_name,
        "distance": distance,
        "energy": energy,
        "reflector": reflector,
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
    """Bevel a set of edges (destructive). `width` is offset, `segments` subdivisions, `profile` 0-1.
    Do NOT chain on already-beveled geometry — use `add_bevel_modifier` with limit='ANGLE' instead.
    Safe width <= min adjacent face width / 2; higher widths need `clamp_overlap=False` on a modifier.
    For hard-surface, prefer non-destructive `add_bevel_modifier` + `add_weighted_normals_modifier`."""
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


# ---------- Pro modeling tools — curves, revolve, reference workflow ----------


@mcp.tool()
def create_bezier_curve(
    points: list[list[float]],
    name: str = "ProfileCurve",
    closed: bool = False,
) -> dict:
    """Create a Bezier curve from control points — the foundation for
    professional product modeling.

    `points` is a list of coordinate arrays.  Each can be:
    - [x, z] — 2D profile (y=0), ideal for revolving into round products
    - [x, y, z] — full 3D curve

    For a bottle profile, trace ONE SIDE of the silhouette from bottom to top:
      [[3.5, 0], [3.5, 14], [2.0, 16], [1.5, 17]]
    Then use `revolve_curve` to spin it into a 3D body.

    Handles are set to AUTO for smooth interpolation.  Use 4-8 points for
    most product profiles — Bezier curves fill in the smoothness."""
    return _send({"tool": "create_bezier_curve", "points": points, "name": name, "closed": closed})


@mcp.tool()
def revolve_curve(
    name: str,
    axis: str = "Z",
    angle: float = 360,
    steps: int = 64,
    subdivisions: int = 0,
    convert_to_mesh: bool = True,
) -> dict:
    """Revolve a Bezier curve around an axis using a Screw modifier —
    this is how professional 3D artists create bottles, cups, vases,
    and any round product from a single profile curve.

    Workflow:
    1. `create_bezier_curve` with the silhouette profile
    2. `revolve_curve` to spin it → instant 3D body

    `name` — the curve object to revolve.
    `axis` — X, Y, or Z (default Z = vertical axis).
    `angle` — degrees to revolve (360 = full circle, 180 = half).
    `steps` — mesh resolution (64 is smooth, 32 is coarser).
    `subdivisions` — if > 0, adds a Subdivision Surface modifier.
    `convert_to_mesh` — if true, converts the result to a mesh for
    subsequent editing (extrude, bevel, materials, etc.)."""
    return _send({
        "tool": "revolve_curve",
        "name": name,
        "axis": axis,
        "angle": angle,
        "steps": steps,
        "subdivisions": subdivisions,
        "convert_to_mesh": convert_to_mesh,
    })


@mcp.tool()
def set_reference_image(
    image_path: str,
    size: float = 5.0,
    opacity: float = 0.5,
    height: float = 0.0,
) -> dict:
    """Load a reference image into the Blender viewport as a visible guide.

    Places the image as a semi-transparent Empty in the scene so you (and
    the user) can see it alongside the model being built.  This is how
    professional 3D artists work — always model against a reference.

    `image_path` — absolute path to the reference image.
    `size` — display size in Blender units (default 5).
    `opacity` — transparency 0-1 (default 0.5).
    `height` — vertical offset to center the reference."""
    return _send({
        "tool": "set_reference_image",
        "file_path": image_path,
        "size": size,
        "opacity": opacity,
        "height": height,
    })


@mcp.tool()
def compare_to_reference(resolution: int = 512) -> Image:
    """Render the current scene and return the image so you can compare
    it against the user's reference image.

    THIS IS THE MOST IMPORTANT TOOL FOR ACCURACY.  Use it after every
    major modeling step:
    1. Build/modify the shape
    2. Call `compare_to_reference` to see the current state
    3. Compare it against the reference image the user provided
    4. Identify differences: silhouette, proportions, curves, angles
    5. Fix them with modeling tools
    6. Repeat until it matches

    Be specific about what doesn't match:
    - "Body is too narrow — scale X by 1.3"
    - "Shoulder taper starts too low — need to adjust profile"
    - "Pump nozzle angle is 30° but should be 45°"

    Keep iterating until the render matches the reference."""
    response = _send(
        {"tool": "render_and_show", "resolution": resolution},
        timeout=300.0,
    )
    data = base64.b64decode(response["data_base64"])
    return Image(data=data, format="png")


# ---------- Product shot composition ----------


@mcp.tool()
def frame_product_shot(
    subject_name: str,
    angle: str = "FRONT",
    composition: str = "GOLDEN_RATIO",
    lens_mm: float = 85,
    padding: float = 1.3,
    aspect: str = "4:5",
) -> dict:
    """Auto-frame a product for professional photography composition.

    Calculates the perfect camera position based on the object's bounding
    box, lens focal length, and composition rule.  Creates (or reuses) a
    camera, aims it at the subject, and sets it as the active scene camera.

    This is what separates amateur renders from professional product shots.

    `subject_name` — the object to frame.
    `angle` — camera angle preset:
      FRONT, FRONT_HIGH, THREE_QUARTER, SIDE, TOP, HERO
    `composition` — framing rule:
      GOLDEN_RATIO (default, most pleasing), RULE_OF_THIRDS, CENTER
    `lens_mm` — focal length (85mm default = product photography standard,
      50mm for wider context, 135mm for tight detail shots).
    `padding` — how much space around the object (1.0 = tight, 1.5 = loose).
    `aspect` — render aspect ratio: 1:1, 4:5, 3:4, 16:9, 9:16."""
    return _send({
        "tool": "frame_product_shot",
        "subject_name": subject_name,
        "angle": angle,
        "composition": composition,
        "lens_mm": lens_mm,
        "padding": padding,
        "aspect": aspect,
    })


# ---------- Render optimization ----------


@mcp.tool()
def optimize_cycles(
    device: str = "GPU",
    noise_threshold: float = 0.1,
    samples: int = 128,
    denoise: bool = True,
    max_bounces: int = 4,
    caustics: bool = False,
    fast_gi: bool = True,
    persistent_data: bool = True,
) -> dict:
    """Configure Cycles for fast, high-quality rendering in one call.

    Applies all major optimizations from professional workflows:
    - GPU rendering (vs CPU — 10-50x faster)
    - Adaptive noise threshold (stops early when clean enough)
    - Optimized light path bounces (fast defaults)
    - Caustics disabled (big speed win, rarely needed for products)
    - Fast GI approximation (faster indirect lighting)
    - Persistent data (keeps textures in memory between renders)
    - Denoiser enabled (cleans up remaining noise)

    Default settings give ~95% speed improvement over Cycles defaults
    while maintaining good quality for product visualization.

    For the compare_to_reference iteration loop, these defaults are ideal.
    For final beauty renders, increase samples to 256-512 and set
    noise_threshold to 0.01."""
    return _send({
        "tool": "optimize_cycles",
        "device": device,
        "noise_threshold": noise_threshold,
        "samples": samples,
        "denoise": denoise,
        "max_bounces": max_bounces,
        "caustics": caustics,
        "fast_gi": fast_gi,
        "persistent_data": persistent_data,
    })


# ---------- AI/ML accuracy pipeline — VLM analysis ----------


@mcp.tool()
def analyze_reference_image(image_path: str) -> dict:
    """Analyse a reference product image using a Vision-Language Model and
    return a structured spec: object type, shape description, materials,
    colors, estimated dimensions, and step-by-step modeling hints.

    Use this when the user provides a reference image.  The returned spec
    tells you exactly how to recreate the object using the existing modeling
    tools (create_primitive, extrude_faces, bevel_edges, create_pbr_material,
    etc.).  Read the `modeling_hints` field for a suggested build order.

    `image_path` must be an absolute path to a local image file (PNG, JPG, WEBP).

    Requires a network connection (calls a Hugging Face Space).  Set the env
    var JUSTTHREED_VLM_SPACE to override the default VLM endpoint."""
    from justthreed.ml_pipeline import analyze_image

    return analyze_image(image_path)



# ---------- Hard-surface iPhone-style modeling helpers ----------


@mcp.tool()
def create_rounded_box(
    name: str,
    width: float,
    height: float,
    depth: float,
    corner_radius: float,
    edge_bevel: float = 0.0,
    segments: int = 16,
    location: list[float] | None = None,
) -> dict:
    """Create a hard-surface rounded box (front-face XZ rounded-rect extruded along Y).
    Optional `edge_bevel` adds a small chamfer on front/back perimeter edges for the iPhone rail look."""
    return _send({
        "tool": "create_rounded_box",
        "name": name,
        "width": width,
        "height": height,
        "depth": depth,
        "corner_radius": corner_radius,
        "edge_bevel": edge_bevel,
        "segments": segments,
        "location": list(location) if location else [0.0, 0.0, 0.0],
    })


@mcp.tool()
def create_phone_body(
    name: str = "PhoneBody",
    width: float = 7.7,
    height: float = 16.0,
    depth: float = 0.83,
    corner_radius: float = 1.35,
    rail_bevel: float = 0.18,
    location: list[float] | None = None,
) -> dict:
    """Create an iPhone-proportioned rounded box with sensible defaults and an auto rail bevel.
    Wrapper around `create_rounded_box` for discoverability."""
    return _send({
        "tool": "create_phone_body",
        "name": name,
        "width": width,
        "height": height,
        "depth": depth,
        "corner_radius": corner_radius,
        "rail_bevel": rail_bevel,
        "location": list(location) if location else [0.0, 0.0, 0.0],
    })


@mcp.tool()
def fillet_seam(
    obj_a: str,
    obj_b: str,
    plane_value: float,
    radius: float,
    plane_axis: str = "Y",
    overlap: float = 0.1,
    segments: int = 10,
    profile: float = 0.5,
    consume_b: bool = True,
) -> dict:
    """Union `obj_b` into `obj_a` and fillet the resulting seam ring (camera plateau onto phone body).
    Auto-clamps radius and detects seam edges via interface-plane face filtering (not angle-based)."""
    return _send({
        "tool": "fillet_seam",
        "obj_a": obj_a,
        "obj_b": obj_b,
        "plane_axis": plane_axis,
        "plane_value": plane_value,
        "radius": radius,
        "overlap": overlap,
        "segments": segments,
        "profile": profile,
        "consume_b": consume_b,
    })


@mcp.tool()
def load_reference_image(
    path: str,
    axis: str = "-Y",
    opacity: float = 0.5,
    empty_name: str = "RefImage",
) -> dict:
    """Load an image as an Empty (IMAGE) oriented to face a viewport axis (+X/-X/+Y/-Y/+Z/-Z).
    Use as a background reference plane while modeling."""
    return _send({
        "tool": "load_reference_image",
        "path": path,
        "axis": axis,
        "opacity": opacity,
        "empty_name": empty_name,
    })


# ---------- Hard-surface batch 2 — booleans, modifiers, edge ops, queries ----------


@mcp.tool()
def cylinder_cut(
    target: str,
    location: list[float],
    axis: str,
    radius: float,
    depth: float,
    vertices: int = 48,
    consume_cutter: bool = True,
) -> dict:
    """Boolean-difference a cylinder out of `target` (cylinder centered at `location`, oriented along `axis`).
    Cylinder spans +-depth/2 along the axis; `consume_cutter` removes the temp cutter when True."""
    return _send({
        "tool": "cylinder_cut",
        "target": target,
        "location": list(location),
        "axis": axis,
        "radius": radius,
        "depth": depth,
        "vertices": vertices,
        "consume_cutter": consume_cutter,
    })


@mcp.tool()
def add_mirror_modifier(
    name: str,
    axis: str = "X",
    use_clip: bool = True,
    merge_threshold: float = 0.001,
    mirror_object: str | None = None,
) -> dict:
    """Add a Mirror modifier (axis can be combos like 'XY'); does not apply.
    Optional `mirror_object` is the empty/object used as mirror pivot."""
    return _send({
        "tool": "add_mirror_modifier",
        "name": name,
        "axis": axis,
        "use_clip": use_clip,
        "merge_threshold": merge_threshold,
        "mirror_object": mirror_object,
    })


@mcp.tool()
def apply_mirror(name: str, modifier_name: str = "Mirror") -> dict:
    """Apply the named Mirror modifier on `name`."""
    return _send({"tool": "apply_mirror", "name": name, "modifier_name": modifier_name})


@mcp.tool()
def add_subsurf_modifier(
    name: str,
    levels: int = 2,
    render_levels: int = 3,
    use_limit_surface: bool = True,
) -> dict:
    """Add a Subdivision Surface modifier and shade smooth the object."""
    return _send({
        "tool": "add_subsurf_modifier",
        "name": name,
        "levels": levels,
        "render_levels": render_levels,
        "use_limit_surface": use_limit_surface,
    })


@mcp.tool()
def set_edge_crease(
    name: str,
    edge_selection: str = "sharp",
    value: float = 1.0,
    sharp_angle: float = 30.0,
) -> dict:
    """Set the crease weight on edges chosen by `edge_selection` (sharp/boundary/all/selected)."""
    return _send({
        "tool": "set_edge_crease",
        "name": name,
        "edge_selection": edge_selection,
        "value": value,
        "sharp_angle": sharp_angle,
    })


@mcp.tool()
def set_edge_bevel_weight(
    name: str,
    edge_selection: str = "sharp",
    value: float = 1.0,
    sharp_angle: float = 30.0,
) -> dict:
    """Set the bevel weight on edges chosen by `edge_selection` (sharp/boundary/all/selected)."""
    return _send({
        "tool": "set_edge_bevel_weight",
        "name": name,
        "edge_selection": edge_selection,
        "value": value,
        "sharp_angle": sharp_angle,
    })


@mcp.tool()
def add_support_loops(
    name: str,
    edge_selection: str = "sharp",
    distance: float = 0.05,
    sharp_angle: float = 30.0,
) -> dict:
    """Add SubD-friendly support loops (2-segment profile=1 bevel) at `distance` around chosen edges."""
    return _send({
        "tool": "add_support_loops",
        "name": name,
        "edge_selection": edge_selection,
        "distance": distance,
        "sharp_angle": sharp_angle,
    })


@mcp.tool()
def select_by(
    name: str,
    mode: str,
    sharp_angle: float = 30.0,
    normal: list[float] | None = None,
    tolerance: float = 0.1,
    axis: str | None = None,
    value: float = 0.0,
) -> dict:
    """Select edges/faces on `name` by `mode` (boundary/non_manifold/sharp/by_normal/by_plane).
    Selection is left active in object mode for follow-up ops."""
    payload: dict = {
        "tool": "select_by",
        "name": name,
        "mode": mode,
        "sharp_angle": sharp_angle,
        "tolerance": tolerance,
        "value": value,
    }
    if normal is not None:
        payload["normal"] = list(normal)
    if axis is not None:
        payload["axis"] = axis
    return _send(payload)


@mcp.tool()
def surface_blend_loops(
    obj_a: str,
    obj_b: str,
    loop_a_selector: dict,
    loop_b_selector: dict,
    segments: int = 8,
    profile: float = 0.5,
    consume_b: bool = True,
) -> dict:
    """Join `obj_b` into `obj_a` and bridge the two selected edge loops into a smooth blend.
    Loop selectors reuse `select_by` modes (e.g. {'mode': 'by_plane', 'axis': 'Y', 'value': 0.5})."""
    return _send({
        "tool": "surface_blend_loops",
        "obj_a": obj_a,
        "obj_b": obj_b,
        "loop_a_selector": dict(loop_a_selector),
        "loop_b_selector": dict(loop_b_selector),
        "segments": segments,
        "profile": profile,
        "consume_b": consume_b,
    })


@mcp.tool()
def add_array_modifier(
    name: str,
    count: int = 3,
    relative_offset: list[float] | None = None,
    use_constant: bool = False,
    constant_offset: list[float] | None = None,
) -> dict:
    """Add an Array modifier with a relative-offset displacement and optional constant-offset."""
    return _send({
        "tool": "add_array_modifier",
        "name": name,
        "count": count,
        "relative_offset": list(relative_offset) if relative_offset else [1.0, 0.0, 0.0],
        "use_constant": use_constant,
        "constant_offset": list(constant_offset) if constant_offset else [0.0, 0.0, 0.0],
    })


@mcp.tool()
def solidify(
    name: str,
    thickness: float,
    offset: float = -1.0,
    even_thickness: bool = True,
    apply: bool = False,
) -> dict:
    """Add a Solidify modifier (offset -1 inward, 0 centered, +1 outward); optionally apply."""
    return _send({
        "tool": "solidify",
        "name": name,
        "thickness": thickness,
        "offset": offset,
        "even_thickness": even_thickness,
        "apply": apply,
    })


@mcp.tool()
def inset_and_extrude(
    name: str,
    inset: float,
    extrude: float,
    face_selection: str = "selected",
    normal: list[float] | None = None,
    tolerance: float = 0.1,
) -> dict:
    """Inset then extrude faces chosen by `face_selection` (selected/by_normal/top/bottom/front/back/left/right).
    Positive `extrude` = outward along face normal, negative = inward (well/recess)."""
    payload: dict = {
        "tool": "inset_and_extrude",
        "name": name,
        "face_selection": face_selection,
        "inset": inset,
        "extrude": extrude,
        "tolerance": tolerance,
    }
    if normal is not None:
        payload["normal"] = list(normal)
    return _send(payload)


@mcp.tool()
def dimensions_of(name: str) -> dict:
    """Return world-space dimensions, bbox min/max, and origin location for `name`."""
    return _send({"tool": "dimensions_of", "name": name})


@mcp.tool()
def distance_between(a: str, b: str) -> dict:
    """Return world-space distance and delta vector between the origins of objects `a` and `b`."""
    return _send({"tool": "distance_between", "a": a, "b": b})


# ---------- Batch 3: Hard-surface atomic ops + composites (from Blender Bros e-book) ----------


@mcp.tool()
def add_bevel_modifier(
    name: str,
    width: float = 0.02,
    segments: int = 3,
    profile: float = 0.5,
    limit_method: str = "ANGLE",
    angle_degrees: float = 30.0,
    clamp_overlap: bool = False,
    harden_normals: bool = True,
    loop_slide: bool = True,
) -> dict:
    """Add a non-destructive Bevel modifier — the hard-surface workhorse.
    `limit_method` ANGLE (auto) / WEIGHT (manual via set_edge_bevel_weight) / NONE (every edge).
    Pitfall: Blender clamps bevels at Boolean connection points; set clamp_overlap=False to bevel past them.
    For multi-size bevels stack two: first angle=30 small, second angle=60 larger (avoids overlap)."""
    return _send({
        "tool": "add_bevel_modifier",
        "name": name,
        "width": width,
        "segments": segments,
        "profile": profile,
        "limit_method": limit_method,
        "angle_degrees": angle_degrees,
        "clamp_overlap": clamp_overlap,
        "harden_normals": harden_normals,
        "loop_slide": loop_slide,
    })


@mcp.tool()
def add_weighted_normals_modifier(
    name: str,
    weight: int = 50,
    keep_sharp: bool = True,
    face_influence: bool = False,
) -> dict:
    """Add a Weighted Normal modifier to straighten bevel normals — fixes shading warps on flat hard-surface.
    Stack AFTER Bevel. Pitfall: ineffective on curved surfaces (cylinders); use horizontal support loops instead."""
    return _send({
        "tool": "add_weighted_normals_modifier",
        "name": name,
        "weight": weight,
        "keep_sharp": keep_sharp,
        "face_influence": face_influence,
    })


@mcp.tool()
def add_boolean_modifier(
    target: str,
    cutter: str,
    operation: str = "DIFFERENCE",
    solver: str = "EXACT",
    apply: bool = False,
    hide_cutter: bool = True,
) -> dict:
    """Non-destructive Boolean modifier (DIFFERENCE/UNION/INTERSECT). EXACT solver is slower but cleaner.
    Stack ABOVE Bevel so bevel only applies to original edges, not boolean cut (PDF: bevel first, bool second).
    Keep non-destructive so you can move cutter freely; only apply when ready to clean vertices."""
    return _send({
        "tool": "add_boolean_modifier",
        "target": target,
        "cutter": cutter,
        "operation": operation,
        "solver": solver,
        "apply": apply,
        "hide_cutter": hide_cutter,
    })


@mcp.tool()
def set_auto_smooth(name: str, angle_degrees: float = 30.0) -> dict:
    """Enable shade-smooth with an auto-smooth angle cutoff (edges sharper than angle stay faceted).
    30deg is the hard-surface default. Uses mesh.auto_smooth in Blender 4.0, smooth-by-angle modifier in 4.1+."""
    return _send({"tool": "set_auto_smooth", "name": name, "angle_degrees": angle_degrees})


@mcp.tool()
def merge_by_distance(name: str, distance: float = 0.0001) -> dict:
    """Weld vertices within `distance`. Essential for boolean cleanup — removes duplicate verts after applying.
    Pitfall: too-large distance collapses intentional detail; start with 0.0001 and increase only if needed."""
    return _send({"tool": "merge_by_distance", "name": name, "distance": distance})


@mcp.tool()
def limited_dissolve(name: str, angle_degrees: float = 5.0) -> dict:
    """Dissolve coplanar edges/verts whose angle is under `angle_degrees` — cleans up stray edges after booleans.
    Pitfall: angle too high destroys curvature; keep under 10deg for cylinders, under 5deg for subtle curves."""
    return _send({"tool": "limited_dissolve", "name": name, "angle_degrees": angle_degrees})


@mcp.tool()
def loop_cut(name: str, edge_index: int, cuts: int = 1, factor: float = 0.0) -> dict:
    """Insert `cuts` loop(s) perpendicular to the edge ring containing `edge_index`.
    Use for 'dicing': add loops to a cutter BEFORE boolean to force denser geometry in cut area (PDF ch. Dicing Booleans).
    Also use for support loops on SubD meshes to tighten edges (alternative: add_support_loops)."""
    return _send({"tool": "loop_cut", "name": name, "edge_index": edge_index, "cuts": cuts, "factor": factor})


@mcp.tool()
def knife_project(target: str, cutter: str, cut_through: bool = True) -> dict:
    """Project `cutter`'s silhouette onto `target` as mesh cuts (scriptable knife-project equivalent).
    Limitation: no interactive knife tool via script; use boolean for volumetric cuts or this for surface cuts.
    Pitfall: requires camera/view alignment — target normal should face cutter."""
    return _send({"tool": "knife_project", "target": target, "cutter": cutter, "cut_through": cut_through})


@mcp.tool()
def symmetrize(name: str, direction: str = "POSITIVE_X", threshold: float = 0.0001) -> dict:
    """Make `name` symmetric by mirroring one half onto the other in-place (destructive, single object).
    `direction` e.g. POSITIVE_X -> NEGATIVE_X copies +X side to -X. Different from Mirror modifier (separate object)."""
    return _send({"tool": "symmetrize", "name": name, "direction": direction, "threshold": threshold})


@mcp.tool()
def triangulate(name: str, apply: bool = False, min_vertices: int = 4) -> dict:
    """Add a Triangulate modifier (converts ngons/quads to tris). Needed for export to game engines / 3D printing.
    Pitfall: applying destroys quad topology — keep non-destructive for hard-surface, only apply for export."""
    return _send({"tool": "triangulate", "name": name, "apply": apply, "min_vertices": min_vertices})


@mcp.tool()
def add_decimate_modifier(
    name: str,
    mode: str = "COLLAPSE",
    ratio: float = 0.5,
    angle_degrees: float = 5.0,
) -> dict:
    """Non-destructive Decimate modifier. `mode` COLLAPSE (ratio), UNSUBDIV (iterations), DISSOLVE (planar).
    Use PLANAR+angle=5 to clean dense boolean output without destroying silhouette."""
    return _send({
        "tool": "add_decimate_modifier",
        "name": name,
        "mode": mode,
        "ratio": ratio,
        "angle_degrees": angle_degrees,
    })


@mcp.tool()
def remesh_modifier(
    name: str,
    mode: str = "VOXEL",
    voxel_size: float = 0.02,
    octree_depth: int = 6,
    apply: bool = False,
) -> dict:
    """Add a Remesh modifier. VOXEL rebuilds as uniform voxels; SHARP preserves edges; SMOOTH smooths.
    For hard-surface, use VOXEL only as last-resort cleanup; destroys hard edges. Better: manual cleanup + weighted normals."""
    return _send({
        "tool": "remesh_modifier",
        "name": name,
        "mode": mode,
        "voxel_size": voxel_size,
        "octree_depth": octree_depth,
        "apply": apply,
    })


@mcp.tool()
def make_planar(name: str) -> dict:
    """Flatten selected faces onto their average plane (mesh.face_make_planar). Use after knife/boolean to clean tilted faces."""
    return _send({"tool": "make_planar", "name": name})


# ---------- Workflow composites ----------


@mcp.tool()
def hard_edge_weighted_normals(
    name: str,
    bevel_width: float = 0.005,
    bevel_segments: int = 1,
    angle_degrees: float = 30.0,
) -> dict:
    """One-shot 'hard-edge' setup: single-segment small bevel (angle-limited) + weighted normals modifier.
    PDF's canonical flat-surface shading fix. Use on flat-dominant objects; for curved use add_support_loops instead."""
    return _send({
        "tool": "hard_edge_weighted_normals",
        "name": name,
        "bevel_width": bevel_width,
        "bevel_segments": bevel_segments,
        "angle_degrees": angle_degrees,
    })


@mcp.tool()
def boolean_with_cleanup(
    target: str,
    cutter: str,
    operation: str = "DIFFERENCE",
    support_loop_distance: float = 0.01,
    add_weighted_normals: bool = True,
    consume_cutter: bool = True,
) -> dict:
    """Apply a boolean then auto-clean: merge_by_distance, limited_dissolve, support loops on sharp edges, weighted normals.
    PDF's 'Boolean Cleanup Strategies' distilled. For curved surfaces this may still leave artifacts — inspect and slide verts."""
    return _send({
        "tool": "boolean_with_cleanup",
        "target": target,
        "cutter": cutter,
        "operation": operation,
        "support_loop_distance": support_loop_distance,
        "add_weighted_normals": add_weighted_normals,
        "consume_cutter": consume_cutter,
    })


@mcp.tool()
def panel_cut(
    name: str,
    face_selection: str = "top",
    inset: float = 0.02,
    depth: float = -0.005,
    bevel_inner: float = 0.001,
) -> dict:
    """Create a recessed panel (phone back, hatch, vent plate). Inset selected face, extrude inward, bevel inner edges.
    `depth` negative = recess, positive = raised panel. face_selection: top/bottom/front/back/left/right/selected."""
    return _send({
        "tool": "panel_cut",
        "name": name,
        "face_selection": face_selection,
        "inset": inset,
        "depth": depth,
        "bevel_inner": bevel_inner,
    })


@mcp.tool()
def dice_boolean(
    target: str,
    cutter: str,
    loop_cuts: int = 8,
    operation: str = "DIFFERENCE",
    apply: bool = True,
    consume_cutter: bool = True,
) -> dict:
    """PDF's 'Dicing Booleans' technique: add loop cuts THROUGH the cutter before boolean, forcing denser geometry
    in the cut area of the target. Use for angled/curved cuts that produce bad shading. `loop_cuts` = count through cutter."""
    return _send({
        "tool": "dice_boolean",
        "target": target,
        "cutter": cutter,
        "loop_cuts": loop_cuts,
        "operation": operation,
        "apply": apply,
        "consume_cutter": consume_cutter,
    })


@mcp.tool()
def screw_hole(
    target: str,
    location: list[float],
    radius: float = 0.005,
    depth: float = 0.01,
    countersink_radius: float = 0.008,
    countersink_depth: float = 0.002,
    axis: str = "Z",
) -> dict:
    """Cut a screw-hole with countersink: two stacked cylinder-cuts (wide shallow + narrow deep)."""
    return _send({
        "tool": "screw_hole",
        "target": target,
        "location": list(location),
        "radius": radius,
        "depth": depth,
        "countersink_radius": countersink_radius,
        "countersink_depth": countersink_depth,
        "axis": axis,
    })


# ---------- Cookbook: learn/context layer ----------


_HARDSURFACE_COOKBOOK: dict = {
    "bevel_everything": {
        "technique": "Always bevel every edge",
        "when_to_use": "Every hard-surface object before render. Microscopic in reality -> always add one.",
        "inputs": {"width": "0.002-0.01 m", "segments": "1-3", "limit": "ANGLE 30deg"},
        "tool_sequence": ["add_bevel_modifier(name, width=0.005, limit_method='ANGLE', angle_degrees=30)"],
        "pitfall": "Do not apply if you might re-edit; keep in stack.",
    },
    "auto_smooth_30": {
        "technique": "Auto-smooth at 30 degrees",
        "when_to_use": "Any object with a mix of flat faces and rounded bevels — default shading setup.",
        "inputs": {"angle": "30deg"},
        "tool_sequence": ["set_auto_smooth(name, angle_degrees=30)"],
        "pitfall": "Blender 4.0 uses mesh.use_auto_smooth; 4.1+ uses a smooth-by-angle modifier.",
    },
    "hard_edge_flat": {
        "technique": "Hard edge with Weighted Normals (flat surfaces)",
        "when_to_use": "Flat-dominant shapes (boxes, plates). Fixes bevel shading warps.",
        "inputs": {"bevel_width": "0.005", "segments": "1", "weight": "50"},
        "tool_sequence": ["hard_edge_weighted_normals(name, bevel_width=0.005, bevel_segments=1)"],
        "pitfall": "Ineffective on curved/cylindrical surfaces — use support loops + denser geo there.",
    },
    "curved_boolean_cleanup": {
        "technique": "Boolean on a curved surface with horizontal support loops",
        "when_to_use": "Cutting holes into cylinders/curved panels. PDF ch. Booleans (curved surfaces).",
        "inputs": {"cylinder_vertices": "64+", "horizontal_loops": "1 above + 1 below cut"},
        "tool_sequence": [
            "loop_cut(target, edge_on_vertical_side, cuts=2)   # horizontal loops bracketing the cut",
            "add_boolean_modifier(target, cutter, apply=True)",
            "merge_by_distance(target, 0.0001)",
            "add_bevel_modifier(target, width=0.002, limit_method='WEIGHT')",
        ],
        "pitfall": "Weighted Normals barely helps curved surfaces; focus on dense geo and tight bevel instead.",
    },
    "dicing_boolean": {
        "technique": "Dicing a Boolean cutter",
        "when_to_use": "Angled / long cutters producing stretched shading on the target.",
        "inputs": {"loop_cuts_through_cutter": "8-16"},
        "tool_sequence": ["dice_boolean(target, cutter, loop_cuts=12, apply=True)"],
        "pitfall": "Make cutter just large enough to cover cut area; oversized cutter wastes loops.",
    },
    "multi_level_bevels": {
        "technique": "Two bevel modifiers for different-size bevels on same mesh",
        "when_to_use": "Boolean cut edges want larger/smaller bevel than object's exterior edges.",
        "inputs": {"first_bevel_angle": "30", "second_bevel_angle": "60 (higher)"},
        "tool_sequence": [
            "add_bevel_modifier(name, angle_degrees=30, width=0.005)",
            "add_boolean_modifier(name, cutter)",
            "add_bevel_modifier(name, angle_degrees=60, width=0.002)  # only catches bool-cut edges",
        ],
        "pitfall": "If both bevels have the same angle_degrees, they double-bevel and overlap. Second MUST be higher.",
    },
    "bevel_weight_only_bool": {
        "technique": "Bevel weight for manual control on applied booleans",
        "when_to_use": "After applying a boolean, you want the cut-edges beveled separately.",
        "inputs": {"weight": "1.0 on selected loops"},
        "tool_sequence": [
            "add_boolean_modifier(target, cutter, apply=True)",
            "select_by(target, mode='boundary')  # or by_normal, by_plane",
            "set_edge_bevel_weight(target, edge_selection='selected', value=1.0)",
            "add_bevel_modifier(target, limit_method='WEIGHT', width=0.003)",
        ],
        "pitfall": "Semi-destructive: applying the boolean breaks non-destructive edits.",
    },
    "subd_with_boolean": {
        "technique": "SubD + Boolean in correct stack order",
        "when_to_use": "Organic-ish hard surface (e.g. sci-fi panels). PDF ch. Subd with Booleans.",
        "inputs": {"subsurf_levels": "2-3", "apply_subsurf_before_bool": "yes"},
        "tool_sequence": [
            "add_subsurf_modifier(name, levels=2)",
            "apply_modifier(name, 'Subsurf')   # critical: apply BEFORE bool",
            "add_boolean_modifier(name, cutter)",
            "add_bevel_modifier(name, width=0.002)",
        ],
        "pitfall": "Boolean BEFORE subsurf = catastrophic mesh collapse. Always apply subsurf first.",
    },
    "support_loops_subd": {
        "technique": "Support loops to sharpen SubD edges",
        "when_to_use": "Need tight bevels on a SubD mesh without creases.",
        "inputs": {"distance": "0.02-0.05 from target edge"},
        "tool_sequence": ["add_support_loops(name, edge_selection='sharp', distance=0.02)"],
        "pitfall": "Too-close loops cause pinching; too-far loops round off corners.",
    },
    "non_destructive_panel": {
        "technique": "Recessed panel (phone back / hatch)",
        "when_to_use": "Inset + recess on a face — phones, controllers, device hatches.",
        "inputs": {"inset": "0.003", "depth": "-0.001"},
        "tool_sequence": ["panel_cut(name, face_selection='top', inset=0.003, depth=-0.001, bevel_inner=0.0005)"],
        "pitfall": "Depth too negative pokes through thin shells; check solidify thickness.",
    },
    "speaker_grille": {
        "technique": "Speaker grille via array + mirror + boolean",
        "when_to_use": "Grid of identical cut-outs (speakers, vents).",
        "inputs": {"array_count": "8x8", "cut_radius": "0.001"},
        "tool_sequence": [
            "create_primitive('CYLINDER', radius=0.001, depth=0.01) as cutter",
            "add_array_modifier(cutter, count=8, relative_offset=[1.2,0,0])",
            "add_array_modifier(cutter, count=8, relative_offset=[0,1.2,0])",
            "apply all, then add_boolean_modifier(target, cutter, apply=True)",
        ],
        "pitfall": "Apply arrays before boolean; EXACT solver required for many cutters at once.",
    },
    "boolean_artifact_fix": {
        "technique": "Fix shading artifacts around a Boolean",
        "when_to_use": "Dark blotches along a boolean-cut edge — geometry overlap.",
        "inputs": {"merge_distance": "0.0001", "dissolve_angle": "5deg"},
        "tool_sequence": [
            "apply_modifier(target, 'Boolean')",
            "merge_by_distance(target, 0.0001)",
            "limited_dissolve(target, angle_degrees=5)",
            "# manual: slide overlapping verts away from bevel",
        ],
        "pitfall": "Artifacts = overlaps 100% of the time. If they persist, you missed a vertex pair.",
    },
    "clamp_overlap_off": {
        "technique": "Disable Clamp Overlap on Bevel to bevel past boolean connections",
        "when_to_use": "Bevel stops growing near a boolean cut due to Blender's default clamp.",
        "inputs": {"clamp_overlap": "False"},
        "tool_sequence": ["add_bevel_modifier(name, clamp_overlap=False, width=0.01)"],
        "pitfall": "Off by default can cause immediate overlaps on complex meshes; re-enable if shading breaks.",
    },
    "ngon_workflow": {
        "technique": "Ngons + Booleans (non-destructive modeling)",
        "when_to_use": "Concept renders, 3D prints, CAD-style workflows. NOT rigged/animated meshes.",
        "inputs": {},
        "tool_sequence": ["use booleans liberally; only apply when geometry is frozen"],
        "pitfall": "Ngons are fine for hard-surface per Chipp Walters / PDF. Only convert to quads for deformation/subd.",
    },
}


@mcp.tool()
def get_hardsurface_cookbook(technique: str | None = None) -> dict:
    """Return the built-in hard-surface modeling cookbook (Blender Bros e-book + common patterns).
    Pass `technique` to get one entry (e.g. 'dicing_boolean'), or omit to get all. Each entry has
    when_to_use / inputs / tool_sequence / pitfall to guide AI clients composing workflows."""
    if technique is None:
        return {"ok": True, "techniques": _HARDSURFACE_COOKBOOK, "count": len(_HARDSURFACE_COOKBOOK)}
    entry = _HARDSURFACE_COOKBOOK.get(technique)
    if entry is None:
        return {"ok": False, "error": f"unknown technique {technique!r}. Available: {sorted(_HARDSURFACE_COOKBOOK)}"}
    return {"ok": True, "technique": technique, **entry}


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
