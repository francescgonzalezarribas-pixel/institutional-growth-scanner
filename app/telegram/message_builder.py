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

{score_display} <b>Convicción:</b>
{conviction} / 10
"""