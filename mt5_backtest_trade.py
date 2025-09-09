# boom_crash_live_bot_no_sl.py
import MetaTrader5 as mt5
import pandas as pd
import time
from datetime import datetime, timedelta

import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)

# === ACCOUNT (DEMO) ===
login = 140406934
password = "Pipchaser110@"
server = "DerivBVI-Server-03"

# === STRATEGY SETTINGS ===
symbols = [
    "Boom 1000 Index",
    "Crash 600 Index"
]

# per-symbol lot sizes
lot_sizes = {
    "Boom 1000 Index": 0.30,
    "Crash 600 Index": 0.25,
}

fast_ma = 9
slow_ma = 21
timeframe_m1 = mt5.TIMEFRAME_M1
timeframe_h1 = mt5.TIMEFRAME_H1
pip_value = 0.001            # Boom/Crash tick step (0.001)
hold_minutes = 2            # hold for 2 minutes (2 M1 candles)
deviation = 20              # order deviation tolerance
magic = 20250906
order_comment = "BoomCrash_MA_bot_noSL"

sl_offset = 2.0 

# Logging trades
trade_log = []

# === INITIALIZE MT5 ===
if not mt5.initialize():
    print("mt5.initialize() failed:", mt5.last_error())
    raise SystemExit

if not mt5.login(login, password, server):
    print("mt5.login() failed:", mt5.last_error())
    mt5.shutdown()
    raise SystemExit

print("Logged in. Account:", mt5.account_info().login)

# === Helpers ===
def get_recent_bars(symbol, timeframe, count):
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, count)
    if rates is None:
        return None
    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s')
    return df.sort_values('time').reset_index(drop=True)

def simple_ma(values, period):
    if len(values) < period:
        return None
    return sum(values[-period:]) / period

def has_open_position(symbol):
    pos = mt5.positions_get(symbol=symbol)
    return (pos is not None and len(pos) > 0)


# def place_market_order(symbol, signal, lot):
#     tick = mt5.symbol_info_tick(symbol)
#     if tick is None:
#         print(f"[{symbol}] no tick info")
#         return None

#     if signal == "buy":
#         price = tick.ask
#         order_type = mt5.ORDER_TYPE_BUY
#         sl = price - 1.0
#     else:
#         price = tick.bid
#         order_type = mt5.ORDER_TYPE_SELL
#         sl = price + 1.0

#     request = {
#         "action": mt5.TRADE_ACTION_DEAL,
#         "symbol": symbol,
#         "volume": float(lot),
#         "type": order_type,
#         "price": float(price),
#         "sl": sl,
#         "deviation": deviation,
#         "magic": magic,
#         "comment": order_comment,
#         "type_filling": mt5.ORDER_FILLING_FOK,
#         "type_time": mt5.ORDER_TIME_GTC,
#     }

#     result = mt5.order_send(request)
#     return result


def place_market_order(symbol, signal, lot, sl_offset=2.0):
    tick = mt5.symbol_info_tick(symbol)
    info = mt5.symbol_info(symbol)
    if tick is None or info is None:
        print(f"[{symbol}] no tick/symbol info")
        return None

    # broker minimum stop-loss distance in price terms
    min_sl_distance = info.trade_stops_level * info.point
    digits = info.digits

    if signal == "buy":
        price = tick.ask
        order_type = mt5.ORDER_TYPE_BUY
        sl = round(price - (min_sl_distance + sl_offset), digits)
    else:
        price = tick.bid
        order_type = mt5.ORDER_TYPE_SELL
        sl = round(price + (min_sl_distance + sl_offset), digits)

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": float(lot),
        "type": order_type,
        "price": float(round(price, digits)),
        "sl": sl,
        "deviation": deviation,
        "magic": magic,
        "comment": order_comment,
        "type_filling": mt5.ORDER_FILLING_FOK,  # fill-or-kill
        "type_time": mt5.ORDER_TIME_GTC,
    }

    print(f"[{symbol}] {signal.upper()} {lot} @ {price} | SL={sl} "
          f"(min dist={min_sl_distance}, offset={sl_offset})")
    result = mt5.order_send(request)
    return result


def close_positions_for_symbol(symbol):
    positions = mt5.positions_get(symbol=symbol)
    if not positions:
        return None
    results = []
    for pos in positions:
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            continue
        if pos.type == mt5.POSITION_TYPE_BUY:
            close_type = mt5.ORDER_TYPE_SELL
            price = tick.bid
        else:
            close_type = mt5.ORDER_TYPE_BUY
            price = tick.ask

        close_req = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": float(pos.volume),
            "type": close_type,
            "position": int(pos.ticket),
            "price": float(price),
            "deviation": deviation,
            "magic": magic,
            "comment": "Close by bot",
            "type_filling": mt5.ORDER_FILLING_FOK,  # hardcoded FOK
            "type_time": mt5.ORDER_TIME_GTC,
        }
        res = mt5.order_send(close_req)
        results.append(res)
    return results


def record_trade(symbol, signal, entry_price, exit_price, open_time, close_time):
    # result_pips in Boom/Crash pip units (0.01)
    if signal == "sell":
        result_pips = (entry_price - exit_price) / pip_value
    else:
        result_pips = (exit_price - entry_price) / pip_value
    trade = {
        "symbol": symbol,
        "signal": signal,
        "entry_price": round(entry_price, 2),
        "exit_price": round(exit_price, 2),
        "open_time": open_time,
        "close_time": close_time,
        "result_pips": round(result_pips, 2),
    }
    trade_log.append(trade)
    print(trade)

# === Main loop ===
print("Bot running — monitoring symbols):", symbols)
try:
    while True:
        for symbol in symbols:
            try:
                # ensure symbol exists on terminal
                info = mt5.symbol_info(symbol)
                if info is None:
                    print(f"[{symbol}] symbol not found in market watch — skipping")
                    continue

                # if there's an open position, check holding time and close when time reached
                if has_open_position(symbol):
                    positions = mt5.positions_get(symbol=symbol)
                    for pos in positions:
                        open_time = datetime.utcfromtimestamp(pos.time)
                        if datetime.utcnow() >= open_time + timedelta(minutes=hold_minutes):
                            # close the position now
                            tick = mt5.symbol_info_tick(symbol)
                            if tick:
                                if pos.type == mt5.POSITION_TYPE_BUY:
                                    exit_price = tick.bid
                                else:
                                    exit_price = tick.ask
                                close_positions_for_symbol(symbol)
                                signal = "buy" if pos.type == mt5.POSITION_TYPE_BUY else "sell"
                                record_trade(symbol, signal, float(pos.price_open), float(exit_price),
                                             open_time, datetime.utcnow())
                    # do not open a new trade if one exists
                    continue

                # load recent HTF and M1 bars
                htf = get_recent_bars(symbol, timeframe_h1, 4)   # last 4 H1 bars
                m1 = get_recent_bars(symbol, timeframe_m1, slow_ma + 5)  # enough bars for MA21

                if htf is None or m1 is None or len(htf) < 3 or len(m1) < slow_ma + 2:
                    continue

                # HTF filter: last TWO H1 candles
                last2_htf = htf.tail(2)
                if symbol.lower().startswith("boom"):
                    if not all(last2_htf['close'] < last2_htf['open']):
                        continue
                elif symbol.lower().startswith("crash"):
                    if not all(last2_htf['close'] > last2_htf['open']):
                        continue

                # compute simple moving avgs on M1 closes
                closes = list(m1['close'].values)
                prev_fast = simple_ma(closes[:-1], fast_ma)
                prev_slow = simple_ma(closes[:-1], slow_ma)
                last_fast = simple_ma(closes, fast_ma)
                last_slow = simple_ma(closes, slow_ma)
                if prev_fast is None or prev_slow is None or last_fast is None or last_slow is None:
                    continue

                signal = None
                if symbol.lower().startswith("boom") and prev_fast > prev_slow and last_fast < last_slow:
                    signal = "sell"
                elif symbol.lower().startswith("crash") and prev_fast < prev_slow and last_fast > last_slow:
                    signal = "buy"

                if signal:
                    lot = lot_sizes.get(symbol, 0.20)
                    tick = mt5.symbol_info_tick(symbol)
                    if tick is None:
                        continue

                    # entry price (market)
                    entry_price = tick.ask if signal == "buy" else tick.bid

                    # place order WITH SL
                    res = place_market_order(symbol, signal, lot, sl_offset)
                    if res is None:
                        print(f"[{symbol}] order_send returned None")
                        continue

                    # check result
                    if hasattr(res, 'retcode') and res.retcode != mt5.TRADE_RETCODE_DONE and res.retcode != 10009:
                        print(f"[{symbol}] order_send response: {res}")
                    else:
                        open_time = datetime.utcnow()
                        print(f"[{symbol}] Placed {signal.upper()} order at {entry_price} lot={lot} time={open_time}")

                        # wait up to hold_minutes while monitoring if broker closed (rare without SL)
                        closed_early = False
                        wait_deadline = open_time + timedelta(minutes=hold_minutes)
                        while datetime.utcnow() < wait_deadline:
                            time.sleep(1.0)
                            # if broker closed the position (rare), detect and log
                            if not has_open_position(symbol):
                                tick2 = mt5.symbol_info_tick(symbol)
                                if tick2:
                                    exit_price = tick2.bid if signal == "buy" else tick2.ask
                                else:
                                    exit_price = entry_price
                                record_trade(symbol, signal, entry_price, exit_price, open_time, datetime.utcnow())
                                closed_early = True
                                break

                        if not closed_early:
                            # time's up: close the position if still open
                            if has_open_position(symbol):
                                tick3 = mt5.symbol_info_tick(symbol)
                                if tick3:
                                    exit_price = tick3.bid if signal == "buy" else tick3.ask
                                else:
                                    exit_price = entry_price
                                close_positions_for_symbol(symbol)
                                record_trade(symbol, signal, entry_price, exit_price, open_time, datetime.utcnow())
                            else:
                                # already closed earlier and recorded
                                pass

            except Exception as e:
                print("Error on symbol loop:", symbol, e)

        time.sleep(1.0)

except KeyboardInterrupt:
    print("Stopped by user (KeyboardInterrupt)")

finally:
    if trade_log:
        df = pd.DataFrame(trade_log)
        print("\n=== Trade Log ===")
        pd.set_option("display.max_rows", None)
        pd.set_option("display.max_columns", None)
        print(df)
        df.to_csv("boom_crash_live_trades_noSL.csv", index=False)
        print("Saved trade log to boom_crash_live_trades_noSL.csv")
    else:
        print("No trades were recorded during this session.")
    mt5.shutdown()
