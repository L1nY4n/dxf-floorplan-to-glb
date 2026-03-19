# Contributing

## Development

1. Create a virtual environment.
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Run CLI help checks:

```bash
python3 scripts/dxf_to_glb.py --help
python3 scripts/dxf_to_glb.py build --help
python3 scripts/dxf_to_glb.py serve --help
```

## Pull Requests

- Keep changes focused and explain behavior changes in PR description.
- Include before/after notes for geometry/material defaults when relevant.
- If API payload/response changes, update `references/workbench-api.md`.
