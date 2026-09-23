import time
import os
import csv
from datetime import datetime, timezone
import MetaTrader5 as mt5

# ================= CONFIGURATION (XAUUSD) =================
SYMBOL = "XAUUSD"               # Use "XAUUSDm" if required by Exness
LOT_SIZE = 0.01                 
NUM_TICKETS = 2                 # Total Volume = 0.02 lots (2 oz Gold)
BASKET_TP_USD = 2.00            # Target: +$2.00
BASKET_SL_USD = 15.00           # Stop: -$15.00

# S/D & Engulfing Parameters
LOOKBACK_CANDLES = 120          # Search history for zones
ATR_PERIOD = 14                 
MOMENTUM_MULTIPLIER = 1.8       # Defines institutional displacement
MAX_ALLOWED_SPREAD = 0.45       # Max spread of 45 cents

POLL_INTERVAL_SECONDS = 1       
MAGIC_NUMBER = 770555           
LOG_FILE = "sd_engulf_logs.csv"
# ==========================================================

def init_environment():
    if not mt5.initialize():
        print(f"[-] MT5 Init failed: {mt5.last_error()}")
        quit()
        
    term = mt5.terminal_info()
    if term is None or not term.trade_allowed:
        print("\n[!] ERROR: Algo Trading is disabled in MT5. Press Ctrl + E to enable it.\n")
        quit()

    mt5.symbol_select(SYMBOL, True)
    account = mt5.account_info()
    print(f"[+] Connected | Account: {account.login} | Server: {account.server} | Equity: ${account.equity:.2f}")

    if not os.path.exists(LOG_FILE):
        with open(LOG_FILE, mode="w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "Timestamp", "Event", "Ticket", "Action", "Volume", 
                "Price", "Realized_PnL", "Floating_PnL", 
                "Zone_Top", "Zone_Bottom", "Exit_Reason"
            ])

def log_event(event, ticket, action, vol, price, realized_pnl, floating_pnl, z_top, z_bot, reason):
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, mode="a", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            now_str, event, ticket, action, vol, 
            round(price, 2), round(realized_pnl, 2), round(floating_pnl, 2), 
            round(z_top, 2) if z_top else "N/A", 
            round(z_bot, 2) if z_bot else "N/A", reason
        ])
        f.flush()
        os.fsync(f.fileno())

def scan_sd_and_engulfing(rates):
    """
    Finds fresh Supply/Demand zones in the past 120 candles.
    Checks if the exactly closed candles (index -3 and -2) formed an Engulfing pattern touching the zone.
    """
    # Calculate simple ATR without pandas for speed
    tr_list = []
    for i in range(1, len(rates)):
        tr0 = abs(rates[i]['high'] - rates[i]['low'])
        tr1 = abs(rates[i]['high'] - rates[i-1]['close'])
        tr2 = abs(rates[i]['low'] - rates[i-1]['close'])
        tr_list.append(max(tr0, tr1, tr2))
    
    current_atr = sum(tr_list[-ATR_PERIOD:]) / ATR_PERIOD

    demands, supplies = [], []
    
    # 1. Scan for Fresh Zones (Ignore the last 3 candles used for setup)
    for i in range(1, len(rates) - 3):
        c_base = rates[i-1]
        c_exp = rates[i]
        body = abs(c_exp['close'] - c_exp['open'])
        
        if body > current_atr * MOMENTUM_MULTIPLIER:
            if c_exp['close'] > c_exp['open']:  # Bullish Displacement -> Demand
                z_top = max(c_base['open'], c_base['close'])
                z_bot = c_base['low']
                
                # Freshness Check (Has price touched it before the engulfing setup?)
                is_fresh = True
                for j in range(i+1, len(rates) - 3):
                    if rates[j]['low'] <= z_top:
                        is_fresh = False
                        break
                if is_fresh: demands.append((z_top, z_bot))
                
            else:  # Bearish Displacement -> Supply
                z_bot = min(c_base['open'], c_base['close'])
                z_top = c_base['high']
                
                # Freshness Check
                is_fresh = True
                for j in range(i+1, len(rates) - 3):
                    if rates[j]['high'] >= z_bot:
                        is_fresh = False
                        break
                if is_fresh: supplies.append((z_top, z_bot))

    # 2. Check Engulfing Pattern on the immediately closed candles
    c1 = rates[-3] # First candle
    c2 = rates[-2] # Second candle (The Engulfing one)

    # Bullish Engulfing (Red candle followed by Green that engulfs real body)
    if c1['close'] < c1['open'] and c2['close'] > c2['open']:
        if c2['close'] >= c1['open'] and c2['open'] <= c1['close']:
            # Verify if it touches any fresh Demand Zone
            for z_top, z_bot in demands:
                if c2['low'] <= z_top and c2['close'] >= z_bot:
                    return "BUY", z_top, z_bot

    # Bearish Engulfing (Green candle followed by Red that engulfs real body)
    if c1['close'] > c1['open'] and c2['close'] < c2['open']:
        if c2['close'] <= c1['open'] and c2['open'] >= c1['close']:
            # Verify if it touches any fresh Supply Zone
            for z_top, z_bot in supplies:
                if c2['high'] >= z_bot and c2['close'] <= z_top:
                    return "SELL", z_top, z_bot

    return None, None, None

def close_all_bot_positions(reason):
    """Liquidates basket."""
    positions = mt5.positions_get(symbol=SYMBOL)
    bot_positions = [p for p in positions if p.magic == MAGIC_NUMBER] if positions else []
    if not bot_positions: return
    
    filling = mt5.symbol_info(SYMBOL).filling_mode
    filling_type = mt5.ORDER_FILLING_IOC if (filling & 2) else mt5.ORDER_FILLING_FOK

    for p in bot_positions:
        tick = mt5.symbol_info_tick(SYMBOL)
        c_type = mt5.ORDER_TYPE_SELL if p.type == mt5.POSITION_TYPE_BUY else mt5.ORDER_TYPE_BUY
        c_price = tick.bid if p.type == mt5.POSITION_TYPE_BUY else tick.ask
        
        req = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": SYMBOL,
            "volume": float(p.volume),
            "type": c_type,
            "position": p.ticket,
            "price": c_price,
            "deviation": 30,
            "magic": MAGIC_NUMBER,
            "comment": reason,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": filling_type,
        }
        mt5.order_send(req)

def fire_basket(action, zone_top, zone_bot):
    """Fires two 0.01 lot orders into the market."""
    tick = mt5.symbol_info_tick(SYMBOL)
    filling = mt5.symbol_info(SYMBOL).filling_mode
    filling_type = mt5.ORDER_FILLING_IOC if (filling & 2) else mt5.ORDER_FILLING_FOK
    
    o_type = mt5.ORDER_TYPE_BUY if action == "BUY" else mt5.ORDER_TYPE_SELL
    price = tick.ask if action == "BUY" else tick.bid
    
    print(f"\n[★] Engulfing Pattern in Fresh {action} Zone! Executing 2x {LOT_SIZE}...")
    
    for i in range(NUM_TICKETS):
        req = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": SYMBOL,
            "volume": float(LOT_SIZE),
            "type": o_type,
            "price": price,
            "sl": 0.0, 
            "tp": 0.0,
            "deviation": 30,
            "magic": MAGIC_NUMBER,
            "comment": f"Engulf_{action}_{i+1}",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": filling_type,
        }
        res = mt5.order_send(req)
        if res.retcode == mt5.TRADE_RETCODE_DONE:
            log_event("ENTRY", res.order, action, LOT_SIZE, res.price, 0.0, 0.0, zone_top, zone_bot, "Engulfing Trigger")

def run_engulf_engine():
    print(f"[*] S/D Engulfing Engine Active | Spread Guard: ${MAX_ALLOWED_SPREAD:.2f}")
    print(f"[*] Optimization: Heavy analysis runs only once per minute.\n")
    
    last_analyzed_minute = None

    while True:
        time.sleep(POLL_INTERVAL_SECONDS)
        
        positions = mt5.positions_get(symbol=SYMBOL)
        bot_pos = [p for p in positions if p.magic == MAGIC_NUMBER] if positions else []
        
        # 1. In-Flight Trade Management (Checked every 1 second)
        if len(bot_pos) > 0:
            total_floating = sum(p.profit for p in bot_pos)
            print(f"    In-Flight: {len(bot_pos)} Orders | Floating Basket PnL: ${total_floating:.2f}    ", end="\r")
            
            if total_floating >= BASKET_TP_USD:
                print(f"\n[+] Profit Target (+${total_floating:.2f}) Achieved. Liquidating...")
                close_all_bot_positions("TARGET_HIT")
                log_event("BASKET_EXIT", "ALL", "CLOSE", LOT_SIZE*NUM_TICKETS, 0.0, total_floating, 0.0, 0, 0, "Target $2.00")
            elif total_floating <= -BASKET_SL_USD:
                print(f"\n[-] Stop Loss Limit (-${total_floating:.2f}) Breached. Liquidating...")
                close_all_bot_positions("STOP_HIT")
                log_event("BASKET_EXIT", "ALL", "CLOSE", LOT_SIZE*NUM_TICKETS, 0.0, total_floating, 0.0, 0, 0, "Stop -$15.00")
                
            continue # Block scanning while holding trades
            
        # 2. Market Scanning (Runs ONLY exactly when a new minute starts)
        current_time = int(time.time())
        current_minute = current_time // 60
        
        if current_minute != last_analyzed_minute:
            rates = mt5.copy_rates_from_pos(SYMBOL, mt5.TIMEFRAME_M1, 0, LOOKBACK_CANDLES)
            if rates is not None and len(rates) >= ATR_PERIOD + 3:
                
                tick = mt5.symbol_info_tick(SYMBOL)
                spread = tick.ask - tick.bid
                
                if spread <= MAX_ALLOWED_SPREAD:
                    action, z_top, z_bot = scan_sd_and_engulfing(rates)
                    
                    print(f"    Scanning {SYMBOL} | Minute: {datetime.now().strftime('%H:%M')} | Spread: ${spread:.2f} | Status: Pending Setup...    ", end="\r")
                    
                    if action:
                        fire_basket(action, z_top, z_bot)
                        
                last_analyzed_minute = current_minute

if __name__ == "__main__":
    init_environment()
    try:
        run_engulf_engine()
    except KeyboardInterrupt:
        print("\n[!] Engine halted manually.")
    finally:
        mt5.shutdown()