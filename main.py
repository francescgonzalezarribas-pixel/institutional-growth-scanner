"""
Financial Telegram Bot — 100% GRATIS
IA: Google Gemini 1.5 Flash — 1.500 req/día gratis
Datos: yfinance (Yahoo Finance) — ilimitado gratis
Noticias: RSS feeds (MarketWatch, Reuters, Google News) — gratis
Deploy: Railway free tier
"""

import os
import logging
import feedparser
import yfinance as yf
import google.generativeai as genai
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes, CallbackQueryHandler
)

# ── Config ────────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]

SYSTEM = """Eres un analista financiero senior. Reglas:
- Responde SIEMPRE en español
- Usa emojis para separar secciones (📌 🔴 🟢 ⚠️ 🎯)
- Usa *negrita* con asteriscos para datos clave
- Máximo 4 párrafos o listas cortas
- Da conclusiones concretas y accionables
- No das consejos de inversión pero sí análisis objetivo con sesgo claro"""

genai.configure(api_key=GEMINI_API_KEY)
gemini_model = genai.GenerativeModel(
    model_name="gemini-2.0-flash-lite",
    system_instruction=SYSTEM
)

# ── RSS feeds gratuitos ───────────────────────────────────────────────────────
RSS_FEEDS = [
    "https://feeds.marketwatch.com/marketwatch/topstories/",
    "https://news.google.com/rss/search?q=stock+market+europe+usa&hl=en&gl=US&ceid=US:en",
    "https://feeds.reuters.com/reuters/businessNews",
    "https://finance.yahoo.com/news/rssindex",
]

# ── Universo de activos ───────────────────────────────────────────────────────
INDICES = {
    "^GSPC":     "S&P 500",
    "^DJI":      "Dow Jones",
    "^IXIC":     "Nasdaq",
    "^STOXX50E": "Euro Stoxx 50",
    "^GDAXI":    "DAX",
    "^FTSE":     "FTSE 100",
}
US_STOCKS = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "JPM", "GS"]
EU_STOCKS = ["ASML", "SAP", "SIE.DE", "TTE.PA", "LVMH.PA", "NESN.SW"]
CRYPTO    = ["BTC-USD", "ETH-USD", "SOL-USD"]

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

# ── Helpers ───────────────────────────────────────────────────────────────────
def ask_ai(prompt: str) -> str:
    """Llama a Google Gemini 1.5 Flash — gratis."""
    try:
        resp = gemini_model.generate_content(prompt)
        return resp.text
    except Exception as e:
        log.error(f"Gemini error: {e}")
        return "⚠️ Error IA. Intenta en unos segundos."


def get_news(max_items: int = 12) -> list[str]:
    """Titulares de RSS feeds — completamente gratis."""
    titulares = []
    for url in RSS_FEEDS:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:4]:
                title = entry.get("title", "").strip()
                if title and title not in titulares:
                    titulares.append(title)
            if len(titulares) >= max_items:
                break
        except Exception as e:
            log.warning(f"RSS error {url}: {e}")
    return titulares[:max_items]


def fetch_quote(ticker: str, period: str = "3mo") -> dict | None:
    """Datos OHLCV de Yahoo Finance + cálculo S/R — gratis."""
    try:
        hist = yf.Ticker(ticker).history(period=period)
        if hist.empty or len(hist) < 5:
            return None

        c, h, lo = hist["Close"], hist["High"], hist["Low"]
        price    = c.iloc[-1]
        change1d = (price - c.iloc[-2]) / c.iloc[-2] * 100
        change5d = (price - c.iloc[-6]) / c.iloc[-6] * 100 if len(c) > 5 else 0

        pivot = (h.iloc[-1] + lo.iloc[-1] + c.iloc[-1]) / 3
        r1 = 2 * pivot - lo.iloc[-1]
        s1 = 2 * pivot - h.iloc[-1]

        hi20, lo20 = h.tail(20).max(), lo.tail(20).min()
        rng = hi20 - lo20
        r2  = hi20 + rng * 0.382
        s2  = lo20 - rng * 0.382

        return {
            "ticker": ticker,
            "price":  round(price, 2),
            "d1":     round(change1d, 2),
            "d5":     round(change5d, 2),
            "pivot":  round(pivot, 2),
            "r1":     round(r1, 2),
            "r2":     round(r2, 2),
            "s1":     round(s1, 2),
            "s2":     round(s2, 2),
            "hi52":   round(h.tail(252).max(), 2),
            "lo52":   round(lo.tail(252).min(), 2),
        }
    except Exception as e:
        log.warning(f"fetch_quote {ticker}: {e}")
        return None


def arrow(val: float) -> str:
    return f"{'🟢' if val >= 0 else '🔴'} {val:+.2f}%"

# ── Handlers ──────────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("📰 Noticias",     callback_data="noticias"),
         InlineKeyboardButton("📊 Mercados",      callback_data="mercados")],
        [InlineKeyboardButton("🎯 Oportunidades", callback_data="oportunidades"),
         InlineKeyboardButton("₿  Crypto",        callback_data="crypto")],
    ]
    await update.message.reply_text(
        "🏦 *Financial Bot* — Análisis EU & EEUU\n\n"
        "*/noticias* — Titulares RSS + resumen IA\n"
        "*/mercados* — Índices con variación\n"
        "*/oportunidades* — Mejores setups detectados\n"
        "*/analisis TICKER* — S/R + sesgo operativo\n"
        "*/crypto* — BTC, ETH, SOL\n"
        "✉️ Pregunta libre → IA responde\n\n"
        "Elige o escribe 👇",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(kb)
    )


async def cmd_noticias(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.effective_message.reply_text("📰 Leyendo feeds RSS…")
    titulares = get_news(12)

    if titulares:
        bloque = "\n".join(f"• {t}" for t in titulares)
        prompt = (
            f"Noticias financieras de hoy:\n{bloque}\n\n"
            "Dame:\n"
            "1. Los 3 titulares más relevantes y por qué importan\n"
            "2. Sentimiento general del mercado (alcista/bajista/mixto)\n"
            "3. Qué esperar hoy en bolsa EU y EEUU\n"
            "4. 2-3 sectores o acciones que podrían moverse"
        )
    else:
        prompt = (
            "Sin feed disponible. Basándote en el contexto macro actual:\n"
            "1. Estado general de mercados EU y EEUU\n"
            "2. Factores clave a vigilar esta semana\n"
            "3. Sectores con más momentum"
        )

    texto = ask_ai(prompt)
    await msg.edit_text(
        f"📰 *Noticias — {datetime.now().strftime('%d/%m %H:%M')}*\n\n{texto}",
        parse_mode="Markdown"
    )


async def cmd_mercados(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.effective_message.reply_text("📊 Cargando mercados…")

    lines, data_ai = [], []
    for t, nombre in INDICES.items():
        d = fetch_quote(t, "1mo")
        if d:
            lines.append(
                f"{arrow(d['d1'])} *{nombre}*: `{d['price']:,.0f}` | semana {arrow(d['d5'])}"
            )
            data_ai.append(
                f"{nombre}: {d['price']:,.0f} ({d['d1']:+.2f}% hoy, {d['d5']:+.2f}% semana)"
            )

    snap = "\n".join(lines) or "_Datos no disponibles_"
    prompt = (
        "Snapshot de índices:\n" + "\n".join(data_ai) + "\n\n"
        "Analiza:\n"
        "1. Lectura global del mercado\n"
        "2. Divergencias EU vs EEUU\n"
        "3. Qué vigilar las próximas sesiones"
    )
    analisis = ask_ai(prompt)
    await msg.edit_text(
        f"📊 *Mercados — {datetime.now().strftime('%d/%m %H:%M')}*\n\n{snap}\n\n{analisis}",
        parse_mode="Markdown"
    )


async def cmd_oportunidades(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.effective_message.reply_text("🎯 Escaneando oportunidades…")

    rows = []
    for t in US_STOCKS[:5] + EU_STOCKS[:4]:
        d = fetch_quote(t, "3mo")
        if d:
            dist_hi = (d["price"] - d["hi52"]) / d["hi52"] * 100
            dist_lo = (d["price"] - d["lo52"]) / d["lo52"] * 100
            rows.append(
                f"{t}: precio={d['price']}, 1d={d['d1']}%, 5d={d['d5']}%, "
                f"S1={d['s1']}, R1={d['r1']}, "
                f"dist_52hi={dist_hi:.1f}%, dist_52lo={dist_lo:.1f}%"
            )

    prompt = (
        "Datos técnicos acciones EU + EEUU:\n" + "\n".join(rows) + "\n\n"
        "Identifica:\n"
        "1. 2-3 acciones con mejor setup técnico y por qué\n"
        "2. Acciones a evitar ahora mismo\n"
        "3. Un trade concreto: entrada, objetivo, stop\n"
        "4. Nivel de riesgo de mercado 1-10"
    )
    texto = ask_ai(prompt)
    await msg.edit_text(f"🎯 *Oportunidades Detectadas*\n\n{texto}", parse_mode="Markdown")


async def cmd_analisis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "Uso: `/analisis TICKER`\n"
            "Ej: `/analisis AAPL` · `/analisis ASML` · `/analisis SIE.DE`",
            parse_mode="Markdown"
        )
        return

    ticker = context.args[0].upper()
    msg = await update.message.reply_text(f"🔍 Analizando *{ticker}*…", parse_mode="Markdown")

    d = fetch_quote(ticker, "6mo")
    if not d:
        await msg.edit_text(
            f"❌ Sin datos para `{ticker}`.\nEjemplos: `AAPL`, `ASML`, `SIE.DE`, `^GSPC`",
            parse_mode="Markdown"
        )
        return

    prompt = (
        f"Acción: {ticker}\n"
        f"Precio: {d['price']} | 1d: {d['d1']}% | 5d: {d['d5']}%\n"
        f"Máx52: {d['hi52']} | Mín52: {d['lo52']}\n"
        f"Pivot: {d['pivot']} | R1: {d['r1']} | R2: {d['r2']} | S1: {d['s1']} | S2: {d['s2']}\n\n"
        "Proporciona:\n"
        "1. Posición técnica actual (tendencia, momentum)\n"
        "2. Niveles clave y qué significan\n"
        "3. Escenario alcista vs bajista con precios\n"
        "4. Sesgo operativo claro (comprar/vender/esperar)\n"
        "5. Ratio riesgo/recompensa si hay zona de entrada"
    )
    texto = ask_ai(prompt)

    header = (
        f"🔍 *{ticker}*\n"
        f"💰 `{d['price']}` | Hoy: {arrow(d['d1'])} | Semana: {arrow(d['d5'])}\n"
        f"🔴 R2 `{d['r2']}` › R1 `{d['r1']}`\n"
        f"⚪ Pivot `{d['pivot']}`\n"
        f"🟢 S1 `{d['s1']}` › S2 `{d['s2']}`\n"
        f"📅 Rango 52s: `{d['lo52']}` — `{d['hi52']}`\n\n"
    )
    await msg.edit_text(header + texto, parse_mode="Markdown")


async def cmd_crypto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.effective_message.reply_text("₿ Cargando crypto…")

    nombres = {"BTC-USD": "Bitcoin", "ETH-USD": "Ethereum", "SOL-USD": "Solana"}
    lines, data_ai = [], []
    for t in CRYPTO:
        d = fetch_quote(t, "1mo")
        if d:
            n = nombres[t]
            lines.append(
                f"{arrow(d['d1'])} *{n}*: `{d['price']:,.0f}` | semana {arrow(d['d5'])}"
            )
            data_ai.append(
                f"{n}: {d['price']:,.0f} ({d['d1']:+.2f}% hoy) S1={d['s1']} R1={d['r1']}"
            )

    prompt = (
        "Datos crypto:\n" + "\n".join(data_ai) + "\n\n"
        "1. Lectura técnica global del mercado crypto\n"
        "2. Par con mejor setup ahora\n"
        "3. Niveles críticos de BTC esta semana"
    )
    texto = ask_ai(prompt)
    await msg.edit_text(
        f"₿ *Crypto — {datetime.now().strftime('%H:%M')}*\n\n"
        + "\n".join(lines) + f"\n\n{texto}",
        parse_mode="Markdown"
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    resp = ask_ai(
        f"Pregunta del usuario: {update.message.text}\n\nResponde como analista financiero."
    )
    await update.message.reply_text(resp, parse_mode="Markdown")


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    handlers = {
        "noticias":      cmd_noticias,
        "mercados":      cmd_mercados,
        "oportunidades": cmd_oportunidades,
        "crypto":        cmd_crypto,
    }
    fn = handlers.get(q.data)
    if fn:
        await fn(update, context)


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start",         start))
    app.add_handler(CommandHandler("noticias",      cmd_noticias))
    app.add_handler(CommandHandler("mercados",      cmd_mercados))
    app.add_handler(CommandHandler("oportunidades", cmd_oportunidades))
    app.add_handler(CommandHandler("analisis",      cmd_analisis))
    app.add_handler(CommandHandler("crypto",        cmd_crypto))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    log.info("🤖 Financial Bot arrancado (Gemini + yfinance + RSS)")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()