import yfinance as yf
from datetime import datetime

# ─────────────────────────────────────────────────────────────
# ACTIVOS MACRO
# ─────────────────────────────────────────────────────────────

ASSETS = {

    # Índices
    "SPY": "SP500",
    "QQQ": "NASDAQ",
    "IWM": "RUSSELL",

    # Volatilidad
    "^VIX": "VIX",

    # Dólar
    "DX-Y.NYB": "DXY",

    # Bonos
    "^TNX": "US10Y",

    # Oro
    "GC=F": "GOLD",

    # Bitcoin
    "BTC-USD": "BTC"
}


# ─────────────────────────────────────────────────────────────
# OBTENER DATOS
# ─────────────────────────────────────────────────────────────

def fetch_data(ticker):

    try:

        df = yf.download(
            ticker,
            period="3mo",
            interval="1d",
            progress=False,
            auto_adjust=True
        )

        if df.empty:
            return None

        close = df["Close"]

        price = round(float(close.iloc[-1]), 2)

        weekly = round(
            (
                (price - close.iloc[-5])
                / close.iloc[-5]
            ) * 100,
            2
        )

        monthly = round(
            (
                (price - close.iloc[-20])
                / close.iloc[-20]
            ) * 100,
            2
        )

        ema20 = close.ewm(span=20).mean()

        trend = price > ema20.iloc[-1]

        return {
            "price": price,
            "weekly": weekly,
            "monthly": monthly,
            "trend": trend
        }

    except Exception as e:

        print(f"[MARKET ERROR] {ticker} -> {e}")

        return None


# ─────────────────────────────────────────────────────────────
# DETECTAR FASE
# ─────────────────────────────────────────────────────────────

def detect_market_phase():

    data = {}

    for ticker, name in ASSETS.items():

        d = fetch_data(ticker)

        if d:
            data[name] = d

    if not data:
        return None

    # ─────────────────────────────
    # VARIABLES
    # ─────────────────────────────

    spy = data.get("SP500")
    qqq = data.get("NASDAQ")
    vix = data.get("VIX")
    dxy = data.get("DXY")
    btc = data.get("BTC")

    phase = "NEUTRAL"

    score = 0

    reasons = []

    # ─────────────────────────────
    # SP500
    # ─────────────────────────────

    if spy:

        if spy["trend"]:
            score += 2
            reasons.append("SP500 sobre EMA20")

        if spy["weekly"] > 1:
            score += 1
            reasons.append("Momentum SP500")

    # ─────────────────────────────
    # NASDAQ
    # ─────────────────────────────

    if qqq:

        if qqq["trend"]:
            score += 2
            reasons.append("NASDAQ fuerte")

        if qqq["monthly"] > 4:
            score += 1
            reasons.append("Tech momentum")

    # ─────────────────────────────
    # VIX
    # ─────────────────────────────

    if vix:

        if vix["price"] < 18:
            score += 2
            reasons.append("VIX bajo")

        elif vix["price"] > 25:
            score -= 3
            reasons.append("VIX alto")

    # ─────────────────────────────
    # DXY
    # ─────────────────────────────

    if dxy:

        if dxy["weekly"] > 1:
            score -= 1
            reasons.append("Dólar fuerte")

    # ─────────────────────────────
    # BTC
    # ─────────────────────────────

    if btc:

        if btc["monthly"] > 8:
            score += 1
            reasons.append("BTC risk-on")

    # ─────────────────────────────
    # FASE FINAL
    # ─────────────────────────────

    if score >= 6:
        phase = "RISK_ON"

    elif score <= 1:
        phase = "RISK_OFF"

    else:
        phase = "NEUTRAL"

    # ─────────────────────────────
    # CONFIGURACIÓN BOT
    # ─────────────────────────────

    settings = {

        "max_signals": 10,

        "min_score": 6,

        "allow_intraday": True,

        "allow_swing": True,

        "risk_level": "NORMAL"
    }

    if phase == "RISK_ON":

        settings["max_signals"] = 15
        settings["min_score"] = 5
        settings["risk_level"] = "AGGRESSIVE"

    elif phase == "RISK_OFF":

        settings["max_signals"] = 5
        settings["min_score"] = 8
        settings["allow_intraday"] = False
        settings["risk_level"] = "DEFENSIVE"

    # ─────────────────────────────
    # RESULTADO
    # ─────────────────────────────

    return {

        "phase": phase,

        "score": score,

        "reasons": reasons,

        "settings": settings,

        "data": data,

        "time": datetime.now().strftime("%d/%m %H:%M")
    }


# ─────────────────────────────────────────────────────────────
# TEST
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":

    market = detect_market_phase()

    if market:

        print("\n🌍 MARKET PHASE\n")

        print(
            f"Fase: {market['phase']}"
        )

        print(
            f"Score: {market['score']}"
        )

        print(
            "\nMotivos:"
        )

        for r in market["reasons"]:

            print(f"- {r}")

        print("\nConfiguración:\n")

        for k, v in market["settings"].items():

            print(f"{k}: {v}")