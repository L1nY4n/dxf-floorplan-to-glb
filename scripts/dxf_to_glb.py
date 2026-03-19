#!/usr/bin/env python3
"""DXF floorplan to configurable GLB with interactive preview workbench.

Modes:
- build: one-shot conversion to GLB/JSON/HTML
- serve: local workbench with rebuild and material/profile APIs
"""

from __future__ import annotations

import argparse
import array
import json
import math
import os
import re
import struct
import threading
import time
from dataclasses import dataclass
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import ezdxf

JSON_CHUNK_TYPE = 0x4E4F534A
BIN_CHUNK_TYPE = 0x004E4942

SEMANTIC_MATERIALS = ("ground", "wall", "door")

DEFAULT_GEOMETRY = {
    "door_mode": "none",
    "center_mode": "grounded-xz",
    "unit_scale": 0.001,
    "wall_height_m": 3.8,
    "door_height_m": 2.4,
    "wall_thickness_mm": 120.0,
    "column_thickness_mm": 180.0,
    "ground_thickness_m": 0.08,
    "ground_margin_m": 0.6,
    "window_pad_mm": 20.0,
}

DEFAULT_PRESET = "crystal-purple"

DEFAULT_STYLE_PRESETS: dict[str, dict[str, Any]] = {
    "crystal-purple": {
        "materials": {
            "ground": {
                "baseColor": "#eef0f8",
                "opacity": 1.0,
                "roughness": 0.88,
                "metallic": 0.02,
                "emissive": "#000000",
                "specular": 0.42,
                "transmission": 0.0,
                "ior": 1.45,
                "doubleSided": True,
                "alphaMode": "OPAQUE",
            },
            "wall": {
                "baseColor": "#9d8bff",
                "opacity": 0.34,
                "roughness": 0.06,
                "metallic": 0.0,
                "emissive": "#120a36",
                "specular": 0.84,
                "transmission": 0.88,
                "ior": 1.48,
                "doubleSided": True,
                "alphaMode": "BLEND",
            },
            "door": {
                "baseColor": "#4f3ac6",
                "opacity": 0.78,
                "roughness": 0.18,
                "metallic": 0.05,
                "emissive": "#080419",
                "specular": 0.62,
                "transmission": 0.2,
                "ior": 1.45,
                "doubleSided": True,
                "alphaMode": "BLEND",
            },
}
    },
    "frosted-purple": {
        "materials": {
            "ground": {
                "baseColor": "#f1f3f8",
                "opacity": 1.0,
                "roughness": 0.9,
                "metallic": 0.0,
                "emissive": "#000000",
                "specular": 0.35,
                "transmission": 0.0,
                "ior": 1.45,
                "doubleSided": True,
                "alphaMode": "OPAQUE",
            },
            "wall": {
                "baseColor": "#a798ff",
                "opacity": 0.45,
                "roughness": 0.35,
                "metallic": 0.0,
                "emissive": "#0d0824",
                "specular": 0.35,
                "transmission": 0.42,
                "ior": 1.45,
                "doubleSided": True,
                "alphaMode": "BLEND",
            },
            "door": {
                "baseColor": "#5d4cc5",
                "opacity": 0.84,
                "roughness": 0.4,
                "metallic": 0.05,
                "emissive": "#090613",
                "specular": 0.3,
                "transmission": 0.08,
                "ior": 1.45,
                "doubleSided": True,
                "alphaMode": "BLEND",
            },
        }
    },
    "mirror-purple": {
        "materials": {
            "ground": {
                "baseColor": "#eceef6",
                "opacity": 1.0,
                "roughness": 0.86,
                "metallic": 0.02,
                "emissive": "#000000",
                "specular": 0.4,
                "transmission": 0.0,
                "ior": 1.45,
                "doubleSided": True,
                "alphaMode": "OPAQUE",
            },
            "wall": {
                "baseColor": "#8972ff",
                "opacity": 0.42,
                "roughness": 0.03,
                "metallic": 0.35,
                "emissive": "#0f0933",
                "specular": 1.0,
                "transmission": 0.65,
                "ior": 1.5,
                "doubleSided": True,
                "alphaMode": "BLEND",
            },
            "door": {
                "baseColor": "#4a35a3",
                "opacity": 0.86,
                "roughness": 0.08,
                "metallic": 0.25,
                "emissive": "#0c0820",
                "specular": 0.92,
                "transmission": 0.12,
                "ior": 1.48,
                "doubleSided": True,
                "alphaMode": "BLEND",
            },
        }
    },
}


@dataclass
class Footprint:
    x0: float
    y0: float
    x1: float
    y1: float
    semantic: str
    source: str

    @property
    def area(self) -> float:
        return max(0.0, (self.x1 - self.x0) * (self.y1 - self.y0))


@dataclass
class BuildStats:
    wall_count: int = 0
    door_count: int = 0
    window_count: int = 0
    filtered_door_count: int = 0


@dataclass
class BuildResult:
    output_glb: Path
    output_json: Path | None
    preview_html: Path | None
    metadata: dict[str, Any]


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def normalize_hex_color(raw: str | None, default: str) -> str:
    if not raw:
        return default
    value = raw.strip().lower()
    if value.startswith("#"):
        value = value[1:]
    if len(value) == 3 and re.fullmatch(r"[0-9a-f]{3}", value):
        value = "".join(ch * 2 for ch in value)
    if not re.fullmatch(r"[0-9a-f]{6}", value):
        return default
    return f"#{value}"


def hex_to_rgb01(value: str) -> list[float]:
    clean = value.strip().lstrip("#")
    if len(clean) != 6:
        return [1.0, 1.0, 1.0]
    return [
        int(clean[0:2], 16) / 255.0,
        int(clean[2:4], 16) / 255.0,
        int(clean[4:6], 16) / 255.0,
    ]


def normalize_material_entry(raw: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
    base = dict(fallback)
    if isinstance(raw, dict):
        base.update(raw)

    color = normalize_hex_color(str(base.get("baseColor", "#ffffff")), "#ffffff")
    emissive = normalize_hex_color(str(base.get("emissive", "#000000")), "#000000")

    return {
        "baseColor": color,
        "opacity": clamp(float(base.get("opacity", 1.0)), 0.0, 1.0),
        "roughness": clamp(float(base.get("roughness", 0.5)), 0.0, 1.0),
        "metallic": clamp(float(base.get("metallic", 0.0)), 0.0, 1.0),
        "emissive": emissive,
        "specular": clamp(float(base.get("specular", 0.5)), 0.0, 1.0),
        "transmission": clamp(float(base.get("transmission", 0.0)), 0.0, 1.0),
        "ior": clamp(float(base.get("ior", 1.45)), 1.0, 2.5),
        "doubleSided": bool(base.get("doubleSided", True)),
        "alphaMode": str(base.get("alphaMode", "BLEND" if float(base.get("opacity", 1.0)) < 1 else "OPAQUE")).upper(),
    }


def normalize_profile(profile_payload: dict[str, Any] | None, preset_name: str | None = None) -> dict[str, Any]:
    preset_key = preset_name or (profile_payload or {}).get("preset") or DEFAULT_PRESET
    if preset_key not in DEFAULT_STYLE_PRESETS:
        preset_key = DEFAULT_PRESET

    preset = DEFAULT_STYLE_PRESETS[preset_key]
    incoming_materials = {}
    if isinstance(profile_payload, dict):
        materials = profile_payload.get("materials", profile_payload)
        if isinstance(materials, dict):
            incoming_materials = materials

    materials: dict[str, dict[str, Any]] = {}
    for name in SEMANTIC_MATERIALS:
        materials[name] = normalize_material_entry(
            incoming_materials.get(name, {}) if isinstance(incoming_materials.get(name), dict) else {},
            preset["materials"][name],
        )

    return {
        "preset": preset_key,
        "materials": materials,
    }


def load_profile_from_file(path: Path | None) -> dict[str, Any]:
    if path is None:
        return normalize_profile(None, DEFAULT_PRESET)
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        return normalize_profile(raw)
    raise ValueError(f"Invalid material profile JSON in {path}")


def get_script_dir() -> Path:
    return Path(__file__).resolve().parent


def get_default_profile_path() -> Path:
    return get_script_dir().parent / "assets" / "material_profile_crystal_purple.json"


def ensure_defaults_material_profile() -> dict[str, Any]:
    path = get_default_profile_path()
    if path.exists():
        return load_profile_from_file(path)
    profile = normalize_profile(None, DEFAULT_PRESET)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(profile, indent=2), encoding="utf-8")
    return profile


def segment_bbox(x1: float, y1: float, x2: float, y2: float, thickness: float) -> tuple[float, float, float, float]:
    half = thickness / 2.0
    if abs(y1 - y2) < 1e-6:
        xa, xb = min(x1, x2), max(x1, x2)
        ya, yb = y1 - half, y1 + half
    elif abs(x1 - x2) < 1e-6:
        xa, xb = x1 - half, x1 + half
        ya, yb = min(y1, y2), max(y1, y2)
    else:
        xa, xb = min(x1, x2) - half, max(x1, x2) + half
        ya, yb = min(y1, y2) - half, max(y1, y2) + half
    return xa, ya, xb, yb


def append_line_footprint(
    footprints: list[Footprint],
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    thickness: float,
    semantic: str,
    source: str,
) -> None:
    if abs(x1 - x2) < 1e-6 and abs(y1 - y2) < 1e-6:
        return
    xa, ya, xb, yb = segment_bbox(x1, y1, x2, y2, thickness)
    if xb - xa <= 1e-6 or yb - ya <= 1e-6:
        return
    footprints.append(Footprint(xa, ya, xb, yb, semantic=semantic, source=source))


def block_local_bbox(doc: ezdxf.document.Drawing, block_name: str) -> tuple[float, float, float, float] | None:
    block = doc.blocks.get(block_name)
    if block is None:
        return None

    points: list[tuple[float, float]] = []
    for entity in block:
        kind = entity.dxftype()
        if kind == "LINE":
            points.append((entity.dxf.start.x, entity.dxf.start.y))
            points.append((entity.dxf.end.x, entity.dxf.end.y))
        elif kind == "LWPOLYLINE":
            points.extend((p[0], p[1]) for p in entity.get_points())
        elif kind in {"CIRCLE", "ARC"}:
            cx = entity.dxf.center.x
            cy = entity.dxf.center.y
            r = entity.dxf.radius
            points.extend(((cx - r, cy - r), (cx + r, cy + r)))

    if not points:
        return None

    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def transformed_insert_bbox(
    insert_entity: ezdxf.entities.Insert,
    local_bbox: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    lx0, ly0, lx1, ly1 = local_bbox
    corners = [(lx0, ly0), (lx1, ly0), (lx1, ly1), (lx0, ly1)]

    ix = insert_entity.dxf.insert.x
    iy = insert_entity.dxf.insert.y
    sx = float(getattr(insert_entity.dxf, "xscale", 1.0) or 1.0)
    sy = float(getattr(insert_entity.dxf, "yscale", 1.0) or 1.0)
    rot = math.radians(float(getattr(insert_entity.dxf, "rotation", 0.0) or 0.0))

    cos_a = math.cos(rot)
    sin_a = math.sin(rot)

    world: list[tuple[float, float]] = []
    for x, y in corners:
        x = x * sx
        y = y * sy
        rx = x * cos_a - y * sin_a
        ry = x * sin_a + y * cos_a
        world.append((ix + rx, iy + ry))

    xs = [item[0] for item in world]
    ys = [item[1] for item in world]
    return min(xs), min(ys), max(xs), max(ys)


def collect_footprints(
    input_dxf: Path,
    geometry: dict[str, Any],
) -> tuple[list[Footprint], BuildStats]:
    doc = ezdxf.readfile(input_dxf)
    model = doc.modelspace()

    wall_thickness = float(geometry["wall_thickness_mm"])
    column_thickness = float(geometry["column_thickness_mm"])
    window_pad = float(geometry["window_pad_mm"])
    door_mode = str(geometry["door_mode"])

    footprints: list[Footprint] = []
    stats = BuildStats()

    for entity in model.query('LINE[layer=="WALL"]'):
        stats.wall_count += 1
        append_line_footprint(
            footprints,
            entity.dxf.start.x,
            entity.dxf.start.y,
            entity.dxf.end.x,
            entity.dxf.end.y,
            thickness=wall_thickness,
            semantic="wall",
            source="wall_line",
        )

    for entity in model.query('LWPOLYLINE[layer=="WALL"]'):
        points = [(p[0], p[1]) for p in entity.get_points()]
        if len(points) < 2:
            continue
        stats.wall_count += 1
        for a, b in zip(points, points[1:]):
            append_line_footprint(
                footprints,
                a[0],
                a[1],
                b[0],
                b[1],
                thickness=wall_thickness,
                semantic="wall",
                source="wall_poly_segment",
            )

    for entity in model.query('LINE[layer=="COLUMN"]'):
        stats.wall_count += 1
        append_line_footprint(
            footprints,
            entity.dxf.start.x,
            entity.dxf.start.y,
            entity.dxf.end.x,
            entity.dxf.end.y,
            thickness=column_thickness,
            semantic="wall",
            source="column_line",
        )

    block_bbox_cache: dict[str, tuple[float, float, float, float] | None] = {}
    for insert in model.query('INSERT[layer=="WINDOW"]'):
        stats.window_count += 1
        block_name = str(insert.dxf.name)
        if block_name not in block_bbox_cache:
            block_bbox_cache[block_name] = block_local_bbox(doc, block_name)
        local_bbox = block_bbox_cache[block_name]
        if local_bbox is None:
            continue

        x0, y0, x1, y1 = transformed_insert_bbox(insert, local_bbox)
        x0 -= window_pad
        y0 -= window_pad
        x1 += window_pad
        y1 += window_pad

        semantic = "wall"
        if "dor" in block_name.lower():
            stats.door_count += 1
            if door_mode == "none":
                stats.filtered_door_count += 1
                continue
            semantic = "door"

        if x1 - x0 <= 1e-6 or y1 - y0 <= 1e-6:
            continue
        footprints.append(Footprint(x0, y0, x1, y1, semantic=semantic, source=f"window_insert:{block_name}"))

    if not footprints:
        raise ValueError("No usable floorplan footprints were extracted from DXF.")

    return footprints, stats


def add_box(
    vertices: list[tuple[float, float, float]],
    faces: list[tuple[int, int, int]],
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    z0: float,
    z1: float,
) -> None:
    if x1 <= x0 or y1 <= y0 or z1 <= z0:
        return

    base = len(vertices) + 1
    vertices.extend(
        [
            (x0, y0, z0),
            (x1, y0, z0),
            (x1, y1, z0),
            (x0, y1, z0),
            (x0, y0, z1),
            (x1, y0, z1),
            (x1, y1, z1),
            (x0, y1, z1),
        ]
    )

    idx = [base + i for i in range(8)]
    faces.extend(
        [
            (idx[0], idx[2], idx[1]),
            (idx[0], idx[3], idx[2]),
            (idx[4], idx[5], idx[6]),
            (idx[4], idx[6], idx[7]),
            (idx[0], idx[1], idx[5]),
            (idx[0], idx[5], idx[4]),
            (idx[1], idx[2], idx[6]),
            (idx[1], idx[6], idx[5]),
            (idx[2], idx[3], idx[7]),
            (idx[2], idx[7], idx[6]),
            (idx[3], idx[0], idx[4]),
            (idx[3], idx[4], idx[7]),
        ]
    )


def compute_bbox(vertices: list[tuple[float, float, float]]) -> dict[str, float]:
    xs = [v[0] for v in vertices]
    ys = [v[1] for v in vertices]
    zs = [v[2] for v in vertices]
    return {
        "min_x": min(xs),
        "min_y": min(ys),
        "min_z": min(zs),
        "max_x": max(xs),
        "max_y": max(ys),
        "max_z": max(zs),
    }


def transform_vertices(
    vertices: list[tuple[float, float, float]],
    center_mode: str,
) -> tuple[list[tuple[float, float, float]], dict[str, float], dict[str, float], dict[str, float]]:
    before = compute_bbox(vertices)

    if center_mode == "none":
        offset = {"x": 0.0, "y": 0.0, "z": 0.0}
    elif center_mode == "bbox-center":
        offset = {
            "x": -((before["min_x"] + before["max_x"]) * 0.5),
            "y": -((before["min_y"] + before["max_y"]) * 0.5),
            "z": -((before["min_z"] + before["max_z"]) * 0.5),
        }
    elif center_mode == "grounded-xz":
        offset = {
            "x": -((before["min_x"] + before["max_x"]) * 0.5),
            "y": -before["min_y"],
            "z": -((before["min_z"] + before["max_z"]) * 0.5),
        }
    else:
        raise ValueError(f"Unsupported center-mode: {center_mode}")

    transformed = [(x + offset["x"], y + offset["y"], z + offset["z"]) for x, y, z in vertices]
    after = compute_bbox(transformed)
    return transformed, before, after, offset


def zup_to_yup(vertices: list[tuple[float, float, float]]) -> list[tuple[float, float, float]]:
    # z-up (x,y,z) -> y-up (x,y,z)
    return [(x, z, -y) for x, y, z in vertices]


def extract_indexed_mesh(
    vertices: list[tuple[float, float, float]],
    faces: list[tuple[int, int, int]],
) -> tuple[list[tuple[float, float, float]], list[int]]:
    index_map: dict[int, int] = {}
    packed_positions: list[tuple[float, float, float]] = []
    packed_indices: list[int] = []

    for a, b, c in faces:
        for source_index_1based in (a, b, c):
            source_index = source_index_1based - 1
            mapped = index_map.get(source_index)
            if mapped is None:
                mapped = len(packed_positions)
                index_map[source_index] = mapped
                packed_positions.append(vertices[source_index])
            packed_indices.append(mapped)

    return packed_positions, packed_indices


def bounds_of_positions(positions: list[tuple[float, float, float]]) -> tuple[list[float], list[float]]:
    xs = [p[0] for p in positions]
    ys = [p[1] for p in positions]
    zs = [p[2] for p in positions]
    return [min(xs), min(ys), min(zs)], [max(xs), max(ys), max(zs)]


def padded_4(data: bytes, pad_byte: bytes = b"\x00") -> bytes:
    pad_size = (4 - (len(data) % 4)) % 4
    if pad_size == 0:
        return data
    return data + (pad_byte * pad_size)


def material_to_gltf(name: str, spec: dict[str, Any]) -> tuple[dict[str, Any], set[str]]:
    rgb = hex_to_rgb01(spec["baseColor"])
    emissive = hex_to_rgb01(spec["emissive"])
    alpha = spec["opacity"]

    material: dict[str, Any] = {
        "name": name,
        "doubleSided": bool(spec.get("doubleSided", True)),
        "pbrMetallicRoughness": {
            "baseColorFactor": [rgb[0], rgb[1], rgb[2], alpha],
            "metallicFactor": float(spec["metallic"]),
            "roughnessFactor": float(spec["roughness"]),
        },
    }

    alpha_mode = str(spec.get("alphaMode", "OPAQUE")).upper()
    if alpha_mode not in {"OPAQUE", "MASK", "BLEND"}:
        alpha_mode = "BLEND" if alpha < 1.0 else "OPAQUE"
    material["alphaMode"] = alpha_mode
    if alpha_mode == "MASK":
        material["alphaCutoff"] = 0.5

    if any(channel > 0.0 for channel in emissive):
        material["emissiveFactor"] = emissive

    extensions: dict[str, Any] = {}
    extensions_used: set[str] = set()

    transmission = float(spec.get("transmission", 0.0))
    if transmission > 0:
        extensions["KHR_materials_transmission"] = {"transmissionFactor": clamp(transmission, 0.0, 1.0)}
        extensions_used.add("KHR_materials_transmission")

    ior = float(spec.get("ior", 1.45))
    if ior > 0:
        extensions["KHR_materials_ior"] = {"ior": clamp(ior, 1.0, 2.5)}
        extensions_used.add("KHR_materials_ior")

    specular = float(spec.get("specular", 0.5))
    if specular > 0:
        extensions["KHR_materials_specular"] = {"specularFactor": clamp(specular, 0.0, 1.0)}
        extensions_used.add("KHR_materials_specular")

    if extensions:
        material["extensions"] = extensions

    return material, extensions_used


def build_gltf(
    vertices: list[tuple[float, float, float]],
    faces_by_semantic: dict[str, list[tuple[int, int, int]]],
    profile: dict[str, Any],
) -> tuple[dict[str, Any], bytes]:
    materials: list[dict[str, Any]] = []
    material_index: dict[str, int] = {}
    extensions_used: set[str] = set()

    for name in SEMANTIC_MATERIALS:
        material, material_exts = material_to_gltf(name, profile["materials"][name])
        material_index[name] = len(materials)
        materials.append(material)
        extensions_used.update(material_exts)

    buffer_blob = bytearray()
    buffer_views: list[dict[str, Any]] = []
    accessors: list[dict[str, Any]] = []
    primitives: list[dict[str, Any]] = []

    def append_buffer_view(data: bytes, target: int | None = None) -> int:
        offset = len(buffer_blob)
        buffer_blob.extend(data)
        while len(buffer_blob) % 4 != 0:
            buffer_blob.append(0)
        view = {
            "buffer": 0,
            "byteOffset": offset,
            "byteLength": len(data),
        }
        if target is not None:
            view["target"] = target
        buffer_views.append(view)
        return len(buffer_views) - 1

    def append_accessor(
        view_index: int,
        component_type: int,
        count: int,
        gltf_type: str,
        mins: list[float] | None = None,
        maxs: list[float] | None = None,
    ) -> int:
        accessor: dict[str, Any] = {
            "bufferView": view_index,
            "componentType": component_type,
            "count": count,
            "type": gltf_type,
        }
        if mins is not None:
            accessor["min"] = mins
        if maxs is not None:
            accessor["max"] = maxs
        accessors.append(accessor)
        return len(accessors) - 1

    for semantic in SEMANTIC_MATERIALS:
        faces = faces_by_semantic.get(semantic, [])
        if not faces:
            continue

        positions, indices = extract_indexed_mesh(vertices, faces)
        if not positions or not indices:
            continue

        flat_positions = [coord for pos in positions for coord in pos]
        pos_bytes = struct.pack(f"<{len(flat_positions)}f", *flat_positions)

        idx_array = array.array("I", indices)
        if os.sys.byteorder != "little":
            idx_array.byteswap()
        idx_bytes = idx_array.tobytes()

        pos_view = append_buffer_view(pos_bytes, target=34962)
        idx_view = append_buffer_view(idx_bytes, target=34963)

        mins, maxs = bounds_of_positions(positions)
        pos_accessor = append_accessor(
            view_index=pos_view,
            component_type=5126,
            count=len(positions),
            gltf_type="VEC3",
            mins=mins,
            maxs=maxs,
        )
        idx_accessor = append_accessor(
            view_index=idx_view,
            component_type=5125,
            count=len(indices),
            gltf_type="SCALAR",
            mins=[0],
            maxs=[max(indices)],
        )

        primitives.append(
            {
                "attributes": {"POSITION": pos_accessor},
                "indices": idx_accessor,
                "material": material_index[semantic],
            }
        )

    if not primitives:
        raise ValueError("No mesh primitives generated.")

    gltf: dict[str, Any] = {
        "asset": {"version": "2.0", "generator": "dxf_to_glb.py"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0, "name": "DxfFloorplanScene"}],
        "meshes": [{"name": "DxfFloorplanMesh", "primitives": primitives}],
        "materials": materials,
        "buffers": [{"byteLength": len(buffer_blob)}],
        "bufferViews": buffer_views,
        "accessors": accessors,
    }
    if extensions_used:
        gltf["extensionsUsed"] = sorted(extensions_used)

    return gltf, bytes(buffer_blob)


def write_glb(
    output_glb: Path,
    vertices: list[tuple[float, float, float]],
    faces_by_semantic: dict[str, list[tuple[int, int, int]]],
    profile: dict[str, Any],
) -> None:
    gltf, bin_blob = build_gltf(vertices, faces_by_semantic, profile)
    json_bytes = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
    json_bytes = padded_4(json_bytes, pad_byte=b" ")
    bin_bytes = padded_4(bin_blob, pad_byte=b"\x00")

    total_length = 12 + 8 + len(json_bytes) + 8 + len(bin_bytes)
    header = struct.pack("<4sII", b"glTF", 2, total_length)
    json_header = struct.pack("<II", len(json_bytes), JSON_CHUNK_TYPE)
    bin_header = struct.pack("<II", len(bin_bytes), BIN_CHUNK_TYPE)

    output_glb.parent.mkdir(parents=True, exist_ok=True)
    with output_glb.open("wb") as handle:
        handle.write(header)
        handle.write(json_header)
        handle.write(json_bytes)
        handle.write(bin_header)
        handle.write(bin_bytes)


def to_serializable_bbox(bbox: dict[str, float]) -> dict[str, float]:
    return {k: round(v, 6) for k, v in bbox.items()}


def build_scene(
    input_dxf: Path,
    output_glb: Path,
    emit_json: Path | None,
    preview_html: Path | None,
    geometry: dict[str, Any],
    profile: dict[str, Any],
) -> BuildResult:
    footprints, stats = collect_footprints(input_dxf, geometry)
    unit_scale = float(geometry["unit_scale"])
    wall_height = float(geometry["wall_height_m"])
    door_height = float(geometry["door_height_m"])
    ground_thickness = float(geometry["ground_thickness_m"])
    ground_margin = float(geometry["ground_margin_m"])

    # Scale 2D footprints from CAD units to world units.
    scaled: list[Footprint] = []
    for fp in footprints:
        scaled.append(
            Footprint(
                x0=fp.x0 * unit_scale,
                y0=fp.y0 * unit_scale,
                x1=fp.x1 * unit_scale,
                y1=fp.y1 * unit_scale,
                semantic=fp.semantic,
                source=fp.source,
            )
        )

    min_x = min(fp.x0 for fp in scaled)
    min_y = min(fp.y0 for fp in scaled)
    max_x = max(fp.x1 for fp in scaled)
    max_y = max(fp.y1 for fp in scaled)

    vertices: list[tuple[float, float, float]] = []
    faces_by_semantic: dict[str, list[tuple[int, int, int]]] = {
        "ground": [],
        "wall": [],
        "door": [],
    }

    add_box(
        vertices,
        faces_by_semantic["ground"],
        min_x - ground_margin,
        min_y - ground_margin,
        max_x + ground_margin,
        max_y + ground_margin,
        0.0,
        ground_thickness,
    )

    for fp in scaled:
        faces: list[tuple[int, int, int]] = []
        height = wall_height if fp.semantic == "wall" else door_height
        add_box(
            vertices,
            faces,
            fp.x0,
            fp.y0,
            fp.x1,
            fp.y1,
            ground_thickness,
            ground_thickness + height,
        )
        faces_by_semantic[fp.semantic].extend(faces)

    # Convert to y-up and then apply center/ground policy.
    vertices_yup = zup_to_yup(vertices)
    centered_vertices, bbox_before, bbox_after, offset = transform_vertices(
        vertices_yup,
        center_mode=str(geometry["center_mode"]),
    )

    write_glb(output_glb, centered_vertices, faces_by_semantic, profile)

    metadata: dict[str, Any] = {
        "input": str(input_dxf.resolve()),
        "output_glb": str(output_glb.resolve()),
        "transform_policy": {
            "up_axis": "y",
            "center_mode": geometry["center_mode"],
            "offset": {k: round(v, 6) for k, v in offset.items()},
        },
        "params": geometry,
        "counts": {
            "wall_count": stats.wall_count,
            "door_count": stats.door_count,
            "window_count": stats.window_count,
            "filtered_door_count": stats.filtered_door_count,
            "footprints_total": len(footprints),
            "extruded_wall_boxes": len([f for f in scaled if f.semantic == "wall"]),
            "extruded_door_boxes": len([f for f in scaled if f.semantic == "door"]),
        },
        "bbox_before": to_serializable_bbox(bbox_before),
        "final_bbox": to_serializable_bbox(bbox_after),
        "material_profile": profile,
    }

    if emit_json:
        emit_json.parent.mkdir(parents=True, exist_ok=True)
        emit_json.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

    if preview_html:
        preview_html.parent.mkdir(parents=True, exist_ok=True)
        default_state = {
            "geometry": geometry,
            "profile": profile,
            "modelUrl": os.path.relpath(output_glb, preview_html.parent).replace("\\", "/"),
            "jsonUrl": os.path.relpath(emit_json, preview_html.parent).replace("\\", "/") if emit_json else None,
            "apiEnabled": False,
        }
        preview_html.write_text(render_preview_html(default_state), encoding="utf-8")

    return BuildResult(
        output_glb=output_glb,
        output_json=emit_json,
        preview_html=preview_html,
        metadata=metadata,
    )


def render_preview_html(default_state: dict[str, Any]) -> str:
    return f"""<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>DXF GLB Workbench</title>
  <style>
    :root {{
      --bg: #eff1f7;
      --panel: #ffffffcc;
      --line: #d9dde8;
      --text: #20242e;
      --muted: #6f788b;
      --accent: #7460f6;
    }}
    html, body {{ margin: 0; width: 100%; height: 100%; background: var(--bg); color: var(--text); font-family: ui-sans-serif, -apple-system, Segoe UI, Helvetica, Arial, sans-serif; }}
    #root {{ display: grid; grid-template-columns: minmax(320px, 400px) 1fr; height: 100%; }}
    #panel {{ overflow: auto; border-right: 1px solid var(--line); background: var(--panel); backdrop-filter: blur(8px); padding: 16px; }}
    #viewport {{ position: relative; }}
    #canvas-host {{ width: 100%; height: 100%; }}
    .section {{ border: 1px solid var(--line); border-radius: 12px; padding: 12px; margin-bottom: 12px; background: #fff; }}
    .section h3 {{ margin: 0 0 10px 0; font-size: 14px; }}
    .row {{ display: grid; grid-template-columns: 1fr 100px; gap: 8px; align-items: center; margin-bottom: 8px; }}
    .row label {{ font-size: 12px; color: var(--muted); }}
    .row input, .row select {{ width: 100%; box-sizing: border-box; }}
    .actions {{ display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }}
    button {{ border: 1px solid var(--line); border-radius: 10px; padding: 9px 10px; background: #fff; cursor: pointer; }}
    button.primary {{ background: var(--accent); color: #fff; border-color: var(--accent); }}
    button:disabled {{ opacity: 0.6; cursor: wait; }}
    .material-grid {{ display: grid; grid-template-columns: 1fr; gap: 8px; }}
    .material-card {{ border: 1px solid var(--line); border-radius: 10px; padding: 8px; }}
    .material-card h4 {{ margin: 0 0 8px 0; font-size: 12px; text-transform: uppercase; letter-spacing: 0.04em; color: var(--muted); }}
    #status {{ position: absolute; left: 12px; bottom: 12px; background: #111827cc; color: #e5e7eb; padding: 8px 10px; border-radius: 8px; font-size: 12px; max-width: 70%; }}
    .small {{ font-size: 11px; color: var(--muted); }}
    .mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 11px; }}
  </style>
</head>
<body>
<div id=\"root\">
  <aside id=\"panel\">
    <div class=\"section\">
      <h3>Geometry</h3>
      <div class=\"row\"><label for=\"doorMode\">door mode</label><select id=\"doorMode\"><option value=\"none\">none</option><option value=\"block\">block</option></select></div>
      <div class=\"row\"><label for=\"wallHeight\">wall-height-m</label><input id=\"wallHeight\" type=\"number\" min=\"0.1\" step=\"0.1\" /></div>
      <div class=\"row\"><label for=\"doorHeight\">door-height-m</label><input id=\"doorHeight\" type=\"number\" min=\"0.1\" step=\"0.1\" /></div>
      <div class=\"row\"><label for=\"wallThickness\">wall-thickness-mm</label><input id=\"wallThickness\" type=\"number\" min=\"1\" step=\"1\" /></div>
      <div class=\"row\"><label for=\"columnThickness\">column-thickness-mm</label><input id=\"columnThickness\" type=\"number\" min=\"1\" step=\"1\" /></div>
      <div class=\"row\"><label for=\"groundThickness\">ground-thickness-m</label><input id=\"groundThickness\" type=\"number\" min=\"0.01\" step=\"0.01\" /></div>
      <div class=\"row\"><label for=\"groundMargin\">ground-margin-m</label><input id=\"groundMargin\" type=\"number\" min=\"0\" step=\"0.1\" /></div>
    </div>

    <div class=\"section\">
      <h3>Materials</h3>
      <div class=\"row\"><label for=\"preset\">preset</label><select id=\"preset\"></select></div>
      <div class=\"material-grid\" id=\"materialCards\"></div>
      <p class=\"small\">Material edits apply immediately in preview. Geometry edits require rebuild.</p>
    </div>

    <div class=\"section\">
      <h3>Actions</h3>
      <div class=\"actions\">
        <button class=\"primary\" id=\"applyMaterials\">Apply Materials</button>
        <button class=\"primary\" id=\"rebuild\">Rebuild Geometry</button>
        <button id=\"rebuildWithProfile\">Rebuild + Write Profile</button>
        <button id=\"copyCmd\">Copy CLI Args</button>
        <button id=\"exportProfile\">Export Profile</button>
        <button id=\"importProfileBtn\">Import Profile</button>
      </div>
      <input type=\"file\" id=\"importProfileFile\" accept=\"application/json\" style=\"display:none\" />
      <p class=\"small mono\" id=\"paths\"></p>
    </div>
  </aside>
  <main id=\"viewport\">
    <div id=\"canvas-host\"></div>
    <div id=\"status\">Initializing preview...</div>
  </main>
</div>

<script type=\"importmap\">{{\"imports\":{{\"three\":\"https://unpkg.com/three@0.161.0/build/three.module.js\",\"three/addons/\":\"https://unpkg.com/three@0.161.0/examples/jsm/\"}}}}</script>
<script type=\"module\">
import * as THREE from 'three';
import {{ OrbitControls }} from 'three/addons/controls/OrbitControls.js';
import {{ GLTFLoader }} from 'three/addons/loaders/GLTFLoader.js';
import {{ RGBELoader }} from 'three/addons/loaders/RGBELoader.js';

const state = {json.dumps(default_state, ensure_ascii=False)};
const presets = {json.dumps(DEFAULT_STYLE_PRESETS, ensure_ascii=False)};
let currentProfile = structuredClone(state.profile);
let modelRoot = null;
let loading = false;

const statusEl = document.getElementById('status');
const pathsEl = document.getElementById('paths');
const canvasHost = document.getElementById('canvas-host');
const presetEl = document.getElementById('preset');

const geometryIds = {{
  door_mode: 'doorMode',
  wall_height_m: 'wallHeight',
  door_height_m: 'doorHeight',
  wall_thickness_mm: 'wallThickness',
  column_thickness_mm: 'columnThickness',
  ground_thickness_m: 'groundThickness',
  ground_margin_m: 'groundMargin',
}};

Object.keys(presets).forEach((key) => {{
  const opt = document.createElement('option');
  opt.value = key;
  opt.textContent = key;
  presetEl.appendChild(opt);
}});

const renderer = new THREE.WebGLRenderer({{ antialias: true }});
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
renderer.setSize(canvasHost.clientWidth, canvasHost.clientHeight);
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.0;
renderer.outputColorSpace = THREE.SRGBColorSpace;
canvasHost.appendChild(renderer.domElement);

const scene = new THREE.Scene();
scene.background = new THREE.Color('#eef1f8');

const camera = new THREE.PerspectiveCamera(52, canvasHost.clientWidth / canvasHost.clientHeight, 0.01, 10000);
camera.position.set(8, 8, 8);

const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.target.set(0, 1.5, 0);

const hemi = new THREE.HemisphereLight(0xffffff, 0xdde3f5, 0.8);
scene.add(hemi);
const keyLight = new THREE.DirectionalLight(0xffffff, 1.0);
keyLight.position.set(8, 12, 6);
scene.add(keyLight);
const fillLight = new THREE.DirectionalLight(0xffffff, 0.35);
fillLight.position.set(-6, 5, -8);
scene.add(fillLight);

const grid = new THREE.GridHelper(120, 120, 0xb8bfd2, 0xd5daea);
grid.position.y = 0;
scene.add(grid);

async function applyHdrEnvironment() {{
  try {{
    const hdr = await new RGBELoader().loadAsync('https://dl.polyhaven.org/file/ph-assets/HDRIs/hdr/1k/studio_small_09_1k.hdr');
    hdr.mapping = THREE.EquirectangularReflectionMapping;
    scene.environment = hdr;
  }} catch (err) {{
    console.warn('HDR fallback to basic lighting', err);
  }}
}}
applyHdrEnvironment();

function setStatus(msg) {{
  statusEl.textContent = msg;
}}

function resize() {{
  const w = canvasHost.clientWidth;
  const h = canvasHost.clientHeight;
  camera.aspect = w / Math.max(h, 1);
  camera.updateProjectionMatrix();
  renderer.setSize(w, h);
}}
window.addEventListener('resize', resize);

function materialFromName(name) {{
  if (!name) return null;
  if (name.includes('ground')) return 'ground';
  if (name.includes('door')) return 'door';
  if (name.includes('wall')) return 'wall';
  return null;
}}

function applyProfileToLoadedModel() {{
  if (!modelRoot) return;
  modelRoot.traverse((obj) => {{
    if (!obj.isMesh || !obj.material) return;
    const mats = Array.isArray(obj.material) ? obj.material : [obj.material];
    mats.forEach((m) => {{
      const group = materialFromName(m.name);
      if (!group || !currentProfile.materials[group]) return;
      const spec = currentProfile.materials[group];
      m.color.set(spec.baseColor);
      m.opacity = spec.opacity;
      m.transparent = spec.opacity < 0.999 || spec.alphaMode === 'BLEND';
      m.roughness = spec.roughness;
      m.metalness = spec.metallic;
      m.side = spec.doubleSided ? THREE.DoubleSide : THREE.FrontSide;
      if (m.emissive) m.emissive.set(spec.emissive || '#000000');
      if ('transmission' in m) m.transmission = spec.transmission || 0;
      if ('ior' in m) m.ior = spec.ior || 1.45;
      if ('specularIntensity' in m) m.specularIntensity = spec.specular || 0.5;
      m.needsUpdate = true;
    }});
  }});
}}

const loader = new GLTFLoader();
function fitCameraToObject(root) {{
  const box = new THREE.Box3().setFromObject(root);
  const size = box.getSize(new THREE.Vector3());
  const center = box.getCenter(new THREE.Vector3());
  const maxDim = Math.max(size.x, size.y, size.z, 1);
  const distance = maxDim * 1.6;
  camera.position.set(center.x + distance, center.y + distance * 0.9, center.z + distance);
  controls.target.set(center.x, Math.max(center.y, 0.6), center.z);
  controls.update();
}}

async function loadModel(url) {{
  if (modelRoot) {{
    scene.remove(modelRoot);
    modelRoot.traverse((obj) => {{
      if (obj.geometry) obj.geometry.dispose();
      if (obj.material) {{
        const mats = Array.isArray(obj.material) ? obj.material : [obj.material];
        mats.forEach((m) => m.dispose && m.dispose());
      }}
    }});
    modelRoot = null;
  }}

  setStatus('Loading model...');
  const gltf = await loader.loadAsync(url);
  modelRoot = gltf.scene;
  scene.add(modelRoot);
  fitCameraToObject(modelRoot);
  applyProfileToLoadedModel();
  setStatus('Model ready');
}}

function renderMaterialCards() {{
  const host = document.getElementById('materialCards');
  host.innerHTML = '';
  ['wall', 'door', 'ground'].forEach((group) => {{
    const spec = currentProfile.materials[group];
    const card = document.createElement('div');
    card.className = 'material-card';
    card.innerHTML = `
      <h4>${{group}}</h4>
      <div class=\"row\"><label>baseColor</label><input data-mgroup=\"${{group}}\" data-mkey=\"baseColor\" type=\"color\" value=\"${{spec.baseColor}}\" /></div>
      <div class=\"row\"><label>opacity</label><input data-mgroup=\"${{group}}\" data-mkey=\"opacity\" type=\"range\" min=\"0\" max=\"1\" step=\"0.01\" value=\"${{spec.opacity}}\" /></div>
      <div class=\"row\"><label>roughness</label><input data-mgroup=\"${{group}}\" data-mkey=\"roughness\" type=\"range\" min=\"0\" max=\"1\" step=\"0.01\" value=\"${{spec.roughness}}\" /></div>
      <div class=\"row\"><label>metallic</label><input data-mgroup=\"${{group}}\" data-mkey=\"metallic\" type=\"range\" min=\"0\" max=\"1\" step=\"0.01\" value=\"${{spec.metallic}}\" /></div>
      <div class=\"row\"><label>emissive</label><input data-mgroup=\"${{group}}\" data-mkey=\"emissive\" type=\"color\" value=\"${{spec.emissive}}\" /></div>
      <div class=\"row\"><label>specular</label><input data-mgroup=\"${{group}}\" data-mkey=\"specular\" type=\"range\" min=\"0\" max=\"1\" step=\"0.01\" value=\"${{spec.specular}}\" /></div>
      <div class=\"row\"><label>transmission</label><input data-mgroup=\"${{group}}\" data-mkey=\"transmission\" type=\"range\" min=\"0\" max=\"1\" step=\"0.01\" value=\"${{spec.transmission}}\" /></div>
      <div class=\"row\"><label>ior</label><input data-mgroup=\"${{group}}\" data-mkey=\"ior\" type=\"range\" min=\"1\" max=\"2.5\" step=\"0.01\" value=\"${{spec.ior}}\" /></div>
    `;
    host.appendChild(card);
  }});

  host.querySelectorAll('input').forEach((input) => {{
    input.addEventListener('input', () => {{
      const group = input.dataset.mgroup;
      const key = input.dataset.mkey;
      const value = input.type === 'color' ? input.value : Number(input.value);
      currentProfile.materials[group][key] = value;
      applyProfileToLoadedModel();
    }});
  }});
}}

function readGeometryFromUi() {{
  return {{
    door_mode: document.getElementById(geometryIds.door_mode).value,
    wall_height_m: Number(document.getElementById(geometryIds.wall_height_m).value),
    door_height_m: Number(document.getElementById(geometryIds.door_height_m).value),
    wall_thickness_mm: Number(document.getElementById(geometryIds.wall_thickness_mm).value),
    column_thickness_mm: Number(document.getElementById(geometryIds.column_thickness_mm).value),
    ground_thickness_m: Number(document.getElementById(geometryIds.ground_thickness_m).value),
    ground_margin_m: Number(document.getElementById(geometryIds.ground_margin_m).value),
  }};
}}

function writeGeometryToUi(geometry) {{
  document.getElementById(geometryIds.door_mode).value = geometry.door_mode;
  document.getElementById(geometryIds.wall_height_m).value = geometry.wall_height_m;
  document.getElementById(geometryIds.door_height_m).value = geometry.door_height_m;
  document.getElementById(geometryIds.wall_thickness_mm).value = geometry.wall_thickness_mm;
  document.getElementById(geometryIds.column_thickness_mm).value = geometry.column_thickness_mm;
  document.getElementById(geometryIds.ground_thickness_m).value = geometry.ground_thickness_m;
  document.getElementById(geometryIds.ground_margin_m).value = geometry.ground_margin_m;
}}

async function apiPost(path, payload) {{
  const response = await fetch(path, {{
    method: 'POST',
    headers: {{ 'Content-Type': 'application/json' }},
    body: JSON.stringify(payload),
  }});
  const data = await response.json();
  if (!response.ok || !data.ok) throw new Error(data.error || `HTTP ${{response.status}}`);
  return data;
}}

async function rebuild(applyMaterialToExport) {{
  if (!state.apiEnabled || loading) return;
  loading = true;
  document.querySelectorAll('button').forEach((b) => (b.disabled = true));
  setStatus('Rebuilding geometry...');
  try {{
    const payload = {{
      geometry: readGeometryFromUi(),
      material_profile: currentProfile,
      apply_material_to_export: Boolean(applyMaterialToExport),
    }};
    const data = await apiPost('/api/rebuild', payload);
    pathsEl.textContent = `GLB: ${{data.glb_url}} | JSON: ${{data.json_url}}`;
    await loadModel(data.glb_url);
    setStatus(`Rebuild done (minY=${{data.final_bbox.min_y.toFixed(4)}})`);
  }} catch (err) {{
    setStatus(`Rebuild failed: ${{err.message}}`);
  }} finally {{
    document.querySelectorAll('button').forEach((b) => (b.disabled = false));
    loading = false;
  }}
}}

function downloadJson(filename, data) {{
  const blob = new Blob([JSON.stringify(data, null, 2)], {{ type: 'application/json' }});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}}

function copyCliArgs() {{
  const g = readGeometryFromUi();
  const text = `--door-mode ${{g.door_mode}} --wall-height-m ${{g.wall_height_m}} --door-height-m ${{g.door_height_m}} --wall-thickness-mm ${{g.wall_thickness_mm}} --column-thickness-mm ${{g.column_thickness_mm}} --ground-thickness-m ${{g.ground_thickness_m}} --ground-margin-m ${{g.ground_margin_m}}`;
  navigator.clipboard.writeText(text).then(() => setStatus('CLI args copied')).catch(() => setStatus('Copy failed'));
}}

presetEl.addEventListener('change', () => {{
  const key = presetEl.value;
  currentProfile = structuredClone(presets[key]);
  currentProfile.preset = key;
  renderMaterialCards();
  applyProfileToLoadedModel();
  setStatus(`Preset applied: ${{key}}`);
}});

document.getElementById('applyMaterials').addEventListener('click', async () => {{
  if (state.apiEnabled) {{
    try {{
      const data = await apiPost('/api/material/preview', {{ profile: currentProfile }});
      currentProfile = data.profile;
      renderMaterialCards();
    }} catch (err) {{
      setStatus(`Material preview normalize failed: ${{err.message}}`);
    }}
  }}
  applyProfileToLoadedModel();
  setStatus('Materials applied in preview');
}});

document.getElementById('rebuild').addEventListener('click', () => rebuild(false));
document.getElementById('rebuildWithProfile').addEventListener('click', () => rebuild(true));
document.getElementById('copyCmd').addEventListener('click', copyCliArgs);

document.getElementById('exportProfile').addEventListener('click', () => {{
  downloadJson('material_profile.json', currentProfile);
  setStatus('Profile exported');
}});

document.getElementById('importProfileBtn').addEventListener('click', () => {{
  document.getElementById('importProfileFile').click();
}});

document.getElementById('importProfileFile').addEventListener('change', async (evt) => {{
  const file = evt.target.files?.[0];
  if (!file) return;
  const text = await file.text();
  try {{
    const parsed = JSON.parse(text);
    if (state.apiEnabled) {{
      const data = await apiPost('/api/profile/import', {{ profile: parsed }});
      currentProfile = data.profile;
    }} else {{
      currentProfile = parsed;
    }}
    renderMaterialCards();
    applyProfileToLoadedModel();
    setStatus('Profile imported');
  }} catch (err) {{
    setStatus(`Invalid profile: ${{err.message}}`);
  }}
}});

writeGeometryToUi(state.geometry);
presetEl.value = currentProfile.preset || Object.keys(presets)[0];
renderMaterialCards();

const initialModelUrl = state.modelUrl + (state.modelUrl.includes('?') ? '&' : '?') + 'ts=' + Date.now();
loadModel(initialModelUrl).catch((err) => setStatus(`Model load failed: ${{err.message}}`));
pathsEl.textContent = `GLB: ${{state.modelUrl}}${{state.jsonUrl ? ` | JSON: ${{state.jsonUrl}}` : ''}}`;

function animate() {{
  requestAnimationFrame(animate);
  controls.update();
  renderer.render(scene, camera);
}}
animate();
</script>
</body>
</html>
"""


def normalize_geometry(payload: dict[str, Any] | None, base: dict[str, Any] | None = None) -> dict[str, Any]:
    src = dict(base or DEFAULT_GEOMETRY)
    if isinstance(payload, dict):
        src.update(payload)

    door_mode = str(src.get("door_mode", DEFAULT_GEOMETRY["door_mode"]))
    if door_mode not in {"none", "block"}:
        door_mode = "none"

    center_mode = str(src.get("center_mode", DEFAULT_GEOMETRY["center_mode"]))
    if center_mode not in {"grounded-xz", "none", "bbox-center"}:
        center_mode = "grounded-xz"

    return {
        "door_mode": door_mode,
        "center_mode": center_mode,
        "unit_scale": clamp(float(src.get("unit_scale", DEFAULT_GEOMETRY["unit_scale"])), 1e-6, 1000.0),
        "wall_height_m": clamp(float(src.get("wall_height_m", DEFAULT_GEOMETRY["wall_height_m"])), 0.1, 300.0),
        "door_height_m": clamp(float(src.get("door_height_m", DEFAULT_GEOMETRY["door_height_m"])), 0.1, 300.0),
        "wall_thickness_mm": clamp(float(src.get("wall_thickness_mm", DEFAULT_GEOMETRY["wall_thickness_mm"])), 1.0, 10000.0),
        "column_thickness_mm": clamp(float(src.get("column_thickness_mm", DEFAULT_GEOMETRY["column_thickness_mm"])), 1.0, 10000.0),
        "ground_thickness_m": clamp(float(src.get("ground_thickness_m", DEFAULT_GEOMETRY["ground_thickness_m"])), 0.001, 10.0),
        "ground_margin_m": clamp(float(src.get("ground_margin_m", DEFAULT_GEOMETRY["ground_margin_m"])), 0.0, 1000.0),
        "window_pad_mm": clamp(float(src.get("window_pad_mm", DEFAULT_GEOMETRY["window_pad_mm"])), 0.0, 2000.0),
    }


class RebuildState:
    def __init__(
        self,
        input_dxf: Path,
        out_dir: Path,
        geometry: dict[str, Any],
        profile: dict[str, Any],
    ) -> None:
        self.input_dxf = input_dxf
        self.out_dir = out_dir
        self.geometry = geometry
        self.profile = profile
        self.lock = threading.Lock()
        self.last_metadata: dict[str, Any] = {}
        self.last_error = ""

    @property
    def glb_path(self) -> Path:
        return self.out_dir / "scene_latest.glb"

    @property
    def json_path(self) -> Path:
        return self.out_dir / "scene_latest.json"

    @property
    def html_path(self) -> Path:
        return self.out_dir / "index.html"

    @property
    def profile_path(self) -> Path:
        return self.out_dir / "material_profile_latest.json"


class ApiHandler(SimpleHTTPRequestHandler):
    state: RebuildState | None = None

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(length) if length > 0 else b"{}"
        if not body:
            return {}
        data = json.loads(body.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Request JSON must be an object")
        return data

    def _json(self, payload: dict[str, Any], status: int = 200) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self) -> None:  # noqa: N802
        state = self.state
        if state is None:
            self._json({"ok": False, "error": "Server state unavailable"}, status=500)
            return

        try:
            payload = self._read_json()
            if self.path == "/api/material/preview":
                profile = normalize_profile(payload.get("profile") if isinstance(payload, dict) else None)
                self._json({"ok": True, "profile": profile})
                return

            if self.path == "/api/profile/import":
                profile = normalize_profile(payload.get("profile") if isinstance(payload, dict) else None)
                self._json({"ok": True, "profile": profile})
                return

            if self.path == "/api/profile/export":
                profile = normalize_profile(payload.get("profile") if isinstance(payload, dict) else None)
                self._json({"ok": True, "profile": profile})
                return

            if self.path == "/api/rebuild":
                geometry_payload = payload.get("geometry") if isinstance(payload, dict) else None
                profile_payload = payload.get("material_profile") if isinstance(payload, dict) else None
                apply_material = bool(payload.get("apply_material_to_export", False))

                geometry = normalize_geometry(geometry_payload, state.geometry)
                profile = normalize_profile(profile_payload)

                with state.lock:
                    export_profile = profile if apply_material else state.profile
                    state.profile = profile
                    state.geometry = geometry
                    state.profile_path.write_text(json.dumps(profile, indent=2), encoding="utf-8")

                    result = build_scene(
                        input_dxf=state.input_dxf,
                        output_glb=state.glb_path,
                        emit_json=state.json_path,
                        preview_html=None,
                        geometry=geometry,
                        profile=export_profile,
                    )
                    state.last_metadata = result.metadata

                    render_state = {
                        "geometry": geometry,
                        "profile": profile,
                        "modelUrl": "scene_latest.glb",
                        "jsonUrl": "scene_latest.json",
                        "apiEnabled": True,
                    }
                    state.html_path.write_text(render_preview_html(render_state), encoding="utf-8")

                ts = int(time.time() * 1000)
                self._json(
                    {
                        "ok": True,
                        "glb_url": f"/scene_latest.glb?ts={ts}",
                        "json_url": f"/scene_latest.json?ts={ts}",
                        "final_bbox": result.metadata.get("final_bbox", {}),
                        "counts": result.metadata.get("counts", {}),
                    }
                )
                return

            self._json({"ok": False, "error": f"Unsupported endpoint: {self.path}"}, status=404)
        except Exception as exc:  # pylint: disable=broad-except
            self._json({"ok": False, "error": str(exc)}, status=400)


def start_server(
    input_dxf: Path,
    out_dir: Path,
    host: str,
    port: int,
    geometry: dict[str, Any],
    profile: dict[str, Any],
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    state = RebuildState(input_dxf=input_dxf, out_dir=out_dir, geometry=geometry, profile=profile)

    result = build_scene(
        input_dxf=input_dxf,
        output_glb=state.glb_path,
        emit_json=state.json_path,
        preview_html=None,
        geometry=geometry,
        profile=profile,
    )
    state.last_metadata = result.metadata
    state.profile_path.write_text(json.dumps(profile, indent=2), encoding="utf-8")

    render_state = {
        "geometry": geometry,
        "profile": profile,
        "modelUrl": "scene_latest.glb",
        "jsonUrl": "scene_latest.json",
        "apiEnabled": True,
    }
    state.html_path.write_text(render_preview_html(render_state), encoding="utf-8")

    handler_class = type("DxfGlbApiHandler", (ApiHandler,), {})
    handler_class.state = state

    def handler_factory(*args: Any, **kwargs: Any) -> ApiHandler:
        return handler_class(*args, directory=str(out_dir), **kwargs)

    server = ThreadingHTTPServer((host, port), handler_factory)
    print(f"[OK] Workbench running at http://{host}:{port}")
    print(f"[OK] Input DXF: {input_dxf.resolve()}")
    print(f"[OK] Output dir: {out_dir.resolve()}")
    print("[OK] Endpoints: /api/rebuild, /api/material/preview, /api/profile/export, /api/profile/import")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[INFO] Stopping server...")
    finally:
        server.server_close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="mode", required=True)

    def add_geometry_args(target: argparse.ArgumentParser) -> None:
        target.add_argument("--door-mode", choices=["none", "block"], default=DEFAULT_GEOMETRY["door_mode"])
        target.add_argument(
            "--center-mode",
            choices=["grounded-xz", "none", "bbox-center"],
            default=DEFAULT_GEOMETRY["center_mode"],
        )
        target.add_argument("--unit-scale", type=float, default=DEFAULT_GEOMETRY["unit_scale"])
        target.add_argument("--wall-height-m", type=float, default=DEFAULT_GEOMETRY["wall_height_m"])
        target.add_argument("--door-height-m", type=float, default=DEFAULT_GEOMETRY["door_height_m"])
        target.add_argument("--wall-thickness-mm", type=float, default=DEFAULT_GEOMETRY["wall_thickness_mm"])
        target.add_argument("--column-thickness-mm", type=float, default=DEFAULT_GEOMETRY["column_thickness_mm"])
        target.add_argument("--ground-thickness-m", type=float, default=DEFAULT_GEOMETRY["ground_thickness_m"])
        target.add_argument("--ground-margin-m", type=float, default=DEFAULT_GEOMETRY["ground_margin_m"])
        target.add_argument("--window-pad-mm", type=float, default=DEFAULT_GEOMETRY["window_pad_mm"])

    build_cmd = subparsers.add_parser("build", help="Build a GLB scene from DXF")
    build_cmd.add_argument("--input", required=True, type=Path, help="Input DXF file")
    build_cmd.add_argument("--output", required=True, type=Path, help="Output GLB path")
    build_cmd.add_argument("--emit-json", type=Path, help="Optional metadata JSON output")
    build_cmd.add_argument("--preview-html", type=Path, help="Optional preview HTML output")
    build_cmd.add_argument("--material-profile", type=Path, help="Material profile JSON path")
    build_cmd.add_argument("--preset", choices=sorted(DEFAULT_STYLE_PRESETS.keys()), default=DEFAULT_PRESET)
    add_geometry_args(build_cmd)

    serve_cmd = subparsers.add_parser("serve", help="Start local workbench with rebuild APIs")
    serve_cmd.add_argument("--input", required=True, type=Path, help="Input DXF file")
    serve_cmd.add_argument("--out-dir", required=True, type=Path, help="Output workspace directory")
    serve_cmd.add_argument("--host", default="127.0.0.1")
    serve_cmd.add_argument("--port", type=int, default=8124)
    serve_cmd.add_argument("--default-profile", type=Path, help="Default profile for workbench")
    serve_cmd.add_argument("--preset", choices=sorted(DEFAULT_STYLE_PRESETS.keys()), default=DEFAULT_PRESET)
    add_geometry_args(serve_cmd)

    return parser


def args_to_geometry(args: argparse.Namespace) -> dict[str, Any]:
    raw = {
        "door_mode": args.door_mode,
        "center_mode": args.center_mode,
        "unit_scale": args.unit_scale,
        "wall_height_m": args.wall_height_m,
        "door_height_m": args.door_height_m,
        "wall_thickness_mm": args.wall_thickness_mm,
        "column_thickness_mm": args.column_thickness_mm,
        "ground_thickness_m": args.ground_thickness_m,
        "ground_margin_m": args.ground_margin_m,
        "window_pad_mm": args.window_pad_mm,
    }
    return normalize_geometry(raw)


def run_build(args: argparse.Namespace) -> int:
    if not args.input.exists():
        print(f"[ERROR] Input file does not exist: {args.input}")
        return 1

    ensure_defaults_material_profile()

    profile_path = args.material_profile
    if profile_path is None:
        profile_path = get_default_profile_path()

    profile = load_profile_from_file(profile_path)
    if args.preset:
        profile = normalize_profile(profile, preset_name=args.preset)

    geometry = args_to_geometry(args)

    try:
        result = build_scene(
            input_dxf=args.input,
            output_glb=args.output,
            emit_json=args.emit_json,
            preview_html=args.preview_html,
            geometry=geometry,
            profile=profile,
        )
    except Exception as exc:  # pylint: disable=broad-except
        print(f"[ERROR] Build failed: {exc}")
        return 1

    print("[OK] Build complete")
    print(f"[OK] GLB: {result.output_glb.resolve()}")
    if result.output_json:
        print(f"[OK] JSON: {result.output_json.resolve()}")
    if result.preview_html:
        print(f"[OK] Preview: {result.preview_html.resolve()}")
    final_bbox = result.metadata.get("final_bbox", {})
    print(f"[OK] final_bbox.min_y = {final_bbox.get('min_y')}")
    counts = result.metadata.get("counts", {})
    print(f"[OK] counts = {counts}")
    return 0


def run_serve(args: argparse.Namespace) -> int:
    if not args.input.exists():
        print(f"[ERROR] Input file does not exist: {args.input}")
        return 1

    ensure_defaults_material_profile()

    profile_path = args.default_profile if args.default_profile else get_default_profile_path()
    profile = load_profile_from_file(profile_path)
    if args.preset:
        profile = normalize_profile(profile, preset_name=args.preset)

    geometry = args_to_geometry(args)

    start_server(
        input_dxf=args.input,
        out_dir=args.out_dir,
        host=args.host,
        port=args.port,
        geometry=geometry,
        profile=profile,
    )
    return 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.mode == "build":
        return run_build(args)
    if args.mode == "serve":
        return run_serve(args)

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
