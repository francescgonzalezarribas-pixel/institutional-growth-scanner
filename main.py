import os
import io
import math
import time
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


# ===================== TICKER RESOLVER =====================
CRYPTO_MAP = {
    "BTC": "BTC-USD", "ETH": "ETH-USD", "SOL": "SOL-USD", "BNB": "BNB-USD",
    "XRP": "XRP-USD", "ADA": "ADA-USD", "DOGE": "DOGE-USD", "DOT": "DOT-USD",
    "HBAR": "HBAR-USD", "AVAX": "AVAX-USD", "LINK": "LINK-USD", "MATIC": "MATIC-USD",
    "POL": "POL-USD", "SHIB": "SHIB-USD", "PEPE": "PEPE-USD", "NEAR": "NEAR-USD",
    "ATOM": "ATOM-USD", "LTC": "LTC-USD", "BCH": "BCH-USD", "UNI": "UNI-USD",
    "AAVE": "AAVE-USD", "ARB": "ARB-USD", "OP": "OP-USD", "SUI": "SUI-USD",
    "SEI": "SEI-USD", "TIA": "TIA-USD", "INJ": "INJ-USD", "FET": "FET-USD",
    "RENDER": "RENDER-USD", "RNDR": "RENDER-USD", "WIF": "WIF-USD", "BONK": "BONK-USD",
    "FLOKI": "FLOKI-USD", "TON": "TON-USD", "TRX": "TRX-USD", "XLM": "XLM-USD",
    "ALGO": "ALGO-USD", "VET": "VET-USD", "ICP": "ICP-USD", "FIL": "FIL-USD",
    "APT": "APT-USD", "STX": "STX-USD", "IMX": "IMX-USD", "GRT": "GRT-USD",
    "SAND": "SAND-USD", "MANA": "MANA-USD", "AXS": "AXS-USD", "CRV": "CRV-USD",
    "LDO": "LDO-USD", "RUNE": "RUNE-USD", "GALA": "GALA-USD", "APE": "APE-USD",
}

def resolve_ticker(raw: str) -> str:
    t = raw.upper().strip().replace("USDT", "").replace("/USD", "").replace("-USD", "")
    if t in CRYPTO_MAP:
        return CRYPTO_MAP[t]
    if len(t) <= 6 and t.isalpha():
        return f"{t}-USD"
    return t


# ===================== LISTAS DE ACCIONES =====================
EU_STOCKS = [
    "SAN.MC", "BBVA.MC", "ITX.MC", "REP.MC", "TEF.MC", "IBE.MC", "ELE.MC", "AMS.MC",
    "OR.PA", "BNP.PA", "AIR.PA", "MC.PA", "TTE.PA", "BN.PA",
    "BMW.DE", "BAS.DE", "DTE.DE", "SIE.DE", "SAP", "ALV.DE",
    "ASML", "NESN.SW", "NOVO-B.CO", "HSBA.L", "BP.L", "SHEL.L"
]

US_STOCKS = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AMD", "NFLX",
    "JPM", "V", "MA", "JNJ", "UNH", "XOM", "CVX", "WMT", "HD", "KO", "PEP",
    "BA", "CAT", "DIS", "CRM", "ORCL", "INTC", "PLTR", "COIN", "HOOD", "SOFI",
    "RIVN", "LCID", "SMCI", "ARM", "AVGO", "QCOM", "MU", "SHOP", "SNOW", "CRWD"
]


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
    return {
        "hist": float(hist.iloc[-1]),
        "cross_up": cross_up,
        "macd": float(macd.iloc[-1]),
        "signal": float(signal.iloc[-1])
    }


def get_fear_greed():
    try:
        r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=6)
        return float(r.json()["data"][0]["value"])
    except:
        return 50.0


def get_vix() -> float:
    try:
        return float(yf.Ticker("^VIX").history(period="5d")["Close"].iloc[-1])
    except:
        return 18.0


def get_ticker_news(ticker: str, name: str, max_items=3):
    clean = ticker.replace("-USD", "")
    url = f"https://news.google.com/rss/search?q={clean}+OR+{name}&hl=es&gl=ES&ceid=ES:es"
    headlines = []
    try:
        feed = feedparser.parse(url)
        for entry in feed.entries[:5]:
            title = entry.title.strip()
            if " - " in title:
                title = title.rsplit(" - ", 1)[0]
            if title and len(title) > 20 and title not in headlines:
                headlines.append(title)
    except:
        pass
    return headlines[:max_items]


# ===================== SCORING VALOR =====================
def score_rsi(rsi): 
    if rsi <= 28: return 10
    if rsi <= 35: return 8.5
    if rsi <= 42: return 7
    if rsi <= 50: return 5
    if rsi <= 58: return 3.5
    if rsi <= 65: return 2
    if rsi <= 72: return 1
    return 0.5

def score_ema_pct(pct):
    if pct <= -30: return 10
    if pct <= -18: return 9
    if pct <= -10: return 7.5
    if pct <= -4: return 6
    if pct <= 4: return 4.5
    if pct <= 12: return 2.5
    if pct <= 22: return 1.5
    return 0.5

def score_dist_52w(pct):
    if pct <= -50: return 10
    if pct <= -35: return 9
    if pct <= -22: return 7.5
    if pct <= -12: return 6
    if pct <= -4: return 4.5
    if pct <= 5: return 3
    if pct <= 12: return 1.5
    return 0.5

def score_volume(rel):
    if rel >= 2.2: return 8
    if rel >= 1.6: return 6.5
    if rel >= 1.15: return 5
    if rel >= 0.85: return 4
    if rel >= 0.55: return 3
    return 2

def score_macd(m):
    if m["cross_up"]: return 9
    if m["hist"] > 0: return 6.5
    if m["hist"] < 0: return 2.5
    return 4

def score_rel_strength(pct):
    if pct >= 12: return 9
    if pct >= 6: return 7.5
    if pct >= 2: return 6
    if pct >= -2: return 4.5
    if pct >= -8: return 3
    return 1.5

def score_vix(v): 
    if v >= 32: return 9
    if v >= 26: return 7
    if v >= 20: return 5
    if v >= 15: return 3.5
    return 2

def score_fg(fg):
    if fg <= 22: return 10
    if fg <= 35: return 8
    if fg <= 48: return 5.5
    if fg <= 62: return 3
    if fg <= 75: return 1.5
    return 0.5

def get_label(score):
    if score >= 75: return "BARATO — OPORTUNIDAD"
    if score >= 60: return "NEUTRAL — ACUMULACIÓN"
    if score >= 45: return "NEUTRAL"
    if score >= 30: return "CARO — PRECAUCIÓN"
    return "MUY CARO — ALTO RIESGO"

def get_outlook(score, rsi, ema_pct, dist_52, d1, fg=None):
    if score >= 72: return "Zona de valor atractiva. Se puede acumular por tramos."
    if score >= 58: return "Zona de acumulación. Mejor entrar en retrocesos."
    if score <= 28: return "Precio muy extendido. Alto riesgo de corrección. Mejor esperar."
    if rsi >= 70 and dist_52 > -10: return "Sobrecomprado y cerca de máximos. Probable pullback."
    if rsi <= 32 and dist_52 < -25: return "Sobreventa importante. Posible rebote técnico."
    if d1 <= -4 and rsi < 45: return "Caída fuerte reciente. Posible zona de interés si se estabiliza."
    return "Situación equilibrada. Mejor esperar confirmación de dirección."

def score_to_color(s):
    if s >= 7.5: return "#30d158"
    if s >= 5.5: return "#ffd60a"
    if s >= 3.5: return "#ff9f0a"
    return "#ff453a"


# ===================== CÁLCULO VALOR =====================
def calculate_value_index(raw_ticker: str) -> dict:
    ticker = resolve_ticker(raw_ticker)
    is_crypto = "-USD" in ticker

    stock = yf.Ticker(ticker)
    hist = stock.history(period="1y")

    if (hist.empty or len(hist) < 25) and not ticker.endswith("-USD"):
        stock = yf.Ticker(ticker + "-USD")
        hist = stock.history(period="1y")
        ticker = ticker + "-USD"
        is_crypto = True

    if hist.empty or len(hist) < 25:
        raise ValueError(f"No hay datos suficientes para {raw_ticker.upper()}")

    close = hist["Close"]
    high = hist["High"]
    low = hist["Low"]
    volume = hist["Volume"]
    price = float(close.iloc[-1])
    d1 = (price - float(close.iloc[-2])) / float(close.iloc[-2]) * 100 if len(close) > 1 else 0

    rsi = compute_rsi(close)
    ema200 = close.ewm(span=min(200, len(close)-1), adjust=False).mean().iloc[-1]
    ema_pct = ((price - ema200) / ema200) * 100
    avg_vol = volume.iloc[-15:-1].mean() if len(volume) > 15 else volume.mean()
    rel_vol = float(volume.iloc[-1] / avg_vol) if avg_vol > 0 else 1.0
    high_52 = float(close.max())
    dist_52 = ((price - high_52) / high_52) * 100
    weekly = close.resample("W").last().dropna()
    rsi_w = compute_rsi(weekly, 14) if len(weekly) > 14 else 50.0
    macd_data = compute_macd(close)

    try:
        bench = yf.Ticker("BTC-USD" if is_crypto else "SPY").history(period="3mo")["Close"]
        rel_str = ((price / float(close.iloc[-min(21, len(close)-1)])) - (float(bench.iloc[-1]) / float(bench.iloc[-min(21, len(bench)-1)]))) * 100
    except:
        rel_str = 0.0

    # S/R
    pivot = (high.iloc[-1] + low.iloc[-1] + close.iloc[-1]) / 3
    r1 = 2 * pivot - low.iloc[-1]
    s1 = 2 * pivot - high.iloc[-1]
    recent_high = float(high.tail(15).max())
    recent_low = float(low.tail(15).min())

    supports = sorted(set([round(s, 4) if price < 10 else (round(s, 2) if price < 1000 else int(round(s))) 
                          for s in [s1, recent_low] if s > 0 and s < price]))[-2:]
    resistances = sorted(set([round(r, 4) if price < 10 else (round(r, 2) if price < 1000 else int(round(r))) 
                             for r in [r1, recent_high] if r > price]))[:2]

    weights = {"RSI 14d": 1.8, "Dist. Max 52s": 1.8, "EMA200": 1.3, "MACD": 1.2,
               "Fuerza Rel": 1.1, "RSI Semanal": 1.0, "Volumen": 0.8, "VIX": 0.7,
               "Fear&Greed": 1.3 if is_crypto else 0}

    components = []
    total_w = total_weight = 0.0

    comps = [
        ("RSI 14d", f"{rsi:.1f}", score_rsi(rsi)),
        ("Dist. Max 52s", f"{dist_52:.1f}%", score_dist_52w(dist_52)),
        ("EMA200", f"{ema_pct:+.1f}%", score_ema_pct(ema_pct)),
        ("MACD", "Cruce ↑" if macd_data["cross_up"] else f"{macd_data['hist']:+.1f}", score_macd(macd_data)),
        ("Fuerza Rel", f"{rel_str:+.1f}%", score_rel_strength(rel_str)),
        ("RSI Semanal", f"{rsi_w:.1f}", score_rsi(rsi_w)),
        ("Volumen", f"{rel_vol:.2f}x", score_volume(rel_vol)),
    ]
    vix = get_vix()
    comps.append(("VIX", f"{vix:.1f}", score_vix(vix)))
    fg = get_fear_greed() if is_crypto else None
    if is_crypto:
        comps.append(("Fear&Greed", f"{fg:.0f}", score_fg(fg)))

    for name, val, sc in comps:
        w = weights.get(name, 1.0)
        if w == 0: continue
        components.append((name, val, round(sc, 1)))
        total_w += sc * w
        total_weight += w

    final_score = max(0, min(100, round((total_w / total_weight) * 10)))

    try:
        name = stock.info.get("shortName") or stock.info.get("longName") or ticker
    except:
        name = ticker

    return {
        "ticker": ticker, "name": name, "price": price, "d1": d1,
        "score": final_score, "label": get_label(final_score),
        "components": components,
        "outlook": get_outlook(final_score, rsi, ema_pct, dist_52, d1, fg),
        "supports": supports, "resistances": resistances,
        "news": get_ticker_news(ticker, name)
    }


# ===================== SEÑALES =====================
def fetch_signal_data(ticker):
    try:
        hist = yf.Ticker(ticker).history(period="6mo")
        if hist.empty or len(hist) < 30:
            return None
        close = hist["Close"]
        high = hist["High"]
        low = hist["Low"]
        volume = hist["Volume"]
        price = float(close.iloc[-1])

        rsi = compute_rsi(close)
        macd = compute_macd(close)
        ema20 = close.ewm(span=20, adjust=False).mean().iloc[-1]
        ema50 = close.ewm(span=50, adjust=False).mean().iloc[-1]
        avg_vol = volume.tail(20).mean()
        vol_rel = float(volume.iloc[-1] / avg_vol) if avg_vol > 0 else 1

        # ATR
        tr = pd.concat([high-low, (high-close.shift()).abs(), (low-close.shift()).abs()], axis=1).max(axis=1)
        atr = float(tr.ewm(span=14, adjust=False).mean().iloc[-1])

        return {
            "ticker": ticker, "price": price, "rsi": rsi, "macd": macd,
            "ema20": ema20, "ema50": ema50, "vol_rel": vol_rel, "atr": atr,
            "sobre_ema20": price > ema20, "tendencia": ema20 > ema50
        }
    except:
        return None


def get_top_signals(stocks, n=4):
    candidatos = []
    for t in stocks:
        d = fetch_signal_data(t)
        if not d:
            continue
        if d["rsi"] > 68 or d["vol_rel"] < 0.7:
            continue

        score = 0
        motivos = []

        if d["rsi"] < 30:
            score += 5; motivos.append(f"RSI {d['rsi']:.0f} sobreventa extrema")
        elif d["rsi"] < 40:
            score += 4; motivos.append(f"RSI {d['rsi']:.0f} zona de entrada")
        elif d["rsi"] < 50:
            score += 2; motivos.append(f"RSI {d['rsi']:.0f} saludable")

        if d["macd"]["cross_up"]:
            score += 4; motivos.append("MACD cruce alcista")
        elif d["macd"]["hist"] > 0:
            score += 2

        if d["vol_rel"] >= 1.8:
            score += 3; motivos.append(f"Volumen {d['vol_rel']:.1f}x elevado")
        elif d["vol_rel"] >= 1.2:
            score += 1

        if d["tendencia"] and d["sobre_ema20"]:
            score += 3; motivos.append("Tendencia alcista + precio sobre EMA20")
        elif d["sobre_ema20"]:
            score += 1

        if score < 7:
            continue

        entry = d["price"]
        stop = round(entry - d["atr"] * 1.5, 2)
        if stop <= 0:
            stop = round(entry * 0.97, 2)
        risk = entry - stop
        tp1 = round(entry + risk * 1.5, 2)
        tp2 = round(entry + risk * 2.8, 2)
        rr = round((tp1 - entry) / risk, 1) if risk > 0 else 0

        try:
            name = yf.Ticker(t).info.get("shortName", t)
        except:
            name = t

        candidatos.append({
            "ticker": t, "nombre": name, "score": score, "motivos": motivos,
            "entry": entry, "stop": stop, "tp1": tp1, "tp2": tp2, "rr": rr,
            "rsi": round(d["rsi"], 1), "vol_rel": round(d["vol_rel"], 1)
        })

    candidatos.sort(key=lambda x: x["score"], reverse=True)
    return candidatos[:n]


def send_signals(message, region="EU"):
    bot.send_chat_action(message.chat.id, "typing")
    stocks = EU_STOCKS if region == "EU" else US_STOCKS
    titulo = "SEÑALES EUROPA" if region == "EU" else "SEÑALES EEUU"

    try:
        signals = get_top_signals(stocks, n=4)
        if not signals:
            bot.send_message(message.chat.id, f"No hay señales claras en {region} ahora mismo.", reply_markup=get_main_keyboard())
            return

        lines = [f"**{titulo}** — {datetime.now().strftime('%d/%m %H:%M')}\n"]
        for i, s in enumerate(signals, 1):
            lines.append(
                f"**{i}. {s['nombre']} ({s['ticker']})** — Score {s['score']}\n"
                f"Entrada: `{s['entry']}`\n"
                f"TP1: `{s['tp1']}` | TP2: `{s['tp2']}`\n"
                f"Stop: `{s['stop']}` | R/R: {s['rr']}x\n"
                f"RSI: {s['rsi']} | Vol: {s['vol_rel']}x\n"
                f"_{' · '.join(s['motivos'][:2])}_\n"
            )
        bot.send_message(message.chat.id, "\n".join(lines), reply_markup=get_main_keyboard())
    except Exception as e:
        bot.reply_to(message, f"⚠️ Error generando señales: {e}")


# ===================== GAUGE =====================
def create_gauge_image(score, title, label, components):
    fig = plt.figure(figsize=(10, 9.0), facecolor="#0b0f14")
    ax1 = fig.add_axes([0.08, 0.45, 0.84, 0.50])
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

    angle = math.radians(180 - (score / 100) * 180)
    ax1.plot([0, 0.95 * math.cos(angle)], [0, 0.95 * math.sin(angle)],
             color="white", linewidth=5.5, solid_capstyle="round", zorder=10)
    ax1.add_patch(Circle((0, 0), 0.12, facecolor="white", zorder=11))
    ax1.add_patch(Circle((0, 0), 0.06, facecolor="#0b0f14", zorder=12))

    ax1.text(0, 1.30, title, ha="center", fontsize=13, color="#e6edf3", fontweight="bold")
    ax1.text(0, -0.28, f"{score}/100", ha="center", fontsize=34, color="white", fontweight="bold")
    ax1.text(0, -0.48, label, ha="center", fontsize=12, color="#8b949e")
    ax1.text(-1.25, -0.12, "CARO", ha="center", fontsize=11, color="#ff453a", fontweight="bold")
    ax1.text(1.25, -0.12, "BARATO", ha="center", fontsize=11, color="#30d158", fontweight="bold")

    ax2 = fig.add_axes([0.07, 0.03, 0.86, 0.39])
    ax2.set_facecolor("#0b0f14")
    ax2.set_xlim(0, 10)
    ax2.set_ylim(-0.3, len(components) + 0.5)
    ax2.axis("off")
    ax2.text(5, len(components) + 0.2, "COMPONENTES", ha="center", fontsize=10, color="#8b949e", fontweight="bold")

    for i, (name, value, sc) in enumerate(reversed(components)):
        y = i
        color = score_to_color(sc)
        ax2.add_patch(Rectangle((2.9, y-0.2), 5.4, 0.4, facecolor="#21262d"))
        ax2.add_patch(Rectangle((2.9, y-0.2), (sc/10)*5.4, 0.4, facecolor=color, alpha=0.9))
        ax2.text(0.1, y, name, ha="left", va="center", fontsize=9, color="#e6edf3")
        ax2.text(2.75, y, value, ha="right", va="center", fontsize=8, color="#8b949e")
        ax2.text(8.5, y, f"{sc}/10", ha="left", va="center", fontsize=9, color=color, fontweight="bold")

    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=150, facecolor="#0b0f14", bbox_inches="tight", pad_inches=0.15)
    plt.close(fig)
    buf.seek(0)
    return buf


# ===================== HANDLERS =====================
def get_main_keyboard():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("💎 Índice Valor", callback_data="valor_help"),
        InlineKeyboardButton("₿ BTC", callback_data="btc"),
        InlineKeyboardButton("🇪🇺 Señales EU", callback_data="senales_eu"),
        InlineKeyboardButton("🇺🇸 Señales US", callback_data="senales_us"),
        InlineKeyboardButton("📰 Noticias", callback_data="noticias"),
        InlineKeyboardButton("📊 Menú", callback_data="menu"),
    )
    return kb


@bot.message_handler(commands=["start", "menu"])
def send_welcome(message):
    if not is_authorized(message.from_user.id): return
    text = (
        "🤖 **Bot Financiero**\n\n"
        "• `/valor TICKER` → Medidor de valor (acciones + criptos)\n"
        "• `/senales_eu` → Señales Europa\n"
        "• `/senales_us` → Señales EEUU\n"
        "• `/btc` → Resumen Bitcoin\n"
        "• `/noticias` → Noticias\n\n"
        "_Ejemplos: /valor PEPE  /valor HBAR  /valor TSLA_"
    )
    bot.reply_to(message, text, reply_markup=get_main_keyboard())


@bot.callback_query_handler(func=lambda c: True)
def callbacks(call):
    if not is_authorized(call.from_user.id): return
    bot.answer_callback_query(call.id)
    if call.data == "valor_help":
        bot.send_message(call.message.chat.id, "💎 Ejemplos:\n`/valor BTC`\n`/valor PEPE`\n`/valor HBAR`\n`/valor TSLA`")
    elif call.data == "btc":
        send_valor(call.message, "BTC")
    elif call.data == "senales_eu":
        send_signals(call.message, "EU")
    elif call.data == "senales_us":
        send_signals(call.message, "US")
    elif call.data == "noticias":
        send_noticias(call.message)
    elif call.data == "menu":
        send_welcome(call.message)


@bot.message_handler(commands=["valor", "value"])
def cmd_valor(message):
    if not is_authorized(message.from_user.id): return
    args = message.text.split()
    if len(args) < 2:
        bot.reply_to(message, "⚠️ Usa: `/valor PEPE` o `/valor TSLA`")
        return
    send_valor(message, args[1])


def send_valor(message, raw):
    bot.send_chat_action(message.chat.id, "upload_photo")
    try:
        data = calculate_value_index(raw)
        p = data["price"]
        price_str = f"{p:.6f}" if p < 0.1 else f"{p:.4f}" if p < 1 else f"{p:.2f}" if p < 1000 else f"{p:,.0f}"
        title = f"{data['name']} — {price_str} ({data['d1']:+.2f}%)"
        img = create_gauge_image(data["score"], title, data["label"], data["components"])

        lines = [
            f"**{data['name']} ({data['ticker']}) — {price_str} ({data['d1']:+.2f}%)**",
            f"**{data['score']}/100 — {data['label']}**\n",
            f"**Soportes:** {' | '.join(map(str, data['supports']))}",
            f"**Resistencias:** {' | '.join(map(str, data['resistances']))}\n",
            f"**Qué esperar:**\n{data['outlook']}"
        ]
        if data["news"]:
            lines.append("\n**Noticias:**")
            for n in data["news"][:2]:
                lines.append(f"• {n}")

        bot.send_photo(message.chat.id, img, caption="\n".join(lines), reply_markup=get_main_keyboard())
    except Exception as e:
        bot.reply_to(message, f"⚠️ No pude obtener datos de **{raw.upper()}**.\nPrueba otro símbolo o espera unos minutos.")


@bot.message_handler(commands=["senales_eu", "señales_eu"])
def cmd_eu(message):
    if not is_authorized(message.from_user.id): return
    send_signals(message, "EU")


@bot.message_handler(commands=["senales_us", "señales_us"])
def cmd_us(message):
    if not is_authorized(message.from_user.id): return
    send_signals(message, "US")


@bot.message_handler(commands=["btc"])
def cmd_btc(message):
    if not is_authorized(message.from_user.id): return
    send_valor(message, "BTC")


@bot.message_handler(commands=["noticias"])
def send_noticias(message):
    if not is_authorized(message.from_user.id): return
    bot.send_chat_action(message.chat.id, "typing")
    headlines = []
    for url in ["https://www.expansion.com/rss/mercados.xml", "https://cincodias.elpais.com/rss/cincodias/portada.xml"]:
        try:
            feed = feedparser.parse(url)
            for e in feed.entries[:3]:
                if e.title not in headlines:
                    headlines.append(f"• {e.title}")
        except: pass
    bot.send_message(message.chat.id, "📰 **NOTICIAS**\n\n" + "\n".join(headlines[:6] or ["Sin noticias"]), reply_markup=get_main_keyboard())


@bot.message_handler(func=lambda m: True)
def fallback(message):
    if not is_authorized(message.from_user.id): return
    if message.text and not message.text.startswith("/"):
        bot.reply_to(message, "Usa `/valor TICKER`, `/senales_eu` o `/senales_us`", reply_markup=get_main_keyboard())


if __name__ == "__main__":
    print("🤖 Bot con Señales + Multi-crypto iniciado...")
    bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=30)