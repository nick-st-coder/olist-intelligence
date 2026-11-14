"""Validate PBIR report files against the official Microsoft JSON schemas.

Power BI Desktop validates these on open, but it needs a GUI. This script fetches each file's
declared ``$schema`` and validates against it, so report authoring can be checked in CI or from
the terminal. Schemas are cached under ``.tools/pbir-schema-cache``.

Usage:
    python scripts/validate_pbir.py powerbi/Olist.Report
"""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

import jsonschema

CACHE = Path(".tools/pbir-schema-cache")


def fetch_schema(url: str) -> dict:
    CACHE.mkdir(parents=True, exist_ok=True)
    cached = CACHE / (url.rsplit("/", 1)[-1] + "-" + str(abs(hash(url))))
    if cached.exists():
        return json.loads(cached.read_text(encoding="utf-8"))
    with urllib.request.urlopen(url, timeout=30) as response:
        schema = json.loads(response.read().decode("utf-8"))
    cached.write_text(json.dumps(schema), encoding="utf-8")
    return schema


def validate_file(path: Path) -> list[str]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"{path}: invalid JSON — {exc}"]

    url = document.get("$schema")
    if not url:
        return [f"{path}: no $schema property"]

    try:
        schema = fetch_schema(url)
    except Exception as exc:
        return [f"{path}: could not fetch schema {url} — {exc}"]

    validator = jsonschema.Draft7Validator(schema)
    errors = sorted(validator.iter_errors(document), key=lambda e: list(e.path))
    return [
        f"{path}: {'/'.join(str(p) for p in err.path) or '<root>'} — {err.message}"
        for err in errors
    ]


def main(argv: list[str]) -> int:
    root = Path(argv[1] if len(argv) > 1 else "powerbi/Olist.Report")
    if not root.exists():
        print(f"not found: {root}")
        return 2

    files = sorted(root.rglob("*.json"))
    # .platform files use a different schema family and are not report definitions.
    files = [f for f in files if f.name != ".platform"]

    all_errors: list[str] = []
    for path in files:
        errors = validate_file(path)
        status = "OK  " if not errors else "FAIL"
        print(f"{status} {path.relative_to(root)}")
        all_errors.extend(errors)

    print("-" * 70)
    if all_errors:
        for err in all_errors:
            print(err)
        print(f"{len(all_errors)} schema error(s) across {len(files)} file(s)")
        return 1
    print(f"All {len(files)} file(s) validate against their declared schemas.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
