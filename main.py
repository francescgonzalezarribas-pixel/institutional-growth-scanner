import os
import time
import json
import logging
from datetime import datetime

import ccxt
import numpy as np
import requests

from dotenv import load_dotenv

# =========================================
# LOAD ENV
# =========================================

load_dotenv()

# =========================================
# CONFIG
# =========================================

API_KEY        = os.getenv("BITGET_API_KEY", "")
API_SECRET     = os.getenv("BITGET_API_SECRET", "")
API_PASSPHRASE = os.getenv("BITGET_API_PASSPHRASE", "")

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

LEVERAGE           = 3
MAX_TRADES         = 2
CAPITAL_PER_TRADE  = 20

SL_PCT             = 0.015
BREAKEVEN_TRIGGER  = 0.015
TRAILING_PCT       = 0.012

SCAN_INTERVAL      = 20
COOLDOWN_MINUTES   = 60

MIN_VOLUME         = 300000
MIN_CHANGE_5M      = 2.0
MIN_RVOL           = 2.0
MAX_SPREAD         = 0.40

STATE_FILE = "state.json"

# =========================================
# LOGGING
# =========================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)

log = logging.getLogger(__name__)

# =========================================
# EXCHANGE
# =========================================

exchange = ccxt.bitget({
    'apiKey': API_KEY,
    'secret': API_SECRET,
    'password': API_PASSPHRASE,
    'options': {
        'defaultType': 'swap'
    }
})

# =========================================
# TELEGRAM
# =========================================

def tg(msg):

    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return

    try:

        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": msg
        }

        requests.post(
            url,
            json=payload,
            timeout=10
        )

    except Exception as e:
        log.error(f"telegram: {e}")

# =========================================
# STATE
# =========================================

state = {
    "positions": [],
    "cooldowns": {}
}

# =========================================
# SAVE / LOAD
# =========================================

def load_state():

    global state

    if os.path.exists(STATE_FILE):

        try:

            with open(STATE_FILE, "r") as f:
                state = json.load(f)

        except Exception as e:
            log.error(f"load_state: {e}")

def save_state():

    try:

        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)

    except Exception as e:
        log.error(f"save_state: {e}")

# =========================================
# SYMBOLS
# =========================================

def get_tradfi_symbols():

    markets = exchange.load_markets()

    symbols = []

    crypto_blacklist = {
        "BTC",
        "ETH",
        "SOL",
        "XRP",
        "DOGE",
        "SHIB",
        "PEPE",
        "BNB",
        "TRX",
        "ADA",
        "LTC"
    }

    for symbol, market in markets.items():

        try:

            if not market.get("active"):
                continue

            if not market.get("swap"):
                continue

            if "/USDT:USDT" not in symbol:
                continue

            base = market.get("base", "")

            if base in crypto_blacklist:
                continue

            symbols.append(symbol)

        except:
            pass

    return sorted(symbols)

# =========================================
# HELPERS
# =========================================

def pct_change(a, b):

    if b == 0:
        return 0

    return ((a - b) / b) * 100

def calculate_rvol(volumes):

    if len(volumes) < 10:
        return 0

    current = volumes[-1]
    avg     = np.mean(volumes[:-1])

    if avg == 0:
        return 0

    return current / avg

def is_on_cooldown(symbol):

    last = state["cooldowns"].get(symbol)

    if not last:
        return False

    elapsed = time.time() - last

    return elapsed < (COOLDOWN_MINUTES * 60)

def set_cooldown(symbol):

    state["cooldowns"][symbol] = time.time()

    save_state()

# =========================================
# SCANNER
# =========================================

def scan_symbol(symbol):

    try:

        candles = exchange.fetch_ohlcv(
            symbol,
            timeframe='1m',
            limit=40
        )

        if not candles or len(candles) < 20:
            return None

        closes  = [x[4] for x in candles]
        highs   = [x[2] for x in candles]
        volumes = [x[5] for x in candles]

        current_price = closes[-1]

        change_1m = pct_change(closes[-1], closes[-2])
        change_5m = pct_change(closes[-1], closes[-6])

        rvol = calculate_rvol(volumes)

        high_15m = max(highs[-16:-1])

        breakout = current_price > high_15m

        ema20 = np.mean(closes[-20:])

        trend_ok = current_price > ema20

        ticker = exchange.fetch_ticker(symbol)

        quote_volume = ticker.get('quoteVolume', 0)

        bid = ticker.get('bid', 0)
        ask = ticker.get('ask', 0)

        spread = 0

        if bid > 0:
            spread = ((ask - bid) / bid) * 100

        if quote_volume < MIN_VOLUME:
            return None

        if change_5m < MIN_CHANGE_5M:
            return None

        if rvol < MIN_RVOL:
            return None

        if spread > MAX_SPREAD:
            return None

        if not breakout:
            return None

        if not trend_ok:
            return None

        score = (
            (change_1m * 0.4) +
            (change_5m * 0.3) +
            (rvol * 0.2) +
            (1 if breakout else 0) * 0.1
        )

        return {
            "symbol": symbol,
            "price": current_price,
            "change_1m": round(change_1m, 2),
            "change_5m": round(change_5m, 2),
            "rvol": round(rvol, 2),
            "spread": round(spread, 3),
            "score": round(score, 2),
            "volume": round(quote_volume, 2)
        }

    except Exception as e:

        log.error(f"scan {symbol}: {e}")

        return None

# =========================================
# OPEN TRADE
# =========================================

def open_trade(signal):

    symbol = signal["symbol"]
    price  = signal["price"]

    if len(state["positions"]) >= MAX_TRADES:
        return

    if is_on_cooldown(symbol):
        return

    size = round(
        (CAPITAL_PER_TRADE * LEVERAGE) / price,
        4
    )

    try:

        exchange.set_leverage(
            LEVERAGE,
            symbol,
            params={
                'marginMode': 'isolated',
                'productType': 'USDT-FUTURES'
            }
        )

        time.sleep(0.5)

        exchange.create_order(
            symbol,
            'market',
            'buy',
            size,
            None,
            {
                'marginMode': 'isolated',
                'leverage': str(LEVERAGE),
                'reduceOnly': False,
            }
        )

        sl = round(
            price * (1 - SL_PCT),
            4
        )

        position = {
            "symbol": symbol,
            "entry": price,
            "size": size,
            "sl": sl,
            "best_price": price,
            "trail_sl": sl,
            "breakeven": False,
            "opened_at": time.time()
        }

        state["positions"].append(position)

        set_cooldown(symbol)

        save_state()

        log.info(f"OPEN LONG {symbol} @ {price}")

        tg(
            f"🚀 LONG {symbol}\n"
            f"💰 Entry: {price}\n"
            f"📈 5m: {signal['change_5m']}%\n"
            f"🔥 RVOL: {signal['rvol']}\n"
            f"⭐ Score: {signal['score']}"
        )

    except Exception as e:

        log.error(f"open_trade {symbol}: {e}")

# =========================================
# CLOSE TRADE
# =========================================

def close_trade(position, reason):

    try:

        exchange.create_order(
            position["symbol"],
            'market',
            'sell',
            position["size"],
            None,
            {
                'marginMode': 'isolated',
                'leverage': str(LEVERAGE),
                'reduceOnly': True,
            }
        )

        log.info(
            f"CLOSE {position['symbol']} | {reason}"
        )

        tg(
            f"🏁 CLOSE {position['symbol']}\n"
            f"📋 {reason}"
        )

    except Exception as e:

        log.error(f"close_trade: {e}")

    finally:

        if position in state["positions"]:
            state["positions"].remove(position)

        save_state()

# =========================================
# MANAGE POSITIONS
# =========================================

def manage_positions():

    positions = state["positions"][:]

    for pos in positions:

        try:

            ticker = exchange.fetch_ticker(
                pos["symbol"]
            )

            price = ticker['last']

            pnl = pct_change(
                price,
                pos["entry"]
            )

            if price <= pos["sl"]:

                close_trade(
                    pos,
                    f"SL {round(pnl,2)}%"
                )

                continue

            if pnl >= (
                BREAKEVEN_TRIGGER * 100
            ) and not pos["breakeven"]:

                pos["sl"] = pos["entry"]

                pos["breakeven"] = True

                log.info(
                    f"BREAKEVEN {pos['symbol']}"
                )

            if price > pos["best_price"]:

                pos["best_price"] = price

                dynamic_trail = TRAILING_PCT

                if pnl >= 5:
                    dynamic_trail = 0.02

                if pnl >= 10:
                    dynamic_trail = 0.03

                pos["trail_sl"] = round(
                    price * (
                        1 - dynamic_trail
                    ),
                    4
                )

            if pos["breakeven"]:

                if price <= pos["trail_sl"]:

                    close_trade(
                        pos,
                        f"TRAIL {round(pnl,2)}%"
                    )

                    continue

            save_state()

        except Exception as e:

            log.error(
                f"manage_positions: {e}"
            )

# =========================================
# MAIN LOOP
# =========================================

def main():

    load_state()

    log.info(
        "Loading TradFi symbols..."
    )

    symbols = get_tradfi_symbols()

    log.info(
        f"TradFi symbols loaded: {len(symbols)}"
    )

    tg(
        f"🚀 TradFi Momentum Bot iniciado\n"
        f"📊 Símbolos: {len(symbols)}\n"
        f"⚡ Scanner activo"
    )

    while True:

        try:

            signals = []

            for symbol in symbols:

                result = scan_symbol(symbol)

                if result:
                    signals.append(result)

            signals = sorted(
                signals,
                key=lambda x: x['score'],
                reverse=True
            )

            if signals:

                log.info("TOP SIGNALS")

                for s in signals[:5]:

                    log.info(
                        f"{s['symbol']} | "
                        f"score={s['score']} | "
                        f"5m={s['change_5m']}% | "
                        f"RVOL={s['rvol']}"
                    )

                for signal in signals[:2]:
                    open_trade(signal)

            manage_positions()

            time.sleep(SCAN_INTERVAL)

        except KeyboardInterrupt:

            break

        except Exception as e:

            log.error(
                f"main loop: {e}"
            )

            time.sleep(10)

# =========================================
# START
# =========================================

if __name__ == '__main__':
    main()