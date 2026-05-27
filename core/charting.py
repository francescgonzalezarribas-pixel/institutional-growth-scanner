import io
import yfinance as yf
import matplotlib.pyplot as plt
import pandas as pd
import numpy as np

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

    hist = macd - signal

    return macd, signal, hist


# ─────────────────────────────────────────────────────────────
# VWAP
# ─────────────────────────────────────────────────────────────

def calc_vwap(df):

    tp = (df["High"] + df["Low"] + df["Close"]) / 3

    vwap = (
        (tp * df["Volume"]).cumsum()
        / df["Volume"].cumsum()
    )

    return vwap


# ─────────────────────────────────────────────────────────────
# SOPORTES / RESISTENCIAS
# ─────────────────────────────────────────────────────────────

def support_resistance(df):

    high = df["High"].tail(30).max()
    low = df["Low"].tail(30).min()

    return round(low, 2), round(high, 2)


# ─────────────────────────────────────────────────────────────
# GRAFICO PRINCIPAL
# ─────────────────────────────────────────────────────────────

def create_chart(
    ticker,
    entry=None,
    tp=None,
    sl=None,
    period="3mo"
):

    df = yf.download(
        ticker,
        period=period,
        interval="1d",
        auto_adjust=True,
        progress=False
    )

    if df.empty:
        return None

    close = df["Close"]

    # Indicadores
    rsi = calc_rsi(close)

    macd, signal, hist = calc_macd(close)

    vwap = calc_vwap(df)

    support, resistance = support_resistance(df)

    # ─────────────────────────────
    # FIGURA
    # ─────────────────────────────

    fig = plt.figure(figsize=(12, 9))

    # PRECIO
    ax1 = plt.subplot(3, 1, 1)

    ax1.plot(close.index, close, label="Precio")
    ax1.plot(vwap.index, vwap, label="VWAP")

    ax1.axhline(support, linestyle="--")
    ax1.axhline(resistance, linestyle="--")

    if entry:
        ax1.axhline(entry, linestyle="-")
        ax1.text(close.index[-1], entry, f"ENTRY {entry}")

    if tp:
        ax1.axhline(tp, linestyle="-")
        ax1.text(close.index[-1], tp, f"TP {tp}")

    if sl:
        ax1.axhline(sl, linestyle="-")
        ax1.text(close.index[-1], sl, f"SL {sl}")

    ax1.set_title(f"{ticker} — PRICE / VWAP")

    ax1.legend()

    # RSI
    ax2 = plt.subplot(3, 1, 2)

    ax2.plot(rsi.index, rsi, label="RSI")

    ax2.axhline(70, linestyle="--")
    ax2.axhline(30, linestyle="--")

    ax2.set_ylim(0, 100)

    ax2.set_title("RSI")

    # MACD
    ax3 = plt.subplot(3, 1, 3)

    ax3.plot(macd.index, macd, label="MACD")
    ax3.plot(signal.index, signal, label="SIGNAL")

    ax3.bar(hist.index, hist)

    ax3.set_title("MACD")

    ax3.legend()

    plt.tight_layout()

    # ─────────────────────────────
    # EXPORTAR MEMORIA
    # ─────────────────────────────

    buffer = io.BytesIO()

    plt.savefig(
        buffer,
        format="png",
        bbox_inches="tight"
    )

    buffer.seek(0)

    plt.close()

    return buffer


# ─────────────────────────────────────────────────────────────
# TEST
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":

    chart = create_chart(
        "NVDA",
        entry=180,
        tp=195,
        sl=172
    )

    if chart:
        with open("test_chart.png", "wb") as f:
            f.write(chart.getbuffer())

        print("Chart generado")
    else:
        print("Error generando chart")