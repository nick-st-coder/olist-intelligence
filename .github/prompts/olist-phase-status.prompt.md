---
description: "Show the current phase of the Olist platform project, what is done, what is next, and the recommended agent/prompt to use."
name: "Olist Phase Status"
agent: "agent"
tools: [read, search, execute]
argument-hint: "Optional: a phase number to focus on"
---
Report the project status for the Olist E-commerce Intelligence Platform.

1. Inspect the workspace to determine which phases have artifacts:
   - Phase 1–2: `notebooks/01_data_understanding.ipynb`, `notebooks/02_eda.ipynb`, data dictionary docs
   - Phase 3: `src/preprocessing/`, `src/validation/schemas.py`, `tests/test_cleaning.py`
   - Phase 4: `dbt/models/**`, `dags/`, `docker-compose.yml`
   - Phase 5: Power BI / Excel artifacts
   - Phase 6: `src/features/`, `src/modeling/`, MLflow runs
   - Phase 7: `api/main.py`, `app/streamlit_app.py`, `docker/`
   - Phase 8: `src/monitoring/drift.py`, Evidently reports
   - Phase 9: `.github/workflows/`, `README.md`
2. Output a table: Phase | Status (Not started / In progress / Done) | Evidence (paths found) | Missing deliverables.
3. Identify the **current phase** (first not Done) and list its next 3 concrete tasks.
4. Recommend the agent and prompt to use next (e.g. `Olist Data Engineer` + `/olist-add-dbt-model`).
5. Flag any blockers: missing raw files, failing tests, leakage risks.

Do not modify any files. Keep the report under 40 lines.
