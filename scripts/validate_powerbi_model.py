"""Evaluate every DAX measure in the live Power BI model and compare to the catalogue.

The model is loaded in Power BI Desktop, which hosts a local Analysis Services instance. This
script finds that instance, runs a DAX query that returns all measures in one row, and compares
each result against the expected value in ``docs/powerbi-measures.md``.

This is the acceptance test for Phase 5: a model that reproduces every expected value is correct.

Usage:
    python scripts/validate_powerbi_model.py
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AS_QUERY = ROOT / "tools" / "as-query"

#: (measure name, expected value, tolerance)
EXPECTED: list[tuple[str, float, float]] = [
    ("Total Orders", 99_441, 0),
    ("Eligible Orders", 96_470, 0),
    ("Total Revenue", 13_591_643.70, 0.01),
    ("Total Freight", 2_251_909.54, 0.01),
    ("Average Order Value", 136.68, 0.01),
    ("Freight Share of Revenue", 0.16568, 0.0001),
    ("Late Orders", 7_826, 0),
    ("Late Rate %", 0.08112, 0.0001),
    ("Average Delivery Days", 12.497, 0.01),
    ("Average Estimated Lead Days", 24.404, 0.01),
    ("Average Delay Days (late only)", 8.867, 0.01),
    ("On Time Rate %", 0.91888, 0.0001),
    ("Unique Customers", 96_096, 0),
    ("Orders per Customer", 1.035, 0.001),
    ("Repeat Customers", 2_997, 0),
    ("Repeat Purchase Rate %", 0.03119, 0.0001),
    ("Revenue per Customer", 141.438, 0.01),
    ("Average Review Score", 4.087, 0.001),
    ("Low Score Orders", 14_476, 0),
    ("Low Score Rate %", 0.14557, 0.0001),
]


def find_port() -> str | None:
    """Locate the Analysis Services port of the running Power BI Desktop instance."""
    base = Path(os.environ["LOCALAPPDATA"]) / "Microsoft" / "Power BI Desktop" / "AnalysisServicesWorkspaces"
    if not base.exists():
        return None
    candidates = sorted(
        base.glob("*/Data/msmdsrv.port.txt"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        return None
    raw = Path(candidates[0]).read_bytes()
    # The port file is written as UTF-16LE with a BOM, but not always.
    for encoding in ("utf-16", "utf-8-sig", "utf-8"):
        try:
            text = raw.decode(encoding).strip()
        except (UnicodeError, UnicodeDecodeError):
            continue
        if text:
            return text
    return None


def build_dax() -> str:
    """One row, one column per measure."""
    parts = []
    for name, _, _ in EXPECTED:
        parts.append(f'    "{name}", [{name}]')
    body = ",\n".join(parts)
    return f"EVALUATE\nROW(\n{body}\n)"


def main() -> int:
    port = find_port()
    if not port:
        print("No Power BI Desktop Analysis Services instance found. Open the project first.")
        return 1
    print(f"Analysis Services port: {port}")

    dax = build_dax()
    proc = subprocess.run(
        ["dotnet", "run", "--project", str(AS_QUERY), "--", port, "--dax", dax],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    if proc.returncode != 0:
        print("DAX query failed:")
        print(proc.stdout)
        print(proc.stderr)
        return 1

    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    # Skip the "connected:" and "--- db ---" lines.
    data = [ln for ln in lines if "\t" in ln]
    if len(data) < 2:
        print("Unexpected output:")
        print(proc.stdout)
        return 1

    headers = [h.strip().strip("[]") for h in data[0].rstrip("\t").split("\t")]
    values = data[1].rstrip("\t").split("\t")
    result = dict(zip(headers, values, strict=False))

    failures = 0
    print(f"\n{'Measure':34s} {'Expected':>14s} {'Actual':>14s}  Status")
    print("-" * 78)
    for name, expected, tol in EXPECTED:
        raw = result.get(name)
        if raw is None:
            print(f"{name:34s} {expected:>14,.4f} {'MISSING':>14s}  MISSING")
            failures += 1
            continue
        try:
            actual = float(raw)
        except ValueError:
            print(f"{name:34s} {expected:>14,.4f} {raw:>14s}  NOT NUMERIC")
            failures += 1
            continue
        ok = abs(actual - expected) <= tol
        if not ok:
            failures += 1
        print(
            f"{name:34s} {expected:>14,.4f} {actual:>14,.4f}  {'OK' if ok else 'MISMATCH'}"
        )

    print("-" * 78)
    if failures:
        print(f"{failures} of {len(EXPECTED)} measures do not match.")
        return 1
    print(f"All {len(EXPECTED)} measures match the catalogue.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
