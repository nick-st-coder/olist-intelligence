---
description: "Pre-commit quality gate for the Olist project: run lint, tests, dbt tests and data validation, then summarize what is safe to commit."
name: "Olist Commit Check"
agent: "agent"
tools: [read, search, execute]
argument-hint: "Optional: scope, e.g. 'src/features'"
---
Run the pre-commit quality gate for the Olist E-commerce Intelligence Platform.

1. **Lint & format**: `uv run ruff check .` and `uv run ruff format --check .` (skip gracefully if `uv`/`ruff` is not installed and say so).
2. **Unit tests**: `uv run pytest -q`. Report pass/fail counts and the first failing test's traceback.
3. **dbt tests** (if `dbt/` exists and a profile is configured): `dbt build --select state:modified+` or `dbt test`. If no profile is configured, say so explicitly instead of failing silently.
4. **Data validation**: run the Pandera schema checks in `src/validation/` if a runner exists.
5. **Secrets scan**: grep the staged/changed files for `password`, `secret`, `api_key`, `token`, `GOOGLE_APPLICATION_CREDENTIALS`, and any `.env` file that is not `.env.example`. Flag hits.
6. **Leakage guard**: if `src/features/` changed, remind the user to run `/olist-leakage-check`.

Output a checklist table: Check | Result | Details. End with **Ready to commit** or **Fix before committing** plus the exact commands that failed.
