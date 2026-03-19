---
name: dxf-floorplan-to-glb
description: Convert architectural DXF floorplans into grounded GLB scenes with configurable wall/door geometry and interactive material tuning. Use when the user asks for DXF to 3D conversion, wants door filtering (for example remove door boxes), needs wall height/thickness controls, or wants a local web workbench with rebuild APIs and material profile import/export.
---

# DXF Floorplan To GLB

## Overview

Use `scripts/dxf_to_glb.py` to convert DXF floorplans into structured GLB output and generate a parameter workbench preview.
Default workflow is `serve` mode first: start the local workbench, tune geometry/material interactively, then deliver the exported GLB/JSON from the workbench output directory.
Default geometry/material behavior targets stable web delivery: grounded model (`minY=0`), XZ centering, and crystal-purple transparent wall style.

## Quick Start

Run local interactive workbench with APIs (default):

```bash
python3 scripts/dxf_to_glb.py serve \
  --input /path/to/floorplan.dxf \
  --out-dir /tmp/dxf_glb_workbench \
  --host 127.0.0.1 \
  --port 8124
```

Then open `http://127.0.0.1:8124`, tune parameters in the UI, and use the latest exported files in `--out-dir` (`scene_latest.glb`, `scene_latest.json`, `material_profile_latest.json`).

One-shot build (optional, non-interactive/batch use):

```bash
python3 scripts/dxf_to_glb.py build \
  --input /path/to/floorplan.dxf \
  --output /tmp/floorplan.glb \
  --emit-json /tmp/floorplan.json \
  --preview-html /tmp/floorplan_preview.html
```

## Geometry Controls

Tune geometry with CLI flags or the web panel:

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

- Preset switching (`crystal-purple`, `frosted-purple`, `mirror-purple`)
- Per-group editing for `wall`, `door`, `ground`
- Real-time preview updates for color/opacity/roughness/metallic/emissive/specular/transmission/ior
- Profile export/import as JSON
- Optional rebuild with profile writeback (`Rebuild + Write Profile`)

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

## Resources

- Primary script: [scripts/dxf_to_glb.py](scripts/dxf_to_glb.py)
- Layer and transform rules: [references/layer-mapping.md](references/layer-mapping.md)
- API and UI behavior: [references/workbench-api.md](references/workbench-api.md)
- Default material profile: [assets/material_profile_crystal_purple.json](assets/material_profile_crystal_purple.json)
