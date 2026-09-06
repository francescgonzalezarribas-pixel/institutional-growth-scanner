import os
import pytz
import requests
import feedparser
import yfinance as yf
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

# --- CONFIGURACIÓN Y VARIABLES DE ENTORNO ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
MISTRAL_API_KEY = os.environ.get("MISTRAL_API_KEY")
ALLOWED_USER_ID = int(os.environ.get("ALLOWED_USER_ID", 0))
MADRID_TZ = pytz.timezone("Europe/Madrid")

bot = telebot.TeleBot(TELEGRAM_TOKEN)


# --- FILTRO DE SEGURIDAD ---
def is_authorized(user_id: int) -> bool:
    if ALLOWED_USER_ID == 0:
        return True
    return user_id == ALLOWED_USER_ID


# --- FUNCIÓN IA (HTTP DIRECTO - SIN ERRORES DE LIBRERÍA) ---
def ask_mistral(
    prompt: str,
    system_prompt: str = "Eres un analista financiero experto en mercados y activos de inversión.",
) -> str:
    if not MISTRAL_API_KEY:
        return "⚠️ Error: API Key de Mistral no configurada."

    url = "https://api.mistral.ai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {MISTRAL_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "mistral-small-latest",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=25)
        data = response.json()
        if "choices" in data and len(data["choices"]) > 0:
            return data["choices"][0]["message"]["content"]
        else:
            print(f"Error respuesta Mistral: {data}")
            return "⚠️ No se pudo generar la respuesta de la IA."
    except Exception as e:
        print(f"Error conexión Mistral: {e}")
        return "⚠️ Error al conectar con el servicio de IA."


# --- TECLADO PRINCIPAL ---
def get_main_keyboard():
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("📰 Noticias Impacto", callback_data="noticias"),
        InlineKeyboardButton(
            "🔍 Investigación Ticker", callback_data="investigar_help"
        ),
        InlineKeyboardButton(
            "🔮 Previsiones Mercado", callback_data="previsiones"
        ),
        InlineKeyboardButton(
            "💎 Índice Valor BTC", callback_data="indice_valor_btc"
        ),
    )
    return markup


# --- HANDLERS COMANDOS INICIALES ---
@bot.message_handler(commands=["start", "menu"])
def send_welcome(message):
    if not is_authorized(message.from_user.id):
        return
    text = (
        "🤖 **Bot de Investigación, Noticias y Previsiones Financieras**\n\n"
        "Selecciona una opción del menú o usa las funciones directas:\n"
        "• `/investigar TICKER` - Análisis fundamental y técnico con IA\n"
        "• `/noticias` - Titulares clave e impacto de mercado\n"
        "• `/previsiones` - Perspectivas y proyecciones macro\n"
        "• `/btc` - Índice de Valor y métricas de Bitcoin\n\n"
        "_O escribe cualquier duda financiera directamente._"
    )
    bot.reply_to(
        message, text, parse_mode="Markdown", reply_markup=get_main_keyboard()
    )


# --- HANDLERS BOTONES DE TELEGRAM ---
@bot.callback_query_handler(func=lambda call: True)
def handle_query(call):
    if not is_authorized(call.from_user.id):
        return

    if call.data == "noticias":
        bot.answer_callback_query(call.id, "Analizando noticias...")
        send_noticias(call.message)
    elif call.data == "investigar_help":
        bot.answer_callback_query(call.id)
        bot.send_message(
            call.message.chat.id,
            "🔍 Envíame el ticker que deseas investigar.\n\nEjemplo: `/investigar NVDA` o `/investigar AAPL`",
            parse_mode="Markdown",
        )
    elif call.data == "previsiones":
        bot.answer_callback_query(call.id, "Generando previsiones...")
        send_previsiones(call.message)
    elif call.data == "indice_valor_btc":
        bot.answer_callback_query(call.id, "Calculando Índice Valor BTC...")
        send_indice_valor_btc(call.message)


# --- MÓDULO 1: NOTICIAS DE IMPACTO ---
@bot.message_handler(commands=["noticias"])
def send_noticias(message):
    if not is_authorized(message.from_user.id):
        return
    bot.send_chat_action(message.chat.id, "typing")

    rss_urls = [
        "https://search.cnbc.com/rs/search/combinedrenderer/view.xml?partnerId=2000&keywords=markets&target=all",
        "https://feeds.finance.yahoo.com/rss/2.0/headline?s=^GSPC&region=US&lang=en-US",
    ]

    headlines = []
    for url in rss_urls:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:3]:
                headlines.append(f"- {entry.title}")
        except Exception as e:
            print(f"Error cargando RSS {url}: {e}")

    raw_news = (
        "\n".join(headlines)
        if headlines
        else "No se pudieron obtener titulares en tiempo real."
    )

    prompt = f"Sintetiza estas noticias financieras destacando los 3 puntos clave de mayor impacto para los inversores:\n\n{raw_news}"
    analysis = ask_mistral(
        prompt, "Eres un analista de noticias de mercados financieros."
    )

    response = f"📰 **NOTICIAS DE IMPACTO EN EL MERCADO**\n\n{analysis}"
    bot.send_message(
        message.chat.id,
        response,
        parse_mode="Markdown",
        reply_markup=get_main_keyboard(),
    )


# --- MÓDULO 2: INVESTIGACIÓN DE TICKER ---
@bot.message_handler(commands=["investigar"])
def send_investigacion(message):
    if not is_authorized(message.from_user.id):
        return

    args = message.text.split()
    if len(args) < 2:
        bot.reply_to(
            message,
            "⚠️ Especifica un ticker. Ejemplo: `/investigar TSLA`",
            parse_mode="Markdown",
        )
        return

    ticker = args[1].upper()
    bot.send_chat_action(message.chat.id, "typing")

    try:
        stock = yf.Ticker(ticker)
        info = stock.info

        name = info.get("shortName", ticker)
        price = info.get("currentPrice") or info.get(
            "regularMarketPrice", "N/D"
        )
        pe_ratio = info.get("forwardPE", "N/D")
        market_cap = info.get("marketCap", "N/D")
        target_price = info.get("targetMeanPrice", "N/D")

        datos_str = (
            f"Empresa: {name} ({ticker})\n"
            f"Precio actual: ${price}\n"
            f"P/E Forward: {pe_ratio}\n"
            f"Market Cap: {market_cap}\n"
            f"Precio Objetivo Analistas: ${target_price}"
        )

        prompt = (
            f"Realiza un informe de investigación resumido basándote en estos datos:\n{datos_str}\n\n"
            f"Estructura la respuesta en: 1. Diagnóstico Fundamental, 2. Factores de Riesgo/Oportunidad, 3. Valoración Final."
        )
        analysis = ask_mistral(prompt)

        msg = f"🔍 **INFORME DE INVESTIGACIÓN: {ticker}**\n\n{analysis}"
        bot.send_message(
            message.chat.id,
            msg,
            parse_mode="Markdown",
            reply_markup=get_main_keyboard(),
        )
    except Exception as e:
        bot.reply_to(message, f"⚠️ Error obteniendo datos de {ticker}: {e}")


# --- MÓDULO 3: PREVISIONES DE MERCADO ---
@bot.message_handler(commands=["previsiones"])
def send_previsiones(message):
    if not is_authorized(message.from_user.id):
        return
    bot.send_chat_action(message.chat.id, "typing")

    try:
        sp500 = yf.Ticker("^GSPC")
        hist = sp500.history(period="1mo")
        precio_actual = hist["Close"].iloc[-1]
        var_mes = (
            (precio_actual - hist["Close"].iloc[0]) / hist["Close"].iloc[0]
        ) * 100

        prompt = (
            f"El S&P 500 cotiza en {precio_actual:.2f} con una variación del {var_mes:.2f}% en el último mes.\n"
            f"Ofrece un análisis de previsión a corto y medio plazo contemplando el contexto macroeconómico y los posibles escenarios técnicos."
        )
        forecast = ask_mistral(prompt)

        response = f"🔮 **PREVISIONES Y PERSPECTIVAS DE MERCADO**\n\n{forecast}"
        bot.send_message(
            message.chat.id,
            response,
            parse_mode="Markdown",
            reply_markup=get_main_keyboard(),
        )
    except Exception as e:
        bot.send_message(message.chat.id, f"⚠️ Error generando previsiones: {e}")


# --- MÓDULO 4: ÍNDICE VALOR BITCOIN (BTC) ---
@bot.message_handler(commands=["btc", "indice_valor"])
def send_indice_valor_btc(message):
    if not is_authorized(message.from_user.id):
        return
    bot.send_chat_action(message.chat.id, "typing")

    try:
        btc = yf.Ticker("BTC-USD")
        hist = btc.history(period="1y")
        precio_actual = hist["Close"].iloc[-1]
        sma_200 = hist["Close"].rolling(window=200).mean().iloc[-1]

        ratio = precio_actual / sma_200

        if ratio < 1.0:
            zona = "🟢 Infravalorado / Zona de Oportunidad"
            puntuacion = 85
        elif 1.0 <= ratio < 1.5:
            zona = "🟡 Zona Neutra / Acumulación"
            puntuacion = 55
        else:
            zona = "🔴 Sobrevalorado / Riesgo Elevado"
            puntuacion = 25

        prompt = (
            f"El precio de Bitcoin es ${precio_actual:,.2f} y su Media Móvil de 200 días es ${sma_200:,.2f} (Ratio {ratio:.2f}).\n"
            f"Explica brevemente qué implica este nivel de valoración y cuál es la estrategia recomendada para un inversor a largo plazo."
        )
        ia_opinion = ask_mistral(
            prompt,
            "Eres un analista experto en la valoración fundamental de Bitcoin.",
        )

        response = (
            f"💎 **ÍNDICE VALOR BITCOIN (BTC)**\n\n"
            f"• **Precio Actual:** ${precio_actual:,.2f}\n"
            f"• **SMA 200 Días:** ${sma_200:,.2f}\n"
            f"• **Ratio de Valoración:** `{ratio:.2f}`\n"
            f"• **Estado:** {zona}\n"
            f"• **Puntuación de Valor:** `{puntuacion}/100`\n\n"
            f"🧠 **Análisis de Valoración (IA):**\n{ia_opinion}"
        )
        bot.send_message(
            message.chat.id,
            response,
            parse_mode="Markdown",
            reply_markup=get_main_keyboard(),
        )
    except Exception as e:
        bot.send_message(
            message.chat.id, f"⚠️ Error calculando el Índice Valor BTC: {e}"
        )


# --- MANEJADOR DE CONSULTAS LIBRES ---
@bot.message_handler(func=lambda msg: True)
def handle_free_question(message):
    if not is_authorized(message.from_user.id):
        return
    bot.send_chat_action(message.chat.id, "typing")
    answer = ask_mistral(message.text)
    bot.reply_to(
        message,
        answer,
        parse_mode="Markdown",
        reply_markup=get_main_keyboard(),
    )


# --- ARRANQUE DEL BOT ---
if __name__ == "__main__":
    print("🤖 Bot financiero iniciado correctamente...")
    bot.infinity_polling(skip_pending=True)