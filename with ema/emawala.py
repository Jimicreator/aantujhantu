import time
import os
import csv
import math
import sys
from datetime import datetime, timezone
import MetaTrader5 as mt5

sys.path.insert(0, r"E:\tradingggg\quant_lab_tools")
from bot_ml_support import capture_features, log_closed_trade, start_background_research

# ================= CONFIGURATION (PROFILE B: CONTROLLED 2-LAYER) =================
SYMBOL = "XAUUSD"               
LOT_SIZE = 0.01                 
MAX_LAYERS = 2                  
MAX_BASKET_RISK_USD = 3.00      
UNIT_RISK_USD = MAX_BASKET_RISK_USD / MAX_LAYERS 

PYRAMID_ADD_THRESHOLD = 1.00    
BE_TRIGGER_USD        = 0.75    
TRAIL_TRIGGER_USD     = 2.50    
TRAIL_DISTANCE_USD    = 1.50    

INITIAL_CAPITAL = 20.00         
MAX_RISK_ALLOWANCE = 15.00      
PROFIT_LOCK_PCT = 0.90          

MAX_ALLOWED_SPREAD = 0.40       
POLL_INTERVAL_SECONDS = 0.2     
MAGIC_NUMBER = 880777           

TERMINAL_PATH = r"C:\Program Files\MetaTrader 5 - Account 2\terminal64.exe"
LAB_DIR = r"C:\Shared_Quant_Lab"
LOG_DIR = os.path.join(LAB_DIR, "Logs")
os.makedirs(LOG_DIR, exist_ok=True)
CSV_LOG_FILE = os.path.join(LOG_DIR, "Master_ML_Log.csv")
# =========================================================================

_symbol = "XAUUSD"
cooldown_until = 0.0
last_known_positions = set()
ticket_tracker = {}

def init_mt5():
    global _symbol
    if not mt5.initialize(path=TERMINAL_PATH):
        print(f"[-] MT5 Init failed: {mt5.last_error()}")
        os._exit(1)
    
    account = mt5.account_info()
    if account is None:
        mt5.shutdown()
        os._exit(1)
        
    print(f"[ACCOUNT] Bot 2 connected | Login: {account.login} | Equity: ${account.equity:.2f}")
    for sym in (SYMBOL, "XAUUSDm", "GOLD", "GOLDm"):
        if mt5.symbol_info(sym) is not None:
            mt5.symbol_select(sym, True)
            _symbol = sym
            break
    print(f"[+] Bot 2: MULTI-REGIME ENGINE | Strict Execution Rules Applied")

def get_fill_mode():
    sym_info = mt5.symbol_info(_symbol)
    if sym_info is None: return mt5.ORDER_FILLING_FOK
    if sym_info.filling_mode & 2: return mt5.ORDER_FILLING_IOC
    return mt5.ORDER_FILLING_FOK

def calculate_dynamic_stop_price(action, entry_price, target_loss_usd):
    o_type = mt5.ORDER_TYPE_BUY if action == "BUY" else mt5.ORDER_TYPE_SELL
    direction = -1.0 if action == "BUY" else 1.0
    sym_info = mt5.symbol_info(_symbol)
    if sym_info is None: return entry_price + (direction * 1.50) 
    
    for points in range(1, 10000):
        test_sl = round(entry_price + (direction * points * sym_info.point), sym_info.digits)
        loss = mt5.order_calc_profit(o_type, _symbol, LOT_SIZE, entry_price, test_sl)
        if loss is not None and abs(loss) >= target_loss_usd:
            return test_sl
    return entry_price + (direction * 1.50)

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
    
    mean_20 = sum(closes[-20:]) / 20
    std_20 = math.sqrt(sum((x - mean_20) ** 2 for x in closes[-20:]) / 20)
    upper_bb, lower_bb = mean_20 + 2.0 * std_20, mean_20 - 2.0 * std_20
    
    trs = [max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1])) for i in range(-14, 0)]
    denom = max(highs[-14:]) - min(lows[-14:])
    ci = 50.0
    if denom > 0: ci = 100.0 * math.log10(sum(trs) / denom) / math.log10(14)
    
    regime = "RANGE" if ci >= 50.0 else "TRENDING"
    
    if regime == "TRENDING":
        a3, a7 = 2.0 / 4.0, 2.0 / 8.0
        ema3, ema7 = closes[0], closes[0]
        for c in closes[-10:]: ema3, ema7 = (c - ema3) * a3 + ema3, (c - ema7) * a7 + ema7
        prev, curr = closed_rates[-2], closed_rates[-1]['close']
        if ema3 > ema7 and prev['close'] < prev['open'] and curr > prev['high']: return regime, "BUY"
        if ema3 < ema7 and prev['close'] > prev['open'] and curr < prev['low']: return regime, "SELL"

    elif regime == "RANGE":
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
                    print(f"\n[-] Broker Stop Loss executed on Ticket {t}. Entering 600s Cooldown.")
                    cooldown_until = time.time() + 600
                    
    last_known_positions = current_tickets

def execute_single_layer(action, layer_num):
    tick = mt5.symbol_info_tick(_symbol)
    if tick is None: return False
    
    o_type = mt5.ORDER_TYPE_BUY if action == "BUY" else mt5.ORDER_TYPE_SELL
    price = tick.ask if action == "BUY" else tick.bid
    sl_price = calculate_dynamic_stop_price(action, price, UNIT_RISK_USD)
    
    req = {
        "action": mt5.TRADE_ACTION_DEAL, "symbol": _symbol, "volume": float(LOT_SIZE),
        "type": o_type, "price": price, "sl": sl_price, "tp": 0.0, "deviation": 30,
        "magic": MAGIC_NUMBER, "comment": f"L{layer_num}", "type_time": mt5.ORDER_TIME_GTC, "type_filling": get_fill_mode(),
    }
    
    chk = mt5.order_check(req)
    if chk is None or chk.retcode != 0: return False
        
    res = mt5.order_send(req)
    if res is not None and res.retcode == mt5.TRADE_RETCODE_DONE:
        print(f"\n[★] Executed Layer {layer_num} ({action}). Broker-Verified SL: {sl_price:.2f}")
        return True
    return False

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
            all_layers_green = all((p.profit + p.swap) > 0 for p in positions)
            req_profit = num_layers * PYRAMID_ADD_THRESHOLD
            
            print(f"    Layers: {num_layers} | PnL: ${total_pnl:.2f} | Next Add: ${req_profit:.2f} | All Green: {all_layers_green}  ", end="\r")
            
            if total_pnl >= req_profit and all_layers_green and num_layers < MAX_LAYERS:
                regime, action = get_market_regime_and_signal()
                
                # Bot 2 rule: Only pyramid in TRENDING regimes
                if action == direction and regime == "TRENDING": 
                    print(f"\n[+] Profit Target hit. Securing older layers.")
                    stops_secured = True
                    for p in positions:
                        be_sl = p.price_open + 0.15 if direction == "BUY" else p.price_open - 0.15
                        if (direction == "BUY" and (p.sl == 0 or p.sl < be_sl)) or (direction == "SELL" and (p.sl == 0 or p.sl > be_sl)):
                            res = mt5.order_send({"action": mt5.TRADE_ACTION_SLTP, "symbol": _symbol, "position": p.ticket, "sl": round(be_sl, 2), "tp": 0.0, "magic": MAGIC_NUMBER, "comment": "BE_Lock"})
                            if res is None or res.retcode != mt5.TRADE_RETCODE_DONE: stops_secured = False
                    
                    if stops_secured:
                        if execute_single_layer(direction, num_layers+1): time.sleep(2.0) 

            # Verified Trailing
            tick = mt5.symbol_info_tick(_symbol)
            curr_price = tick.bid if direction == "BUY" else tick.ask
            for p in positions:
                if p.profit >= BE_TRIGGER_USD and p.profit < TRAIL_TRIGGER_USD:
                    be_sl = p.price_open + 0.15 if direction == "BUY" else p.price_open - 0.15
                    if (direction == "BUY" and (p.sl == 0 or be_sl > p.sl)) or (direction == "SELL" and (p.sl == 0 or be_sl < p.sl)):
                        mt5.order_send({"action": mt5.TRADE_ACTION_SLTP, "symbol": _symbol, "position": p.ticket, "sl": round(be_sl, 2), "tp": 0.0, "magic": MAGIC_NUMBER, "comment": "BE_Lock"})
                
                elif p.profit >= TRAIL_TRIGGER_USD:
                    trail_sl = curr_price - TRAIL_DISTANCE_USD if direction == "BUY" else curr_price + TRAIL_DISTANCE_USD
                    if (direction == "BUY" and trail_sl > p.price_open + 0.15 and (p.sl == 0 or trail_sl > p.sl + 0.10)) or \
                       (direction == "SELL" and trail_sl < p.price_open - 0.15 and (p.sl == 0 or trail_sl < p.sl - 0.10)):
                        mt5.order_send({"action": mt5.TRADE_ACTION_SLTP, "symbol": _symbol, "position": p.ticket, "sl": round(trail_sl, 2), "tp": 0.0, "magic": MAGIC_NUMBER, "comment": "Trail"})
            continue 
            
        if curr_time < cooldown_until:
            rem = int(cooldown_until - curr_time)
            print(f"    [FLAT] Cooldown Active: {rem}s remaining...       ", end="\r")
            continue
            
        tick = mt5.symbol_info_tick(_symbol)
        if tick is None or (tick.ask - tick.bid) > MAX_ALLOWED_SPREAD: continue

        regime, action = get_market_regime_and_signal()
        print(f"    Bot 2 Flat | Regime: {regime} | Analyzing exact entry...    ", end="\r")
        
        if action in ["BUY", "SELL"]: 
            if execute_single_layer(action, 1): time.sleep(2.0) 

if __name__ == "__main__":
    try: run_scalper()
    except KeyboardInterrupt: mt5.shutdown()