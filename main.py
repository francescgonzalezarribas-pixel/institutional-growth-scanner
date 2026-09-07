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

def is_authorized(uid):
    return ALLOWED_USER_ID == 0 or uid == ALLOWED_USER_ID

# ===================== TICKERS =====================
CRYPTO_MAP = {
    "BTC":"BTC-USD","ETH":"ETH-USD","SOL":"SOL-USD","BNB":"BNB-USD","XRP":"XRP-USD",
    "ADA":"ADA-USD","DOGE":"DOGE-USD","DOT":"DOT-USD","HBAR":"HBAR-USD","AVAX":"AVAX-USD",
    "LINK":"LINK-USD","PEPE":"PEPE-USD","SUI":"SUI-USD","NEAR":"NEAR-USD","ATOM":"ATOM-USD",
    "LTC":"LTC-USD","UNI":"UNI-USD","AAVE":"AAVE-USD","ARB":"ARB-USD","OP":"OP-USD",
    "WIF":"WIF-USD","BONK":"BONK-USD","FET":"FET-USD","RENDER":"RENDER-USD","TON":"TON-USD",
    "SHIB":"SHIB-USD","TRX":"TRX-USD","XLM":"XLM-USD","APT":"APT-USD","INJ":"INJ-USD"
}

COMMODITIES = {
    "ORO":"GC=F", "GOLD":"GC=F", "XAU":"GC=F",
    "PLATA":"SI=F", "SILVER":"SI=F", "XAG":"SI=F",
    "PETROLEO":"CL=F", "OIL":"CL=F", "WTI":"CL=F",
    "COBRE":"HG=F", "COPPER":"HG=F"
}

EU_STOCKS = ["SAN.MC","BBVA.MC","ITX.MC","REP.MC","TEF.MC","IBE.MC","AMS.MC","OR.PA","BNP.PA","AIR.PA","MC.PA","TTE.PA","BMW.DE","SIE.DE","SAP","ASML","NESN.SW","NOVO-B.CO","HSBA.L","BP.L","SHEL.L","ALV.DE","DTE.DE"]
US_STOCKS = ["AAPL","MSFT","NVDA","AMZN","GOOGL","META","TSLA","AMD","NFLX","JPM","V","MA","JNJ","UNH","XOM","CVX","WMT","HD","KO","BA","CAT","DIS","CRM","PLTR","COIN","HOOD","SMCI","ARM","AVGO","QCOM","MU","SHOP","SNOW","CRWD","UBER","ABNB","INTC","ORCL","NOW","PANW"]

def resolve_ticker(raw):
    t = raw.upper().strip().replace("USDT","").replace("-USD","")
    if t in CRYPTO_MAP: return CRYPTO_MAP[t]
    if t in COMMODITIES: return COMMODITIES[t]
    if len(t) <= 5 and t.isalpha(): return f"{t}-USD"
    return t

# ===================== INDICADORES =====================
def compute_rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_g = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    avg_l = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    rs = avg_g / avg_l.replace(0, np.nan)
    rsi = 100 - (100/(1+rs))
    return float(rsi.iloc[-1]) if not pd.isna(rsi.iloc[-1]) else 50.0

def compute_macd(series):
    ema12 = series.ewm(span=12, adjust=False).mean()
    ema26 = series.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    hist = macd - signal
    cross_up = len(macd)>=2 and macd.iloc[-1]>signal.iloc[-1] and macd.iloc[-2]<=signal.iloc[-2]
    return {"hist": float(hist.iloc[-1]), "cross_up": cross_up}

def get_fear_greed():
    try:
        return float(requests.get("https://api.alternative.me/fng/?limit=1", timeout=6).json()["data"][0]["value"])
    except: return 50.0

def get_vix():
    try: return float(yf.Ticker("^VIX").history(period="5d")["Close"].iloc[-1])
    except: return 18.0

# ===================== VALOR (simplificado) =====================
def calculate_value_index(raw):
    ticker = resolve_ticker(raw)
    is_crypto = "-USD" in ticker
    stock = yf.Ticker(ticker)
    hist = stock.history(period="1y")
    if (hist.empty or len(hist)<20) and not ticker.endswith("-USD"):
        stock = yf.Ticker(ticker+"-USD")
        hist = stock.history(period="1y")
        ticker = ticker+"-USD"
        is_crypto = True
    if hist.empty or len(hist)<20:
        raise ValueError("Sin datos")

    close, high, low, volume = hist["Close"], hist["High"], hist["Low"], hist["Volume"]
    price = float(close.iloc[-1])
    d1 = (price/float(close.iloc[-2])-1)*100 if len(close)>1 else 0
    rsi = compute_rsi(close)
    ema200 = close.ewm(span=min(200,len(close)-1), adjust=False).mean().iloc[-1]
    ema_pct = (price/ema200-1)*100
    avg_vol = volume.iloc[-15:-1].mean() if len(volume)>15 else volume.mean()
    rel_vol = float(volume.iloc[-1]/avg_vol) if avg_vol>0 else 1
    dist_52 = (price/float(close.max())-1)*100
    macd = compute_macd(close)
    weekly = close.resample("W").last().dropna()
    rsi_w = compute_rsi(weekly) if len(weekly)>14 else 50

    def s_rsi(r):
        if r<=28: return 10
        if r<=35: return 8.5
        if r<=42: return 7
        if r<=50: return 5
        if r<=58: return 3.5
        if r<=65: return 2
        return 1
    def s_ema(p):
        if p<=-25: return 10
        if p<=-12: return 8
        if p<=-4: return 6
        if p<=5: return 4.5
        if p<=15: return 2.5
        return 1
    def s_dist(p):
        if p<=-40: return 10
        if p<=-25: return 8
        if p<=-12: return 6
        if p<=0: return 4
        return 2

    components = [
        ("RSI 14d", f"{rsi:.1f}", s_rsi(rsi)),
        ("Dist. Max 52s", f"{dist_52:.1f}%", s_dist(dist_52)),
        ("EMA200", f"{ema_pct:+.1f}%", s_ema(ema_pct)),
        ("MACD", "Cruce ↑" if macd["cross_up"] else f"{macd['hist']:+.1f}", 8 if macd["cross_up"] else (6 if macd["hist"]>0 else 2.5)),
        ("Volumen", f"{rel_vol:.2f}x", 7 if rel_vol>1.8 else (5 if rel_vol>1.1 else 3)),
        ("RSI Semanal", f"{rsi_w:.1f}", s_rsi(rsi_w)),
    ]
    if is_crypto:
        fg = get_fear_greed()
        components.append(("Fear&Greed", f"{fg:.0f}", 9 if fg<30 else (5 if fg<55 else 1.5)))

    score = int(round(sum(c[2] for c in components)/len(components)*10))
    score = max(0, min(100, score))

    label = "BARATO — OPORTUNIDAD" if score>=72 else "NEUTRAL — ACUMULACIÓN" if score>=58 else "NEUTRAL" if score>=42 else "CARO — PRECAUCIÓN" if score>=28 else "MUY CARO"
    try: name = stock.info.get("shortName") or stock.info.get("longName") or ticker
    except: name = ticker

    # S/R simples
    pivot = (high.iloc[-1]+low.iloc[-1]+close.iloc[-1])/3
    s1 = 2*pivot - high.iloc[-1]
    r1 = 2*pivot - low.iloc[-1]
    supports = [round(s1,4) if price<10 else (round(s1,2) if price<1000 else int(s1))]
    resistances = [round(r1,4) if price<10 else (round(r1,2) if price<1000 else int(r1))]

    return {
        "ticker":ticker, "name":name, "price":price, "d1":d1, "score":score, "label":label,
        "components":[(n,v,round(s,1)) for n,v,s in components],
        "supports":supports, "resistances":resistances,
        "outlook": "Zona interesante" if score>=60 else "Mejor esperar confirmación" if score>=40 else "Precio extendido, precaución"
    }

def create_gauge(score, title, label, components):
    fig = plt.figure(figsize=(9.5, 8.5), facecolor="#0b0f14")
    ax1 = fig.add_axes([0.08,0.42,0.84,0.52])
    ax1.set_facecolor("#0b0f14"); ax1.set_xlim(-1.35,1.35); ax1.set_ylim(-0.5,1.35); ax1.set_aspect("equal"); ax1.axis("off")
    colors = ["#ff453a","#ff9f0a","#ffd60a","#30d158","#00c7be"]
    for i,a in enumerate([180,144,108,72,36]):
        ax1.add_patch(Wedge((0,0),1.12, a-36, a, width=0.32, facecolor=colors[i], edgecolor="#0b0f14", lw=2.5))
    ang = math.radians(180 - score/100*180)
    ax1.plot([0,0.92*math.cos(ang)],[0,0.92*math.sin(ang)], color="white", lw=5, solid_capstyle="round", zorder=10)
    ax1.add_patch(Circle((0,0),0.1, facecolor="white", zorder=11))
    ax1.add_patch(Circle((0,0),0.05, facecolor="#0b0f14", zorder=12))
    ax1.text(0,1.25, title, ha="center", fontsize=13, color="white", fontweight="bold")
    ax1.text(0,-0.25, f"{score}/100", ha="center", fontsize=32, color="white", fontweight="bold")
    ax1.text(0,-0.45, label, ha="center", fontsize=12, color="#8b949e")
    ax1.text(-1.2,-0.1, "CARO", ha="center", color="#ff453a", fontweight="bold")
    ax1.text(1.2,-0.1, "BARATO", ha="center", color="#30d158", fontweight="bold")

    ax2 = fig.add_axes([0.08,0.04,0.84,0.35])
    ax2.set_facecolor("#0b0f14"); ax2.set_xlim(0,10); ax2.set_ylim(-0.3,len(components)+0.4); ax2.axis("off")
    for i,(n,v,s) in enumerate(reversed(components)):
        color = "#30d158" if s>=7 else "#ffd60a" if s>=5 else "#ff9f0a" if s>=3 else "#ff453a"
        ax2.add_patch(Rectangle((3,i-0.2),5.2,0.4, facecolor="#21262d"))
        ax2.add_patch(Rectangle((3,i-0.2),(s/10)*5.2,0.4, facecolor=color, alpha=0.9))
        ax2.text(0.1,i, n, va="center", fontsize=9, color="#e6edf3")
        ax2.text(2.9,i, v, ha="right", va="center", fontsize=8, color="#8b949e")
        ax2.text(8.4,i, f"{s}/10", va="center", fontsize=9, color=color, fontweight="bold")
    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=140, facecolor="#0b0f14", bbox_inches="tight", pad_inches=0.12)
    plt.close(); buf.seek(0)
    return buf

# ===================== SEÑALES + GRÁFICO =====================
def fetch_signal_data(ticker):
    try:
        hist = yf.Ticker(ticker).history(period="6mo")
        if hist.empty or len(hist)<25: return None
        close, high, low, vol = hist["Close"], hist["High"], hist["Low"], hist["Volume"]
        price = float(close.iloc[-1])
        rsi = compute_rsi(close)
        macd = compute_macd(close)
        ema20 = close.ewm(span=20, adjust=False).mean().iloc[-1]
        ema50 = close.ewm(span=50, adjust=False).mean().iloc[-1]
        avg_vol = vol.tail(20).mean()
        vol_rel = float(vol.iloc[-1]/avg_vol) if avg_vol>0 else 1
        tr = pd.concat([high-low, (high-close.shift()).abs(), (low-close.shift()).abs()], axis=1).max(axis=1)
        atr = float(tr.ewm(span=14, adjust=False).mean().iloc[-1])
        return {"ticker":ticker, "price":price, "rsi":rsi, "macd":macd, "ema20":ema20, "ema50":ema50,
                "vol_rel":vol_rel, "atr":atr, "hist":hist, "sobre_ema20":price>ema20, "tendencia":ema20>ema50}
    except: return None

def get_top_signals(stocks, n=5, min_score=6):
    cands = []
    for t in stocks:
        d = fetch_signal_data(t)
        if not d or d["rsi"]>70 or d["vol_rel"]<0.65: continue
        score, motivos = 0, []
        if d["rsi"]<32: score+=5; motivos.append(f"RSI {d['rsi']:.0f} sobreventa")
        elif d["rsi"]<42: score+=4; motivos.append(f"RSI {d['rsi']:.0f} zona entrada")
        elif d["rsi"]<52: score+=2; motivos.append(f"RSI {d['rsi']:.0f}")
        if d["macd"]["cross_up"]: score+=4; motivos.append("MACD cruce alcista")
        elif d["macd"]["hist"]>0: score+=2
        if d["vol_rel"]>=1.7: score+=3; motivos.append(f"Vol {d['vol_rel']:.1f}x")
        elif d["vol_rel"]>=1.15: score+=1
        if d["tendencia"] and d["sobre_ema20"]: score+=3; motivos.append("Tendencia alcista")
        elif d["sobre_ema20"]: score+=1
        if score < min_score: continue
        entry = d["price"]
        stop = round(entry - d["atr"]*1.4, 2 if entry>10 else 4)
        if stop<=0: stop = round(entry*0.97, 2)
        risk = entry - stop
        tp1 = round(entry + risk*1.6, 2 if entry>10 else 4)
        tp2 = round(entry + risk*2.8, 2 if entry>10 else 4)
        try: name = yf.Ticker(t).info.get("shortName", t)
        except: name = t
        cands.append({**d, "nombre":name, "score":score, "motivos":motivos, "entry":round(entry,2 if entry>10 else 4),
                      "stop":stop, "tp1":tp1, "tp2":tp2, "rr":round((tp1-entry)/risk,1) if risk>0 else 0})
    cands.sort(key=lambda x: x["score"], reverse=True)
    return cands[:n]

def create_signal_chart(d):
    """Gráfico simple con entrada, stop y TPs"""
    hist = d["hist"].tail(60)
    fig, ax = plt.subplots(figsize=(9, 4.5), facecolor="#0b0f14")
    ax.set_facecolor("#0b0f14")
    ax.plot(hist.index, hist["Close"], color="#58a6ff", lw=1.6)
    ax.axhline(d["entry"], color="#3fb950", ls="--", lw=1.2, label=f"Entrada {d['entry']}")
    ax.axhline(d["tp1"], color="#2ea043", ls=":", lw=1.1, label=f"TP1 {d['tp1']}")
    ax.axhline(d["tp2"], color="#238636", ls=":", lw=1.1, label=f"TP2 {d['tp2']}")
    ax.axhline(d["stop"], color="#f85149", ls="--", lw=1.2, label=f"Stop {d['stop']}")
    ax.fill_between(hist.index, d["stop"], d["entry"], color="#f85149", alpha=0.08)
    ax.fill_between(hist.index, d["entry"], d["tp2"], color="#3fb950", alpha=0.07)
    ax.set_title(f"{d['nombre']} ({d['ticker']})  |  Score {d['score']}", color="white", fontsize=12, pad=8)
    ax.tick_params(colors="#8b949e")
    ax.legend(facecolor="#161b22", edgecolor="#30363d", labelcolor="white", fontsize=8, loc="upper left")
    for spine in ax.spines.values(): spine.set_color("#30363d")
    ax.grid(True, alpha=0.15, color="#8b949e")
    fig.autofmt_xdate()
    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=130, facecolor="#0b0f14", bbox_inches="tight")
    plt.close(); buf.seek(0)
    return buf

def send_signals(message, region="US"):
    bot.send_chat_action(message.chat.id, "typing")
    stocks = US_STOCKS if region=="US" else EU_STOCKS
    titulo = "🇺🇸 SEÑALES EEUU" if region=="US" else "🇪🇺 SEÑALES EUROPA"
    try:
        signals = get_top_signals(stocks, n=4, min_score=6 if region=="US" else 5)
        if not signals:
            bot.send_message(message.chat.id, f"No hay señales claras en {region} ahora mismo.\nPrueba más tarde o usa `/valor TICKER`.", reply_markup=get_kb())
            return
        bot.send_message(message.chat.id, f"**{titulo}** — {datetime.now().strftime('%d/%m %H:%M')}")
        for s in signals:
            caption = (
                f"**{s['nombre']} ({s['ticker']})** — Score **{s['score']}**\n\n"
                f"🟢 Entrada: `{s['entry']}`\n"
                f"🎯 TP1: `{s['tp1']}`  |  TP2: `{s['tp2']}`\n"
                f"🔴 Stop: `{s['stop']}`  |  R/R: **{s['rr']}x**\n"
                f"RSI: {s['rsi']:.1f}  |  Vol: {s['vol_rel']:.1f}x\n\n"
                f"_{' · '.join(s['motivos'])}_"
            )
            try:
                chart = create_signal_chart(s)
                bot.send_photo(message.chat.id, chart, caption=caption)
            except:
                bot.send_message(message.chat.id, caption)
            time.sleep(0.4)
        bot.send_message(message.chat.id, "Usa los botones para más opciones:", reply_markup=get_kb())
    except Exception as e:
        bot.reply_to(message, f"⚠️ Error: {e}")

# ===================== HANDLERS =====================
def get_kb():
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

@bot.message_handler(commands=["start","menu"])
def start(m):
    if not is_authorized(m.from_user.id): return
    bot.reply_to(m,
        "🤖 **Bot Financiero**\n\n"
        "• `/valor TICKER` → Medidor (acciones, criptos, oro, plata...)\n"
        "• `/senales_eu` → Señales Europa + gráfico\n"
        "• `/senales_us` → Señales EEUU + gráfico\n"
        "• `/btc` → Bitcoin\n\n"
        "_Ejemplos: /valor PEPE  /valor ORO  /valor TSLA  /valor GC=F_",
        reply_markup=get_kb())

@bot.callback_query_handler(func=lambda c: True)
def cb(call):
    if not is_authorized(call.from_user.id): return
    bot.answer_callback_query(call.id)
    if call.data=="valor_help":
        bot.send_message(call.message.chat.id, "💎 Ejemplos:\n`/valor BTC`\n`/valor PEPE`\n`/valor ORO`\n`/valor TSLA`\n`/valor SAN.MC`")
    elif call.data=="btc": send_valor(call.message, "BTC")
    elif call.data=="senales_eu": send_signals(call.message, "EU")
    elif call.data=="senales_us": send_signals(call.message, "US")
    elif call.data=="noticias": noticias(call.message)
    elif call.data=="menu": start(call.message)

@bot.message_handler(commands=["valor","value"])
def cmd_valor(m):
    if not is_authorized(m.from_user.id): return
    args = m.text.split()
    if len(args)<2:
        bot.reply_to(m, "⚠️ Usa: `/valor TSLA` o `/valor ORO` o `/valor PEPE`")
        return
    send_valor(m, args[1])

def send_valor(m, raw):
    bot.send_chat_action(m.chat.id, "upload_photo")
    try:
        data = calculate_value_index(raw)
        p = data["price"]
        ps = f"{p:.6f}" if p<0.01 else f"{p:.4f}" if p<1 else f"{p:.2f}" if p<1000 else f"{p:,.0f}"
        title = f"{data['name']} — {ps} ({data['d1']:+.2f}%)"
        img = create_gauge(data["score"], title, data["label"], data["components"])
        cap = (f"**{data['name']} ({data['ticker']}) — {ps} ({data['d1']:+.2f}%)**\n"
               f"**{data['score']}/100 — {data['label']}**\n\n"
               f"**Soportes:** {' | '.join(map(str,data['supports']))}\n"
               f"**Resistencias:** {' | '.join(map(str,data['resistances']))}\n\n"
               f"**Qué esperar:** {data['outlook']}")
        bot.send_photo(m.chat.id, img, caption=cap, reply_markup=get_kb())
    except Exception as e:
        bot.reply_to(m, f"⚠️ No pude obtener datos de **{raw.upper()}**.\nPrueba otro símbolo (ej: BTC, ORO, TSLA, SAN.MC)")

@bot.message_handler(commands=["senales_eu","señales_eu"])
def eu(m):
    if is_authorized(m.from_user.id): send_signals(m, "EU")

@bot.message_handler(commands=["senales_us","señales_us"])
def us(m):
    if is_authorized(m.from_user.id): send_signals(m, "US")

@bot.message_handler(commands=["btc"])
def btc(m):
    if is_authorized(m.from_user.id): send_valor(m, "BTC")

@bot.message_handler(commands=["noticias"])
def noticias(m):
    if not is_authorized(m.from_user.id): return
    bot.send_chat_action(m.chat.id, "typing")
    heads = []
    for url in ["https://www.expansion.com/rss/mercados.xml","https://cincodias.elpais.com/rss/cincodias/portada.xml"]:
        try:
            for e in feedparser.parse(url).entries[:3]:
                if e.title not in heads: heads.append(f"• {e.title}")
        except: pass
    bot.send_message(m.chat.id, "📰 **NOTICIAS**\n\n"+"\n".join(heads[:6] or ["Sin noticias"]), reply_markup=get_kb())

@bot.message_handler(func=lambda m: True)
def fb(m):
    if is_authorized(m.from_user.id) and m.text and not m.text.startswith("/"):
        bot.reply_to(m, "Usa `/valor TICKER`, `/senales_eu` o `/senales_us`", reply_markup=get_kb())

if __name__ == "__main__":
    print("Bot señales + gráficos iniciado")
    bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=30)