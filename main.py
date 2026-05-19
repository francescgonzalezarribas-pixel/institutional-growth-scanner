"""
Financial Telegram Bot — 100% GRATIS
IA: Google Gemini 2.0 Flash Lite
Datos: yfinance (Yahoo Finance)
Noticias: RSS feeds
Calendario: ForexFactory (gratis)
Alertas: APScheduler
"""

import os
import logging
import feedparser
import yfinance as yf
import requests
import pytz
import asyncio
import google.generativeai as genai
from datetime import datetime, time
from apscheduler.schedulers.background import BackgroundScheduler
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes, CallbackQueryHandler
)

# ── Config ────────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN  = os.environ["TELEGRAM_TOKEN"]
GEMINI_API_KEY  = os.environ["GEMINI_API_KEY"]
ALLOWED_USER_ID = int(os.environ.get("ALLOWED_USER_ID", 0))
MADRID          = pytz.timezone("Europe/Madrid")

SYSTEM = """Eres un analista financiero senior. Reglas:
- Responde SIEMPRE en español
- Usa emojis para separar secciones (📌 🔴 🟢 ⚠️ 🎯 💥)
- Usa *negrita* con asteriscos para datos clave
- Máximo 4 párrafos o listas cortas
- Da conclusiones concretas y accionables
- No das consejos de inversión pero sí análisis objetivo con sesgo claro"""

genai.configure(api_key=GEMINI_API_KEY)
gemini_model = genai.GenerativeModel(
    model_name="gemini-2.0-flash-lite",
    system_instruction=SYSTEM
)

# ── RSS feeds ─────────────────────────────────────────────────────────────────
RSS_FEEDS = [
    "https://feeds.marketwatch.com/marketwatch/topstories/",
    "https://news.google.com/rss/search?q=stock+market+europe+usa&hl=en&gl=US&ceid=US:en",
    "https://feeds.reuters.com/reuters/businessNews",
    "https://finance.yahoo.com/news/rssindex",
]

# ── Universo de activos ───────────────────────────────────────────────────────
INDICES = {
    "^GSPC": "S&P 500", "^DJI": "Dow Jones", "^IXIC": "Nasdaq",
    "^STOXX50E": "Euro Stoxx 50", "^GDAXI": "DAX", "^FTSE": "FTSE 100",
}
US_STOCKS  = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "JPM", "GS", "XOM"]
EU_STOCKS  = ["ASML", "SAP", "SIE.DE", "TTE.PA", "LVMH.PA", "NESN.SW", "NOVO-B.CO"]
CRYPTO     = ["BTC-USD", "ETH-USD", "SOL-USD"]
ALL_STOCKS = US_STOCKS + EU_STOCKS

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

# ── Helpers ───────────────────────────────────────────────────────────────────
def is_allowed(update: Update) -> bool:
    if ALLOWED_USER_ID == 0:
        return True
    return update.effective_user.id == ALLOWED_USER_ID


def ask_ai(prompt: str) -> str:
    try:
        resp = gemini_model.generate_content(prompt)
        return resp.text
    except Exception as e:
        log.error(f"Gemini error: {e}")
        return "⚠️ Error IA. Intenta en unos segundos."


def get_news(max_items: int = 10) -> list:
    titulares = []
    for url in RSS_FEEDS:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:3]:
                title = entry.get("title", "").strip()
                if title and title not in titulares:
                    titulares.append(title)
            if len(titulares) >= max_items:
                break
        except Exception as e:
            log.warning(f"RSS error: {e}")
    return titulares[:max_items]


def fetch_quote(ticker: str, period: str = "3mo") -> dict:
    try:
        tk   = yf.Ticker(ticker)
        hist = tk.history(period=period)
        if hist.empty or len(hist) < 10:
            return None
        c, h, lo = hist["Close"], hist["High"], hist["Low"]
        vol      = hist["Volume"]
        price    = c.iloc[-1]
        change1d = (price - c.iloc[-2]) / c.iloc[-2] * 100
        change5d = (price - c.iloc[-6]) / c.iloc[-6] * 100 if len(c) > 5 else 0
        pivot = (h.iloc[-1] + lo.iloc[-1] + c.iloc[-1]) / 3
        r1    = 2 * pivot - lo.iloc[-1]
        s1    = 2 * pivot - h.iloc[-1]
        hi20, lo20 = h.tail(20).max(), lo.tail(20).min()
        rng   = hi20 - lo20
        r2    = hi20 + rng * 0.382
        s2    = lo20 - rng * 0.382
        avg_vol = vol.tail(20).mean()
        vol_rel = (vol.iloc[-1] / avg_vol) if avg_vol > 0 else 1.0
        hi52 = h.tail(252).max()
        lo52 = lo.tail(252).min()
        return {
            "ticker": ticker, "price": round(price, 2),
            "d1": round(change1d, 2), "d5": round(change5d, 2),
            "pivot": round(pivot, 2),
            "r1": round(r1, 2), "r2": round(r2, 2),
            "s1": round(s1, 2), "s2": round(s2, 2),
            "hi52": round(hi52, 2), "lo52": round(lo52, 2),
            "vol_rel": round(vol_rel, 2),
        }
    except Exception as e:
        log.warning(f"fetch_quote {ticker}: {e}")
        return None


def get_economic_calendar() -> list:
    try:
        url  = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
        resp = requests.get(url, timeout=8)
        data = resp.json()
        high = [e for e in data if e.get("impact") == "High"]
        return high[:15]
    except Exception as e:
        log.warning(f"Calendar error: {e}")
        return []


def scan_sr_alerts(stocks: list) -> list:
    alerts = []
    for t in stocks:
        d = fetch_quote(t, "3mo")
        if not d:
            continue
        p = d["price"]
        for nivel, nombre in [(d["s1"], "S1"), (d["s2"], "S2"),
                               (d["r1"], "R1"), (d["r2"], "R2")]:
            dist_pct = abs(p - nivel) / nivel * 100
            if dist_pct <= 1.5:
                tipo = "🟢 SOPORTE" if "S" in nombre else "🔴 RESIST."
                alerts.append(
                    f"{tipo} *{t}* cerca de {nombre} `{nivel}` "
                    f"— precio `{p}` ({dist_pct:.1f}% distancia)"
                )
    return alerts


def scan_explosions(stocks: list) -> list:
    candidates = []
    for t in stocks:
        d = fetch_quote(t, "3mo")
        if not d:
            continue
        dist_hi52 = (d["hi52"] - d["price"]) / d["hi52"] * 100
        if d["vol_rel"] >= 1.8 and d["d5"] >= 3.0 and dist_hi52 <= 8.0:
            candidates.append({
                "ticker": t, "d5": d["d5"], "vol_rel": d["vol_rel"],
                "dist_hi52": dist_hi52, "price": d["price"], "r1": d["r1"],
            })
    candidates.sort(key=lambda x: x["vol_rel"] * x["d5"], reverse=True)
    return candidates[:5]


def arrow(val: float) -> str:
    return f"{'🟢' if val >= 0 else '🔴'} {val:+.2f}%"

# ── Comandos ──────────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    kb = [
        [InlineKeyboardButton("📰 Noticias",      callback_data="noticias"),
         InlineKeyboardButton("📊 Mercados",       callback_data="mercados")],
        [InlineKeyboardButton("🎯 Oportunidades",  callback_data="oportunidades"),
         InlineKeyboardButton("💥 Explosiones",    callback_data="explosiones")],
        [InlineKeyboardButton("📅 Calendario",     callback_data="calendario"),
         InlineKeyboardButton("₿  Crypto",         callback_data="crypto")],
        [InlineKeyboardButton("🔔 S/R Alertas",    callback_data="sr_scan")],
    ]
    await update.message.reply_text(
        "🏦 *Financial Bot* — Análisis EU & EEUU\n\n"
        "*/noticias* — Titulares + resumen IA\n"
        "*/mercados* — Índices con variación\n"
        "*/oportunidades* — Mejores setups\n"
        "*/explosiones* — Acciones con momentum explosivo\n"
        "*/calendario* — Eventos económicos semana\n"
        "*/sr* — Scanner soportes y resistencias\n"
        "*/analisis TICKER* — Análisis completo\n"
        "*/crypto* — BTC, ETH, SOL\n"
        "✉️ Pregunta libre → IA responde\n\n"
        "Elige o escribe 👇",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(kb)
    )


async def cmd_noticias(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update): return
    msg = await update.effective_message.reply_text("📰 Leyendo feeds RSS…")
    titulares = get_news(10)
    if titulares:
        bloque = "\n".join(f"• {t}" for t in titulares)
        prompt = (
            f"Noticias financieras de hoy:\n{bloque}\n\n"
            "Dame:\n1. 3 titulares más relevantes y por qué\n"
            "2. Sentimiento general del mercado\n"
            "3. Qué esperar hoy EU y EEUU\n"
            "4. 2-3 sectores o acciones que podrían moverse"
        )
    else:
        prompt = "Estado actual mercados EU y EEUU, factores clave esta semana, sectores con momentum."
    texto = ask_ai(prompt)
    await msg.edit_text(
        f"📰 *Noticias — {datetime.now().strftime('%d/%m %H:%M')}*\n\n{texto}",
        parse_mode="Markdown"
    )


async def cmd_mercados(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update): return
    msg = await update.effective_message.reply_text("📊 Cargando mercados…")
    lines, data_ai = [], []
    for t, nombre in INDICES.items():
        d = fetch_quote(t, "1mo")
        if d:
            lines.append(f"{arrow(d['d1'])} *{nombre}*: `{d['price']:,.0f}` | semana {arrow(d['d5'])}")
            data_ai.append(f"{nombre}: {d['price']:,.0f} ({d['d1']:+.2f}% hoy, {d['d5']:+.2f}% semana)")
    snap     = "\n".join(lines) or "_Sin datos_"
    analisis = ask_ai("Snapshot:\n" + "\n".join(data_ai) + "\n\nLectura global, divergencias EU/EEUU, qué vigilar.")
    await msg.edit_text(
        f"📊 *Mercados — {datetime.now().strftime('%d/%m %H:%M')}*\n\n{snap}\n\n{analisis}",
        parse_mode="Markdown"
    )


async def cmd_oportunidades(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update): return
    msg = await update.effective_message.reply_text("🎯 Escaneando oportunidades…")
    rows = []
    for t in ALL_STOCKS[:9]:
        d = fetch_quote(t, "3mo")
        if d:
            dist_hi = (d["price"] - d["hi52"]) / d["hi52"] * 100
            rows.append(
                f"{t}: precio={d['price']}, 1d={d['d1']}%, 5d={d['d5']}%, "
                f"S1={d['s1']}, R1={d['r1']}, dist_52hi={dist_hi:.1f}%, vol_rel={d['vol_rel']}x"
            )
    prompt = (
        "Datos técnicos:\n" + "\n".join(rows) + "\n\n"
        "1. 2-3 acciones mejor setup y por qué\n"
        "2. Acciones a evitar\n3. Trade concreto: entrada, objetivo, stop\n4. Riesgo mercado 1-10"
    )
    texto = ask_ai(prompt)
    await msg.edit_text(f"🎯 *Oportunidades*\n\n{texto}", parse_mode="Markdown")


async def cmd_explosiones(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update): return
    msg = await update.effective_message.reply_text("💥 Buscando explosiones de precio…")
    candidates = scan_explosions(ALL_STOCKS)
    if not candidates:
        await msg.edit_text("💥 *Explosiones*\n\nNo hay señales claras ahora mismo. Mercado en calma.", parse_mode="Markdown")
        return
    rows = []
    for c in candidates:
        rows.append(
            f"*{c['ticker']}*: `{c['price']}` | semana {c['d5']:+.1f}% | "
            f"vol {c['vol_rel']}x | dist máx52 {c['dist_hi52']:.1f}% | R1 `{c['r1']}`"
        )
    bloque = "\n".join(rows)
    prompt = (
        f"Acciones con posible explosión:\n{bloque}\n\n"
        "Para cada una:\n1. Por qué podría explotar al alza\n"
        "2. Nivel clave a superar para confirmar\n3. Riesgo si falla"
    )
    texto = ask_ai(prompt)
    await msg.edit_text(f"💥 *Posibles Explosiones*\n\n{bloque}\n\n{texto}", parse_mode="Markdown")


async def cmd_calendario(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update): return
    msg = await update.effective_message.reply_text("📅 Cargando calendario económico…")
    eventos = get_economic_calendar()
    if eventos:
        lines = []
        for e in eventos:
            fecha  = e.get("date", "")[:10]
            pais   = e.get("country", "").upper()
            titulo = e.get("title", "")
            lines.append(f"📌 *{fecha}* [{pais}] {titulo}")
        bloque = "\n".join(lines)
        prompt = (
            f"Eventos económicos alto impacto esta semana:\n{bloque}\n\n"
            "1. Los 3 más importantes y por qué mueven mercado\n"
            "2. Qué esperar de cada uno\n"
            "3. Sectores/acciones más afectados"
        )
        texto = ask_ai(prompt)
        await msg.edit_text(
            f"📅 *Calendario Económico — Esta Semana*\n\n{bloque}\n\n{texto}",
            parse_mode="Markdown"
        )
    else:
        texto = ask_ai(
            "Dame el calendario de eventos económicos clave de esta semana: "
            "Fed, BCE, inflación, empleo, earnings importantes. Fechas y expectativas."
        )
        await msg.edit_text(f"📅 *Calendario Económico*\n\n{texto}", parse_mode="Markdown")


async def cmd_sr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update): return
    msg = await update.effective_message.reply_text("🔔 Escaneando soportes y resistencias…")
    alerts = scan_sr_alerts(ALL_STOCKS)
    if alerts:
        bloque = "\n".join(alerts)
        prompt = (
            f"Acciones cerca de niveles S/R clave:\n{bloque}\n\n"
            "Para las más interesantes:\n1. Probabilidad de rebote o ruptura\n"
            "2. Qué confirmaría cada escenario\n3. Operativa sugerida"
        )
        texto = ask_ai(prompt)
        await msg.edit_text(f"🔔 *S/R Alert Scanner*\n\n{bloque}\n\n{texto}", parse_mode="Markdown")
    else:
        await msg.edit_text(
            "🔔 *S/R Alert Scanner*\n\nNinguna acción en zona crítica ahora mismo.",
            parse_mode="Markdown"
        )


async def cmd_analisis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update): return
    if not context.args:
        await update.message.reply_text("Uso: `/analisis TICKER`\nEj: `/analisis AAPL`", parse_mode="Markdown")
        return
    ticker = context.args[0].upper()
    msg    = await update.message.reply_text(f"🔍 Analizando *{ticker}*…", parse_mode="Markdown")
    d      = fetch_quote(ticker, "6mo")
    if not d:
        await msg.edit_text(f"❌ Sin datos para `{ticker}`.", parse_mode="Markdown")
        return
    prompt = (
        f"Acción: {ticker}\nPrecio: {d['price']} | 1d: {d['d1']}% | 5d: {d['d5']}%\n"
        f"Máx52: {d['hi52']} | Mín52: {d['lo52']} | Vol relativo: {d['vol_rel']}x\n"
        f"Pivot: {d['pivot']} | R1: {d['r1']} | R2: {d['r2']} | S1: {d['s1']} | S2: {d['s2']}\n\n"
        "1. Posición técnica actual\n2. Niveles clave\n"
        "3. Escenario alcista vs bajista\n4. Sesgo operativo\n5. R/R si hay entrada"
    )
    texto  = ask_ai(prompt)
    header = (
        f"🔍 *{ticker}*\n"
        f"💰 `{d['price']}` | Hoy: {arrow(d['d1'])} | Semana: {arrow(d['d5'])}\n"
        f"📊 Vol: `{d['vol_rel']}x` media\n"
        f"🔴 R2 `{d['r2']}` › R1 `{d['r1']}`\n"
        f"⚪ Pivot `{d['pivot']}`\n"
        f"🟢 S1 `{d['s1']}` › S2 `{d['s2']}`\n"
        f"📅 Rango 52s: `{d['lo52']}` — `{d['hi52']}`\n\n"
    )
    await msg.edit_text(header + texto, parse_mode="Markdown")


async def cmd_crypto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update): return
    msg     = await update.effective_message.reply_text("₿ Cargando crypto…")
    nombres = {"BTC-USD": "Bitcoin", "ETH-USD": "Ethereum", "SOL-USD": "Solana"}
    lines, data_ai = [], []
    for t in CRYPTO:
        d = fetch_quote(t, "1mo")
        if d:
            n = nombres[t]
            lines.append(f"{arrow(d['d1'])} *{n}*: `{d['price']:,.0f}` | semana {arrow(d['d5'])}")
            data_ai.append(f"{n}: {d['price']:,.0f} ({d['d1']:+.2f}% hoy) S1={d['s1']} R1={d['r1']}")
    texto = ask_ai("Datos crypto:\n" + "\n".join(data_ai) + "\n\n1. Lectura técnica\n2. Mejor setup\n3. Niveles críticos BTC")
    await msg.edit_text(
        f"₿ *Crypto — {datetime.now().strftime('%H:%M')}*\n\n" + "\n".join(lines) + f"\n\n{texto}",
        parse_mode="Markdown"
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update): return
    resp = ask_ai(f"Pregunta: {update.message.text}\nResponde como analista financiero.")
    await update.message.reply_text(resp, parse_mode="Markdown")


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    handlers = {
        "noticias":      cmd_noticias,
        "mercados":      cmd_mercados,
        "oportunidades": cmd_oportunidades,
        "explosiones":   cmd_explosiones,
        "calendario":    cmd_calendario,
        "sr_scan":       cmd_sr,
        "crypto":        cmd_crypto,
    }
    fn = handlers.get(q.data)
    if fn:
        await fn(update, context)

# ── Jobs automáticos ──────────────────────────────────────────────────────────
async def job_morning(app):
    bot       = app.bot
    titulares = get_news(8)
    bloque    = "\n".join(f"• {t}" for t in titulares) if titulares else "Sin noticias"
    eventos   = get_economic_calendar()
    cal_hoy   = [e for e in eventos if datetime.now().strftime("%Y-%m-%d") in e.get("date", "")]
    cal_txt   = "\n".join(f"• [{e.get('country','').upper()}] {e.get('title','')}" for e in cal_hoy) or "Sin eventos hoy"
    prompt    = (
        f"Briefing — {datetime.now().strftime('%A %d/%m')}:\n\n"
        f"Noticias:\n{bloque}\n\nEventos hoy:\n{cal_txt}\n\n"
        "Briefing completo: sentimiento, qué vigilar, sectores calientes, niveles S&P y DAX."
    )
    texto = ask_ai(prompt)
    await bot.send_message(chat_id=ALLOWED_USER_ID,
        text=f"☀️ *Briefing — {datetime.now().strftime('%d/%m')}*\n\n{texto}",
        parse_mode="Markdown")


async def job_close_eu(app):
    bot = app.bot
    lines, data_ai = [], []
    for t, nombre in list(INDICES.items())[3:]:
        d = fetch_quote(t, "5d")
        if d:
            lines.append(f"{arrow(d['d1'])} *{nombre}*: `{d['price']:,.0f}`")
            data_ai.append(f"{nombre}: {d['d1']:+.2f}% hoy")
    snap  = "\n".join(lines)
    texto = ask_ai("Cierre EU:\n" + "\n".join(data_ai) + "\n\nResumen sesión EU y qué esperar de EEUU.")
    await bot.send_message(chat_id=ALLOWED_USER_ID,
        text=f"🇪🇺 *Cierre Europa — {datetime.now().strftime('%d/%m %H:%M')}*\n\n{snap}\n\n{texto}",
        parse_mode="Markdown")


async def job_close_us(app):
    bot = app.bot
    lines, data_ai = [], []
    for t, nombre in list(INDICES.items())[:3]:
        d = fetch_quote(t, "5d")
        if d:
            lines.append(f"{arrow(d['d1'])} *{nombre}*: `{d['price']:,.0f}`")
            data_ai.append(f"{nombre}: {d['d1']:+.2f}% hoy")
    snap  = "\n".join(lines)
    texto = ask_ai("Cierre EEUU:\n" + "\n".join(data_ai) + "\n\nResumen sesión EEUU y perspectiva mañana.")
    await bot.send_message(chat_id=ALLOWED_USER_ID,
        text=f"🇺🇸 *Cierre EEUU — {datetime.now().strftime('%d/%m %H:%M')}*\n\n{snap}\n\n{texto}",
        parse_mode="Markdown")


async def job_sr_scanner(app):
    bot    = app.bot
    alerts = scan_sr_alerts(ALL_STOCKS)
    if not alerts:
        return
    bloque = "\n".join(alerts[:6])
    texto  = ask_ai(f"Alertas S/R:\n{bloque}\n\nTop 2 más interesantes y operativa sugerida.")
    await bot.send_message(chat_id=ALLOWED_USER_ID,
        text=f"🔔 *Alerta S/R — {datetime.now().strftime('%H:%M')}*\n\n{bloque}\n\n{texto}",
        parse_mode="Markdown")


async def job_explosion_scanner(app):
    bot        = app.bot
    candidates = scan_explosions(ALL_STOCKS)
    if not candidates:
        return
    rows   = [f"💥 *{c['ticker']}*: semana {c['d5']:+.1f}% | vol {c['vol_rel']}x | R1 `{c['r1']}`" for c in candidates[:3]]
    bloque = "\n".join(rows)
    texto  = ask_ai(f"Posibles explosiones:\n{bloque}\n\nAnálisis rápido y niveles a vigilar.")
    await bot.send_message(chat_id=ALLOWED_USER_ID,
        text=f"💥 *Detector Explosiones — {datetime.now().strftime('%H:%M')}*\n\n{bloque}\n\n{texto}",
        parse_mode="Markdown")

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start",          start))
    app.add_handler(CommandHandler("noticias",       cmd_noticias))
    app.add_handler(CommandHandler("mercados",       cmd_mercados))
    app.add_handler(CommandHandler("oportunidades",  cmd_oportunidades))
    app.add_handler(CommandHandler("explosiones",    cmd_explosiones))
    app.add_handler(CommandHandler("calendario",     cmd_calendario))
    app.add_handler(CommandHandler("sr",             cmd_sr))
    app.add_handler(CommandHandler("analisis",       cmd_analisis))
    app.add_handler(CommandHandler("crypto",         cmd_crypto))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    if ALLOWED_USER_ID:
        def run_job(coro_fn):
            asyncio.run(coro_fn(app))

        scheduler = BackgroundScheduler(timezone=MADRID)
        scheduler.add_job(lambda: run_job(job_morning),           "cron",     hour=8,  minute=0)
        scheduler.add_job(lambda: run_job(job_close_eu),          "cron",     hour=17, minute=35)
        scheduler.add_job(lambda: run_job(job_close_us),          "cron",     hour=22, minute=5)
        scheduler.add_job(lambda: run_job(job_sr_scanner),        "interval", hours=2)
        scheduler.add_job(lambda: run_job(job_explosion_scanner), "interval", hours=3)
        scheduler.start()
        log.info("✅ Jobs automáticos activados")
    else:
        log.warning("⚠️ ALLOWED_USER_ID no configurado — jobs desactivados")

    log.info("🤖 Financial Bot arrancado")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()