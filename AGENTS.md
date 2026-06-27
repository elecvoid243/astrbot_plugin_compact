# AGENTS.md

Guidance for AI programming agents working in the `astrbot_plugin_compact` repository.

## Project

- **Absolute path**: `F:\github\astrbot_plugin_compact` (Windows). If working under WSL/Git Bash, the equivalent POSIX path is `/f/github/astrbot_plugin_compact`.
- **Name**: AstrBot `/compact` Plugin
- **Purpose**: Slash command that manually triggers LLM-summary context compaction for AstrBot sessions, reusing the built-in `LLMSummaryCompressor`.
- **Language**: Python 3.12+
- **Linter / Formatter**: Ruff
- **Test framework**: pytest

This is **not** a packaged library and is **not** built with msbuild (or any other build tool). It is loaded directly by AstrBot by being placed under `<AstrBot>/data/plugins/`.

---

## Build / Lint / Test Commands

There is no compile or build step. The "build" is simply installing the plugin into AstrBot's `data/plugins/` directory and (re)starting AstrBot.

### Lint and format

```bash
ruff check .
ruff format --check .
```

Auto-fix and reformat before committing:

```bash
ruff check --fix .
ruff format .
```

### Test — full suite

```bash
pytest
```

### Test — single file

```bash
pytest tests/test_compressor.py
```

### Test — single test function

```bash
pytest tests/test_compressor.py::test_function_name
```

### Test — single test method inside a class

```bash
pytest tests/test_handler.py::TestClassName::test_method_name
```

### Test — by keyword substring

```bash
pytest -k "keep"
pytest -k "resolve_provider and not integration"
```

### Useful pytest flags

```bash
pytest -x          # stop on first failure
pytest -vv         # extra verbose
pytest --tb=short  # shorter tracebacks
pytest -q          # quiet (only summary)
```

### Config / metadata

- Plugin WebUI schema: `_conf_schema.json`
- Runtime per-command overrides: `data/cmd_config.json`
- Plugin identity: `metadata.yaml`
- Pytest config: `pytest.ini`

---

## Code Style Guide

### Language and types

- Target Python 3.12 or newer. Use modern syntax: PEP 604 unions (`X | None`), PEP 695 generics, structural pattern matching (`match`/`case`), built-in generics (`list[int]`, `dict[str, T]`).
- All public functions and class methods must have type hints. Internal helpers should also be typed where practical.
- Do not use `Optional[...]` or `typing.List/Dict` — prefer the modern equivalents.
- Do not add `from __future__ import annotations` unless there's a specific reason; the project is pinned to 3.12+.

### Formatting (Ruff)

- Formatter: `ruff format` (Black-compatible). Default line length (88).
- Imports sorted by Ruff's isort-compatible rules.
- Always run `ruff check --fix` and `ruff format` before committing.

### Imports

- Prefer absolute imports over relative imports.
- Group order, separated by a blank line:
  1. Standard library
  2. Third-party (`astrbot.*`, `pytest`, etc.)
  3. First-party / local (`from compressor import ...`)
- Never use wildcard imports (`from x import *`).

### Naming conventions

- Modules / files: `snake_case.py`
- Functions, variables, methods: `snake_case`
- Classes: `PascalCase`
- Constants: `UPPER_SNAKE_CASE`
- Module-internal helpers / private attributes: leading underscore (`_resolve_provider`, `_build_summary`)
- Async functions / coroutines: same `snake_case` rules; suffix with `_async` only if it meaningfully disambiguates.
- Tests: files `test_*.py`, functions `test_*`, classes `Test*`.

### Error handling

- Catch the narrowest applicable exception; never bare `except:`.
- Use `raise NewError("…") from err` when translating exceptions at boundaries.
- **Hard rule (documented in README):** on any compression failure, the original `history` must be preserved — never mutate the input list in place on the error path.
- Surface errors through the AstrBot event API's standard return shape rather than `print()`-ing to stdout.
- Validate CLI-style args early in the handler so bad input fails fast with a friendly message.

### Logging

- Use the AstrBot logger (`from astrbot.api import logger`) rather than `logging.getLogger(__name__)`.
- INFO for lifecycle events (plugin init, command received, compression started/finished).
- WARNING / ERROR for recoverable failures and degraded fallbacks.

### AstrBot-specific conventions

- Entry point is `main.py`; commands and event handlers are registered through AstrBot's `@register` decorators.
- Plugin identity must remain in sync between `metadata.yaml`, `_conf_schema.json`, and `README.md`.
- Read configuration via AstrBot's config hook (declared in `_conf_schema.json`). Do not parse `data/cmd_config.json` from business logic unless intentionally reading a per-command override.
- Keep templates under `data/t2i_templates/` self-contained; do not introduce runtime CDN fetches at render time.

---

## Directory Structure and Architecture

```
F:\github\astrbot_plugin_compact
├── main.py                     # Plugin entry point: @register decorators, slash command wiring
├── compressor.py               # Wrapper around AstrBot's LLMSummaryCompressor
├── _conf_schema.json           # Plugin config schema (consumed by AstrBot WebUI)
├── metadata.yaml               # Plugin metadata (name, author, version)
├── pytest.ini                  # Pytest configuration
├── README.md                   # User-facing documentation
├── MANUAL_QA.md                # Manual QA notes / checklists
├── data/
│   ├── cmd_config.json         # Per-command runtime overrides
│   └── t2i_templates/          # HTML templates for rendered output
│       ├── base.html
│       ├── astrbot_powershell.html
│       └── astrbot_vitepress.html
│   └── temp/                   # Scratch / generated files (treat as ephemeral)
└── tests/
    ├── test_args.py            # Arg parsing for `/compact [--keep] [--provider] [focus]`
    ├── test_resolve_provider.py# Provider selection and precedence
    ├── test_compressor.py      # Compressor wrapper behavior and failure fallback
    ├── test_handler.py         # Slash-command handler integration
    ├── test_initialize.py      # Plugin init / lifecycle / config loading
    └── test_integration.py     # End-to-end flow
```

### Architecture

- **Single-process plugin.** Loaded by AstrBot at startup. No background threads, daemons, or scheduled jobs are expected.
- **Command flow**

  1. AstrBot parses `/compact [...]`.
  2. The handler registered in `main.py` is invoked.
  3. Args are parsed (covered by `test_args.py`).
  4. The provider is resolved with precedence rules (covered by `test_resolve_provider.py`).
  5. `compressor.py` invokes `LLMSummaryCompressor` against the session history, applying `keep_ratio`.
  6. The compressed result is returned through the AstrBot event API.

- **Configuration layering** (lowest to highest precedence):

  1. Plugin defaults declared in `_conf_schema.json`.
  2. Per-command overrides in `data/cmd_config.json`.
  3. Per-call CLI flags (`--keep`, `--provider`) — highest priority.

### Responsibilities of key files

| File | Responsibility |
|------|----------------|
| `main.py` | Registers the `/compact` command and any event hooks via AstrBot's `@register` API. |
| `compressor.py` | Wraps `astrbot.core.compression.LLMSummaryCompressor`; applies `keep_ratio`, builds the focus prompt, preserves original `history` on failure. |
| `_conf_schema.json` | Declarative config schema exposed in the AstrBot WebUI. |
| `data/cmd_config.json` | JSON map of per-command tweaks loaded at runtime. |
| `data/t2i_templates/*.html` | Self-contained HTML templates used for rendered output. |
| `metadata.yaml` | Plugin identity (name, author, version) consumed by AstrBot. |

### Test layout conventions

Each `tests/test_*.py` covers one layer:

- `test_args.py` — `/compact` argument parsing.
- `test_resolve_provider.py` — provider resolution and override precedence.
- `test_compressor.py` — compression wrapper behavior and failure fallback.
- `test_handler.py` — slash-command handler integration with the AstrBot event API.
- `test_initialize.py` — plugin lifecycle and config loading.
- `test_integration.py` — full end-to-end flow.

When adding tests, place them in the file whose scope matches the change. New top-level test files must start with `test_`.

---

## Notes for Agents

- Do **not** introduce a `pyproject.toml` / `setup.py` / `msbuild` build step unless explicitly asked — the plugin is loaded directly by AstrBot.
- Do **not** lower the Python version below 3.12.
- When changing `_conf_schema.json`, update the configuration table in `README.md` to match.
- Templates under `data/t2i_templates/` must remain self-contained.
- On any failure path inside `compressor.py`, the original `history` must be preserved — this is a hard guarantee documented in the README.
- When adding new public symbols, prefer adding them to the existing module that owns the responsibility rather than creating new top-level files.