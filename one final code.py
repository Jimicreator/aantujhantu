import time
import os
import csv
import math
from datetime import datetime, timezone
import MetaTrader5 as mt5

# ================= CONFIGURATION (OMNI-ENGINE) =================
SYMBOL = "XAUUSD"               
LOT_SIZE = 0.01                 

# Pyramiding & Risk Limits
MAX_TREND_LAYERS = 4            # Exponential scaling for Trends
MAX_RANGE_LAYERS = 1            # STRICTLY 1 layer for Ranges (No pyramiding in chop)
MAX_LOSS_PER_UNIT_USD = 3.00    # Optimal breathing room for Gold
PYRAMID_ADD_THRESHOLD = 1.00    # Add next layer when +$1.00 in profit

# Smart Trailing Profile
BE_TRIGGER_USD        = 0.75    # When to lock Break-Even
TRAIL_TRIGGER_USD     = 2.50    # When to start the heavy trail
TRAIL_DISTANCE_USD    = 1.50    # Wider breathing room for Gold pullbacks

# 90/10 Virtual Vault
INITIAL_CAPITAL = 20.00         
MAX_RISK_ALLOWANCE = 15.00      
PROFIT_LOCK_PCT = 0.90          

MAX_ALLOWED_SPREAD = 0.40       
POLL_INTERVAL_SECONDS = 0.2     
MAGIC_NUMBER = 880999           # Unified Omni Magic Number

# [!] CHANGE THIS PATH DEPENDING ON WHICH ACCOUNT FOLDER YOU RUN IT IN
TERMINAL_PATH = r"C:\Program Files\MetaTrader 5\terminal64.exe"

# --- SHARED QUANT LAB PATHS ---
LAB_DIR = r"C:\Shared_Quant_Lab"
LOG_DIR = os.path.join(LAB_DIR, "Logs")
os.makedirs(LOG_DIR, exist_ok=True)
CSV_LOG_FILE = os.path.join(LOG_DIR, "Master_ML_Log.csv")
# =========================================================================

_symbol = "XAUUSD"
peak_equity = INITIAL_CAPITAL
cooldown_until = 0.0
ticket_tracker = {}
last_known_positions = set()
_digits = 2

def init_mt5():
    global _symbol, _digits
    if not mt5.initialize(path=TERMINAL_PATH):
        print(f"[-] MT5 Init failed: {mt5.last_error()}")
        os._exit(1)
    
    account = mt5.account_info()
    if account is None:
        mt5.shutdown()
        os._exit(1)
        
    print(f"[ACCOUNT] Omni-Bot connected | Login: {account.login} | Equity: ${account.equity:.2f}")
    for sym in (SYMBOL, "XAUUSDm", "GOLD", "GOLDm"):
        info = mt5.symbol_info(sym)
        if info is not None:
            mt5.symbol_select(sym, True)
            _symbol = sym
            _digits = info.digits
            break
    print(f"[+] OMNI-ENGINE ACTIVE | Dynamic Regime Shifting | ML Shadow Logging On")

def get_fill_mode():
    sym_info = mt5.symbol_info(_symbol)
    if sym_info is None: return mt5.ORDER_FILLING_FOK
    if sym_info.filling_mode & 2: return mt5.ORDER_FILLING_IOC
    return mt5.ORDER_FILLING_FOK

def calculate_dynamic_stop_price(action, entry_price, target_loss_usd):
    o_type = mt5.ORDER_TYPE_BUY if action == "BUY" else mt5.ORDER_TYPE_SELL
    direction = -1.0 if action == "BUY" else 1.0
    sym_info = mt5.symbol_info(_symbol)
    if sym_info is None: return round(entry_price + (direction * 1.50), _digits)
    
    for points in range(1, 10000):
        test_sl = round(entry_price + (direction * points * sym_info.point), _digits)
        loss = mt5.order_calc_profit(o_type, _symbol, LOT_SIZE, entry_price, test_sl)
        if loss is not None and abs(loss) >= target_loss_usd:
            return test_sl
    return round(entry_price + (direction * 1.50), _digits)

def calc_rsi(closes, period=14):
    if len(closes) < period + 1: return 50.0
    gains = [closes[i] - closes[i-1] if closes[i] > closes[i-1] else 0 for i in range(1, period+1)]
    losses = [closes[i-1] - closes[i] if closes[i] < closes[i-1] else 0 for i in range(1, period+1)]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    for i in range(period + 1, len(closes)):
        change = closes[i] - closes[i-1]
        gain = change if change > 0 else 0.0
        loss = abs(change) if change < 0 else 0.0
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_loss == 0: return 100.0
    return 100.0 - (100.0 / (1.0 + (avg_gain / avg_loss)))

def calc_adx(highs, lows, closes, period=14):
    if len(closes) < period + 1: return 25.0
    plus_dm, minus_dm, tr = [], [], []
    for i in range(1, len(closes)):
        h_diff = highs[i] - highs[i-1]
        l_diff = lows[i-1] - lows[i]
        plus_dm.append(h_diff if h_diff > l_diff and h_diff > 0 else 0)
        minus_dm.append(l_diff if l_diff > h_diff and l_diff > 0 else 0)
        tr.append(max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1])))
    if sum(tr[-period:]) == 0: return 25.0
    plus_di = 100 * (sum(plus_dm[-period:]) / sum(tr[-period:]))
    minus_di = 100 * (sum(minus_dm[-period:]) / sum(tr[-period:]))
    dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di) if (plus_di + minus_di) > 0 else 0
    return dx

def extract_ml_features(tick_bid, tick_ask, spread, layer_num, action, regime):
    rates = mt5.copy_rates_from_pos(_symbol, mt5.TIMEFRAME_M1, 0, 100)
    htf_rates = mt5.copy_rates_from_pos(_symbol, mt5.TIMEFRAME_M15, 0, 10)
    if rates is None or htf_rates is None: return {}
    
    c = [r['close'] for r in rates[:-1]]
    h = [r['high'] for r in rates[:-1]]
    l = [r['low'] for r in rates[:-1]]
    
    ranges = [h[i] - l[i] for i in range(len(h))]
    atr_14 = sum(ranges[-14:]) / 14 if len(ranges) >= 14 else 1.0
    baseline_atr = sum(ranges[-50:]) / 50 if len(ranges) >= 50 else 1.0
    fast_atr = sum(ranges[-5:]) / 5 if len(ranges) >= 5 else 1.0
    
    a3, a7 = 2.0 / 4.0, 2.0 / 8.0
    ema3, ema7 = c[0], c[0]
    for p in c[1:]: 
        ema3 = (p - ema3) * a3 + ema3
        ema7 = (p - ema7) * a7 + ema7
        
    mean_20 = sum(c[-20:]) / 20
    std_20 = math.sqrt(sum((x - mean_20) ** 2 for x in c[-20:]) / 20)
    upper_bb, lower_bb = mean_20 + 2.0 * std_20, mean_20 - 2.0 * std_20
    bb_pos = (c[-1] - lower_bb) / (upper_bb - lower_bb) if (upper_bb - lower_bb) > 0 else 0.5
    
    trs = [max(h[i] - l[i], abs(h[i] - c[i-1]), abs(l[i] - c[i-1])) for i in range(-14, 0)]
    denom = max(h[-14:]) - min(l[-14:])
    ci = 100.0 * math.log10(sum(trs) / denom) / math.log10(14) if denom > 0 else 50.0
    
    y = c[-30:]
    n = 30
    x_mean = 14.5
    y_mean = sum(y) / n
    num = sum((i - x_mean) * (y[i] - y_mean) for i in range(n))
    den = sum((i - x_mean) ** 2 for i in range(n))
    slope = num / den if den != 0 else 0
    
    m15_c = [r['close'] for r in htf_rates]
    htf_trend = "BULLISH" if m15_c[-1] > sum(m15_c[-10:])/10 else "BEARISH"
    
    utc_hour = datetime.now(timezone.utc).hour
    if 1 <= utc_hour < 8: session = "ASIAN"
    elif 8 <= utc_hour < 13: session = "LONDON"
    elif 13 <= utc_hour < 20: session = "NEW_YORK"
    else: session = "DEAD_ZONE"
    
    return {
        "Strategy_Version": "v6.0_OMNI_MASTER",
        "Symbol": _symbol,
        "Timeframe": "M1",
        "Session": session,
        "HTF_Trend": htf_trend,
        "Regime": regime,
        "Layer_Number": layer_num,
        "Spread": round(spread, 2),
        "Bid": tick_bid,
        "Ask": tick_ask,
        "ATR_14": round(atr_14, 3),
        "Recent_Volatility_Ratio": round(fast_atr / max(baseline_atr, 0.01), 2),
        "RSI_14": round(calc_rsi(c), 2),
        "ADX_14": round(calc_adx(h, l, c), 2),
        "EMA_Separation": round(ema3 - ema7, 3),
        "BB_Position": round(bb_pos, 2),
        "Choppiness": round(ci, 2),
        "Slope_to_ATR": round(slope / atr_14, 4)
    }

def log_trade_to_ml_dataset(ticket, action, vol, entry_p, exit_p, pnl, mfe, mae, features):
    if not features: return
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    win_loss = "WIN" if pnl > 0 else "LOSS"
    
    header = ["Timestamp", "Bot_ID", "Ticket", "Action", "Volume", "Entry_Price", "Exit_Price", 
              "Final_Realized_PnL", "Win_Loss", "Max_Favorable_Excursion", "Max_Adverse_Excursion",
              "Strategy_Version", "Symbol", "Timeframe", "Session", "HTF_Trend", "Regime", "Layer_Number",
              "Spread", "Bid", "Ask", "ATR_14", "Recent_Volatility_Ratio", "RSI_14", "ADX_14", 
              "EMA_Separation", "BB_Position", "Choppiness", "Slope_to_ATR"]
              
    row = [now_str, "OMNI_BOT", ticket, action, vol, entry_p, exit_p, round(pnl, 2), win_loss, 
           round(mfe, 2), round(mae, 2), features["Strategy_Version"], features["Symbol"], features["Timeframe"], features["Session"], 
           features["HTF_Trend"], features["Regime"], features["Layer_Number"], features["Spread"], features["Bid"], features["Ask"], features["ATR_14"], 
           features["Recent_Volatility_Ratio"], features["RSI_14"], features["ADX_14"], features["EMA_Separation"], 
           features["BB_Position"], features["Choppiness"], features["Slope_to_ATR"]]
    
    try:
        file_exists = os.path.exists(CSV_LOG_FILE)
        with open(CSV_LOG_FILE, mode="a", newline="") as file:
            writer = csv.writer(file)
            if not file_exists: writer.writerow(header)
            writer.writerow(row)
    except PermissionError: pass

def check_virtual_vault():
    global peak_equity
    acc = mt5.account_info()
    if not acc: return
    curr_eq = acc.equity
    if curr_eq > peak_equity: peak_equity = curr_eq
        
    gross_profit = max(0.0, peak_equity - INITIAL_CAPITAL)
    locked_vault = gross_profit * PROFIT_LOCK_PCT
    hard_floor = (INITIAL_CAPITAL - MAX_RISK_ALLOWANCE) + locked_vault
    
    print(f"    Vault: ${locked_vault:.2f} | Floor: ${hard_floor:.2f} | Eq: ${curr_eq:.2f}    ", end="\r")
    if curr_eq < hard_floor:
        print(f"\n\n[!!!] 90/10 VAULT BREACHED (Equity < ${hard_floor:.2f}) [!!!]")
        close_all_positions("VAULT_BREACH_PANIC")
        mt5.shutdown()
        os._exit(1)

def get_market_regime_and_signal():
    rates = mt5.copy_rates_from_pos(_symbol, mt5.TIMEFRAME_M1, 0, 60)
    if rates is None or len(rates) < 60: return "UNKNOWN", None
    
    closed_rates = rates[:-1]
    closes = [r['close'] for r in closed_rates]
    highs = [r['high'] for r in closed_rates]
    lows = [r['low'] for r in closed_rates]
    ranges = [h - l for h, l in zip(highs, lows)]
    
    baseline_atr = sum(ranges[-50:]) / 50
    fast_atr = sum(ranges[-5:]) / 5
    if baseline_atr > 0 and (fast_atr / baseline_atr) > 2.5: return "SHOCK", None
    
    # Choppiness Index for Regime Filtering
    trs = [max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1])) for i in range(-14, 0)]
    denom = max(highs[-14:]) - min(lows[-14:])
    ci = 50.0
    if denom > 0: ci = 100.0 * math.log10(sum(trs) / denom) / math.log10(14)
    
    regime = "RANGE" if ci >= 50.0 else "TRENDING"
    
    if regime == "TRENDING":
        # Trend Logic: Linear Regression + EMA Momentum
        y = closes[-30:]
        n = 30
        x_mean = 14.5
        y_mean = sum(y) / n
        num = sum((i - x_mean) * (y[i] - y_mean) for i in range(n))
        den = sum((i - x_mean) ** 2 for i in range(n))
        slope = num / den if den != 0 else 0
        
        a3, a7 = 2.0 / 4.0, 2.0 / 8.0
        ema3, ema7 = closes[0], closes[0]
        for c in closes[-15:]: ema3, ema7 = (c - ema3) * a3 + ema3, (c - ema7) * a7 + ema7
            
        atr_14 = sum(ranges[-14:]) / 14
        normalized_slope = slope / atr_14 if atr_14 > 0 else 0
            
        if normalized_slope > 0.02 and ema3 > ema7: return regime, "BUY"
        if normalized_slope < -0.02 and ema3 < ema7: return regime, "SELL"

    elif regime == "RANGE":
        # Range Logic: Bollinger Band Sweep & Reclaim
        mean_20 = sum(closes[-20:]) / 20
        std_20 = math.sqrt(sum((x - mean_20) ** 2 for x in closes[-20:]) / 20)
        upper_bb, lower_bb = mean_20 + 2.0 * std_20, mean_20 - 2.0 * std_20
        
        prev, curr = closed_rates[-2], closed_rates[-1]['close']
        if prev['low'] < lower_bb and curr > lower_bb and curr > prev['close']: return regime, "BUY"
        if prev['high'] > upper_bb and curr < upper_bb and curr < prev['close']: return regime, "SELL"

    return regime, None

def bot_positions():
    pos = mt5.positions_get(symbol=_symbol)
    return [p for p in pos if p.magic == MAGIC_NUMBER] if pos else []

def verify_broker_closures():
    global last_known_positions, cooldown_until
    current_tickets = {p.ticket for p in bot_positions()}
    missing_tickets = last_known_positions - current_tickets
    
    if missing_tickets:
        for t in missing_tickets:
            import datetime
            deals = mt5.history_deals_get(datetime.datetime.now() - datetime.timedelta(days=1), datetime.datetime.now(), position=t)
            if deals:
                final_deal = deals[-1]
                if final_deal.profit < 0:
                    print(f"\n[-] Broker SL triggered on Ticket {t}. Entering 600s Cooldown.")
                    cooldown_until = time.time() + 600
    last_known_positions = current_tickets

def close_single_ticket(ticket, reason):
    pos = mt5.positions_get(ticket=ticket)
    if not pos: return
    p = pos[0]
    tick = mt5.symbol_info_tick(_symbol)
    c_type = mt5.ORDER_TYPE_SELL if p.type == mt5.POSITION_TYPE_BUY else mt5.ORDER_TYPE_BUY
    c_price = tick.bid if p.type == mt5.POSITION_TYPE_BUY else tick.ask
    
    req = {
        "action": mt5.TRADE_ACTION_DEAL, "symbol": _symbol, "volume": float(p.volume),
        "type": c_type, "position": p.ticket, "price": c_price, "deviation": 30,
        "magic": MAGIC_NUMBER, "comment": reason[:20], "type_time": mt5.ORDER_TIME_GTC, "type_filling": get_fill_mode(),
    }
    res = mt5.order_send(req)
    if res is not None and res.retcode == mt5.TRADE_RETCODE_DONE:
        if ticket in ticket_tracker:
            log_trade_to_ml_dataset(ticket, "CLOSE", p.volume, p.price_open, c_price, p.profit, 
                                    ticket_tracker[ticket]["mfe"], ticket_tracker[ticket]["mae"], ticket_tracker[ticket]["features"])
            del ticket_tracker[ticket]

def close_all_positions(reason):
    for p in bot_positions(): close_single_ticket(p.ticket, reason)

def execute_single_layer(action, layer_num, regime):
    tick = mt5.symbol_info_tick(_symbol)
    if tick is None: return False
    
    o_type = mt5.ORDER_TYPE_BUY if action == "BUY" else mt5.ORDER_TYPE_SELL
    price = tick.ask if action == "BUY" else tick.bid
    spread = tick.ask - tick.bid
    
    ml_feats = extract_ml_features(tick.bid, tick.ask, spread, layer_num, action, regime)
    sl_price = calculate_dynamic_stop_price(action, price, MAX_LOSS_PER_UNIT_USD)
    
    req = {
        "action": mt5.TRADE_ACTION_DEAL, "symbol": _symbol, "volume": float(LOT_SIZE),
        "type": o_type, "price": price, "sl": sl_price, "tp": 0.0, "deviation": 30,
        "magic": MAGIC_NUMBER, "comment": f"{regime[:3]}_L{layer_num}", "type_time": mt5.ORDER_TIME_GTC, "type_filling": get_fill_mode(),
    }
    
    chk = mt5.order_check(req)
    if chk is None or chk.retcode != 0: return False
        
    res = mt5.order_send(req)
    if res is not None and res.retcode == mt5.TRADE_RETCODE_DONE:
        ticket_tracker[res.order] = {"mfe": 0.0, "mae": 0.0, "features": ml_feats}
        print(f"\n[★] Executed {regime} Layer {layer_num} ({action}). SL: {sl_price:.{_digits}f}")
        return True
    return False

def update_stops(positions, direction, is_trail=False):
    tick = mt5.symbol_info_tick(_symbol)
    curr_price = tick.bid if direction == "BUY" else tick.ask
    all_success = True
    
    for p in positions:
        target_sl = 0.0
        is_modification_needed = False
        
        if p.profit >= BE_TRIGGER_USD and p.profit < TRAIL_TRIGGER_USD:
            target_sl = round(p.price_open + 0.15 if direction == "BUY" else p.price_open - 0.15, _digits)
            if (direction == "BUY" and (p.sl == 0 or target_sl > p.sl)) or (direction == "SELL" and (p.sl == 0 or target_sl < p.sl)):
                is_modification_needed = True
                
        elif p.profit >= TRAIL_TRIGGER_USD:
            target_sl = round(curr_price - TRAIL_DISTANCE_USD if direction == "BUY" else curr_price + TRAIL_DISTANCE_USD, _digits)
            if (direction == "BUY" and target_sl > p.price_open + 0.15 and (p.sl == 0 or target_sl > p.sl + 0.05)) or \
               (direction == "SELL" and target_sl < p.price_open - 0.15 and (p.sl == 0 or target_sl < p.sl - 0.05)):
                is_modification_needed = True

        if is_modification_needed:
            req = {"action": mt5.TRADE_ACTION_SLTP, "symbol": _symbol, "position": p.ticket, "sl": target_sl, "tp": 0.0, "magic": MAGIC_NUMBER, "comment": "Lock"}
            res = mt5.order_send(req)
            if res is None or (res.retcode != mt5.TRADE_RETCODE_DONE and res.retcode != 10025):
                all_success = False
    return all_success

def run_scalper():
    global cooldown_until, last_known_positions
    init_mt5()

    while True:
        time.sleep(POLL_INTERVAL_SECONDS)
        verify_broker_closures() 
        
        positions = bot_positions()
        num_layers = len(positions)
        curr_time = time.time()
        
        if num_layers > 0:
            total_pnl = sum(p.profit + p.swap for p in positions)
            direction = "BUY" if positions[0].type == mt5.POSITION_TYPE_BUY else "SELL"
            current_regime = ticket_tracker.get(positions[0].ticket, {}).get("features", {}).get("Regime", "UNKNOWN")
            
            layer_closed = False
            for p in positions:
                pnl = p.profit + p.swap
                if p.ticket in ticket_tracker:
                    if pnl > ticket_tracker[p.ticket]["mfe"]: ticket_tracker[p.ticket]["mfe"] = pnl
                    if pnl < ticket_tracker[p.ticket]["mae"]: ticket_tracker[p.ticket]["mae"] = pnl
                
                if pnl <= -MAX_LOSS_PER_UNIT_USD:
                    close_single_ticket(p.ticket, "UNIT_SL_HIT")
                    layer_closed = True
                    
            if layer_closed:
                if len(bot_positions()) == 0:
                    print(f"\n[-] Pyramid collapsed. Entering 10-minute cooldown.")
                    cooldown_until = curr_time + 600
                continue
            
            req_profit = num_layers * PYRAMID_ADD_THRESHOLD
            all_layers_green = all((p.profit + p.swap) > 0 for p in positions)
            
            print(f"    Layers: {num_layers} | PnL: ${total_pnl:.2f} | Mode: {current_regime}    ", end="\r")
            
            # --- TREND MODE: ANTI-MARTINGALE PYRAMIDING ---
            if current_regime == "TRENDING" and total_pnl >= req_profit and all_layers_green and num_layers < MAX_TREND_LAYERS:
                regime, action = get_market_regime_and_signal()
                if action == direction: 
                    print(f"\n[+] Trend Confirmed & Profit Target hit (+${total_pnl:.2f}). Securing older layers.")
                    if update_stops(positions, direction):
                        if execute_single_layer(direction, num_layers+1, regime): time.sleep(2.0) 
                    else:
                        print(f"    [-] Broker rejected BE lock. Aborting pyramid add.")
                        time.sleep(2.0)

            # --- RANGE MODE: TARGET CAPPING ---
            # Do NOT pyramid in ranges. Cash out at $1.50 to avoid getting trapped in chop.
            if current_regime == "RANGE" and total_pnl >= 1.50:
                print(f"\n[+] RANGE Target Reached. Securing Profit!")
                close_all_positions("RANGE_TARGET_HIT")
                continue

            update_stops(positions, direction, is_trail=True)
            continue 
            
        if curr_time < cooldown_until:
            rem = int(cooldown_until - curr_time)
            print(f"    [FLAT] Cooldown Active: {rem}s remaining...       ", end="\r")
            continue
            
        tick = mt5.symbol_info_tick(_symbol)
        if tick is None or (tick.ask - tick.bid) > MAX_ALLOWED_SPREAD: continue

        regime, action = get_market_regime_and_signal()
        print(f"    Omni-Bot Flat | Regime: {regime} | Analyzing entry...    ", end="\r")
        
        if action in ["BUY", "SELL"]: 
            if execute_single_layer(action, 1, regime): time.sleep(2.0) 

if __name__ == "__main__":
    try: run_scalper()
    except KeyboardInterrupt: mt5.shutdown()