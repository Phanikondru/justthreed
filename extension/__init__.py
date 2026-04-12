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
import contextlib
import io
import json
import math
import os
import queue
import socket
import tempfile
import threading
import traceback

import bmesh
import bpy
from mathutils import Vector

HOST = "localhost"
PORT = 9876

# Each job: (command_dict, result_dict, done_event)
# The socket-handler thread enqueues a job and waits on the event.
# The main-thread timer drains jobs, fills the result dict, and sets the event.
_job_queue: "queue.Queue[tuple[dict, dict, threading.Event]]" = queue.Queue()

_server_socket: socket.socket | None = None
_server_thread: threading.Thread | None = None
_running = False

# Phase 17 — Python escape hatch consent gate. The `execute_python` tool is
# powerful enough to delete files / modify preferences / shell out, so every
# Blender session starts with it disabled. The user must click "Enable Python"
# in the JustThreed N-panel to opt in. Disabling the extension, closing
# Blender, or clicking the button again flips it back off.
_python_allowed = False


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

# Modifier params whose values are object names — must be resolved to bpy.types.Object
# before being assigned. Keyed by the attribute name on the modifier.
_OBJECT_REF_PARAMS = {
    "object",          # Boolean
    "mirror_object",   # Mirror
    "target",          # Shrinkwrap
    "origin",          # Screw, Array (offset object)
    "offset_object",   # Array
    "start_cap",       # Array
    "end_cap",         # Array
    "curve",           # Curve (deform)
    "texture_coords_object",  # Displace
    "object_from",     # UVProject etc.
    "object_to",
}

_ORIGIN_MODES = {
    "GEOMETRY": ("ORIGIN_GEOMETRY", "MEDIAN"),
    "BOUNDS": ("ORIGIN_GEOMETRY", "BOUNDS"),
    "CURSOR": ("ORIGIN_CURSOR", "MEDIAN"),
    "VOLUME": ("ORIGIN_CENTER_OF_VOLUME", "MEDIAN"),
    "MASS": ("ORIGIN_CENTER_OF_MASS", "MEDIAN"),
}


def _resolve_object(name):
    """Look up an object by name. Returns (obj, None) on success or (None, error_dict)."""
    if not name:
        return None, {"ok": False, "error": "object name is required"}
    obj = bpy.data.objects.get(name)
    if obj is None:
        return None, {"ok": False, "error": f"No object named {name!r}"}
    return obj, None


def _select_only(obj) -> None:
    """Deselect everything, then select+activate a single object.
    Required before any bpy.ops that reads from the selection."""
    for o in list(bpy.context.selected_objects):
        o.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def _apply_modifier_params(mod, params: dict) -> tuple[bool, str | None]:
    """Set a dict of params onto a modifier, resolving object references.
    Returns (ok, error_message)."""
    for key, value in params.items():
        if key == "mod_name":
            continue
        if key in _OBJECT_REF_PARAMS and isinstance(value, str):
            ref = bpy.data.objects.get(value)
            if ref is None:
                return False, f"Referenced object {value!r} not found for param {key!r}"
            value = ref
        if not hasattr(mod, key):
            return False, f"Modifier {mod.type!r} has no parameter {key!r}"
        try:
            setattr(mod, key, value)
        except Exception as exc:
            return False, f"Failed to set {key!r}={value!r}: {exc}"
    return True, None


def _modifier_summary(mod) -> dict:
    info = {"name": mod.name, "type": mod.type}
    # Common attributes — only surface the ones that exist on this modifier.
    for key in ("levels", "render_levels", "width", "segments", "profile",
                "operation", "use_axis", "count", "thickness", "offset",
                "ratio", "angle", "strength", "wrap_method", "merge_threshold"):
        if hasattr(mod, key):
            try:
                val = getattr(mod, key)
                if hasattr(val, "__iter__") and not isinstance(val, str):
                    val = list(val)
                info[key] = val
            except Exception:
                pass
    return info


_SHADER_NODE_TYPES = {
    "BSDF_PRINCIPLED": "ShaderNodeBsdfPrincipled",
    "BSDF_GLASS": "ShaderNodeBsdfGlass",
    "BSDF_TRANSPARENT": "ShaderNodeBsdfTransparent",
    "BSDF_DIFFUSE": "ShaderNodeBsdfDiffuse",
    "BSDF_REFRACTION": "ShaderNodeBsdfRefraction",
    "EMISSION": "ShaderNodeEmission",
    "MIX_SHADER": "ShaderNodeMixShader",
    "ADD_SHADER": "ShaderNodeAddShader",
    "BACKGROUND": "ShaderNodeBackground",
    "TEX_IMAGE": "ShaderNodeTexImage",
    "TEX_ENVIRONMENT": "ShaderNodeTexEnvironment",
    "TEX_NOISE": "ShaderNodeTexNoise",
    "TEX_VORONOI": "ShaderNodeTexVoronoi",
    "TEX_WAVE": "ShaderNodeTexWave",
    "TEX_CHECKER": "ShaderNodeTexChecker",
    "TEX_GRADIENT": "ShaderNodeTexGradient",
    "TEX_BRICK": "ShaderNodeTexBrick",
    "MAPPING": "ShaderNodeMapping",
    "TEXTURE_COORD": "ShaderNodeTexCoord",
    "COLOR_RAMP": "ShaderNodeValToRGB",
    "MATH": "ShaderNodeMath",
    "VECTOR_MATH": "ShaderNodeVectorMath",
    "HUE_SAT": "ShaderNodeHueSaturation",
    "MIX_RGB": "ShaderNodeMixRGB",
    "MIX": "ShaderNodeMix",
    "BUMP": "ShaderNodeBump",
    "NORMAL_MAP": "ShaderNodeNormalMap",
    "RGB": "ShaderNodeRGB",
    "VALUE": "ShaderNodeValue",
    "INVERT": "ShaderNodeInvert",
    "GAMMA": "ShaderNodeGamma",
    "BRIGHT_CONTRAST": "ShaderNodeBrightContrast",
    "OUTPUT_MATERIAL": "ShaderNodeOutputMaterial",
    "OUTPUT_WORLD": "ShaderNodeOutputWorld",
}

# socket_key -> (principled_bsdf_input, image_colorspace, intermediate_node_type)
_MATERIAL_TEXTURE_SLOTS = {
    "BASE_COLOR":  ("Base Color",     "sRGB",      None),
    "ROUGHNESS":   ("Roughness",      "Non-Color", None),
    "METALLIC":    ("Metallic",       "Non-Color", None),
    "EMISSION":    ("Emission Color", "sRGB",      None),
    "ALPHA":       ("Alpha",          "Non-Color", None),
    "NORMAL":      ("Normal",         "Non-Color", "NORMAL_MAP"),
}


def _color4(color, default=(0.8, 0.8, 0.8, 1.0)):
    """Normalize a color argument to a 4-tuple (RGBA)."""
    if color is None:
        return default
    if len(color) == 3:
        return (float(color[0]), float(color[1]), float(color[2]), 1.0)
    if len(color) == 4:
        return (float(color[0]), float(color[1]), float(color[2]), float(color[3]))
    raise ValueError(f"color must have 3 or 4 components, got {len(color)}")


def _resolve_material(name):
    if not name:
        return None, {"ok": False, "error": "material name is required"}
    mat = bpy.data.materials.get(name)
    if mat is None:
        return None, {"ok": False, "error": f"No material named {name!r}"}
    return mat, None


def _ensure_material_nodes(mat):
    if not mat.use_nodes:
        mat.use_nodes = True
    return mat.node_tree


def _find_principled(mat):
    tree = _ensure_material_nodes(mat)
    for node in tree.nodes:
        if node.type == "BSDF_PRINCIPLED":
            return node
    return None


def _set_socket_value(node, socket_name, value):
    """Set a socket's default value by name, supporting both inputs and attrs."""
    sock = node.inputs.get(socket_name)
    if sock is None:
        return False, f"Node {node.name!r} has no input {socket_name!r}"
    try:
        if hasattr(value, "__iter__") and not isinstance(value, str):
            cur = sock.default_value
            if hasattr(cur, "__len__") and len(cur) == 4 and len(value) == 3:
                value = (*value, 1.0)
        sock.default_value = value
    except Exception as exc:
        return False, f"Failed to set {socket_name!r}={value!r}: {exc}"
    return True, None


def _set_node_param(node, param, value):
    """Set a parameter on a shader node — either an input socket default or a node attr."""
    if param in node.inputs:
        return _set_socket_value(node, param, value)
    if hasattr(node, param):
        try:
            if hasattr(value, "__iter__") and not isinstance(value, str):
                setattr(node, param, tuple(value))
            else:
                setattr(node, param, value)
        except Exception as exc:
            return False, f"Failed to set {param!r}={value!r}: {exc}"
        return True, None
    return False, f"Node {node.name!r} has no param or input {param!r}"


def _new_material_with_principled(name):
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    tree = mat.node_tree
    for n in list(tree.nodes):
        tree.nodes.remove(n)
    bsdf = tree.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (0, 0)
    output = tree.nodes.new("ShaderNodeOutputMaterial")
    output.location = (300, 0)
    tree.links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])
    return mat, bsdf


def _material_summary(mat):
    return {
        "name": mat.name,
        "use_nodes": mat.use_nodes,
        "users": mat.users,
        "node_count": len(mat.node_tree.nodes) if mat.use_nodes and mat.node_tree else 0,
    }


_LIGHT_TYPES = {"SUN", "POINT", "SPOT", "AREA"}


def _kelvin_to_rgb(kelvin):
    """Tanner Helland's black-body → sRGB approximation. Returns 0–1 RGB."""
    k = max(1000.0, min(40000.0, float(kelvin)))
    t = k / 100.0
    if t <= 66:
        r = 255.0
    else:
        r = 329.698727446 * ((t - 60) ** -0.1332047592)
    if t <= 66:
        g = 99.4708025861 * math.log(t) - 161.1195681661
    else:
        g = 288.1221695283 * ((t - 60) ** -0.0755148492)
    if t >= 66:
        b = 255.0
    elif t <= 19:
        b = 0.0
    else:
        b = 138.5177312231 * math.log(t - 10) - 305.0447927307
    return (
        max(0.0, min(255.0, r)) / 255.0,
        max(0.0, min(255.0, g)) / 255.0,
        max(0.0, min(255.0, b)) / 255.0,
    )


def _new_light(light_type: str, name: str, location):
    data = bpy.data.lights.new(name=name, type=light_type)
    obj = bpy.data.objects.new(name=name, object_data=data)
    bpy.context.collection.objects.link(obj)
    if location is not None:
        obj.location = tuple(location)
    return data, obj


def _aim_object_at(obj, target_location):
    """Rotate `obj` so its local -Z points at target_location and +Y is up."""
    direction = Vector(target_location) - Vector(obj.location)
    if direction.length_squared == 0:
        return
    rot_quat = direction.to_track_quat("-Z", "Y")
    obj.rotation_euler = rot_quat.to_euler()


def _resolve_light_object(name):
    obj, err = _resolve_object(name)
    if err:
        return None, err
    if obj.type != "LIGHT":
        return None, {"ok": False, "error": f"{name!r} is not a light"}
    return obj, None


def _resolve_camera_object(name):
    obj, err = _resolve_object(name)
    if err:
        return None, err
    if obj.type != "CAMERA":
        return None, {"ok": False, "error": f"{name!r} is not a camera"}
    return obj, None


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


# ---------- Phase 12 — edit-mode / bmesh helpers ----------

_SELECT_MODES = {"VERT", "EDGE", "FACE"}

_DELETE_CONTEXTS = {
    "VERTS": "VERTS",
    "EDGES": "EDGES",
    "FACES": "FACES",
    "ONLY_FACES": "FACES_ONLY",
    "FACES_ONLY": "FACES_ONLY",
    "FACES_KEEP_BOUNDARY": "FACES_KEEP_BOUNDARY",
    "EDGES_FACES": "EDGES_FACES",
}

_DISSOLVE_TYPES = {"VERTS", "EDGES", "FACES"}
_MERGE_MODES = {"CENTER", "FIRST", "LAST", "DISTANCE"}
_BOOLEAN_OPS = {"UNION", "DIFFERENCE", "INTERSECT"}
_BOOLEAN_SOLVERS = {"FAST", "EXACT"}
_EXTRUDE_ELEMENTS = {"VERTS", "EDGES", "FACES"}


def _resolve_mesh_object(name):
    obj, err = _resolve_object(name)
    if err:
        return None, err
    if obj.type != "MESH" or obj.data is None:
        return None, {"ok": False, "error": f"{name!r} is not a mesh"}
    return obj, None


def _load_bmesh(obj):
    """Load a BMesh from an object's mesh data.

    If the object is currently in edit mode we use ``bmesh.from_edit_mesh`` so
    changes apply to the live edit-mesh; otherwise we create a detached BMesh
    from ``obj.data``. ``_write_bmesh`` must be called with the same
    ``was_edit`` flag to commit the result correctly.
    """
    if obj.mode == "EDIT":
        bm = bmesh.from_edit_mesh(obj.data)
        return bm, True
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    return bm, False


def _write_bmesh(obj, bm, was_edit):
    bm.normal_update()
    if was_edit:
        bmesh.update_edit_mesh(obj.data)
    else:
        bm.to_mesh(obj.data)
        bm.free()
        obj.data.update()


# ---------- Phase 10b — render settings / compositor / export ----------

# Valid render-engine identifiers. Blender 4.2+ uses BLENDER_EEVEE_NEXT as
# the canonical EEVEE engine name; older "BLENDER_EEVEE" is aliased here so
# users with older muscle memory don't get an unhelpful error.
_RENDER_ENGINE_ALIASES = {
    "CYCLES": "CYCLES",
    "EEVEE": "BLENDER_EEVEE_NEXT",
    "EEVEE_NEXT": "BLENDER_EEVEE_NEXT",
    "BLENDER_EEVEE": "BLENDER_EEVEE_NEXT",
    "BLENDER_EEVEE_NEXT": "BLENDER_EEVEE_NEXT",
    "WORKBENCH": "BLENDER_WORKBENCH",
    "BLENDER_WORKBENCH": "BLENDER_WORKBENCH",
}

# View transforms accepted by scene.view_settings.view_transform. We keep a
# permissive alias table so users can say "FILMIC" even though the full name
# is "Filmic".
_VIEW_TRANSFORM_ALIASES = {
    "STANDARD": "Standard",
    "FILMIC": "Filmic",
    "FILMIC_LOG": "Filmic Log",
    "AGX": "AgX",
    "KHRONOS": "Khronos PBR Neutral",
    "KHRONOS_PBR_NEUTRAL": "Khronos PBR Neutral",
    "RAW": "Raw",
    "FALSE_COLOR": "False Color",
}


def _resolve_look(requested, view_transform, valid_looks):
    """Match a user-supplied `look` against Blender's enum.

    Blender 4.x namespaces looks by view transform (e.g. "AgX - Medium High
    Contrast", "Filmic - High Contrast"). Older callers pass the bare tail
    ("Medium High Contrast"), which no longer exists under AgX. This helper
    tries the raw value first, then reattaches / swaps the view-transform
    prefix so the old shorthand keeps working. Returns the valid enum
    identifier or None if nothing matches.
    """
    if requested is None:
        return None
    if requested in valid_looks:
        return requested
    # Strip any existing "<prefix> - " so we can rebuild it.
    tail = requested.split(" - ", 1)[1] if " - " in requested else requested
    candidates = [
        f"{view_transform} - {tail}",
        tail,
        f"{view_transform} - {requested}",
    ]
    for candidate in candidates:
        if candidate in valid_looks:
            return candidate
    # Case-insensitive fallback.
    lowered = {v.lower(): v for v in valid_looks}
    for candidate in candidates + [requested]:
        hit = lowered.get(candidate.lower())
        if hit:
            return hit
    return None

_COMPOSITOR_NODE_TYPES = {
    "RENDER_LAYERS":    "CompositorNodeRLayers",
    "COMPOSITE":        "CompositorNodeComposite",
    "VIEWER":           "CompositorNodeViewer",
    "OUTPUT_FILE":      "CompositorNodeOutputFile",
    "GLARE":            "CompositorNodeGlare",
    "BLUR":             "CompositorNodeBlur",
    "DEFOCUS":          "CompositorNodeDefocus",
    "LENS_DISTORTION":  "CompositorNodeLensdist",
    "DENOISE":          "CompositorNodeDenoise",
    "COLOR_BALANCE":    "CompositorNodeColorBalance",
    "COLOR_CORRECTION": "CompositorNodeColorCorrection",
    "CURVE_RGB":        "CompositorNodeCurveRGB",
    "HUE_SAT":          "CompositorNodeHueSat",
    "BRIGHT_CONTRAST":  "CompositorNodeBrightContrast",
    "GAMMA":            "CompositorNodeGamma",
    "INVERT":           "CompositorNodeInvert",
    "FILTER":           "CompositorNodeFilter",
    "MIX_RGB":          "CompositorNodeMixRGB",
    "MATH":             "CompositorNodeMath",
    "ELLIPSE_MASK":     "CompositorNodeEllipseMask",
    "BOX_MASK":         "CompositorNodeBoxMask",
    "VIGNETTE":         "CompositorNodeEllipseMask",  # shortcut — users multiply this against the image
    "ALPHA_OVER":       "CompositorNodeAlphaOver",
    "Z_COMBINE":        "CompositorNodeZcombine",
    "SET_ALPHA":        "CompositorNodeSetAlpha",
    "PREMUL_KEY":       "CompositorNodePremulKey",
    "TONEMAP":          "CompositorNodeTonemap",
}

# Export format → (operator, extension, kwargs-builder). Blender 4.x uses
# wm.obj_export / wm.stl_export / wm.usd_export for the new exporters and
# leaves gltf + fbx on the older `export_scene` operator namespace.
_EXPORT_FORMATS = {
    "GLB":  "gltf",
    "GLTF": "gltf",
    "FBX":  "fbx",
    "OBJ":  "obj",
    "USD":  "usd",
    "USDA": "usd",
    "USDC": "usd",
    "USDZ": "usd",
    "STL":  "stl",
}


def _run_export(fmt_key, filepath, selected_only, apply_modifiers):
    """Dispatch an export operator for a given high-level format. All paths
    are taken as absolute; the caller is responsible for os.makedirs."""
    kind = _EXPORT_FORMATS.get(fmt_key)
    if kind is None:
        raise ValueError(f"Unknown export format {fmt_key!r}. Valid: {sorted(_EXPORT_FORMATS)}")
    if kind == "gltf":
        export_format = "GLB" if fmt_key == "GLB" else "GLTF_SEPARATE"
        bpy.ops.export_scene.gltf(
            filepath=filepath,
            export_format=export_format,
            use_selection=selected_only,
            export_apply=apply_modifiers,
        )
    elif kind == "fbx":
        bpy.ops.export_scene.fbx(
            filepath=filepath,
            use_selection=selected_only,
            use_mesh_modifiers=apply_modifiers,
        )
    elif kind == "obj":
        bpy.ops.wm.obj_export(
            filepath=filepath,
            export_selected_objects=selected_only,
            apply_modifiers=apply_modifiers,
        )
    elif kind == "usd":
        bpy.ops.wm.usd_export(
            filepath=filepath,
            selected_objects_only=selected_only,
            export_animation=False,
        )
    elif kind == "stl":
        bpy.ops.wm.stl_export(
            filepath=filepath,
            export_selected_objects=selected_only,
            apply_modifiers=apply_modifiers,
        )


def _ensure_compositor_tree(scene):
    """Enable compositor nodes on a scene and return its node tree, seeding
    a Render Layers → Composite passthrough when the tree is empty."""
    scene.use_nodes = True
    tree = scene.node_tree
    if tree is None:
        return None
    if not tree.nodes:
        rlayers = tree.nodes.new("CompositorNodeRLayers")
        rlayers.location = (-300, 0)
        composite = tree.nodes.new("CompositorNodeComposite")
        composite.location = (300, 0)
        tree.links.new(rlayers.outputs["Image"], composite.inputs["Image"])
    return tree


# ---------- Phase 15 — geometry nodes helpers ----------

# Curated mapping of stable, user-facing geometry-node types to their
# bpy bl_idnames. Covers the common modeling + procedural operations —
# primitives, scatter / instance pipelines, curve ↔ mesh conversion,
# attribute IO, transforms, booleans, and the math nodes GN shares with
# shader trees.
_GEO_NODE_TYPES = {
    # Group IO (for connecting to the modifier's input/output sockets)
    "GROUP_INPUT": "NodeGroupInput",
    "GROUP_OUTPUT": "NodeGroupOutput",
    # Mesh primitives
    "MESH_CUBE": "GeometryNodeMeshCube",
    "MESH_UV_SPHERE": "GeometryNodeMeshUVSphere",
    "MESH_ICO_SPHERE": "GeometryNodeMeshIcoSphere",
    "MESH_CYLINDER": "GeometryNodeMeshCylinder",
    "MESH_CONE": "GeometryNodeMeshCone",
    "MESH_CIRCLE": "GeometryNodeMeshCircle",
    "MESH_GRID": "GeometryNodeMeshGrid",
    "MESH_TORUS": "GeometryNodeMeshTorus",
    "MESH_LINE": "GeometryNodeMeshLine",
    # Curve primitives
    "CURVE_CIRCLE": "GeometryNodeCurvePrimitiveCircle",
    "CURVE_LINE": "GeometryNodeCurvePrimitiveLine",
    "CURVE_SPIRAL": "GeometryNodeCurveSpiral",
    "CURVE_BEZIER_SEGMENT": "GeometryNodeCurvePrimitiveBezierSegment",
    # Scatter + instancing
    "DISTRIBUTE_POINTS_ON_FACES": "GeometryNodeDistributePointsOnFaces",
    "INSTANCE_ON_POINTS": "GeometryNodeInstanceOnPoints",
    "MESH_TO_POINTS": "GeometryNodeMeshToPoints",
    "POINTS_TO_VERTICES": "GeometryNodePointsToVertices",
    "REALIZE_INSTANCES": "GeometryNodeRealizeInstances",
    "ROTATE_INSTANCES": "GeometryNodeRotateInstances",
    "SCALE_INSTANCES": "GeometryNodeScaleInstances",
    "TRANSLATE_INSTANCES": "GeometryNodeTranslateInstances",
    # Mesh <-> curve
    "MESH_TO_CURVE": "GeometryNodeMeshToCurve",
    "CURVE_TO_MESH": "GeometryNodeCurveToMesh",
    # Geometry ops
    "JOIN_GEOMETRY": "GeometryNodeJoinGeometry",
    "TRANSFORM_GEOMETRY": "GeometryNodeTransform",
    "SET_POSITION": "GeometryNodeSetPosition",
    "SET_MATERIAL": "GeometryNodeSetMaterial",
    "DELETE_GEOMETRY": "GeometryNodeDeleteGeometry",
    "SEPARATE_GEOMETRY": "GeometryNodeSeparateGeometry",
    "BOUNDING_BOX": "GeometryNodeBoundBox",
    "CONVEX_HULL": "GeometryNodeConvexHull",
    "EXTRUDE_MESH": "GeometryNodeExtrudeMesh",
    "MESH_BOOLEAN": "GeometryNodeMeshBoolean",
    "SUBDIVIDE_MESH": "GeometryNodeSubdivideMesh",
    "SUBDIVISION_SURFACE": "GeometryNodeSubdivisionSurface",
    "DUAL_MESH": "GeometryNodeDualMesh",
    "FLIP_FACES": "GeometryNodeFlipFaces",
    "MERGE_BY_DISTANCE": "GeometryNodeMergeByDistance",
    # Object / collection input
    "OBJECT_INFO": "GeometryNodeObjectInfo",
    "COLLECTION_INFO": "GeometryNodeCollectionInfo",
    "SELF_OBJECT": "GeometryNodeSelfObject",
    # Inputs / attributes
    "INPUT_POSITION": "GeometryNodeInputPosition",
    "INPUT_NORMAL": "GeometryNodeInputNormal",
    "INPUT_INDEX": "GeometryNodeInputIndex",
    "INPUT_ID": "GeometryNodeInputID",
    "INPUT_RADIUS": "GeometryNodeInputRadius",
    "INPUT_SCENE_TIME": "GeometryNodeInputSceneTime",
    "NAMED_ATTRIBUTE": "GeometryNodeInputNamedAttribute",
    "STORE_NAMED_ATTRIBUTE": "GeometryNodeStoreNamedAttribute",
    # Math / utilities (Shader and Function nodes are valid inside GN trees)
    "MATH": "ShaderNodeMath",
    "VECTOR_MATH": "ShaderNodeVectorMath",
    "COMBINE_XYZ": "ShaderNodeCombineXYZ",
    "SEPARATE_XYZ": "ShaderNodeSeparateXYZ",
    "VALUE": "ShaderNodeValue",
    "COLOR_RAMP": "ShaderNodeValToRGB",
    "COMPARE": "FunctionNodeCompare",
    "BOOLEAN_MATH": "FunctionNodeBooleanMath",
    "RANDOM_VALUE": "FunctionNodeRandomValue",
    "SWITCH": "GeometryNodeSwitch",
    "NOISE_TEXTURE": "ShaderNodeTexNoise",
    "VORONOI_TEXTURE": "ShaderNodeTexVoronoi",
    "GRADIENT_TEXTURE": "ShaderNodeTexGradient",
    "MAP_RANGE": "ShaderNodeMapRange",
    "CLAMP": "ShaderNodeClamp",
}


def _resolve_node_group(group_name):
    if not group_name:
        return None, {"ok": False, "error": "group_name is required"}
    group = bpy.data.node_groups.get(group_name)
    if group is None:
        return None, {"ok": False, "error": f"No node group named {group_name!r}"}
    if group.bl_idname != "GeometryNodeTree":
        return None, {"ok": False, "error": f"{group_name!r} is not a Geometry Nodes tree"}
    return group, None


def _new_geometry_nodes_tree(name):
    """Create a new GeometryNodeTree with a Geometry → Geometry passthrough.

    Returns the tree. Uses the Blender 4.0+ ``interface`` API for declaring
    the group's input/output sockets.
    """
    tree = bpy.data.node_groups.new(name=name, type="GeometryNodeTree")
    iface = tree.interface
    iface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    iface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")

    group_in = tree.nodes.new("NodeGroupInput")
    group_in.location = (-300, 0)
    group_out = tree.nodes.new("NodeGroupOutput")
    group_out.location = (300, 0)
    tree.links.new(group_in.outputs["Geometry"], group_out.inputs["Geometry"])
    return tree


def _apply_temp_modifier(obj, mod):
    """Select `obj` and apply a single modifier in-place."""
    _select_only(obj)
    if obj.mode == "EDIT":
        bpy.ops.object.mode_set(mode="OBJECT")
    try:
        bpy.ops.object.modifier_apply(modifier=mod.name)
    except RuntimeError as exc:
        if mod.name in obj.modifiers:
            obj.modifiers.remove(obj.modifiers[mod.name])
        return f"apply failed: {exc}"
    return None


# ---------- Phase 14 — texture painting / baking helpers ----------

# Bake types supported by Cycles' bpy.ops.object.bake — value is what the op
# wants passed as `type`, and the tuple is (colorspace, is_normalish).
_BAKE_TYPES = {
    "COMBINED":     ("sRGB",       False),
    "DIFFUSE":      ("sRGB",       False),
    "GLOSSY":       ("sRGB",       False),
    "TRANSMISSION": ("sRGB",       False),
    "EMIT":         ("sRGB",       False),
    "ENVIRONMENT":  ("sRGB",       False),
    "AO":           ("Non-Color",  False),
    "SHADOW":       ("Non-Color",  False),
    "POSITION":     ("Non-Color",  False),
    "NORMAL":       ("Non-Color",  True),
    "UV":           ("Non-Color",  False),
    "ROUGHNESS":    ("Non-Color",  False),
}


def _ensure_material_for_bake(obj, image, create_if_missing=True):
    """Return a material on `obj` that has `image` plugged into an image-texture
    node, and mark that node as active — the bake op will write to whichever
    image is on the active texture node of the active material.
    """
    if not obj.material_slots or obj.material_slots[0].material is None:
        if not create_if_missing:
            return None, f"{obj.name!r} has no material; create one first"
        mat, _ = _new_material_with_principled(f"{obj.name}_BakeMat")
        if obj.data.materials:
            obj.data.materials[0] = mat
        else:
            obj.data.materials.append(mat)
    mat = obj.material_slots[0].material
    tree = _ensure_material_nodes(mat)

    tex_node = None
    for node in tree.nodes:
        if node.type == "TEX_IMAGE" and node.image is image:
            tex_node = node
            break
    if tex_node is None:
        tex_node = tree.nodes.new("ShaderNodeTexImage")
        tex_node.image = image
        # Park the baked-image node off to the side so it doesn't visually
        # clobber any existing shader graph.
        tex_node.location = (-400, -400)

    for node in tree.nodes:
        node.select = False
    tex_node.select = True
    tree.nodes.active = tex_node
    return mat, None


def _fill_image_pixels(image, color):
    """Flood-fill a Blender image with an RGBA color, per-pixel."""
    if len(color) == 3:
        color = (color[0], color[1], color[2], 1.0)
    width, height = image.size[0], image.size[1]
    if width <= 0 or height <= 0:
        return
    channels = image.channels or 4
    stripe = tuple(float(c) for c in color[:channels])
    image.pixels = list(stripe * (width * height))
    image.update()


# ---------- Phase 13 — UV unwrap / packing helpers ----------

# Unwrap methods that route through bpy.ops.uv.unwrap — each value is the
# string passed to the operator's `method` argument.
_UV_UNWRAP_METHODS = {
    "UNWRAP": "ANGLE_BASED",        # default alias
    "ANGLE_BASED": "ANGLE_BASED",
    "CONFORMAL": "CONFORMAL",
    "MINIMUM_STRETCH": "MINIMUM_STRETCH",
}

# Projection methods route through their own operator.
_UV_PROJECTION_METHODS = {
    "SMART_PROJECT",
    "CUBE_PROJECTION",
    "CYLINDER_PROJECTION",
    "SPHERE_PROJECTION",
}


def _viewport_override():
    """Return a context override dict targeting a 3D viewport, or None.

    Most UV operators refuse to run without a 3D view in context. Running
    inside a ``temp_override`` lets us call them from a background-thread job
    that the timer dispatcher relays to the main thread. On a headless Blender
    (no open windows) this returns None — callers fall back to calling the op
    bare, which is still fine on modern Blender 4.x for most UV ops.
    """
    wm = bpy.context.window_manager
    if wm is None:
        return None
    for window in wm.windows:
        screen = window.screen
        if screen is None:
            continue
        for area in screen.areas:
            if area.type != "VIEW_3D":
                continue
            for region in area.regions:
                if region.type == "WINDOW":
                    return {"window": window, "area": area, "region": region}
    return None


def _run_uv_op(fn):
    """Invoke a UV operator either inside a viewport override or bare."""
    override = _viewport_override()
    if override is not None:
        with bpy.context.temp_override(**override):
            return fn()
    return fn()


def _prepare_edit_select_all(obj):
    """Ensure `obj` is the active object, in edit mode, with everything selected.
    Returns the previous mode so the caller can restore it."""
    prev_mode = obj.mode
    _select_only(obj)
    if obj.mode != "EDIT":
        bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    return prev_mode


def _restore_mode(obj, prev_mode):
    if prev_mode != "EDIT" and obj.mode == "EDIT":
        bpy.ops.object.mode_set(mode="OBJECT")


def _collect_indexed(sequence, indices, label):
    """Look up elements by index, rejecting out-of-range ones with a clear error."""
    length = len(sequence)
    out = []
    for i in indices:
        if not isinstance(i, int) or i < 0 or i >= length:
            return None, f"{label} index {i!r} out of range [0, {length - 1}]"
        out.append(sequence[i])
    return out, None


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

    if tool == "set_transform":
        obj, err = _resolve_object(cmd.get("name"))
        if err:
            return err
        if cmd.get("location") is not None:
            obj.location = tuple(cmd["location"])
        if cmd.get("rotation") is not None:
            obj.rotation_euler = tuple(cmd["rotation"])
        if cmd.get("scale") is not None:
            obj.scale = tuple(cmd["scale"])
        return {
            "ok": True,
            "name": obj.name,
            "location": list(obj.location),
            "rotation_euler": list(obj.rotation_euler),
            "scale": list(obj.scale),
        }

    if tool == "move_object":
        obj, err = _resolve_object(cmd.get("name"))
        if err:
            return err
        delta = cmd.get("delta")
        if delta is None or len(delta) != 3:
            return {"ok": False, "error": "'delta' must be a 3-element list"}
        obj.location = (
            obj.location[0] + float(delta[0]),
            obj.location[1] + float(delta[1]),
            obj.location[2] + float(delta[2]),
        )
        return {"ok": True, "name": obj.name, "location": list(obj.location)}

    if tool == "rotate_object":
        obj, err = _resolve_object(cmd.get("name"))
        if err:
            return err
        axis = (cmd.get("axis") or "Z").upper()
        if axis not in ("X", "Y", "Z"):
            return {"ok": False, "error": f"axis must be X, Y, or Z (got {axis!r})"}
        radians = float(cmd.get("radians") or 0.0)
        idx = {"X": 0, "Y": 1, "Z": 2}[axis]
        rot = list(obj.rotation_euler)
        rot[idx] += radians
        obj.rotation_euler = rot
        return {"ok": True, "name": obj.name, "rotation_euler": list(obj.rotation_euler)}

    if tool == "scale_object":
        obj, err = _resolve_object(cmd.get("name"))
        if err:
            return err
        factor = cmd.get("factor")
        if factor is None:
            return {"ok": False, "error": "'factor' is required (number or 3-element list)"}
        if isinstance(factor, (int, float)):
            f = (float(factor),) * 3
        elif len(factor) == 3:
            f = tuple(float(v) for v in factor)
        else:
            return {"ok": False, "error": "'factor' must be a number or 3-element list"}
        obj.scale = (obj.scale[0] * f[0], obj.scale[1] * f[1], obj.scale[2] * f[2])
        return {"ok": True, "name": obj.name, "scale": list(obj.scale)}

    if tool == "set_origin":
        obj, err = _resolve_object(cmd.get("name"))
        if err:
            return err
        mode = (cmd.get("to") or "GEOMETRY").upper()
        mapping = _ORIGIN_MODES.get(mode)
        if mapping is None:
            return {"ok": False, "error": f"Unknown origin mode {mode!r}. Valid: {sorted(_ORIGIN_MODES)}"}
        op_type, center = mapping
        _select_only(obj)
        bpy.ops.object.origin_set(type=op_type, center=center)
        return {"ok": True, "name": obj.name, "location": list(obj.location)}

    if tool == "parent_to":
        child, err = _resolve_object(cmd.get("child"))
        if err:
            return err
        parent_name = cmd.get("parent")
        if parent_name is None:
            child.parent = None
            return {"ok": True, "child": child.name, "parent": None}
        parent, err = _resolve_object(parent_name)
        if err:
            return err
        if parent is child:
            return {"ok": False, "error": "Cannot parent an object to itself"}
        child.parent = parent
        # Preserve the child's world transform so it doesn't jump.
        child.matrix_parent_inverse = parent.matrix_world.inverted()
        return {"ok": True, "child": child.name, "parent": parent.name}

    if tool == "join_objects":
        names = cmd.get("names") or []
        if len(names) < 2:
            return {"ok": False, "error": "'names' must list at least 2 objects (target first)"}
        objs = []
        for n in names:
            o, err = _resolve_object(n)
            if err:
                return err
            objs.append(o)
        target_type = objs[0].type
        for o in objs[1:]:
            if o.type != target_type:
                return {
                    "ok": False,
                    "error": f"All objects must be the same type; {o.name!r} is {o.type}, target is {target_type}",
                }
        for o in list(bpy.context.selected_objects):
            o.select_set(False)
        for o in objs:
            o.select_set(True)
        bpy.context.view_layer.objects.active = objs[0]
        bpy.ops.object.join()
        return {"ok": True, "joined_into": objs[0].name, "count": len(objs)}

    if tool == "add_modifier":
        obj, err = _resolve_object(cmd.get("name"))
        if err:
            return err
        mod_type = (cmd.get("type") or "").upper()
        if not mod_type:
            return {"ok": False, "error": "'type' is required (e.g. SUBSURF, BEVEL, BOOLEAN, MIRROR, ARRAY)"}
        params = dict(cmd.get("params") or {})
        desired_name = params.pop("mod_name", None) or cmd.get("mod_name") or mod_type.title()
        try:
            mod = obj.modifiers.new(name=desired_name, type=mod_type)
        except (TypeError, RuntimeError) as exc:
            return {"ok": False, "error": f"Could not create modifier {mod_type!r}: {exc}"}
        ok, err_msg = _apply_modifier_params(mod, params)
        if not ok:
            obj.modifiers.remove(mod)
            return {"ok": False, "error": err_msg}
        return {"ok": True, "name": obj.name, "modifier": _modifier_summary(mod)}

    if tool == "apply_modifier":
        obj, err = _resolve_object(cmd.get("name"))
        if err:
            return err
        mod_name = cmd.get("modifier_name")
        if not mod_name or mod_name not in obj.modifiers:
            return {"ok": False, "error": f"No modifier named {mod_name!r} on {obj.name!r}"}
        _select_only(obj)
        try:
            bpy.ops.object.modifier_apply(modifier=mod_name)
        except RuntimeError as exc:
            return {"ok": False, "error": f"modifier_apply failed: {exc}"}
        return {"ok": True, "name": obj.name, "applied": mod_name}

    if tool == "remove_modifier":
        obj, err = _resolve_object(cmd.get("name"))
        if err:
            return err
        mod_name = cmd.get("modifier_name")
        mod = obj.modifiers.get(mod_name) if mod_name else None
        if mod is None:
            return {"ok": False, "error": f"No modifier named {mod_name!r} on {obj.name!r}"}
        obj.modifiers.remove(mod)
        return {"ok": True, "name": obj.name, "removed": mod_name}

    if tool == "set_modifier_param":
        obj, err = _resolve_object(cmd.get("name"))
        if err:
            return err
        mod_name = cmd.get("modifier_name")
        mod = obj.modifiers.get(mod_name) if mod_name else None
        if mod is None:
            return {"ok": False, "error": f"No modifier named {mod_name!r} on {obj.name!r}"}
        params = cmd.get("params")
        if not isinstance(params, dict) or not params:
            return {"ok": False, "error": "'params' must be a non-empty dict"}
        ok, err_msg = _apply_modifier_params(mod, params)
        if not ok:
            return {"ok": False, "error": err_msg}
        return {"ok": True, "name": obj.name, "modifier": _modifier_summary(mod)}

    if tool == "reorder_modifier":
        obj, err = _resolve_object(cmd.get("name"))
        if err:
            return err
        mod_name = cmd.get("modifier_name")
        if not mod_name or mod_name not in obj.modifiers:
            return {"ok": False, "error": f"No modifier named {mod_name!r} on {obj.name!r}"}
        index = int(cmd.get("index", -1))
        if index < 0 or index >= len(obj.modifiers):
            return {"ok": False, "error": f"'index' must be in [0, {len(obj.modifiers) - 1}]"}
        _select_only(obj)
        try:
            bpy.ops.object.modifier_move_to_index(modifier=mod_name, index=index)
        except RuntimeError as exc:
            return {"ok": False, "error": f"modifier_move_to_index failed: {exc}"}
        return {
            "ok": True,
            "name": obj.name,
            "modifier_order": [m.name for m in obj.modifiers],
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

    # ---------- Phase 7 — Materials + shader node graph ----------

    if tool == "create_material":
        name = cmd.get("name") or "Material"
        if bpy.data.materials.get(name):
            return {"ok": False, "error": f"Material named {name!r} already exists"}
        base_color = _color4(cmd.get("base_color"), default=(0.8, 0.8, 0.8, 1.0))
        roughness = float(cmd.get("roughness", 0.5))
        metallic = float(cmd.get("metallic", 0.0))
        mat, bsdf = _new_material_with_principled(name)
        bsdf.inputs["Base Color"].default_value = base_color
        bsdf.inputs["Roughness"].default_value = roughness
        bsdf.inputs["Metallic"].default_value = metallic
        return {"ok": True, "material": _material_summary(mat)}

    if tool == "create_pbr_material":
        name = cmd.get("name") or "PBR_Material"
        if bpy.data.materials.get(name):
            return {"ok": False, "error": f"Material named {name!r} already exists"}
        base_color = _color4(cmd.get("base_color"), default=(0.8, 0.8, 0.8, 1.0))
        roughness = float(cmd.get("roughness", 0.5))
        metallic = float(cmd.get("metallic", 0.0))
        emission = _color4(cmd.get("emission"), default=(0.0, 0.0, 0.0, 1.0))
        emission_strength = float(cmd.get("emission_strength", 0.0))
        mat, bsdf = _new_material_with_principled(name)
        bsdf.inputs["Base Color"].default_value = base_color
        bsdf.inputs["Roughness"].default_value = roughness
        bsdf.inputs["Metallic"].default_value = metallic
        if "Emission Color" in bsdf.inputs:
            bsdf.inputs["Emission Color"].default_value = emission
        elif "Emission" in bsdf.inputs:
            bsdf.inputs["Emission"].default_value = emission
        if "Emission Strength" in bsdf.inputs:
            bsdf.inputs["Emission Strength"].default_value = emission_strength
        return {"ok": True, "material": _material_summary(mat)}

    if tool == "create_glass_material":
        name = cmd.get("name") or "Glass"
        if bpy.data.materials.get(name):
            return {"ok": False, "error": f"Material named {name!r} already exists"}
        color = _color4(cmd.get("color"), default=(1.0, 1.0, 1.0, 1.0))
        ior = float(cmd.get("ior", 1.45))
        roughness = float(cmd.get("roughness", 0.0))
        transmission = float(cmd.get("transmission", 1.0))
        mat, bsdf = _new_material_with_principled(name)
        bsdf.inputs["Base Color"].default_value = color
        bsdf.inputs["Roughness"].default_value = roughness
        bsdf.inputs["IOR"].default_value = ior
        # Transmission socket is called "Transmission Weight" in 4.x
        tr_socket = (
            bsdf.inputs.get("Transmission Weight")
            or bsdf.inputs.get("Transmission")
        )
        if tr_socket is not None:
            tr_socket.default_value = transmission
        mat.use_screen_refraction = True
        return {"ok": True, "material": _material_summary(mat)}

    if tool == "assign_material":
        obj, err = _resolve_object(cmd.get("object_name"))
        if err:
            return err
        mat, err = _resolve_material(cmd.get("material_name"))
        if err:
            return err
        if obj.data is None or not hasattr(obj.data, "materials"):
            return {"ok": False, "error": f"{obj.name!r} has no material slots"}
        slot_index = int(cmd.get("slot_index", 0))
        slots = obj.data.materials
        while len(slots) <= slot_index:
            slots.append(None)
        slots[slot_index] = mat
        return {
            "ok": True,
            "object": obj.name,
            "material": mat.name,
            "slot_index": slot_index,
            "slot_count": len(slots),
        }

    if tool == "assign_material_to_faces":
        obj, err = _resolve_object(cmd.get("object_name"))
        if err:
            return err
        mat, err = _resolve_material(cmd.get("material_name"))
        if err:
            return err
        if obj.type != "MESH" or obj.data is None:
            return {"ok": False, "error": f"{obj.name!r} is not a mesh"}
        face_indices = cmd.get("face_indices")
        if not isinstance(face_indices, list) or not face_indices:
            return {"ok": False, "error": "'face_indices' must be a non-empty list of ints"}
        slots = obj.data.materials
        slot_index = None
        for i, existing in enumerate(slots):
            if existing is not None and existing.name == mat.name:
                slot_index = i
                break
        if slot_index is None:
            slots.append(mat)
            slot_index = len(slots) - 1
        polygons = obj.data.polygons
        poly_count = len(polygons)
        assigned = 0
        for fi in face_indices:
            if not isinstance(fi, int) or fi < 0 or fi >= poly_count:
                return {"ok": False, "error": f"face index {fi} out of range [0, {poly_count - 1}]"}
            polygons[fi].material_index = slot_index
            assigned += 1
        obj.data.update()
        return {
            "ok": True,
            "object": obj.name,
            "material": mat.name,
            "slot_index": slot_index,
            "faces_assigned": assigned,
        }

    if tool == "list_materials":
        mats = [_material_summary(m) for m in bpy.data.materials]
        return {"ok": True, "count": len(mats), "materials": mats}

    if tool == "duplicate_material":
        src, err = _resolve_material(cmd.get("name"))
        if err:
            return err
        new_name = cmd.get("new_name")
        if not new_name:
            return {"ok": False, "error": "'new_name' is required"}
        if bpy.data.materials.get(new_name):
            return {"ok": False, "error": f"Material named {new_name!r} already exists"}
        copy = src.copy()
        copy.name = new_name
        return {"ok": True, "material": _material_summary(copy)}

    if tool == "set_world_hdri":
        path = cmd.get("path")
        if not path:
            return {"ok": False, "error": "'path' is required (local file path)"}
        abs_path = os.path.abspath(os.path.expanduser(path))
        if not os.path.exists(abs_path):
            return {"ok": False, "error": f"HDRI file not found: {abs_path}"}
        strength = float(cmd.get("strength", 1.0))
        rotation = cmd.get("rotation", 0.0)
        if isinstance(rotation, (int, float)):
            rotation_euler = (0.0, 0.0, float(rotation))
        elif len(rotation) == 3:
            rotation_euler = tuple(float(v) for v in rotation)
        else:
            return {"ok": False, "error": "'rotation' must be a number (Z) or 3-element list"}

        scene = bpy.context.scene
        world = scene.world
        if world is None:
            world = bpy.data.worlds.new(name="World")
            scene.world = world
        world.use_nodes = True
        tree = world.node_tree
        for n in list(tree.nodes):
            tree.nodes.remove(n)
        tex_coord = tree.nodes.new("ShaderNodeTexCoord")
        tex_coord.location = (-800, 0)
        mapping = tree.nodes.new("ShaderNodeMapping")
        mapping.location = (-600, 0)
        mapping.inputs["Rotation"].default_value = rotation_euler
        env = tree.nodes.new("ShaderNodeTexEnvironment")
        env.location = (-400, 0)
        try:
            env.image = bpy.data.images.load(abs_path, check_existing=True)
        except Exception as exc:
            return {"ok": False, "error": f"Failed to load HDRI: {exc}"}
        background = tree.nodes.new("ShaderNodeBackground")
        background.location = (-100, 0)
        background.inputs["Strength"].default_value = strength
        output = tree.nodes.new("ShaderNodeOutputWorld")
        output.location = (150, 0)
        tree.links.new(tex_coord.outputs["Generated"], mapping.inputs["Vector"])
        tree.links.new(mapping.outputs["Vector"], env.inputs["Vector"])
        tree.links.new(env.outputs["Color"], background.inputs["Color"])
        tree.links.new(background.outputs["Background"], output.inputs["Surface"])
        return {
            "ok": True,
            "path": abs_path,
            "strength": strength,
            "rotation": list(rotation_euler),
        }

    if tool == "load_image_texture":
        name = cmd.get("name")
        path = cmd.get("path")
        if not path:
            return {"ok": False, "error": "'path' is required (local file path)"}
        abs_path = os.path.abspath(os.path.expanduser(path))
        if not os.path.exists(abs_path):
            return {"ok": False, "error": f"Image file not found: {abs_path}"}
        try:
            image = bpy.data.images.load(abs_path, check_existing=True)
        except Exception as exc:
            return {"ok": False, "error": f"Failed to load image: {exc}"}
        if name:
            image.name = name
        return {
            "ok": True,
            "name": image.name,
            "path": abs_path,
            "size": [image.size[0], image.size[1]],
            "channels": image.channels,
        }

    if tool == "set_material_texture":
        mat, err = _resolve_material(cmd.get("material_name"))
        if err:
            return err
        socket_key = (cmd.get("socket") or "").upper()
        slot_spec = _MATERIAL_TEXTURE_SLOTS.get(socket_key)
        if slot_spec is None:
            return {
                "ok": False,
                "error": f"Unknown socket {socket_key!r}. Valid: {sorted(_MATERIAL_TEXTURE_SLOTS)}",
            }
        bsdf_input_name, colorspace, intermediate = slot_spec
        texture_name = cmd.get("texture_name")
        if not texture_name:
            return {"ok": False, "error": "'texture_name' is required"}
        image = bpy.data.images.get(texture_name)
        if image is None:
            return {"ok": False, "error": f"No image texture named {texture_name!r} (load it first with load_image_texture)"}

        tree = _ensure_material_nodes(mat)
        bsdf = _find_principled(mat)
        if bsdf is None:
            return {"ok": False, "error": f"Material {mat.name!r} has no Principled BSDF node"}
        if bsdf_input_name not in bsdf.inputs:
            return {"ok": False, "error": f"Principled BSDF has no input {bsdf_input_name!r}"}

        tex = tree.nodes.new("ShaderNodeTexImage")
        tex.image = image
        try:
            tex.image.colorspace_settings.name = colorspace
        except Exception:
            pass
        tex.location = (bsdf.location.x - 600, bsdf.location.y - 100)

        uv_map = cmd.get("uv_map")
        if uv_map:
            uv_node = tree.nodes.new("ShaderNodeUVMap")
            uv_node.uv_map = uv_map
            uv_node.location = (tex.location.x - 250, tex.location.y)
            tree.links.new(uv_node.outputs["UV"], tex.inputs["Vector"])

        if intermediate == "NORMAL_MAP":
            nmap = tree.nodes.new("ShaderNodeNormalMap")
            nmap.location = (tex.location.x + 300, tex.location.y)
            tree.links.new(tex.outputs["Color"], nmap.inputs["Color"])
            tree.links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])
        else:
            out_socket = "Alpha" if socket_key == "ALPHA" else "Color"
            tree.links.new(tex.outputs[out_socket], bsdf.inputs[bsdf_input_name])

        return {
            "ok": True,
            "material": mat.name,
            "socket": socket_key,
            "texture": image.name,
            "node": tex.name,
        }

    if tool == "add_shader_node":
        mat, err = _resolve_material(cmd.get("material_name"))
        if err:
            return err
        node_type_key = (cmd.get("node_type") or "").upper()
        bl_idname = _SHADER_NODE_TYPES.get(node_type_key)
        if bl_idname is None:
            return {
                "ok": False,
                "error": f"Unknown node_type {node_type_key!r}. Valid: {sorted(_SHADER_NODE_TYPES)}",
            }
        tree = _ensure_material_nodes(mat)
        try:
            node = tree.nodes.new(bl_idname)
        except RuntimeError as exc:
            return {"ok": False, "error": f"Failed to create {node_type_key}: {exc}"}
        location = cmd.get("location")
        if location and len(location) == 2:
            node.location = (float(location[0]), float(location[1]))
        desired_name = cmd.get("name")
        if desired_name:
            node.name = desired_name
            node.label = desired_name
        return {
            "ok": True,
            "material": mat.name,
            "node": {
                "name": node.name,
                "type": node_type_key,
                "bl_idname": node.bl_idname,
                "location": [node.location.x, node.location.y],
                "inputs": [s.name for s in node.inputs],
                "outputs": [s.name for s in node.outputs],
            },
        }

    if tool == "connect_shader_nodes":
        mat, err = _resolve_material(cmd.get("material_name"))
        if err:
            return err
        tree = _ensure_material_nodes(mat)
        from_node = tree.nodes.get(cmd.get("from_node"))
        to_node = tree.nodes.get(cmd.get("to_node"))
        if from_node is None:
            return {"ok": False, "error": f"No node named {cmd.get('from_node')!r} in {mat.name!r}"}
        if to_node is None:
            return {"ok": False, "error": f"No node named {cmd.get('to_node')!r} in {mat.name!r}"}
        from_socket_name = cmd.get("from_socket")
        to_socket_name = cmd.get("to_socket")
        from_socket = from_node.outputs.get(from_socket_name)
        if from_socket is None:
            return {"ok": False, "error": f"{from_node.name!r} has no output {from_socket_name!r}. Available: {[s.name for s in from_node.outputs]}"}
        to_socket = to_node.inputs.get(to_socket_name)
        if to_socket is None:
            return {"ok": False, "error": f"{to_node.name!r} has no input {to_socket_name!r}. Available: {[s.name for s in to_node.inputs]}"}
        link = tree.links.new(from_socket, to_socket)
        return {
            "ok": True,
            "material": mat.name,
            "from": f"{from_node.name}.{from_socket.name}",
            "to": f"{to_node.name}.{to_socket.name}",
            "valid": link.is_valid,
        }

    if tool == "disconnect_shader_nodes":
        mat, err = _resolve_material(cmd.get("material_name"))
        if err:
            return err
        tree = _ensure_material_nodes(mat)
        from_name = cmd.get("from_node")
        to_name = cmd.get("to_node")
        from_socket = cmd.get("from_socket")
        to_socket = cmd.get("to_socket")
        removed = 0
        for link in list(tree.links):
            if (link.from_node.name == from_name
                    and link.to_node.name == to_name
                    and link.from_socket.name == from_socket
                    and link.to_socket.name == to_socket):
                tree.links.remove(link)
                removed += 1
        if removed == 0:
            return {"ok": False, "error": "No matching link found"}
        return {"ok": True, "material": mat.name, "removed": removed}

    if tool == "set_shader_node_param":
        mat, err = _resolve_material(cmd.get("material_name"))
        if err:
            return err
        tree = _ensure_material_nodes(mat)
        node = tree.nodes.get(cmd.get("node"))
        if node is None:
            return {"ok": False, "error": f"No node named {cmd.get('node')!r} in {mat.name!r}"}
        params = cmd.get("params")
        if not isinstance(params, dict) or not params:
            return {"ok": False, "error": "'params' must be a non-empty dict"}
        for key, value in params.items():
            ok, err_msg = _set_node_param(node, key, value)
            if not ok:
                return {"ok": False, "error": err_msg}
        return {"ok": True, "material": mat.name, "node": node.name}

    # ---------- Phase 8 — Lights, cameras, studio presets ----------

    if tool == "add_light":
        light_type = (cmd.get("type") or "AREA").upper()
        if light_type not in _LIGHT_TYPES:
            return {"ok": False, "error": f"Unknown light type {light_type!r}. Valid: {sorted(_LIGHT_TYPES)}"}
        name = cmd.get("name") or f"{light_type.title()}Light"
        location = cmd.get("location") or (0.0, 0.0, 3.0)
        energy = cmd.get("energy")
        color = cmd.get("color")
        data, obj = _new_light(light_type, name, location)
        if energy is not None:
            data.energy = float(energy)
        if color is not None:
            c = _color4(color)
            data.color = (c[0], c[1], c[2])
        return {
            "ok": True,
            "name": obj.name,
            "type": light_type,
            "location": list(obj.location),
            "energy": data.energy,
            "color": list(data.color),
        }

    if tool == "set_light_properties":
        obj, err = _resolve_light_object(cmd.get("name"))
        if err:
            return err
        data = obj.data
        if cmd.get("energy") is not None:
            data.energy = float(cmd["energy"])
        if cmd.get("color") is not None:
            c = _color4(cmd["color"])
            data.color = (c[0], c[1], c[2])
        if cmd.get("temperature_kelvin") is not None:
            data.color = _kelvin_to_rgb(cmd["temperature_kelvin"])
        size = cmd.get("size")
        if size is not None:
            if data.type == "AREA":
                if isinstance(size, (int, float)):
                    data.size = float(size)
                elif len(size) == 2:
                    data.shape = "RECTANGLE"
                    data.size = float(size[0])
                    data.size_y = float(size[1])
            elif data.type == "SUN":
                data.angle = float(size)
            elif data.type in ("POINT", "SPOT"):
                data.shadow_soft_size = float(size)
        return {
            "ok": True,
            "name": obj.name,
            "energy": data.energy,
            "color": list(data.color),
        }

    if tool == "add_camera":
        name = cmd.get("name") or "Camera"
        location = cmd.get("location") or (7.0, -7.0, 5.0)
        target = cmd.get("target")
        lens_mm = cmd.get("lens_mm")
        data = bpy.data.cameras.new(name=name)
        obj = bpy.data.objects.new(name=name, object_data=data)
        bpy.context.collection.objects.link(obj)
        obj.location = tuple(location)
        if lens_mm is not None:
            data.lens = float(lens_mm)
        if target is not None:
            if isinstance(target, str):
                target_obj, err = _resolve_object(target)
                if err:
                    bpy.data.objects.remove(obj, do_unlink=True)
                    bpy.data.cameras.remove(data)
                    return err
                _aim_object_at(obj, target_obj.location)
            else:
                _aim_object_at(obj, tuple(target))
        return {
            "ok": True,
            "name": obj.name,
            "location": list(obj.location),
            "rotation_euler": list(obj.rotation_euler),
            "lens": data.lens,
        }

    if tool == "set_active_camera":
        obj, err = _resolve_camera_object(cmd.get("name"))
        if err:
            return err
        bpy.context.scene.camera = obj
        return {"ok": True, "active_camera": obj.name}

    if tool == "set_camera_properties":
        obj, err = _resolve_camera_object(cmd.get("name"))
        if err:
            return err
        data = obj.data
        if cmd.get("lens_mm") is not None:
            data.lens = float(cmd["lens_mm"])
        dof_distance = cmd.get("dof_distance")
        fstop = cmd.get("fstop")
        if dof_distance is not None:
            data.dof.use_dof = True
            data.dof.focus_distance = float(dof_distance)
        if fstop is not None:
            data.dof.use_dof = True
            data.dof.aperture_fstop = float(fstop)
        return {
            "ok": True,
            "name": obj.name,
            "lens": data.lens,
            "use_dof": data.dof.use_dof,
            "focus_distance": data.dof.focus_distance,
            "aperture_fstop": data.dof.aperture_fstop,
        }

    if tool == "setup_three_point_lighting":
        subject_name = cmd.get("subject_name")
        subject, err = _resolve_object(subject_name)
        if err:
            return err
        distance = float(cmd.get("distance") or 5.0)
        energy = float(cmd.get("energy") or 500.0)
        # Use the subject's bounding box center as the aim target so off-origin
        # objects still get framed correctly.
        center = subject.matrix_world.translation
        dims = subject.dimensions
        height_offset = max(float(dims.z) * 0.6, 1.0)

        def _add_area(name, offset, size, energy_scale):
            loc = (center.x + offset[0], center.y + offset[1], center.z + offset[2])
            data, obj = _new_light("AREA", name, loc)
            data.size = size
            data.energy = energy * energy_scale
            data.color = (1.0, 1.0, 1.0)
            _aim_object_at(obj, center)
            return obj.name

        key_name = _add_area(
            f"Key_{subject.name}",
            (distance * 0.9, -distance * 0.7, height_offset),
            size=distance * 0.6,
            energy_scale=1.0,
        )
        fill_name = _add_area(
            f"Fill_{subject.name}",
            (-distance * 0.9, -distance * 0.5, height_offset * 0.4),
            size=distance * 1.2,
            energy_scale=0.35,
        )
        rim_name = _add_area(
            f"Rim_{subject.name}",
            (-distance * 0.2, distance * 1.0, height_offset * 1.2),
            size=distance * 0.3,
            energy_scale=0.6,
        )
        return {
            "ok": True,
            "subject": subject.name,
            "lights": [key_name, fill_name, rim_name],
        }

    if tool == "setup_product_studio":
        subject_name = cmd.get("subject_name")
        subject, err = _resolve_object(subject_name)
        if err:
            return err
        style = (cmd.get("style") or "SOFTBOX").upper()
        distance = float(cmd.get("distance") or 4.0)
        valid_styles = {"SOFTBOX", "HARD_LIGHT", "HIGH_KEY", "LOW_KEY"}
        if style not in valid_styles:
            return {"ok": False, "error": f"Unknown style {style!r}. Valid: {sorted(valid_styles)}"}

        center = subject.matrix_world.translation
        lights = []

        def _add(name, ltype, offset, energy, size, color=(1.0, 1.0, 1.0)):
            loc = (center.x + offset[0], center.y + offset[1], center.z + offset[2])
            data, obj = _new_light(ltype, name, loc)
            if ltype == "AREA":
                data.size = size
            elif ltype == "SUN":
                data.angle = math.radians(size)
            else:
                data.shadow_soft_size = size
            data.energy = energy
            data.color = color
            _aim_object_at(obj, center)
            lights.append(obj.name)

        prefix = subject.name
        if style == "SOFTBOX":
            _add(f"Softbox_Top_{prefix}", "AREA", (0, 0, distance * 1.2), 800.0, distance * 1.5)
            _add(f"Softbox_Left_{prefix}", "AREA", (-distance, -distance * 0.6, distance * 0.4), 400.0, distance * 1.2)
            _add(f"Softbox_Right_{prefix}", "AREA", (distance, -distance * 0.6, distance * 0.4), 400.0, distance * 1.2)
        elif style == "HARD_LIGHT":
            _add(f"Hard_Key_{prefix}", "SPOT", (distance * 0.8, -distance * 0.8, distance * 1.2), 1500.0, 0.05)
            _add(f"Hard_Fill_{prefix}", "AREA", (-distance * 0.9, -distance * 0.5, distance * 0.3), 150.0, distance * 0.8)
        elif style == "HIGH_KEY":
            _add(f"High_Top_{prefix}", "AREA", (0, 0, distance * 1.2), 1200.0, distance * 2.0)
            _add(f"High_Front_{prefix}", "AREA", (0, -distance, distance * 0.4), 800.0, distance * 1.8)
            _add(f"High_Left_{prefix}", "AREA", (-distance, 0, distance * 0.4), 600.0, distance * 1.5)
            _add(f"High_Right_{prefix}", "AREA", (distance, 0, distance * 0.4), 600.0, distance * 1.5)
        else:  # LOW_KEY
            _add(f"Low_Rim_{prefix}", "AREA", (-distance * 0.3, distance * 1.1, distance * 1.0), 700.0, distance * 0.4)
            _add(f"Low_Fill_{prefix}", "AREA", (distance * 0.9, -distance * 0.6, distance * 0.2), 60.0, distance * 1.0)

        return {"ok": True, "subject": subject.name, "style": style, "lights": lights}

    if tool == "frame_product_shot":
        subject, err = _resolve_object(cmd.get("subject_name"))
        if err:
            return err

        angle = (cmd.get("angle") or "FRONT").upper()
        composition = (cmd.get("composition") or "GOLDEN_RATIO").upper()
        lens_mm = float(cmd.get("lens_mm") or 85)
        padding = float(cmd.get("padding") or 1.3)  # 1.0 = tight, 1.5 = loose

        # Calculate object bounds in world space.
        bbox_corners = [subject.matrix_world @ Vector(c) for c in subject.bound_box]
        bbox_min = Vector((
            min(c.x for c in bbox_corners),
            min(c.y for c in bbox_corners),
            min(c.z for c in bbox_corners),
        ))
        bbox_max = Vector((
            max(c.x for c in bbox_corners),
            max(c.y for c in bbox_corners),
            max(c.z for c in bbox_corners),
        ))
        center = (bbox_min + bbox_max) / 2
        dims = bbox_max - bbox_min
        max_dim = max(dims.x, dims.y, dims.z)

        # Camera distance so the object fills the frame with padding.
        # d = (size/2) / tan(fov/2)
        fov_rad = 2 * math.atan(18.0 / lens_mm)  # 36mm sensor half-width
        cam_distance = (max_dim * padding / 2) / math.tan(fov_rad / 2)

        # Camera position based on angle preset.
        angle_presets = {
            "FRONT":       (0, -1, 0.3),
            "FRONT_HIGH":  (0, -1, 0.7),
            "THREE_QUARTER": (0.7, -1, 0.5),
            "SIDE":        (1, 0, 0.3),
            "TOP":         (0, -0.1, 1),
            "HERO":        (0.5, -1, 0.35),
        }
        if angle not in angle_presets:
            return {"ok": False, "error": f"Unknown angle {angle!r}. Valid: {sorted(angle_presets)}"}
        dx, dy, dz = angle_presets[angle]
        dir_vec = Vector((dx, dy, dz)).normalized()
        cam_loc = center + dir_vec * cam_distance

        # Composition offset — shift the aim point off-center.
        # Golden ratio: subject at ~1/1.618 of the frame.
        aim_target = Vector(center)
        if composition == "GOLDEN_RATIO":
            # Offset aim slightly so subject sits at golden ratio intersection.
            golden = 1.0 / 1.618
            offset_x = dims.x * (golden - 0.5) * 0.3
            aim_target.x += offset_x
        elif composition == "RULE_OF_THIRDS":
            offset_x = dims.x * (1/3 - 0.5) * 0.3
            aim_target.x += offset_x
        # CENTER = no offset

        # Create or reuse camera.
        cam_name = cmd.get("camera_name") or "ProductCamera"
        cam_obj = bpy.data.objects.get(cam_name)
        if cam_obj and cam_obj.type == 'CAMERA':
            cam_obj.location = cam_loc
            cam_obj.data.lens = lens_mm
        else:
            cam_data = bpy.data.cameras.new(name=cam_name)
            cam_data.lens = lens_mm
            cam_obj = bpy.data.objects.new(cam_name, cam_data)
            bpy.context.collection.objects.link(cam_obj)
            cam_obj.location = cam_loc

        _aim_object_at(cam_obj, aim_target)
        bpy.context.scene.camera = cam_obj

        # Set render resolution — product photography aspect ratios.
        aspect = (cmd.get("aspect") or "4:5").replace(" ", "")
        aspect_map = {
            "1:1": (1080, 1080),
            "4:5": (1080, 1350),
            "3:4": (1080, 1440),
            "16:9": (1920, 1080),
            "9:16": (1080, 1920),
        }
        if aspect in aspect_map:
            w, h = aspect_map[aspect]
            bpy.context.scene.render.resolution_x = w
            bpy.context.scene.render.resolution_y = h

        return {
            "ok": True,
            "message": f"Product shot framed: {angle} angle, {composition} composition, {lens_mm}mm lens",
            "camera": cam_obj.name,
            "camera_location": list(cam_obj.location),
            "aim_target": list(aim_target),
            "distance": round(cam_distance, 2),
            "lens_mm": lens_mm,
            "angle": angle,
            "composition": composition,
            "aspect": aspect,
        }

    # ---------- Phase 12 — Edit mode / hard-surface modeling ----------

    if tool == "enter_edit_mode":
        obj, err = _resolve_mesh_object(cmd.get("name"))
        if err:
            return err
        _select_only(obj)
        if obj.mode != "EDIT":
            bpy.ops.object.mode_set(mode="EDIT")
        return {"ok": True, "name": obj.name, "mode": obj.mode}

    if tool == "exit_edit_mode":
        # Exit whatever active object is currently in edit mode.
        active = bpy.context.view_layer.objects.active
        if active is not None and active.mode == "EDIT":
            bpy.ops.object.mode_set(mode="OBJECT")
        return {"ok": True, "mode": active.mode if active else "OBJECT"}

    if tool == "set_select_mode":
        mode = (cmd.get("mode") or "").upper()
        if mode not in _SELECT_MODES:
            return {"ok": False, "error": f"mode must be one of {sorted(_SELECT_MODES)}"}
        active = bpy.context.view_layer.objects.active
        if active is None or active.mode != "EDIT":
            return {"ok": False, "error": "No object is in edit mode. Call enter_edit_mode first."}
        bpy.ops.mesh.select_mode(type=mode)
        return {"ok": True, "mode": mode}

    if tool == "select_all":
        obj, err = _resolve_mesh_object(cmd.get("name"))
        if err:
            return err
        _select_only(obj)
        if obj.mode != "EDIT":
            bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        return {"ok": True, "name": obj.name}

    if tool == "deselect_all":
        active = bpy.context.view_layer.objects.active
        if active is not None and active.mode == "EDIT":
            bpy.ops.mesh.select_all(action="DESELECT")
            return {"ok": True, "name": active.name}
        return {"ok": False, "error": "No object is in edit mode."}

    if tool == "extrude_faces":
        obj, err = _resolve_mesh_object(cmd.get("name"))
        if err:
            return err
        indices = cmd.get("face_indices")
        if not isinstance(indices, list) or not indices:
            return {"ok": False, "error": "'face_indices' must be a non-empty list"}
        vector = cmd.get("vector") or (0.0, 0.0, 0.0)
        if len(vector) != 3:
            return {"ok": False, "error": "'vector' must be a 3-element list"}
        bm, was_edit = _load_bmesh(obj)
        try:
            bm.faces.ensure_lookup_table()
            faces, msg = _collect_indexed(bm.faces, indices, "face")
            if faces is None:
                return {"ok": False, "error": msg}
            result = bmesh.ops.extrude_face_region(bm, geom=faces)
            new_verts = [g for g in result["geom"] if isinstance(g, bmesh.types.BMVert)]
            bmesh.ops.translate(bm, vec=Vector(tuple(float(v) for v in vector)), verts=new_verts)
        finally:
            _write_bmesh(obj, bm, was_edit)
        return {"ok": True, "name": obj.name, "extruded_faces": len(indices)}

    if tool == "extrude_edges":
        obj, err = _resolve_mesh_object(cmd.get("name"))
        if err:
            return err
        indices = cmd.get("edge_indices")
        if not isinstance(indices, list) or not indices:
            return {"ok": False, "error": "'edge_indices' must be a non-empty list"}
        vector = cmd.get("vector") or (0.0, 0.0, 0.0)
        bm, was_edit = _load_bmesh(obj)
        try:
            bm.edges.ensure_lookup_table()
            edges, msg = _collect_indexed(bm.edges, indices, "edge")
            if edges is None:
                return {"ok": False, "error": msg}
            result = bmesh.ops.extrude_edge_only(bm, edges=edges)
            new_verts = [g for g in result["geom"] if isinstance(g, bmesh.types.BMVert)]
            bmesh.ops.translate(bm, vec=Vector(tuple(float(v) for v in vector)), verts=new_verts)
        finally:
            _write_bmesh(obj, bm, was_edit)
        return {"ok": True, "name": obj.name, "extruded_edges": len(indices)}

    if tool == "extrude_vertices":
        obj, err = _resolve_mesh_object(cmd.get("name"))
        if err:
            return err
        indices = cmd.get("vertex_indices")
        if not isinstance(indices, list) or not indices:
            return {"ok": False, "error": "'vertex_indices' must be a non-empty list"}
        vector = cmd.get("vector") or (0.0, 0.0, 0.0)
        bm, was_edit = _load_bmesh(obj)
        try:
            bm.verts.ensure_lookup_table()
            verts, msg = _collect_indexed(bm.verts, indices, "vertex")
            if verts is None:
                return {"ok": False, "error": msg}
            result = bmesh.ops.extrude_vert_indiv(bm, verts=verts)
            new_verts = result.get("verts", [])
            bmesh.ops.translate(bm, vec=Vector(tuple(float(v) for v in vector)), verts=new_verts)
        finally:
            _write_bmesh(obj, bm, was_edit)
        return {"ok": True, "name": obj.name, "extruded_verts": len(indices)}

    if tool == "inset_faces":
        obj, err = _resolve_mesh_object(cmd.get("name"))
        if err:
            return err
        indices = cmd.get("face_indices")
        if not isinstance(indices, list) or not indices:
            return {"ok": False, "error": "'face_indices' must be a non-empty list"}
        thickness = float(cmd.get("thickness", 0.05))
        depth = float(cmd.get("depth", 0.0))
        individual = bool(cmd.get("individual", False))
        bm, was_edit = _load_bmesh(obj)
        try:
            bm.faces.ensure_lookup_table()
            faces, msg = _collect_indexed(bm.faces, indices, "face")
            if faces is None:
                return {"ok": False, "error": msg}
            if individual:
                bmesh.ops.inset_individual(
                    bm, faces=faces, thickness=thickness, depth=depth, use_even_offset=True,
                )
            else:
                bmesh.ops.inset_region(
                    bm,
                    faces=faces,
                    thickness=thickness,
                    depth=depth,
                    use_even_offset=True,
                    use_boundary=True,
                )
        finally:
            _write_bmesh(obj, bm, was_edit)
        return {
            "ok": True,
            "name": obj.name,
            "inset_faces": len(indices),
            "individual": individual,
        }

    if tool == "bevel_edges":
        obj, err = _resolve_mesh_object(cmd.get("name"))
        if err:
            return err
        indices = cmd.get("edge_indices")
        if not isinstance(indices, list) or not indices:
            return {"ok": False, "error": "'edge_indices' must be a non-empty list"}
        width = float(cmd.get("width", 0.05))
        segments = int(cmd.get("segments", 1))
        profile = float(cmd.get("profile", 0.5))
        bm, was_edit = _load_bmesh(obj)
        try:
            bm.edges.ensure_lookup_table()
            edges, msg = _collect_indexed(bm.edges, indices, "edge")
            if edges is None:
                return {"ok": False, "error": msg}
            bmesh.ops.bevel(
                bm,
                geom=edges,
                offset=width,
                segments=segments,
                profile=profile,
                affect="EDGES",
                clamp_overlap=True,
            )
        finally:
            _write_bmesh(obj, bm, was_edit)
        return {"ok": True, "name": obj.name, "beveled_edges": len(indices)}

    if tool == "bevel_vertices":
        obj, err = _resolve_mesh_object(cmd.get("name"))
        if err:
            return err
        indices = cmd.get("vertex_indices")
        if not isinstance(indices, list) or not indices:
            return {"ok": False, "error": "'vertex_indices' must be a non-empty list"}
        width = float(cmd.get("width", 0.05))
        segments = int(cmd.get("segments", 1))
        bm, was_edit = _load_bmesh(obj)
        try:
            bm.verts.ensure_lookup_table()
            verts, msg = _collect_indexed(bm.verts, indices, "vertex")
            if verts is None:
                return {"ok": False, "error": msg}
            bmesh.ops.bevel(
                bm,
                geom=verts,
                offset=width,
                segments=segments,
                profile=0.5,
                affect="VERTICES",
                clamp_overlap=True,
            )
        finally:
            _write_bmesh(obj, bm, was_edit)
        return {"ok": True, "name": obj.name, "beveled_vertices": len(indices)}

    if tool == "subdivide":
        obj, err = _resolve_mesh_object(cmd.get("name"))
        if err:
            return err
        indices = cmd.get("edge_indices")
        cuts = int(cmd.get("cuts", 1))
        smoothness = float(cmd.get("smoothness", 0.0))
        bm, was_edit = _load_bmesh(obj)
        try:
            bm.edges.ensure_lookup_table()
            if isinstance(indices, list) and indices:
                edges, msg = _collect_indexed(bm.edges, indices, "edge")
                if edges is None:
                    return {"ok": False, "error": msg}
            else:
                edges = list(bm.edges)
            bmesh.ops.subdivide_edges(
                bm, edges=edges, cuts=cuts, smooth=smoothness, use_grid_fill=True,
            )
        finally:
            _write_bmesh(obj, bm, was_edit)
        return {"ok": True, "name": obj.name, "cuts": cuts, "edges": len(edges)}

    if tool == "bridge_edge_loops":
        obj, err = _resolve_mesh_object(cmd.get("name"))
        if err:
            return err
        indices = cmd.get("edge_indices")
        if not isinstance(indices, list) or not indices:
            return {"ok": False, "error": "'edge_indices' must be a non-empty list"}
        bm, was_edit = _load_bmesh(obj)
        try:
            bm.edges.ensure_lookup_table()
            edges, msg = _collect_indexed(bm.edges, indices, "edge")
            if edges is None:
                return {"ok": False, "error": msg}
            try:
                bmesh.ops.bridge_loops(bm, edges=edges)
            except RuntimeError as exc:
                return {"ok": False, "error": f"bridge_loops failed: {exc}"}
        finally:
            _write_bmesh(obj, bm, was_edit)
        return {"ok": True, "name": obj.name, "edges_used": len(indices)}

    if tool == "merge_vertices":
        obj, err = _resolve_mesh_object(cmd.get("name"))
        if err:
            return err
        mode = (cmd.get("mode") or "DISTANCE").upper()
        if mode not in _MERGE_MODES:
            return {"ok": False, "error": f"mode must be one of {sorted(_MERGE_MODES)}"}
        indices = cmd.get("vertex_indices")
        distance = float(cmd.get("distance", 0.0001))
        bm, was_edit = _load_bmesh(obj)
        try:
            bm.verts.ensure_lookup_table()
            if isinstance(indices, list) and indices:
                verts, msg = _collect_indexed(bm.verts, indices, "vertex")
                if verts is None:
                    return {"ok": False, "error": msg}
            else:
                verts = list(bm.verts)
            if not verts:
                return {"ok": False, "error": "No vertices selected"}
            before = len(bm.verts)
            if mode == "DISTANCE":
                bmesh.ops.remove_doubles(bm, verts=verts, dist=distance)
            else:
                if mode == "CENTER":
                    co = Vector((0.0, 0.0, 0.0))
                    for v in verts:
                        co += v.co
                    co /= len(verts)
                elif mode == "FIRST":
                    co = verts[0].co.copy()
                else:  # LAST
                    co = verts[-1].co.copy()
                bmesh.ops.pointmerge(bm, verts=verts, merge_co=co)
            after = len(bm.verts)
        finally:
            _write_bmesh(obj, bm, was_edit)
        return {
            "ok": True,
            "name": obj.name,
            "mode": mode,
            "removed": before - after,
        }

    if tool == "dissolve":
        obj, err = _resolve_mesh_object(cmd.get("name"))
        if err:
            return err
        dtype = (cmd.get("type") or "").upper()
        if dtype not in _DISSOLVE_TYPES:
            return {"ok": False, "error": f"type must be one of {sorted(_DISSOLVE_TYPES)}"}
        indices = cmd.get("indices")
        if not isinstance(indices, list) or not indices:
            return {"ok": False, "error": "'indices' must be a non-empty list"}
        bm, was_edit = _load_bmesh(obj)
        try:
            if dtype == "VERTS":
                bm.verts.ensure_lookup_table()
                verts, msg = _collect_indexed(bm.verts, indices, "vertex")
                if verts is None:
                    return {"ok": False, "error": msg}
                bmesh.ops.dissolve_verts(bm, verts=verts)
            elif dtype == "EDGES":
                bm.edges.ensure_lookup_table()
                edges, msg = _collect_indexed(bm.edges, indices, "edge")
                if edges is None:
                    return {"ok": False, "error": msg}
                bmesh.ops.dissolve_edges(bm, edges=edges, use_verts=False)
            else:  # FACES
                bm.faces.ensure_lookup_table()
                faces, msg = _collect_indexed(bm.faces, indices, "face")
                if faces is None:
                    return {"ok": False, "error": msg}
                bmesh.ops.dissolve_faces(bm, faces=faces)
        finally:
            _write_bmesh(obj, bm, was_edit)
        return {"ok": True, "name": obj.name, "type": dtype, "count": len(indices)}

    if tool == "delete_elements":
        obj, err = _resolve_mesh_object(cmd.get("name"))
        if err:
            return err
        dtype = (cmd.get("type") or "").upper()
        context = _DELETE_CONTEXTS.get(dtype)
        if context is None:
            return {"ok": False, "error": f"type must be one of {sorted(_DELETE_CONTEXTS)}"}
        indices = cmd.get("indices")
        if not isinstance(indices, list) or not indices:
            return {"ok": False, "error": "'indices' must be a non-empty list"}
        bm, was_edit = _load_bmesh(obj)
        try:
            if context == "VERTS":
                bm.verts.ensure_lookup_table()
                geom, msg = _collect_indexed(bm.verts, indices, "vertex")
            elif context in ("EDGES", "EDGES_FACES"):
                bm.edges.ensure_lookup_table()
                geom, msg = _collect_indexed(bm.edges, indices, "edge")
            else:  # FACES variants
                bm.faces.ensure_lookup_table()
                geom, msg = _collect_indexed(bm.faces, indices, "face")
            if geom is None:
                return {"ok": False, "error": msg}
            bmesh.ops.delete(bm, geom=geom, context=context)
        finally:
            _write_bmesh(obj, bm, was_edit)
        return {"ok": True, "name": obj.name, "type": dtype, "count": len(indices)}

    if tool == "recalculate_normals":
        obj, err = _resolve_mesh_object(cmd.get("name"))
        if err:
            return err
        inside = bool(cmd.get("inside", False))
        bm, was_edit = _load_bmesh(obj)
        try:
            bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
            if inside:
                bmesh.ops.reverse_faces(bm, faces=list(bm.faces))
        finally:
            _write_bmesh(obj, bm, was_edit)
        return {"ok": True, "name": obj.name, "inside": inside}

    if tool == "shade_smooth":
        obj, err = _resolve_mesh_object(cmd.get("name"))
        if err:
            return err
        angle = cmd.get("angle_degrees")
        for poly in obj.data.polygons:
            poly.use_smooth = True
        obj.data.update()
        used_by_angle = False
        if angle is not None:
            _select_only(obj)
            if obj.mode == "EDIT":
                bpy.ops.object.mode_set(mode="OBJECT")
            try:
                bpy.ops.object.shade_smooth_by_angle(angle=math.radians(float(angle)))
                used_by_angle = True
            except (AttributeError, RuntimeError):
                # Older Blender without the modifier op — smooth was already applied above.
                used_by_angle = False
        return {"ok": True, "name": obj.name, "by_angle": used_by_angle}

    if tool == "shade_flat":
        obj, err = _resolve_mesh_object(cmd.get("name"))
        if err:
            return err
        for poly in obj.data.polygons:
            poly.use_smooth = False
        obj.data.update()
        return {"ok": True, "name": obj.name}

    if tool == "mark_sharp":
        obj, err = _resolve_mesh_object(cmd.get("name"))
        if err:
            return err
        indices = cmd.get("edge_indices")
        if not isinstance(indices, list) or not indices:
            return {"ok": False, "error": "'edge_indices' must be a non-empty list"}
        clear = bool(cmd.get("clear", False))
        edges = obj.data.edges
        for i in indices:
            if not isinstance(i, int) or i < 0 or i >= len(edges):
                return {"ok": False, "error": f"edge index {i!r} out of range [0, {len(edges) - 1}]"}
            edges[i].use_edge_sharp = not clear
        obj.data.update()
        return {"ok": True, "name": obj.name, "count": len(indices), "clear": clear}

    if tool == "mark_seam":
        obj, err = _resolve_mesh_object(cmd.get("name"))
        if err:
            return err
        indices = cmd.get("edge_indices")
        if not isinstance(indices, list) or not indices:
            return {"ok": False, "error": "'edge_indices' must be a non-empty list"}
        clear = bool(cmd.get("clear", False))
        edges = obj.data.edges
        for i in indices:
            if not isinstance(i, int) or i < 0 or i >= len(edges):
                return {"ok": False, "error": f"edge index {i!r} out of range [0, {len(edges) - 1}]"}
            edges[i].use_seam = not clear
        obj.data.update()
        return {"ok": True, "name": obj.name, "count": len(indices), "clear": clear}

    if tool == "boolean_operation":
        target, err = _resolve_mesh_object(cmd.get("target"))
        if err:
            return err
        cutter, err = _resolve_mesh_object(cmd.get("cutter"))
        if err:
            return err
        op = (cmd.get("op") or "DIFFERENCE").upper()
        if op not in _BOOLEAN_OPS:
            return {"ok": False, "error": f"op must be one of {sorted(_BOOLEAN_OPS)}"}
        solver = (cmd.get("solver") or "EXACT").upper()
        if solver not in _BOOLEAN_SOLVERS:
            return {"ok": False, "error": f"solver must be one of {sorted(_BOOLEAN_SOLVERS)}"}
        if target is cutter:
            return {"ok": False, "error": "target and cutter must be different objects"}
        if target.mode == "EDIT":
            _select_only(target)
            bpy.ops.object.mode_set(mode="OBJECT")
        mod = target.modifiers.new(name="_jt_bool", type="BOOLEAN")
        mod.object = cutter
        mod.operation = op
        try:
            mod.solver = solver
        except (AttributeError, TypeError):
            pass
        _select_only(target)
        try:
            bpy.ops.object.modifier_apply(modifier=mod.name)
        except RuntimeError as exc:
            if mod.name in target.modifiers:
                target.modifiers.remove(target.modifiers[mod.name])
            return {"ok": False, "error": f"boolean apply failed: {exc}"}
        return {
            "ok": True,
            "target": target.name,
            "cutter": cutter.name,
            "op": op,
            "solver": solver,
        }

    # ---------- Phase 13 — UV editing & unwrapping ----------

    if tool == "list_uv_maps":
        obj, err = _resolve_mesh_object(cmd.get("name"))
        if err:
            return err
        layers = obj.data.uv_layers
        active = layers.active
        return {
            "ok": True,
            "name": obj.name,
            "uv_maps": [layer.name for layer in layers],
            "active": active.name if active else None,
        }

    if tool == "create_uv_map":
        obj, err = _resolve_mesh_object(cmd.get("name"))
        if err:
            return err
        map_name = cmd.get("map_name") or "UVMap"
        layers = obj.data.uv_layers
        if layers.get(map_name) is not None:
            return {"ok": False, "error": f"UV map {map_name!r} already exists on {obj.name!r}"}
        try:
            layer = layers.new(name=map_name)
        except RuntimeError as exc:
            return {"ok": False, "error": f"Failed to create UV map: {exc}"}
        if cmd.get("active", True):
            layers.active = layer
        return {
            "ok": True,
            "name": obj.name,
            "uv_map": layer.name,
            "active": layers.active.name if layers.active else None,
        }

    if tool == "set_active_uv_map":
        obj, err = _resolve_mesh_object(cmd.get("name"))
        if err:
            return err
        map_name = cmd.get("map_name")
        layer = obj.data.uv_layers.get(map_name) if map_name else None
        if layer is None:
            return {
                "ok": False,
                "error": f"No UV map named {map_name!r} on {obj.name!r}. Available: {[l.name for l in obj.data.uv_layers]}",
            }
        obj.data.uv_layers.active = layer
        return {"ok": True, "name": obj.name, "active": layer.name}

    if tool == "uv_unwrap":
        obj, err = _resolve_mesh_object(cmd.get("name"))
        if err:
            return err
        method = (cmd.get("method") or "UNWRAP").upper()
        if method not in _UV_UNWRAP_METHODS and method not in _UV_PROJECTION_METHODS:
            valid = sorted(set(_UV_UNWRAP_METHODS) | _UV_PROJECTION_METHODS)
            return {"ok": False, "error": f"Unknown unwrap method {method!r}. Valid: {valid}"}

        # If there's no UV map yet, create one so the operator has somewhere to write.
        layers = obj.data.uv_layers
        if len(layers) == 0:
            try:
                layers.new(name="UVMap")
            except RuntimeError as exc:
                return {"ok": False, "error": f"Failed to create default UV map: {exc}"}

        angle_limit_deg = float(cmd.get("angle_limit", 66.0))
        island_margin = float(cmd.get("island_margin", 0.001))
        correct_aspect = bool(cmd.get("correct_aspect", True))
        scale_to_bounds = bool(cmd.get("scale_to_bounds", False))
        cube_size = float(cmd.get("cube_size", 2.0))

        prev_mode = _prepare_edit_select_all(obj)
        try:
            if method in _UV_UNWRAP_METHODS:
                op_method = _UV_UNWRAP_METHODS[method]

                def _op():
                    return bpy.ops.uv.unwrap(
                        method=op_method,
                        margin=island_margin,
                        correct_aspect=correct_aspect,
                    )
            elif method == "SMART_PROJECT":
                def _op():
                    return bpy.ops.uv.smart_project(
                        angle_limit=math.radians(angle_limit_deg),
                        island_margin=island_margin,
                        correct_aspect=correct_aspect,
                        scale_to_bounds=scale_to_bounds,
                    )
            elif method == "CUBE_PROJECTION":
                def _op():
                    return bpy.ops.uv.cube_project(
                        cube_size=cube_size,
                        correct_aspect=correct_aspect,
                        scale_to_bounds=scale_to_bounds,
                    )
            elif method == "CYLINDER_PROJECTION":
                def _op():
                    return bpy.ops.uv.cylinder_project(
                        direction="VIEW_ON_EQUATOR",
                        align="POLAR_ZX",
                        radius=1.0,
                        correct_aspect=correct_aspect,
                        scale_to_bounds=scale_to_bounds,
                    )
            else:  # SPHERE_PROJECTION
                def _op():
                    return bpy.ops.uv.sphere_project(
                        direction="VIEW_ON_EQUATOR",
                        align="POLAR_ZX",
                        correct_aspect=correct_aspect,
                        scale_to_bounds=scale_to_bounds,
                    )

            try:
                _run_uv_op(_op)
            except (RuntimeError, TypeError) as exc:
                return {"ok": False, "error": f"{method} failed: {exc}"}
        finally:
            _restore_mode(obj, prev_mode)

        active = obj.data.uv_layers.active
        return {
            "ok": True,
            "name": obj.name,
            "method": method,
            "uv_map": active.name if active else None,
        }

    if tool == "pack_islands":
        obj, err = _resolve_mesh_object(cmd.get("name"))
        if err:
            return err
        if not obj.data.uv_layers:
            return {"ok": False, "error": f"{obj.name!r} has no UV map to pack"}
        margin = float(cmd.get("margin", 0.001))
        rotate = bool(cmd.get("rotate", True))
        prev_mode = _prepare_edit_select_all(obj)
        try:
            def _op():
                try:
                    return bpy.ops.uv.pack_islands(margin=margin, rotate=rotate)
                except TypeError:
                    # Older signature — drop rotate and retry.
                    return bpy.ops.uv.pack_islands(margin=margin)

            try:
                _run_uv_op(_op)
            except RuntimeError as exc:
                return {"ok": False, "error": f"pack_islands failed: {exc}"}
        finally:
            _restore_mode(obj, prev_mode)
        return {"ok": True, "name": obj.name, "margin": margin, "rotate": rotate}

    if tool == "average_islands_scale":
        obj, err = _resolve_mesh_object(cmd.get("name"))
        if err:
            return err
        if not obj.data.uv_layers:
            return {"ok": False, "error": f"{obj.name!r} has no UV map"}
        prev_mode = _prepare_edit_select_all(obj)
        try:
            try:
                _run_uv_op(bpy.ops.uv.average_islands_scale)
            except RuntimeError as exc:
                return {"ok": False, "error": f"average_islands_scale failed: {exc}"}
        finally:
            _restore_mode(obj, prev_mode)
        return {"ok": True, "name": obj.name}

    if tool == "get_uv_layout":
        obj, err = _resolve_mesh_object(cmd.get("name"))
        if err:
            return err
        mesh = obj.data
        if not mesh.uv_layers:
            return {"ok": False, "error": f"{obj.name!r} has no UV map"}
        map_name = cmd.get("uv_map")
        layer = mesh.uv_layers.get(map_name) if map_name else mesh.uv_layers.active
        if layer is None:
            return {
                "ok": False,
                "error": f"No UV map named {map_name!r}. Available: {[l.name for l in mesh.uv_layers]}",
            }
        max_polys = int(cmd.get("max_polygons", 5000))
        poly_count = len(mesh.polygons)
        truncated = poly_count > max_polys
        data = layer.data
        faces = []
        for idx, poly in enumerate(mesh.polygons):
            if idx >= max_polys:
                break
            uvs = []
            for li in poly.loop_indices:
                uv = data[li].uv
                uvs.append([round(uv.x, 6), round(uv.y, 6)])
            faces.append({
                "index": poly.index,
                "vertices": [mesh.loops[li].vertex_index for li in poly.loop_indices],
                "uvs": uvs,
            })
        return {
            "ok": True,
            "name": obj.name,
            "uv_map": layer.name,
            "polygon_count": poly_count,
            "truncated": truncated,
            "faces": faces,
        }

    # ---------- Phase 14 — Texture painting + baking ----------

    if tool == "create_paint_texture":
        obj, err = _resolve_mesh_object(cmd.get("name"))
        if err:
            return err
        image_name = cmd.get("image_name") or f"{obj.name}_Paint"
        if bpy.data.images.get(image_name) is not None:
            return {"ok": False, "error": f"Image {image_name!r} already exists"}
        size = int(cmd.get("size") or 1024)
        if size <= 0 or size > 8192:
            return {"ok": False, "error": "'size' must be in (0, 8192]"}
        color = _color4(cmd.get("color"), default=(1.0, 1.0, 1.0, 1.0))
        alpha = bool(cmd.get("alpha", True))
        image = bpy.data.images.new(
            name=image_name, width=size, height=size, alpha=alpha, float_buffer=False,
        )
        _fill_image_pixels(image, color)

        mat, err_msg = _ensure_material_for_bake(obj, image, create_if_missing=True)
        if err_msg:
            return {"ok": False, "error": err_msg}

        # Wire the new texture into Base Color so painting is immediately visible.
        tree = _ensure_material_nodes(mat)
        bsdf = _find_principled(mat)
        tex_node = tree.nodes.active  # just set by _ensure_material_for_bake
        if bsdf is not None and tex_node is not None:
            try:
                tree.links.new(tex_node.outputs["Color"], bsdf.inputs["Base Color"])
            except (KeyError, RuntimeError):
                pass

        return {
            "ok": True,
            "name": obj.name,
            "image": image.name,
            "material": mat.name,
            "size": [image.size[0], image.size[1]],
        }

    if tool == "fill_texture":
        image_name = cmd.get("image_name")
        image = bpy.data.images.get(image_name) if image_name else None
        if image is None:
            return {"ok": False, "error": f"No image named {image_name!r}"}
        color = _color4(cmd.get("color"), default=(1.0, 1.0, 1.0, 1.0))
        _fill_image_pixels(image, color)
        return {
            "ok": True,
            "image": image.name,
            "color": list(color),
            "size": [image.size[0], image.size[1]],
        }

    if tool == "save_paint_texture":
        image_name = cmd.get("image_name")
        image = bpy.data.images.get(image_name) if image_name else None
        if image is None:
            return {"ok": False, "error": f"No image named {image_name!r}"}
        path = cmd.get("path")
        if not path:
            return {"ok": False, "error": "'path' is required"}
        abs_path = os.path.abspath(os.path.expanduser(path))
        os.makedirs(os.path.dirname(abs_path) or ".", exist_ok=True)
        ext = os.path.splitext(abs_path)[1].lower()
        fmt = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".tga": "TARGA", ".exr": "OPEN_EXR"}.get(ext, "PNG")
        prev_fp = image.filepath_raw
        prev_format = image.file_format
        try:
            image.filepath_raw = abs_path
            image.file_format = fmt
            image.save()
        except Exception as exc:
            return {"ok": False, "error": f"Failed to save image: {exc}"}
        finally:
            # Keep the bound path so the image is portable, but restore the
            # file_format in case the caller was mid-workflow.
            image.file_format = prev_format or fmt
        return {"ok": True, "image": image.name, "path": abs_path, "format": fmt}

    if tool == "set_bake_settings":
        scene = bpy.context.scene
        samples = cmd.get("samples")
        if samples is not None:
            scene.cycles.samples = int(samples)
        margin = cmd.get("margin")
        if margin is not None:
            scene.render.bake.margin = int(margin)
        use_cage = cmd.get("use_cage")
        if use_cage is not None:
            scene.render.bake.use_cage = bool(use_cage)
        cage_extrusion = cmd.get("cage_extrusion")
        if cage_extrusion is not None:
            scene.render.bake.cage_extrusion = float(cage_extrusion)
        max_ray_distance = cmd.get("max_ray_distance")
        if max_ray_distance is not None:
            try:
                scene.render.bake.max_ray_distance = float(max_ray_distance)
            except AttributeError:
                pass
        return {
            "ok": True,
            "samples": scene.cycles.samples,
            "margin": scene.render.bake.margin,
            "use_cage": scene.render.bake.use_cage,
            "cage_extrusion": scene.render.bake.cage_extrusion,
        }

    if tool == "bake_texture":
        obj, err = _resolve_mesh_object(cmd.get("name"))
        if err:
            return err
        bake_type = (cmd.get("bake_type") or "COMBINED").upper()
        if bake_type not in _BAKE_TYPES:
            return {"ok": False, "error": f"Unknown bake_type {bake_type!r}. Valid: {sorted(_BAKE_TYPES)}"}
        resolution = int(cmd.get("resolution") or 1024)
        if resolution <= 0 or resolution > 8192:
            return {"ok": False, "error": "'resolution' must be in (0, 8192]"}
        image_name = cmd.get("image_name") or f"{obj.name}_{bake_type}"
        colorspace, is_normal = _BAKE_TYPES[bake_type]

        image = bpy.data.images.get(image_name)
        if image is None:
            image = bpy.data.images.new(
                name=image_name,
                width=resolution,
                height=resolution,
                alpha=False,
                float_buffer=(bake_type == "POSITION"),
                is_data=(colorspace == "Non-Color"),
            )
        try:
            image.colorspace_settings.name = colorspace
        except Exception:
            pass

        mat, err_msg = _ensure_material_for_bake(obj, image, create_if_missing=True)
        if err_msg:
            return {"ok": False, "error": err_msg}

        if not obj.data.uv_layers:
            return {"ok": False, "error": f"{obj.name!r} has no UV map. Run uv_unwrap first."}

        scene = bpy.context.scene
        prev_engine = scene.render.engine
        scene.render.engine = "CYCLES"
        prev_use_selected = scene.render.bake.use_selected_to_active
        scene.render.bake.use_selected_to_active = False

        _select_only(obj)
        if obj.mode == "EDIT":
            bpy.ops.object.mode_set(mode="OBJECT")

        try:
            bpy.ops.object.bake(type=bake_type)
        except RuntimeError as exc:
            return {"ok": False, "error": f"bake failed: {exc}"}
        finally:
            scene.render.engine = prev_engine
            scene.render.bake.use_selected_to_active = prev_use_selected

        output_path = cmd.get("output_path")
        saved_path = None
        if output_path:
            abs_path = os.path.abspath(os.path.expanduser(output_path))
            os.makedirs(os.path.dirname(abs_path) or ".", exist_ok=True)
            ext = os.path.splitext(abs_path)[1].lower()
            fmt = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".exr": "OPEN_EXR"}.get(ext, "PNG")
            image.filepath_raw = abs_path
            image.file_format = fmt
            image.save()
            saved_path = abs_path

        return {
            "ok": True,
            "name": obj.name,
            "bake_type": bake_type,
            "image": image.name,
            "size": [image.size[0], image.size[1]],
            "is_normal": is_normal,
            "output_path": saved_path,
        }

    if tool == "bake_from_selected":
        low_poly, err = _resolve_mesh_object(cmd.get("low_poly"))
        if err:
            return err
        high_poly, err = _resolve_mesh_object(cmd.get("high_poly"))
        if err:
            return err
        if low_poly is high_poly:
            return {"ok": False, "error": "low_poly and high_poly must be different objects"}
        bake_type = (cmd.get("bake_type") or "NORMAL").upper()
        if bake_type not in _BAKE_TYPES:
            return {"ok": False, "error": f"Unknown bake_type {bake_type!r}. Valid: {sorted(_BAKE_TYPES)}"}
        resolution = int(cmd.get("resolution") or 1024)
        if resolution <= 0 or resolution > 8192:
            return {"ok": False, "error": "'resolution' must be in (0, 8192]"}
        cage_extrusion = float(cmd.get("cage_extrusion") or 0.05)

        if not low_poly.data.uv_layers:
            return {"ok": False, "error": f"{low_poly.name!r} (low-poly target) has no UV map. Run uv_unwrap first."}

        image_name = cmd.get("image_name") or f"{low_poly.name}_{bake_type}"
        colorspace, is_normal = _BAKE_TYPES[bake_type]
        image = bpy.data.images.get(image_name)
        if image is None:
            image = bpy.data.images.new(
                name=image_name,
                width=resolution,
                height=resolution,
                alpha=False,
                float_buffer=(bake_type == "POSITION"),
                is_data=(colorspace == "Non-Color"),
            )
        try:
            image.colorspace_settings.name = colorspace
        except Exception:
            pass

        mat, err_msg = _ensure_material_for_bake(low_poly, image, create_if_missing=True)
        if err_msg:
            return {"ok": False, "error": err_msg}

        scene = bpy.context.scene
        prev_engine = scene.render.engine
        scene.render.engine = "CYCLES"
        prev_use_selected = scene.render.bake.use_selected_to_active
        prev_cage_extrusion = scene.render.bake.cage_extrusion
        scene.render.bake.use_selected_to_active = True
        scene.render.bake.cage_extrusion = cage_extrusion

        # Selection must be: high-poly selected + low-poly active.
        for o in list(bpy.context.selected_objects):
            o.select_set(False)
        if low_poly.mode == "EDIT":
            bpy.context.view_layer.objects.active = low_poly
            bpy.ops.object.mode_set(mode="OBJECT")
        high_poly.select_set(True)
        low_poly.select_set(True)
        bpy.context.view_layer.objects.active = low_poly

        try:
            bpy.ops.object.bake(type=bake_type)
        except RuntimeError as exc:
            return {"ok": False, "error": f"bake_from_selected failed: {exc}"}
        finally:
            scene.render.engine = prev_engine
            scene.render.bake.use_selected_to_active = prev_use_selected
            scene.render.bake.cage_extrusion = prev_cage_extrusion

        output_path = cmd.get("output_path")
        saved_path = None
        if output_path:
            abs_path = os.path.abspath(os.path.expanduser(output_path))
            os.makedirs(os.path.dirname(abs_path) or ".", exist_ok=True)
            ext = os.path.splitext(abs_path)[1].lower()
            fmt = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".exr": "OPEN_EXR"}.get(ext, "PNG")
            image.filepath_raw = abs_path
            image.file_format = fmt
            image.save()
            saved_path = abs_path

        return {
            "ok": True,
            "low_poly": low_poly.name,
            "high_poly": high_poly.name,
            "bake_type": bake_type,
            "image": image.name,
            "size": [image.size[0], image.size[1]],
            "cage_extrusion": cage_extrusion,
            "output_path": saved_path,
        }

    # ---------- Phase 15 — Geometry nodes ----------

    if tool == "create_geometry_nodes_modifier":
        obj, err = _resolve_mesh_object(cmd.get("name"))
        if err:
            return err
        group_name = cmd.get("group_name") or f"{obj.name}_GeoNodes"
        if bpy.data.node_groups.get(group_name) is not None:
            return {"ok": False, "error": f"Node group {group_name!r} already exists"}
        modifier_name = cmd.get("modifier_name") or "GeometryNodes"
        try:
            tree = _new_geometry_nodes_tree(group_name)
        except RuntimeError as exc:
            return {"ok": False, "error": f"Failed to create node tree: {exc}"}
        try:
            mod = obj.modifiers.new(name=modifier_name, type="NODES")
        except RuntimeError as exc:
            bpy.data.node_groups.remove(tree)
            return {"ok": False, "error": f"Failed to create NODES modifier: {exc}"}
        mod.node_group = tree
        return {
            "ok": True,
            "name": obj.name,
            "modifier": mod.name,
            "group_name": tree.name,
            "nodes": [n.name for n in tree.nodes],
        }

    if tool == "add_geo_node":
        group, err = _resolve_node_group(cmd.get("group_name"))
        if err:
            return err
        node_type_key = (cmd.get("node_type") or "").upper()
        bl_idname = _GEO_NODE_TYPES.get(node_type_key)
        if bl_idname is None:
            return {
                "ok": False,
                "error": f"Unknown node_type {node_type_key!r}. Valid: {sorted(_GEO_NODE_TYPES)}",
            }
        try:
            node = group.nodes.new(bl_idname)
        except RuntimeError as exc:
            return {"ok": False, "error": f"Failed to create {node_type_key}: {exc}"}
        location = cmd.get("location")
        if location and len(location) == 2:
            node.location = (float(location[0]), float(location[1]))
        desired_name = cmd.get("name")
        if desired_name:
            node.name = desired_name
            node.label = desired_name
        return {
            "ok": True,
            "group": group.name,
            "node": {
                "name": node.name,
                "type": node_type_key,
                "bl_idname": node.bl_idname,
                "location": [node.location.x, node.location.y],
                "inputs": [s.name for s in node.inputs],
                "outputs": [s.name for s in node.outputs],
            },
        }

    if tool == "connect_geo_nodes":
        group, err = _resolve_node_group(cmd.get("group_name"))
        if err:
            return err
        from_node = group.nodes.get(cmd.get("from_node"))
        to_node = group.nodes.get(cmd.get("to_node"))
        if from_node is None:
            return {"ok": False, "error": f"No node named {cmd.get('from_node')!r} in {group.name!r}"}
        if to_node is None:
            return {"ok": False, "error": f"No node named {cmd.get('to_node')!r} in {group.name!r}"}
        from_socket_name = cmd.get("from_socket")
        to_socket_name = cmd.get("to_socket")
        from_socket = from_node.outputs.get(from_socket_name)
        if from_socket is None:
            return {"ok": False, "error": f"{from_node.name!r} has no output {from_socket_name!r}. Available: {[s.name for s in from_node.outputs]}"}
        to_socket = to_node.inputs.get(to_socket_name)
        if to_socket is None:
            return {"ok": False, "error": f"{to_node.name!r} has no input {to_socket_name!r}. Available: {[s.name for s in to_node.inputs]}"}
        link = group.links.new(from_socket, to_socket)
        return {
            "ok": True,
            "group": group.name,
            "from": f"{from_node.name}.{from_socket.name}",
            "to": f"{to_node.name}.{to_socket.name}",
            "valid": link.is_valid,
        }

    if tool == "disconnect_geo_nodes":
        group, err = _resolve_node_group(cmd.get("group_name"))
        if err:
            return err
        from_name = cmd.get("from_node")
        to_name = cmd.get("to_node")
        from_socket = cmd.get("from_socket")
        to_socket = cmd.get("to_socket")
        removed = 0
        for link in list(group.links):
            if (link.from_node.name == from_name
                    and link.to_node.name == to_name
                    and link.from_socket.name == from_socket
                    and link.to_socket.name == to_socket):
                group.links.remove(link)
                removed += 1
        if removed == 0:
            return {"ok": False, "error": "No matching link found"}
        return {"ok": True, "group": group.name, "removed": removed}

    if tool == "set_geo_node_param":
        group, err = _resolve_node_group(cmd.get("group_name"))
        if err:
            return err
        node = group.nodes.get(cmd.get("node"))
        if node is None:
            return {"ok": False, "error": f"No node named {cmd.get('node')!r} in {group.name!r}"}
        params = cmd.get("params")
        if not isinstance(params, dict) or not params:
            return {"ok": False, "error": "'params' must be a non-empty dict"}
        # Params values that reference Blender objects need resolution.
        for key, value in params.items():
            if isinstance(value, str) and key in node.inputs:
                sock = node.inputs.get(key)
                if sock is not None and sock.type == "OBJECT":
                    obj = bpy.data.objects.get(value)
                    if obj is None:
                        return {"ok": False, "error": f"Referenced object {value!r} not found for input {key!r}"}
                    try:
                        sock.default_value = obj
                    except Exception as exc:
                        return {"ok": False, "error": f"Failed to set {key!r}={value!r}: {exc}"}
                    continue
            ok, err_msg = _set_node_param(node, key, value)
            if not ok:
                return {"ok": False, "error": err_msg}
        return {"ok": True, "group": group.name, "node": node.name}

    if tool == "list_geo_nodes":
        group, err = _resolve_node_group(cmd.get("group_name"))
        if err:
            return err
        nodes = [
            {
                "name": n.name,
                "bl_idname": n.bl_idname,
                "location": [n.location.x, n.location.y],
                "inputs": [s.name for s in n.inputs],
                "outputs": [s.name for s in n.outputs],
            }
            for n in group.nodes
        ]
        links = [
            {
                "from": f"{l.from_node.name}.{l.from_socket.name}",
                "to": f"{l.to_node.name}.{l.to_socket.name}",
            }
            for l in group.links
        ]
        return {"ok": True, "group": group.name, "nodes": nodes, "links": links}

    if tool == "scatter_objects":
        target, err = _resolve_mesh_object(cmd.get("target"))
        if err:
            return err
        instance_obj, err = _resolve_object(cmd.get("instance"))
        if err:
            return err
        density = float(cmd.get("density", 10.0))
        seed = int(cmd.get("seed", 0))
        scale_min = float(cmd.get("scale_min", 0.8))
        scale_max = float(cmd.get("scale_max", 1.2))
        align_to_normal = bool(cmd.get("align_to_normal", True))

        group_name = cmd.get("group_name") or f"{target.name}_Scatter"
        if bpy.data.node_groups.get(group_name) is not None:
            return {"ok": False, "error": f"Node group {group_name!r} already exists"}
        tree = _new_geometry_nodes_tree(group_name)
        # Remove the default passthrough link so we can insert the scatter chain.
        for link in list(tree.links):
            tree.links.remove(link)

        group_in = next(n for n in tree.nodes if n.bl_idname == "NodeGroupInput")
        group_out = next(n for n in tree.nodes if n.bl_idname == "NodeGroupOutput")

        distribute = tree.nodes.new("GeometryNodeDistributePointsOnFaces")
        distribute.location = (-100, 100)
        distribute.distribute_method = "POISSON" if cmd.get("poisson", True) else "RANDOM"
        distribute.inputs["Density"].default_value = density
        try:
            distribute.inputs["Seed"].default_value = seed
        except KeyError:
            pass

        instance_node = tree.nodes.new("GeometryNodeInstanceOnPoints")
        instance_node.location = (200, 100)

        object_info = tree.nodes.new("GeometryNodeObjectInfo")
        object_info.location = (-100, -200)
        object_info.inputs["Object"].default_value = instance_obj
        object_info.transform_space = "RELATIVE"

        rand = tree.nodes.new("FunctionNodeRandomValue")
        rand.location = (-100, -50)
        rand.data_type = "FLOAT_VECTOR"
        rand.inputs["Min"].default_value = (scale_min, scale_min, scale_min)
        rand.inputs["Max"].default_value = (scale_max, scale_max, scale_max)
        try:
            rand.inputs["Seed"].default_value = seed + 1
        except KeyError:
            pass

        tree.links.new(group_in.outputs["Geometry"], distribute.inputs["Mesh"])
        tree.links.new(distribute.outputs["Points"], instance_node.inputs["Points"])
        tree.links.new(object_info.outputs["Geometry"], instance_node.inputs["Instance"])
        tree.links.new(rand.outputs["Value"], instance_node.inputs["Scale"])
        if align_to_normal and "Rotation" in distribute.outputs:
            tree.links.new(distribute.outputs["Rotation"], instance_node.inputs["Rotation"])
        tree.links.new(instance_node.outputs["Instances"], group_out.inputs["Geometry"])

        try:
            mod = target.modifiers.new(name="Scatter", type="NODES")
        except RuntimeError as exc:
            bpy.data.node_groups.remove(tree)
            return {"ok": False, "error": f"Failed to create NODES modifier: {exc}"}
        mod.node_group = tree

        return {
            "ok": True,
            "target": target.name,
            "instance": instance_obj.name,
            "modifier": mod.name,
            "group": tree.name,
            "density": density,
        }

    if tool == "apply_voxel_remesh":
        obj, err = _resolve_mesh_object(cmd.get("name"))
        if err:
            return err
        voxel_size = float(cmd.get("voxel_size", 0.1))
        if voxel_size <= 0:
            return {"ok": False, "error": "'voxel_size' must be > 0"}
        adaptivity = float(cmd.get("adaptivity", 0.0))
        mod = obj.modifiers.new(name="_jt_voxel_remesh", type="REMESH")
        mod.mode = "VOXEL"
        mod.voxel_size = voxel_size
        mod.adaptivity = adaptivity
        err_msg = _apply_temp_modifier(obj, mod)
        if err_msg:
            return {"ok": False, "error": err_msg}
        return {
            "ok": True,
            "name": obj.name,
            "voxel_size": voxel_size,
            "polygons": len(obj.data.polygons),
        }

    if tool == "apply_decimate":
        obj, err = _resolve_mesh_object(cmd.get("name"))
        if err:
            return err
        dtype = (cmd.get("decimate_type") or "COLLAPSE").upper()
        if dtype not in {"COLLAPSE", "UNSUBDIV", "DISSOLVE"}:
            return {"ok": False, "error": "decimate_type must be COLLAPSE, UNSUBDIV, or DISSOLVE"}
        mod = obj.modifiers.new(name="_jt_decimate", type="DECIMATE")
        mod.decimate_type = dtype
        if dtype == "COLLAPSE":
            ratio = float(cmd.get("ratio", 0.5))
            if ratio <= 0 or ratio > 1.0:
                obj.modifiers.remove(mod)
                return {"ok": False, "error": "'ratio' must be in (0, 1]"}
            mod.ratio = ratio
        elif dtype == "UNSUBDIV":
            mod.iterations = int(cmd.get("iterations", 2))
        else:  # DISSOLVE
            angle = float(cmd.get("angle_degrees", 5.0))
            mod.angle_limit = math.radians(angle)
        before = len(obj.data.polygons)
        err_msg = _apply_temp_modifier(obj, mod)
        if err_msg:
            return {"ok": False, "error": err_msg}
        return {
            "ok": True,
            "name": obj.name,
            "decimate_type": dtype,
            "polygons_before": before,
            "polygons_after": len(obj.data.polygons),
        }

    # ---------- Phase 16 — Collections + file I/O ----------

    if tool == "list_collections":
        scene = bpy.context.scene
        def _walk(coll):
            return {
                "name": coll.name,
                "hide_viewport": coll.hide_viewport,
                "hide_render": coll.hide_render,
                "objects": [o.name for o in coll.objects],
                "children": [_walk(c) for c in coll.children],
            }
        return {
            "ok": True,
            "scene": scene.name,
            "root": _walk(scene.collection),
        }

    if tool == "create_collection":
        name = cmd.get("collection_name") or cmd.get("name")
        if not name:
            return {"ok": False, "error": "'collection_name' is required"}
        if bpy.data.collections.get(name) is not None:
            return {"ok": False, "error": f"Collection {name!r} already exists"}
        parent_name = cmd.get("parent")
        if parent_name:
            parent = bpy.data.collections.get(parent_name)
            if parent is None:
                return {"ok": False, "error": f"Parent collection {parent_name!r} not found"}
        else:
            parent = bpy.context.scene.collection
        new_coll = bpy.data.collections.new(name)
        parent.children.link(new_coll)
        return {
            "ok": True,
            "name": new_coll.name,
            "parent": parent.name,
        }

    if tool == "move_to_collection":
        obj, err = _resolve_object(cmd.get("object_name") or cmd.get("name"))
        if err:
            return err
        coll_name = cmd.get("collection_name")
        target = bpy.data.collections.get(coll_name) if coll_name else None
        if target is None and coll_name == bpy.context.scene.collection.name:
            target = bpy.context.scene.collection
        if target is None:
            return {"ok": False, "error": f"Collection {coll_name!r} not found"}
        # Unlink from every collection the object is currently in, then link
        # to the target. Objects can be multi-linked in Blender but "move"
        # is almost always what users want.
        for c in list(obj.users_collection):
            c.objects.unlink(obj)
        target.objects.link(obj)
        return {
            "ok": True,
            "object": obj.name,
            "collection": target.name,
        }

    if tool == "set_collection_visibility":
        coll_name = cmd.get("collection_name") or cmd.get("name")
        coll = bpy.data.collections.get(coll_name) if coll_name else None
        if coll is None:
            # Fall back to the scene root collection if the name matches.
            root = bpy.context.scene.collection
            if coll_name == root.name:
                coll = root
        if coll is None:
            return {"ok": False, "error": f"Collection {coll_name!r} not found"}
        if cmd.get("hide_viewport") is not None:
            coll.hide_viewport = bool(cmd["hide_viewport"])
        if cmd.get("hide_render") is not None:
            coll.hide_render = bool(cmd["hide_render"])
        return {
            "ok": True,
            "name": coll.name,
            "hide_viewport": coll.hide_viewport,
            "hide_render": coll.hide_render,
        }

    if tool == "delete_collection":
        coll_name = cmd.get("collection_name") or cmd.get("name")
        coll = bpy.data.collections.get(coll_name) if coll_name else None
        if coll is None:
            return {"ok": False, "error": f"Collection {coll_name!r} not found"}
        delete_objects = bool(cmd.get("delete_objects", False))
        moved = 0
        deleted = 0
        if delete_objects:
            for obj in list(coll.objects):
                bpy.data.objects.remove(obj, do_unlink=True)
                deleted += 1
        else:
            # Move the collection's objects up to the scene root so they don't vanish.
            root = bpy.context.scene.collection
            for obj in list(coll.objects):
                for c in list(obj.users_collection):
                    c.objects.unlink(obj)
                if obj.name not in root.objects:
                    root.objects.link(obj)
                moved += 1
        bpy.data.collections.remove(coll)
        return {
            "ok": True,
            "removed": coll_name,
            "objects_deleted": deleted,
            "objects_moved": moved,
        }

    if tool == "save_blend_file":
        path = cmd.get("path")
        if not path:
            return {"ok": False, "error": "'path' is required"}
        abs_path = os.path.abspath(os.path.expanduser(path))
        os.makedirs(os.path.dirname(abs_path) or ".", exist_ok=True)
        compress = bool(cmd.get("compress", True))
        try:
            bpy.ops.wm.save_as_mainfile(filepath=abs_path, compress=compress, copy=False)
        except RuntimeError as exc:
            return {"ok": False, "error": f"save_as_mainfile failed: {exc}"}
        return {"ok": True, "path": abs_path, "compress": compress}

    if tool == "open_blend_file":
        path = cmd.get("path")
        if not path:
            return {"ok": False, "error": "'path' is required"}
        abs_path = os.path.abspath(os.path.expanduser(path))
        if not os.path.exists(abs_path):
            return {"ok": False, "error": f"Blend file not found: {abs_path}"}
        # Note: this wipes the current session. A load_post handler
        # (_on_load_post) re-registers the dispatcher timer so the MCP server
        # keeps responding after the file swap.
        try:
            bpy.ops.wm.open_mainfile(filepath=abs_path)
        except RuntimeError as exc:
            return {"ok": False, "error": f"open_mainfile failed: {exc}"}
        return {"ok": True, "path": abs_path, "scene": bpy.context.scene.name}

    if tool in ("append_from_blend", "link_from_blend"):
        path = cmd.get("path")
        if not path:
            return {"ok": False, "error": "'path' is required"}
        abs_path = os.path.abspath(os.path.expanduser(path))
        if not os.path.exists(abs_path):
            return {"ok": False, "error": f"Blend file not found: {abs_path}"}
        data_type = (cmd.get("data_type") or "objects").lower()
        valid_types = {"objects", "materials", "meshes", "node_groups", "collections", "worlds"}
        if data_type not in valid_types:
            return {"ok": False, "error": f"data_type must be one of {sorted(valid_types)}"}
        item_names = cmd.get("names") or ([cmd.get("name")] if cmd.get("name") else None)
        if not item_names or not all(isinstance(n, str) for n in item_names):
            return {"ok": False, "error": "'names' must be a non-empty list of strings"}

        link_mode = (tool == "link_from_blend")
        # Resolve the target collection for objects (only relevant when
        # data_type == 'objects'; materials/meshes go straight into bpy.data).
        target_coll = None
        if data_type in ("objects", "collections"):
            target_name = cmd.get("collection_name")
            if target_name:
                target_coll = bpy.data.collections.get(target_name)
                if target_coll is None:
                    return {"ok": False, "error": f"Target collection {target_name!r} not found"}
            else:
                target_coll = bpy.context.scene.collection

        try:
            with bpy.data.libraries.load(abs_path, link=link_mode) as (src, dst):
                available = set(getattr(src, data_type))
                missing = [n for n in item_names if n not in available]
                if missing:
                    return {
                        "ok": False,
                        "error": f"Items not found in {os.path.basename(abs_path)}: {missing}. Available: {sorted(available)[:20]}",
                    }
                setattr(dst, data_type, list(item_names))
        except Exception as exc:
            return {"ok": False, "error": f"libraries.load failed: {exc}"}

        # The `with` block mutated `dst` in place; retrieve the loaded
        # datablocks by name from bpy.data now that the load has completed.
        data_collection = getattr(bpy.data, data_type)
        loaded = [data_collection.get(n) for n in item_names]
        loaded = [item for item in loaded if item is not None]

        linked_into_scene = 0
        if data_type == "objects" and target_coll is not None:
            for obj in loaded:
                if obj.name not in target_coll.objects:
                    target_coll.objects.link(obj)
                    linked_into_scene += 1
        elif data_type == "collections" and target_coll is not None:
            for coll in loaded:
                try:
                    target_coll.children.link(coll)
                    linked_into_scene += 1
                except RuntimeError:
                    # Already linked somewhere — ignore.
                    pass

        return {
            "ok": True,
            "path": abs_path,
            "mode": "link" if link_mode else "append",
            "data_type": data_type,
            "loaded": [item.name for item in loaded],
            "linked_into_collection": target_coll.name if target_coll else None,
            "count": linked_into_scene,
        }

    # ---------- Phase 10b — Render settings / compositor / export ----------

    if tool == "set_render_engine":
        scene = bpy.context.scene
        raw = (cmd.get("engine") or "").upper()
        canonical = _RENDER_ENGINE_ALIASES.get(raw)
        if canonical is None:
            return {"ok": False, "error": f"Unknown engine {raw!r}. Valid: {sorted(set(_RENDER_ENGINE_ALIASES.values()))}"}
        try:
            scene.render.engine = canonical
        except TypeError as exc:
            return {"ok": False, "error": f"Could not set engine to {canonical!r}: {exc}"}
        return {"ok": True, "engine": scene.render.engine}

    if tool == "set_render_settings":
        scene = bpy.context.scene
        render = scene.render
        width = cmd.get("width")
        if width is not None:
            render.resolution_x = int(width)
        height = cmd.get("height")
        if height is not None:
            render.resolution_y = int(height)
        pct = cmd.get("resolution_percentage")
        if pct is not None:
            render.resolution_percentage = int(pct)

        samples = cmd.get("samples")
        denoise = cmd.get("denoise")
        device = cmd.get("device")
        engine = render.engine

        if engine == "CYCLES":
            if samples is not None:
                scene.cycles.samples = int(samples)
            if denoise is not None:
                scene.cycles.use_denoising = bool(denoise)
            if device is not None:
                dev = str(device).upper()
                if dev not in ("CPU", "GPU"):
                    return {"ok": False, "error": "'device' must be CPU or GPU"}
                scene.cycles.device = dev
        elif engine == "BLENDER_EEVEE_NEXT":
            if samples is not None:
                try:
                    scene.eevee.taa_render_samples = int(samples)
                except AttributeError:
                    pass

        return {
            "ok": True,
            "engine": engine,
            "width": render.resolution_x,
            "height": render.resolution_y,
            "percentage": render.resolution_percentage,
            "samples": (
                scene.cycles.samples if engine == "CYCLES"
                else getattr(scene.eevee, "taa_render_samples", None)
            ),
        }

    if tool == "optimize_cycles":
        scene = bpy.context.scene
        if scene.render.engine != "CYCLES":
            scene.render.engine = "CYCLES"

        cycles = scene.cycles

        # 1. GPU rendering.
        device = (cmd.get("device") or "GPU").upper()
        cycles.device = device

        # 2. Noise threshold — adaptive sampling.
        noise_threshold = cmd.get("noise_threshold")
        if noise_threshold is not None:
            cycles.use_adaptive_sampling = True
            cycles.adaptive_threshold = float(noise_threshold)
        else:
            cycles.use_adaptive_sampling = True
            cycles.adaptive_threshold = 0.1  # fast default

        # Max samples (adaptive sampling will stop earlier if threshold met).
        samples = cmd.get("samples")
        if samples is not None:
            cycles.samples = int(samples)
        else:
            cycles.samples = 128  # reasonable cap

        # 3. Denoising.
        denoise = cmd.get("denoise")
        cycles.use_denoising = bool(denoise) if denoise is not None else True

        # 4. Light path bounces.
        bounces = cmd.get("max_bounces")
        if bounces is not None:
            cycles.max_bounces = int(bounces)
        else:
            cycles.max_bounces = 4  # fast default

        diffuse_bounces = cmd.get("diffuse_bounces")
        if diffuse_bounces is not None:
            cycles.diffuse_bounces = int(diffuse_bounces)
        else:
            cycles.diffuse_bounces = 2

        glossy_bounces = cmd.get("glossy_bounces")
        if glossy_bounces is not None:
            cycles.glossy_bounces = int(glossy_bounces)
        else:
            cycles.glossy_bounces = 2

        transmission_bounces = cmd.get("transmission_bounces")
        if transmission_bounces is not None:
            cycles.transmission_bounces = int(transmission_bounces)
        else:
            cycles.transmission_bounces = 2

        transparent_bounces = cmd.get("transparent_max_bounces")
        if transparent_bounces is not None:
            cycles.transparent_max_bounces = int(transparent_bounces)
        else:
            cycles.transparent_max_bounces = 4

        # 5. Caustics — disable for speed unless explicitly needed.
        caustics = cmd.get("caustics")
        if caustics is not None:
            cycles.caustics_reflective = bool(caustics)
            cycles.caustics_refractive = bool(caustics)
        else:
            cycles.caustics_reflective = False
            cycles.caustics_refractive = False

        # 6. Fast GI approximation.
        fast_gi = cmd.get("fast_gi")
        if fast_gi is not None:
            cycles.use_fast_gi = bool(fast_gi)
        else:
            cycles.use_fast_gi = True

        # 7. Performance — persistent data.
        persistent = cmd.get("persistent_data")
        if persistent is not None:
            scene.render.use_persistent_data = bool(persistent)
        else:
            scene.render.use_persistent_data = True

        return {
            "ok": True,
            "message": "Cycles optimized for fast rendering",
            "device": cycles.device,
            "noise_threshold": cycles.adaptive_threshold,
            "samples": cycles.samples,
            "denoising": cycles.use_denoising,
            "max_bounces": cycles.max_bounces,
            "caustics": cycles.caustics_reflective,
            "fast_gi": cycles.use_fast_gi,
            "persistent_data": scene.render.use_persistent_data,
        }

    if tool == "set_color_management":
        scene = bpy.context.scene
        view = scene.view_settings
        vt = cmd.get("view_transform")
        if vt is not None:
            canonical = _VIEW_TRANSFORM_ALIASES.get(str(vt).upper(), vt)
            try:
                view.view_transform = canonical
            except TypeError as exc:
                return {"ok": False, "error": f"Unknown view_transform {canonical!r}: {exc}"}
        look = cmd.get("look")
        if look is not None:
            valid_looks = [
                item.identifier
                for item in view.bl_rna.properties["look"].enum_items
            ]
            resolved = _resolve_look(look, view.view_transform, valid_looks)
            if resolved is None:
                return {
                    "ok": False,
                    "error": (
                        f"Unknown look {look!r} for view_transform "
                        f"{view.view_transform!r}. Valid looks: {valid_looks}"
                    ),
                }
            view.look = resolved
        exposure = cmd.get("exposure")
        if exposure is not None:
            view.exposure = float(exposure)
        gamma = cmd.get("gamma")
        if gamma is not None:
            view.gamma = float(gamma)
        return {
            "ok": True,
            "view_transform": view.view_transform,
            "look": view.look,
            "exposure": view.exposure,
            "gamma": view.gamma,
        }

    if tool == "enable_compositor":
        scene = bpy.context.scene
        tree = _ensure_compositor_tree(scene)
        if tree is None:
            return {"ok": False, "error": "Failed to initialize compositor node tree"}
        return {
            "ok": True,
            "use_nodes": scene.use_nodes,
            "nodes": [n.name for n in tree.nodes],
        }

    if tool == "disable_compositor":
        scene = bpy.context.scene
        scene.use_nodes = False
        return {"ok": True, "use_nodes": False}

    if tool == "add_compositor_node":
        scene = bpy.context.scene
        tree = _ensure_compositor_tree(scene)
        if tree is None:
            return {"ok": False, "error": "Compositor node tree not available"}
        node_type_key = (cmd.get("node_type") or "").upper()
        bl_idname = _COMPOSITOR_NODE_TYPES.get(node_type_key)
        if bl_idname is None:
            return {
                "ok": False,
                "error": f"Unknown node_type {node_type_key!r}. Valid: {sorted(_COMPOSITOR_NODE_TYPES)}",
            }
        try:
            node = tree.nodes.new(bl_idname)
        except RuntimeError as exc:
            return {"ok": False, "error": f"Failed to create {node_type_key}: {exc}"}
        location = cmd.get("location")
        if location and len(location) == 2:
            node.location = (float(location[0]), float(location[1]))
        desired_name = cmd.get("name")
        if desired_name:
            node.name = desired_name
            node.label = desired_name
        params = cmd.get("params")
        if isinstance(params, dict):
            for key, value in params.items():
                ok, err_msg = _set_node_param(node, key, value)
                if not ok:
                    return {"ok": False, "error": err_msg}
        return {
            "ok": True,
            "node": {
                "name": node.name,
                "type": node_type_key,
                "bl_idname": node.bl_idname,
                "location": [node.location.x, node.location.y],
                "inputs": [s.name for s in node.inputs],
                "outputs": [s.name for s in node.outputs],
            },
        }

    if tool == "connect_compositor_nodes":
        scene = bpy.context.scene
        tree = _ensure_compositor_tree(scene)
        if tree is None:
            return {"ok": False, "error": "Compositor node tree not available"}
        from_node = tree.nodes.get(cmd.get("from_node"))
        to_node = tree.nodes.get(cmd.get("to_node"))
        if from_node is None:
            return {"ok": False, "error": f"No node named {cmd.get('from_node')!r} in compositor"}
        if to_node is None:
            return {"ok": False, "error": f"No node named {cmd.get('to_node')!r} in compositor"}
        from_socket = from_node.outputs.get(cmd.get("from_socket"))
        to_socket = to_node.inputs.get(cmd.get("to_socket"))
        if from_socket is None:
            return {"ok": False, "error": f"{from_node.name!r} has no output {cmd.get('from_socket')!r}. Available: {[s.name for s in from_node.outputs]}"}
        if to_socket is None:
            return {"ok": False, "error": f"{to_node.name!r} has no input {cmd.get('to_socket')!r}. Available: {[s.name for s in to_node.inputs]}"}
        link = tree.links.new(from_socket, to_socket)
        return {
            "ok": True,
            "from": f"{from_node.name}.{from_socket.name}",
            "to": f"{to_node.name}.{to_socket.name}",
            "valid": link.is_valid,
        }

    if tool == "export_scene":
        fmt = (cmd.get("format") or "").upper()
        if fmt not in _EXPORT_FORMATS:
            return {"ok": False, "error": f"Unknown format {fmt!r}. Valid: {sorted(_EXPORT_FORMATS)}"}
        path = cmd.get("path")
        if not path:
            return {"ok": False, "error": "'path' is required"}
        abs_path = os.path.abspath(os.path.expanduser(path))
        os.makedirs(os.path.dirname(abs_path) or ".", exist_ok=True)
        selected_only = bool(cmd.get("selected_only", False))
        apply_modifiers = bool(cmd.get("apply_modifiers", True))
        try:
            _run_export(fmt, abs_path, selected_only, apply_modifiers)
        except RuntimeError as exc:
            return {"ok": False, "error": f"{fmt} export failed: {exc}"}
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return {
            "ok": True,
            "format": fmt,
            "path": abs_path,
            "selected_only": selected_only,
            "apply_modifiers": apply_modifiers,
        }

    if tool == "export_collection":
        coll_name = cmd.get("collection_name")
        coll = bpy.data.collections.get(coll_name) if coll_name else None
        if coll is None:
            return {"ok": False, "error": f"Collection {coll_name!r} not found"}
        fmt = (cmd.get("format") or "").upper()
        if fmt not in _EXPORT_FORMATS:
            return {"ok": False, "error": f"Unknown format {fmt!r}. Valid: {sorted(_EXPORT_FORMATS)}"}
        path = cmd.get("path")
        if not path:
            return {"ok": False, "error": "'path' is required"}
        abs_path = os.path.abspath(os.path.expanduser(path))
        os.makedirs(os.path.dirname(abs_path) or ".", exist_ok=True)
        apply_modifiers = bool(cmd.get("apply_modifiers", True))

        # Walk the collection (including nested children) to gather every object
        # the user expects to export.
        def _all_objects(c):
            out = list(c.objects)
            for child in c.children:
                out.extend(_all_objects(child))
            return out

        objs = _all_objects(coll)
        if not objs:
            return {"ok": False, "error": f"Collection {coll_name!r} has no objects"}

        # Remember the current selection / active object so we can restore it.
        prev_selected = list(bpy.context.selected_objects)
        prev_active = bpy.context.view_layer.objects.active
        for o in list(bpy.context.selected_objects):
            o.select_set(False)
        try:
            for o in objs:
                o.select_set(True)
            bpy.context.view_layer.objects.active = objs[0]
            _run_export(fmt, abs_path, selected_only=True, apply_modifiers=apply_modifiers)
        except RuntimeError as exc:
            return {"ok": False, "error": f"{fmt} export failed: {exc}"}
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        finally:
            for o in list(bpy.context.selected_objects):
                o.select_set(False)
            for o in prev_selected:
                try:
                    o.select_set(True)
                except ReferenceError:
                    pass
            if prev_active is not None:
                try:
                    bpy.context.view_layer.objects.active = prev_active
                except ReferenceError:
                    pass

        return {
            "ok": True,
            "format": fmt,
            "collection": coll.name,
            "path": abs_path,
            "object_count": len(objs),
            "apply_modifiers": apply_modifiers,
        }

    # ---------- Phase 17 — Python escape hatch ----------

    if tool == "execute_python":
        if not _python_allowed:
            return {
                "ok": False,
                "error": (
                    "Python execution is disabled. Open Blender → N-panel → "
                    "JustThreed → click 'Enable Python' to allow this session "
                    "to run arbitrary bpy code."
                ),
            }
        code = cmd.get("code")
        if not isinstance(code, str) or not code.strip():
            return {"ok": False, "error": "'code' must be a non-empty string"}
        undo_label = cmd.get("undo_label") or "JustThreed Python"

        # Snapshot undo state before running so the user can Ctrl+Z the entire
        # block in one step. `undo_push` is a no-op in some contexts, so we
        # swallow RuntimeError rather than failing the whole call.
        try:
            bpy.ops.ed.undo_push(message=f"[{undo_label}] start")
        except RuntimeError:
            pass

        # Log to Blender's console so the user can always see what ran, even
        # if the MCP client's output was dropped or truncated.
        print(f"[JustThreed] execute_python (label={undo_label!r}):")
        for line in code.splitlines():
            print(f"[JustThreed] | {line}")

        script_globals = {
            "bpy": bpy,
            "bmesh": bmesh,
            "math": math,
            "Vector": Vector,
            "__name__": "__justthreed_script__",
        }
        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()
        result_value = None
        try:
            with contextlib.redirect_stdout(stdout_buf), contextlib.redirect_stderr(stderr_buf):
                # Try eval() first so a single expression returns a value.
                # Fall back to exec() for statements / multi-line scripts.
                try:
                    compiled = compile(code, "<justthreed>", "eval")
                    result_value = eval(compiled, script_globals)
                except SyntaxError:
                    compiled = compile(code, "<justthreed>", "exec")
                    exec(compiled, script_globals)
                    # Convention: if the script sets `_result`, surface it back.
                    result_value = script_globals.get("_result")
        except Exception as exc:
            tb = traceback.format_exc()
            print(f"[JustThreed] execute_python failed: {exc}")
            return {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "stdout": stdout_buf.getvalue(),
                "stderr": stderr_buf.getvalue(),
                "traceback": tb,
            }

        try:
            bpy.ops.ed.undo_push(message=f"[{undo_label}] done")
        except RuntimeError:
            pass

        result_repr = None
        if result_value is not None:
            try:
                result_repr = repr(result_value)
                if len(result_repr) > 4000:
                    result_repr = result_repr[:4000] + "... (truncated)"
            except Exception:
                result_repr = "<unrepresentable>"

        return {
            "ok": True,
            "undo_label": undo_label,
            "stdout": stdout_buf.getvalue(),
            "stderr": stderr_buf.getvalue(),
            "result_repr": result_repr,
        }

    # ---------- Pro modeling tools — curves, revolve, reference workflow ----------

    if tool == "create_bezier_curve":
        points = cmd.get("points")  # list of [x, y, z] or [x, z] (2D profile)
        if not points or not isinstance(points, list) or len(points) < 2:
            return {"ok": False, "error": "'points' must be a list of at least 2 coordinate arrays"}

        name = cmd.get("name") or "ProfileCurve"
        closed = bool(cmd.get("closed", False))

        # Normalize to 3D — if 2D points given, treat as (X, Z) profile for revolving.
        pts_3d = []
        for p in points:
            if len(p) == 2:
                pts_3d.append((float(p[0]), 0.0, float(p[1])))
            elif len(p) >= 3:
                pts_3d.append((float(p[0]), float(p[1]), float(p[2])))
            else:
                return {"ok": False, "error": f"Each point needs 2 or 3 coordinates, got {len(p)}"}

        # Create curve data.
        curve_data = bpy.data.curves.new(name=name, type='CURVE')
        curve_data.dimensions = '3D'
        curve_data.resolution_u = 24

        spline = curve_data.splines.new('BEZIER')
        spline.bezier_points.add(len(pts_3d) - 1)  # one already exists
        spline.use_cyclic_u = closed

        for i, co in enumerate(pts_3d):
            bp = spline.bezier_points[i]
            bp.co = co
            bp.handle_left_type = 'AUTO'
            bp.handle_right_type = 'AUTO'

        curve_obj = bpy.data.objects.new(name, curve_data)
        bpy.context.collection.objects.link(curve_obj)
        _select_only(curve_obj)

        return {
            "ok": True,
            "message": f"Created Bezier curve '{curve_obj.name}' with {len(pts_3d)} control points",
            "name": curve_obj.name,
            "point_count": len(pts_3d),
            "closed": closed,
        }

    if tool == "revolve_curve":
        obj, err = _resolve_object(cmd.get("name"))
        if err:
            return err

        axis = (cmd.get("axis") or "Z").upper()
        if axis not in ("X", "Y", "Z"):
            return {"ok": False, "error": f"axis must be X, Y, or Z, got {axis!r}"}
        angle_deg = float(cmd.get("angle") or 360)
        steps = int(cmd.get("steps") or 64)
        convert_to_mesh = cmd.get("convert_to_mesh", True)

        _select_only(obj)

        # Add Screw modifier.
        mod = obj.modifiers.new(name="Revolve", type='SCREW')
        mod.axis = axis
        mod.angle = math.radians(angle_deg)
        mod.steps = steps
        mod.render_steps = steps
        mod.use_merge_vertices = True
        mod.merge_threshold = 0.001

        # Optionally add Subdivision Surface for smoothness.
        if cmd.get("subdivisions"):
            sub = obj.modifiers.new(name="Smooth", type='SUBSURF')
            sub.levels = int(cmd.get("subdivisions"))
            sub.render_levels = int(cmd.get("subdivisions"))

        # Convert to mesh so subsequent tools (extrude, bevel, etc.) work.
        if convert_to_mesh:
            bpy.ops.object.convert(target='MESH')

        return {
            "ok": True,
            "message": f"Revolved '{obj.name}' {angle_deg}° around {axis} axis ({steps} steps)",
            "name": obj.name,
            "axis": axis,
            "angle": angle_deg,
            "steps": steps,
            "converted_to_mesh": convert_to_mesh,
            "vertex_count": len(obj.data.vertices) if obj.type == 'MESH' else None,
        }

    if tool == "set_reference_image":
        file_path = cmd.get("file_path")
        if not file_path or not isinstance(file_path, str):
            return {"ok": False, "error": "'file_path' is required"}

        import pathlib
        fp = pathlib.Path(file_path)
        if not fp.is_file():
            return {"ok": False, "error": f"File not found: {file_path}"}

        # Load or reuse the image datablock.
        img_name = fp.stem
        img = bpy.data.images.get(img_name)
        if img is None:
            img = bpy.data.images.load(str(fp))
            img.name = img_name

        # Create an Empty → Image reference in the scene (visible in viewport).
        empty = bpy.data.objects.new(f"Ref_{img_name}", None)
        empty.empty_display_type = 'IMAGE'
        empty.data = img
        empty.empty_display_size = float(cmd.get("size") or 5.0)
        empty.empty_image_side = 'FRONT'

        # Position it: default at origin facing front, slight offset behind.
        loc = cmd.get("location")
        if loc and len(loc) >= 3:
            empty.location = (float(loc[0]), float(loc[1]), float(loc[2]))
        else:
            empty.location = (0, -0.5, float(cmd.get("height") or 0))

        # Make semi-transparent so it doesn't obscure the model.
        empty.color[3] = float(cmd.get("opacity") or 0.5)
        empty.show_in_front = True

        bpy.context.collection.objects.link(empty)

        return {
            "ok": True,
            "message": f"Reference image '{img_name}' loaded as viewport guide",
            "name": empty.name,
            "image": img_name,
            "size": empty.empty_display_size,
        }

    # ---------- Stage A — import mesh from file (for ML pipeline retrieval) ----------

    if tool == "import_mesh_file":
        file_path = cmd.get("file_path")
        if not file_path or not isinstance(file_path, str):
            return {"ok": False, "error": "'file_path' is required"}

        import pathlib
        fp = pathlib.Path(file_path)
        if not fp.is_file():
            return {"ok": False, "error": f"File not found: {file_path}"}

        ext = fp.suffix.lower()
        before_names = set(o.name for o in bpy.data.objects)

        try:
            if ext in (".glb", ".gltf"):
                bpy.ops.import_scene.gltf(filepath=str(fp))
            elif ext == ".obj":
                bpy.ops.wm.obj_import(filepath=str(fp))
            elif ext == ".fbx":
                bpy.ops.import_scene.fbx(filepath=str(fp))
            elif ext == ".stl":
                bpy.ops.import_mesh.stl(filepath=str(fp))
            elif ext == ".ply":
                bpy.ops.import_mesh.ply(filepath=str(fp))
            else:
                return {"ok": False, "error": f"Unsupported format: {ext}"}
        except Exception as exc:
            return {"ok": False, "error": f"Import failed: {exc}"}

        after_names = set(o.name for o in bpy.data.objects)
        new_names = sorted(after_names - before_names)

        # Optionally rename the root imported object.
        rename = cmd.get("object_name")
        if rename and new_names:
            root = bpy.data.objects.get(new_names[0])
            if root:
                root.name = rename
                new_names[0] = rename

        return {
            "ok": True,
            "message": f"Imported {len(new_names)} object(s) from {fp.name}",
            "imported_objects": new_names,
            "format": ext,
        }

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


class JUSTTHREED_OT_toggle_python(bpy.types.Operator):
    """Toggle the Python escape hatch. While enabled, MCP clients can run
    arbitrary bpy code via the execute_python tool — this is powerful but
    potentially dangerous (file writes, preferences, shelling out)."""
    bl_idname = "justthreed.toggle_python"
    bl_label = "Toggle Python Execution"

    def execute(self, context):
        global _python_allowed
        _python_allowed = not _python_allowed
        state = "enabled" if _python_allowed else "disabled"
        level = 'WARNING' if _python_allowed else 'INFO'
        self.report({level}, f"JustThreed Python execution {state}")
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
        col.label(text="Python escape hatch", icon='SCRIPT')
        if _python_allowed:
            col.operator(
                "justthreed.toggle_python", text="Disable Python", icon='CHECKMARK'
            )
            col.label(text="Python: ENABLED", icon='ERROR')
        else:
            col.operator(
                "justthreed.toggle_python", text="Enable Python", icon='LOCKED'
            )
            col.label(text="Python: disabled", icon='CHECKMARK')
        col.separator()
        col.operator("justthreed.hello", icon='QUESTION')


# ---------- Registration ----------

classes = (
    JUSTTHREED_OT_hello,
    JUSTTHREED_OT_start_server,
    JUSTTHREED_OT_stop_server,
    JUSTTHREED_OT_toggle_python,
    JUSTTHREED_PT_panel,
)


@bpy.app.handlers.persistent
def _on_load_post(_dummy):
    """Re-register the dispatcher timer after a file load.

    ``bpy.app.timers`` are cleared when ``wm.open_mainfile`` runs, so the
    tool ``open_blend_file`` would otherwise leave the extension unable to
    process any further jobs. This ``persistent`` handler fires after every
    load and re-registers the timer if the server is still running.
    """
    if _running and not bpy.app.timers.is_registered(_process_jobs):
        bpy.app.timers.register(_process_jobs)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    if _on_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load_post)


def unregister():
    _stop_server()
    if _on_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load_post)
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
