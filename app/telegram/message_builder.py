def build_signal(data):

    conviction = round(
        data["score"] / 10,
        1
    )

    if conviction >= 9:
        score_icon = "🟢"

    elif conviction >= 8:
        score_icon = "🔵"

    elif conviction >= 7:
        score_icon = "🟡"

    else:
        score_icon = "⚪"

    return f"""
🟢 <b>SEÑAL LONG — ALTA CONVICCIÓN</b>

🏢 <b>Empresa:</b> {data['symbol']}
🌍 <b>Mercado:</b> NASDAQ
📈 <b>Sector:</b> {data['sector']}

━━━━━━━━━━━━━━━━━━

📍 <b>Precio actual:</b>
{data['price']:.2f} USD

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

{score_icon} <b>Convicción:</b>
{conviction} / 10
"""