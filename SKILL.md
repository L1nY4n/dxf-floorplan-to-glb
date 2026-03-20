---
name: dxf-floorplan-to-glb
description: Convert architectural DXF floorplans into grounded GLB scenes with configurable wall/door geometry and interactive material tuning. Use when the user asks for DXF to 3D conversion, wants door filtering (for example remove door boxes), needs wall height/thickness controls, or wants a local web workbench with rebuild APIs and material profile import/export.
---

# DXF Floorplan To GLB

## Overview

Use `scripts/dxf_to_glb.py` to convert DXF floorplans into structured GLB output and generate a parameter workbench preview.
This skill must present an explicit mode choice to the user before execution: `serve` (preview workbench) or `build` (one-shot export).
If the user has not clearly stated a mode, ask a short confirmation question first instead of assuming.
Default geometry/material behavior targets stable web delivery: grounded model (`minY=0`), XZ centering, and crystal-purple transparent wall style.

## Quick Start

Mode A: interactive preview workbench (`serve`)

```bash
python3 scripts/dxf_to_glb.py serve \
  --input /path/to/floorplan.dxf \
  --out-dir /tmp/dxf_glb_workbench \
  --host 127.0.0.1 \
  --port 8124
```

Then open `http://127.0.0.1:8124`, tune parameters in the UI, and use the latest exported files in `--out-dir` (`scene_latest.glb`, `scene_latest.json`, `material_profile_latest.json`).

Mode B: one-shot export (`build`)

```bash
python3 scripts/dxf_to_glb.py build \
  --input /path/to/floorplan.dxf \
  --output /tmp/floorplan.glb \
  --emit-json /tmp/floorplan.json \
  --preview-html /tmp/floorplan_preview.html
```

Mode C: CLI built-in mode picker (`interactive`)

```bash
python3 scripts/dxf_to_glb.py interactive
```

The CLI shows an internal mode selector (`1=serve`, `2=build`) and guides required paths.

## Interaction Contract

- Required: explicitly confirm mode with the user before running commands when the request is ambiguous.
- Suggested confirmation wording: "Do you want preview mode (`serve`) or direct export (`build`)?"
- CLI option: use built-in mode selector via `python3 scripts/dxf_to_glb.py interactive`.
- If user selects `serve`: start local workbench, return URL, and wait for user to finish tuning before final delivery.
- If user selects `build`: run one-shot export and return output paths directly.
- After final model delivery from `serve`: stop the Python preview process and clean runtime leftovers (`server.pid`, stale temp runtime files/logs) while keeping final deliverables.

## Geometry Controls

Tune geometry with CLI flags or the `dat.GUI` workbench panel:

- `--door-mode none|block` controls door extrusion (default `none`)
- `--wall-height-m` wall extrusion height
- `--door-height-m` door extrusion height
- `--wall-thickness-mm` wall segment thickness for LINE/LWPOLYLINE extraction
- `--column-thickness-mm` column segment thickness
- `--ground-thickness-m` floor slab thickness
- `--ground-margin-m` border around floor slab
- `--center-mode grounded-xz|none|bbox-center` (default `grounded-xz`)
- `--unit-scale` CAD to world scale (default `0.001` for mm -> m)

## Material Workflow

Material profile defaults to `assets/material_profile_crystal_purple.json`.
The workbench supports:

- Preset switching (`crystal-purple`, `frosted-purple`, `mirror-purple`, `warm-sand`, `ocean-cyan`, `emerald-glass`, `charcoal-tech`)
- Per-group editing for `wall`, `door`, `ground`
- Per-group finish presets (`custom` + architectural surface styles like paint/concrete/metal/wood/glass)
- Real-time preview updates for color/opacity/roughness/metallic/emissive/specular/transmission/ior/clearcoat/sheen
- Profile export/import as JSON
- Optional rebuild with profile writeback (`Rebuild + Write Profile`)

## Viewer Core Controls

The preview layer is optimized with a `three-gltf-viewer`-style capability set while keeping DXF workflow controls unchanged:

- Display: `background`, `autoRotate`, `wireframe`, `grid`, `axes`
- Lighting: `environment`, `toneMapping`, `exposure`, ambient/direct light intensity and color
- Cameras: default camera plus model camera switching (when cameras exist)
- Animation: playback speed and per-clip toggles (when clips exist)
- Performance: real-time stats panel toggle
- Environment pipeline: `RoomEnvironment` neutral mode plus remote EXR environment presets with fallback to neutral when loading fails

## API Contract (serve mode)

- `POST /api/rebuild`
  - Input: `geometry`, optional `material_profile`, `apply_material_to_export`
  - Output: updated `glb_url`, `json_url`, `final_bbox`, `counts`
- `POST /api/material/preview`
  - Input: `profile`
  - Output: normalized profile
- `POST /api/profile/export`
  - Input: `profile`
  - Output: normalized profile
- `POST /api/profile/import`
  - Input: `profile`
  - Output: normalized profile

## Notes

- This skill prioritizes structural clarity and tunability, not boolean wall openings.
- Doors can be hidden by default to avoid oversized door box artifacts from CAD block transforms.
- For final delivery QA, verify `final_bbox.min_y == 0` in emitted JSON.
- Viewer design and control taxonomy are inspired by [`donmccurdy/three-gltf-viewer`](https://github.com/donmccurdy/three-gltf-viewer) (MIT).

## Resources

- Primary script: [scripts/dxf_to_glb.py](scripts/dxf_to_glb.py)
- Layer and transform rules: [references/layer-mapping.md](references/layer-mapping.md)
- API and UI behavior: [references/workbench-api.md](references/workbench-api.md)
- Default material profile: [assets/material_profile_crystal_purple.json](assets/material_profile_crystal_purple.json)
