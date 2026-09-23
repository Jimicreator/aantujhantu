"""Validate shared Quant Lab files without changing them."""
from __future__ import annotations

import csv
import os
from pathlib import Path

LAB_ROOT = Path(os.environ.get("QUANT_LAB_ROOT", r"C:\Shared_Quant_Lab"))
LOG_DIR = LAB_ROOT / "Logs"
REQUIRED = {"Unified_Trade_Log.csv": {"Timestamp", "Bot_ID", "Ticket", "Action"}, "Decision_Features.csv": {"Timestamp_UTC", "Bot_ID", "Decision_ID", "Action"}}


def inspect_csv(path: Path, required: set[str]):
    if not path.exists():
        return {"file": str(path), "status": "MISSING"}
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.reader(handle)
        header = next(reader, [])
        rows = sum(1 for _ in reader)
    missing = sorted(required.difference(header))
    return {"file": str(path), "status": "OK" if not missing else "SCHEMA_MISMATCH", "rows": rows, "missing_columns": missing, "columns": header}


def main():
    for name, required in REQUIRED.items():
        print(inspect_csv(LOG_DIR / name, required))
    print({"master_ml_log": str(LOG_DIR / "Master_ML_Log.csv"), "master_ml_log_exists": (LOG_DIR / "Master_ML_Log.csv").exists(), "master_excel_exists": (LOG_DIR / "Master_ML_Dataset.xlsx").exists()})


if __name__ == "__main__":
    main()
