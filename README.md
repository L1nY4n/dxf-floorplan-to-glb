# DXF Floorplan To GLB

Convert architectural DXF floorplans into grounded GLB scenes with configurable wall/door geometry and an interactive local workbench.

Default workflow is `serve` mode first: launch a local preview workbench, tune geometry/material in the browser, then export final GLB/JSON.

## Features

- DXF to GLB conversion for floorplan-like drawings
- Grounded output (`final_bbox.min_y == 0`) with XZ centering by default
- Door filtering mode (`none` or `block`)
- Tunable wall/door/ground geometry
- Interactive workbench UI with live material tuning
- Viewer core controls: display, lighting, camera, animation, performance stats
- Environment pipeline with neutral `RoomEnvironment` and remote EXR presets
- API endpoints for rebuild/profile import/export

## Requirements

- Python 3.10+
- `pip install -r requirements.txt`

## Quick Start

### 1) Launch the local workbench (recommended)

```bash
python3 scripts/dxf_to_glb.py serve \
  --input /path/to/floorplan.dxf \
  --out-dir /tmp/dxf_glb_workbench \
  --host 127.0.0.1 \
  --port 8124
```

Open `http://127.0.0.1:8124`.

After tuning, use files in `--out-dir`:

- `scene_latest.glb`
- `scene_latest.json`
- `material_profile_latest.json`

### 2) One-shot build (batch use)

```bash
python3 scripts/dxf_to_glb.py build \
  --input /path/to/floorplan.dxf \
  --output /tmp/floorplan.glb \
  --emit-json /tmp/floorplan.json \
  --preview-html /tmp/floorplan_preview.html
```

## Common Geometry Controls

- `--door-mode none|block`
- `--wall-height-m`
- `--door-height-m`
- `--wall-thickness-mm`
- `--column-thickness-mm`
- `--ground-thickness-m`
- `--ground-margin-m`
- `--center-mode grounded-xz|none|bbox-center`
- `--unit-scale` (default `0.001`, mm to m)

## Workbench API

- `POST /api/rebuild`
- `POST /api/material/preview`
- `POST /api/profile/export`
- `POST /api/profile/import`

See [references/workbench-api.md](references/workbench-api.md) for request/response examples.

## Repository Layout

- `scripts/dxf_to_glb.py`: main converter and local workbench server
- `assets/material_profile_crystal_purple.json`: default material profile
- `references/layer-mapping.md`: DXF layer extraction rules
- `references/workbench-api.md`: API behavior for serve mode
- `SKILL.md`: Codex skill-oriented usage notes

## Notes

- This project prioritizes structural clarity and tunability over boolean wall opening operations.
- For final delivery QA, verify `final_bbox.min_y == 0` in emitted JSON.
- Viewer UX/control taxonomy is inspired by [donmccurdy/three-gltf-viewer](https://github.com/donmccurdy/three-gltf-viewer) (MIT).

## License

MIT
