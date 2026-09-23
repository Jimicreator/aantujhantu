"""Shared offline ML logging and research-monitor startup for both XAUUSD bots."""
from __future__ import annotations

import csv
import math
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

CSV_FIELDS = [
    "Timestamp", "Bot_ID", "Ticket", "Action", "Volume", "Entry_Price", "Exit_Price",
    "Final_Realized_PnL", "Win_Loss", "Max_Favorable_Excursion", "Max_Adverse_Excursion",
    "Strategy_Version", "Symbol", "Timeframe", "Session", "HTF_Trend", "Regime", "Layer_Number",
    "Spread", "Bid", "Ask", "ATR_14", "Recent_Volatility_Ratio", "RSI_14", "ADX_14",
    "EMA_3", "EMA_7", "EMA_20", "EMA_50", "EMA_Separation", "BB_Position", "Choppiness",
    "Slope_to_ATR", "Recent_Wins", "Recent_Losses",
]


def start_background_research() -> None:
    monitor = Path(__file__).with_name("research_monitor.py")
    if not monitor.exists():
        return
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
    try:
        subprocess.Popen(
            [sys.executable, str(monitor), "--interval-seconds", "60"],
            cwd=str(monitor.parent), stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=flags, close_fds=True,
        )
    except OSError:
        pass


def _ema(values: list[float], period: int) -> float:
    if not values:
        return 0.0
    result = values[0]
    alpha = 2.0 / (period + 1.0)
    for value in values[1:]:
        result = (value - result) * alpha + result
    return result


def _rsi(values: list[float], period: int = 14) -> float:
    if len(values) <= period:
        return 50.0
    gains = []
    losses = []
    for previous, current in zip(values[-period - 1:-1], values[-period:]):
        change = current - previous
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    average_gain = sum(gains) / period
    average_loss = sum(losses) / period
    if average_loss == 0:
        return 100.0 if average_gain else 50.0
    return 100.0 - (100.0 / (1.0 + average_gain / average_loss))


def _adx(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> float:
    if len(closes) <= period:
        return 25.0
    plus = []
    minus = []
    true_ranges = []
    for index in range(1, len(closes)):
        up = highs[index] - highs[index - 1]
        down = lows[index - 1] - lows[index]
        plus.append(up if up > down and up > 0 else 0.0)
        minus.append(down if down > up and down > 0 else 0.0)
        true_ranges.append(max(highs[index] - lows[index], abs(highs[index] - closes[index - 1]), abs(lows[index] - closes[index - 1])))
    total_range = sum(true_ranges[-period:])
    if total_range == 0:
        return 0.0
    plus_di = 100.0 * sum(plus[-period:]) / total_range
    minus_di = 100.0 * sum(minus[-period:]) / total_range
    denominator = plus_di + minus_di
    return 100.0 * abs(plus_di - minus_di) / denominator if denominator else 0.0


def capture_features(mt5, symbol: str, bid: float, ask: float, layer: int, strategy_version: str, recent_wins: int = 0, recent_losses: int = 0) -> dict:
    spread = ask - bid
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M1, 0, 100)
    htf_rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M15, 0, 10)
    if rates is None or htf_rates is None or len(rates) < 60 or len(htf_rates) < 2:
        return {}
    closed = rates[:-1]
    closes = [float(row["close"]) for row in closed]
    highs = [float(row["high"]) for row in closed]
    lows = [float(row["low"]) for row in closed]
    ranges = [high - low for high, low in zip(highs, lows)]
    atr = sum(ranges[-14:]) / 14
    baseline = sum(ranges[-50:]) / 50
    fast = sum(ranges[-5:]) / 5
    ema3, ema7, ema20, ema50 = (_ema(closes, period) for period in (3, 7, 20, 50))
    mean = sum(closes[-20:]) / 20
    deviation = math.sqrt(sum((value - mean) ** 2 for value in closes[-20:]) / 20)
    upper, lower = mean + 2 * deviation, mean - 2 * deviation
    bb_position = (closes[-1] - lower) / (upper - lower) if upper > lower else 0.5
    true_ranges = [max(highs[index] - lows[index], abs(highs[index] - closes[index - 1]), abs(lows[index] - closes[index - 1])) for index in range(-14, 0)]
    denominator = max(highs[-14:]) - min(lows[-14:])
    choppiness = 50.0
    if denominator > 0 and sum(true_ranges) > 0:
        choppiness = 100.0 * math.log10(sum(true_ranges) / denominator) / math.log10(14)
    recent = closes[-30:]
    x_mean = 14.5
    y_mean = sum(recent) / len(recent)
    slope = sum((index - x_mean) * (value - y_mean) for index, value in enumerate(recent)) / sum((index - x_mean) ** 2 for index in range(len(recent)))
    htf_closes = [float(row["close"]) for row in htf_rates]
    htf_trend = "BULLISH" if htf_closes[-1] >= sum(htf_closes) / len(htf_closes) else "BEARISH"
    utc_hour = datetime.now(timezone.utc).hour
    session = "ASIAN" if 1 <= utc_hour < 8 else "LONDON" if 8 <= utc_hour < 13 else "NEW_YORK" if 13 <= utc_hour < 20 else "DEAD_ZONE"
    return {
        "Strategy_Version": strategy_version, "Symbol": symbol, "Timeframe": "M1", "Session": session,
        "HTF_Trend": htf_trend, "Regime": "TRENDING" if choppiness < 50 else "RANGE", "Layer_Number": layer,
        "Spread": round(spread, 5), "Bid": bid, "Ask": ask, "ATR_14": round(atr, 5),
        "Recent_Volatility_Ratio": round(fast / max(baseline, 0.00001), 4), "RSI_14": round(_rsi(closes), 3),
        "ADX_14": round(_adx(highs, lows, closes), 3), "EMA_3": round(ema3, 5), "EMA_7": round(ema7, 5),
        "EMA_20": round(ema20, 5), "EMA_50": round(ema50, 5), "EMA_Separation": round(ema3 - ema7, 5),
        "BB_Position": round(bb_position, 5), "Choppiness": round(choppiness, 3),
        "Slope_to_ATR": round(slope / atr, 5) if atr else 0.0, "Recent_Wins": recent_wins, "Recent_Losses": recent_losses,
    }


def log_closed_trade(path: str, bot_id: str, ticket: int, volume: float, entry_price: float, exit_price: float, pnl: float, mfe: float, mae: float, reason: str, features: dict) -> None:
    if not features:
        return
    row = {field: "" for field in CSV_FIELDS}
    row.update(features)
    row.update({
        "Timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), "Bot_ID": bot_id,
        "Ticket": ticket, "Action": "CLOSE", "Volume": volume, "Entry_Price": entry_price,
        "Exit_Price": exit_price, "Final_Realized_PnL": round(pnl, 4), "Win_Loss": "WIN" if pnl > 0 else "LOSS",
        "Max_Favorable_Excursion": round(mfe, 4), "Max_Adverse_Excursion": round(mae, 4), "Reason": reason,
    })
    try:
        file_exists = os.path.exists(path)
        with open(path, "a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
            if not file_exists:
                writer.writeheader()
            writer.writerow(row)
    except OSError:
        pass
