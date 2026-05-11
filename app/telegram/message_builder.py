from app.utils.company_names import (
    COMPANY_NAMES
)


def build_signal(data):

    conviction = round(
        data["score"] / 10,
        1
    )

    if conviction >= 9.5:
        score_display = "🟢🟢"

    elif conviction >= 9.0:
        score_display = "🟢"

    elif conviction >= 8.5:
        score_display = "🔵🔵"

    elif conviction >= 8.0:
        score_display = "🔵"

    elif conviction >= 7.5:
        score_display = "🟡🟡"

    else:
        score_display = "🟡"

    company_name = COMPANY_NAMES.get(
        data["symbol"],
        data["symbol"]
    )

    symbol = data["symbol"]

    # =========================
    # REGIÓN
    # =========================

    if (
        ".T" in symbol
        or ".KS" in symbol
        or ".TW" in symbol
    ):

        region = "🌏 Asia"

    elif (
        ".PA" in symbol
        or ".DE" in symbol
        or ".AS" in symbol
        or ".L" in symbol
    ):

        region = "🇪🇺 Europa"

    else:

        region = "🇺🇸 USA"

    return f"""
🟢 <b>SEÑAL LONG — ALTA CONVICCIÓN</b>

🏢 <b>Empresa:</b>
{company_name} ({symbol})

🌍 <b>Región:</b>
{region}

📈 <b>Sector:</b> {data['sector']}

━━━━━━━━━━━━━━━━━━

📍 <b>Precio actual:</b>
{data['price']:.2f}

📊 <b>Volumen relativo:</b>
x{data['relative_volume']}

━━━━━━━━━━━━━━━━━━

🔥 <b>Qué detecta el sistema:</b>

• Volumen anormal
• Momentum fuerte
• Posible entrada institucional
• Breakout técnico
• Catalizador activo

━━━━━━━━━━━━━━━━━━

📰 <b>Noticia clave:</b>

{data['headline']}

━━━━━━━━━━━━━━━━━━

{score_display} <b>Convicción:</b>
{conviction} / 10
"""