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
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()

bot = telebot.TeleBot(TELEGRAM_TOKEN, parse_mode="Markdown")

def is_authorized(uid):
    return ALLOWED_USER_ID == 0 or uid == ALLOWED_USER_ID

# ===================== MAPAS Y RESOLUCIÓN =====================
CRYPTO_MAP = {
    "BTC":"BTC-USD","ETH":"ETH-USD","SOL":"SOL-USD","BNB":"BNB-USD","XRP":"XRP-USD",
    "ADA":"ADA-USD","DOGE":"DOGE-USD","DOT":"DOT-USD","HBAR":"HBAR-USD","AVAX":"AVAX-USD",
    "LINK":"LINK-USD","PEPE":"PEPE-USD","SUI":"SUI-USD","NEAR":"NEAR-USD","ATOM":"ATOM-USD",
    "LTC":"LTC-USD","UNI":"UNI-USD","AAVE":"AAVE-USD","ARB":"ARB-USD","OP":"OP-USD",
    "WIF":"WIF-USD","BONK":"BONK-USD","FET":"FET-USD","RENDER":"RENDER-USD","TON":"TON-USD",
    "SHIB":"SHIB-USD","TRX":"TRX-USD","XLM":"XLM-USD","APT":"APT-USD","INJ":"INJ-USD",
    "SEI":"SEI-USD","TIA":"TIA-USD"
}

COINGECKO_IDS = {
    "BTC":"bitcoin","ETH":"ethereum","SOL":"solana","BNB":"binancecoin","XRP":"ripple",
    "ADA":"cardano","DOGE":"dogecoin","DOT":"polkadot","HBAR":"hedera-hashgraph",
    "AVAX":"avalanche-2","LINK":"chainlink","PEPE":"pepe","SUI":"sui","NEAR":"near",
    "ATOM":"cosmos","LTC":"litecoin","UNI":"uniswap","AAVE":"aave","ARB":"arbitrum",
    "OP":"optimism","WIF":"dogwifcoin","BONK":"bonk","FET":"fetch-ai","RENDER":"render-token",
    "TON":"the-open-network","SHIB":"shiba-inu","TRX":"tron","XLM":"stellar",
    "APT":"aptos","INJ":"injective-protocol","SEI":"sei-network","TIA":"celestia"
}

COMMODITIES = {"ORO":"GC=F","GOLD":"GC=F","XAU":"GC=F","PLATA":"SI=F","SILVER":"SI=F","PETROLEO":"CL=F","OIL":"CL=F"}

EU_STOCKS = ["SAN.MC","BBVA.MC","ITX.MC","REP.MC","TEF.MC","IBE.MC","AMS.MC","OR.PA","BNP.PA","AIR.PA","MC.PA","TTE.PA","BMW.DE","SIE.DE","SAP","ASML","NESN.SW","HSBA.L","BP.L","SHEL.L"]
US_STOCKS = ["AAPL","MSFT","NVDA","AMZN","GOOGL","META","TSLA","AMD","NFLX","JPM","V","MA","JNJ","UNH","XOM","WMT","HD","BA","CAT","DIS","CRM","PLTR","COIN","SMCI","ARM","AVGO","QCOM","SHOP","SNOW","CRWD","UBER","INTC","ORCL"]

def resolve_ticker(raw):
    t = raw.upper().strip().replace("USDT","").replace("-USD","")
    if t in CRYPTO_MAP: return CRYPTO_MAP[t]
    if t in COMMODITIES: return COMMODITIES[t]
    if len(t) <= 5 and t.isalpha(): return f"{t}-USD"
    return t

def get_cg_id(raw):
    t = raw.upper().strip().replace("USDT","").replace("-USD","")
    return COINGECKO_IDS.get(t)

# ===================== GEMINI 3.6 FLASH (PARSEO SEGURO) =====================
def ask_gemini(prompt, max_tokens=650):
    if not GEMINI_API_KEY:
        return None
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.6-flash:generateContent?key={GEMINI_API_KEY}"
        payload = {
            "contents": [{
                "parts": [{
                    "text": (
                        "Eres un analista financiero sénior experto en bolsas globales, criptomonedas y macroeconomía. "
                        "Responde siempre en español. Proporciona explicaciones analíticas detalladas y fundamentadas en datos numéricos concretos "
                        "(RSI, VIX, USDT.D, soportes/resistencias, volumen). "
                        "Contextualiza los datos técnicos con eventos de mercado, catalizadores sectoriales y el entorno macroeconómico actual. "
                        "Estructura las respuestas con claridad, datos exactos y conclusiones accionables. Evita generalidades vacías.\n\n" + prompt
                    )
                }]
            }],
            "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0.3}
        }
        r = requests.post(url, json=payload, timeout=35)
        if r.status_code == 200:
            data = r.json()
            candidates = data.get("candidates", [])
            if candidates:
                cand = candidates[0]
                parts = cand.get("content", {}).get("parts", [])
                if parts and "text" in parts[0]:
                    return parts[0]["text"].strip()
                
                finish_reason = cand.get("finishReason", "DESCONOCIDO")
                print(f"Gemini no devolvió texto. Razón: {finish_reason}")
        else:
            print(f"Gemini 3.6 error [{r.status_code}]: {r.text[:250]}")
    except Exception as e:
        print(f"Excepción Gemini: {e}")
    return None

# ===================== COINGECKO & MACRO =====================
def get_crypto_data(raw):
    cg_id = get_cg_id(raw)
    if not cg_id:
        return None
    try:
        url = f"https://api.coingecko.com/api/v3/coins/{cg_id}?localization=false&tickers=false&community_data=false&developer_data=false"
        r = requests.get(url, timeout=10)
        if r.status_code != 200:
            return None
        data = r.json()
        market = data.get("market_data", {})
        price = market.get("current_price", {}).get("usd")
        if not price:
            return None
        return {
            "name": data.get("name", raw.upper()),
            "symbol": data.get("symbol", raw).upper(),
            "price": float(price),
            "d1": market.get("price_change_percentage_24h") or 0,
            "d7": market.get("price_change_percentage_7d") or 0,
            "ath_change": market.get("ath_change_percentage", {}).get("usd") or 0,
        }
    except Exception as e:
        print(f"CoinGecko error: {e}")
        return None

def get_vix():
    try:
        data = yf.Ticker("^VIX").history(period="5d")
        if not data.empty:
            return float(data["Close"].iloc[-1])
    except:
        pass
    return 18.0

def score_vix(vix):
    if vix >= 32: return 10.0
    if vix >= 26: return 8.0
    if vix >= 20: return 6.0
    if vix >= 15: return 4.0
    return 2.0

def get_usdt_dominance():
    try:
        r = requests.get("https://api.coingecko.com/api/v3/global", timeout=6)
        if r.status_code == 200:
            data = r.json()["data"]
            return float(data["market_cap_percentage"].get("usdt", 0))
    except:
        pass
    return None

def score_usdt_dominance(usdt_dom):
    if usdt_dom >= 8.5: return 10.0
    if usdt_dom >= 7.5: return 8.5
    if usdt_dom >= 6.0: return 6.0
    if usdt_dom >= 4.8: return 4.0
    if usdt_dom >= 4.0: return 2.0
    return 0.5

# ===================== INDICADORES TÉCNICOS =====================
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
    except:
        return 50.0

# ===================== CÁLCULO DE VALOR =====================
def calculate_value_index(raw):
    ticker = resolve_ticker(raw)
    is_crypto = get_cg_id(raw) is not None or "-USD" in ticker

    stock = yf.Ticker(ticker)
    hist = stock.history(period="1y")

    if (hist.empty or len(hist) < 20) and is_crypto:
        cg = get_crypto_data(raw)
        if not cg:
            raise ValueError("Sin datos disponibles")
        fg = get_fear_greed()
        vix = get_vix()
        usdt_dom = get_usdt_dominance()

        score = 45
        if cg["d1"] < -5: score += 10
        if cg["d7"] < -12: score += 10
        if cg["ath_change"] < -70: score += 10
        if fg < 30: score += 10
        if fg > 75: score -= 15
        if vix >= 26: score += 10
        if usdt_dom and usdt_dom >= 7.5: score += 10
        score = max(12, min(88, score))

        label = ("BARATO — OPORTUNIDAD" if score >= 72 else
                 "NEUTRAL — ACUMULACIÓN" if score >= 58 else
                 "NEUTRAL" if score >= 42 else
                 "CARO — PRECAUCIÓN" if score >= 28 else "MUY CARO")

        components = [
            ("Cambio 24h", f"{cg['d1']:+.1f}%", 8 if cg["d1"] < -4 else 4),
            ("Cambio 7d", f"{cg['d7']:+.1f}%", 8 if cg["d7"] < -10 else 4),
            ("Desde ATH", f"{cg['ath_change']:+.1f}%", 9 if cg["ath_change"] < -60 else 3),
            ("Fear&Greed", f"{fg:.0f}", 9 if fg < 30 else 3),
            ("VIX", f"{vix:.1f}", score_vix(vix)),
        ]
        if usdt_dom:
            components.append(("USDT.D", f"{usdt_dom:.2f}%", score_usdt_dominance(usdt_dom)))

        outlook = ask_gemini(
            f"Activo cripto: {cg['name']} ({cg['symbol']}). Precio: ${cg['price']}. "
            f"Datos: Variación 24h: {cg['d1']:.1f}%, Variación 7d: {cg['d7']:.1f}%, Caída desde Máximos (ATH): {cg['ath_change']:.1f}%, Fear & Greed: {fg:.0f}/100, VIX: {vix:.1f}, USDT.D: {usdt_dom if usdt_dom else 'N/A'}%. "
            f"Score de Valor: {score}/100 ({label}). "
            f"Explica con datos numéricos el nivel de liquidez, el riesgo macro actual y la estrategia recomendada (acumular, esperar o reducir exposición)."
        ) or "Revisa el sentimiento del mercado y el volumen antes de entrar."

        return {
            "ticker": cg["symbol"] + "-USD", "name": cg["name"], "price": cg["price"], "d1": cg["d1"],
            "score": score, "label": label, "components": components,
            "supports": [], "resistances": [], "outlook": outlook
        }

    if hist.empty or len(hist) < 20:
        raise ValueError("Sin datos")

    close, high, low, volume = hist["Close"], hist["High"], hist["Low"], hist["Volume"]
    price = float(close.iloc[-1])
    d1 = (price / float(close.iloc[-2]) - 1) * 100 if len(close) > 1 else 0
    rsi = compute_rsi(close)
    ema200 = close.ewm(span=min(200, len(close)-1), adjust=False).mean().iloc[-1]
    ema_pct = (price / ema200 - 1) * 100
    avg_vol = volume.iloc[-15:-1].mean() if len(volume) > 15 else volume.mean()
    rel_vol = float(volume.iloc[-1] / avg_vol) if avg_vol > 0 else 1
    dist_52 = (price / float(close.max()) - 1) * 100
    macd = compute_macd(close)
    weekly = close.resample("W").last().dropna()
    rsi_w = compute_rsi(weekly) if len(weekly) > 14 else 50

    def s_rsi(r):
        if r <= 28: return 10
        if r <= 35: return 8.5
        if r <= 42: return 7
        if r <= 50: return 5
        if r <= 58: return 3.5
        if r <= 65: return 2
        return 1
    def s_ema(p):
        if p <= -25: return 10
        if p <= -12: return 8
        if p <= -4: return 6
        if p <= 5: return 4.5
        if p <= 15: return 2.5
        return 1
    def s_dist(p):
        if p <= -40: return 10
        if p <= -25: return 8
        if p <= -12: return 6
        if p <= 0: return 4
        return 2

    components = [
        ("RSI 14d", f"{rsi:.1f}", s_rsi(rsi)),
        ("Dist. Max 52s", f"{dist_52:.1f}%", s_dist(dist_52)),
        ("EMA200", f"{ema_pct:+.1f}%", s_ema(ema_pct)),
        ("MACD", "Cruce ↑" if macd["cross_up"] else f"{macd['hist']:+.1f}", 8 if macd["cross_up"] else (6 if macd["hist"]>0 else 2.5)),
        ("Volumen", f"{rel_vol:.2f}x", 7 if rel_vol>1.8 else (5 if rel_vol>1.1 else 3)),
        ("RSI Semanal", f"{rsi_w:.1f}", s_rsi(rsi_w)),
    ]

    vix = get_vix()
    components.append(("VIX", f"{vix:.1f}", score_vix(vix)))

    if is_crypto:
        fg = get_fear_greed()
        components.append(("Fear&Greed", f"{fg:.0f}", 9 if fg<30 else (5 if fg<55 else 1.5)))
        usdt_dom = get_usdt_dominance()
        if usdt_dom:
            components.append(("USDT.D", f"{usdt_dom:.2f}%", score_usdt_dominance(usdt_dom)))

    score = max(0, min(100, int(round(sum(c[2] for c in components)/len(components)*10))))
    label = ("BARATO — OPORTUNIDAD" if score>=72 else "NEUTRAL — ACUMULACIÓN" if score>=58 else
             "NEUTRAL" if score>=42 else "CARO — PRECAUCIÓN" if score>=28 else "MUY CARO")

    try:
        name = stock.info.get("shortName") or stock.info.get("longName") or ticker
    except:
        name = ticker

    pivot = (high.iloc[-1] + low.iloc[-1] + close.iloc[-1]) / 3
    supports = [round(2*pivot - high.iloc[-1], 4 if price<10 else 2)]
    resistances = [round(2*pivot - low.iloc[-1], 4 if price<10 else 2)]

    comp_str = ", ".join([f"{n}: {v}" for n, v, _ in components])
    outlook = ask_gemini(
        f"Activo: {name} ({ticker}). Precio: {price:.4f}. Variación 24h: {d1:+.2f}%. "
        f"Desglose de métricas clave: {comp_str}. Score Global: {score}/100 ({label}). "
        f"Explica con datos exactos la situación técnica actual, qué implica la distancia respecto a la EMA200 y la tendencia del volumen, ofreciendo una conclusión operativa clara."
    ) or ("Zona interesante para acumular" if score>=60 else "Mejor esperar confirmación" if score>=40 else "Precio extendido, precaución")

    return {
        "ticker": ticker, "name": name, "price": price, "d1": d1,
        "score": score, "label": label,
        "components": [(n,v,round(s,1)) for n,v,s in components],
        "supports": supports, "resistances": resistances, "outlook": outlook
    }

def create_gauge(score, title, label, components):
    fig = plt.figure(figsize=(9.5, 8.5), facecolor="#0b0f14")
    ax1 = fig.add_axes([0.08, 0.42, 0.84, 0.52])
    ax1.set_facecolor("#0b0f14")
    ax1.set_xlim(-1.35, 1.35)
    ax1.set_ylim(-0.5, 1.35)
    ax1.set_aspect("equal")
    ax1.axis("off")
    colors = ["#ff453a","#ff9f0a","#ffd60a","#30d158","#00c7be"]
    for i,a in enumerate([180,144,108,72,36]):
        ax1.add_patch(Wedge((0,0),1.12,a-36,a,width=0.32,facecolor=colors[i],edgecolor="#0b0f14",lw=2.5))
    ang = math.radians(180 - score/100*180)
    ax1.plot([0,0.92*math.cos(ang)],[0,0.92*math.sin(ang)],color="white",lw=5,solid_capstyle="round",zorder=10)
    ax1.add_patch(Circle((0,0),0.1,facecolor="white",zorder=11))
    ax1.add_patch(Circle((0,0),0.05,facecolor="#0b0f14",zorder=12))
    ax1.text(0,1.25,title,ha="center",fontsize=13,color="white",fontweight="bold")
    ax1.text(0,-0.25,f"{score}/100",ha="center",fontsize=32,color="white",fontweight="bold")
    ax1.text(0,-0.45,label,ha="center",fontsize=12,color="#8b949e")
    ax1.text(-1.2,-0.1,"CARO",ha="center",color="#ff453a",fontweight="bold")
    ax1.text(1.2,-0.1,"BARATO",ha="center",color="#30d158",fontweight="bold")

    ax2 = fig.add_axes([0.08,0.04,0.84,0.35])
    ax2.set_facecolor("#0b0f14")
    ax2.set_xlim(0,10)
    ax2.set_ylim(-0.3,len(components)+0.4)
    ax2.axis("off")
    for i,(n,v,s) in enumerate(reversed(components)):
        color = "#30d158" if s>=7 else "#ffd60a" if s>=5 else "#ff9f0a" if s>=3 else "#ff453a"
        ax2.add_patch(Rectangle((3,i-0.2),5.2,0.4,facecolor="#21262d"))
        ax2.add_patch(Rectangle((3,i-0.2),(s/10)*5.2,0.4,facecolor=color,alpha=0.9))
        ax2.text(0.1,i,n,va="center",fontsize=9,color="#e6edf3")
        ax2.text(2.9,i,v,ha="right",va="center",fontsize=8,color="#8b949e")
        ax2.text(8.4,i,f"{s}/10",va="center",fontsize=9,color=color,fontweight="bold")
    buf = io.BytesIO()
    plt.savefig(buf,format="png",dpi=140,facecolor="#0b0f14",bbox_inches="tight",pad_inches=0.12)
    plt.close()
    buf.seek(0)
    return buf

# ===================== SEÑALES =====================
def fetch_signal_data(ticker):
    try:
        hist = yf.Ticker(ticker).history(period="6mo")
        if hist.empty or len(hist)<25: return None
        close, high, low, vol = hist["Close"], hist["High"], hist["Low"], hist["Volume"]
        price = float(close.iloc[-1])
        rsi = compute_rsi(close)
        macd = compute_macd(close)
        ema20 = close.ewm(span=20,adjust=False).mean().iloc[-1]
        ema50 = close.ewm(span=50,adjust=False).mean().iloc[-1]
        avg_vol = vol.tail(20).mean()
        vol_rel = float(vol.iloc[-1]/avg_vol) if avg_vol>0 else 1
        tr = pd.concat([high-low,(high-close.shift()).abs(),(low-close.shift()).abs()],axis=1).max(axis=1)
        atr = float(tr.ewm(span=14,adjust=False).mean().iloc[-1])
        return {"ticker":ticker,"price":price,"rsi":rsi,"macd":macd,"ema20":ema20,"ema50":ema50,
                "vol_rel":vol_rel,"atr":atr,"hist":hist,"sobre_ema20":price>ema20,"tendencia":ema20>ema50}
    except: return None

def get_top_signals(stocks, n=3, min_score=6):
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
        if stop<=0: stop = round(entry*0.97,2)
        risk = max(entry - stop, 0.000001)
        tp1 = round(entry + risk*1.6, 2 if entry>10 else 4)
        tp2 = round(entry + risk*2.8, 2 if entry>10 else 4)
        try: name = yf.Ticker(t).info.get("shortName", t)
        except: name = t
        cands.append({**d,"nombre":name,"score":score,"motivos":motivos,
                      "entry":round(entry,2 if entry>10 else 4),"stop":stop,"tp1":tp1,"tp2":tp2,
                      "rr":round((tp1-entry)/risk,1) if risk>0 else 0})
    cands.sort(key=lambda x: x["score"], reverse=True)
    return cands[:n]

def create_signal_chart(d):
    hist = d["hist"].tail(60)
    fig, ax = plt.subplots(figsize=(9,4.5), facecolor="#0b0f14")
    ax.set_facecolor("#0b0f14")
    ax.plot(hist.index, hist["Close"], color="#58a6ff", lw=1.6)
    ax.axhline(d["entry"], color="#3fb950", ls="--", lw=1.2, label=f"Entrada {d['entry']}")
    ax.axhline(d["tp1"], color="#2ea043", ls=":", lw=1.1, label=f"TP1 {d['tp1']}")
    ax.axhline(d["tp2"], color="#238636", ls=":", lw=1.1, label=f"TP2 {d['tp2']}")
    ax.axhline(d["stop"], color="#f85149", ls="--", lw=1.2, label=f"Stop {d['stop']}")
    ax.fill_between(hist.index, d["stop"], d["entry"], color="#f85149", alpha=0.08)
    ax.fill_between(hist.index, d["entry"], d["tp2"], color="#3fb950", alpha=0.07)
    ax.set_title(f"{d['nombre']} ({d['ticker']}) | Score {d['score']}", color="white", fontsize=12, pad=8)
    ax.tick_params(colors="#8b949e")
    ax.legend(facecolor="#161b22", edgecolor="#30363d", labelcolor="white", fontsize=8, loc="upper left")
    for spine in ax.spines.values(): spine.set_color("#30363d")
    ax.grid(True, alpha=0.15, color="#8b949e")
    fig.autofmt_xdate()
    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=130, facecolor="#0b0f14", bbox_inches="tight")
    plt.close()
    buf.seek(0)
    return buf

def send_signals(message, region="US"):
    bot.send_chat_action(message.chat.id, "typing")
    stocks = US_STOCKS if region=="US" else EU_STOCKS
    titulo = "🇺🇸 SEÑALES EEUU" if region=="US" else "🇪🇺 SEÑALES EUROPA"
    vix = get_vix()
    try:
        signals = get_top_signals(stocks, n=3, min_score=6 if region=="US" else 5)
        if not signals:
            bot.send_message(message.chat.id, f"No hay señales claras en {region} ahora.", reply_markup=get_kb())
            return
        bot.send_message(message.chat.id, f"**{titulo}** — {datetime.now().strftime('%d/%m %H:%M')}\n_VIX actual: {vix:.1f}_")
        for s in signals:
            explicacion = ask_gemini(
                f"Analiza técnicamente la siguiente señal de trading en {s['nombre']} ({s['ticker']}):\n"
                f"- Precio Entrada: {s['entry']}\n"
                f"- Stop Loss: {s['stop']} | Take Profit 1: {s['tp1']} | Take Profit 2: {s['tp2']}\n"
                f"- Ratio R/R: {s['rr']}x\n"
                f"- Indicadores clave: RSI {s['rsi']:.1f}, Volumen Relativo {s['vol_rel']:.1f}x, Motivos: {', '.join(s['motivos'])}\n"
                f"- VIX Global: {vix:.1f}\n\n"
                f"Explica con métricas cuantitativas por qué el ratio riesgo/beneficio es adecuado y qué catalizador del mercado podría impulsar el objetivo de ganancias.",
                max_tokens=450
            )
            caption = (f"**{s['nombre']} ({s['ticker']})** — Score **{s['score']}**\n\n"
                       f"🟢 Entrada: `{s['entry']}`\n🎯 TP1: `{s['tp1']}` | TP2: `{s['tp2']}`\n"
                       f"🔴 Stop: `{s['stop']}` | R/R: **{s['rr']}x**\n"
                       f"RSI: {s['rsi']:.1f} | Vol: {s['vol_rel']:.1f}x\n\n"
                       f"_{' · '.join(s['motivos'])}_")
            if explicacion:
                caption += f"\n\n**Análisis IA con Datos:**\n{explicacion}"
            try:
                bot.send_photo(message.chat.id, create_signal_chart(s), caption=caption)
            except:
                bot.send_message(message.chat.id, caption)
            time.sleep(1.0)
    except Exception as e:
        bot.send_message(message.chat.id, f"⚠️ Error: {e}", reply_markup=get_kb())

# ===================== ANALIZA DETALLADO =====================
def send_analiza(message, raw):
    bot.send_chat_action(message.chat.id, "typing")
    try:
        data = calculate_value_index(raw)
        p = data["price"]
        ps = f"{p:.8f}" if p<0.001 else f"{p:.6f}" if p<0.1 else f"{p:.4f}" if p<1 else f"{p:.2f}" if p<1000 else f"{p:,.0f}"

        comp_details = "\n".join([f"• {n}: {v} (Puntuación: {s}/10)" for n, v, s in data["components"]])
        soportes_str = " | ".join(map(str, data["supports"])) if data["supports"] else "No calculados"
        resistencias_str = " | ".join(map(str, data["resistances"])) if data["resistances"] else "No calculados"

        prompt = (
            f"Elabora un informe financiero detallado y fundamentado en datos sobre {data['name']} ({data['ticker']}).\n\n"
            f"**Métricas y Datos cuantitativos:**\n"
            f"- Precio Actual: ${ps} (Cambio 24h: {data['d1']:+.2f}%)\n"
            f"- Índice Global de Valor: {data['score']}/100 ({data['label']})\n"
            f"- Desglose de Indicadores del Modelo:\n{comp_details}\n"
            f"- Pivotes Técnicos -> Soportes: {soportes_str} | Resistencias: {resistencias_str}\n\n"
            f"**Desarrolla el análisis estructurado en estas 4 secciones:**\n"
            f"1. **Estructura Técnica y Datos Key:** Interpreta numéricamente el RSI, volumen y medias móviles frente al precio.\n"
            f"2. **Entorno Macro y Catalizadores:** Cómo influye el contexto macroeconómico, el VIX o la dominancia de USDT en este activo.\n"
            f"3. **Evaluación de Riesgo/Recompensa:** Detalla si estamos en zona de acumulación, distribución o alta volatilidad.\n"
            f"4. **Veredicto Operativo:** Conclusión directa con niveles clave de compra/salida."
        )
        analisis = ask_gemini(prompt, max_tokens=700)

        text = (f"🔍 **Análisis Financiero Avanzado: {data['name']} ({data['ticker']})**\n\n"
                f"Cotización: **{ps}** ({data['d1']:+.2f}%)\n"
                f"Valoración Técnica: **{data['score']}/100** — _{data['label']}_\n\n")
        if analisis:
            text += analisis
        else:
            text += data["outlook"]
        bot.send_message(message.chat.id, text, reply_markup=get_kb())
    except Exception as e:
        bot.reply_to(message, f"⚠️ No pude analizar **{raw.upper()}**: {e}")

# ===================== HANDLERS Y MENÚ =====================
def get_kb():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("💎 Índice Valor", callback_data="valor_help"),
        InlineKeyboardButton("🔍 Analizar", callback_data="analiza_help"),
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
        "🤖 **Bot Financiero Profesional + IA Gemini 3.6**\n\n"
        "• `/valor TICKER` → Medidor de valor cuantitativo + indicadores\n"
        "• `/analiza TICKER` → Informe detallado con datos macro e IA\n"
        "• `/senales_eu` / `/senales_us` → Señales técnicas con R/R e IA\n"
        "• `/noticias` → Noticias en vivo + impacto cuantitativo\n\n"
        "_Ejemplos: /valor BTC  /analiza NVDA  /valor ORO_",
        reply_markup=get_kb())

@bot.callback_query_handler(func=lambda c: True)
def cb(call):
    if not is_authorized(call.from_user.id): return
    bot.answer_callback_query(call.id)
    if call.data == "valor_help":
        bot.send_message(call.message.chat.id, "💎 Ejemplos:\n`/valor PEPE`\n`/valor BTC`\n`/valor TSLA`\n`/valor ORO`")
    elif call.data == "analiza_help":
        bot.send_message(call.message.chat.id, "🔍 Ejemplos:\n`/analiza BTC`\n`/analiza NVDA`\n`/analiza AAPL`")
    elif call.data == "senales_eu":
        send_signals(call.message, "EU")
    elif call.data == "senales_us":
        send_signals(call.message, "US")
    elif call.data == "noticias":
        noticias(call.message)
    elif call.data == "menu":
        start(call.message)

@bot.message_handler(commands=["valor","value"])
def cmd_valor(m):
    if not is_authorized(m.from_user.id): return
    args = m.text.split()
    if len(args)<2:
        bot.reply_to(m, "⚠️ Usa: `/valor PEPE` o `/valor TSLA`")
        return
    send_valor(m, args[1])

def send_valor(m, raw):
    bot.send_chat_action(m.chat.id, "upload_photo")
    try:
        data = calculate_value_index(raw)
        p = data["price"]
        ps = f"{p:.8f}" if p<0.001 else f"{p:.6f}" if p<0.1 else f"{p:.4f}" if p<1 else f"{p:.2f}" if p<1000 else f"{p:,.0f}"
        title = f"{data['name']} — {ps} ({data['d1']:+.2f}%)"
        img = create_gauge(data["score"], title, data["label"], data["components"])
        cap = f"**{data['name']} ({data['ticker']}) — {ps} ({data['d1']:+.2f}%)**\n**{data['score']}/100 — {data['label']}**\n\n"
        if data["supports"]:
            cap += f"**Soportes Clave:** {' | '.join(map(str,data['supports']))}\n"
        if data["resistances"]:
            cap += f"**Resistencias Clave:** {' | '.join(map(str,data['resistances']))}\n"
        cap += f"\n**Análisis Cuantitativo IA:**\n{data['outlook']}"
        bot.send_photo(m.chat.id, img, caption=cap, reply_markup=get_kb())
    except Exception as e:
        bot.reply_to(m, f"⚠️ No pude obtener datos de **{raw.upper()}**.")

@bot.message_handler(commands=["analiza","analisis","analyze"])
def cmd_analiza(m):
    if not is_authorized(m.from_user.id): return
    args = m.text.split()
    if len(args)<2:
        bot.reply_to(m, "⚠️ Usa: `/analiza TSLA` o `/analiza BTC`")
        return
    send_analiza(m, args[1])

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
            for e in feedparser.parse(url).entries[:5]:
                if e.title not in heads: heads.append(e.title)
        except: pass

    vix = get_vix()
    text = f"📰 **NOTICIAS FINANCIERAS Y ANÁLISIS MACRO**\n_VIX de Volatilidad: {vix:.1f}_\n\n"
    if heads and GEMINI_API_KEY:
        resumen = ask_gemini(
            "Analiza los siguientes titulares financieros de última hora:\n" + "\n".join(heads[:6]) + f"\n\nContexto: VIX actual en {vix:.1f}.\n"
            "Realiza un resumen ejecutivo estructurado con:\n"
            "1) Impacto económico directo y datos relevantes mencionados.\n"
            "2) Sesgo general del mercado (Alcista / Bajista / Neutral) con justificación macro.\n"
            "3) Implicaciones concretas para bolsa y criptomonedas.",
            max_tokens=500
        )
        if resumen:
            text += resumen + "\n\n**Titulares Destacados:**\n"
    text += "\n".join([f"• {h}" for h in heads[:5]] or ["Sin noticias disponibles."])
    bot.send_message(m.chat.id, text, reply_markup=get_kb())

@bot.message_handler(func=lambda m: True)
def fb(m):
    if is_authorized(m.from_user.id) and m.text and not m.text.startswith("/"):
        bot.reply_to(m, "Usa `/valor TICKER`, `/analiza TICKER`, `/senales_eu` o `/senales_us`", reply_markup=get_kb())

if __name__ == "__main__":
    print("Bot activo con Gemini 3.6 Flash + Análisis Macro e Indicadores cuantitativos")
    bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=30)
