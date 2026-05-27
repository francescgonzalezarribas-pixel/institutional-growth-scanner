import yfinance as yf
import pandas as pd
import numpy as np

# ─────────────────────────────────────────────────────────────
# WATCHLIST INTRADÍA
# ─────────────────────────────────────────────────────────────

WATCHLIST = [

    # IA / Momentum
    "NVDA","AMD","SMCI","PLTR","ARM","MU",

    # Small caps calientes
    "SOUN","BBAI","RKLB","ASTS","LUNR",

    # Crypto
    "MSTR","COIN","MARA","RIOT",

    # ETFs
    "QQQ","SPY","SMH","IWM",

    # Fintech
    "SOFI","AFRM","HOOD"
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

    return 100 - (100 / (1 + rs))


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

    typical_price = (
        df["High"]
        + df["Low"]
        + df["Close"]
    ) / 3

    return (
        (typical_price * df["Volume"]).cumsum()
        / df["Volume"].cumsum()
    )


# ─────────────────────────────────────────────────────────────
# BREAKOUT
# ─────────────────────────────────────────────────────────────

def detect_breakout(df):

    recent_high = df["High"].tail(20).max()

    current = df["Close"].iloc[-1]

    return current >= recent_high * 0.995


# ─────────────────────────────────────────────────────────────
# ANALISIS INTRADÍA
# ─────────────────────────────────────────────────────────────

def analyze_intraday(ticker):

    try:

        df = yf.download(
            ticker,
            period="5d",
            interval="15m",
            auto_adjust=True,
            progress=False
        )

        if df.empty or len(df) < 50:
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

        macd_bull = (
            macd.iloc[-1]
            > signal.iloc[-1]
        )

        # VWAP
        vwap = calc_vwap(df)

        above_vwap = (
            close.iloc[-1]
            > vwap.iloc[-1]
        )

        # Volumen relativo
        vol_now = volume.iloc[-1]

        vol_avg = volume.tail(30).mean()

        rel_volume = round(
            float(vol_now / vol_avg),
            2
        )

        # Momentum sesión
        intraday_move = round(
            (
                (price - close.iloc[-12])
                / close.iloc[-12]
            ) * 100,
            2
        )

        # Breakout
        breakout = detect_breakout(df)

        # ─────────────────────────────
        # SCORE
        # ─────────────────────────────

        score = 0

        reasons = []

        # RSI
        if 50 <= rsi <= 75:
            score += 1
            reasons.append("RSI fuerte")

        # MACD
        if macd_bull:
            score += 2
            reasons.append("MACD alcista")

        # VWAP
        if above_vwap:
            score += 2
            reasons.append("Sobre VWAP")

        # Volumen
        if rel_volume > 1.8:
            score += 3
            reasons.append(
                f"Volumen x{rel_volume}"
            )

        # Momentum
        if intraday_move > 2:
            score += 2
            reasons.append(
                f"Momentum {intraday_move}%"
            )

        # Breakout
        if breakout:
            score += 3
            reasons.append("Breakout")

        # ─────────────────────────────
        # VALIDACIÓN
        # ─────────────────────────────

        if score < 7:
            return None

        # ─────────────────────────────
        # ENTRY / TP / SL
        # ─────────────────────────────

        entry = price

        tp1 = round(
            entry * 1.025,
            2
        )

        tp2 = round(
            entry * 1.05,
            2
        )

        sl = round(
            entry * 0.985,
            2
        )

        rr = round(
            (tp1 - entry)
            / (entry - sl),
            2
        )

        # ─────────────────────────────
        # RESULTADO
        # ─────────────────────────────

        return {

            "ticker": ticker,

            "signal": "INTRADAY",

            "score": score,

            "price": price,

            "entry": entry,

            "tp1": tp1,

            "tp2": tp2,

            "sl": sl,

            "rr": rr,

            "rsi": rsi,

            "rel_volume": rel_volume,

            "move": intraday_move,

            "reasons": reasons
        }

    except Exception as e:

        print(
            f"[INTRADAY ERROR] "
            f"{ticker} -> {e}"
        )

        return None


# ─────────────────────────────────────────────────────────────
# GENERADOR
# ─────────────────────────────────────────────────────────────

def generate_intraday_signals(limit=5):

    signals = []

    for ticker in WATCHLIST:

        data = analyze_intraday(ticker)

        if data:
            signals.append(data)

    signals.sort(
        key=lambda x: x["score"],
        reverse=True
    )

    return signals[:limit]


# ─────────────────────────────────────────────────────────────
# TEST
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":

    data = generate_intraday_signals()

    print("\n🔥 INTRADAY SIGNALS\n")

    for s in data:

        print("━━━━━━━━━━━━━━━━━━")

        print(
            f"{s['ticker']} | "
            f"Score {s['score']}"
        )

        print(
            f"Entrada: {s['entry']}"
        )

        print(
            f"TP1: {s['tp1']} | "
            f"TP2: {s['tp2']}"
        )

        print(
            f"SL: {s['sl']}"
        )

        print(
            f"RSI: {s['rsi']} | "
            f"Vol: x{s['rel_volume']}"
        )

        print(
            "Motivos:",
            ", ".join(s["reasons"])
        )