"""Run an existing bot while recording lifecycle and heartbeat events."""
from __future__ import annotations

import argparse
import os
import subprocess
import time
from pathlib import Path

from quant_lab_session_logger import append_event


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bot-id", required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--account-login", default="")
    parser.add_argument("--terminal-path", default="")
    parser.add_argument("--symbol", default="XAUUSD")
    parser.add_argument("--strategy-version", default="")
    parser.add_argument("--heartbeat-seconds", type=float, default=10.0)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command and args.command[0] == "--" else args.command
    if not command:
        parser.error("provide the child command after --")
    if not args.source.exists():
        parser.error(f"source file not found: {args.source}")
    session_id = f"{args.bot_id}-{os.getpid()}-{int(time.time())}"
    common = (args.bot_id, args.account_login, args.terminal_path, args.symbol, args.strategy_version, str(args.source.resolve()))
    append_event(session_id, "RUN_START", *common, reason="SUPERVISOR_START")
    process = subprocess.Popen(command)
    try:
        while process.poll() is None:
            time.sleep(args.heartbeat_seconds)
            if process.poll() is None:
                append_event(session_id, "HEARTBEAT", *common, reason="CHILD_ALIVE")
    except KeyboardInterrupt:
        append_event(session_id, "RUN_STOP_REQUESTED", *common, reason="SUPERVISOR_INTERRUPT")
        process.terminate()
        process.wait(timeout=10)
        append_event(session_id, "RUN_STOP", *common, reason="SUPERVISOR_INTERRUPT")
        return 130
    append_event(session_id, "RUN_STOP", *common, reason=f"EXIT_CODE_{process.returncode}")
    return int(process.returncode or 0)


if __name__ == "__main__":
    raise SystemExit(main())
