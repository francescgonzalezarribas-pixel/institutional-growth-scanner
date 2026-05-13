import os
import ccxt
import pandas as pd
import time
import requests
import logging

from ta.trend import EMAIndicator
from ta.momentum import RSIIndicator

# ==========================================
# API KEYS
# ==========================================

API_KEY = os.getenv("BITGET_API_KEY")
API_SECRET = os.getenv("BITGET_API_SECRET")
API_PASSWORD = os.getenv("BITGET_API_PASSPHRASE")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# ==========================================
# CONFIG
# ==========================================

TIMEFRAME = "5m"

LEVERAGE = 3
MARGIN_MODE = "isolated"

USDT_PER_TRADE = 20
MAX_OPEN_TRADES = 2

TAKE_PROFIT = 0.04
STOP_LOSS = 0.015

TRAILING_TRIGGER = 0.02
TRAILING_DISTANCE = 0.01

SCAN_INTERVAL = 20

# ==========================================
# LOGGING
# ==========================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)

# ==========================================
# EXCHANGE
# ==========================================

exchange = ccxt.bitget({
    "apiKey": API_KEY,
    "secret": API_SECRET,
    "password": API_PASSWORD,
    "enableRateLimit": True,
    "options": {
        "defaultType": "swap"
    }
})

# ==========================================
# SYMBOLS
# ==========================================

SYMBOLS = [

    "GOOGLUSDT",
    "BABAUSDT",
    "RKLBUSDT",
    "AAOIUSDT",
    "MRVLUSDT",
    "LITEUSDT",
    "LWLGUSDT",
    "NBISUSDT",
    "COINUSDT",
    "PLTRUSDT",

    "MUUSDT",
    "NVDAUSDT",
    "SNDKUSDT",
    "INTCUSDT",
    "TSLAUSDT",
    "CRCLUSDT",
    "MSTRUSDT",
    "AMDUSDT",

    "HOODUSDT",
    "MSFTUSDT",
    "COHRUSDT",
    "AAPLUSDT",
    "KOPNUSDT",
    "METAUSDT",
    "TSMUSDT",
    "AXTIUSDT",
    "AMZNUSDT",

    "GMEUSDT",
    "WMTUSDT",
    "RDDTUSDT",
    "MCDUSDT",
    "OXYUSDT",
    "GEUSDT",
    "COPUSDT",
    "XOMUSDT",

    "NIOUSDT",
    "MPUSDT",
    "COSTUSDT",
    "APPUSDT",
    "VRTUSDT",
    "ETNUSDT",
    "AMATUSDT",
    "UNHUSDT",
    "KLACUSDT",

    "USARUSDT",
    "IONQUSDT",
    "CBRSUSDT",
    "AVGOUSDT",
    "ORCLUSDT",
    "INFQUSDT",
    "LLYUSDT",
    "NFLXUSDT",
    "ASMLUSDT",

    "APLDUSDT",
    "ARMUSDT",
    "JDUSDT",
    "OKLOUSDT",
    "FLYUSDT",
    "BEUSDT",
    "FUTUUSDT",
    "CRWVUSDT",
    "CRDOUSDT"
]

# ==========================================
# TELEGRAM
# ==========================================

def send_telegram(message):

    try:

        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

        requests.post(
            url,
            data={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message
            }
        )

    except Exception as e:
        logging.error(f"Telegram error: {e}")

# ==========================================
# GET DATA
# ==========================================

def get_ohlcv(symbol):

    try:

        data = exchange.fetch_ohlcv(
            symbol,
            timeframe=TIMEFRAME,
            limit=120
        )

        df = pd.DataFrame(
            data,
            columns=[
                "timestamp",
                "open",
                "high",
                "low",
                "close",
                "volume"
            ]
        )

        return df

    except Exception as e:

        logging.error(f"{symbol} OHLCV ERROR: {e}")
        return None

# ==========================================
# SIGNALS
# ==========================================

def get_signal(symbol):

    df = get_ohlcv(symbol)

    if df is None:
        return None

    try:

        df["ema9"] = EMAIndicator(
            close=df["close"],
            window=9
        ).ema_indicator()

        df["ema21"] = EMAIndicator(
            close=df["close"],
            window=21
        ).ema_indicator()

        df["rsi"] = RSIIndicator(
            close=df["close"],
            window=14
        ).rsi()

        volume_now = df["volume"].iloc[-1]
        volume_avg = df["volume"].rolling(20).mean().iloc[-1]

        close = df["close"].iloc[-1]

        ema9 = df["ema9"].iloc[-1]
        ema21 = df["ema21"].iloc[-1]

        rsi = df["rsi"].iloc[-1]

        # LONG

        if (
            ema9 > ema21
            and rsi > 55
            and volume_now > volume_avg * 1.5
        ):

            logging.info(f"{symbol} LONG SIGNAL")

            return {
                "side": "buy",
                "price": close
            }

        # SHORT

        if (
            ema9 < ema21
            and rsi < 45
            and volume_now > volume_avg * 1.5
        ):

            logging.info(f"{symbol} SHORT SIGNAL")

            return {
                "side": "sell",
                "price": close
            }

        return None

    except Exception as e:

        logging.error(f"{symbol} SIGNAL ERROR: {e}")
        return None

# ==========================================
# OPEN POSITIONS
# ==========================================

open_positions = {}

# ==========================================
# OPEN TRADE
# ==========================================

def open_trade(symbol, side):

    try:

        if len(open_positions) >= MAX_OPEN_TRADES:
            return

        ticker = exchange.fetch_ticker(symbol)

        price = ticker["last"]

        amount = (USDT_PER_TRADE * LEVERAGE) / price

        # FORCE ISOLATED

        try:
            exchange.set_margin_mode(
                MARGIN_MODE,
                symbol
            )
        except:
            pass

        # FORCE LEVERAGE

        try:
            exchange.set_leverage(
                LEVERAGE,
                symbol
            )
        except:
            pass

        order = exchange.create_market_order(
            symbol=symbol,
            side=side,
            amount=amount
        )

        open_positions[symbol] = {
            "side": side,
            "entry": price,
            "amount": amount,
            "highest": price,
            "lowest": price
        }

        message = (
            f"🚀 TRADE OPEN\n\n"
            f"Symbol: {symbol}\n"
            f"Side: {side}\n"
            f"Entry: {price}\n"
            f"Capital: {USDT_PER_TRADE}$\n"
            f"Leverage: x{LEVERAGE}"
        )

        logging.info(message)

        send_telegram(message)

    except Exception as e:

        logging.error(f"{symbol} OPEN TRADE ERROR: {e}")

# ==========================================
# CLOSE TRADE
# ==========================================

def close_trade(symbol):

    try:

        if symbol not in open_positions:
            return

        position = open_positions[symbol]

        side = "sell" if position["side"] == "buy" else "buy"

        exchange.create_market_order(
            symbol=symbol,
            side=side,
            amount=position["amount"]
        )

        send_telegram(f"❌ CLOSED {symbol}")

        logging.info(f"{symbol} CLOSED")

        del open_positions[symbol]

    except Exception as e:

        logging.error(f"{symbol} CLOSE ERROR: {e}")

# ==========================================
# MANAGE POSITIONS
# ==========================================

def manage_positions():

    for symbol in list(open_positions.keys()):

        try:

            ticker = exchange.fetch_ticker(symbol)

            price = ticker["last"]

            position = open_positions[symbol]

            entry = position["entry"]

            side = position["side"]

            # ======================================
            # LONG
            # ======================================

            if side == "buy":

                pnl = (price - entry) / entry

                if price > position["highest"]:
                    position["highest"] = price

                # TAKE PROFIT

                if pnl >= TAKE_PROFIT:

                    logging.info(f"{symbol} TAKE PROFIT")

                    close_trade(symbol)

                    continue

                # STOP LOSS

                if pnl <= -STOP_LOSS:

                    logging.info(f"{symbol} STOP LOSS")

                    close_trade(symbol)

                    continue

                # TRAILING

                if pnl >= TRAILING_TRIGGER:

                    trailing_price = (
                        position["highest"]
                        * (1 - TRAILING_DISTANCE)
                    )

                    if price <= trailing_price:

                        logging.info(f"{symbol} TRAILING STOP")

                        close_trade(symbol)

                        continue

            # ======================================
            # SHORT
            # ======================================

            if side == "sell":

                pnl = (entry - price) / entry

                if price < position["lowest"]:
                    position["lowest"] = price

                # TAKE PROFIT

                if pnl >= TAKE_PROFIT:

                    logging.info(f"{symbol} TAKE PROFIT")

                    close_trade(symbol)

                    continue

                # STOP LOSS

                if pnl <= -STOP_LOSS:

                    logging.info(f"{symbol} STOP LOSS")

                    close_trade(symbol)

                    continue

                # TRAILING

                if pnl >= TRAILING_TRIGGER:

                    trailing_price = (
                        position["lowest"]
                        * (1 + TRAILING_DISTANCE)
                    )

                    if price >= trailing_price:

                        logging.info(f"{symbol} TRAILING STOP")

                        close_trade(symbol)

                        continue

        except Exception as e:

            logging.error(f"{symbol} MANAGE ERROR: {e}")

# ==========================================
# MAIN
# ==========================================

def main():

    logging.info(
        f"TradFi symbols loaded: {len(SYMBOLS)}"
    )

    send_telegram(
        f"🤖 BOT STARTED\n\nSymbols: {len(SYMBOLS)}"
    )

    while True:

        try:

            manage_positions()

            if len(open_positions) < MAX_OPEN_TRADES:

                for symbol in SYMBOLS:

                    if symbol in open_positions:
                        continue

                    signal = get_signal(symbol)

                    if signal:

                        open_trade(
                            symbol,
                            signal["side"]
                        )

                        if len(open_positions) >= MAX_OPEN_TRADES:
                            break

                    time.sleep(0.5)

            time.sleep(SCAN_INTERVAL)

        except Exception as e:

            logging.error(f"MAIN LOOP ERROR: {e}")

            send_telegram(
                f"⚠️ MAIN LOOP ERROR\n{e}"
            )

            time.sleep(10)

# ==========================================
# START
# ==========================================

if __name__ == "__main__":
    main()