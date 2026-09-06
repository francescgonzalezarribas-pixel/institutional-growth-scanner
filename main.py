import os
import io
import math
import requests
import feedparser
import yfinance as yf
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Wedge, Circle
from datetime import datetime

# --- CONFIGURACIÓN ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
ALLOWED_USER_ID = int(os.environ.get("ALLOWED_USER_ID", 0))

bot = telebot.TeleBot(TELEGRAM_TOKEN, parse_mode="Markdown")


def is_authorized(user_id: int) -> bool:
    if ALLOWED_USER_ID == 0:
        return True
    return user_id == ALLOWED_USER_ID


# ===================== INDICADORES =====================
def compute_rsi(series: pd.Series, period: int = 14) -> float:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return float(rsi.iloc[-1]) if not pd.isna(rsi.iloc[-1]) else 50.0


def get_fear_greed():
    try:
        r = requests.get("https://api.alternative.me/fng/?limit=2", timeout=8)
        data = r.json()["data"]
        actual = float(data[0]["value"])
        ayer = float(data[1]["value"]) if len(data) > 1 else actual
        clasif = data[0]["value_classification"]
        return {"valor": actual, "ayer": ayer, "clasificacion": clasif, "cambio": actual - ayer}
    except:
        return None


def get_vix() -> float:
    try:
        hist = yf.Ticker("^VIX").history(period="5d")
        return float(hist["Close"].iloc[-1])
    except:
        return 18.0


def get_binance_funding():
    try:
        r = requests.get("https://fapi.binance.com/fapi/v1/premiumIndex?symbol=BTCUSDT", timeout=6)
        data = r.json()
        funding = float(data.get("lastFundingRate", 0)) * 100
        return round(funding, 4)
    except:
        return None


def get_binance_long_short():
    try:
        r = requests.get("https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol=BTCUSDT&period=1h&limit=1", timeout=6)
        data = r.json()
        if data:
            ratio = float(data[0]["longShortRatio"])
            long_pct = round(ratio / (1 + ratio) * 100, 1)
            short_pct = round(100 - long_pct, 1)
            return {"ratio": round(ratio, 3), "long_pct": long_pct, "short_pct": short_pct}
    except:
        return None


def get_btc_dominance():
    try:
        r = requests.get("https://api.coingecko.com/api/v3/global", timeout=8)
        data = r.json()["data"]
        return {
            "btc_dom": round(data["market_cap_percentage"]["btc"], 1),
            "eth_dom": round(data["market_cap_percentage"].get("eth", 0), 1),
            "total_mcap_b": round(data["total_market_cap"]["usd"] / 1e9, 0)
        }
    except:
        return None


def get_realtime_btc():
    try:
        r = requests.get("https://api.binance.com/api/v3/ticker/24hr?symbol=BTCUSDT", timeout=6)
        data = r.json()
        return {
            "price": float(data["lastPrice"]),
            "d1": float(data["priceChangePercent"])
        }
    except:
        return None


# ===================== ÍNDICE VALOR =====================
def score_rsi(rsi: float) -> int:
    if rsi <= 30: return 10
    if rsi <= 40: return 8
    if rsi <= 50: return 6
    if rsi <= 60: return 4
    if rsi <= 70: return 2
    return 1

def score_ema_pct(pct: float) -> int:
    if pct <= -25: return 10
    if pct <= -15: return 9
    if pct <= -8:  return 8
    if pct <= -3:  return 7
    if pct <= 3:   return 5
    if pct <= 10:  return 3
    if pct <= 20:  return 2
    return 1

def score_volume(rel_vol: float) -> int:
    if rel_vol >= 2.0: return 8
    if rel_vol >= 1.5: return 7
    if rel_vol >= 1.1: return 6
    if rel_vol >= 0.8: return 5
    if rel_vol >= 0.5: return 4
    return 3

def score_dist_52w(pct: float) -> int:
    if pct <= -45: return 10
    if pct <= -30: return 9
    if pct <= -20: return 8
    if pct <= -12: return 7
    if pct <= -5:  return 6
    if pct <= 0:   return 5
    if pct <= 8:   return 3
    return 1

def score_vix(vix: float) -> int:
    if vix >= 30: return 9
    if vix >= 25: return 7
    if vix >= 20: return 5
    if vix >= 16: return 4
    return 3

def score_fear_greed(fg: float) -> int:
    if fg <= 25: return 10
    if fg <= 40: return 8
    if fg <= 55: return 5
    if fg <= 70: return 2
    return 1

def get_label(score: int) -> str:
    if score >= 72: return "BARATO — OPORTUNIDAD"
    if score >= 58: return "NEUTRAL — ACUMULACIÓN"
    if score >= 42: return "NEUTRAL"
    if score >= 28: return "CARO — PRECAUCIÓN"
    return "MUY CARO — ALTO RIESGO"


def calculate_value_index(ticker: str) -> dict:
    ticker = ticker.upper().strip()
    is_crypto = any(x in ticker for x in ["BTC", "ETH", "SOL", "BNB"]) or "-USD" in ticker

    mapping = {"BTC": "BTC-USD", "ETH": "ETH-USD", "SOL": "SOL-USD", "BNB": "BNB-USD"}
    ticker = mapping.get(ticker, ticker)

    stock = yf.Ticker(ticker)
    hist = stock.history(period="1y")
    if hist.empty or len(hist) < 60:
        raise ValueError(f"No hay datos suficientes para {ticker}")

    close = hist["Close"]
    volume = hist["Volume"]
    price = float(close.iloc[-1])

    rsi = compute_rsi(close, 14)
    ema200 = close.ewm(span=200, adjust=False).mean().iloc[-1]
    ema_pct = ((price - ema200) / ema200) * 100
    avg_vol = volume.iloc[-21:-1].mean()
    rel_vol = float(volume.iloc[-1] / avg_vol) if avg_vol > 0 else 1.0
    high_52 = float(close.max())
    dist_52 = ((price - high_52) / high_52) * 100
    weekly = close.resample("W").last().dropna()
    rsi_w = compute_rsi(weekly, 14) if len(weekly) > 15 else 50.0

    components = []
    total = max_p = 0

    for name, val, sc in [
        ("RSI 14d", f"{rsi:.1f}", score_rsi(rsi)),
        ("EMA200", f"{ema_pct:+.1f}%", score_ema_pct(ema_pct)),
        ("Volumen", f"{rel_vol:.2f}x", score_volume(rel_vol)),
        ("Dist. Max 52s", f"{dist_52:.1f}%", score_dist_52w(dist_52)),
        ("RSI Semanal", f"{rsi_w:.1f}", score_rsi(rsi_w)),
    ]:
        components.append((name, val, sc))
        total += sc
        max_p += 10

    vix = get_vix()
    components.append(("VIX", f"{vix:.2f}", score_vix(vix)))
    total += score_vix(vix)
    max_p += 10

    if is_crypto:
        fg = get_fear_greed()
        fg_val = fg["valor"] if fg else 50
        components.append(("Fear&Greed", f"{fg_val:.0f}", score_fear_greed(fg_val)))
        total += score_fear_greed(fg_val)
        max_p += 10

    final_score = int(round((total / max_p) * 100))
    name = ticker
    try:
        info = stock.info
        name = info.get("shortName") or info.get("longName") or ticker
    except:
        pass

    return {
        "ticker": ticker, "name": name, "price": price,
        "score": final_score, "label": get_label(final_score),
        "components": components
    }


def create_gauge_image(score: int, title: str, label: str) -> io.BytesIO:
    fig, ax = plt.subplots(figsize=(8, 5.2), facecolor="#0d1117")
    ax.set_facecolor("#0d1117")
    ax.set_xlim(-1.35, 1.35)
    ax.set_ylim(-0.45, 1.35)
    ax.set_aspect("equal")
    ax.axis("off")

    colors = ["#ff3b30", "#ff9500", "#ffcc00", "#34c759", "#30d158"]
    angles = [180, 144, 108, 72, 36, 0]
    for i in range(5):
        ax.add_patch(Wedge((0, 0), 1.05, angles[i+1], angles[i], width=0.30,
                           facecolor=colors[i], edgecolor="#0d1117", linewidth=2.5))

    angle_deg = 180 - (score / 100) * 180
    angle_rad = math.radians(angle_deg)
    ax.plot([0, 0.88 * math.cos(angle_rad)], [0, 0.88 * math.sin(angle_rad)],
            color="white", linewidth=4.5, solid_capstyle="round", zorder=10)
    ax.add_patch(Circle((0, 0), 0.09, facecolor="white", zorder=11))
    ax.add_patch(Circle((0, 0), 0.045, facecolor="#0d1117", zorder=12))

    ax.text(0, 1.22, title, ha="center", va="center", fontsize=13, color="white", fontweight="bold")
    ax.text(0, -0.22, f"{score}/100", ha="center", va="center", fontsize=30, color="white", fontweight="bold")
    ax.text(0, -0.40, label, ha="center", va="center", fontsize=12, color="#8b949e")
    ax.text(-1.18, -0.08, "CARO", ha="center", va="center", fontsize=11, color="#ff3b30", fontweight="bold")
    ax.text(1.18, -0.08, "BARATO", ha="center", va="center", fontsize=11, color="#30d158", fontweight="bold")
    ax.text(0, 1.08, "50\nNEUTRAL", ha="center", va="center", fontsize=9, color="#8b949e")

    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=160, bbox_inches="tight", facecolor="#0d1117")
    plt.close(fig)
    buf.seek(0)
    return buf


# ===================== BTC PROFUNDO =====================
def analisis_btc_profundo():
    # Precio real time
    rt = get_realtime_btc()
    hist = yf.Ticker("BTC-USD").history(period="1y")
    close = hist["Close"]
    high = hist["High"]
    low = hist["Low"]

    price = rt["price"] if rt else float(close.iloc[-1])
    d1 = rt["d1"] if rt else 0

    d5 = (price - float(close.iloc[-6])) / float(close.iloc[-6]) * 100 if len(close) > 5 else 0
    d20 = (price - float(close.iloc[-21])) / float(close.iloc[-21]) * 100 if len(close) > 20 else 0

    rsi = compute_rsi(close)
    ema20 = close.ewm(span=20, adjust=False).mean().iloc[-1]
    ema50 = close.ewm(span=50, adjust=False).mean().iloc[-1]

    # Soportes / Resistencias simples
    pivot = (high.iloc[-1] + low.iloc[-1] + close.iloc[-1]) / 3
    r1 = 2 * pivot - low.iloc[-1]
    s1 = 2 * pivot - high.iloc[-1]
    hi20 = high.tail(20).max()
    lo20 = low.tail(20).min()

    # Niveles psicológicos
    niveles = []
    for n in [70000, 75000, 80000, 85000, 90000, 95000, 100000]:
        dist = (n - price) / price * 100
        if abs(dist) < 12:
            tipo = "Resistencia" if n > price else "Soporte"
            niveles.append({"nivel": n, "tipo": tipo, "dist": round(dist, 1)})

    fg = get_fear_greed()
    funding = get_binance_funding()
    ls = get_binance_long_short()
    dom = get_btc_dominance()

    return {
        "price": price, "d1": d1, "d5": d5, "d20": d20,
        "rsi": round(rsi, 1),
        "ema20": round(ema20, 0), "ema50": round(ema50, 0),
        "sobre_ema20": price > ema20,
        "r1": round(r1, 0), "s1": round(s1, 0), "pivot": round(pivot, 0),
        "hi20": round(hi20, 0), "lo20": round(lo20, 0),
        "niveles": niveles,
        "fear_greed": fg,
        "funding": funding,
        "long_short": ls,
        "dominancia": dom,
    }


def send_btc_profundo(message):
    bot.send_chat_action(message.chat.id, "typing")
    try:
        d = analisis_btc_profundo()

        lines = []
        lines.append(f"**BITCOIN** — {datetime.now().strftime('%d/%m %H:%M')}")
        lines.append(f"Precio:   **{d['price']:,.0f} USD**")
        lines.append(f"Hoy:      {d['d1']:+.2f}%")
        lines.append(f"Semana:   {d['d5']:+.2f}%")
        lines.append(f"Mes:      {d['d20']:+.2f}%")
        lines.append(f"RSI:      {d['rsi']}")
        lines.append(f"EMA20:    {'SOBRE' if d['sobre_ema20'] else 'BAJO'} ({d['ema20']:,.0f})")
        lines.append(f"EMA50:    {d['ema50']:,.0f}")
        lines.append("")
        lines.append("**Soportes / Resistencias**")
        lines.append(f"R1: {d['r1']:,.0f}  |  Pivot: {d['pivot']:,.0f}  |  S1: {d['s1']:,.0f}")
        lines.append("")

        if d["niveles"]:
            lines.append("**Niveles psicológicos cercanos**")
            for n in d["niveles"][:4]:
                lines.append(f"{n['tipo']}: {n['nivel']:,} ({n['dist']:+.1f}%)")
            lines.append("")

        if d["fear_greed"]:
            fg = d["fear_greed"]
            lines.append(f"**Fear & Greed:** {fg['valor']:.0f}/100 — {fg['clasificacion']}")
            lines.append(f"Ayer: {fg['ayer']:.0f} ({'↑' if fg['cambio']>0 else '↓'})")
            lines.append("")

        if d["funding"] is not None:
            fr = d["funding"]
            señal = "Longs pagando (posible squeeze)" if fr > 0.02 else "Shorts pagando (posible rebote)" if fr < -0.01 else "Neutral"
            lines.append(f"**Funding Rate:** {fr:+.4f}% → {señal}")

        if d["long_short"]:
            ls = d["long_short"]
            lines.append(f"**Long/Short:** {ls['ratio']} ({ls['long_pct']}% longs / {ls['short_pct']}% shorts)")

        if d["dominancia"]:
            dom = d["dominancia"]
            lines.append(f"**Dominancia BTC:** {dom['btc_dom']}%  |  ETH: {dom['eth_dom']}%")
            lines.append(f"Market Cap total: {dom['total_mcap_b']:,.0f}B USD")

        # Conclusión simple
        lines.append("")
        if d["rsi"] > 70 and d["fear_greed"] and d["fear_greed"]["valor"] > 70:
            lines.append("**Sesgo:** Sobrecomprado + codicia → **Precaución**")
        elif d["rsi"] < 35 and d["fear_greed"] and d["fear_greed"]["valor"] < 30:
            lines.append("**Sesgo:** Sobreventa + miedo → **Zona de interés**")
        elif d["sobre_ema20"]:
            lines.append("**Sesgo:** Alcista (precio sobre EMA20)")
        else:
            lines.append("**Sesgo:** Neutral / Lateral")

        bot.send_message(message.chat.id, "\n".join(lines), reply_markup=get_main_keyboard())

    except Exception as e:
        bot.reply_to(message, f"⚠️ Error en análisis BTC: {e}")


# ===================== TECLADO Y HANDLERS =====================
def get_main_keyboard():
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("💎 Índice Valor", callback_data="valor_help"),
        InlineKeyboardButton("₿ BTC Profundo", callback_data="btc_profundo"),
        InlineKeyboardButton("📰 Noticias", callback_data="noticias"),
        InlineKeyboardButton("🔍 Investigar", callback_data="investigar_help"),
        InlineKeyboardButton("📊 Menú", callback_data="menu"),
    )
    return markup


@bot.message_handler(commands=["start", "menu"])
def send_welcome(message):
    if not is_authorized(message.from_user.id):
        return
    text = (
        "🤖 **Bot Financiero**\n\n"
        "• `/valor TICKER` → Medidor de valor\n"
        "• `/btc` → Análisis profundo Bitcoin\n"
        "• `/noticias` → Noticias en español\n"
        "• `/investigar TICKER` → Datos del activo\n\n"
        "_Ejemplos: /valor TSLA  |  /valor BTC_"
    )
    bot.reply_to(message, text, reply_markup=get_main_keyboard())


@bot.callback_query_handler(func=lambda call: True)
def handle_query(call):
    if not is_authorized(call.from_user.id):
        return
    if call.data == "noticias":
        bot.answer_callback_query(call.id)
        send_noticias(call.message)
    elif call.data == "investigar_help":
        bot.answer_callback_query(call.id)
        bot.send_message(call.message.chat.id, "🔍 Escribe: `/investigar TSLA`")
    elif call.data == "valor_help":
        bot.answer_callback_query(call.id)
        bot.send_message(call.message.chat.id, "💎 Escribe: `/valor TSLA`  o  `/valor BTC`")
    elif call.data == "btc_profundo":
        bot.answer_callback_query(call.id)
        send_btc_profundo(call.message)
    elif call.data == "menu":
        bot.answer_callback_query(call.id)
        send_welcome(call.message)


@bot.message_handler(commands=["valor", "value", "indice"])
def cmd_valor(message):
    if not is_authorized(message.from_user.id):
        return
    args = message.text.split()
    if len(args) < 2:
        bot.reply_to(message, "⚠️ Usa: `/valor TSLA`  o  `/valor BTC`")
        return
    send_valor(message, forced_ticker=args[1].upper())


def send_valor(message, forced_ticker=None):
    if not is_authorized(message.from_user.id):
        return
    ticker = forced_ticker or "BTC"
    bot.send_chat_action(message.chat.id, "upload_photo")
    try:
        data = calculate_value_index(ticker)
        title = f"{data['name']} ({data['ticker']}) — {data['price']:.2f}"
        img = create_gauge_image(data["score"], title, data["label"])
        lines = [f"**{data['name']} ({data['ticker']}) — {data['price']:.2f}**",
                 f"**{data['score']}/100 — {data['label']}**\n",
                 "**COMPONENTES:**"]
        for n, v, s in data["components"]:
            lines.append(f"{n}: {v} → {s}/10")
        bot.send_photo(message.chat.id, img, caption="\n".join(lines), reply_markup=get_main_keyboard())
    except Exception as e:
        bot.reply_to(message, f"⚠️ Error: {e}")


@bot.message_handler(commands=["btc"])
def cmd_btc(message):
    if not is_authorized(message.from_user.id):
        return
    send_btc_profundo(message)


@bot.message_handler(commands=["noticias"])
def send_noticias(message):
    if not is_authorized(message.from_user.id):
        return
    bot.send_chat_action(message.chat.id, "typing")
    rss_urls = [
        "https://feeds.finance.yahoo.com/rss/2.0/headline?s=^GSPC&region=ES&lang=es-ES",
        "https://www.expansion.com/rss/mercados.xml",
        "https://cincodias.elpais.com/rss/cincodias/portada.xml",
    ]
    headlines = []
    for url in rss_urls:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:3]:
                title = entry.title.strip()
                if title and title not in headlines:
                    headlines.append(f"• {title}")
        except:
            continue
    if not headlines:
        headlines = ["• No se pudieron cargar noticias ahora."]
    text = "📰 **NOTICIAS DE IMPACTO**\n\n" + "\n".join(headlines[:6])
    bot.send_message(message.chat.id, text, reply_markup=get_main_keyboard())


@bot.message_handler(commands=["investigar"])
def send_investigacion(message):
    if not is_authorized(message.from_user.id):
        return
    args = message.text.split()
    if len(args) < 2:
        bot.reply_to(message, "⚠️ Usa: `/investigar TSLA`")
        return
    ticker = args[1].upper()
    bot.send_chat_action(message.chat.id, "typing")
    try:
        stock = yf.Ticker(ticker)
        info = stock.info
        name = info.get("shortName") or info.get("longName") or ticker
        price = info.get("currentPrice") or info.get("regularMarketPrice") or "N/D"
        pe = info.get("forwardPE") or info.get("trailingPE") or "N/D"
        mcap = info.get("marketCap")
        mcap_str = f"{mcap/1e9:.1f}B $" if isinstance(mcap, (int, float)) else "N/D"
        sector = info.get("sector") or "N/D"
        msg = (f"🔍 **{name} ({ticker})**\n\n"
               f"• Precio: **{price}**\n"
               f"• P/E: {pe}\n"
               f"• Market Cap: {mcap_str}\n"
               f"• Sector: {sector}\n\n"
               f"_Usa /valor {ticker} para el medidor_")
        bot.send_message(message.chat.id, msg, reply_markup=get_main_keyboard())
    except Exception as e:
        bot.reply_to(message, f"⚠️ Error: {e}")


@bot.message_handler(func=lambda msg: True)
def handle_free(message):
    if not is_authorized(message.from_user.id):
        return
    if not message.text or message.text.startswith("/"):
        return
    bot.reply_to(message, "Usa `/valor TICKER`, `/btc` o `/noticias`", reply_markup=get_main_keyboard())


if __name__ == "__main__":
    print("🤖 Bot con BTC Profundo iniciado...")
    bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=30)