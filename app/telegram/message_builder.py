def build_signal(data):

    conviction = round(data["score"] / 10, 1)

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

⭐ <b>Convicción:</b>
{conviction} / 10
"""