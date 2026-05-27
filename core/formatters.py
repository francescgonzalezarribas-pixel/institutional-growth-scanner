# core/formatters.py

from datetime import datetime


def fmt_pct(v):
    try:
        return f"{v:+.2f}%"
    except:
        return "N/A"


def fmt_price(v):
    try:
        if v >= 1000:
            return f"{v:,.0f}"
        return f"{v:,.2f}"
    except:
        return "N/A"


def signal_strength(score):
    if score >= 9:
        return "🔥 MUY FUERTE"
    elif score >= 7:
        return "🚀 FUERTE"
    elif score >= 5:
        return "⚡ MEDIA"
    return "⚪ NORMAL"


def rr(entry, stop, tp):
    try:
        risk = abs(entry - stop)
        reward = abs(tp - entry)
        if risk == 0:
            return 0
        return round(reward / risk, 2)
    except:
        return 0


def format_signal(s):
    """
    Formatea una señal para Telegram
    """

    score_txt = signal_strength(s.get("score", 0))

    entrada = s.get("entry")
    stop = s.get("sl")
    tp1 = s.get("tp1")
    tp2 = s.get("tp2")

    rr1 = rr(entrada, stop, tp1)
    rr2 = rr(entrada, stop, tp2)

    motivos = s.get("reasons", [])

    motivos_txt = ""
    for r in motivos:
        motivos_txt += f"• {r}\n"

    tipo = s.get("type", "INTRADIA")

    texto = (
        f"{score_txt}\n\n"

        f"📈 {s['name']} ({s['ticker']})\n"
        f"━━━━━━━━━━━━━━━\n"

        f"💰 Precio: {fmt_price(s['price'])}\n"
        f"🎯 Entrada: {fmt_price(entrada)}\n"
        f"🛑 Stop: {fmt_price(stop)}\n"
        f"✅ TP1: {fmt_price(tp1)}\n"
        f"🚀 TP2: {fmt_price(tp2)}\n\n"

        f"📊 RSI: {s.get('rsi')}\n"
        f"📉 MACD: {'ALCISTA' if s.get('macd') else 'NEUTRO'}\n"
        f"📦 Volumen: {s.get('vol_rel')}x\n"
        f"📍 VWAP: {'SOBRE' if s.get('above_vwap') else 'BAJO'}\n\n"

        f"⚖️ R/R TP1: {rr1}\n"
        f"⚖️ R/R TP2: {rr2}\n\n"

        f"🧠 Motivos:\n{motivos_txt}\n"

        f"⏰ {tipo} | {datetime.now().strftime('%d/%m %H:%M')}"
    )

    return texto


def format_intradia_header(signals, market="US"):
    now = datetime.now().strftime("%d/%m %H:%M")

    if market == "EU":
        flag = "🇪🇺"
        name = "EUROPA"
    else:
        flag = "🇺🇸"
        name = "EEUU"

    return (
        f"{flag} SCANNER INTRADÍA {name}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"⏰ {now}\n"
        f"📊 Señales encontradas: {len(signals)}\n"
    )


def format_swing_header(signals, market="US"):
    now = datetime.now().strftime("%d/%m %H:%M")

    if market == "EU":
        flag = "🇪🇺"
        name = "EUROPA"
    else:
        flag = "🇺🇸"
        name = "EEUU"

    return (
        f"{flag} SWING SCANNER {name}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"⏰ {now}\n"
        f"📈 Oportunidades swing: {len(signals)}\n"
    )