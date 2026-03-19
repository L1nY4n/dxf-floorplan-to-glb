# Workbench API

## POST /api/rebuild

### Request

```json
{
  "geometry": {
    "door_mode": "none",
    "wall_height_m": 3.8,
    "door_height_m": 2.4,
    "wall_thickness_mm": 120,
    "column_thickness_mm": 180,
    "ground_thickness_m": 0.08,
    "ground_margin_m": 0.6
  },
  "material_profile": {
    "preset": "crystal-purple",
    "materials": {
      "wall": {"baseColor": "#9d8bff", "opacity": 0.34}
    }
  },
  "apply_material_to_export": false
}
```

### Response

```json
{
  "ok": true,
  "glb_url": "/scene_latest.glb?ts=...",
  "json_url": "/scene_latest.json?ts=...",
  "final_bbox": {"min_y": 0.0},
  "counts": {"filtered_door_count": 12}
}
```

## POST /api/material/preview

Normalize a material profile for front-end usage.

## POST /api/profile/import and /api/profile/export

Round-trip profile JSON through server-side normalization.

## Notes

- In workbench mode, material changes can update preview instantly.
- Geometry changes require `/api/rebuild` to regenerate GLB.
- Viewer controls (display/lighting/camera/animation/performance) are client-side and do not change API payload shape.
- Environment presets include neutral `RoomEnvironment` and remote EXR maps with automatic fallback.
