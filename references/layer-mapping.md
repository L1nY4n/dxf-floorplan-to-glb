# Layer Mapping

## Extraction Rules

- `WALL` layer
  - `LINE` and `LWPOLYLINE` entities are expanded into wall strip footprints.
- `COLUMN` layer
  - `LINE` entities are expanded into thicker wall strips.
- `WINDOW` layer
  - `INSERT` blocks are transformed by local block bbox + insert transform.
  - Block names containing `dor` are treated as doors.
  - Other blocks are treated as wall-like window strips.

## Door Filtering

- `door-mode=none` drops door inserts before extrusion.
- `door-mode=block` extrudes doors with `door-height-m`.

## Coordinate Policy

- CAD XY plane is treated as horizontal floor plane.
- Mesh is authored in z-up then converted to y-up for GLB.
- `center-mode=grounded-xz` shifts output so:
  - `minY=0` (floor grounded)
  - `X/Z` centered around origin

This avoids web/grid viewers showing the grid slicing through wall mid-height.
