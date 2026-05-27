import time
import logging
from datetime import datetime

import yfinance as yf
import pandas as pd
import numpy as np

from config import *

log = logging.getLogger(__name__)

# =========================================================
# CACHE
# =========================================================

CACHE = {}

CACHE_SECONDS = CACHE_MINUTES * 60


# =========================================================
# CACHE HELPERS
# =========================================================

def cache_get(key):
    item = CACHE.get(key)

    if not item:
        return None

    if time.time() - item["time"] > CACHE_SECONDS:
        return None

    return item["data"]


def cache_set(key, data):
    CACHE[key] = {
        "time": time.time(),
        "data": data
    }


# =========================================================
# INDICATORS
# =========================================================

def calculate_rsi(series, period=14):
    delta = series.diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()

    rs = avg_gain / avg_loss

    rsi = 100 - (100 / (1 + rs))

    return round(rsi.iloc[-1], 2)


def calculate_macd(series):
    ema12 = series.ewm(span=12, adjust=False).mean()
    ema26 = series.ewm(span=26, adjust=False).mean()

    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()

    return round(macd.iloc[-1], 4), round(signal.iloc[-1], 4)


def calculate_vwap(df):
    try:
        typical_price = (df["High"] + df["Low"] + df["Close"]) / 3

        vwap = (
            (typical_price * df["Volume"]).cumsum()
            / df["Volume"].cumsum()
        )

        return round(vwap.iloc[-1], 2)

    except:
        return None


def relative_volume(df):
    try:
        current = df["Volume"].iloc[-1]

        avg = df["Volume"].rolling(20).mean().iloc[-1]

        if avg == 0:
            return 1

        return round(current / avg, 2)

    except:
        return 1


# =========================================================
# FETCH SINGLE
# =========================================================

def fetch_quote(ticker, period="1mo", interval="1d"):

    cache_key = f"{ticker}_{period}_{interval}"

    cached = cache_get(cache_key)

    if cached:
        return cached

    try:
        df = yf.download(
            ticker,
            period=period,
            interval=interval,
            progress=False,
            auto_adjust=True,
            threads=False
        )

        if df.empty:
            return None

        close = df["Close"]

        price = round(close.iloc[-1], 2)

        d1 = round(
            ((close.iloc[-1] / close.iloc[-2]) - 1) * 100,
            2
        ) if len(close) > 2 else 0

        d5 = round(
            ((close.iloc[-1] / close.iloc[-6]) - 1) * 100,
            2
        ) if len(close) > 6 else 0

        rsi = calculate_rsi(close)

        macd, signal = calculate_macd(close)

        ema20 = round(
            close.ewm(span=20).mean().iloc[-1],
            2
        )

        ema50 = round(
            close.ewm(span=50).mean().iloc[-1],
            2
        )

        vwap = calculate_vwap(df)

        vol_rel = relative_volume(df)

        hi52 = round(df["High"].max(), 2)

        lo52 = round(df["Low"].min(), 2)

        result = {
            "ticker": ticker,
            "price": price,
            "d1": d1,
            "d5": d5,
            "rsi": rsi,
            "macd": macd,
            "macd_signal": signal,
            "macd_cross_up": macd > signal,
            "ema20": ema20,
            "ema50": ema50,
            "sobre_ema20": price > ema20,
            "sobre_ema50": price > ema50,
            "vwap": vwap,
            "vol_rel": vol_rel,
            "hi52": hi52,
            "lo52": lo52,
            "volume": int(df["Volume"].iloc[-1]),
            "updated": datetime.now().strftime("%H:%M:%S"),
        }

        cache_set(cache_key, result)

        return result

    except Exception as e:
        log.warning(f"fetch_quote error {ticker}: {e}")

        return None


# =========================================================
# FETCH MULTIPLE
# =========================================================

def fetch_multiple(tickers, period="1mo"):

    results = []

    for ticker in tickers:

        try:
            data = fetch_quote(ticker, period)

            if data:
                results.append(data)

        except Exception as e:
            log.warning(f"fetch_multiple {ticker}: {e}")

    return results


# =========================================================
# MARKET MOVERS
# =========================================================

def get_market_movers(tickers):

    data = fetch_multiple(tickers)

    movers = sorted(
        data,
        key=lambda x: abs(x["d1"]),
        reverse=True
    )

    return movers[:10]


# =========================================================
# OPPORTUNITY SCAN
# =========================================================

def opportunity_scan(tickers):

    data = fetch_multiple(tickers)

    out = []

    for d in data:

        try:

            score = 0

            # Volumen fuerte
            if d["vol_rel"] >= 2:
                score += 2

            # Momentum
            if d["d5"] > 5:
                score += 2

            # RSI saludable
            if 45 <= d["rsi"] <= 70:
                score += 2

            # MACD
            if d["macd_cross_up"]:
                score += 2

            # Sobre VWAP
            if d["vwap"] and d["price"] > d["vwap"]:
                score += 1

            # Recuperación desde mínimos
            dist_low = (
                (d["price"] - d["lo52"])
                / d["lo52"]
            ) * 100

            if dist_low > 20:
                score += 1

            if score >= 6:

                d["score"] = score

                out.append(d)

        except:
            pass

    out.sort(
        key=lambda x: x["score"],
        reverse=True
    )

    return out[:10]