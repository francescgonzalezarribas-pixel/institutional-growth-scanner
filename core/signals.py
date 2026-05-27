import logging

from core.market_data import fetch_multiple

log = logging.getLogger(__name__)

# =========================================================
# INTRADAY SIGNALS
# =========================================================

def intraday_signals(tickers):

    data = fetch_multiple(
        tickers,
        period="5d"
    )

    signals = []

    for d in data:

        try:

            score = 0
            reasons = []

            # =================================================
            # RSI
            # =================================================

            if 50 <= d["rsi"] <= 70:
                score += 2
                reasons.append(f"RSI fuerte ({d['rsi']})")

            # =================================================
            # VWAP
            # =================================================

            if d["vwap"] and d["price"] > d["vwap"]:
                score += 2
                reasons.append("Sobre VWAP")

            # =================================================
            # MACD
            # =================================================

            if d["macd_cross_up"]:
                score += 2
                reasons.append("MACD alcista")

            # =================================================
            # VOLUMEN
            # =================================================

            if d["vol_rel"] >= 1.8:
                score += 2
                reasons.append(f"Volumen {d['vol_rel']}x")

            # =================================================
            # MOMENTUM
            # =================================================

            if d["d1"] > 2:
                score += 1
                reasons.append(f"Momentum {d['d1']}%")

            # =================================================
            # SIGNAL
            # =================================================

            if score >= 6:

                entry = round(d["price"], 2)

                tp1 = round(entry * 1.03, 2)

                tp2 = round(entry * 1.06, 2)

                sl = round(entry * 0.98, 2)

                signals.append({
                    "type": "INTRADAY",
                    "ticker": d["ticker"],
                    "price": d["price"],
                    "entry": entry,
                    "tp1": tp1,
                    "tp2": tp2,
                    "sl": sl,
                    "score": score,
                    "reasons": reasons,
                    "rsi": d["rsi"],
                    "vol_rel": d["vol_rel"],
                })

        except Exception as e:

            log.warning(f"intraday signal error: {e}")

    signals.sort(
        key=lambda x: x["score"],
        reverse=True
    )

    return signals


# =========================================================
# SWING SIGNALS
# =========================================================

def swing_signals(tickers):

    data = fetch_multiple(
        tickers,
        period="3mo"
    )

    signals = []

    for d in data:

        try:

            score = 0
            reasons = []

            # =================================================
            # RECUPERACIÓN
            # =================================================

            dist_low = (
                (d["price"] - d["lo52"])
                / d["lo52"]
            ) * 100

            if dist_low > 20:
                score += 2
                reasons.append("Recuperación fuerte")

            # =================================================
            # RSI
            # =================================================

            if 45 <= d["rsi"] <= 65:
                score += 2
                reasons.append(f"RSI saludable ({d['rsi']})")

            # =================================================
            # EMA20
            # =================================================

            if d["sobre_ema20"]:
                score += 2
                reasons.append("Sobre EMA20")

            # =================================================
            # MACD
            # =================================================

            if d["macd_cross_up"]:
                score += 2
                reasons.append("MACD alcista")

            # =================================================
            # VOLUMEN
            # =================================================

            if d["vol_rel"] >= 1.5:
                score += 1
                reasons.append(f"Volumen {d['vol_rel']}x")

            # =================================================
            # SEMANA
            # =================================================

            if d["d5"] > 5:
                score += 1
                reasons.append(f"Semana fuerte {d['d5']}%")

            # =================================================
            # SIGNAL
            # =================================================

            if score >= 6:

                entry = round(d["price"], 2)

                tp1 = round(entry * 1.08, 2)

                tp2 = round(entry * 1.15, 2)

                sl = round(entry * 0.94, 2)

                signals.append({
                    "type": "SWING",
                    "ticker": d["ticker"],
                    "price": d["price"],
                    "entry": entry,
                    "tp1": tp1,
                    "tp2": tp2,
                    "sl": sl,
                    "score": score,
                    "reasons": reasons,
                    "rsi": d["rsi"],
                    "vol_rel": d["vol_rel"],
                })

        except Exception as e:

            log.warning(f"swing signal error: {e}")

    signals.sort(
        key=lambda x: x["score"],
        reverse=True
    )

    return signals


# =========================================================
# FORMAT SIGNAL
# =========================================================

def format_signal(signal):

    lines = []

    emoji = "🔥" if signal["type"] == "INTRADAY" else "🚀"

    lines.append(
        f"{emoji} {signal['type']} SIGNAL"
    )

    lines.append("")

    lines.append(
        f"{signal['ticker']}"
    )

    lines.append(
        f"Entrada: {signal['entry']}"
    )

    lines.append(
        f"TP1: {signal['tp1']}"
    )

    lines.append(
        f"TP2: {signal['tp2']}"
    )

    lines.append(
        f"SL: {signal['sl']}"
    )

    lines.append("")

    lines.append(
        f"Score: {signal['score']}/10"
    )

    lines.append("")

    for r in signal["reasons"]:

        lines.append(f"• {r}")

    return "\n".join(lines)


# =========================================================
# TEST
# =========================================================

if __name__ == "__main__":

    TEST_TICKERS = [
        "NVDA",
        "PLTR",
        "RKLB",
        "ASTS",
        "SMCI",
        "AMD",
    ]

    print("\n======================")
    print("INTRADAY")
    print("======================\n")

    intraday = intraday_signals(TEST_TICKERS)

    for s in intraday[:3]:

        print(format_signal(s))
        print("\n-----------------\n")

    print("\n======================")
    print("SWING")
    print("======================\n")

    swing = swing_signals(TEST_TICKERS)

    for s in swing[:3]:

        print(format_signal(s))
        print("\n-----------------\n")