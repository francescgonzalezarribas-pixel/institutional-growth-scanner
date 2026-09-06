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

# --- CONFIGURACIÓN ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
MISTRAL_API_KEY = os.environ.get("MISTRAL_API_KEY", "").strip()
ALLOWED_USER_ID = int(os.environ.get("ALLOWED_USER_ID", 0))

bot = telebot.TeleBot(TELEGRAM_TOKEN, parse_mode="Markdown")


def is_authorized(user_id: int) -> bool:
    if ALLOWED_USER_ID == 0:
        return True
    return user_id == ALLOWED_USER_ID


# --- MISTRAL (OPCIONAL) ---
def ask_mistral(prompt: str, system_prompt: str = "Eres un analista financiero experto.") -> str:
    if not MISTRAL_API_KEY:
        return ""

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
        "max_tokens": 600,
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=20)
        data = response.json()
        if response.status_code == 200 and "choices" in data:
            return data["choices"][0]["message"]["content"]
        return ""
    except Exception as e:
        print(f"Mistral error: {e}")
        return ""


# --- INDICADORES ---
def compute_rsi(series: pd.Series, period: int = 14) -> float:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return float(rsi.iloc[-1]) if not pd.isna(rsi.iloc[-1]) else 50.0


def get_fear_greed() -> float:
    try:
        r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=8)
        data = r.json()
        return float(data["data"][0]["value"])
    except Exception:
        return 50.0


def get_vix() -> float:
    try:
        vix = yf.Ticker("^VIX")
        hist = vix.history(period="5d")
        return float(hist["Close"].iloc[-1])
    except Exception:
        return 20.0


def score_rsi(rsi: float) -> int:
    if rsi < 30: return 10
    if rsi < 40: return 8
    if rsi < 50: return 6
    if rsi < 60: return 5
    if rsi < 70: return 3
    if rsi < 80: return 2
    return 1


def score_ema_pct(pct: float) -> int:
    if pct < -20: return 10
    if pct < -10: return 8
    if pct < -5:  return 7
    if pct < 0:   return 6
    if pct < 5:   return 5
    if pct < 10:  return 4
    if pct < 20:  return 3
    return 2


def score_volume(rel_vol: float) -> int:
    if rel_vol > 2.0: return 8
    if rel_vol > 1.5: return 7
    if rel_vol > 1.2: return 6
    if rel_vol > 0.8: return 5
    if rel_vol > 0.5: return 4
    return 3


def score_dist_52w(pct: float) -> int:
    if pct < -40: return 10
    if pct < -30: return 9
    if pct < -20: return 8
    if pct < -10: return 7
    if pct < -5:  return 6
    if pct < 0:   return 5
    if pct < 5:   return 4
    return 2


def score_vix(vix: float) -> int:
    if vix > 30: return 9
    if vix > 25: return 7
    if vix > 20: return 5
    if vix > 15: return 4
    return 3


def score_fear_greed(fg: float) -> int:
    if fg < 25: return 10
    if fg < 40: return 8
    if fg < 55: return 5
    if fg < 70: return 3
    return 1


def get_label(score: int) -> str:
    if score >= 70: return "BARATO — OPORTUNIDAD"
    if score >= 55: return "NEUTRAL — ACUMULACIÓN"
    if score >= 40: return "NEUTRAL"
    if score >= 25: return "CARO — PRECAUCIÓN"
    return "MUY CARO — ALTO RIESGO"


def calculate_value_index(ticker: str) -> dict:
    ticker = ticker.upper().strip()
    is_crypto = ticker in ["BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "DOGE"] or "-USD" in ticker

    if ticker == "BTC": ticker = "BTC-USD"
    elif ticker == "ETH": ticker = "ETH-USD"
    elif ticker == "SOL": ticker = "SOL-USD"
    elif ticker == "BNB": ticker = "BNB-USD"

    stock = yf.Ticker(ticker)
    hist = stock.history(period="1y")

    if hist.empty or len(hist) < 50:
        raise ValueError(f"No hay datos suficientes para {ticker}")

    close = hist["Close"]
    volume = hist["Volume"]
    price = float(close.iloc[-1])

    rsi = compute_rsi(close, 14)
    rsi_score = score_rsi(rsi)

    ema200 = close.ewm(span=200, adjust=False).mean().iloc[-1]
    ema_pct = ((price - ema200) / ema200) * 100
    ema_score = score_ema_pct(ema_pct)

    avg_vol = volume.iloc[-21:-1].mean()
    rel_vol = float(volume.iloc[-1] / avg_vol) if avg_vol > 0 else 1.0
    vol_score = score_volume(rel_vol)

    high_52 = float(close.max())
    dist_52 = ((price - high_52) / high_52) * 100
    dist_score = score_dist_52w(dist_52)

    weekly_close = close.resample("W").last().dropna()
    rsi_weekly = compute_rsi(weekly_close, 14) if len(weekly_close) > 14 else 50.0
    rsi_w_score = score_rsi(rsi_weekly)

    components = []
    total_score = 0
    max_possible = 0

    components.append(("RSI 14d", f"{rsi:.1f}", rsi_score))
    total_score += rsi_score
    max_possible += 10

    components.append(("EMA200", f"{ema_pct:+.1f}%", ema_score))
    total_score += ema_score
    max_possible += 10

    components.append(("Volumen", f"{rel_vol:.2f}x", vol_score))
    total_score += vol_score
    max_possible += 10

    components.append(("Dist. Max 52s", f"{dist_52:.1f}%", dist_score))
    total_score += dist_score
    max_possible += 10

    components.append(("RSI Semanal", f"{rsi_weekly:.1f}", rsi_w_score))
    total_score += rsi_w_score
    max_possible += 10

    vix = get_vix()
    vix_score = score_vix(vix)
    components.append(("VIX", f"{vix:.2f}", vix_score))
    total_score += vix_score
    max_possible += 10

    if is_crypto or "BTC" in ticker or "ETH" in ticker:
        fg = get_fear_greed()
        fg_score = score_fear_greed(fg)
        components.append(("Fear&Greed", f"{fg:.0f}", fg_score))
        total_score += fg_score
        max_possible += 10

    final_score = int(round((total_score / max_possible) * 100))

    name = ticker
    try:
        info = stock.info
        name = info.get("shortName") or info.get("longName") or ticker
    except Exception:
        pass

    return {
        "ticker": ticker,
        "name": name,
        "price": price,
        "score": final_score,
        "label": get_label(final_score),
        "components": components,
    }


def create_gauge_image(score: int, title: str, label: str) -> io.BytesIO:
    fig, ax = plt.subplots(figsize=(8, 5), facecolor="#0d1117")
    ax.set_facecolor("#0d1117")
    ax.set_xlim(-1.3, 1.3)
    ax.set_ylim(-0.4, 1.3)
    ax.set_aspect("equal")
    ax.axis("off")

    colors = ["#ff3b30", "#ff9500", "#ffcc00", "#34c759", "#30d158"]
    angles = [180, 144, 108, 72, 36, 0]

    for i in range(5):
        wedge = Wedge((0, 0), 1.0, angles[i+1], angles[i], width=0.28,
                      facecolor=colors[i], edgecolor="#0d1117", linewidth=2)
        ax.add_patch(wedge)

    angle_deg = 180 - (score / 100) * 180
    angle_rad = math.radians(angle_deg)
    needle_len = 0.85
    ax.plot([0, needle_len * math.cos(angle_rad)],
            [0, needle_len * math.sin(angle_rad)],
            color="white", linewidth=4, solid_capstyle="round", zorder=10)

    ax.add_patch(Circle((0, 0), 0.08, facecolor="white", zorder=11))
    ax.add_patch(Circle((0, 0), 0.04, facecolor="#0d1117", zorder=12))

    ax.text(0, 1.18, title, ha="center", va="center", fontsize=14, color="white", fontweight="bold")
    ax.text(0, -0.25, f"{score}/100", ha="center", va="center", fontsize=28, color="white", fontweight="bold")
    ax.text(0, -0.42, label, ha="center", va="center", fontsize=13, color="#8b949e")
    ax.text(-1.15, -0.05, "CARO", ha="center", va="center", fontsize=11, color="#ff3b30", fontweight="bold")
    ax.text(1.15, -0.05, "BARATO", ha="center", va="center", fontsize=11, color="#30d158", fontweight="bold")
    ax.text(0, 1.05, "50\nNEUTRAL", ha="center", va="center", fontsize=9, color="#8b949e")

    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=150, bbox_inches="tight", facecolor="#0d1117")
    plt.close(fig)
    buf.seek(0)
    return buf


def get_main_keyboard():
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("💎 Índice Valor", callback_data="valor_help"),
        InlineKeyboardButton("₿ BTC Valor", callback_data="valor_btc"),
        InlineKeyboardButton("📰 Noticias", callback_data="noticias"),
        InlineKeyboardButton("🔍 Investigar", callback_data="investigar_help"),
        InlineKeyboardButton("🔮 Previsiones", callback_data="previsiones"),
        InlineKeyboardButton("📊 Menú", callback_data="menu"),
    )
    return markup


@bot.message_handler(commands=["start", "menu"])
def send_welcome(message):
    if not is_authorized(message.from_user.id):
        return
    text = (
        "🤖 **Bot Financiero - Índice de Valor**\n\n"
        "Comandos principales:\n"
        "• `/valor TICKER` → Medidor de valor (acciones o crypto)\n"
        "• `/btc` → Índice de valor de Bitcoin\n"
        "• `/investigar TICKER` → Análisis con IA\n"
        "• `/noticias` → Noticias de impacto\n"
        "• `/previsiones` → Perspectivas de mercado\n\n"
        "_Ejemplos: /valor TSLA  |  /valor BTC  |  /valor IONQ_"
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
        bot.send_message(call.message.chat.id, "🔍 Envía: `/investigar TSLA`")
    elif call.data == "previsiones":
        bot.answer_callback_query(call.id)
        send_previsiones(call.message)
    elif call.data == "valor_help":
        bot.answer_callback_query(call.id)
        bot.send_message(call.message.chat.id, "💎 Envía: `/valor TSLA`  o  `/valor BTC`")
    elif call.data == "valor_btc":
        bot.answer_callback_query(call.id, "Calculando BTC...")
        send_valor(call.message, forced_ticker="BTC")
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


def send_valor(message, forced_ticker: str = None):
    if not is_authorized(message.from_user.id):
        return

    ticker = forced_ticker or "BTC"
    bot.send_chat_action(message.chat.id, "upload_photo")

    try:
        data = calculate_value_index(ticker)
        title = f"{data['name']} ({data['ticker']}) — {data['price']:.2f}"
        img_buf = create_gauge_image(data["score"], title, data["label"])

        lines = [f"**{data['name']} ({data['ticker']}) — {data['price']:.2f}**"]
        lines.append(f"**{data['score']}/100 — {data['label']}**\n")
        lines.append("**COMPONENTES:**")
        for name, value, sc in data["components"]:
            lines.append(f"{name}: {value} → {sc}/10")

        bot.send_photo(
            message.chat.id,
            img_buf,
            caption="\n".join(lines),
            reply_markup=get_main_keyboard(),
        )
    except Exception as e:
        bot.reply_to(message, f"⚠️ Error calculando valor de {ticker}: {e}")


@bot.message_handler(commands=["btc"])
def cmd_btc(message):
    if not is_authorized(message.from_user.id):
        return
    send_valor(message, forced_ticker="BTC")


@bot.message_handler(commands=["noticias"])
def send_noticias(message):
    if not is_authorized(message.from_user.id):
        return
    bot.send_chat_action(message.chat.id, "typing")

    rss_urls = [
        "https://feeds.finance.yahoo.com/rss/2.0/headline?s=^GSPC&region=US&lang=en-US",
        "https://search.cnbc.com/rs/search/combinedrenderer/view.xml?partnerId=2000&keywords=markets&target=all",
    ]
    headlines = []
    for url in rss_urls:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:4]:
                headlines.append(f"• {entry.title}")
        except Exception:
            pass

    raw = "\n".join(headlines) if headlines else "No se pudieron obtener titulares."
    analysis = ask_mistral(f"Sintetiza estas noticias en 3 puntos clave de impacto:\n\n{raw}")

    text = f"📰 **NOTICIAS DE IMPACTO**\n\n{analysis}" if analysis else f"📰 **NOTICIAS DE IMPACTO**\n\n{raw}"
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
        name = info.get("shortName", ticker)
        price = info.get("currentPrice") or info.get("regularMarketPrice", "N/D")
        pe = info.get("forwardPE", "N/D")
        mcap = info.get("marketCap", "N/D")
        target = info.get("targetMeanPrice", "N/D")

        datos = f"Empresa: {name} ({ticker})\nPrecio: ${price}\nP/E Forward: {pe}\nMarket Cap: {mcap}\nTarget: ${target}"
        analysis = ask_mistral(f"Haz un informe breve (Diagnóstico, Riesgos/Oportunidades, Valoración):\n{datos}")

        msg = f"🔍 **INFORME: {ticker}**\n\n{analysis}" if analysis else f"🔍 **DATOS DE {ticker}**\n\n{datos}"
        bot.send_message(message.chat.id, msg, reply_markup=get_main_keyboard())
    except Exception as e:
        bot.reply_to(message, f"⚠️ Error con {ticker}: {e}")


@bot.message_handler(commands=["previsiones"])
def send_previsiones(message):
    if not is_authorized(message.from_user.id):
        return
    bot.send_chat_action(message.chat.id, "typing")

    try:
        sp500 = yf.Ticker("^GSPC")
        hist = sp500.history(period="1mo")
        precio = float(hist["Close"].iloc[-1])
        var = ((precio - float(hist["Close"].iloc[0])) / float(hist["Close"].iloc[0])) * 100
        analysis = ask_mistral(f"S&P 500 en {precio:.0f} ({var:+.1f}% último mes). Previsión corta y media plazo.")

        text = f"🔮 **PREVISIONES DE MERCADO**\n\n{analysis}" if analysis else f"🔮 **S&P 500**: {precio:.0f} ({var:+.1f}%)"
        bot.send_message(message.chat.id, text, reply_markup=get_main_keyboard())
    except Exception as e:
        bot.send_message(message.chat.id, f"⚠️ Error: {e}")


@bot.message_handler(func=lambda msg: True)
def handle_free(message):
    if not is_authorized(message.from_user.id):
        return
    if not message.text or message.text.startswith("/"):
        return

    bot.send_chat_action(message.chat.id, "typing")
    answer = ask_mistral(message.text)
    if answer:
        bot.reply_to(message, answer, reply_markup=get_main_keyboard())
    else:
        bot.reply_to(message, "Prueba `/valor TICKER` o `/noticias`.", reply_markup=get_main_keyboard())


if __name__ == "__main__":
    print("🤖 Bot Índice de Valor iniciado...")
    bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=30)