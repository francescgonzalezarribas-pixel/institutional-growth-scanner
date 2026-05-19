"""
Financial Telegram Bot — 100% GRATIS
IA: OpenRouter (Llama 3.3 70B) — gratis
Datos: yfinance
Noticias: RSS feeds
Alertas: APScheduler
"""

import os
import logging
import time
import feedparser
import yfinance as yf
import requests
import pytz
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from openai import OpenAI
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler

# ── Config ────────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN     = os.environ["TELEGRAM_TOKEN"]
OPENROUTER_API_KEY = os.environ["OPENROUTER_API_KEY"]
ALLOWED_USER_ID    = int(os.environ.get("ALLOWED_USER_ID", 0))
MADRID             = pytz.timezone("Europe/Madrid")

SYSTEM = """Eres un analista financiero senior. Reglas:
- Responde SIEMPRE en español
- Usa emojis para separar secciones
- Usa *negrita* con asteriscos para datos clave
- Máximo 4 párrafos o listas cortas
- Da conclusiones concretas y accionables
- No das consejos de inversión pero sí análisis objetivo con sesgo claro"""

ai_client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY,
)
bot = telebot.TeleBot(TELEGRAM_TOKEN)

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

# ── RSS feeds ─────────────────────────────────────────────────────────────────
RSS_FEEDS = [
    "https://feeds.marketwatch.com/marketwatch/topstories/",
    "https://news.google.com/rss/search?q=stock+market+europe+usa&hl=en&gl=US&ceid=US:en",
    "https://feeds.reuters.com/reuters/businessNews",
    "https://finance.yahoo.com/news/rssindex",
]

# ── Activos ───────────────────────────────────────────────────────────────────
INDICES = {
    "^GSPC": "S&P 500", "^DJI": "Dow Jones", "^IXIC": "Nasdaq",
    "^STOXX50E": "Euro Stoxx 50", "^GDAXI": "DAX", "^FTSE": "FTSE 100",
}
US_STOCKS  = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "JPM", "GS", "XOM"]
EU_STOCKS  = ["ASML", "SAP", "SIE.DE", "TTE.PA", "LVMH.PA", "NESN.SW"]
CRYPTO     = ["BTC-USD", "ETH-USD", "SOL-USD"]
ALL_STOCKS = US_STOCKS + EU_STOCKS

# ── Helpers ───────────────────────────────────────────────────────────────────
def allowed(message):
    if ALLOWED_USER_ID == 0:
        return True
    return message.from_user.id == ALLOWED_USER_ID


def ask_ai(prompt: str) -> str:
    for attempt in range(3):
        try:
            resp = ai_client.chat.completions.create(
                model="google/gemini-2.0-flash-exp:free",
                messages=[
                    {"role": "system", "content": SYSTEM},
                    {"role": "user",   "content": prompt}
                ],
                temperature=0.4,
            )
            return resp.choices[0].message.content
        except Exception as e:
            if attempt < 2:
                time.sleep(3)
            else:
                log.error(f"AI error: {e}")
                return "⚠️ Error IA. Intenta en unos segundos."


def get_news(max_items=10):
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
            log.warning(f"RSS: {e}")
    return titulares[:max_items]


def fetch_quote(ticker, period="3mo"):
    try:
        hist = yf.Ticker(ticker).history(period=period)
        if hist.empty or len(hist) < 10:
            return None
        c, h, lo = hist["Close"], hist["High"], hist["Low"]
        vol      = hist["Volume"]
        price    = c.iloc[-1]
        d1 = (price - c.iloc[-2]) / c.iloc[-2] * 100
        d5 = (price - c.iloc[-6]) / c.iloc[-6] * 100 if len(c) > 5 else 0
        pivot = (h.iloc[-1] + lo.iloc[-1] + c.iloc[-1]) / 3
        r1 = 2 * pivot - lo.iloc[-1]
        s1 = 2 * pivot - h.iloc[-1]
        hi20, lo20 = h.tail(20).max(), lo.tail(20).min()
        rng = hi20 - lo20
        r2  = hi20 + rng * 0.382
        s2  = lo20 - rng * 0.382
        avg_vol = vol.tail(20).mean()
        vol_rel = vol.iloc[-1] / avg_vol if avg_vol > 0 else 1.0
        return {
            "ticker": ticker, "price": round(price, 2),
            "d1": round(d1, 2), "d5": round(d5, 2),
            "pivot": round(pivot, 2),
            "r1": round(r1, 2), "r2": round(r2, 2),
            "s1": round(s1, 2), "s2": round(s2, 2),
            "hi52": round(h.tail(252).max(), 2),
            "lo52": round(lo.tail(252).min(), 2),
            "vol_rel": round(vol_rel, 2),
        }
    except Exception as e:
        log.warning(f"fetch_quote {ticker}: {e}")
        return None


def get_economic_calendar():
    eventos = []
    try:
        resp    = requests.get("https://nfs.faireconomy.media/ff_calendar_thisweek.json", timeout=8)
        eventos = [e for e in resp.json() if e.get("impact") == "High"][:12]
    except Exception as e:
        log.warning(f"Calendar: {e}")

    for ticker in ["NVDA", "AAPL", "MSFT", "AMZN", "GOOGL", "META", "TSLA", "JPM"]:
        try:
            cal = yf.Ticker(ticker).calendar
            if cal is not None and "Earnings Date" in cal:
                fecha  = str(cal["Earnings Date"][0])[:10]
                semana = datetime.now().strftime("%Y-%m")
                if semana in fecha:
                    eventos.append({
                        "date": fecha, "country": "US",
                        "title": f"📊 Earnings {ticker}", "impact": "High"
                    })
        except:
            pass
    return eventos


def scan_sr_alerts(stocks):
    alerts = []
    for t in stocks:
        d = fetch_quote(t, "3mo")
        if not d:
            continue
        for nivel, nombre in [(d["s1"],"S1"),(d["s2"],"S2"),(d["r1"],"R1"),(d["r2"],"R2")]:
            dist = abs(d["price"] - nivel) / nivel * 100
            if dist <= 1.5:
                tipo = "🟢 SOPORTE" if "S" in nombre else "🔴 RESIST."
                alerts.append(f"{tipo} *{t}* cerca {nombre} `{nivel}` — `{d['price']}` ({dist:.1f}%)")
    return alerts


def scan_explosions(stocks):
    out = []
    for t in stocks:
        d = fetch_quote(t, "3mo")
        if not d:
            continue
        dist_hi = (d["hi52"] - d["price"]) / d["hi52"] * 100
        if d["vol_rel"] >= 1.8 and d["d5"] >= 3.0 and dist_hi <= 8.0:
            out.append({"ticker": t, "d5": d["d5"], "vol_rel": d["vol_rel"],
                        "dist_hi52": dist_hi, "price": d["price"], "r1": d["r1"]})
    out.sort(key=lambda x: x["vol_rel"] * x["d5"], reverse=True)
    return out[:5]


def arrow(v):
    return f"{'🟢' if v >= 0 else '🔴'} {v:+.2f}%"


def main_kb():
    kb = InlineKeyboardMarkup()
    kb.row(InlineKeyboardButton("📰 Noticias",     callback_data="noticias"),
           InlineKeyboardButton("📊 Mercados",      callback_data="mercados"))
    kb.row(InlineKeyboardButton("🎯 Oportunidades", callback_data="oportunidades"),
           InlineKeyboardButton("💥 Explosiones",   callback_data="explosiones"))
    kb.row(InlineKeyboardButton("📅 Calendario",    callback_data="calendario"),
           InlineKeyboardButton("₿ Crypto",         callback_data="crypto"))
    kb.row(InlineKeyboardButton("🔔 S/R Scanner",   callback_data="sr_scan"))
    return kb

# ── Handlers ──────────────────────────────────────────────────────────────────
@bot.message_handler(commands=["start"])
def cmd_start(msg):
    if not allowed(msg): return
    bot.send_message(msg.chat.id,
        "🏦 *Financial Bot* — Análisis EU & EEUU\n\n"
        "*/noticias* — Titulares + resumen IA\n"
        "*/mercados* — Índices con variación\n"
        "*/oportunidades* — Mejores setups\n"
        "*/explosiones* — Momentum explosivo\n"
        "*/calendario* — Eventos semana\n"
        "*/sr* — Scanner S/R\n"
        "*/analisis TICKER* — Análisis completo\n"
        "*/crypto* — BTC, ETH, SOL\n"
        "✉️ Pregunta libre → IA responde\n\nElige 👇",
        parse_mode="Markdown", reply_markup=main_kb()
    )


@bot.message_handler(commands=["noticias"])
def cmd_noticias(msg):
    if not allowed(msg): return
    m = bot.send_message(msg.chat.id, "📰 Leyendo feeds RSS…")
    titulares = get_news(10)
    if titulares:
        bloque = "\n".join(f"• {t}" for t in titulares)
        prompt = (f"Noticias:\n{bloque}\n\n1. 3 titulares clave y por qué\n"
                  "2. Sentimiento mercado\n3. Qué esperar EU y EEUU\n4. Sectores/acciones a vigilar")
    else:
        prompt = "Estado mercados EU y EEUU hoy, factores clave, sectores con momentum."
    texto = ask_ai(prompt)
    bot.edit_message_text(f"📰 *Noticias — {datetime.now().strftime('%d/%m %H:%M')}*\n\n{texto}",
                          msg.chat.id, m.message_id, parse_mode="Markdown")


@bot.message_handler(commands=["mercados"])
def cmd_mercados(msg):
    if not allowed(msg): return
    m = bot.send_message(msg.chat.id, "📊 Cargando mercados…")
    lines, data_ai = [], []
    for t, nombre in INDICES.items():
        d = fetch_quote(t, "1mo")
        if d:
            lines.append(f"{arrow(d['d1'])} *{nombre}*: `{d['price']:,.0f}` | semana {arrow(d['d5'])}")
            data_ai.append(f"{nombre}: {d['price']:,.0f} ({d['d1']:+.2f}% hoy, {d['d5']:+.2f}% semana)")
    snap     = "\n".join(lines) or "_Sin datos_"
    analisis = ask_ai("Snapshot:\n" + "\n".join(data_ai) + "\n\nLectura global, divergencias EU/EEUU, qué vigilar.")
    bot.edit_message_text(f"📊 *Mercados — {datetime.now().strftime('%d/%m %H:%M')}*\n\n{snap}\n\n{analisis}",
                          msg.chat.id, m.message_id, parse_mode="Markdown")


@bot.message_handler(commands=["oportunidades"])
def cmd_oportunidades(msg):
    if not allowed(msg): return
    m = bot.send_message(msg.chat.id, "🎯 Escaneando oportunidades…")
    rows = []
    for t in ALL_STOCKS[:9]:
        d = fetch_quote(t, "3mo")
        if d:
            dist_hi = (d["price"] - d["hi52"]) / d["hi52"] * 100
            rows.append(f"{t}: precio={d['price']}, 1d={d['d1']}%, 5d={d['d5']}%, "
                        f"S1={d['s1']}, R1={d['r1']}, dist_52hi={dist_hi:.1f}%, vol={d['vol_rel']}x")
    prompt = ("Datos técnicos:\n" + "\n".join(rows) + "\n\n"
              "1. 2-3 mejores setups\n2. Acciones a evitar\n3. Trade concreto: entrada/objetivo/stop\n4. Riesgo 1-10")
    texto = ask_ai(prompt)
    bot.edit_message_text(f"🎯 *Oportunidades*\n\n{texto}",
                          msg.chat.id, m.message_id, parse_mode="Markdown")


@bot.message_handler(commands=["explosiones"])
def cmd_explosiones(msg):
    if not allowed(msg): return
    m = bot.send_message(msg.chat.id, "💥 Buscando explosiones…")
    candidates = scan_explosions(ALL_STOCKS)
    if not candidates:
        bot.edit_message_text("💥 *Explosiones*\n\nMercado en calma, sin señales claras.",
                              msg.chat.id, m.message_id, parse_mode="Markdown")
        return
    rows = [f"*{c['ticker']}*: `{c['price']}` | semana {c['d5']:+.1f}% | "
            f"vol {c['vol_rel']}x | dist máx52 {c['dist_hi52']:.1f}% | R1 `{c['r1']}`"
            for c in candidates]
    bloque = "\n".join(rows)
    texto  = ask_ai(f"Posibles explosiones:\n{bloque}\n\n1. Por qué podría subir\n2. Nivel a superar\n3. Riesgo si falla")
    bot.edit_message_text(f"💥 *Posibles Explosiones*\n\n{bloque}\n\n{texto}",
                          msg.chat.id, m.message_id, parse_mode="Markdown")


@bot.message_handler(commands=["calendario"])
def cmd_calendario(msg):
    if not allowed(msg): return
    m = bot.send_message(msg.chat.id, "📅 Cargando calendario económico…")
    eventos = get_economic_calendar()
    if eventos:
        eventos_sorted = sorted(eventos, key=lambda x: x.get("date",""))
        lines  = [f"📌 *{e.get('date','')[:10]}* [{e.get('country','').upper()}] {e.get('title','')}"
                  for e in eventos_sorted]
        bloque = "\n".join(lines)
        prompt = (f"Eventos económicos alto impacto esta semana:\n{bloque}\n\n"
                  "Traduce y explica cada evento en español. Luego:\n"
                  "1. Los 3 más importantes y por qué mueven mercado\n"
                  "2. Qué esperar de cada uno\n"
                  "3. Sectores y acciones más afectados")
        texto  = ask_ai(prompt)
        bot.edit_message_text(f"📅 *Calendario — Esta Semana*\n\n{bloque}\n\n{texto}",
                              msg.chat.id, m.message_id, parse_mode="Markdown")
    else:
        texto = ask_ai("Calendario eventos económicos clave esta semana en español: "
                       "Fed, BCE, inflación, empleo, earnings importantes. Fechas y expectativas.")
        bot.edit_message_text(f"📅 *Calendario Económico*\n\n{texto}",
                              msg.chat.id, m.message_id, parse_mode="Markdown")


@bot.message_handler(commands=["sr"])
def cmd_sr(msg):
    if not allowed(msg): return
    m = bot.send_message(msg.chat.id, "🔔 Escaneando S/R…")
    alerts = scan_sr_alerts(ALL_STOCKS)
    if alerts:
        bloque = "\n".join(alerts)
        texto  = ask_ai(f"Acciones en zona S/R:\n{bloque}\n\n1. Rebote o ruptura\n2. Qué confirmaría\n3. Operativa")
        bot.edit_message_text(f"🔔 *S/R Scanner*\n\n{bloque}\n\n{texto}",
                              msg.chat.id, m.message_id, parse_mode="Markdown")
    else:
        bot.edit_message_text("🔔 *S/R Scanner*\n\nNinguna acción en zona crítica ahora mismo.",
                              msg.chat.id, m.message_id, parse_mode="Markdown")


@bot.message_handler(commands=["analisis"])
def cmd_analisis(msg):
    if not allowed(msg): return
    parts = msg.text.split()
    if len(parts) < 2:
        bot.send_message(msg.chat.id, "Uso: `/analisis TICKER`\nEj: `/analisis AAPL`", parse_mode="Markdown")
        return
    ticker = parts[1].upper()
    m = bot.send_message(msg.chat.id, f"🔍 Analizando *{ticker}*…", parse_mode="Markdown")
    d = fetch_quote(ticker, "6mo")
    if not d:
        bot.edit_message_text(f"❌ Sin datos para `{ticker}`.", msg.chat.id, m.message_id, parse_mode="Markdown")
        return
    prompt = (f"Acción: {ticker}\nPrecio: {d['price']} | 1d: {d['d1']}% | 5d: {d['d5']}%\n"
              f"Máx52: {d['hi52']} | Mín52: {d['lo52']} | Vol: {d['vol_rel']}x\n"
              f"Pivot: {d['pivot']} | R1: {d['r1']} | R2: {d['r2']} | S1: {d['s1']} | S2: {d['s2']}\n\n"
              "1. Posición técnica\n2. Niveles clave\n3. Alcista vs bajista\n4. Sesgo operativo\n5. R/R")
    texto  = ask_ai(prompt)
    header = (f"🔍 *{ticker}*\n"
              f"💰 `{d['price']}` | Hoy: {arrow(d['d1'])} | Semana: {arrow(d['d5'])}\n"
              f"📊 Vol: `{d['vol_rel']}x`\n"
              f"🔴 R2 `{d['r2']}` › R1 `{d['r1']}`\n"
              f"⚪ Pivot `{d['pivot']}`\n"
              f"🟢 S1 `{d['s1']}` › S2 `{d['s2']}`\n"
              f"📅 52s: `{d['lo52']}` — `{d['hi52']}`\n\n")
    bot.edit_message_text(header + texto, msg.chat.id, m.message_id, parse_mode="Markdown")


@bot.message_handler(commands=["crypto"])
def cmd_crypto(msg):
    if not allowed(msg): return
    m = bot.send_message(msg.chat.id, "₿ Cargando crypto…")
    nombres = {"BTC-USD": "Bitcoin", "ETH-USD": "Ethereum", "SOL-USD": "Solana"}
    lines, data_ai = [], []
    for t in CRYPTO:
        d = fetch_quote(t, "1mo")
        if d:
            n = nombres[t]
            lines.append(f"{arrow(d['d1'])} *{n}*: `{d['price']:,.0f}` | semana {arrow(d['d5'])}")
            data_ai.append(f"{n}: {d['price']:,.0f} ({d['d1']:+.2f}%) S1={d['s1']} R1={d['r1']}")
    texto = ask_ai("Crypto:\n" + "\n".join(data_ai) + "\n\n1. Lectura técnica\n2. Mejor setup\n3. Niveles BTC")
    bot.edit_message_text(
        f"₿ *Crypto — {datetime.now().strftime('%H:%M')}*\n\n" + "\n".join(lines) + f"\n\n{texto}",
        msg.chat.id, m.message_id, parse_mode="Markdown")


@bot.message_handler(func=lambda m: True)
def handle_text(msg):
    if not allowed(msg): return
    resp = ask_ai(f"Pregunta: {msg.text}\nResponde como analista financiero.")
    bot.send_message(msg.chat.id, resp, parse_mode="Markdown")


@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    bot.answer_callback_query(call.id)
    call.message.from_user = call.from_user
    handlers = {
        "noticias":      cmd_noticias,
        "mercados":      cmd_mercados,
        "oportunidades": cmd_oportunidades,
        "explosiones":   cmd_explosiones,
        "calendario":    cmd_calendario,
        "sr_scan":       cmd_sr,
        "crypto":        cmd_crypto,
    }
    fn = handlers.get(call.data)
    if fn:
        fn(call.message)

# ── Jobs automáticos ──────────────────────────────────────────────────────────
def job_morning():
    titulares = get_news(8)
    bloque    = "\n".join(f"• {t}" for t in titulares) if titulares else "Sin noticias"
    eventos   = get_economic_calendar()
    cal_hoy   = [e for e in eventos if datetime.now().strftime("%Y-%m-%d") in e.get("date","")]
    cal_txt   = "\n".join(f"• [{e.get('country','').upper()}] {e.get('title','')}" for e in cal_hoy) or "Sin eventos hoy"
    prompt    = (f"Briefing {datetime.now().strftime('%A %d/%m')}:\nNoticias:\n{bloque}\n"
                 f"Eventos hoy:\n{cal_txt}\n\nBriefing en español: sentimiento, sectores, niveles S&P y DAX.")
    texto = ask_ai(prompt)
    bot.send_message(ALLOWED_USER_ID, f"☀️ *Briefing — {datetime.now().strftime('%d/%m')}*\n\n{texto}", parse_mode="Markdown")


def job_close_eu():
    lines, data_ai = [], []
    for t, nombre in list(INDICES.items())[3:]:
        d = fetch_quote(t, "5d")
        if d:
            lines.append(f"{arrow(d['d1'])} *{nombre}*: `{d['price']:,.0f}`")
            data_ai.append(f"{nombre}: {d['d1']:+.2f}%")
    snap  = "\n".join(lines)
    texto = ask_ai("Cierre EU:\n" + "\n".join(data_ai) + "\n\nResumen sesión y qué esperar de EEUU.")
    bot.send_message(ALLOWED_USER_ID, f"🇪🇺 *Cierre Europa — {datetime.now().strftime('%d/%m %H:%M')}*\n\n{snap}\n\n{texto}", parse_mode="Markdown")


def job_close_us():
    lines, data_ai = [], []
    for t, nombre in list(INDICES.items())[:3]:
        d = fetch_quote(t, "5d")
        if d:
            lines.append(f"{arrow(d['d1'])} *{nombre}*: `{d['price']:,.0f}`")
            data_ai.append(f"{nombre}: {d['d1']:+.2f}%")
    snap  = "\n".join(lines)
    texto = ask_ai("Cierre EEUU:\n" + "\n".join(data_ai) + "\n\nResumen sesión y perspectiva mañana.")
    bot.send_message(ALLOWED_USER_ID, f"🇺🇸 *Cierre EEUU — {datetime.now().strftime('%d/%m %H:%M')}*\n\n{snap}\n\n{texto}", parse_mode="Markdown")


def job_sr_scanner():
    alerts = scan_sr_alerts(ALL_STOCKS)
    if not alerts:
        return
    bloque = "\n".join(alerts[:6])
    texto  = ask_ai(f"Alertas S/R:\n{bloque}\n\nTop 2 más interesantes y operativa.")
    bot.send_message(ALLOWED_USER_ID, f"🔔 *Alerta S/R — {datetime.now().strftime('%H:%M')}*\n\n{bloque}\n\n{texto}", parse_mode="Markdown")


def job_explosion_scanner():
    candidates = scan_explosions(ALL_STOCKS)
    if not candidates:
        return
    rows   = [f"💥 *{c['ticker']}*: semana {c['d5']:+.1f}% | vol {c['vol_rel']}x | R1 `{c['r1']}`" for c in candidates[:3]]
    bloque = "\n".join(rows)
    texto  = ask_ai(f"Posibles explosiones:\n{bloque}\n\nAnálisis y niveles a vigilar.")
    bot.send_message(ALLOWED_USER_ID, f"💥 *Explosiones — {datetime.now().strftime('%H:%M')}*\n\n{bloque}\n\n{texto}", parse_mode="Markdown")

# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if ALLOWED_USER_ID:
        scheduler = BackgroundScheduler(timezone=MADRID)
        scheduler.add_job(job_morning,           "cron",     hour=8,  minute=0)
        scheduler.add_job(job_close_eu,          "cron",     hour=17, minute=35)
        scheduler.add_job(job_close_us,          "cron",     hour=22, minute=5)
        scheduler.add_job(job_sr_scanner,        "interval", hours=2)
        scheduler.add_job(job_explosion_scanner, "interval", hours=3)
        scheduler.start()
        log.info("✅ Jobs automáticos activados")

    log.info("🤖 Financial Bot arrancado")
    bot.polling(none_stop=True)