"""Load and summarize the Ripple dataset without altering its source files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd


BACKEND_DIR = Path(__file__).resolve().parent
DATASET_DIR = BACKEND_DIR / "data" / "The_Ripple_Dataset"


def load_dataset(dataset_dir: Path | str = DATASET_DIR) -> dict[str, Any]:
    """Load dataset tables and documentation and return data plus diagnostics.

    CSV fields are loaded as strings and blank cells remain empty strings. This
    avoids coercing identifiers or silently filling unknown values.
    """
    root = Path(dataset_dir).resolve()
    csv_data: dict[str, pd.DataFrame] = {}
    documents: dict[str, Any] = {}
    loaded_files: list[str] = []
    errors: list[dict[str, str]] = []
    csv_summary: dict[str, dict[str, Any]] = {}

    if not root.is_dir():
        errors.append({"file": str(root), "error": "Dataset directory is missing or is not a directory."})
        return {
            "dataset_path": str(root), "loaded_files": [], "csv_files": {},
            "documents": {}, "errors": errors,
        }

    for path in sorted(root.glob("*.csv")):
        try:
            frame = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
            csv_data[path.name] = frame
            loaded_files.append(path.name)
            missing_by_column = {
                str(column): int(frame[column].map(lambda value: not str(value).strip()).sum())
                for column in frame.columns
            }
            csv_summary[path.name] = {
                "row_count": int(len(frame)),
                "columns": [str(column) for column in frame.columns],
                "missing_cell_count": int(sum(missing_by_column.values())),
                "missing_cells_by_column": missing_by_column,
            }
        except Exception as exc:  # Report individual file failures and continue.
            errors.append({"file": path.name, "error": f"Could not read CSV: {exc}"})

    optional_files = {
        "incident.json": "json",
        "dataset_manifest.json": "json",
        "optimization_rules.md": "text",
        "ACRONYMS_AND_DATA_GLOSSARY.md": "text",
    }
    for filename, kind in optional_files.items():
        path = root / filename
        if not path.exists():
            errors.append({"file": filename, "error": "Optional dataset file is missing."})
            continue
        try:
            if kind == "json":
                with path.open("r", encoding="utf-8-sig") as handle:
                    documents[filename] = json.load(handle)
            else:
                documents[filename] = path.read_text(encoding="utf-8-sig")
            loaded_files.append(filename)
        except Exception as exc:
            errors.append({"file": filename, "error": f"Could not read file: {exc}"})

    manifest = documents.get("dataset_manifest.json")
    expected_tables = manifest.get("row_counts", {}) if isinstance(manifest, dict) else {}
    for filename in expected_tables:
        if filename not in csv_data and not any(error["file"] == filename for error in errors):
            errors.append({"file": filename, "error": "CSV listed in dataset_manifest.json is missing."})

    return {
        "dataset_path": str(root),
        "loaded_files": loaded_files,
        "csv_files": csv_summary,
        "documents": documents,
        "errors": errors,
        "_csv_data": csv_data,
    }


def get_dataset_summary(dataset: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return the public summary, excluding loaded DataFrames and document bodies."""
    dataset = dataset if dataset is not None else load_dataset()
    return {
        "dataset_path": dataset["dataset_path"],
        "loaded_files": dataset["loaded_files"],
        "csv_files": dataset["csv_files"],
        "documents_loaded": sorted(dataset["documents"].keys()),
        "errors": dataset["errors"],
    }
