"""Command-line wrapper for the offline ML Excel report builder."""
from __future__ import annotations

import argparse
from pathlib import Path

from ml_excel_converter import DEFAULT_OUTPUT, build_ml_excel_sheet, resolve_input

parser = argparse.ArgumentParser()
parser.add_argument("--input", type=Path, default=None)
parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
parser.add_argument("--symbol", default="XAUUSD")
args = parser.parse_args()
result = build_ml_excel_sheet(resolve_input(args.input), args.output, args.symbol)
print(f"Rows processed: {result['rows']}")
print(f"Output: {result['output']}")
