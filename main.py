import os
import time
import json
import logging
import urllib.request
import urllib.parse
from datetime import datetime

import ccxt
import yfinance as yf
import pytz

# =========================================================
# LOGS
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)

log = logging.getLogger(__name__)

TZ = pytz.timezone("Europe/Madrid")

# =========================================================
# CONFIG
# =========================================================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

CAPITAL = 20
LEVERAGE = 3

MAX_TRADES = 3

SL_PCT = 0.015
TP1_PCT = 0.02
TP2_PCT = 0.04
TP3_PCT = 0.06

TRAIL_1 = 0.015
TRAIL_2 = 0.01
TRAIL_3 = 0.005

BE_TRIGGER = 0.003

STATE_FILE = "state.json"

WATCHLIST = [
    "NVDA",
    "AMD",
    "META",
    "TSLA",
    "GOOGL",
    "MSFT",
    "INTC",
    "AMZN",
    "AAPL",
    "COIN",
    "PLTR",
    "BABA",
    "MSTR",
    "MU",
    "ORCL",
    "ARM",
    "TSM",
    "CRWV",
    "OKLO",
    "GME",
    "HOOD",
    "APP",
    "RKLB",
    "IONQ",
    "SNDK",
    "NBIS",
    "NFLX",
    "AVGO",
    "MRVL",
    "AMAT",
    "KLAC",
    "WMT",
    "COST",
    "LLY",
    "XOM",
    "RDDT",
    "GE",
    "UNH",
    "COP",
]

# =========================================================
# BITGET
# =========================================================

exchange = ccxt.bitget({
    "apiKey": os.getenv("BITGET_API_KEY"),
    "secret": os.getenv("BITGET_API_SECRET"),
    "password": os.getenv("BITGET_API_PASSPHRASE"),
    "options": {
        "defaultType": "swap",
    }
})

# =========================================================
# STATE
# =========================================================

state = {
    "positions": [],
    "signals": {}
}

def load_state():
    global state

    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                state = json.load(f)
        except:
            pass

def save_state():
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

# =========================================================
# TELEGRAM
# =========================================================

def tg(msg):

    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return

    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

        data = urllib.parse.urlencode({
            "chat_id": TELEGRAM_CHAT_ID,
            "text": msg,
            "parse_mode": "Markdown"
        }).encode()

        urllib.request.urlopen(url, data=data, timeout=10)

    except Exception as e:
        log.error(f"telegram error {e}")

# =========================================================
# EMA
# =========================================================

def ema(values, period):

    k = 2 / (period + 1)

    out = [values[0]]

    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))

    return out

def get_signal(pair):

    try:

        candles = exchange.fetch_ohlcv(pair, "5m", limit=60)

        closes = [c[4] for c in candles]

        ema9 = ema(closes, 9)
        ema21 = ema(closes, 21)

        price = closes[-1]

        ema_fast = ema9[-1]
        ema_slow = ema21[-1]

        prev_fast = ema9[-2]
        prev_slow = ema21[-2]

        if prev_fast <= prev_slow and ema_fast > ema_slow:
            return "long"

        if prev_fast >= prev_slow and ema_fast < ema_slow:
            return "short"

        gap = abs(ema_fast - ema_slow) / ema_slow

        if gap < 0.001:
            return None

        if ema_fast > ema_slow and price > ema_fast:
            return "long"

        if ema_fast < ema_slow and price < ema_fast:
            return "short"

        return None

    except Exception as e:
        log.error(f"signal {pair} {e}")
        return None

# =========================================================
# ACCELERATION FILTER
# =========================================================

def acceleration_ok(pair, direction):

    try:

        candles = exchange.fetch_ohlcv(pair, "1m", limit=5)

        closes = [c[4] for c in candles]

        move = ((closes[-1] - closes[-3]) / closes[-3]) * 100

        if direction == "long":
            return move > 0.15

        return move < -0.15

    except:
        return False

# =========================================================
# YAHOO DATA
# =========================================================

def get_market_data(symbol):

    try:

        ticker = yf.Ticker(symbol)

        hist = ticker.history(period="2d", interval="1m")

        if hist.empty:
            return None

        price = float(hist["Close"].iloc[-1])

        prev_close = float(hist["Close"].iloc[-390])

        volume = int(hist["Volume"].sum())

        change = ((price - prev_close) / prev_close) * 100

        return {
            "price": price,
            "change": change,
            "volume": volume
        }

    except Exception as e:
        log.error(f"yahoo {symbol} {e}")
        return None

# =========================================================
# TRADE
# =========================================================

def is_active(pair):

    for p in state["positions"]:
        if p["pair"] == pair:
            return True

    return False

def get_active_count():
    return len(state["positions"])

def open_trade(symbol, direction, price):

    pair = f"{symbol}/USDT:USDT"

    if is_active(pair):
        return

    if get_active_count() >= MAX_TRADES:
        return

    side = "buy" if direction == "long" else "sell"

    size = round((CAPITAL * LEVERAGE) / price, 4)

    if size <= 0:
        return

    try:

        # =================================================
        # FORZAR AISLADO + X
        # =================================================

        exchange.set_leverage(
            LEVERAGE,
            pair,
            params={
                'marginMode': 'isolated',
                'productType': 'USDT-FUTURES',
            }
        )

        time.sleep(0.5)

        params = {
            'marginMode': 'isolated',
            'leverage': str(LEVERAGE),
            'reduceOnly': False,
        }

        exchange.create_order(
            pair,
            'market',
            side,
            size,
            None,
            params
        )

        # =================================================
        # LEVELS
        # =================================================

        if direction == "long":

            sl = price * (1 - SL_PCT)

            tp1 = price * (1 + TP1_PCT)
            tp2 = price * (1 + TP2_PCT)
            tp3 = price * (1 + TP3_PCT)

            trail = price * (1 - TRAIL_1)

        else:

            sl = price * (1 + SL_PCT)

            tp1 = price * (1 - TP1_PCT)
            tp2 = price * (1 - TP2_PCT)
            tp3 = price * (1 - TP3_PCT)

            trail = price * (1 + TRAIL_1)

        position = {
            "pair": pair,
            "symbol": symbol,
            "direction": direction,
            "entry": price,
            "size": size,
            "sl": sl,
            "tp1": tp1,
            "tp2": tp2,
            "tp3": tp3,
            "trail": trail,
            "best": price,
            "tp1_hit": False,
            "tp2_hit": False,
            "be": False
        }

        state["positions"].append(position)

        save_state()

        tg(
            f"🚀 *OPEN {direction.upper()}*\n\n"
            f"📈 {pair}\n"
            f"💰 Entry: {round(price,2)}\n"
            f"🛑 SL: {round(sl,2)}\n"
            f"🎯 TP1: {round(tp1,2)}\n"
            f"🎯 TP2: {round(tp2,2)}\n"
            f"🎯 TP3: {round(tp3,2)}\n"
            f"⚡ {CAPITAL} USDT x{LEVERAGE}"
        )

        log.info(f"OPEN {pair} {direction}")

    except Exception as e:

        log.error(f"open trade {pair} {e}")

        tg(f"❌ trade error {pair}\n{e}")

# =========================================================
# CLOSE
# =========================================================

def close_trade(pos, reason):

    try:

        side = "sell" if pos["direction"] == "long" else "buy"

        exchange.create_order(
            pos["pair"],
            "market",
            side,
            pos["size"],
            None,
            {
                'marginMode': 'isolated',
                'leverage': str(LEVERAGE),
                'reduceOnly': True,
            }
        )

    except Exception as e:

        log.error(f"close {e}")

    state["positions"] = [
        p for p in state["positions"]
        if p["pair"] != pos["pair"]
    ]

    save_state()

    tg(
        f"🏁 *CLOSE*\n\n"
        f"{pos['pair']}\n"
        f"📋 {reason}"
    )

# =========================================================
# MANAGE
# =========================================================

def get_trail(pnl):

    if pnl >= 0.03:
        return TRAIL_3

    if pnl >= 0.01:
        return TRAIL_2

    return TRAIL_1

def manage_trades():

    for pos in list(state["positions"]):

        try:

            price = exchange.fetch_ticker(pos["pair"])["last"]

            entry = pos["entry"]

            if pos["direction"] == "long":
                pnl = (price - entry) / entry
            else:
                pnl = (entry - price) / entry

            trail_pct = get_trail(pnl)

            # =============================================
            # BREAKEVEN
            # =============================================

            if not pos["be"] and pnl >= BE_TRIGGER:

                pos["be"] = True

                pos["trail"] = entry

                tg(f"📍 BE {pos['pair']}")

            # =============================================
            # TP1
            # =============================================

            if not pos["tp1_hit"]:

                if (
                    pos["direction"] == "long"
                    and price >= pos["tp1"]
                ) or (
                    pos["direction"] == "short"
                    and price <= pos["tp1"]
                ):

                    pos["tp1_hit"] = True

                    tg(f"🎯 TP1 {pos['pair']}")

            # =============================================
            # TP2
            # =============================================

            if not pos["tp2_hit"]:

                if (
                    pos["direction"] == "long"
                    and price >= pos["tp2"]
                ) or (
                    pos["direction"] == "short"
                    and price <= pos["tp2"]
                ):

                    pos["tp2_hit"] = True

                    tg(f"🎯 TP2 {pos['pair']}")

            # =============================================
            # TP3
            # =============================================

            if (
                pos["direction"] == "long"
                and price >= pos["tp3"]
            ) or (
                pos["direction"] == "short"
                and price <= pos["tp3"]
            ):

                close_trade(pos, "TP3")
                continue

            # =============================================
            # SL
            # =============================================

            if (
                pos["direction"] == "long"
                and price <= pos["sl"]
            ) or (
                pos["direction"] == "short"
                and price >= pos["sl"]
            ):

                close_trade(pos, "SL")
                continue

            # =============================================
            # TRAILING
            # =============================================

            if pos["direction"] == "long":

                if price > pos["best"]:

                    pos["best"] = price

                    pos["trail"] = price * (1 - trail_pct)

                if pos["tp1_hit"] and price <= pos["trail"]:

                    close_trade(pos, "TRAIL")
                    continue

            else:

                if price < pos["best"]:

                    pos["best"] = price

                    pos["trail"] = price * (1 + trail_pct)

                if pos["tp1_hit"] and price >= pos["trail"]:

                    close_trade(pos, "TRAIL")
                    continue

            save_state()

        except Exception as e:

            log.error(f"manage {e}")

# =========================================================
# SCAN
# =========================================================

def scan_market():

    for symbol in WATCHLIST:

        try:

            log.info(f"🔎 checking {symbol}")

            data = get_market_data(symbol)

            if not data:
                continue

            change = data["change"]
            volume = data["volume"]
            price = data["price"]

            # =============================================
            # FILTROS
            # =============================================

            if abs(change) < 2:
                continue

            if volume < 2_000_000:
                continue

            pair = f"{symbol}/USDT:USDT"

            signal = get_signal(pair)

            if not signal:
                continue

            log.info(f"EMA {symbol} -> {signal}")

            accel = acceleration_ok(pair, signal)

            if not accel:

                log.info(f"⛔ {pair} no acceleration")

                continue

            tg(
                f"🔥 *SETUP*\n\n"
                f"📈 {symbol}\n"
                f"📊 {round(change,2)}%\n"
                f"📦 Vol {volume}\n"
                f"⚡ {signal.upper()}"
            )

            open_trade(symbol, signal, price)

        except Exception as e:

            log.error(f"scan {symbol} {e}")

# =========================================================
# MAIN
# =========================================================

def main():

    load_state()

    tg(
        "🚀 *BOT STARTED*\n\n"
        "✅ EMA scanner\n"
        "✅ Dynamic trailing\n"
        "✅ Isolated forced\n"
        "✅ x3 forced\n"
        "✅ Yahoo real volume\n"
        "✅ Bitget execution"
    )

    log.info("🚀 BOT STARTED")

    last_scan = 0
    last_alive = 0

    while True:

        try:

            now = time.time()

            # =============================================
            # ALIVE
            # =============================================

            if now - last_alive > 90:

                last_alive = now

                log.info("💗 scanner alive")

            # =============================================
            # MANAGE
            # =============================================

            if state["positions"]:
                manage_trades()

            # =============================================
            # SCAN
            # =============================================

            if now - last_scan > 300:

                last_scan = now

                log.info("🔍 scanning market")

                scan_market()

            time.sleep(5)

        except Exception as e:

            log.error(f"main loop {e}")

            time.sleep(10)

# =========================================================
# START
# =========================================================

if __name__ == "__main__":
    main()