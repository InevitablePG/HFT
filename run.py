# live_trader_event_based_multi_symbol.py
import MetaTrader5 as mt5
import pandas as pd
import time

# === ACCOUNT LOGIN ===
login = 9935608
password = "Pipchaser110@"
server = "DerivBVI-Server"

# === USER CONFIG (MATCHING THE BACKTESTER) ===
# --- ADD OR REMOVE SYMBOLS TO TRADE IN THIS LIST ---
symbols_to_trade = [
    "EURUSD.0",
    "GBPUSD.0",
    "AUDUSD.0",
    "NZDUSD.0",
] 

risk_per_trade_usd = 1.0   # Risk $1.00 per trade
magic = 20250916         # Unique ID for this bot's trades
deviation = 20

# MA settings
fast_h1 = 5
slow_h1 = 9
fast_m1 = 9
slow_m1 = 21

# Risk Settings
sl_pips_from_m1_cross = 2.0 # Pips to place SL away from the M1 cross price
rr = 3.0                    # Risk:Reward ratio (1:3)

# === INITIALIZE MT5 ===
if not mt5.initialize():
    print("initialize() failed", mt5.last_error()); quit()
if not mt5.login(login, password, server):
    print("login() failed", mt5.last_error()); mt5.shutdown(); quit()
print("Logged in to", mt5.account_info().login)

# === Helpers ===
def get_rates(symbol, timeframe, count):
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, count)
    if rates is None or len(rates) == 0: return None
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    return df

def sma(series, period):
    return series.rolling(period).mean()

def has_open_position(symbol, magic_number):
    positions = mt5.positions_get(symbol=symbol)
    if positions is None: return False
    for pos in positions:
        if pos.magic == magic_number:
            return True
    return False

# --- MODIFIED to include fallback logic ---
def calculate_lot_size(symbol, risk_dollars, stop_pips):
    info = mt5.symbol_info(symbol)
    if not info: return None

    pip_value_per_lot = 10.0 # Standard for XXX/USD pairs
    
    risk_per_lot = stop_pips * pip_value_per_lot
    if risk_per_lot <= 0:
        lots = 0
    else:
        lots = risk_dollars / risk_per_lot
    
    # --- THIS IS THE NEW FALLBACK LOGIC ---
    # If calculated lots are too small, default to the minimum allowed lot size.
    if lots <= 0:
        lots = info.volume_min
        print(f"⚠️ Calculated lot size is <= 0 for {symbol}. Defaulting to minimum: {lots}. Risk will be higher than target.")

    lot_step = info.volume_step
    lots = round(lots / lot_step) * lot_step
    lots = max(info.volume_min, min(lots, info.volume_max))
    return lots

def place_order(symbol, signal, m1_cross_price):
    info = mt5.symbol_info(symbol)
    tick = mt5.symbol_info_tick(symbol)
    if not info or not tick:
        print(f"No tick/info for {symbol}"); return None

    pip_value = 0.0001 if info.digits == 5 else 0.01

    if signal == "buy":
        entry_price = tick.ask
        sl_price = m1_cross_price - (sl_pips_from_m1_cross * pip_value)
        if entry_price <= sl_price:
            print(f"Skipping BUY trade. Entry price ({entry_price}) is worse than SL ({sl_price})"); return None
        risk_distance = abs(entry_price - sl_price)
        tp_price = entry_price + rr * risk_distance
        order_type = mt5.ORDER_TYPE_BUY
    else: # signal == "sell"
        entry_price = tick.bid
        sl_price = m1_cross_price + (sl_pips_from_m1_cross * pip_value)
        if entry_price >= sl_price:
            print(f"Skipping SELL trade. Entry price ({entry_price}) is worse than SL ({sl_price})"); return None
        risk_distance = abs(sl_price - entry_price)
        tp_price = entry_price - rr * risk_distance
        order_type = mt5.ORDER_TYPE_SELL

    stop_distance_pips = risk_distance / pip_value
    lots = calculate_lot_size(symbol, risk_per_trade_usd, stop_distance_pips)
    if not lots:
        print(f"Lot size calculation failed for {symbol}"); return None

    request = {
        "action": mt5.TRADE_ACTION_DEAL, "symbol": symbol, "volume": lots,
        "type": order_type, "price": entry_price, "sl": round(sl_price, info.digits),
        "tp": round(tp_price, info.digits), "deviation": deviation, "magic": magic,
        "comment": "event_based_ma_bot", "type_filling": mt5.ORDER_FILLING_FOK,
        "type_time": mt5.ORDER_TIME_GTC,
    }

    result = mt5.order_send(request)
    print(f"[{signal.upper()}] {symbol} {lots} lot @ {entry_price} SL={sl_price} TP={tp_price}")
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        print(f"Order send failed, retcode={result.retcode}"); print(result)
    return result

# === Main Bot Loop ===
print("Bot running... Press Ctrl+C to stop.")
# --- NEW: A dictionary to track the last H1 cross for EACH symbol ---
last_h1_cross_times = {symbol: None for symbol in symbols_to_trade}

try:
    while True:
        # --- NEW: Loop through every symbol in your list ---
        for symbol in symbols_to_trade:
            # Check if a trade is already open by this bot for the current symbol
            if has_open_position(symbol, magic):
                continue # Skip to the next symbol

            # --- 1. Look for an H1 Crossover Event ---
            h1 = get_rates(symbol, mt5.TIMEFRAME_H1, slow_h1 + 2)
            if h1 is None or len(h1) < slow_h1 + 2:
                # print(f"Not enough H1 data for {symbol}, skipping.")
                continue

            h1['ma_fast'] = sma(h1['close'], fast_h1)
            h1['ma_slow'] = sma(h1['close'], slow_h1)

            prev_fast = h1['ma_fast'].iat[-2]; prev_slow = h1['ma_slow'].iat[-2]
            last_fast = h1['ma_fast'].iat[-1]; last_slow = h1['ma_slow'].iat[-1]

            h1_bias = None
            current_h1_cross_time = h1['time'].iat[-1]

            if (prev_fast < prev_slow) and (last_fast > last_slow): h1_bias = "buy"
            elif (prev_fast > prev_slow) and (last_fast < last_slow): h1_bias = "sell"

            if h1_bias:
                # Check if we have already traded this specific signal for this symbol
                if current_h1_cross_time == last_h1_cross_times[symbol]:
                    continue # Signal already used, skip to next symbol
                
                print(f"New H1 signal for {symbol} detected at {current_h1_cross_time}: {h1_bias.upper()}. Hunting for M1 entry...")

                m1 = get_rates(symbol, mt5.TIMEFRAME_M1, slow_m1 + 2)
                if m1 is None or len(m1) < slow_m1 + 2: continue
                
                m1['ma_fast'] = sma(m1['close'], fast_m1)
                m1['ma_slow'] = sma(m1['close'], slow_m1)

                m1_prev_fast = m1['ma_fast'].iat[-2]; m1_prev_slow = m1['ma_slow'].iat[-2]
                m1_last_fast = m1['ma_fast'].iat[-1]; m1_last_slow = m1['ma_slow'].iat[-1]
                
                m1_cross_type = None
                if (m1_prev_fast < m1_prev_slow) and (m1_last_fast > m1_last_slow): m1_cross_type = "buy"
                elif (m1_prev_fast > m1_prev_slow) and (m1_last_fast < m1_last_slow): m1_cross_type = "sell"

                if m1_cross_type and m1_cross_type == h1_bias:
                    print(f"M1 confirmation for {symbol} found! Placing {h1_bias.upper()} trade.")
                    m1_cross_price = m1['close'].iat[-1]
                    place_order(symbol, h1_bias, m1_cross_price)
                    
                    # Mark this H1 signal as "used" for this symbol
                    last_h1_cross_times[symbol] = current_h1_cross_time
        
        # Wait for 5 seconds AFTER checking all symbols
        time.sleep(5)

except KeyboardInterrupt:
    print("\nBot stopped by user.")
finally:
    mt5.shutdown()