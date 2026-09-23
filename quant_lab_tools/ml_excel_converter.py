"""Offline ML dataset and Excel report builder."""
from __future__ import annotations

import csv
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

LAB_ROOT = Path(os.environ.get("QUANT_LAB_ROOT", r"C:\Shared_Quant_Lab"))
DEFAULT_INPUT = LAB_ROOT / "Logs" / "Master_ML_Log.csv"
FALLBACK_INPUT = LAB_ROOT / "Logs" / "Decision_Features.csv"
DEFAULT_OUTPUT = LAB_ROOT / "Logs" / "Master_ML_Dataset.xlsx"


def resolve_input(path: Path | None = None) -> Path:
    if path is not None:
        return Path(path)
    if DEFAULT_INPUT.exists():
        return DEFAULT_INPUT
    if FALLBACK_INPUT.exists():
        return FALLBACK_INPUT
    return DEFAULT_INPUT


def _parse_timestamp(value: str) -> datetime:
    value = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            parsed = datetime.strptime(value, fmt)
            return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
        except ValueError:
            continue
    raise ValueError(f"Unsupported timestamp: {value!r}")


def _number(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _read_rows(csv_path: Path) -> list[dict]:
    with csv_path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _side(row: dict) -> str | None:
    for key in ("Side", "Direction", "Trade_Side"):
        value = str(row.get(key, "")).upper().strip()
        if value in {"BUY", "SELL"}:
            return value
    return None


def _entry_time(row: dict) -> datetime | None:
    for key in ("Entry_Timestamp", "Entry_Time_UTC", "Open_Timestamp"):
        value = row.get(key)
        if value:
            return _parse_timestamp(value)
    return None


def _forward_price(mt5_module, symbol: str, timestamp: datetime, minutes: int):
    target = timestamp + timedelta(minutes=minutes)
    rates = mt5_module.copy_rates_range(symbol, mt5_module.TIMEFRAME_M1, target, target + timedelta(minutes=1))
    if rates is None or len(rates) == 0:
        return None
    return float(rates[0]["close"])


def _append_labels(rows: Iterable[dict], mt5_module, symbol: str) -> list[dict]:
    output = []
    for row in rows:
        enriched = dict(row)
        side = _side(row)
        entry_time = _entry_time(row)
        entry_price = _number(row.get("Entry_Price"))
        volume = _number(row.get("Volume"), 0.0)
        enriched["Label_Status"] = "UNAVAILABLE"
        enriched["Profit_5m_Horizon"] = "UNAVAILABLE"
        enriched["Profit_15m_Horizon"] = "UNAVAILABLE"
        if side is None or entry_time is None or entry_price is None:
            enriched["Label_Status"] = "MISSING_ENTRY_SIDE_OR_TIME"
            output.append(enriched)
            continue
        for minutes, column in ((5, "Profit_5m_Horizon"), (15, "Profit_15m_Horizon")):
            future_price = _forward_price(mt5_module, symbol, entry_time, minutes)
            if future_price is None:
                enriched[column] = "PENDING"
                enriched["Label_Status"] = "PENDING_MARKET_DATA"
                continue
            difference = future_price - entry_price
            if side == "SELL":
                difference = -difference
            enriched[column] = round(difference * volume * 100.0, 2)
            if enriched["Label_Status"] == "UNAVAILABLE":
                enriched["Label_Status"] = "READY"
        output.append(enriched)
    return output


def _write_excel(rows: list[dict], output_path: Path) -> None:
    try:
        import pandas as pd
    except ImportError as exc:
        raise RuntimeError("pandas is required to build the Excel report") from exc
    frame = pd.DataFrame(rows)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        frame.to_excel(writer, index=False, sheet_name="ML_Dataset")
        sheet = writer.sheets["ML_Dataset"]
        for column_cells in sheet.columns:
            letter = column_cells[0].column_letter
            width = min(42, max(12, max(len(str(cell.value or "")) for cell in column_cells) + 2))
            sheet.column_dimensions[letter].width = width


def build_ml_excel_sheet(csv_path=None, excel_path=DEFAULT_OUTPUT, symbol="XAUUSD"):
    input_path = resolve_input(csv_path)
    output_path = Path(excel_path)
    if not input_path.exists():
        raise FileNotFoundError(input_path)
    rows = _read_rows(input_path)
    if not rows:
        return {"rows": 0, "output": str(output_path)}
    import MetaTrader5 as mt5
    if not mt5.initialize():
        raise RuntimeError(f"MT5 initialization failed: {mt5.last_error()}")
    try:
        labeled = _append_labels(rows, mt5, symbol)
    finally:
        mt5.shutdown()
    _write_excel(labeled, output_path)
    return {"rows": len(labeled), "output": str(output_path)}


if __name__ == "__main__":
    result = build_ml_excel_sheet()
    print(f"Built {result['rows']} rows: {result['output']}")
