"""Automatic offline research monitor for both XAUUSD bots.

This process watches C:\Shared_Quant_Lab\Logs\Master_ML_Log.csv. It starts
analysis after enough valid closed trades exist and retrains candidates after
new data arrives. It never connects to MT5 and never places or modifies orders.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

_trainer_spec = importlib.util.spec_from_file_location("quant_lab_shadow_trainer", Path(__file__).with_name("train_shadow_model_v2.py"))
if _trainer_spec is None or _trainer_spec.loader is None:
    raise ImportError("train_shadow_model_v2.py is unavailable")
_trainer_module = importlib.util.module_from_spec(_trainer_spec)
_trainer_spec.loader.exec_module(_trainer_module)
DEFAULT_CSV = _trainer_module.DEFAULT_CSV
train = _trainer_module.train

LAB_ROOT = Path(os.environ.get("QUANT_LAB_ROOT", r"C:\Shared_Quant_Lab"))
LOG_DIR = LAB_ROOT / "Logs"
REPORT_DIR = LAB_ROOT / "Reports" / "research"
STATE_DIR = LAB_ROOT / "State"
STATE_FILE = STATE_DIR / "RESEARCH_MONITOR_STATE.json"
LOCK_DIR = STATE_DIR / "RESEARCH_MONITOR.lock"
PID_FILE = LOCK_DIR / "pid"
MONITOR_LOG = LOG_DIR / "Research_Monitor.log"
MINIMUM_CLOSED_TRADES = 50
RETRAIN_EVERY_NEW_TRADES = 10


def _log(message: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with MONITOR_LOG.open("a", encoding="utf-8") as handle:
        handle.write(f"{datetime.now(timezone.utc).isoformat()} {message}\n")


def acquire_singleton() -> bool:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        LOCK_DIR.mkdir()
        PID_FILE.write_text(str(os.getpid()), encoding="ascii")
        return True
    except FileExistsError:
        try:
            pid = int(PID_FILE.read_text(encoding="ascii").strip())
            os.kill(pid, 0)
            return False
        except (OSError, ValueError):
            try:
                PID_FILE.unlink(missing_ok=True)
                LOCK_DIR.rmdir()
                LOCK_DIR.mkdir()
                PID_FILE.write_text(str(os.getpid()), encoding="ascii")
                return True
            except OSError:
                return False


def release_singleton() -> None:
    try:
        PID_FILE.unlink(missing_ok=True)
        LOCK_DIR.rmdir()
    except OSError:
        pass


def _resolve_input() -> Path:
    if DEFAULT_CSV.exists():
        return DEFAULT_CSV
    return LOG_DIR / "Decision_Features.csv"


def _numeric(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    for column in columns:
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame


def load_closed(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    frame = pd.read_csv(path)
    if "Action" not in frame.columns:
        return pd.DataFrame()
    frame = frame[frame["Action"].astype(str).str.upper().eq("CLOSE")].copy()
    frame = _numeric(frame, ["Final_Realized_PnL", "Max_Favorable_Excursion", "Max_Adverse_Excursion", "Spread", "Layer_Number"])
    if "Timestamp" in frame.columns:
        frame["Timestamp"] = pd.to_datetime(frame["Timestamp"], errors="coerce", utc=True)
        frame = frame.dropna(subset=["Timestamp"]).sort_values("Timestamp")
    return frame


def _group_report(frame: pd.DataFrame, column: str) -> list[dict]:
    if column not in frame.columns or "Final_Realized_PnL" not in frame.columns:
        return []
    grouped = []
    for key, group in frame.groupby(column, dropna=False):
        pnl = group["Final_Realized_PnL"].dropna()
        if pnl.empty:
            continue
        wins = pnl[pnl > 0]
        losses = pnl[pnl <= 0]
        grouped.append({
            "group": str(key),
            "trades": int(len(pnl)),
            "wins": int(len(wins)),
            "win_rate": round(float((pnl > 0).mean()), 4),
            "net_pnl": round(float(pnl.sum()), 4),
            "average_pnl": round(float(pnl.mean()), 4),
            "average_win": round(float(wins.mean()), 4) if not wins.empty else None,
            "average_loss": round(float(losses.mean()), 4) if not losses.empty else None,
            "max_loss": round(float(pnl.min()), 4),
        })
    return sorted(grouped, key=lambda row: row["net_pnl"], reverse=True)


def _hour_report(frame: pd.DataFrame) -> list[dict]:
    if "Timestamp" not in frame.columns:
        return []
    frame = frame.copy()
    frame["UTC_Hour"] = frame["Timestamp"].dt.hour
    return _group_report(frame, "UTC_Hour")


def _equity_drawdown(frame: pd.DataFrame) -> dict:
    if "Final_Realized_PnL" not in frame.columns:
        return {"max_drawdown": None, "final_net_pnl": None}
    pnl = frame["Final_Realized_PnL"].dropna()
    if pnl.empty:
        return {"max_drawdown": None, "final_net_pnl": None}
    equity = pnl.cumsum()
    drawdown = equity.cummax() - equity
    return {
        "max_drawdown": round(float(drawdown.max()), 4),
        "final_net_pnl": round(float(equity.iloc[-1]), 4),
        "peak_equity_pnl": round(float(equity.max()), 4),
    }


def build_report(path: Path, frame: pd.DataFrame) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    report = {
        "generated_utc": now.isoformat(),
        "input": str(path),
        "closed_trades": int(len(frame)),
        "bots": _group_report(frame, "Bot_ID"),
        "strategies": _group_report(frame, "Strategy_Version"),
        "regimes": _group_report(frame, "Regime"),
        "sessions": _group_report(frame, "Session"),
        "layers": _group_report(frame, "Layer_Number"),
        "utc_hours": _hour_report(frame),
        "drawdown": _equity_drawdown(frame),
        "mfe": _group_report(frame, "Regime") if "Max_Favorable_Excursion" not in frame else [],
    }
    if "Max_Favorable_Excursion" in frame.columns:
        report["average_mfe"] = round(float(frame["Max_Favorable_Excursion"].mean()), 4)
    if "Max_Adverse_Excursion" in frame.columns:
        report["average_mae"] = round(float(frame["Max_Adverse_Excursion"].mean()), 4)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    output = REPORT_DIR / f"research_report_{stamp}.json"
    output.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    (REPORT_DIR / "latest_research_report.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return output


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {"last_closed_trades": 0, "last_training_closed_trades": 0}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"last_closed_trades": 0, "last_training_closed_trades": 0}


def save_state(state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    temporary = STATE_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2), encoding="utf-8")
    os.replace(temporary, STATE_FILE)


def process_once() -> dict:
    path = _resolve_input()
    frame = load_closed(path)
    state = load_state()
    count = len(frame)
    result = {"status": "WAITING_FOR_DATA", "closed_trades": count, "input": str(path), "monitor_pid": os.getpid()}
    if count < MINIMUM_CLOSED_TRADES:
        state["last_closed_trades"] = count
        save_state(state)
        return result

    report_path = build_report(path, frame)
    result.update({"status": "REPORT_READY", "report": str(report_path)})
    previous_training_count = int(state.get("last_training_closed_trades", 0))
    if count >= MINIMUM_CLOSED_TRADES and count - previous_training_count >= RETRAIN_EVERY_NEW_TRADES:
        result["training"] = train(path, minimum_rows=MINIMUM_CLOSED_TRADES)
        _log(f"training status={result['training'].get('status', 'UNKNOWN')} rows={count}")
        state["last_training_closed_trades"] = count
    state["last_closed_trades"] = count
    save_state(state)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval-seconds", type=float, default=60.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if not acquire_singleton():
        print(json.dumps({"status": "ALREADY_RUNNING"}))
        return
    _log("monitor started")
    try:
        while True:
            print(json.dumps(process_once(), indent=2, default=str))
            if args.once:
                return
            time.sleep(args.interval_seconds)
    finally:
        _log("monitor stopped")
        release_singleton()


if __name__ == "__main__":
    main()
