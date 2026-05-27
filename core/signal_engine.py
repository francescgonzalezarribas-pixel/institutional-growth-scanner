import yfinance as yf
import pandas as pd
import numpy as np

# ─────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────

WATCHLIST = [

    # BIG TECH
    "NVDA","AMD","MSFT","META","AMZN","GOOGL","TSLA",

    # AI
    "PLTR","SMCI","ARM","MU","AVGO",

    # SMALL CAPS / MOMENTUM
    "SOUN","BBAI","RKLB","ASTS","LUNR",

    # FINTECH
    "SOFI","HOOD","AFRM",

    # BIOTECH
    "ALT","TEM","RXRX",

    # ETFs
    "QQQ","SPY","IWM","SMH",

    # RECOVERY
    "INTC","BABA","DIS","PFE"
]

# ─────────────────────────────────────────────────────────────
# RSI
# ─────────────────────────────────────────────────────────────

def calc_rsi(series, period=14):

    delta = series.diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()

    rs = avg_gain / avg_loss

    rsi = 100 - (100 / (1 + rs))

    return rsi


# ─────────────────────────────────────────────────────────────
# MACD
# ─────────────────────────────────────────────────────────────

def calc_macd(close):

    ema12 = close.ewm(span=12).mean()
    ema26 = close.ewm(span=26).mean()

    macd = ema12 - ema26
    signal = macd.ewm(span=9).mean()

    return macd, signal


# ─────────────────────────────────────────────────────────────
# VWAP
# ─────────────────────────────────────────────────────────────

def calc_vwap(df):

    tp = (
        df["High"]
        + df["Low"]
        + df["Close"]
    ) / 3

    vwap = (
        (tp * df["Volume"]).cumsum()
        / df["Volume"].cumsum()
    )

    return vwap


# ─────────────────────────────────────────────────────────────
# SUPPORT / RESISTANCE
# ─────────────────────────────────────────────────────────────

def support_resistance(df):

    resistance = round(
        float(df["High"].tail(20).max()),
        2
    )

    support = round(
        float(df["Low"].tail(20).min()),
        2
    )

    return support, resistance


# ─────────────────────────────────────────────────────────────
# ANALISIS ACTIVO
# ─────────────────────────────────────────────────────────────

def analyze_ticker(ticker):

    try:

        df = yf.download(
            ticker,
            period="6mo",
            interval="1d",
            auto_adjust=True,
            progress=False
        )

        if df.empty or len(df) < 60:
            return None

        close = df["Close"]

        volume = df["Volume"]

        price = round(float(close.iloc[-1]), 2)

        # RSI
        rsi = round(
            float(calc_rsi(close).iloc[-1]),
            1
        )

        # MACD
        macd, signal = calc_macd(close)

        macd_bull = macd.iloc[-1] > signal.iloc[-1]

        # VWAP
        vwap = calc_vwap(df)

        above_vwap = close.iloc[-1] > vwap.iloc[-1]

        # Volume
        vol_now = volume.iloc[-1]

        vol_avg = volume.tail(20).mean()

        rel_volume = round(
            float(vol_now / vol_avg),
            2
        )

        # Momentum
        weekly = round(
            (
                (price - close.iloc[-5])
                / close.iloc[-5]
            ) * 100,
            1
        )

        monthly = round(
            (
                (price - close.iloc[-20])
                / close.iloc[-20]
            ) * 100,
            1
        )

        # Trend
        ema20 = close.ewm(span=20).mean()

        ema50 = close.ewm(span=50).mean()

        trend_bull = (
            close.iloc[-1] > ema20.iloc[-1]
            and ema20.iloc[-1] > ema50.iloc[-1]
        )

        # Support / Resistance
        support, resistance = support_resistance(df)

        # ─────────────────────────────
        # SCORE
        # ─────────────────────────────

        score = 0

        reasons = []

        # RSI
        if 45 <= rsi <= 68:
            score += 1
            reasons.append("RSI sano")

        # MACD
        if macd_bull:
            score += 2
            reasons.append("MACD alcista")

        # VWAP
        if above_vwap:
            score += 2
            reasons.append("Sobre VWAP")

        # Volume
        if rel_volume > 1.5:
            score += 2
            reasons.append(
                f"Volumen x{rel_volume}"
            )

        # Trend
        if trend_bull:
            score += 2
            reasons.append("Tendencia alcista")

        # Momentum
        if weekly > 4:
            score += 1
            reasons.append("Momentum semanal")

        # ─────────────────────────────
        # SIGNAL TYPE
        # ─────────────────────────────

        signal_type = "SWING"

        if rel_volume > 2 and weekly > 5:
            signal_type = "INTRADAY"

        # ─────────────────────────────
        # ENTRY / TP / SL
        # ─────────────────────────────

        entry = price

        tp1 = round(
            price * 1.04,
            2
        )

        tp2 = round(
            price * 1.08,
            2
        )

        sl = round(
            support * 0.985,
            2
        )

        rr = round(
            (tp1 - entry)
            / (entry - sl),
            2
        )

        # ─────────────────────────────
        # VALIDATION
        # ─────────────────────────────

        if score < 6:
            return None

        if rr < 1.2:
            return None

        # ─────────────────────────────
        # RESULT
        # ─────────────────────────────

        return {

            "ticker": ticker,

            "price": price,

            "signal": signal_type,

            "score": score,

            "rsi": rsi,

            "weekly": weekly,

            "monthly": monthly,

            "rel_volume": rel_volume,

            "entry": entry,

            "tp1": tp1,

            "tp2": tp2,

            "sl": sl,

            "rr": rr,

            "support": support,

            "resistance": resistance,

            "reasons": reasons,
        }

    except Exception as e:

        print(f"[SIGNAL ERROR] {ticker} -> {e}")

        return None


# ─────────────────────────────────────────────────────────────
# GENERADOR PRINCIPAL
# ─────────────────────────────────────────────────────────────

def generate_signals(limit=10):

    results = []

    for ticker in WATCHLIST:

        data = analyze_ticker(ticker)

        if data:
            results.append(data)

    results.sort(
        key=lambda x: x["score"],
        reverse=True
    )

    return results[:limit]


# ─────────────────────────────────────────────────────────────
# TEST
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":

    signals = generate_signals()

    print("\n🔥 SEÑALES DETECTADAS\n")

    for s in signals:

        print("━━━━━━━━━━━━━━━━━━")

        print(
            f"{s['ticker']} | "
            f"{s['signal']} | "
            f"Score {s['score']}"
        )

        print(
            f"Precio: {s['price']}"
        )

        print(
            f"RSI: {s['rsi']} | "
            f"Vol: x{s['rel_volume']}"
        )

        print(
            f"Entrada: {s['entry']}"
        )

        print(
            f"TP1: {s['tp1']} | "
            f"TP2: {s['tp2']}"
        )

        print(
            f"SL: {s['sl']} | "
            f"R/R: {s['rr']}"
        )

        print(
            "Motivos:",
            ", ".join(s["reasons"])
        )