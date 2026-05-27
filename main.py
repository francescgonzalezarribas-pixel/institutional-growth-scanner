import os
import time
import logging
import requests
import feedparser
import telebot
import yfinance as yf
import pandas as pd
import numpy as np

from datetime import datetime
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from apscheduler.schedulers.background import BackgroundScheduler
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from mistralai.client import MistralClient

# =====================================================
# CONFIG
# =====================================================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY")
ALLOWED_USER_ID = int(os.getenv("ALLOWED_USER_ID", "0"))

bot = telebot.TeleBot(TELEGRAM_TOKEN)
ai = MistralClient(api_key=MISTRAL_API_KEY)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

CACHE = {}
CACHE_TIMEOUT = 300

ALERTS = defaultdict(list)
SEGUIMIENTO = defaultdict(list)

# =====================================================
# LISTAS
# =====================================================

US_STOCKS = [
    "NVDA","AAPL","MSFT","META","AMZN",
    "GOOGL","TSLA","AMD","PLTR","SMCI",
    "NFLX","COIN","SNOW","ARM","UBER"
]

EU_STOCKS = [
    "SAN.MC","BBVA.MC","IBE.MC",
    "MC.PA","ASML.AS","SAP.DE"
]

CRYPTO = [
    "BTC-USD","ETH-USD","SOL-USD","BNB-USD"
]

INDICES = {
    "^GSPC":"SP500",
    "^IXIC":"NASDAQ",
    "^DJI":"DOW JONES",
    "^GDAXI":"DAX",
    "^IBEX":"IBEX35"
}

METALES = {
    "GC=F":"ORO",
    "SI=F":"PLATA"
}

# =====================================================
# UTILS
# =====================================================


def allowed(msg):
    return not ALLOWED_USER_ID or msg.from_user.id == ALLOWED_USER_ID



def safe_send(chat_id, text):
    try:
        bot.send_message(chat_id, text[:4000])
    except Exception as e:
        log.warning(e)



def arrow(v):
    return f"{'🟢' if v >= 0 else '🔴'} {v:+.2f}%"


# =====================================================
# IA
# =====================================================


def ask_ai(prompt, max_chars=2500):

    try:

        r = ai.chat(
            model="mistral-small",
            messages=[
                {
                    "role":"system",
                    "content":"Eres un analista financiero profesional"
                },
                {
                    "role":"user",
                    "content":prompt
                }
            ]
        )

        return r.choices[0].message.content[:max_chars]

    except Exception as e:
        return f"Error IA: {e}"


# =====================================================
# INDICADORES
# =====================================================


def calc_rsi(series, period=14):

    delta = series.diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()

    rs = avg_gain / avg_loss

    rsi = 100 - (100 / (1 + rs))

    return rsi



def calc_macd(series):

    ema12 = series.ewm(span=12, adjust=False).mean()
    ema26 = series.ewm(span=26, adjust=False).mean()

    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()

    return macd, signal


# =====================================================
# FETCH
# =====================================================


def fetch_quote(ticker, period="3mo"):

    key = f"{ticker}_{period}"

    if key in CACHE:

        ts, data = CACHE[key]

        if time.time() - ts < CACHE_TIMEOUT:
            return data

    try:

        hist = yf.download(
            ticker,
            period=period,
            progress=False,
            auto_adjust=False,
            threads=False
        )

        if hist.empty or len(hist) < 30:
            return None

        c = hist["Close"]
        h = hist["High"]
        l = hist["Low"]
        v = hist["Volume"]

        price = float(c.iloc[-1])

        d1 = ((price - c.iloc[-2]) / c.iloc[-2]) * 100
        d5 = ((price - c.iloc[-6]) / c.iloc[-6]) * 100
        d20 = ((price - c.iloc[-21]) / c.iloc[-21]) * 100

        rsi = calc_rsi(c).iloc[-1]

        macd, signal = calc_macd(c)

        macd_cross_up = (
            macd.iloc[-1] > signal.iloc[-1]
            and macd.iloc[-2] <= signal.iloc[-2]
        )

        ema20 = c.ewm(span=20).mean().iloc[-1]
        ema50 = c.ewm(span=50).mean().iloc[-1]

        vol_avg = v.tail(20).mean()
        vol_rel = v.iloc[-1] / vol_avg if vol_avg > 0 else 1

        pivot = (h.iloc[-1] + l.iloc[-1] + c.iloc[-1]) / 3
        r1 = 2 * pivot - l.iloc[-1]
        s1 = 2 * pivot - h.iloc[-1]

        hi52 = h.max()
        lo52 = l.min()

        data = {
            "ticker": ticker,
            "nombre": ticker,
            "price": round(price,2),
            "d1": round(d1,2),
            "d5": round(d5,2),
            "d20": round(d20,2),
            "rsi": round(float(rsi),1),
            "macd_cross_up": macd_cross_up,
            "vol_rel": round(float(vol_rel),2),
            "ema20": round(float(ema20),2),
            "ema50": round(float(ema50),2),
            "sobre_ema20": price > ema20,
            "sobre_ema50": price > ema50,
            "pivot": round(float(pivot),2),
            "r1": round(float(r1),2),
            "s1": round(float(s1),2),
            "hi52": round(float(hi52),2),
            "lo52": round(float(lo52),2)
        }

        CACHE[key] = (time.time(), data)

        return data

    except Exception as e:

        log.warning(f"{ticker}: {e}")

        return None


# =====================================================
# SCANNERS
# =====================================================


def get_top_signals(tickers, n=3):

    out = []

    with ThreadPoolExecutor(max_workers=8) as ex:

        results = list(ex.map(fetch_quote, tickers))

    for d in results:

        if not d:
            continue

        score = 0

        if d["rsi"] > 50:
            score += 1

        if d["macd_cross_up"]:
            score += 2

        if d["vol_rel"] > 1.5:
            score += 1

        if d["sobre_ema20"]:
            score += 1

        if d["d5"] > 2:
            score += 1

        if score >= 4:

            d["score"] = score
            out.append(d)

    out.sort(key=lambda x: x["score"], reverse=True)

    return out[:n]



def scan_explosions(tickers):

    out = []

    for t in tickers:

        d = fetch_quote(t)

        if not d:
            continue

        if (
            d["vol_rel"] >= 2.0
            and d["d5"] >= 4
            and d["rsi"] < 75
        ):
            out.append(d)

    return out[:5]


# =====================================================
# TELEGRAM
# =====================================================


def main_kb():

    kb = InlineKeyboardMarkup()

    kb.row(
        InlineKeyboardButton("Mercados", callback_data="mercados"),
        InlineKeyboardButton("BTC", callback_data="btc")
    )

    kb.row(
        InlineKeyboardButton("Señales US", callback_data="senales_us"),
        InlineKeyboardButton("Señales EU", callback_data="senales_eu")
    )

    kb.row(
        InlineKeyboardButton("Explosiones", callback_data="explosiones"),
        InlineKeyboardButton("Crypto", callback_data="crypto")
    )

    return kb


# =====================================================
# SEND SIGNAL
# =====================================================


def send_signal(chat_id, s):

    riesgo = abs(s["price"] - s["s1"])

    tp = s["price"] + riesgo * 2

    txt = (
        f"🚀 SEÑAL\n\n"
        f"{s['ticker']}\n"
        f"Precio: {s['price']}\n"
        f"RSI: {s['rsi']}\n"
        f"Volumen: {s['vol_rel']}x\n"
        f"Semana: {s['d5']:+.2f}%\n\n"
        f"Entrada: {s['price']}\n"
        f"Stop: {s['s1']}\n"
        f"Objetivo: {round(tp,2)}"
    )

    safe_send(chat_id, txt)


# =====================================================
# START
# =====================================================


@bot.message_handler(commands=["start"])
def cmd_start(msg):

    if not allowed(msg):
        return

    safe_send(
        msg.chat.id,
        "Financial Bot V7 activo",
    )

    bot.send_message(
        msg.chat.id,
        "Selecciona opcion:",
        reply_markup=main_kb()
    )


# =====================================================
# MERCADOS
# =====================================================


@bot.message_handler(commands=["mercados"])
def cmd_mercados(msg):

    if not allowed(msg):
        return

    lines = []

    for t, n in INDICES.items():

        d = fetch_quote(t)

        if d:

            lines.append(
                f"{arrow(d['d1'])} {n}: {d['price']}"
            )

    safe_send(
        msg.chat.id,
        "📊 MERCADOS\n\n" + "\n".join(lines)
    )


# =====================================================
# BTC
# =====================================================


@bot.message_handler(commands=["btc"])
def cmd_btc(msg):

    if not allowed(msg):
        return

    d = fetch_quote("BTC-USD", "6mo")

    if not d:
        safe_send(msg.chat.id, "Error BTC")
        return

    prompt = (
        f"Bitcoin:\n"
        f"Precio {d['price']}\n"
        f"RSI {d['rsi']}\n"
        f"Semana {d['d5']}%\n"
        f"EMA20 {d['ema20']}\n"
        f"R1 {d['r1']} S1 {d['s1']}\n\n"
        f"Analiza tendencia y setup"
    )

    texto = ask_ai(prompt)

    safe_send(
        msg.chat.id,
        f"₿ BTC\n\n"
        f"Precio: {d['price']}\n"
        f"RSI: {d['rsi']}\n"
        f"Semana: {d['d5']}%\n\n"
        + texto
    )


# =====================================================
# SEÑALES US
# =====================================================


@bot.message_handler(commands=["senales_us"])
def cmd_senales_us(msg):

    if not allowed(msg):
        return

    safe_send(msg.chat.id, "Escaneando EEUU...")

    signals = get_top_signals(US_STOCKS)

    if not signals:
        safe_send(msg.chat.id, "Sin señales")
        return

    for s in signals:

        send_signal(msg.chat.id, s)

        time.sleep(1)


# =====================================================
# SEÑALES EU
# =====================================================


@bot.message_handler(commands=["senales_eu"])
def cmd_senales_eu(msg):

    if not allowed(msg):
        return

    signals = get_top_signals(EU_STOCKS)

    if not signals:
        safe_send(msg.chat.id, "Sin señales")
        return

    for s in signals:

        send_signal(msg.chat.id, s)


# =====================================================
# CRYPTO
# =====================================================


@bot.message_handler(commands=["crypto"])
def cmd_crypto(msg):

    if not allowed(msg):
        return

    lines = []

    for t in CRYPTO:

        d = fetch_quote(t)

        if d:

            lines.append(
                f"{d['ticker']} {d['price']} | RSI {d['rsi']}"
            )

    safe_send(
        msg.chat.id,
        "🪙 CRYPTO\n\n" + "\n".join(lines)
    )


# =====================================================
# EXPLOSIONES
# =====================================================


@bot.message_handler(commands=["explosiones"])
def cmd_explosiones(msg):

    if not allowed(msg):
        return

    out = scan_explosions(US_STOCKS + EU_STOCKS)

    if not out:
        safe_send(msg.chat.id, "Sin explosiones")
        return

    lines = []

    for d in out:

        lines.append(
            f"{d['ticker']} | {d['d5']}% | Vol {d['vol_rel']}x"
        )

    safe_send(
        msg.chat.id,
        "💥 EXPLOSIONES\n\n" + "\n".join(lines)
    )


# =====================================================
# ALERTAS
# =====================================================


@bot.message_handler(commands=["alerta"])
def cmd_alerta(msg):

    if not allowed(msg):
        return

    p = msg.text.split()

    if len(p) < 3:

        safe_send(
            msg.chat.id,
            "/alerta TICKER PRECIO"
        )

        return

    ticker = p[1].upper()
    price = float(p[2])

    ALERTS[msg.chat.id].append({
        "ticker": ticker,
        "price": price,
        "triggered": False
    })

    safe_send(msg.chat.id, "Alerta creada")


# =====================================================
# CHECK ALERTS
# =====================================================


def job_check_alerts():

    for chat_id, arr in ALERTS.items():

        for a in arr:

            if a["triggered"]:
                continue

            d = fetch_quote(a["ticker"])

            if not d:
                continue

            if d["price"] >= a["price"]:

                a["triggered"] = True

                safe_send(
                    chat_id,
                    f"🚨 ALERTA {a['ticker']} {d['price']}"
                )


# =====================================================
# CALLBACKS
# =====================================================


@bot.callback_query_handler(func=lambda c: True)
def callbacks(call):

    handlers = {
        "mercados": cmd_mercados,
        "btc": cmd_btc,
        "senales_us": cmd_senales_us,
        "senales_eu": cmd_senales_eu,
        "crypto": cmd_crypto,
        "explosiones": cmd_explosiones
    }

    fn = handlers.get(call.data)

    if fn:

        call.message.from_user = call.from_user

        fn(call.message)


# =====================================================
# AUTO JOBS
# =====================================================


def job_us_open():

    signals = get_top_signals(US_STOCKS)

    if not signals:
        return

    safe_send(ALLOWED_USER_ID, "🇺🇸 APERTURA EEUU")

    for s in signals:

        send_signal(ALLOWED_USER_ID, s)



def job_eu_open():

    signals = get_top_signals(EU_STOCKS)

    if not signals:
        return

    safe_send(ALLOWED_USER_ID, "🇪🇺 APERTURA EUROPA")

    for s in signals:

        send_signal(ALLOWED_USER_ID, s)



def job_crypto():

    d = fetch_quote("BTC-USD")

    if not d:
        return

    if d["vol_rel"] > 2:

        safe_send(
            ALLOWED_USER_ID,
            f"⚡ BTC movimiento fuerte {d['d1']}%"
        )


# =====================================================
# MAIN
# =====================================================


if __name__ == "__main__":

    scheduler = BackgroundScheduler()

    scheduler.add_job(job_check_alerts, "interval", minutes=5)

    scheduler.add_job(job_us_open, "cron", hour=15, minute=0)

    scheduler.add_job(job_eu_open, "cron", hour=9, minute=0)

    scheduler.add_job(job_crypto, "interval", hours=1)

    scheduler.start()

    log.info("BOT ARRANCADO")

    bot.infinity_polling(timeout=60, long_polling_timeout=60)