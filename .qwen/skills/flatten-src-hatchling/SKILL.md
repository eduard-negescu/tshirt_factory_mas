---
name: flatten-src-hatchling
description: Flatten a nested src/package_name/ layout into a flat src/ layout with multiple top-level packages, updating hatchling build config, entry point, sys.path bootstrap, and documentation.
source: auto-skill
extracted_at: '2026-06-03T07:26:46.282Z'
---

# Flatten `src/pkg/` into flat `src/` with Hatchling

Use this when a project has `src/<package_name>/` with multiple sub-packages and you want to flatten everything directly into `src/` (no intermediate package directory).

## Step 1 — Move files

```bash
find src/<old_pkg> -mindepth 1 -maxdepth 1 | while read item; do mv "$item" src/; done
rmdir src/<old_pkg>
```

This moves all subdirectories (agents, config, models, etc.) and top-level modules (main.py, bus.py, _entry.py) up one level.

## Step 2 — Update `pyproject.toml`

### Entry point

Change from `pkg._entry:main` to just `_entry:main`:

```toml
[project.scripts]
dev = "_entry:main"
```

### Hatchling wheel target

List **every sub-package** (directories with `__init__.py`) individually, and force-include top-level `.py` modules that aren't inside any package:

```toml
[tool.hatch.build.targets.wheel]
packages = [
    "src/agents",
    "src/config",
    "src/equipment",
    "src/llm",
    "src/models",
]

[tool.hatch.build.targets.wheel.force-include]
"src/main.py" = "main.py"
"src/bus.py" = "bus.py"
"src/_entry.py" = "_entry.py"
```

**Why:** Hatchling's `packages` only includes directories with `__init__.py`. Lone `.py` files at `src/` level need `force-include` to land in the built wheel.

## Step 3 — Verify `_entry.py` bootstrap still works

The typical `_entry.py` does:

```python
_src = Path(__file__).resolve().parent
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))
from main import main
```

After the move, `Path(__file__).resolve().parent` resolves to `src/` instead of `src/<old_pkg>/`. Since `src/` now directly contains `main.py`, `bus.py`, and all sub-packages, this still works.

After `pip install`, `_entry.py` lands in `site-packages/` root alongside all other modules — the `sys.path` insert becomes a no-op (site-packages is already on the path), and imports resolve naturally.

## Step 4 — Update documentation

- Replace `src/<old_pkg>/` paths with `src/` in the project structure tree.
- Update any `python -m src.old_pkg.main` commands to `python -m src.main`.
- Update dev conventions section that references the old package path.

## Step 5 — Test

```bash
uv run python -c "import sys; sys.path.insert(0, 'src'); from main import main; print('OK')"
uv run dev   # full run
```

## Caveats

- **Generic package names**: `agents`, `config`, `models` are now top-level packages in `site-packages`, which could collide with other installed packages. Only use this pattern for standalone applications, not libraries.
- **Implicit relative imports**: The codebase likely uses imports like `from agents.foo import X` (resolved via `sys.path`). These continue working as long as `_entry.py` adds `src/` to `sys.path` at runtime.
