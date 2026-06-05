---
name: hatchling-force-include-stale
description: Diagnose and fix runtime behavior that doesn't match source when hatchling's force-include copies stale source files into an editable install's site-packages.
source: auto-skill
extracted_at: '2026-06-04T20:16:38.177Z'
---

# Hatchling `force-include` Stale Editable Installs

Use this when Python runs old code despite `src/` containing the fix — and the project uses hatchling with `force-include` to copy top-level `.py` modules into the wheel.

## Symptom

- You modified a `.py` file under `src/` (e.g., `src/bus.py`), saved it, and restarted the process, but runtime still shows the **old** behavior.
- The source on disk (`src/bus.py`) is correct, but the version imported by Python is stale.
- An `AttributeError` like `'NoneType' object has no attribute 'get'` on a return value that should be non-None — the old code returned `None` implicitly, the new code returns a dict.
- `git diff` on the `.py` file shows the expected change — source is fine.
- Purging `__pycache__/` directories doesn't help.

## Root cause

Hatchling's `force-include` copies standalone `.py` files from `src/` into the built wheel:

```toml
[tool.hatch.build.targets.wheel.force-include]
"src/bus.py" = "bus.py"
```

When installed in editable mode, these copies land directly in `site-packages/`:

```
.venv/lib/python3.12/site-packages/bus.py   ← STALE COPY
src/bus.py                                   ← CURRENT SOURCE
```

Python's import order searches `site-packages/` **before** paths added by `.pth` files. So `import bus` resolves to the stale `site-packages/bus.py`, not `src/bus.py`.

Editable installs for **packages** (directories with `__init__.py`) are handled via `.pth` redirects that point back to `src/`. But `force-include` files are not packages — they're standalone modules — so hatchling copies them into site-packages and they go stale when source changes.

## Diagnosis

### Step 1 — Check which file Python is actually importing

```bash
python -c "import bus; print(bus.__file__)"
```

If this shows `.venv/.../site-packages/bus.py` instead of `src/bus.py`, the installed copy is stale.

### Step 2 — Confirm the installed copy differs from source

```bash
diff src/bus.py .venv/lib/python3.12/site-packages/bus.py
```

A diff confirms staleness. Also check `inspect.getsource()` to see the runtime version:

```bash
python -c "from bus import MessageBus; import inspect; print(inspect.getsource(MessageBus.dispatch))"
```

### Step 3 — Check which files are force-included

```bash
grep -A5 'force-include' pyproject.toml
```

Any file listed there is copied at install time and can go stale.

## Fix

### Quick fix — copy manually (one-off)

```bash
cp src/bus.py .venv/lib/python3.12/site-packages/bus.py
```

### Proper fix — re-sync the editable install

```bash
uv sync --reinstall
```

This rebuilds the wheel from current sources and reinstalls all packages. The `--reinstall` flag is required — plain `uv sync` only updates dependencies, not already-installed packages whose version hasn't changed.

### Clean stale bytecode (belt-and-suspenders)

```bash
find src -name '*.pyc' -delete
find .venv -path '*/__pycache__/*.pyc' -delete
```

## After the fix — restart everything

`uv sync --reinstall` updates the on-disk copies, but any **already-running Python process** (Streamlit server, uvicorn, background workers) still has the old module loaded in memory. You must:

1. Kill all running processes that import the stale module
2. Start them fresh so they pick up the reinstalled copy

For Streamlit specifically, use `pkill -f "streamlit run"` or stop the background shell.

## Defense-in-depth: guard call sites against None returns

Even after fixing the root cause, add defensive `None` guards at every call site that depends on the return value. If the stale-copy problem somehow recurs, the simulation degrades gracefully instead of crashing with `AttributeError`.

```python
responses = bus.dispatch()
if responses is None:
    responses = {}
sched_resp = responses.get("scheduler", [None])[-1]
```

Use `replace_all: true` in the `edit` tool to apply the identical guard to multiple call sites in one operation.

## Prevention

When you modify **any** file listed under `[tool.hatch.build.targets.wheel.force-include]`, run `uv sync --reinstall` before testing. These files are:

- `src/main.py` → `main.py`
- `src/bus.py` → `bus.py`
- `src/_entry.py` → `_entry.py`
- `src/logging_config.py` → `logging_config.py`

(Check your own `pyproject.toml` for the current list.)

## Related skills

- `stale-bytecode-git` — for when `.pyc` files tracked in git are the cause of stale behavior (different root cause, similar symptoms).
- `flatten-src-hatchling` — explains why `force-include` exists in this project layout.
