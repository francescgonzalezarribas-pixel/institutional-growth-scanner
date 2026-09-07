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
from matplotlib.patches import Wedge, Circle, Rectangle
from datetime import datetime

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


def compute_macd(series):
    ema12 = series.ewm(span=12, adjust=False).mean()
    ema26 = series.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    hist = macd - signal
    cross_up = len(macd) >= 2 and macd.iloc[-1] > signal.iloc[-1] and macd.iloc[-2] <= signal.iloc[-2]
    cross_down = len(macd) >= 2 and macd.iloc[-1] < signal.iloc[-1] and macd.iloc[-2] >= signal.iloc[-2]
    return {
        "macd": float(macd.iloc[-1]),
        "signal": float(signal.iloc[-1]),
        "hist": float(hist.iloc[-1]),
        "cross_up": cross_up,
        "cross_down": cross_down
    }


def get_fear_greed():
    try:
        r = requests.get("https://api.alternative.me/fng/?limit=2", timeout=8)
        data = r.json()["data"]
        actual = float(data[0]["value"])
        ayer = float(data[1]["value"]) if len(data) > 1 else actual
        return {"valor": actual, "ayer": ayer, "clasificacion": data[0]["value_classification"], "cambio": actual - ayer}
    except:
        return None


def get_vix() -> float:
    try:
        return float(yf.Ticker("^VIX").history(period="5d")["Close"].iloc[-1])
    except:
        return 18.0


def get_ticker_news(ticker: str, name: str, max_items=4):
    queries = [
        f"https://news.google.com/rss/search?q={ticker}+stock+OR+crypto&hl=es&gl=ES&ceid=ES:es",
        f"https://news.google.com/rss/search?q={name}&hl=es&gl=ES&ceid=ES:es",
    ]
    headlines = []
    for url in queries:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:4]:
                title = entry.title.strip()
                if " - " in title:
                    title = title.rsplit(" - ", 1)[0]
                if title and title not in headlines and len(title) > 25:
                    headlines.append(title)
            if len(headlines) >= max_items:
                break
        except:
            continue
    return headlines[:max_items]


# ===================== SCORING =====================
def score_rsi(rsi: float) -> float:
    if rsi <= 28: return 10
    if rsi <= 35: return 8.5
    if rsi <= 42: return 7
    if rsi <= 50: return 5
    if rsi <= 58: return 3.5
    if rsi <= 65: return 2
    if rsi <= 72: return 1
    return 0.5

def score_ema_pct(pct: float) -> float:
    if pct <= -30: return 10
    if pct <= -18: return 9
    if pct <= -10: return 7.5
    if pct <= -4:  return 6
    if pct <= 4:   return 4.5
    if pct <= 12:  return 2.5
    if pct <= 22:  return 1.5
    return 0.5

def score_dist_52w(pct: float) -> float:
    if pct <= -50: return 10
    if pct <= -35: return 9
    if pct <= -22: return 7.5
    if pct <= -12: return 6
    if pct <= -4:  return 4.5
    if pct <= 5:   return 3
    if pct <= 12:  return 1.5
    return 0.5

def score_volume(rel: float) -> float:
    if rel >= 2.2: return 8
    if rel >= 1.6: return 6.5
    if rel >= 1.15: return 5
    if rel >= 0.85: return 4
    if rel >= 0.55: return 3
    return 2

def score_macd(macd_data) -> float:
    if macd_data["cross_up"]: return 9
    if macd_data["hist"] > 0 and macd_data["macd"] > macd_data["signal"]: return 6.5
    if macd_data["cross_down"]: return 1
    if macd_data["hist"] < 0: return 2.5
    return 4

def score_rel_strength(pct: float) -> float:
    if pct >= 12: return 9
    if pct >= 6:  return 7.5
    if pct >= 2:  return 6
    if pct >= -2: return 4.5
    if pct >= -8: return 3
    return 1.5

def score_vix(vix: float) -> float:
    if vix >= 32: return 9
    if vix >= 26: return 7
    if vix >= 20: return 5
    if vix >= 15: return 3.5
    return 2

def score_fear_greed(fg: float) -> float:
    if fg <= 22: return 10
    if fg <= 35: return 8
    if fg <= 48: return 5.5
    if fg <= 62: return 3
    if fg <= 75: return 1.5
    return 0.5


def get_label(score: float) -> str:
    if score >= 75: return "BARATO — OPORTUNIDAD"
    if score >= 60: return "NEUTRAL — ACUMULACIÓN"
    if score >= 45: return "NEUTRAL"
    if score >= 30: return "CARO — PRECAUCIÓN"
    return "MUY CARO — ALTO RIESGO"


def get_outlook(score: float, rsi: float, ema_pct: float, dist_52: float, fg_val: float = None) -> str:
    """Genera una perspectiva corta y accionable"""
    if score >= 72:
        return "Zona de valor atractiva. Se puede acumular por tramos aprovechando debilidades."
    if score >= 58:
        return "Zona de acumulación. Mejor entrar con paciencia y en retrocesos."
    if score <= 28:
        return "Precio muy extendido y sentimiento extremo. Alto riesgo de corrección. Mejor esperar."
    if rsi >= 68 and dist_52 > -8:
        return "Sobrecomprado y cerca de máximos. Probable consolidación o pullback antes de continuar."
    if rsi <= 35 and dist_52 < -25:
        return "Sobreventa significativa. Posible rebote técnico en los próximos días."
    if ema_pct > 12 and (fg_val is None or fg_val > 65):
        return "Tendencia alcista pero extendida + codicia. Cuidado con nuevas entradas agresivas."
    if ema_pct < -8:
        return "Precio por debajo de la media de largo plazo. Posible zona de interés si hay estabilización."
    return "Situación equilibrada. Mejor esperar una confirmación más clara de dirección."


def score_to_color(score: float) -> str:
    if score >= 7.5: return "#30d158"
    if score >= 5.5: return "#ffd60a"
    if score >= 3.5: return "#ff9f0a"
    return "#ff453a"


# ===================== CÁLCULO PRINCIPAL =====================
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
    high = hist["High"]
    low = hist["Low"]
    volume = hist["Volume"]
    price = float(close.iloc[-1])

    # Indicadores
    rsi = compute_rsi(close)
    ema200 = close.ewm(span=200, adjust=False).mean().iloc[-1]
    ema_pct = ((price - ema200) / ema200) * 100
    avg_vol = volume.iloc[-21:-1].mean()
    rel_vol = float(volume.iloc[-1] / avg_vol) if avg_vol > 0 else 1.0
    high_52 = float(close.max())
    dist_52 = ((price - high_52) / high_52) * 100
    weekly = close.resample("W").last().dropna()
    rsi_w = compute_rsi(weekly, 14) if len(weekly) > 15 else 50.0
    macd_data = compute_macd(close)

    # Fuerza relativa
    try:
        bench = yf.Ticker("BTC-USD" if is_crypto else "SPY").history(period="3mo")["Close"]
        rel_str = ((price / float(close.iloc[-21])) - (float(bench.iloc[-1]) / float(bench.iloc[-21]))) * 100
    except:
        rel_str = 0.0

    # Soportes y Resistencias (Pivot + recientes)
    pivot = (high.iloc[-1] + low.iloc[-1] + close.iloc[-1]) / 3
    r1 = 2 * pivot - low.iloc[-1]
    s1 = 2 * pivot - high.iloc[-1]
    r2 = pivot + (high.iloc[-1] - low.iloc[-1])
    s2 = pivot - (high.iloc[-1] - low.iloc[-1])

    # Niveles redondos cercanos
    if price > 1000:
        step = 1000 if price > 20000 else 500
    elif price > 100:
        step = 5
    else:
        step = 1

    round_levels = []
    base = int(price / step) * step
    for i in range(-3, 4):
        lvl = base + i * step
        if lvl > 0:
            round_levels.append(lvl)

    supports = sorted([s for s in [s1, s2] + [l for l in round_levels if l < price]] )[-2:]
    resistances = sorted([r for r in [r1, r2] + [l for l in round_levels if l > price]])[:2]

    # Scoring
    weights = {
        "RSI 14d": 1.8, "Dist. Max 52s": 1.8, "EMA200": 1.3,
        "MACD": 1.2, "Fuerza Rel": 1.1, "RSI Semanal": 1.0,
        "Volumen": 0.8, "VIX": 0.7, "Fear&Greed": 1.3 if is_crypto else 0.0
    }

    components = []
    total_weighted = 0.0
    total_weight = 0.0

    comps_raw = [
        ("RSI 14d", f"{rsi:.1f}", score_rsi(rsi)),
        ("Dist. Max 52s", f"{dist_52:.1f}%", score_dist_52w(dist_52)),
        ("EMA200", f"{ema_pct:+.1f}%", score_ema_pct(ema_pct)),
        ("MACD", "Cruce ↑" if macd_data["cross_up"] else ("Cruce ↓" if macd_data["cross_down"] else f"{macd_data['hist']:+.1f}"), score_macd(macd_data)),
        ("Fuerza Rel", f"{rel_str:+.1f}%", score_rel_strength(rel_str)),
        ("RSI Semanal", f"{rsi_w:.1f}", score_rsi(rsi_w)),
        ("Volumen", f"{rel_vol:.2f}x", score_volume(rel_vol)),
    ]

    vix = get_vix()
    comps_raw.append(("VIX", f"{vix:.1f}", score_vix(vix)))

    fg_val = None
    if is_crypto:
        fg = get_fear_greed()
        fg_val = fg["valor"] if fg else 50
        comps_raw.append(("Fear&Greed", f"{fg_val:.0f}", score_fear_greed(fg_val)))

    for name, val, sc in comps_raw:
        w = weights.get(name, 1.0)
        if w == 0: continue
        components.append((name, val, round(sc, 1)))
        total_weighted += sc * w
        total_weight += w

    final_score = (total_weighted / total_weight) * 10
    final_score = max(0, min(100, round(final_score)))

    name = ticker
    try:
        info = stock.info
        name = info.get("shortName") or info.get("longName") or ticker
    except:
        pass

    outlook = get_outlook(final_score, rsi, ema_pct, dist_52, fg_val)
    news = get_ticker_news(ticker.replace("-USD", ""), name)

    return {
        "ticker": ticker, "name": name, "price": price,
        "score": int(final_score),
        "label": get_label(final_score),
        "components": components,
        "outlook": outlook,
        "supports": [round(s, 2) if price < 1000 else int(round(s)) for s in supports],
        "resistances": [round(r, 2) if price < 1000 else int(round(r)) for r in resistances],
        "news": news
    }


# ===================== GAUGE =====================
def create_gauge_image(score: int, title: str, label: str, components: list) -> io.BytesIO:
    fig = plt.figure(figsize=(10, 9.2), facecolor="#0b0f14")

    # Gauge
    ax1 = fig.add_axes([0.08, 0.46, 0.84, 0.50])
    ax1.set_facecolor("#0b0f14")
    ax1.set_xlim(-1.4, 1.4)
    ax1.set_ylim(-0.55, 1.4)
    ax1.set_aspect("equal")
    ax1.axis("off")

    colors = ["#ff453a", "#ff9f0a", "#ffd60a", "#30d158", "#00c7be"]
    angles = [180, 144, 108, 72, 36, 0]
    for i in range(5):
        ax1.add_patch(Wedge((0, 0), 1.15, angles[i+1], angles[i], width=0.34,
                            facecolor=colors[i], edgecolor="#0b0f14", linewidth=3))

    angle_deg = 180 - (score / 100) * 180
    angle_rad = math.radians(angle_deg)
    ax1.plot([0, 0.95 * math.cos(angle_rad)], [0, 0.95 * math.sin(angle_rad)],
             color="white", linewidth=5.5, solid_capstyle="round", zorder=10)
    ax1.add_patch(Circle((0, 0), 0.12, facecolor="white", zorder=11))
    ax1.add_patch(Circle((0, 0), 0.06, facecolor="#0b0f14", zorder=12))

    ax1.text(0, 1.32, title, ha="center", va="center", fontsize=14, color="#e6edf3", fontweight="bold")
    ax1.text(0, -0.28, f"{score}/100", ha="center", va="center", fontsize=36, color="white", fontweight="bold")
    ax1.text(0, -0.50, label, ha="center", va="center", fontsize=13, color="#8b949e")
    ax1.text(-1.28, -0.12, "CARO", ha="center", va="center", fontsize=12, color="#ff453a", fontweight="bold")
    ax1.text(1.28, -0.12, "BARATO", ha="center", va="center", fontsize=12, color="#30d158", fontweight="bold")

    # Barras componentes
    ax2 = fig.add_axes([0.07, 0.03, 0.86, 0.40])
    ax2.set_facecolor("#0b0f14")
    ax2.set_xlim(0, 10)
    ax2.set_ylim(-0.4, len(components) + 0.6)
    ax2.axis("off")

    ax2.text(5, len(components) + 0.25, "COMPONENTES", ha="center", va="center",
             fontsize=11, color="#8b949e", fontweight="bold")

    for i, (name, value, sc) in enumerate(reversed(components)):
        y = i
        color = score_to_color(sc)
        ax2.add_patch(Rectangle((2.9, y - 0.22), 5.4, 0.44, facecolor="#21262d", edgecolor="none"))
        ax2.add_patch(Rectangle((2.9, y - 0.22), (sc / 10) * 5.4, 0.44, facecolor=color, edgecolor="none", alpha=0.9))
        ax2.text(0.15, y, name, ha="left", va="center", fontsize=9.5, color="#e6edf3")
        ax2.text(2.75, y, value, ha="right", va="center", fontsize=8.5, color="#8b949e")
        ax2.text(8.5, y, f"{sc}/10", ha="left", va="center", fontsize=9.5, color=color, fontweight="bold")

    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=160, facecolor="#0b0f14", bbox_inches="tight", pad_inches=0.18)
    plt.close(fig)
    buf.seek(0)
    return buf


# ===================== BTC PROFUNDO =====================
def analisis_btc_profundo():
    hist = yf.Ticker("BTC-USD").history(period="1y")
    close = hist["Close"]
    high = hist["High"]
    low = hist["Low"]
    price = float(close.iloc[-1])
    d1 = (price - float(close.iloc[-2])) / float(close.iloc[-2]) * 100
    d5 = (price - float(close.iloc[-6])) / float(close.iloc[-6]) * 100 if len(close) > 5 else 0
    d20 = (price - float(close.iloc[-21])) / float(close.iloc[-21]) * 100 if len(close) > 20 else 0
    rsi = compute_rsi(close)
    ema20 = close.ewm(span=20, adjust=False).mean().iloc[-1]
    ema50 = close.ewm(span=50, adjust=False).mean().iloc[-1]
    pivot = (high.iloc[-1] + low.iloc[-1] + close.iloc[-1]) / 3
    r1 = 2 * pivot - low.iloc[-1]
    s1 = 2 * pivot - high.iloc[-1]

    return {
        "price": price, "d1": d1, "d5": d5, "d20": d20,
        "rsi": round(rsi, 1), "ema20": round(ema20, 0), "ema50": round(ema50, 0),
        "sobre_ema20": price > ema20,
        "r1": round(r1, 0), "s1": round(s1, 0), "pivot": round(pivot, 0),
        "fear_greed": get_fear_greed(),
    }


def send_btc_profundo(message):
    bot.send_chat_action(message.chat.id, "typing")
    try:
        d = analisis_btc_profundo()
        lines = [
            f"**BITCOIN** — {datetime.now().strftime('%d/%m %H:%M')}",
            f"Precio:   **{d['price']:,.0f} USD**",
            f"Hoy:      {d['d1']:+.2f}%  |  Semana: {d['d5']:+.2f}%  |  Mes: {d['d20']:+.2f}%",
            f"RSI:      {d['rsi']}",
            f"EMA20:    {'SOBRE' if d['sobre_ema20'] else 'BAJO'} ({d['ema20']:,.0f})  |  EMA50: {d['ema50']:,.0f}",
            f"\n**S/R**  R1: {d['r1']:,.0f}  |  Pivot: {d['pivot']:,.0f}  |  S1: {d['s1']:,.0f}",
        ]
        if d["fear_greed"]:
            fg = d["fear_greed"]
            lines.append(f"\n**Fear & Greed:** {fg['valor']:.0f}/100 — {fg['clasificacion']}")
        bot.send_message(message.chat.id, "\n".join(lines), reply_markup=get_main_keyboard())
    except Exception as e:
        bot.reply_to(message, f"⚠️ Error BTC: {e}")


# ===================== HANDLERS =====================
def get_main_keyboard():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("💎 Índice Valor", callback_data="valor_help"),
        InlineKeyboardButton("₿ BTC Profundo", callback_data="btc_profundo"),
        InlineKeyboardButton("📰 Noticias", callback_data="noticias"),
        InlineKeyboardButton("🔍 Investigar", callback_data="investigar_help"),
        InlineKeyboardButton("📊 Menú", callback_data="menu"),
    )
    return kb


@bot.message_handler(commands=["start", "menu"])
def send_welcome(message):
    if not is_authorized(message.from_user.id): return
    text = ("🤖 **Bot Financiero**\n\n"
            "• `/valor TICKER` → Medidor completo + S/R + perspectiva + noticias\n"
            "• `/btc` → Análisis Bitcoin\n"
            "• `/noticias` → Noticias generales\n"
            "• `/investigar TICKER` → Datos del activo")
    bot.reply_to(message, text, reply_markup=get_main_keyboard())


@bot.callback_query_handler(func=lambda call: True)
def handle_query(call):
    if not is_authorized(call.from_user.id): return
    if call.data == "noticias":
        bot.answer_callback_query(call.id)
        send_noticias(call.message)
    elif call.data == "investigar_help":
        bot.answer_callback_query(call.id)
        bot.send_message(call.message.chat.id, "🔍 Escribe: `/investigar TSLA`")
    elif call.data == "valor_help":
        bot.answer_callback_query(call.id)
        bot.send_message(call.message.chat.id, "💎 Escribe: `/valor TSLA` o `/valor BTC`")
    elif call.data == "btc_profundo":
        bot.answer_callback_query(call.id)
        send_btc_profundo(call.message)
    elif call.data == "menu":
        bot.answer_callback_query(call.id)
        send_welcome(call.message)


@bot.message_handler(commands=["valor", "value", "indice"])
def cmd_valor(message):
    if not is_authorized(message.from_user.id): return
    args = message.text.split()
    if len(args) < 2:
        bot.reply_to(message, "⚠️ Usa: `/valor TSLA`")
        return
    send_valor(message, args[1].upper())


def send_valor(message, ticker):
    bot.send_chat_action(message.chat.id, "upload_photo")
    try:
        data = calculate_value_index(ticker)
        title = f"{data['name']} ({data['ticker']}) — {data['price']:.2f}"
        img = create_gauge_image(data["score"], title, data["label"], data["components"])

        # Caption completo
        lines = [
            f"**{data['name']} ({data['ticker']}) — {data['price']:.2f}**",
            f"**{data['score']}/100 — {data['label']}**",
            "",
            f"**Soportes:** {'  |  '.join(str(s) for s in data['supports'])}",
            f"**Resistencias:** {'  |  '.join(str(r) for r in data['resistances'])}",
            "",
            f"**Qué esperar:**\n{data['outlook']}",
        ]

        if data["news"]:
            lines.append("\n**Noticias recientes:**")
            for n in data["news"][:3]:
                lines.append(f"• {n}")

        bot.send_photo(message.chat.id, img, caption="\n".join(lines), reply_markup=get_main_keyboard())
    except Exception as e:
        bot.reply_to(message, f"⚠️ Error: {e}")


@bot.message_handler(commands=["btc"])
def cmd_btc(message):
    if not is_authorized(message.from_user.id): return
    send_btc_profundo(message)


@bot.message_handler(commands=["noticias"])
def send_noticias(message):
    if not is_authorized(message.from_user.id): return
    bot.send_chat_action(message.chat.id, "typing")
    rss = [
        "https://feeds.finance.yahoo.com/rss/2.0/headline?s=^GSPC&region=ES&lang=es-ES",
        "https://www.expansion.com/rss/mercados.xml",
        "https://cincodias.elpais.com/rss/cincodias/portada.xml",
    ]
    headlines = []
    for url in rss:
        try:
            feed = feedparser.parse(url)
            for e in feed.entries[:3]:
                t = e.title.strip()
                if t and t not in headlines:
                    headlines.append(f"• {t}")
        except: continue
    text = "📰 **NOTICIAS DE IMPACTO**\n\n" + "\n".join(headlines[:6] or ["• Sin noticias ahora"])
    bot.send_message(message.chat.id, text, reply_markup=get_main_keyboard())


@bot.message_handler(commands=["investigar"])
def send_investigacion(message):
    if not is_authorized(message.from_user.id): return
    args = message.text.split()
    if len(args) < 2:
        bot.reply_to(message, "⚠️ Usa: `/investigar TSLA`")
        return
    ticker = args[1].upper()
    try:
        info = yf.Ticker(ticker).info
        name = info.get("shortName") or info.get("longName") or ticker
        price = info.get("currentPrice") or info.get("regularMarketPrice") or "N/D"
        pe = info.get("forwardPE") or info.get("trailingPE") or "N/D"
        mcap = info.get("marketCap")
        mcap_str = f"{mcap/1e9:.1f}B $" if isinstance(mcap, (int, float)) else "N/D"
        msg = (f"🔍 **{name} ({ticker})**\n\n"
               f"• Precio: **{price}**\n• P/E: {pe}\n• Market Cap: {mcap_str}\n"
               f"• Sector: {info.get('sector', 'N/D')}\n\n_Usa /valor {ticker}_")
        bot.send_message(message.chat.id, msg, reply_markup=get_main_keyboard())
    except Exception as e:
        bot.reply_to(message, f"⚠️ Error: {e}")


@bot.message_handler(func=lambda m: True)
def handle_free(message):
    if not is_authorized(message.from_user.id): return
    if message.text and not message.text.startswith("/"):
        bot.reply_to(message, "Usa `/valor TICKER`, `/btc` o `/noticias`", reply_markup=get_main_keyboard())


if __name__ == "__main__":
    print("🤖 Bot Índice Valor completo iniciado...")
    bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=30)