import time
import os
import csv
import math
import sys
from datetime import datetime, timezone
import MetaTrader5 as mt5

sys.path.insert(0, r"E:\tradingggg\quant_lab_tools")
from bot_ml_support import capture_features, log_closed_trade, start_background_research

# ================= CONFIGURATION (BOT 1 - 4-LAYER TREND) =================
SYMBOL = "XAUUSD"               
LOT_SIZE = 0.01                 
MAX_LAYERS = 4                  # Upgraded for exponential trend capture
MAX_LOSS_PER_UNIT_USD = 3.00    # Restored breathing room for Gold
PYRAMID_ADD_THRESHOLD = 1.20    # Wait for +$1.20 to guarantee older stops can lock safely

# Smart Trailing Profile
BE_TRIGGER_USD        = 0.85    
TRAIL_TRIGGER_USD     = 2.50    
TRAIL_DISTANCE_USD    = 1.50    

INITIAL_CAPITAL = 20.00         
MAX_RISK_ALLOWANCE = 15.00      
PROFIT_LOCK_PCT = 0.90          
MAX_ALLOWED_SPREAD = 0.40       
POLL_INTERVAL_SECONDS = 0.2     
MAGIC_NUMBER = 880555           

TERMINAL_PATH = r"C:\Program Files\MetaTrader 5\terminal64.exe"
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

def init_mt5():
    global _symbol
    if not mt5.initialize(path=TERMINAL_PATH):
        print(f"[-] MT5 Init failed: {mt5.last_error()}")
        os._exit(1)
    
    account = mt5.account_info()
    if account is None:
        mt5.shutdown()
        os._exit(1)
        
    print(f"[ACCOUNT] Bot 1 connected | Login: {account.login} | Equity: ${account.equity:.2f}")
    for sym in (SYMBOL, "XAUUSDm", "GOLD", "GOLDm"):
        if mt5.symbol_info(sym) is not None:
            mt5.symbol_select(sym, True)
            _symbol = sym
            break
    print(f"[+] Bot 1: 4-LAYER ANTI-MARTINGALE | Max Unit Risk: ${MAX_LOSS_PER_UNIT_USD:.2f}")

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
    ranges = [r['high'] - r['low'] for r in closed_rates]
    
    baseline_atr = sum(ranges[-50:]) / 50
    fast_atr = sum(ranges[-5:]) / 5
    if baseline_atr > 0 and (fast_atr / baseline_atr) > 2.5: return "SHOCK", None
    
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
        
    if normalized_slope > 0.02 and ema3 > ema7: return "TREND_UP", "BUY"
    if normalized_slope < -0.02 and ema3 < ema7: return "TREND_DOWN", "SELL"
    return "WEAK", None

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
                tracked = ticket_tracker.pop(t, None)
                if tracked:
                    log_closed_trade(CSV_LOG_FILE, "BOT_1", t, tracked["volume"], tracked["entry_price"], final_deal.price, final_deal.profit, tracked["mfe"], tracked["mae"], "BROKER_CLOSE", tracked["features"])
                if final_deal.profit < 0:
                    print(f"\n[-] Broker Stop Loss executed on Ticket {t}. Entering 600s Cooldown.")
                    cooldown_until = time.time() + 600
    last_known_positions = current_tickets

def execute_single_layer(action, layer_num):
    tick = mt5.symbol_info_tick(_symbol)
    if tick is None: return False
    
    o_type = mt5.ORDER_TYPE_BUY if action == "BUY" else mt5.ORDER_TYPE_SELL
    price = tick.ask if action == "BUY" else tick.bid
    sl_price = calculate_dynamic_stop_price(action, price, MAX_LOSS_PER_UNIT_USD)
    
    req = {
        "action": mt5.TRADE_ACTION_DEAL, "symbol": _symbol, "volume": float(LOT_SIZE),
        "type": o_type, "price": price, "sl": sl_price, "tp": 0.0, "deviation": 30,
        "magic": MAGIC_NUMBER, "comment": f"L{layer_num}", "type_time": mt5.ORDER_TIME_GTC, "type_filling": get_fill_mode(),
    }
    
    chk = mt5.order_check(req)
    if chk is None or chk.retcode != 0: return False
        
    features = capture_features(mt5, _symbol, tick.bid, tick.ask, layer_num, "v5.8_Trend")
    res = mt5.order_send(req)
    if res is not None and res.retcode == mt5.TRADE_RETCODE_DONE:
        position_id = getattr(res, "position", 0) or res.order
        ticket_tracker[position_id] = {"mfe": 0.0, "mae": 0.0, "volume": float(LOT_SIZE), "entry_price": price, "features": features}
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
            
            # The Critical "No Subsidizing Losers" Check
            all_layers_green = all((p.profit + p.swap) > 0 for p in positions)
            req_profit = num_layers * PYRAMID_ADD_THRESHOLD
            
            print(f"    Layers: {num_layers}/{MAX_LAYERS} | PnL: ${total_pnl:.2f} | Next Add: ${req_profit:.2f} | All Green: {all_layers_green}  ", end="\r")
            
            # Strict Pyramiding: Add only when previous layers are deeply profitable
            if total_pnl >= req_profit and all_layers_green and num_layers < MAX_LAYERS:
                regime, action = get_market_regime_and_signal()
                
                if action == direction: 
                    print(f"\n[+] Profit Target hit. Securing older layers to Break-Even.")
                    stops_secured = True
                    for p in positions:
                        be_sl = p.price_open + 0.15 if direction == "BUY" else p.price_open - 0.15
                        if (direction == "BUY" and (p.sl == 0 or p.sl < be_sl)) or (direction == "SELL" and (p.sl == 0 or p.sl > be_sl)):
                            res = mt5.order_send({"action": mt5.TRADE_ACTION_SLTP, "symbol": _symbol, "position": p.ticket, "sl": round(be_sl, 2), "tp": 0.0, "magic": MAGIC_NUMBER, "comment": "BE_Lock"})
                            if res is None or res.retcode != mt5.TRADE_RETCODE_DONE: stops_secured = False
                    
                    # Core Logic: Only risk layer 2 if layer 1 is physically locked in profit
                    if stops_secured:
                        if execute_single_layer(direction, num_layers+1): time.sleep(2.0) 
                    else:
                        print(f"    [-] Broker rejected BE lock. Aborting pyramid add to protect capital.")

            # Verified Trailing
            tick = mt5.symbol_info_tick(_symbol)
            curr_price = tick.bid if direction == "BUY" else tick.ask
            for p in positions:
                pnl = p.profit + p.swap
                if p.ticket in ticket_tracker:
                    ticket_tracker[p.ticket]["mfe"] = max(ticket_tracker[p.ticket]["mfe"], pnl)
                    ticket_tracker[p.ticket]["mae"] = min(ticket_tracker[p.ticket]["mae"], pnl)
                if p.profit >= BE_TRIGGER_USD and p.profit < TRAIL_TRIGGER_USD:
                    be_sl = p.price_open + 0.15 if direction == "BUY" else p.price_open - 0.15
                    if (direction == "BUY" and (p.sl == 0 or be_sl > p.sl)) or (direction == "SELL" and (p.sl == 0 or be_sl < p.sl)):
                        res = mt5.order_send({"action": mt5.TRADE_ACTION_SLTP, "symbol": _symbol, "position": p.ticket, "sl": round(be_sl, 2), "tp": 0.0, "magic": MAGIC_NUMBER, "comment": "BE_Lock"})
                
                elif p.profit >= TRAIL_TRIGGER_USD:
                    trail_sl = curr_price - TRAIL_DISTANCE_USD if direction == "BUY" else curr_price + TRAIL_DISTANCE_USD
                    if (direction == "BUY" and trail_sl > p.price_open + 0.15 and (p.sl == 0 or trail_sl > p.sl + 0.10)) or \
                       (direction == "SELL" and trail_sl < p.price_open - 0.15 and (p.sl == 0 or trail_sl < p.sl - 0.10)):
                        res = mt5.order_send({"action": mt5.TRADE_ACTION_SLTP, "symbol": _symbol, "position": p.ticket, "sl": round(trail_sl, 2), "tp": 0.0, "magic": MAGIC_NUMBER, "comment": "Trail"})
            continue 
            
        if curr_time < cooldown_until:
            rem = int(cooldown_until - curr_time)
            print(f"    [FLAT] Broken Trend Cooldown: {rem}s remaining...       ", end="\r")
            continue
            
        tick = mt5.symbol_info_tick(_symbol)
        if tick is None or (tick.ask - tick.bid) > MAX_ALLOWED_SPREAD: continue

        regime, action = get_market_regime_and_signal()
        print(f"    Bot 1 Flat | Regime: {regime} | Analyzing exact entry...    ", end="\r")
        
        if action in ["BUY", "SELL"]: 
            if execute_single_layer(action, 1): time.sleep(2.0) 

if __name__ == "__main__":
    start_background_research()
    try: run_scalper()
    except KeyboardInterrupt: mt5.shutdown()