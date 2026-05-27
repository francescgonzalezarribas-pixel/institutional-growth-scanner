import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime

# ─────────────────────────────────────────────────────────────
# UNIVERSO AMPLIADO
# ─────────────────────────────────────────────────────────────

WATCHLIST = [

    # BIG TECH
    "NVDA","AMD","PLTR","TSLA","META","AMZN","GOOGL","MSFT","AAPL",

    # IA / DATACENTERS
    "SMCI","DELL","ANET","ARM","MU","AVGO",

    # SPACE / SATELLITE
    "RKLB","ASTS","LUNR","SPIR",

    # FINTECH
    "SOFI","AFRM","HOOD","PYPL",

    # BIOTECH
    "ALT","TEM","RXRX","CRSP","EDIT",

    # SMALL CAPS / MEME
    "SOUN","BBAI","CIFR","MARA","RIOT",

    # ETFS
    "QQQ","SPY","IWM","ARKK","SMH",

    # RECOVERY / TURNAROUND
    "INTC","BABA","PFE","DIS","NKE",

    # CRYPTO
    "COIN","MSTR",

    # EUROPA
    "SAP.DE","SIE.DE","MC.PA","ASML.AS"
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
# SCANNER PRINCIPAL
# ─────────────────────────────────────────────────────────────

def scan_opportunities(limit=10):

    results = []

    for ticker in WATCHLIST:

        try:

            df = yf.download(
                ticker,
                period="6mo",
                interval="1d",
                progress=False,
                auto_adjust=True
            )

            if df.empty or len(df) < 50:
                continue

            close = df["Close"]
            volume = df["Volume"]

            price = round(float(close.iloc[-1]), 2)

            # RSI
            rsi = round(float(calc_rsi(close).iloc[-1]), 1)

            # MACD
            macd, signal = calc_macd(close)

            macd_bull = macd.iloc[-1] > signal.iloc[-1]

            # VWAP aproximado
            vwap = (df["Close"] * df["Volume"]).cumsum() / df["Volume"].cumsum()

            above_vwap = close.iloc[-1] > vwap.iloc[-1]

            # Volumen relativo
            vol_now = volume.iloc[-1]
            vol_avg = volume.tail(20).mean()

            rel_volume = round(vol_now / vol_avg, 2)

            # Distancia máximos
            high_52 = close.max()

            drawdown = round(
                ((price - high_52) / high_52) * 100,
                1
            )

            # Momentum semanal
            weekly = round(
                ((price - close.iloc[-5]) / close.iloc[-5]) * 100,
                1
            )

            score = 0
            reasons = []

            # ─────────────────────────────
            # RECOVERY
            # ─────────────────────────────

            if drawdown < -35:
                score += 2
                reasons.append("Muy castigada")

            if rsi < 40:
                score += 1
                reasons.append("RSI bajo")

            # ─────────────────────────────
            # MOMENTUM
            # ─────────────────────────────

            if weekly > 8:
                score += 2
                reasons.append("Momentum fuerte")

            if rel_volume > 1.8:
                score += 2
                reasons.append(f"Volumen x{rel_volume}")

            if macd_bull:
                score += 1
                reasons.append("MACD alcista")

            if above_vwap:
                score += 1
                reasons.append("Sobre VWAP")

            # ─────────────────────────────
            # FINAL
            # ─────────────────────────────

            if score >= 4:

                signal_type = "RECOVERY"

                if weekly > 10 and rel_volume > 2:
                    signal_type = "MOMENTUM"

                results.append({
                    "ticker": ticker,
                    "price": price,
                    "rsi": rsi,
                    "weekly": weekly,
                    "drawdown": drawdown,
                    "rel_volume": rel_volume,
                    "score": score,
                    "signal": signal_type,
                    "reasons": reasons,
                })

        except Exception as e:
            print(f"[SCAN ERROR] {ticker} -> {e}")

    results.sort(
        key=lambda x: x["score"],
        reverse=True
    )

    return results[:limit]


# ─────────────────────────────────────────────────────────────
# TEST
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":

    data = scan_opportunities()

    print("\n🔥 OPORTUNIDADES DETECTADAS\n")

    for d in data:

        print("━━━━━━━━━━━━━━━━━━")

        print(
            f"{d['ticker']} | "
            f"{d['signal']} | "
            f"Score {d['score']}"
        )

        print(
            f"Precio: {d['price']} | "
            f"RSI: {d['rsi']} | "
            f"Semana: {d['weekly']}%"
        )

        print(
            f"Desde máximos: {d['drawdown']}%"
        )

        print(
            f"Volumen: x{d['rel_volume']}"
        )

        print(
            "Motivos:",
            ", ".join(d["reasons"])
        )