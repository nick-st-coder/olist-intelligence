---
description: "Scaffold a new numbered analysis notebook for the Olist project with the standard structure (objective, questions, sections, findings)."
name: "Olist New Notebook"
agent: "Olist EDA Analyst"
tools: [read, edit, search, execute]
argument-hint: "Notebook topic, e.g. 'seller performance analysis'"
---
Create a new notebook in `notebooks/` following the project convention.

1. Determine the next number: list `notebooks/*.ipynb` and use the next zero-padded index. Name it `<NN>_<snake_case_topic>.ipynb`.
2. Structure the notebook with these cells:
   - **Markdown title**: notebook name, objective, business questions answered, datasets used.
   - **Setup cell**: imports, project-root path resolution, plotting style, `pd.set_option` for readable output. Read data from `Data/` (capital D) read-only.
   - **Markdown section per business question**, each followed by a code cell that computes and plots the answer.
   - **Markdown "Findings"** section at the end with numbered findings, each including the metric, the n, and the business implication.
3. Do not put reusable transformation logic in the notebook — if it is needed twice, note that it belongs in `src/`.
4. Do not use post-purchase fields to build ML features here.

Report the created path and the section outline.
