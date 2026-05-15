# =========================================================
# INSTITUTIONAL GROWTH SCANNER v4
# Yahoo Scanner + Bitget Execution
# =========================================================

import os
import time
import json
import logging
import threading
import urllib.request
import urllib.parse

import ccxt
import yfinance as yf
import pytz

from datetime import datetime

# =========================================================
# LOGS
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s'
)

log = logging.getLogger(__name__)

TZ = pytz.timezone("Europe/Madrid")

# =========================================================
# CONFIG
# =========================================================

CAPITAL = 20
LEVERAGE = 3

MAX_TRADES = 3

SL_PCT = 0.015

TP1_PCT = 0.02
TP2_PCT = 0.04
TP3_PCT = 0.06

TRAIL_PCT = 0.015
BE_TRIGGER = 0.003

SCAN_INTERVAL = 90
MANAGE_INTERVAL = 10

STATE_FILE = "igs_state.json"

# =========================================================
# TELEGRAM
# =========================================================

TELEGRAM_TOKEN = os.environ.get(
    "TELEGRAM_TOKEN",
    ""
)

TELEGRAM_CHAT_ID = os.environ.get(
    "TELEGRAM_CHAT_ID",
    ""
)

# =========================================================
# BITGET
# =========================================================

exchange = ccxt.bitget({

    'apiKey': os.environ.get(
        'BITGET_API_KEY',
        ''
    ),

    'secret': os.environ.get(
        'BITGET_API_SECRET',
        ''
    ),

    'password': os.environ.get(
        'BITGET_API_PASSPHRASE',
        ''
    ),

    'timeout': 15000,

    'enableRateLimit': True,

    'options': {
        'defaultType': 'swap'
    }
})

# =========================================================
# TELEGRAM
# =========================================================

def tg(msg):

    if not TELEGRAM_TOKEN:
        return

    if not TELEGRAM_CHAT_ID:
        return

    try:

        url = (
            f"https://api.telegram.org/"
            f"bot{TELEGRAM_TOKEN}/sendMessage"
        )

        data = urllib.parse.urlencode({

            'chat_id': TELEGRAM_CHAT_ID,
            'text': msg,
            'parse_mode': 'Markdown'

        }).encode()

        urllib.request.urlopen(
            url,
            data=data,
            timeout=10
        )

    except Exception as e:

        log.error(f"telegram: {e}")

# =========================================================
# SYMBOLS
# =========================================================

USA_SYMBOL_MAP = {

    "NVDA": "NVDA/USDT:USDT",
    "AMD": "AMD/USDT:USDT",
    "META": "META/USDT:USDT",
    "TSLA": "TSLA/USDT:USDT",
    "GOOGL": "GOOGL/USDT:USDT",
    "MSFT": "MSFT/USDT:USDT",
    "INTC": "INTC/USDT:USDT",
    "AMZN": "AMZN/USDT:USDT",
    "AAPL": "AAPL/USDT:USDT",
    "COIN": "COIN/USDT:USDT",
    "PLTR": "PLTR/USDT:USDT",
    "BABA": "BABA/USDT:USDT",
    "MSTR": "MSTR/USDT:USDT",
    "MU": "MU/USDT:USDT",
    "ORCL": "ORCL/USDT:USDT",
    "ARM": "ARM/USDT:USDT",
    "TSM": "TSM/USDT:USDT",
    "CRWV": "CRWV/USDT:USDT",
    "OKLO": "OKLO/USDT:USDT",
    "GME": "GME/USDT:USDT",
    "HOOD": "HOOD/USDT:USDT",
    "APP": "APP/USDT:USDT",
    "RKLB": "RKLB/USDT:USDT",
    "IONQ": "IONQ/USDT:USDT",
    "SNDK": "SNDK/USDT:USDT",
    "NBIS": "NBIS/USDT:USDT",
    "NFLX": "NFLX/USDT:USDT",
    "AVGO": "AVGO/USDT:USDT",
    "MRVL": "MRVL/USDT:USDT",
}

WATCHLIST = list(
    USA_SYMBOL_MAP.keys()
)

# =========================================================
# STATE
# =========================================================

state = {

    "positions": [],

    "signals_sent": {},

    "last_reset": ""
}

# =========================================================
# STATE SAVE
# =========================================================

def load_state():

    global state

    if os.path.exists(STATE_FILE):

        try:

            with open(STATE_FILE) as f:

                saved = json.load(f)

                state.update(saved)

        except:
            pass

def save_state():

    try:

        with open(STATE_FILE, 'w') as f:

            json.dump(
                state,
                f,
                indent=2
            )

    except Exception as e:

        log.error(f"save_state: {e}")

# =========================================================
# HELPERS
# =========================================================

def can_signal(symbol):

    last = state["signals_sent"].get(
        symbol,
        0
    )

    return (
        time.time() - last
    ) > 3600

def mark_signal(symbol):

    state["signals_sent"][symbol] = time.time()

    save_state()

def active_count():

    return len(state["positions"])

def pair_active(pair):

    return any(
        p["pair"] == pair
        for p in state["positions"]
    )

# =========================================================
# EMA
# =========================================================

def calc_ema(values, period):

    k = 2 / (period + 1)

    ema = [values[0]]

    for v in values[1:]:

        ema.append(
            v * k + ema[-1] * (1 - k)
        )

    return ema

# =========================================================
# EMA CONFIRM
# =========================================================

def confirms_ema(pair, direction):

    try:

        candles = exchange.fetch_ohlcv(
            pair,
            '5m',
            limit=50
        )

        closes = [c[4] for c in candles]

        ema9 = calc_ema(closes, 9)

        ema21 = calc_ema(closes, 21)

        price = closes[-1]

        if direction == "alcista":

            result = (
                ema9[-1] > ema21[-1]
                and price > ema9[-1]
            )

        else:

            result = (
                ema9[-1] < ema21[-1]
                and price < ema9[-1]
            )

        log.info(
            f"EMA {pair} {direction} -> {result}"
        )

        return result

    except Exception as e:

        log.error(
            f"ema {pair}: {e}"
        )

        return False

# =========================================================
# ACCELERATION
# =========================================================

def has_acceleration(pair):

    try:

        candles = exchange.fetch_ohlcv(
            pair,
            '1m',
            limit=5
        )

        closes = [c[4] for c in candles]

        move1 = abs(
            closes[-1] - closes[-2]
        )

        move2 = abs(
            closes[-2] - closes[-3]
        )

        if move2 == 0:
            return False

        accel = move1 / move2

        ok = accel >= 1.1

        if not ok:

            log.info(
                f"⛔ {pair} no acceleration"
            )

        return ok

    except Exception as e:

        log.error(
            f"acceleration {pair}: {e}"
        )

        return False

# =========================================================
# OVEREXTENDED
# =========================================================

def is_overextended(pair):

    try:

        candles = exchange.fetch_ohlcv(
            pair,
            '5m',
            limit=20
        )

        highs = [
            c[2]
            for c in candles[:-1]
        ]

        current = candles[-1][4]

        max_high = max(highs)

        ext = (
            (current - max_high)
            / max_high
        )

        return ext > 0.025

    except:
        return False

# =========================================================
# YAHOO SCANNER
# =========================================================

def scan_market():

    movers = []

    for symbol in WATCHLIST:

        try:

            ticker = yf.Ticker(symbol)

            hist = ticker.history(
                period="1d",
                interval="1m",
                prepost=True
            )

            if hist.empty:
                continue

            price = float(
                hist["Close"].iloc[-1]
            )

            open_price = float(
                hist["Open"].iloc[0]
            )

            volume = float(
                hist["Volume"].sum()
            )

            if open_price == 0:
                continue

            change = (
                (price - open_price)
                / open_price
            ) * 100

            if abs(change) < 3:
                continue

            if volume < 5_000_000:
                continue

            movers.append({

                "symbol": symbol,

                "price": round(price, 2),

                "change": round(change, 2),

                "volume": volume,
            })

            log.info(
                f"🔥 {symbol} "
                f"{round(change,2)}% "
                f"VOL {round(volume)}"
            )

        except Exception as e:

            log.error(
                f"scan {symbol}: {e}"
            )

    movers.sort(
        key=lambda x: abs(x["change"]),
        reverse=True
    )

    return movers[:5]

# =========================================================
# OPEN TRADE
# =========================================================

def open_trade(symbol, direction, price):

    pair = USA_SYMBOL_MAP.get(symbol)

    if not pair:
        return

    if pair_active(pair):
        return

    if active_count() >= MAX_TRADES:
        return

    if is_overextended(pair):

        log.info(
            f"⛔ {pair} overextended"
        )

        return

    if not has_acceleration(pair):
        return

    side = (
        'buy'
        if direction == "alcista"
        else 'sell'
    )

    size = round(
        (CAPITAL * LEVERAGE) / price,
        4
    )

    try:

        # =====================================
        # NO TOCAR
        # =====================================

        exchange.set_leverage(

            LEVERAGE,

            pair,

            params={
                'marginMode': 'isolated',
                'productType': 'USDT-FUTURES',
            }
        )

        exchange.create_order(

            pair,
            'market',
            side,
            size,
            None,

            {
                'marginMode': 'isolated',
                'leverage': str(LEVERAGE),
                'reduceOnly': False,
            }
        )

        # =====================================

        sl = (
            price * (1 - SL_PCT)
            if direction == "alcista"
            else price * (1 + SL_PCT)
        )

        tp1 = (
            price * (1 + TP1_PCT)
            if direction == "alcista"
            else price * (1 - TP1_PCT)
        )

        tp2 = (
            price * (1 + TP2_PCT)
            if direction == "alcista"
            else price * (1 - TP2_PCT)
        )

        tp3 = (
            price * (1 + TP3_PCT)
            if direction == "alcista"
            else price * (1 - TP3_PCT)
        )

        pos = {

            "pair": pair,
            "symbol": symbol,
            "side": direction,
            "entry": price,
            "size": size,

            "sl": sl,

            "tp1": tp1,
            "tp2": tp2,
            "tp3": tp3,

            "trail_best": price,

            "trail_sl": (
                price * (1 - TRAIL_PCT)
                if direction == "alcista"
                else price * (1 + TRAIL_PCT)
            ),

            "tp1_hit": False,
            "be": False,
        }

        state["positions"].append(pos)

        save_state()

        tg(
            f"🚀 TRADE {direction.upper()}\n\n"
            f"{pair}\n"
            f"📈 Entry {price}\n"
            f"🛑 SL {round(sl,2)}\n"
            f"🎯 TP1 {round(tp1,2)}\n"
            f"🎯 TP2 {round(tp2,2)}\n"
            f"🎯 TP3 {round(tp3,2)}"
        )

        log.info(f"🚀 OPEN {pair}")

    except Exception as e:

        log.error(
            f"open trade {pair}: {e}"
        )

# =========================================================
# CLOSE TRADE
# =========================================================

def close_trade(pos, reason):

    side = (
        'sell'
        if pos["side"] == "alcista"
        else 'buy'
    )

    try:

        exchange.create_order(

            pos["pair"],
            'market',
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

        log.error(
            f"close trade: {e}"
        )

    state["positions"] = [

        p

        for p in state["positions"]

        if p["pair"] != pos["pair"]
    ]

    save_state()

    tg(
        f"🏁 CLOSE {pos['pair']}\n"
        f"{reason}"
    )

# =========================================================
# MANAGE TRADES
# =========================================================

def manage_trades():

    while True:

        try:

            for pos in list(state["positions"]):

                try:

                    ticker = exchange.fetch_ticker(
                        pos["pair"]
                    )

                    price = float(
                        ticker['last']
                    )

                    entry = pos["entry"]

                    side = pos["side"]

                    pnl = (

                        ((price - entry) / entry)

                        if side == "alcista"

                        else (

                            (entry - price) / entry
                        )
                    )

                    log.info(
                        f"💰 {pos['pair']} "
                        f"{round(pnl*100,2)}%"
                    )

                    # SL

                    if side == "alcista":

                        if price <= pos["sl"]:

                            close_trade(
                                pos,
                                "SL"
                            )

                            continue

                    else:

                        if price >= pos["sl"]:

                            close_trade(
                                pos,
                                "SL"
                            )

                            continue

                    # TP1

                    if not pos["tp1_hit"]:

                        if (

                            side == "alcista"
                            and price >= pos["tp1"]

                        ) or (

                            side == "bajista"
                            and price <= pos["tp1"]

                        ):

                            pos["tp1_hit"] = True

                            pos["trail_sl"] = entry

                            tg(
                                f"🎯 TP1 "
                                f"{pos['pair']}"
                            )

                    # TP3

                    if (

                        side == "alcista"
                        and price >= pos["tp3"]

                    ) or (

                        side == "bajista"
                        and price <= pos["tp3"]

                    ):

                        close_trade(
                            pos,
                            "TP3"
                        )

                        continue

                    # TRAILING

                    if side == "alcista":

                        if price > pos["trail_best"]:

                            pos["trail_best"] = price

                            pos["trail_sl"] = (
                                price * (1 - TRAIL_PCT)
                            )

                        if (

                            pos["tp1_hit"]

                            and

                            price <= pos["trail_sl"]
                        ):

                            close_trade(
                                pos,
                                "TRAIL"
                            )

                            continue

                    else:

                        if price < pos["trail_best"]:

                            pos["trail_best"] = price

                            pos["trail_sl"] = (
                                price * (1 + TRAIL_PCT)
                            )

                        if (

                            pos["tp1_hit"]

                            and

                            price >= pos["trail_sl"]
                        ):

                            close_trade(
                                pos,
                                "TRAIL"
                            )

                            continue

                    save_state()

                except Exception as e:

                    log.error(
                        f"manage pos: {e}"
                    )

            time.sleep(MANAGE_INTERVAL)

        except Exception as e:

            log.error(
                f"manage loop: {e}"
            )

            time.sleep(5)

# =========================================================
# SCANNER LOOP
# =========================================================

def scanner_loop():

    while True:

        try:

            log.info("💓 scanner alive")

            movers = scan_market()

            for item in movers:

                symbol = item["symbol"]

                price = item["price"]

                change = item["change"]

                if not can_signal(symbol):
                    continue

                direction = (
                    "alcista"
                    if change > 0
                    else "bajista"
                )

                pair = USA_SYMBOL_MAP[symbol]

                log.info(
                    f"🔍 {symbol} {change}%"
                )

                if not confirms_ema(
                    pair,
                    direction
                ):
                    continue

                log.info(
                    f"✅ setup {symbol}"
                )

                tg(
                    f"⚡ SIGNAL {symbol}\n"
                    f"{direction.upper()} "
                    f"{change}%"
                )

                mark_signal(symbol)

                open_trade(
                    symbol,
                    direction,
                    price
                )

            time.sleep(SCAN_INTERVAL)

        except Exception as e:

            log.error(
                f"scanner loop: {e}"
            )

            time.sleep(10)

# =========================================================
# MAIN
# =========================================================

def main():

    load_state()

    tg(
        "🚀 Institutional Growth Scanner v4"
    )

    threading.Thread(
        target=manage_trades,
        daemon=True
    ).start()

    scanner_loop()

# =========================================================
# START
# =========================================================

if __name__ == "__main__":

    main()