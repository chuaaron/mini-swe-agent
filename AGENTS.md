# Repository Guidelines

## Project Structure & Module Organization
- `src/minisweagent/` is the package root, with core modules in `agents/`, `models/`, `environments/`, and `run/`.
- `src/minisweagent/config/` holds default YAML configs and UI assets; keep changes here aligned with CLI behavior.
- `tests/` mirrors the package layout with `test_*.py` files and shared fixtures in `tests/conftest.py`.
- `docs/` and `mkdocs.yml` contain the documentation site content and configuration.

## Build, Test, and Development Commands
```sh
pip install -e ".[dev]"
pre-commit install
mini            # CLI (simple UI)
mini -v         # CLI (visual UI)
python src/minisweagent/run/hello_world.py
pytest -n auto
```
- Use `pip install -e .` for an editable install; `.[dev]` adds tooling and test deps.
- `pre-commit run --all-files` runs Ruff (lint/format) plus typos checks.
- `pytest -m "not slow"` skips tests marked with `@pytest.mark.slow`.

## Coding Style & Naming Conventions
- Python with 4-space indents, 120-char lines, and double quotes (Ruff format settings).
- Prefer `snake_case` for modules/functions and `CapWords` for classes.
- Keep components minimal and self-contained; introduce `utils/` only for shared helpers and `run/extra/` for niche workflows.

## Testing Guidelines
- Use pytest + pytest-asyncio; new tests should live under `tests/` and follow `test_*.py` naming.
- Match the module under test (e.g., `src/minisweagent/models/...` -> `tests/models/...`).
- Use markers sparingly; document slow or integration-heavy tests with `@pytest.mark.slow`.

## Commit & Pull Request Guidelines
- Recent commits follow `Type: summary` (e.g., `Fix: ...`, `Feat: ...`, `Doc: ...`, `CI: ...`, `chore: ...`) and often include `(#123)` references.
- PRs should include a clear description, tests run, and linked issues.
- For UI changes (`mini`/`mini -v`), add screenshots or short GIFs; for user-facing behavior, update relevant docs.
