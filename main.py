# ============================================
# INSTITUTIONAL GROWTH SCANNER v2
# ============================================

import os
import time
import json
import logging
import threading
import urllib.request
import urllib.parse
import ccxt
import finnhub
import yfinance as yf
import requests
import feedparser
import pytz

from datetime import datetime, timedelta

# ============================================
# LOGGING
# ============================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s'
)

log = logging.getLogger(__name__)

TZ = pytz.timezone("Europe/Madrid")

# ============================================
# CONFIG
# ============================================

TELEGRAM_TOKEN = os.environ.get(
    'TELEGRAM_TOKEN',
    ''
)

TELEGRAM_CHAT_ID = os.environ.get(
    'TELEGRAM_CHAT_ID',
    ''
)

FINNHUB_API_KEY = os.environ.get(
    'FINNHUB_API_KEY',
    ''
)

CAPITAL = 20
LEVERAGE = 3

SL_PCT = 0.015

TP1_PCT = 0.02
TP2_PCT = 0.04
TP3_PCT = 0.06

TRAIL_PCT = 0.015

BE_TRIGGER = 0.003

EMA_FAST = 9
EMA_SLOW = 21
EMA_MIN_GAP = 0.001

MAX_TRADES = 3

STATE_FILE = 'igs_state.json'

# ============================================
# CLIENTS
# ============================================

finnhub_client = finnhub.Client(
    api_key=FINNHUB_API_KEY
)

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
    'options': {
        'defaultType': 'swap'
    }
})

# ============================================
# TELEGRAM
# ============================================

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

        log.error(f"Telegram: {e}")

# ============================================
# SYMBOLS
# ============================================

USA_STOCKS = [

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
    "SOUN",
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

USA_SYMBOL_MAP = {

    s: f"{s}/USDT:USDT"

    for s in [

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
        "SOUN",
        "SNDK",
        "NBIS",
        "NFLX",
        "AVGO",
        "MRVL",
        "GME",
        "HOOD",
        "CRWV",
        "RKLB",
        "IONQ",
        "OKLO",
        "APP",
    ]
}

# ============================================
# STATE
# ============================================

state = {
    "positions": [],
    "signals_sent": {},
    "daily_signals": [],
    "last_reset": "",
}

# ============================================
# LOAD SAVE
# ============================================

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

# ============================================
# RESET
# ============================================

def reset_daily():

    today = datetime.now(TZ).strftime(
        "%Y-%m-%d"
    )

    if state["last_reset"] != today:

        state["signals_sent"] = {}

        state["daily_signals"] = []

        state["last_reset"] = today

        save_state()

        log.info("🔄 Reset diario")

# ============================================
# HELPERS
# ============================================

def get_active_count():

    return len(state["positions"])

def is_pair_active(pair):

    return any(
        p["pair"] == pair
        for p in state["positions"]
    )

def can_send_signal(symbol):

    last = state["signals_sent"].get(
        symbol,
        0
    )

    return (
        time.time() - last
    ) >= 4 * 3600

def mark_signal_sent(symbol):

    state["signals_sent"][symbol] = time.time()

    save_state()

# ============================================
# EMA / RSI
# ============================================

def calc_ema(values, period):

    k = 2 / (period + 1)

    ema = [values[0]]

    for v in values[1:]:

        ema.append(
            v * k + ema[-1] * (1 - k)
        )

    return ema

def calc_rsi(closes, period=14):

    if len(closes) < period + 1:
        return 50

    diffs = [

        closes[i] - closes[i - 1]

        for i in range(
            1,
            len(closes)
        )
    ]

    gains = [max(d, 0) for d in diffs]

    losses = [max(-d, 0) for d in diffs]

    ag = sum(gains[-period:]) / period

    al = sum(losses[-period:]) / period

    if al == 0:
        return 100

    rs = ag / al

    return 100 - (100 / (1 + rs))

# ============================================
# SIGNALS
# ============================================

def get_ema_signals(candles):

    if len(candles) < EMA_SLOW + 5:
        return None

    closes = [c[4] for c in candles]

    ema_fast = calc_ema(
        closes,
        EMA_FAST
    )

    ema_slow = calc_ema(
        closes,
        EMA_SLOW
    )

    rsi = calc_rsi(closes[-30:])

    ef_now = ema_fast[-1]
    ef_prev = ema_fast[-2]

    es_now = ema_slow[-1]
    es_prev = ema_slow[-2]

    ema_gap = abs(
        ef_now - es_now
    ) / es_now

    return {

        "cross_long":
            ef_prev <= es_prev
            and ef_now > es_now,

        "cross_short":
            ef_prev >= es_prev
            and ef_now < es_now,

        "aligned_long":
            ef_now > es_now,

        "aligned_short":
            ef_now < es_now,

        "ema_gap":
            ema_gap,

        "rsi":
            rsi
    }

# ============================================
# EMA CONFIRM
# ============================================

def confirms_ema(symbol, direction):

    pair = USA_SYMBOL_MAP.get(symbol)

    if not pair:
        return False

    try:

        candles_1h = exchange.fetch_ohlcv(
            pair,
            '1h',
            limit=100
        )

        sig_1h = get_ema_signals(
            candles_1h
        )

        candles_15m = exchange.fetch_ohlcv(
            pair,
            '15m',
            limit=50
        )

        sig_15m = get_ema_signals(
            candles_15m
        )

        if not sig_1h:
            return False

        if not sig_15m:
            return False

        # LONG

        if direction == 'alcista':

            ok_1h = (

                (
                    sig_1h['cross_long']
                    or sig_1h['aligned_long']
                )

                and sig_1h['ema_gap']
                >= EMA_MIN_GAP

                and 40 <= sig_1h['rsi'] <= 70
            )

            ok_15m = (

                sig_15m['aligned_long']

                and sig_15m['ema_gap']
                >= EMA_MIN_GAP

                and sig_15m['rsi'] > 50
            )

        # SHORT

        else:

            ok_1h = (

                (
                    sig_1h['cross_short']
                    or sig_1h['aligned_short']
                )

                and sig_1h['ema_gap']
                >= EMA_MIN_GAP

                and 30 <= sig_1h['rsi'] <= 60
            )

            ok_15m = (

                sig_15m['aligned_short']

                and sig_15m['ema_gap']
                >= EMA_MIN_GAP

                and sig_15m['rsi'] < 50
            )

        result = ok_1h and ok_15m

        log.info(
            f"EMA {symbol} "
            f"{direction} "
            f"-> {result}"
        )

        return result

    except Exception as e:

        log.error(
            f"confirms_ema {symbol}: {e}"
        )

        return False

# ============================================
# FOMO FILTER
# ============================================

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

        extension = (
            (current - max_high)
            / max_high
        )

        return extension > 0.025

    except Exception as e:

        log.error(
            f"is_overextended {pair}: {e}"
        )

        return False

# ============================================
# ACCELERATION
# ============================================

def has_acceleration(pair):

    try:

        candles = exchange.fetch_ohlcv(
            pair,
            '5m',
            limit=12
        )

        closes = [c[4] for c in candles]

        recent = (
            (closes[-1] - closes[-3])
            / closes[-3]
        )

        previous = (
            (closes[-4] - closes[-8])
            / closes[-8]
        )

        return (
            recent > previous * 1.5
            and recent > 0.01
        )

    except Exception as e:

        log.error(
            f"has_acceleration {pair}: {e}"
        )

        return False

# ============================================
# FINNHUB
# ============================================

def fetch_finnhub_data(symbols):

    data = []

    for symbol in symbols:

        try:

            quote = finnhub_client.quote(
                symbol
            )

            if not quote:
                continue

            price = quote["c"]

            prev = quote["pc"]

            volume = quote.get(
                "v",
                0
            )

            if prev == 0:
                continue

            change = round(
                (
                    (price - prev)
                    / prev
                ) * 100,
                2
            )

            data.append({

                "symbol": symbol,
                "name": symbol,
                "price": price,
                "change": change,
                "volume": volume,
            })

        except Exception as e:

            log.error(
                f"finnhub {symbol}: {e}"
            )

    return data

# ============================================
# ANALYZE
# ============================================

def analyze_signal(item):

    symbol = item["symbol"]

    price = item["price"]

    change = item["change"]

    direction = (
        "alcista"
        if change > 0
        else "bajista"
    )

    if direction == "alcista":

        sl = round(
            price * (1 - SL_PCT),
            2
        )

        tp1 = round(
            price * (1 + TP1_PCT),
            2
        )

        tp2 = round(
            price * (1 + TP2_PCT),
            2
        )

        tp3 = round(
            price * (1 + TP3_PCT),
            2
        )

    else:

        sl = round(
            price * (1 + SL_PCT),
            2
        )

        tp1 = round(
            price * (1 - TP1_PCT),
            2
        )

        tp2 = round(
            price * (1 - TP2_PCT),
            2
        )

        tp3 = round(
            price * (1 - TP3_PCT),
            2
        )

    return {

        "symbol": symbol,

        "name": symbol,

        "price": price,

        "change": change,

        "direction": direction,

        "sl": sl,

        "tp1": tp1,

        "tp2": tp2,

        "tp3": tp3,
    }

# ============================================
# TRAILING
# ============================================

def get_trail_pct(pnl):

    if pnl >= 0.03:
        return 0.005

    elif pnl >= 0.01:
        return 0.01

    return 0.015

# ============================================
# OPEN TRADE
# ============================================

def open_trade(analysis):

    symbol = analysis["symbol"]

    pair = USA_SYMBOL_MAP.get(symbol)

    if not pair:
        return

    if is_pair_active(pair):
        return

    if get_active_count() >= MAX_TRADES:
        return

    # ========================================
    # FILTERS
    # ========================================

    if is_overextended(pair):

        log.info(
            f"⛔ {pair} overextended"
        )

        return

    if not has_acceleration(pair):

        log.info(
            f"⛔ {pair} no acceleration"
        )

        return

    # ========================================

    price = analysis["price"]

    direction = analysis["direction"]

    side = (
        'buy'
        if direction == 'alcista'
        else 'sell'
    )

    size = round(
        (CAPITAL * LEVERAGE) / price,
        4
    )

    if size < 0.01:
        size = 0.01

    try:

        # ====================================
        # FORCE ISOLATED + X3
        # ====================================

        exchange.set_leverage(
            LEVERAGE,
            pair,
            params={
                'marginMode': 'isolated',
                'productType': 'USDT-FUTURES',
            }
        )

        time.sleep(0.5)

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

        # ====================================

        pos = {

            "pair": pair,

            "symbol": symbol,

            "side": direction,

            "entry": price,

            "size": size,

            "sl": analysis["sl"],

            "tp1": analysis["tp1"],

            "tp2": analysis["tp2"],

            "tp3": analysis["tp3"],

            "trail_best": price,

            "trail_sl": round(

                (
                    price * (1 - TRAIL_PCT)
                )

                if direction == 'alcista'

                else (

                    price * (1 + TRAIL_PCT)
                ),

                4
            ),

            "tp1_hit": False,

            "tp2_hit": False,

            "be_activated": False,
        }

        state["positions"].append(pos)

        save_state()

        tg(
            f"🚀 *TRADE* {pair}\n"
            f"📈 {direction.upper()}\n"
            f"💰 {CAPITAL} USDT x{LEVERAGE}\n"
            f"🎯 Entry: {price}\n"
            f"🛑 SL: {analysis['sl']}\n"
            f"🎯 TP1: {analysis['tp1']}\n"
            f"🎯 TP2: {analysis['tp2']}\n"
            f"🎯 TP3: {analysis['tp3']}"
        )

        log.info(
            f"TRADE {direction} "
            f"{pair} @ {price}"
        )

    except Exception as e:

        log.error(f"open_trade: {e}")

# ============================================
# CLOSE TRADE
# ============================================

def close_trade(pos, reason, price=None):

    side = (
        'sell'
        if pos["side"] == 'alcista'
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

        tg(
            f"🏁 CLOSE {pos['pair']}\n"
            f"{reason}"
        )

    except Exception as e:

        log.error(f"close_trade: {e}")

    finally:

        state["positions"] = [

            p

            for p in state["positions"]

            if p["pair"] != pos["pair"]
        ]

        save_state()

# ============================================
# MANAGE TRADES
# ============================================

def manage_trades():

    for pos in list(state["positions"]):

        try:

            price = float(
                exchange.fetch_ticker(
                    pos["pair"]
                )['last']
            )

            entry = pos["entry"]

            side = pos["side"]

            pnl = (

                ((price - entry) / entry)

                if side == 'alcista'

                else (

                    (entry - price) / entry
                )
            )

            trail_pct = get_trail_pct(
                pnl
            )

            # SL

            if (

                (
                    side == 'alcista'
                    and price <= pos["sl"]
                )

                or

                (
                    side == 'bajista'
                    and price >= pos["sl"]
                )
            ):

                close_trade(
                    pos,
                    "SL",
                    price
                )

                continue

            # TP3

            if (

                (
                    side == 'alcista'
                    and price >= pos["tp3"]
                )

                or

                (
                    side == 'bajista'
                    and price <= pos["tp3"]
                )
            ):

                close_trade(
                    pos,
                    "TP3",
                    price
                )

                continue

            # TP1

            if not pos["tp1_hit"]:

                if (

                    (
                        side == 'alcista'
                        and price >= pos["tp1"]
                    )

                    or

                    (
                        side == 'bajista'
                        and price <= pos["tp1"]
                    )
                ):

                    pos["tp1_hit"] = True

                    tg(
                        f"🎯 TP1 "
                        f"{pos['pair']}"
                    )

            # BE

            if (

                not pos["be_activated"]

                and pnl >= BE_TRIGGER
            ):

                pos["be_activated"] = True

                pos["trail_sl"] = entry

            # TRAILING

            if side == 'alcista':

                if price > pos["trail_best"]:

                    pos["trail_best"] = price

                    pos["trail_sl"] = round(
                        price * (1 - trail_pct),
                        4
                    )

                if (

                    price <= pos["trail_sl"]

                    and pos["tp1_hit"]
                ):

                    close_trade(
                        pos,
                        "TRAIL",
                        price
                    )

                    continue

            else:

                if price < pos["trail_best"]:

                    pos["trail_best"] = price

                    pos["trail_sl"] = round(
                        price * (1 + trail_pct),
                        4
                    )

                if (

                    price >= pos["trail_sl"]

                    and pos["tp1_hit"]
                ):

                    close_trade(
                        pos,
                        "TRAIL",
                        price
                    )

                    continue

            save_state()

        except Exception as e:

            log.error(
                f"manage_trades: {e}"
            )

# ============================================
# MAIN LOOP
# ============================================

def main_loop():

    load_state()

    tg(
        "🚀 Institutional Growth Scanner"
    )

    log.info("🚀 BOT STARTED")

    while True:

        try:

            reset_daily()

            manage_trades()

            data = fetch_finnhub_data(
                USA_STOCKS
            )

            for item in data:

                if abs(item["change"]) < 4:
                    continue

                if not can_send_signal(
                    item["symbol"]
                ):
                    continue

                analysis = analyze_signal(
                    item
                )

                mark_signal_sent(
                    item["symbol"]
                )

                state["daily_signals"].append(
                    analysis
                )

                save_state()

                direction = analysis[
                    "direction"
                ]

                if confirms_ema(
                    item["symbol"],
                    direction
                ):

                    open_trade(
                        analysis
                    )

            time.sleep(300)

        except Exception as e:

            log.error(f"MAIN LOOP: {e}")

            time.sleep(30)

# ============================================
# START
# ============================================

if __name__ == '__main__':

    main_loop()