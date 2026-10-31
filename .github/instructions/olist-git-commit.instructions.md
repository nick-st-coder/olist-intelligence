---
description: "Use when creating git commits, writing commit messages, staging files, or reviewing git history in the Olist project. Defines the required commit message format (initialized/added/modified/fixed/removed/docs/test/refactor/chore), grouping rules, and what must never be committed."
name: "Olist Git Commit Conventions"
applyTo: "**"
---

# Git Commit Conventions

## Commit message format

```
<verb>: <short imperative summary>

<optional body: what changed and why, wrapped at 72 chars>

<optional footer: refs, breaking changes>
```

**Subject line:** lowercase verb, colon, space, lowercase summary, no trailing period, ≤ 72 chars.

## Allowed verbs

| Verb | Use for |
|------|---------|
| `initialized:` | First commit of a new project, module, package, or scaffold |
| `added:` | New feature, file, model, endpoint, notebook, test, dependency |
| `modified:` | Changed behavior or content of something that already existed |
| `fixed:` | Bug fix — always state the bug, not the file |
| `removed:` | Deleted file, feature, dependency, or dead code |
| `refactored:` | Restructure with no behavior change |
| `docs:` | Documentation only (README, CONTEXT.md, docstrings, comments) |
| `test:` | Test-only changes |
| `chore:` | Tooling, config, CI, dependency bumps, formatting |
| `data:` | Dataset, schema, or dbt model changes |

Do **not** use `update:`, `changes:`, `wip:`, `misc:`, or bare `fix`. Do not use Conventional Commits prefixes (`feat:`, `fix(scope):`) — this project uses the verb list above.

## Grouping rules

One commit = one logical change. Split by concern, not by file count:

- Scaffolding → `initialized:`
- Agent/instruction/prompt files → `added:` (separate commit from code)
- A feature and its tests → same commit
- A bug fix and its regression test → same commit
- Formatting or lint-only churn → `chore:`, never mixed into a feature commit
- Never mix a refactor with a behavior change

## Staging

- Stage explicitly by path. Avoid `git add -A` / `git add .` — it sweeps in caches and secrets.
- Verify with `git status --porcelain` and `git diff --cached --stat` before committing.
- Never commit `.venv/`, `__pycache__/`, `.pytest_cache/`, `.ruff_cache/`, `.ipynb_checkpoints/`, `mlruns/`, `dbt/target/`.

## Never commit

- `.env` or any file containing credentials, tokens, keys, or connection strings
- `Data/` — the raw Kaggle CSVs are the read-only source of truth and are not versioned
- Model binaries (`*.pkl`, `*.joblib`, `*.onnx`) — use MLflow registry
- Large derived datasets in `data/interim/` or `data/processed/`

## Examples

```
initialized: project scaffold with uv, ruff and pytest config
```

```
added: phase 1 data understanding notebook

Profiles all nine raw CSVs: inventory, dtypes, nulls, grain, FK integrity,
data dictionary and data-quality issues. Confirms 96,470 ML-eligible orders
with an 8.11% late rate.
```

```
fixed: review join multiplying order rows

order_reviews has 547 orders with more than one review and 814 reused
review_id values. Deduplicate to the latest review per order before joining.
```

```
modified: late-delivery target to use end-of-day estimate

order_estimated_delivery_date is date-only, so 1,292 orders delivered on the
estimate day were counted late. Compare against 23:59:59 instead.
```

## Before committing

Run `/olist-commit-check` — it runs ruff, pytest, dbt tests, a secrets scan and a leakage reminder.
