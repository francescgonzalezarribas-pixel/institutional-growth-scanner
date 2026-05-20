"""
Financial Telegram Bot — 100% GRATIS
IA: Mistral AI
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
from mistralai.client import MistralClient as Mistral
from mistralai.models.chat_completion import ChatMessage
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler

# ── Config ────────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN  = os.environ["TELEGRAM_TOKEN"]
MISTRAL_API_KEY = os.environ["MISTRAL_API_KEY"]
ALLOWED_USER_ID = int(os.environ.get("ALLOWED_USER_ID", 0))
MADRID          = pytz.timezone("Europe/Madrid")

SYSTEM = """Eres un analista financiero senior. Reglas:
- Responde SIEMPRE en español
- Usa emojis para separar secciones
- Usa *negrita* con asteriscos para datos clave
- Máximo 4 párrafos o listas cortas
- Da conclusiones concretas y accionables
- No das consejos de inversión pero sí análisis objetivo con sesgo claro"""

ai_client = Mistral(api_key=MISTRAL_API_KEY)
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
METALES    = {
    "GC=F": "Oro", "SI=F": "Plata",
    "PL=F": "Platino", "HG=F": "Cobre", "PA=F": "Paladio",
}

# ── Helpers ───────────────────────────────────────────────────────────────────
def allowed(message):
    if ALLOWED_USER_ID == 0:
        return True
    return message.from_user.id == ALLOWED_USER_ID


def ask_ai(prompt: str, max_chars: int = 3000) -> str:
    for attempt in range(3):
        try:
            resp = ai_client.chat(
                model="mistral-small-latest",
                messages=[
                    ChatMessage(role="system", content=SYSTEM),
                    ChatMessage(role="user",   content=prompt)
                ],
            )
            texto = resp.choices[0].message.content
            if len(texto) > max_chars:
                texto = texto[:max_chars] + "\n\n_...respuesta truncada_"
            return texto
        except Exception as e:
            if attempt < 2:
                time.sleep(3)
            else:
                log.error(f"Mistral error: {e}")
                return "⚠️ Error IA. Intenta en unos segundos."


def safe_send(chat_id, text, message_id=None, parse_mode="Markdown"):
    if len(text) > 4000:
        text = text[:4000] + "\n\n_...mensaje truncado_"
    try:
        if message_id:
            bot.edit_message_text(text, chat_id, message_id, parse_mode=parse_mode)
        else:
            bot.send_message(chat_id, text, parse_mode=parse_mode)
    except Exception as e:
        log.error(f"Send error: {e}")


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
                fecha = str(cal["Earnings Date"][0])[:10]
                if datetime.now().strftime("%Y-%m") in fecha:
                    eventos.append({"date": fecha, "country": "US",
                                    "title": f"📊 Earnings {ticker}", "impact": "High"})
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


def get_top_signals():
    candidatos = []
    for t in ALL_STOCKS + CRYPTO:
        d = fetch_quote(t, "3mo")
        if not d:
            continue
        score = 0
        if d["vol_rel"] >= 1.5:                                      score += 2
        if d["d1"] >= 1.0:                                           score += 2
        if d["d5"] >= 3.0:                                           score += 2
        if (d["hi52"] - d["price"]) / d["hi52"] * 100 <= 5.0:       score += 3
        if (d["price"] - d["lo52"]) / d["lo52"] * 100 <= 5.0:       score += 1
        for nivel in [d["s1"], d["s2"]]:
            if abs(d["price"] - nivel) / nivel * 100 <= 1.5:         score += 2
        candidatos.append({**d, "score": score})
    candidatos.sort(key=lambda x: x["score"], reverse=True)
    return candidatos[:4]


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
    kb.row(InlineKeyboardButton("🔔 S/R Scanner",   callback_data="sr_scan"),
           InlineKeyboardButton("🏆 Señales",        callback_data="senales"))
    kb.row(InlineKeyboardButton("🪙 Metales",        callback_data="metales"))
    return kb

# ── Handlers ──────────────────────────────────────────────────────────────────
@bot.message_handler(commands=["start"])
def cmd_start(msg):
    if not allowed(msg): return
    safe_send(msg.chat.id,
        "🏦 *Financial Bot* — Análisis EU & EEUU\n\n"
        "*/noticias* — Titulares + resumen IA\n"
        "*/mercados* — Índices con variación\n"
        "*/oportunidades* — Mejores setups\n"
        "*/explosiones* — Momentum explosivo\n"
        "*/calendario* — Eventos semana\n"
        "*/sr* — Scanner S/R\n"
        "*/senales* — 4 mejores operaciones del día\n"
        "*/metales* — Oro, Plata, Platino, Cobre\n"
        "*/analisis TICKER* — Análisis completo\n"
        "*/crypto* — BTC, ETH, SOL\n"
        "✉️ Pregunta libre → IA responde\n\nElige 👇"
    )
    bot.send_message(msg.chat.id, "👇", reply_markup=main_kb())


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
    safe_send(msg.chat.id,
        f"📰 *Noticias — {datetime.now().strftime('%d/%m %H:%M')}*\n\n{texto}",
        message_id=m.message_id)


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
    safe_send(msg.chat.id,
        f"📊 *Mercados — {datetime.now().strftime('%d/%m %H:%M')}*\n\n{snap}\n\n{analisis}",
        message_id=m.message_id)


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
    safe_send(msg.chat.id, f"🎯 *Oportunidades*\n\n{texto}", message_id=m.message_id)


@bot.message_handler(commands=["explosiones"])
def cmd_explosiones(msg):
    if not allowed(msg): return
    m = bot.send_message(msg.chat.id, "💥 Buscando explosiones…")
    candidates = scan_explosions(ALL_STOCKS)
    if not candidates:
        safe_send(msg.chat.id, "💥 *Explosiones*\n\nMercado en calma, sin señales claras.", message_id=m.message_id)
        return
    rows = [f"*{c['ticker']}*: `{c['price']}` | semana {c['d5']:+.1f}% | "
            f"vol {c['vol_rel']}x | dist máx52 {c['dist_hi52']:.1f}% | R1 `{c['r1']}`"
            for c in candidates]
    bloque = "\n".join(rows)
    texto  = ask_ai(f"Posibles explosiones:\n{bloque}\n\n1. Por qué podría subir\n2. Nivel a superar\n3. Riesgo si falla")
    safe_send(msg.chat.id, f"💥 *Posibles Explosiones*\n\n{bloque}\n\n{texto}", message_id=m.message_id)


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
        prompt = (f"Eventos esta semana:\n{bloque}\n\n"
                  "Traduce en español. Luego:\n"
                  "1. 3 más importantes y por qué\n2. Qué esperar\n3. Sectores afectados")
        texto  = ask_ai(prompt)
        safe_send(msg.chat.id, f"📅 *Calendario — Esta Semana*\n\n{bloque}\n\n{texto}", message_id=m.message_id)
    else:
        texto = ask_ai("Calendario eventos económicos clave esta semana en español: Fed, BCE, inflación, empleo, earnings.")
        safe_send(msg.chat.id, f"📅 *Calendario Económico*\n\n{texto}", message_id=m.message_id)


@bot.message_handler(commands=["sr"])
def cmd_sr(msg):
    if not allowed(msg): return
    m = bot.send_message(msg.chat.id, "🔔 Escaneando S/R…")
    alerts = scan_sr_alerts(ALL_STOCKS)
    if alerts:
        bloque = "\n".join(alerts)
        texto  = ask_ai(f"Acciones en zona S/R:\n{bloque}\n\n1. Rebote o ruptura\n2. Qué confirmaría\n3. Operativa")
        safe_send(msg.chat.id, f"🔔 *S/R Scanner*\n\n{bloque}\n\n{texto}", message_id=m.message_id)
    else:
        safe_send(msg.chat.id, "🔔 *S/R Scanner*\n\nNinguna acción en zona crítica ahora mismo.", message_id=m.message_id)


@bot.message_handler(commands=["senales"])
def cmd_senales(msg):
    if not allowed(msg): return
    m = bot.send_message(msg.chat.id, "🏆 Calculando las 4 mejores operaciones del día…")
    signals = get_top_signals()
    if not signals:
        safe_send(msg.chat.id, "🏆 *Señales*\n\nNo hay setups claros ahora mismo.", message_id=m.message_id)
        return
    rows = [f"*{s['ticker']}*: `{s['price']}` | hoy {arrow(s['d1'])} | "
            f"semana {arrow(s['d5'])} | vol `{s['vol_rel']}x` | S1 `{s['s1']}` R1 `{s['r1']}`"
            for s in signals]
    bloque = "\n".join(rows)
    prompt = (f"4 mejores setups EU+EEUU+Crypto:\n{bloque}\n\n"
              "Para cada uno:\n1. 🎯 Entrada exacta\n2. 🛑 Stop loss\n"
              "3. ✅ Objetivo\n4. 📊 R/R\n5. ⏱ Horizonte temporal\nSé concreto con precios.")
    texto = ask_ai(prompt)
    safe_send(msg.chat.id,
        f"🏆 *4 Mejores Operaciones — {datetime.now().strftime('%d/%m %H:%M')}*\n\n{bloque}\n\n{texto}",
        message_id=m.message_id)


@bot.message_handler(commands=["metales"])
def cmd_metales(msg):
    if not allowed(msg): return
    m = bot.send_message(msg.chat.id, "🪙 Analizando metales…")
    lines, data_ai = [], []
    for t, nombre in METALES.items():
        d = fetch_quote(t, "3mo")
        if d:
            lines.append(f"{arrow(d['d1'])} *{nombre}*: `{d['price']:,.2f}` | semana {arrow(d['d5'])}\n"
                         f"   🔴 R1 `{d['r1']}` | 🟢 S1 `{d['s1']}` | Vol `{d['vol_rel']}x`")
            data_ai.append(f"{nombre}: precio={d['price']}, 1d={d['d1']}%, 5d={d['d5']}%, "
                           f"S1={d['s1']}, R1={d['r1']}, vol={d['vol_rel']}x, hi52={d['hi52']}, lo52={d['lo52']}")
    snap   = "\n".join(lines) or "_Sin datos_"
    prompt = (f"Metales:\n" + "\n".join(data_ai) + "\n\n"
              "Para cada metal:\n1. Tendencia\n2. Señal: COMPRAR/VENDER/ESPERAR\n"
              "3. Entrada, stop, objetivo\n4. Catalizador macro")
    texto  = ask_ai(prompt)
    safe_send(msg.chat.id,
        f"🪙 *Metales — {datetime.now().strftime('%d/%m %H:%M')}*\n\n{snap}\n\n{texto}",
        message_id=m.message_id)


@bot.message_handler(commands=["analisis"])
def cmd_analisis(msg):
    if not allowed(msg): return
    parts = msg.text.split()
    if len(parts) < 2:
        safe_send(msg.chat.id, "Uso: `/analisis TICKER`\nEj: `/analisis AAPL`")
        return
    ticker = parts[1].upper()
    m = bot.send_message(msg.chat.id, f"🔍 Analizando *{ticker}*…", parse_mode="Markdown")
    d = fetch_quote(ticker, "6mo")
    if not d:
        safe_send(msg.chat.id, f"❌ Sin datos para `{ticker}`.", message_id=m.message_id)
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
    safe_send(msg.chat.id, header + texto, message_id=m.message_id)


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
    safe_send(msg.chat.id,
        f"₿ *Crypto — {datetime.now().strftime('%H:%M')}*\n\n" + "\n".join(lines) + f"\n\n{texto}",
        message_id=m.message_id)


@bot.message_handler(func=lambda m: True)
def handle_text(msg):
    if not allowed(msg): return
    resp = ask_ai(f"Pregunta: {msg.text}\nResponde como analista financiero.")
    safe_send(msg.chat.id, resp)


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
        "senales":       cmd_senales,
        "metales":       cmd_metales,
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
    signals   = get_top_signals()
    sig_rows  = [f"{s['ticker']}: precio={s['price']}, 1d={s['d1']}%, vol={s['vol_rel']}x, S1={s['s1']}, R1={s['r1']}"
                 for s in signals]
    prompt    = (f"Briefing {datetime.now().strftime('%A %d/%m')}:\n\n"
                 f"Noticias:\n{bloque}\n\nEventos hoy:\n{cal_txt}\n\n"
                 f"Top 4 candidatos:\n" + "\n".join(sig_rows) + "\n\n"
                 "1. Resumen del día\n2. Para cada candidato: entrada, stop, objetivo, R/R\n"
                 "3. Niveles clave S&P y DAX\n4. Metal más interesante hoy")
    texto = ask_ai(prompt)
    safe_send(ALLOWED_USER_ID, f"☀️ *Briefing + Señales — {datetime.now().strftime('%d/%m')}*\n\n{texto}")


def job_close_eu():
    lines, data_ai = [], []
    for t, nombre in list(INDICES.items())[3:]:
        d = fetch_quote(t, "5d")
        if d:
            lines.append(f"{arrow(d['d1'])} *{nombre}*: `{d['price']:,.0f}`")
            data_ai.append(f"{nombre}: {d['d1']:+.2f}%")
    snap  = "\n".join(lines)
    texto = ask_ai("Cierre EU:\n" + "\n".join(data_ai) + "\n\nResumen sesión y qué esperar de EEUU.")
    safe_send(ALLOWED_USER_ID, f"🇪🇺 *Cierre Europa — {datetime.now().strftime('%d/%m %H:%M')}*\n\n{snap}\n\n{texto}")


def job_close_us():
    lines, data_ai = [], []
    for t, nombre in list(INDICES.items())[:3]:
        d = fetch_quote(t, "5d")
        if d:
            lines.append(f"{arrow(d['d1'])} *{nombre}*: `{d['price']:,.0f}`")
            data_ai.append(f"{nombre}: {d['d1']:+.2f}%")
    snap  = "\n".join(lines)
    texto = ask_ai("Cierre EEUU:\n" + "\n".join(data_ai) + "\n\nResumen sesión y perspectiva mañana.")
    safe_send(ALLOWED_USER_ID, f"🇺🇸 *Cierre EEUU — {datetime.now().strftime('%d/%m %H:%M')}*\n\n{snap}\n\n{texto}")


def job_sr_scanner():
    alerts = scan_sr_alerts(ALL_STOCKS)
    if not alerts:
        return
    bloque = "\n".join(alerts[:6])
    texto  = ask_ai(f"Alertas S/R:\n{bloque}\n\nTop 2 más interesantes y operativa.")
    safe_send(ALLOWED_USER_ID, f"🔔 *Alerta S/R — {datetime.now().strftime('%H:%M')}*\n\n{bloque}\n\n{texto}")


def job_explosion_scanner():
    candidates = scan_explosions(ALL_STOCKS)
    if not candidates:
        return
    rows   = [f"💥 *{c['ticker']}*: semana {c['d5']:+.1f}% | vol {c['vol_rel']}x | R1 `{c['r1']}`"
              for c in candidates[:3]]
    bloque = "\n".join(rows)
    texto  = ask_ai(f"Posibles explosiones:\n{bloque}\n\nAnálisis y niveles a vigilar.")
    safe_send(ALLOWED_USER_ID, f"💥 *Explosiones — {datetime.now().strftime('%H:%M')}*\n\n{bloque}\n\n{texto}")


def job_metales_scanner():
    data_ai = []
    for t, nombre in METALES.items():
        d = fetch_quote(t, "3mo")
        if d:
            data_ai.append(f"{nombre}: precio={d['price']}, 1d={d['d1']}%, 5d={d['d5']}%, "
                           f"S1={d['s1']}, R1={d['r1']}, vol={d['vol_rel']}x")
    if not data_ai:
        return
    prompt = (f"Metales ahora:\n" + "\n".join(data_ai) + "\n\n"
              "¿Hay señal clara de entrada o salida en algún metal?\n"
              "Si SÍ: cuál, entrada, stop, objetivo.\n"
              "Si NO: responde solo 'SIN SEÑAL'.")
    texto = ask_ai(prompt)
    if "SIN SEÑAL" in texto.upper():
        return
    safe_send(ALLOWED_USER_ID, f"🪙 *Alerta Metales — {datetime.now().strftime('%H:%M')}*\n\n{texto}")

# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if ALLOWED_USER_ID:
        scheduler = BackgroundScheduler(timezone=MADRID)
        scheduler.add_job(job_morning,           "cron",     hour=9,  minute=0)
        scheduler.add_job(job_close_eu,          "cron",     hour=17, minute=35)
        scheduler.add_job(job_close_us,          "cron",     hour=22, minute=5)
        scheduler.add_job(job_sr_scanner,        "interval", hours=2)
        scheduler.add_job(job_explosion_scanner, "interval", hours=3)
        scheduler.add_job(job_metales_scanner,   "interval", hours=4)
        scheduler.start()
        log.info("✅ Jobs automáticos activados")

    log.info("🤖 Financial Bot arrancado")
    bot.polling(none_stop=True)