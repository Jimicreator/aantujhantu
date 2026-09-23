"""Run-session and heartbeat logger for the shared Quant Lab."""
from __future__ import annotations

import csv
import os
import socket
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

LAB_ROOT = Path(os.environ.get("QUANT_LAB_ROOT", r"C:\Shared_Quant_Lab"))
LOG_DIR = LAB_ROOT / "Logs"
SESSION_LOG = LOG_DIR / "Run_Sessions.csv"
FIELDS = ["Event_Time_UTC", "Session_ID", "Event_Type", "Bot_ID", "Process_ID", "Host", "Account_Login", "Terminal_Path", "Symbol", "Strategy_Version", "Source_File", "Reason"]


def append_event(session_id, event_type, bot_id, account_login="", terminal_path="", symbol="XAUUSD", strategy_version="", source_file="", reason=""):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    row = {"Event_Time_UTC": datetime.now(timezone.utc).isoformat(), "Session_ID": session_id, "Event_Type": event_type, "Bot_ID": bot_id, "Process_ID": os.getpid(), "Host": socket.gethostname(), "Account_Login": account_login, "Terminal_Path": terminal_path, "Symbol": symbol, "Strategy_Version": strategy_version, "Source_File": source_file, "Reason": reason}
    file_exists = SESSION_LOG.exists()
    with SESSION_LOG.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)


def run_heartbeat(bot_id, account_login="", terminal_path="", symbol="XAUUSD", strategy_version="", source_file="", interval_seconds=10.0):
    session_id = uuid.uuid4().hex
    append_event(session_id, "RUN_START", bot_id, account_login, terminal_path, symbol, strategy_version, source_file)
    try:
        while True:
            time.sleep(interval_seconds)
            append_event(session_id, "HEARTBEAT", bot_id, account_login, terminal_path, symbol, strategy_version, source_file)
    except KeyboardInterrupt:
        append_event(session_id, "RUN_STOP", bot_id, account_login, terminal_path, symbol, strategy_version, source_file, "KEYBOARD_INTERRUPT")
        raise
