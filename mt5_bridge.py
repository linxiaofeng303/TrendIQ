"""Small, read-only HTTP bridge from a logged-in MetaTrader 5 terminal to TrendIQ.

Run this on the same Windows machine as MT5. It never places or modifies trades.
"""

from contextlib import contextmanager
import hmac
import os

from flask import Flask, jsonify, request
import MetaTrader5 as mt5

app = Flask(__name__)

TIMEFRAMES = {
    "M1": mt5.TIMEFRAME_M1,
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1,
    "H4": mt5.TIMEFRAME_H4,
    "D1": mt5.TIMEFRAME_D1,
    "W1": mt5.TIMEFRAME_W1,
    "MN1": mt5.TIMEFRAME_MN1,
}


def authorized():
    expected = os.getenv("MT5_BRIDGE_TOKEN", "")
    supplied = request.headers.get("Authorization", "")
    return bool(expected) and hmac.compare_digest(supplied, f"Bearer {expected}")


@app.before_request
def require_bearer_token():
    if not authorized():
        return jsonify({"error": "Unauthorized"}), 401


@contextmanager
def connected_terminal():
    if not mt5.initialize():
        raise RuntimeError(f"Could not connect to MT5 terminal: {mt5.last_error()}")
    try:
        terminal = mt5.terminal_info()
        if terminal is None or not terminal.connected:
            raise RuntimeError("MT5 is open but not connected to a broker server.")
        if mt5.account_info() is None:
            raise RuntimeError("Log in to your XM MT5 account before starting the bridge.")
        yield
    finally:
        mt5.shutdown()


def select_symbol(symbol):
    info = mt5.symbol_info(symbol)
    if info is None:
        raise LookupError(f"MT5 does not list the symbol '{symbol}'.")
    if not info.visible and not mt5.symbol_select(symbol, True):
        raise RuntimeError(f"Could not enable MT5 symbol '{symbol}'.")


@app.get("/api/history")
def history():
    symbol = request.args.get("symbol", os.getenv("MT5_SYMBOL", "GOLD")).strip()
    timeframe_name = request.args.get("timeframe", "D1").upper()
    try:
        count = max(40, min(int(request.args.get("count", "1000")), 2000))
    except ValueError:
        return jsonify({"error": "count must be a whole number"}), 400

    timeframe = TIMEFRAMES.get(timeframe_name)
    if not symbol or timeframe is None:
        return jsonify({"error": "A valid symbol and timeframe are required."}), 400

    try:
        with connected_terminal():
            select_symbol(symbol)
            rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, count)
            if rates is None or len(rates) == 0:
                raise RuntimeError(
                    f"MT5 returned no {timeframe_name} candles for {symbol}. "
                    "Open its chart in MT5 once to load history, then retry."
                )
            result = [
                {
                    "time": int(row["time"]),
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "tick_volume": int(row["tick_volume"]),
                }
                for row in rates
            ]
        return jsonify({"symbol": symbol, "timeframe": timeframe_name, "rates": result})
    except LookupError as exc:
        return jsonify({"error": str(exc)}), 404
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 503


@app.get("/api/quote")
def quote():
    symbol = request.args.get("symbol", os.getenv("MT5_SYMBOL", "GOLD")).strip()
    if not symbol:
        return jsonify({"error": "symbol is required"}), 400

    try:
        with connected_terminal():
            select_symbol(symbol)
            tick = mt5.symbol_info_tick(symbol)
            if tick is None:
                raise RuntimeError(f"MT5 returned no current tick for {symbol}.")

            price = float(tick.bid or tick.last or 0)
            if price <= 0 and tick.ask:
                price = float(tick.ask)
            if price <= 0:
                raise RuntimeError(f"MT5 returned an invalid price for {symbol}.")

            previous = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_D1, 1, 1)
            previous_close = float(previous[0]["close"]) if previous is not None and len(previous) else price
            change = price - previous_close
            change_percent = (change / previous_close) * 100 if previous_close else 0.0
            result = {
                "symbol": symbol,
                "price": price,
                "bid": float(tick.bid),
                "ask": float(tick.ask),
                "change": change,
                "changePercent": change_percent,
                "time": int(tick.time),
            }
        return jsonify(result)
    except LookupError as exc:
        return jsonify({"error": str(exc)}), 404
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 503


if __name__ == "__main__":
    if not os.getenv("MT5_BRIDGE_TOKEN", "").strip():
        raise SystemExit("Set MT5_BRIDGE_TOKEN before starting the MT5 bridge.")
    app.run(host="127.0.0.1", port=8765, debug=False, threaded=False)
