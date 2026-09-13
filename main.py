#!/usr/bin/env python3
"""AnalisisPro Bot — Versión corregida con /valor /fundamental /halvingbtc"""
import os, io, time, logging, requests
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.dates as mdates
import telebot
from groq import Groq
import pytz
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)

TELEGRAM_TOKEN  = os.environ["TELEGRAM_TOKEN"]
GROQ_API_KEY    = os.environ["GROQ_API_KEY"]
ALLOWED_USER_ID = int(os.environ.get("ALLOWED_USER_ID", 0))
MADRID = pytz.timezone("Europe/Madrid")

bot = telebot.TeleBot(TELEGRAM_TOKEN)
ai  = Groq(api_key=GROQ_API_KEY)

SYSTEM = """Eres un analista financiero senior. Responde SIEMPRE en español.
Sin markdown. Máximo 4 párrafos concisos y accionables."""

# ═══ CACHÉ + RETRY (fix rate-limit yfinance/Stooq) ══════════
_CACHE = {}
CACHE_TTL = 180  # segundos — 3 min

def cache_get(key):
    hit = _CACHE.get(key)
    if not hit: return None
    ts, val = hit
    if time.time() - ts > CACHE_TTL:
        _CACHE.pop(key, None)
        return None
    return val

def cache_set(key, val):
    _CACHE[key] = (time.time(), val)

def with_retry(fn, tries=3, base_delay=2, what=""):
    """Reintenta fn() con espera progresiva si detecta rate-limit ('Too Many
    Requests', 429, 'Rate limited'). Devuelve None si todos los intentos fallan."""
    last_err = None
    for i in range(tries):
        try:
            return fn()
        except Exception as e:
            last_err = e
            msg = str(e)
            is_rate_limit = ("Too Many Requests" in msg or "429" in msg
                              or "Rate limited" in msg or "rate limit" in msg.lower())
            wait = base_delay * (i + 1) if is_rate_limit else base_delay
            log.warning(f"with_retry {what} intento {i+1}/{tries} falló ({'rate-limit' if is_rate_limit else 'error'}): {e}")
            if i < tries - 1:
                time.sleep(wait)
    log.warning(f"with_retry {what}: agotados los reintentos, último error: {last_err}")
    return None

def allowed(msg):
    if ALLOWED_USER_ID == 0: return True
    return msg.from_user.id == ALLOWED_USER_ID

def safe_send(chat_id, text, message_id=None, **kwargs):
    """Envía un mensaje nuevo, o edita uno existente si se pasa message_id.
    FIX: antes se llamaba a bot.send_message(..., message_id=...) lo cual
    provocaba 'TeleBot.send_message() got an unexpected keyword argument
    message_id'. Ahora, si hay message_id, se intenta editar ese mensaje;
    si falla la edición (p.ej. mensaje ya borrado), se envía uno nuevo."""
    try:
        if message_id:
            try:
                bot.edit_message_text(text[:4096], chat_id, message_id, **kwargs)
                return
            except Exception as e:
                log.debug(f"safe_send: edit_message_text falló, envío nuevo mensaje: {e}")
        bot.send_message(chat_id, text[:4096], **kwargs)
    except Exception as e:
        log.warning(f"safe_send: {e}")

def ask_ai(prompt, max_chars=2500):
    try:
        r = ai.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=[{"role":"system","content":SYSTEM},
                      {"role":"user","content":prompt}],
            max_tokens=1024, temperature=0.7)
        t = r.choices[0].message.content
        return t[:max_chars]
    except Exception as e:
        log.error(f"Groq: {e}")
        return "IA no disponible."

def calc_rsi(s, period=14):
    d = s.diff()
    g = d.clip(lower=0).rolling(period).mean()
    l = (-d.clip(upper=0)).rolling(period).mean()
    rs = g / l.replace(0, 1e-10)
    return 100 - 100 / (1 + rs)

def calc_macd(s, fast=12, slow=26, signal=9):
    ef = s.ewm(span=fast, adjust=False).mean()
    es = s.ewm(span=slow, adjust=False).mean()
    m = ef - es
    sig = m.ewm(span=signal, adjust=False).mean()
    return m, sig

def build_quote(ticker, c, h, lo, vol):
    price = float(c.iloc[-1])
    d1  = float((price-c.iloc[-2])/c.iloc[-2]*100) if len(c)>1 else 0
    d5  = float((price-c.iloc[-6])/c.iloc[-6]*100) if len(c)>5 else 0
    d20 = float((price-c.iloc[-21])/c.iloc[-21]*100) if len(c)>20 else 0
    rsi = float(calc_rsi(c).iloc[-1]) if len(c)>=14 else 50
    ml, ms = calc_macd(c)
    macd_up = len(ml)>=2 and ml.iloc[-1]>ms.iloc[-1] and ml.iloc[-2]<=ms.iloc[-2]
    avg_vol = vol.tail(20).mean()
    vol_rel = float(vol.iloc[-1]/avg_vol) if avg_vol>0 else 1.0
    ema20  = c.ewm(span=20, adjust=False).mean()
    ema50  = c.ewm(span=50, adjust=False).mean()
    ema200 = c.ewm(span=200, adjust=False).mean()
    return {
        "ticker":ticker,"price":round(price,2),
        "d1":round(d1,2),"d5":round(d5,2),"d20":round(d20,2),
        "hi52":round(float(h.max()),2),"lo52":round(float(lo.min()),2),
        "rsi":round(rsi,1),"macd_up":macd_up,"vol_rel":round(vol_rel,2),
        "ema20":round(float(ema20.iloc[-1]),2),
        "ema50":round(float(ema50.iloc[-1]),2),
        "ema200":round(float(ema200.iloc[-1]),2) if len(c)>=200 else 0,
        "sobre_ema200":(price>float(ema200.iloc[-1])) if len(c)>=200 else None,
        "tendencia":float(ema20.iloc[-1])>float(ema50.iloc[-1]),
        "closes":c,"highs":h,"lows":lo,"vols":vol,
    }

BINANCE_MAP = {
    "BTC-USD":"BTCUSDT","ETH-USD":"ETHUSDT","SOL-USD":"SOLUSDT",
    "BNB-USD":"BNBUSDT","XRP-USD":"XRPUSDT","ADA-USD":"ADAUSDT",
    "AVAX-USD":"AVAXUSDT","LINK-USD":"LINKUSDT","DOGE-USD":"DOGEUSDT",
}
STOOQ_MAP = {
    "^GSPC":"^spx","^IXIC":"^ndx","^GDAXI":"^dax","^IBEX":"^ibex",
    "^FCHI":"^cac","^FTSE":"^ukx","^VIX":"^vix","^TNX":"^tnx",
    "DX-Y.NYB":"usdidx","GC=F":"xauusd","CL=F":"cl.f",
    "AAPL":"aapl.us","MSFT":"msft.us","NVDA":"nvda.us","TSLA":"tsla.us",
    "AMZN":"amzn.us","GOOGL":"googl.us","META":"meta.us","AMD":"amd.us",
    "INTC":"intc.us","IONQ":"ionq.us","PLTR":"pltr.us","COIN":"coin.us",
    "NKE":"nke.us","DIS":"dis.us","JPM":"jpm.us","BAC":"bac.us",
    "XOM":"xom.us","IBIT":"ibit.us","SPY":"spy.us","QQQ":"qqq.us",
    "GLD":"gld.us","MP":"mp.us","RKLB":"rklb.us","SMCI":"smci.us",
    "NFLX":"nflx.us","UBER":"uber.us","ABNB":"abnb.us",
}

def fetch_binance(symbol, days=100):
    ck = f"binance:{symbol}"
    cached = cache_get(ck)
    if cached is not None: return cached
    def _do():
        r = requests.get("https://api.binance.com/api/v3/klines",
                        params={"symbol":symbol,"interval":"1d","limit":days+10},timeout=8)
        if r.status_code!=200: raise RuntimeError(f"HTTP {r.status_code}")
        klines = r.json()
        if len(klines)<5: raise RuntimeError("pocos datos")
        c  = pd.Series([float(k[4]) for k in klines])
        h  = pd.Series([float(k[2]) for k in klines])
        lo = pd.Series([float(k[3]) for k in klines])
        vol= pd.Series([float(k[5]) for k in klines])
        return build_quote(symbol.replace("USDT","-USD"),c,h,lo,vol)
    res = with_retry(_do, tries=2, base_delay=2, what=f"fetch_binance {symbol}")
    if res: cache_set(ck, res)
    return res

def fetch_yfinance_fallback(ticker, days=100):
    """Fallback para cuando Stooq no devuelve datos (rate-limit, ticker
    desconocido, bloqueo puntual, etc). Usa yfinance con caché + reintentos
    con espera progresiva para evitar el rate-limit de Yahoo."""
    ck = f"yf:{ticker}"
    cached = cache_get(ck)
    if cached is not None: return cached
    def _do():
        import yfinance as yf
        hist = yf.Ticker(ticker).history(period=f"{days+30}d")
        if hist is None or hist.empty or len(hist) < 5:
            raise RuntimeError("sin datos")
        c  = hist["Close"].reset_index(drop=True)
        h  = hist["High"].reset_index(drop=True)
        lo = hist["Low"].reset_index(drop=True)
        vol = hist["Volume"].reset_index(drop=True) if "Volume" in hist.columns else pd.Series([1e6]*len(c))
        return build_quote(ticker, c, h, lo, vol)
    res = with_retry(_do, tries=3, base_delay=3, what=f"fetch_yfinance_fallback {ticker}")
    if res: cache_set(ck, res)
    return res

def fetch_stooq(ticker, days=100):
    import datetime as dt
    t = ticker.upper()
    ck = f"stooq:{t}"
    cached = cache_get(ck)
    if cached is not None: return cached
    def to_stooq(t):
        if t in STOOQ_MAP: return STOOQ_MAP[t]
        if t.endswith(".MC"): return t.lower()
        if t.endswith(".DE"): return t.lower()
        if t.endswith(".PA"): return t.lower()
        if t.endswith(".L"):  return t.lower().replace(".l",".uk")
        if "." not in t: return f"{t.lower()}.us"
        return t.lower()
    st = to_stooq(t)
    def _do():
        d1 = (dt.date.today()-dt.timedelta(days=days+30)).strftime("%Y%m%d")
        d2 = dt.date.today().strftime("%Y%m%d")
        url = f"https://stooq.com/q/d/l/?s={st}&d1={d1}&d2={d2}&i=d"
        r = requests.get(url,timeout=10,headers={"User-Agent":"Mozilla/5.0"})
        if r.status_code!=200 or "No data" in r.text or len(r.text)<50:
            raise RuntimeError("sin datos en Stooq")
        from io import StringIO
        df = pd.read_csv(StringIO(r.text))
        if df.empty or len(df)<5:
            raise RuntimeError("Stooq: pocos datos")
        df.columns = [c.strip() for c in df.columns]
        df = df.sort_values("Date")
        c  = df["Close"].astype(float)
        h  = df["High"].astype(float)
        lo = df["Low"].astype(float)
        vol= df["Volume"].astype(float) if "Volume" in df.columns else pd.Series([1e6]*len(c))
        return build_quote(t,c,h,lo,vol)
    res = with_retry(_do, tries=2, base_delay=1, what=f"fetch_stooq {ticker}")
    if res is None:
        log.debug(f"fetch_stooq {ticker}({st}): agotado, probando yfinance")
        res = fetch_yfinance_fallback(t, days)
    if res: cache_set(ck, res)
    return res

def get_quote(ticker):
    t = ticker.upper()
    if t in BINANCE_MAP:
        res = fetch_binance(BINANCE_MAP[t])
        if res is not None:
            return res
        # Fallback si Binance está bloqueado (HTTP 451, etc.)
        log.warning(f"Binance falló para {t}, usando yfinance")
        return fetch_yfinance_fallback(t)
    # Acciones: yfinance primero (Stooq falla con mucha frecuencia)
    res = fetch_yfinance_fallback(t)
    if res is not None:
        return res
    return fetch_stooq(t)

def get_fear_greed():
    try:
        r = requests.get("https://api.alternative.me/fng/?limit=1",timeout=6)
        d = r.json()["data"][0]
        return {"valor":int(d["value"]),"texto":d["value_classification"]}
    except: return None

def get_funding(symbol="BTCUSDT"):
    try:
        r = requests.get("https://fapi.binance.com/fapi/v1/fundingRate",
                        params={"symbol":symbol,"limit":1},timeout=6)
        return float(r.json()[0]["fundingRate"])*100
    except: return None

def get_dxy():
    try:
        r = requests.get("https://api.frankfurter.app/latest?from=EUR&to=USD",timeout=6)
        eurusd = r.json()["rates"]["USD"]
        return round(210-eurusd*100,2)
    except: return None

def get_trends(keyword="Bitcoin"):
    try:
        from pytrends.request import TrendReq
        pt = TrendReq(hl='es-ES',tz=60,timeout=(5,15))
        pt.build_payload([keyword],timeframe="today 3-m")
        df = pt.interest_over_time()
        if df.empty: return None
        val=int(df[keyword].iloc[-1]); mx=int(df[keyword].max())
        return round(val/mx*100) if mx>0 else 50
    except: return None

# ═══ /VALOR ══════════════════════════════════════════════════

def calcular_valor(ticker):
    t = ticker.upper()
    es_crypto = t in BINANCE_MAP
    d = get_quote(t)
    if not d:
        log.warning(f"calcular_valor {t}: sin datos de precio")
        return None

    comp = {}

    # EMA 200
    if d["sobre_ema200"] is not None:
        dist = round((d["price"]-d["ema200"])/d["ema200"]*100,1) if d["ema200"]>0 else 0
        if dist<-30:  p=10; v=f"{dist:+.1f}% — muy bajo (suelo)"
        elif dist<-15:p=8;  v=f"{dist:+.1f}% — bajo EMA200"
        elif dist<-5: p=6;  v=f"{dist:+.1f}% — tocando EMA200"
        elif dist<0:  p=5;  v=f"{dist:+.1f}% — justo bajo"
        elif dist<10: p=3;  v=f"{dist:+.1f}% — sobre EMA200 (bull)"
        elif dist<25: p=2;  v=f"{dist:+.1f}% — alejado"
        else:         p=1;  v=f"{dist:+.1f}% — euforia"
        comp["EMA 200"] = {"p":p,"v":v}
    else:
        comp["EMA 200"] = {"p":5,"v":"N/D"}

    # RSI
    rsi = d["rsi"]
    if rsi<25:   p=10; v=f"{rsi} — sobreventa extrema"
    elif rsi<35: p=8;  v=f"{rsi} — sobreventa"
    elif rsi<45: p=6;  v=f"{rsi} — zona compra"
    elif rsi<55: p=5;  v=f"{rsi} — neutral"
    elif rsi<65: p=3;  v=f"{rsi} — sobrecompra leve"
    elif rsi<75: p=2;  v=f"{rsi} — sobrecompra"
    else:        p=1;  v=f"{rsi} — sobrecompra extrema"
    comp["RSI 14d"] = {"p":p,"v":v}

    if es_crypto:
        fg = get_fear_greed()
        if fg:
            val=fg["valor"]
            if val<15:   p=10; v=f"{val} — miedo extremo"
            elif val<30: p=8;  v=f"{val} — miedo"
            elif val<45: p=6;  v=f"{val} — miedo moderado"
            elif val<55: p=5;  v=f"{val} — neutral"
            elif val<70: p=3;  v=f"{val} — codicia"
            else:        p=1;  v=f"{val} — codicia extrema"
            comp["Fear & Greed"] = {"p":p,"v":v}
        else:
            comp["Fear & Greed"] = {"p":5,"v":"N/D"}

        sym_map = {"BTC-USD":"BTCUSDT","ETH-USD":"ETHUSDT","SOL-USD":"SOLUSDT"}
        sym = sym_map.get(t,"BTCUSDT")
        fr = get_funding(sym)
        if fr is not None:
            if fr<-0.02: p=10; v=f"{fr:+.4f}% — shorts pagando"
            elif fr<0:   p=7;  v=f"{fr:+.4f}% — negativo"
            elif fr<0.01:p=5;  v=f"{fr:+.4f}% — neutro"
            elif fr<0.03:p=3;  v=f"{fr:+.4f}% — longs pagando"
            else:        p=1;  v=f"{fr:+.4f}% — apalancado"
            comp["Funding Rate"] = {"p":p,"v":v}
        else:
            comp["Funding Rate"] = {"p":5,"v":"N/D"}

        dxy = get_dxy()
        if dxy:
            if dxy>106:   p=9; v=f"~{dxy} — muy fuerte"
            elif dxy>103: p=7; v=f"~{dxy} — fuerte"
            elif dxy>100: p=5; v=f"~{dxy} — neutral"
            elif dxy>97:  p=3; v=f"~{dxy} — débil"
            else:         p=2; v=f"~{dxy} — muy débil"
            comp["DXY Dólar"] = {"p":p,"v":v}
        else:
            comp["DXY Dólar"] = {"p":5,"v":"N/D"}

        kw = "Bitcoin" if "BTC" in t else "Ethereum" if "ETH" in t else "crypto"
        tr = get_trends(kw)
        if tr is not None:
            if tr<15:   p=10; v=f"{tr}% — nadie busca (suelo)"
            elif tr<25: p=8;  v=f"{tr}% — interés muy bajo"
            elif tr<40: p=6;  v=f"{tr}% — interés bajo"
            elif tr<60: p=5;  v=f"{tr}% — normal"
            elif tr<75: p=3;  v=f"{tr}% — alto"
            else:       p=1;  v=f"{tr}% — euforia"
            comp["Google Trends"] = {"p":p,"v":v}
        else:
            comp["Google Trends"] = {"p":5,"v":"N/D"}

        import datetime as dt
        meses = (dt.date.today()-dt.date(2024,4,19)).days//30
        if meses<6:    p=7; v=f"Mes {meses} — bull temprano"
        elif meses<18: p=4; v=f"Mes {meses} — bull maduro"
        elif meses<22: p=2; v=f"Mes {meses} — zona techo"
        elif meses<32: p=6; v=f"Mes {meses} — bear normal"
        elif meses<40: p=9; v=f"Mes {meses} — suelo histórico"
        else:          p=8; v=f"Mes {meses} — recuperación"
        comp["Ciclo Halving"] = {"p":p,"v":v}

        max_pts = len(comp)*10
    else:
        vix_d = get_quote("^VIX")
        if vix_d:
            vix=vix_d["price"]
            if vix>35:   p=10; v=f"{vix} — pánico"
            elif vix>28: p=8;  v=f"{vix} — miedo"
            elif vix>22: p=6;  v=f"{vix} — moderado"
            elif vix>16: p=3;  v=f"{vix} — calma"
            else:        p=2;  v=f"{vix} — complacencia"
            comp["VIX"] = {"p":p,"v":v}
        else:
            comp["VIX"] = {"p":5,"v":"N/D"}

        dist52 = round((d["price"]-d["hi52"])/d["hi52"]*100,1) if d["hi52"]>0 else 0
        if dist52<-40:   p=10; v=f"{dist52:+.1f}% desde máximo"
        elif dist52<-25: p=8;  v=f"{dist52:+.1f}% desde máximo"
        elif dist52<-15: p=6;  v=f"{dist52:+.1f}% desde máximo"
        elif dist52<-5:  p=4;  v=f"{dist52:+.1f}% desde máximo"
        else:            p=1;  v=f"{dist52:+.1f}% — cerca del techo"
        comp["Dist. Máximo"] = {"p":p,"v":v}
        max_pts = len(comp)*10

    total = sum(x["p"] for x in comp.values())
    score = max(0, min(100, round(total/max_pts*100)))
    if score>=80:   zona="BARATO — ACUMULACIÓN FUERTE"
    elif score>=65: zona="BARATO — BUENA ZONA"
    elif score>=45: zona="NEUTRAL"
    elif score>=30: zona="CARO — PRECAUCIÓN"
    else:           zona="MUY CARO — EVITAR"

    return {"ticker":t,"price":d["price"],"d1":d["d1"],
            "score":score,"zona":zona,"comp":comp}

def chart_valor(res):
    comp=res["comp"]; n=len(comp)
    fig=plt.figure(figsize=(12,8+n*0.6))
    fig.patch.set_facecolor('#0d1117')
    ax=fig.add_axes([0.05,0.45,0.90,0.50],projection='polar')
    ax.set_facecolor('#0d1117')
    theta=np.linspace(np.pi,0,101)
    for i in range(100):
        if i<30:    c='#FF3333'
        elif i<45:  c='#FF7700'
        elif i<65:  c='#FFCC00'
        elif i<80:  c='#99DD00'
        else:       c='#00CC44'
        ax.barh(1,theta[i]-theta[i+1],left=theta[i+1],height=0.45,color=c,edgecolor='none')
    score=res["score"]; angle=np.pi-(score/100*np.pi)
    ax.plot([angle,angle],[0,1.10],color='white',linewidth=6,zorder=5)
    ax.plot(angle,0,'o',color='white',markersize=22,zorder=6)
    ax.plot(angle,0,'o',color='#0d1117',markersize=11,zorder=7)
    ax.set_ylim(0,1.35); ax.set_theta_zero_location('E'); ax.set_theta_direction(1)
    ax.set_thetamin(0); ax.set_thetamax(180)
    ax.set_xticks([np.pi,3*np.pi/4,np.pi/2,np.pi/4,0])
    ax.set_xticklabels(['0\nCARO','25','50\nNEUTRAL','75','100\nBARATÓ'],
                       color='white',fontsize=10,fontweight='bold')
    ax.set_yticks([]); ax.spines['polar'].set_visible(False); ax.grid(False)
    zc='#FF3333' if score<30 else '#FF7700' if score<45 else '#FFCC00' if score<65 else '#99DD00' if score<80 else '#00CC44'
    fig.text(0.5,0.475,f"{score}/100",ha='center',va='center',fontsize=34,color='white',fontweight='bold')
    fig.text(0.5,0.430,res["zona"],ha='center',va='center',fontsize=12,color=zc,fontweight='bold')
    fig.text(0.5,0.975,f"{res['ticker']}",ha='center',fontsize=15,color='white',fontweight='bold')
    fig.text(0.5,0.950,f"{res['price']} USD  ({res['d1']:+.2f}% hoy)",ha='center',fontsize=11,color='#AAAAAA')
    fig.text(0.5,0.408,'─'*60,ha='center',color='#333333',fontsize=8)
    fig.text(0.5,0.400,'COMPONENTES',ha='center',fontsize=10,color='#666666',fontweight='bold')
    ax2=fig.add_axes([0.05,0.03,0.90,0.36])
    ax2.set_facecolor('#0d1117'); ax2.axis('off')
    ax2.set_xlim(-3.5,11.5); ax2.set_ylim(-0.5,n-0.5)
    for idx,(nom,datos) in enumerate(reversed(list(comp.items()))):
        pts=datos['p']; val=datos['v']; y=idx
        ax2.barh(y,10,height=0.65,color='#1a1a2e',zorder=1)
        bc='#FF3333' if pts<=3 else '#FF7700' if pts<=5 else '#FFCC00' if pts<=7 else '#00CC44'
        ax2.barh(y,pts,height=0.65,color=bc,alpha=0.9,zorder=2)
        ax2.text(-0.2,y,nom,va='center',ha='right',color='white',fontsize=11,fontweight='bold')
        ax2.text(pts+0.15,y,str(val)[:40],va='center',ha='left',color='#CCCCCC',fontsize=9)
        ax2.text(10.2,y,f"{pts}/10",va='center',ha='left',color=bc,fontsize=11,fontweight='bold')
    buf=io.BytesIO()
    try: plt.tight_layout(pad=1.5)
    except: pass
    plt.savefig(buf,format='png',dpi=130,facecolor='#0d1117',bbox_inches='tight')
    plt.close(); buf.seek(0)
    return buf

@bot.message_handler(commands=["valor"])
def cmd_valor(msg):
    if not allowed(msg): return
    parts = msg.text.split()
    if len(parts)<2:
        safe_send(msg.chat.id,
            "Uso: /valor TICKER\n\nEjemplos:\n/valor BTC-USD\n/valor ETH-USD\n/valor TSLA\n/valor NVDA\n/valor SAN.MC")
        return
    ticker = parts[1].upper()
    m = bot.send_message(msg.chat.id, f"Calculando índice para {ticker}...")
    res = calcular_valor(ticker)
    if not res:
        safe_send(msg.chat.id, f"Sin datos para {ticker}. Comprueba el ticker.", message_id=m.message_id)
        return
    try:
        chart = chart_valor(res)
        bot.delete_message(msg.chat.id, m.message_id)
        bot.send_photo(msg.chat.id, chart)
    except Exception as e:
        log.warning(f"chart_valor: {e}")
        safe_send(msg.chat.id,
            f"{res['ticker']} — {res['price']} USD ({res['d1']:+.2f}%)\n"
            f"{res['score']}/100 — {res['zona']}\n\n"+
            "\n".join([f"{k}: {v['v']} ({v['p']}/10)" for k,v in res['comp'].items()]),
            message_id=m.message_id)
    comp_txt = "\n".join([f"{k}: {v['v']} ({v['p']}/10)" for k,v in res['comp'].items()])
    prompt = (f"Índice barato/caro de {res['ticker']}:\nPrecio: {res['price']} USD ({res['d1']:+.2f}% hoy)\n"
              f"Score: {res['score']}/100 — {res['zona']}\nComponentes:\n{comp_txt}\n\n"
              f"1. ¿Es buen momento para entrar?\n2. Riesgo principal\n3. Estrategia concreta con precio")
    safe_send(msg.chat.id, f"ANÁLISIS IA\n\n{ask_ai(prompt)}")

# ═══ /FUNDAMENTAL ════════════════════════════════════════════

def fetch_fundamentales(ticker):
    """Fundamentales robustos: no depende del endpoint .info (roto con 401/crumb).
    Usa fast_info + financials/balance_sheet/cashflow, y .info solo como bonus si responde."""
    ck = f"fund:{ticker}"
    cached = cache_get(ck)
    if cached is not None:
        return cached

    def _do():
        import yfinance as yf
        t = yf.Ticker(ticker)

        # --- Precio y market cap vía fast_info (sigue funcionando) ---
        fi = t.fast_info
        if fi is None:
            raise RuntimeError("sin datos fundamentales")
        if not isinstance(fi, dict):
            try:
                fi = dict(fi)
            except Exception:
                fi = {
                    "lastPrice": getattr(fi, "last_price", None),
                    "marketCap": getattr(fi, "market_cap", None),
                    "yearHigh": getattr(fi, "year_high", None),
                    "yearLow": getattr(fi, "year_low", None),
                }
        price = fi.get("lastPrice") or fi.get("regularMarketPrice") or fi.get("last_price")
        mktcap = fi.get("marketCap") or fi.get("market_cap") or 0
        if not price:
            raise RuntimeError("sin precio")

        # Valores por defecto
        nombre = ticker
        sector = ""
        pe = peg = ev_ebitda = margen_neto = margen_bruto = roe = None
        fcf = caja = deuda_total = ebitda = None
        rev_growth = earn_growth = rev_ttm = current_ratio = None
        insider_pct = div_yield = None
        d1 = 0

        # --- Intento suave de .info (si un día vuelve, se aprovecha) ---
        try:
            info = t.info or {}
            if info and len(info) > 5:
                nombre = info.get("longName") or ticker
                sector = info.get("sector") or ""
                pe = info.get("trailingPE")
                peg = info.get("pegRatio")
                ev_ebitda = info.get("enterpriseToEbitda")
                margen_neto = info.get("profitMargins")
                margen_bruto = info.get("grossMargins")
                roe = info.get("returnOnEquity")
                fcf = info.get("freeCashflow")
                caja = info.get("totalCash")
                deuda_total = info.get("totalDebt")
                ebitda = info.get("ebitda")
                rev_growth = info.get("revenueGrowth")
                earn_growth = info.get("earningsGrowth")
                rev_ttm = info.get("totalRevenue")
                current_ratio = info.get("currentRatio")
                insider_pct = info.get("heldPercentInsiders")
                dy = info.get("dividendYield")
                if dy and dy > 1:
                    dy = dy / 100
                div_yield = dy
                d1 = info.get("regularMarketChangePercent") or 0
                if not mktcap:
                    mktcap = info.get("marketCap") or 0
                if not price:
                    price = info.get("regularMarketPrice") or info.get("currentPrice") or price
        except Exception as e:
            log.debug(f"info fallback {ticker}: {e}")

        # --- Relleno desde estados financieros si .info falló ---
        if pe is None or margen_neto is None or deuda_total is None or fcf is None:
            try:
                fin = t.financials
                bs = t.balance_sheet
                cf = t.cashflow
                if fin is not None and not fin.empty:
                    if "Net Income" in fin.index and "Total Revenue" in fin.index:
                        ni = fin.loc["Net Income"].iloc[0]
                        tr = fin.loc["Total Revenue"].iloc[0]
                        if tr and tr != 0:
                            margen_neto = float(ni / tr) if margen_neto is None else margen_neto
                            rev_ttm = float(tr) if rev_ttm is None else rev_ttm
                    if "Gross Profit" in fin.index and "Total Revenue" in fin.index:
                        gp = fin.loc["Gross Profit"].iloc[0]
                        tr = fin.loc["Total Revenue"].iloc[0]
                        if tr and tr != 0 and margen_bruto is None:
                            margen_bruto = float(gp / tr)
                    # Crecimiento ingresos (2 periodos)
                    if "Total Revenue" in fin.index and fin.shape[1] >= 2:
                        r0 = fin.loc["Total Revenue"].iloc[0]
                        r1 = fin.loc["Total Revenue"].iloc[1]
                        if r1 and r1 != 0 and rev_growth is None:
                            rev_growth = float((r0 - r1) / abs(r1))
                if bs is not None and not bs.empty:
                    for key in ("Total Debt", "Long Term Debt"):
                        if key in bs.index and deuda_total is None:
                            deuda_total = float(bs.loc[key].iloc[0])
                            break
                    for key in ("Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments"):
                        if key in bs.index and caja is None:
                            caja = float(bs.loc[key].iloc[0])
                            break
                    ca = bs.loc["Current Assets"].iloc[0] if "Current Assets" in bs.index else None
                    cl = bs.loc["Current Liabilities"].iloc[0] if "Current Liabilities" in bs.index else None
                    if ca and cl and cl != 0 and current_ratio is None:
                        current_ratio = float(ca / cl)
                if cf is not None and not cf.empty:
                    for key in ("Free Cash Flow", "Operating Cash Flow"):
                        if key in cf.index and fcf is None:
                            fcf = float(cf.loc[key].iloc[0])
                            if key == "Free Cash Flow":
                                break
            except Exception as e:
                log.debug(f"financials fallback {ticker}: {e}")

        return {
            "nombre": nombre,
            "sector": sector,
            "price": float(price),
            "d1": float(d1) if d1 else 0,
            "mktcap": float(mktcap) if mktcap else 0,
            "pe": pe,
            "peg": peg,
            "ev_ebitda": ev_ebitda,
            "margen_neto": margen_neto,
            "margen_bruto": margen_bruto,
            "roe": roe,
            "fcf": fcf,
            "caja": caja,
            "deuda_total": deuda_total,
            "ebitda": ebitda,
            "rev_growth": rev_growth,
            "earn_growth": earn_growth,
            "rev_ttm": rev_ttm,
            "current_ratio": current_ratio,
            "insider_pct": insider_pct,
            "div_yield": div_yield,
        }

    res = with_retry(_do, tries=3, base_delay=3, what=f"fetch_fundamentales {ticker}")
    if res:
        cache_set(ck, res)
    return res

def calcular_fundamental(ticker):
    f = fetch_fundamentales(ticker)
    if not f: return None
    sector = (f.get("sector") or "").lower()
    cats = {}

    # Valoración
    pts=10; notas=[]
    if "utility" in sector:
        if f["div_yield"]: pts+=3; notas.append(f"Div {f['div_yield']*100:.1f}%")
        notas.append("Utility — deuda alta normal")
    else:
        if f["pe"]:
            if f["pe"]<15:   pts+=4; notas.append(f"P/E {f['pe']:.1f} barato")
            elif f["pe"]<25: pts+=2; notas.append(f"P/E {f['pe']:.1f} razonable")
            elif f["pe"]<40: pts-=1; notas.append(f"P/E {f['pe']:.1f} elevado")
            else:            pts-=3; notas.append(f"P/E {f['pe']:.1f} caro")
        if f["peg"]:
            if f["peg"]<1:   pts+=3; notas.append(f"PEG {f['peg']:.2f} infravalorado")
            elif f["peg"]<2: pts+=1
            else:            pts-=2; notas.append(f"PEG {f['peg']:.2f} caro")
    cats["Valoración"] = {"pts":max(0,min(20,pts)),"notas":notas,
                          "vals":f"P/E:{f['pe'] or 'ND'} EV/EBITDA:{f['ev_ebitda'] or 'ND'}"}

    # Salud Financiera
    pts=10; notas=[]
    if "utility" in sector:
        pts+=3; notas.append("Deuda estructural normal")
    elif f["deuda_total"] and f["ebitda"] and f["ebitda"]>0:
        r=f["deuda_total"]/f["ebitda"]
        if r<0:   pts+=5; notas.append("Caja neta positiva")
        elif r<1: pts+=4; notas.append(f"D/EBITDA {r:.1f}x baja")
        elif r<2: pts+=2; notas.append(f"D/EBITDA {r:.1f}x ok")
        elif r<4: pts-=1; notas.append(f"D/EBITDA {r:.1f}x moderada")
        else:     pts-=3; notas.append(f"D/EBITDA {r:.1f}x alta")
    if f["current_ratio"]:
        if f["current_ratio"]>2:   pts+=3; notas.append(f"Liquidez {f['current_ratio']:.1f} excelente")
        elif f["current_ratio"]>1: pts+=1
        else:                      pts-=2; notas.append("Liquidez ajustada")
    caja_b=round(f["caja"]/1e9,1) if f["caja"] else 0
    notas.append(f"Caja: {caja_b}B")
    cats["Salud Financiera"] = {"pts":max(0,min(20,pts)),"notas":notas,"vals":f"Caja:{caja_b}B"}

    # Rentabilidad
    pts=10; notas=[]
    fcf_ok = (f.get("rev_growth") or 0)>0.30
    if f["fcf"] and f["fcf"]>0:
        pts+=3; notas.append(f"FCF +{round(f['fcf']/1e9,1)}B")
    elif f["fcf"] and f["fcf"]<0 and not fcf_ok:
        pts-=2; notas.append("FCF negativo")
    if f["margen_neto"]:
        mn=f["margen_neto"]*100
        if mn>25:   pts+=4; notas.append(f"Margen {mn:.1f}% excelente")
        elif mn>15: pts+=3; notas.append(f"Margen {mn:.1f}% bueno")
        elif mn>5:  pts+=1; notas.append(f"Margen {mn:.1f}% ok")
        elif mn<0 and not fcf_ok: pts-=2; notas.append(f"Margen {mn:.1f}% negativo")
    mn_txt=f"{f['margen_neto']*100:.1f}%" if f['margen_neto'] else 'ND'
    cats["Rentabilidad"] = {"pts":max(0,min(20,pts)),"notas":notas,"vals":f"Margen:{mn_txt}"}

    # Crecimiento
    pts=10; notas=[]
    if f["rev_growth"]:
        rg=f["rev_growth"]*100
        if rg>50:   pts+=6; notas.append(f"Ingresos +{rg:.0f}% excepcional")
        elif rg>30: pts+=4; notas.append(f"Ingresos +{rg:.0f}% excelente")
        elif rg>15: pts+=2; notas.append(f"Ingresos +{rg:.0f}% bueno")
        elif rg>5:  pts+=1; notas.append(f"Ingresos +{rg:.0f}% moderado")
        elif rg<0 and "utility" not in sector: pts-=3; notas.append(f"Ingresos {rg:.0f}% cayendo")
    eg = (f.get("earn_growth") or 0)
    if eg>1.0 and (f.get("rev_growth") or 0)<0.05: eg=min(eg,0.30)
    if eg:
        if eg*100>50:   pts+=4; notas.append(f"Beneficios +{eg*100:.0f}%")
        elif eg*100>20: pts+=2
        elif eg*100>5:  pts+=1
    rg_txt=f"{f['rev_growth']*100:.0f}%" if f['rev_growth'] else 'ND'
    cats["Crecimiento"] = {"pts":max(0,min(20,pts)),"notas":notas,"vals":f"Rev:{rg_txt}"}

    # Potencial LP
    pts=10; notas=[]
    if f["insider_pct"]:
        ip=f["insider_pct"]*100
        if ip>10: pts+=3; notas.append(f"Insiders {ip:.1f}%")
        elif ip>5: pts+=1
    if f["margen_bruto"]:
        mb=f["margen_bruto"]*100
        if mb>60:  pts+=4; notas.append(f"Margen bruto {mb:.0f}% — moat fuerte")
        elif mb>40: pts+=2; notas.append(f"Margen bruto {mb:.0f}%")
        elif mb>20: pts+=1
    if f["div_yield"] and f["div_yield"]>0.03:
        pts+=2; notas.append(f"Dividendo {f['div_yield']*100:.1f}%")
    if "utility" in sector: pts+=2; notas.append("Monopolio regulado")
    mb_txt=f"{f['margen_bruto']*100:.0f}%" if f['margen_bruto'] else 'ND'
    cats["Potencial LP"] = {"pts":max(0,min(20,pts)),"notas":notas,"vals":f"MB:{mb_txt}"}

    total=sum(c["pts"] for c in cats.values())
    if total>=80:   zona="EXCELENTE"
    elif total>=65: zona="BUENA INVERSIÓN"
    elif total>=50: zona="ACEPTABLE"
    elif total>=35: zona="PRECAUCIÓN"
    else:           zona="EVITAR"

    return {"ticker":ticker,"nombre":f["nombre"],"sector":f["sector"],
            "price":f["price"],"d1":f["d1"],"mktcap":f["mktcap"],
            "score":total,"zona":zona,"cats":cats,"f":f}

def chart_fundamental(res):
    cats=res["cats"]; n=len(cats)
    fig=plt.figure(figsize=(12,10))
    fig.patch.set_facecolor('#0d1117')
    ax=fig.add_axes([0.05,0.52,0.90,0.43],projection='polar')
    ax.set_facecolor('#0d1117')
    theta=np.linspace(np.pi,0,101)
    for i in range(100):
        if i<35:    c='#FF3333'
        elif i<50:  c='#FF7700'
        elif i<65:  c='#FFCC00'
        elif i<80:  c='#99DD00'
        else:       c='#00CC44'
        ax.barh(1,theta[i]-theta[i+1],left=theta[i+1],height=0.45,color=c,edgecolor='none')
    score=res["score"]; angle=np.pi-(score/100*np.pi)
    ax.plot([angle,angle],[0,1.10],color='white',linewidth=6,zorder=5)
    ax.plot(angle,0,'o',color='white',markersize=22,zorder=6)
    ax.plot(angle,0,'o',color='#0d1117',markersize=11,zorder=7)
    ax.set_ylim(0,1.35); ax.set_theta_zero_location('E'); ax.set_theta_direction(1)
    ax.set_thetamin(0); ax.set_thetamax(180)
    ax.set_xticks([np.pi,3*np.pi/4,np.pi/2,np.pi/4,0])
    ax.set_xticklabels(['0\nEVITAR','25','50\nNEUTRAL','75','100\nEXCELENTE'],
                       color='white',fontsize=10,fontweight='bold')
    ax.set_yticks([]); ax.spines['polar'].set_visible(False); ax.grid(False)
    zc='#FF3333' if score<35 else '#FF7700' if score<50 else '#FFCC00' if score<65 else '#99DD00' if score<80 else '#00CC44'
    fig.text(0.5,0.555,f"{score}/100",ha='center',fontsize=32,color='white',fontweight='bold')
    fig.text(0.5,0.510,res["zona"],ha='center',fontsize=12,color=zc,fontweight='bold')
    mktcap_b=round(res['mktcap']/1e9,1) if res.get('mktcap') else 'ND'
    fig.text(0.5,0.975,f"{res['nombre']} ({res['ticker']})",ha='center',fontsize=13,color='white',fontweight='bold')
    fig.text(0.5,0.950,f"{res['price']} USD  |  Cap: {mktcap_b}B  |  {res['sector']}",
             ha='center',fontsize=10,color='#AAAAAA')
    fig.text(0.5,0.408,'─'*60,ha='center',color='#333333',fontsize=8)
    fig.text(0.5,0.400,'CATEGORÍAS (0-20 cada una)',ha='center',fontsize=10,color='#666666',fontweight='bold')
    ax2=fig.add_axes([0.20,0.18,0.65,0.20])
    ax2.set_facecolor('#0d1117'); ax2.axis('off')
    ax2.set_xlim(-7,23); ax2.set_ylim(-0.5,n-0.5)
    for idx,(nom,datos) in enumerate(reversed(list(cats.items()))):
        pts=datos['pts']; y=idx
        ax2.barh(y,20,height=0.7,color='#1a1a2e',zorder=1)
        bc='#FF3333' if pts<7 else '#FF7700' if pts<10 else '#FFCC00' if pts<14 else '#00CC44'
        ax2.barh(y,pts,height=0.7,color=bc,alpha=0.9,zorder=2)
        ax2.text(-0.5,y,nom,va='center',ha='right',color='white',fontsize=11,fontweight='bold')
        nt=" | ".join(datos['notas'][:2]) if datos['notas'] else datos['vals']
        ax2.text(pts+0.4,y,nt[:45],va='center',ha='left',color='#AAAAAA',fontsize=8)
        ax2.text(20.8,y,f"{pts}/20",va='center',ha='left',color=bc,fontsize=11,fontweight='bold')
    buf=io.BytesIO()
    try: plt.tight_layout(pad=1.5)
    except: pass
    plt.savefig(buf,format='png',dpi=130,facecolor='#0d1117',bbox_inches='tight')
    plt.close(); buf.seek(0)
    return buf

@bot.message_handler(commands=["fundamental"])
def cmd_fundamental(msg):
    if not allowed(msg): return
    parts = msg.text.split()
    if len(parts)<2:
        safe_send(msg.chat.id,"Uso: /fundamental TICKER\n\nEjemplos:\n/fundamental TSLA\n/fundamental NVDA\n/fundamental NKE")
        return
    ticker = parts[1].upper()
    m = bot.send_message(msg.chat.id, f"Analizando fundamentales de {ticker}... (10-15s)")
    res = calcular_fundamental(ticker)
    if not res:
        safe_send(msg.chat.id, f"Sin datos para {ticker}.", message_id=m.message_id)
        return
    try:
        chart = chart_fundamental(res)
        bot.delete_message(msg.chat.id, m.message_id)
        bot.send_photo(msg.chat.id, chart)
    except Exception as e:
        log.warning(f"chart_fundamental: {e}")
        lines=[f"{res['nombre']} ({res['ticker']})",f"Precio: {res['price']} USD",
               f"Score: {res['score']}/100 — {res['zona']}\n"]
        for k,v in res['cats'].items(): lines.append(f"{k}: {v['pts']}/20")
        safe_send(msg.chat.id,"\n".join(lines),message_id=m.message_id)
    comp_txt="\n".join([f"{k}: {v['pts']}/20 — {' | '.join(v['notas'][:2])}" for k,v in res['cats'].items()])
    prompt=(f"Análisis fundamental de {res['nombre']} ({ticker}):\nPrecio: {res['price']} USD\n"
            f"Score: {res['score']}/100 — {res['zona']}\nSector: {res['sector']}\n{comp_txt}\n\n"
            f"1. ¿Es buena inversión a largo plazo?\n2. Principal riesgo del sector\n"
            f"3. Ventaja competitiva (moat)\n4. Veredicto con precio objetivo")
    safe_send(msg.chat.id, f"ANÁLISIS IA\n\n{ask_ai(prompt)}")

# ═══ /HALVINGBTC ═════════════════════════════════════════════

def chart_halving():
    import datetime as dt
    try:
        r=requests.get("https://api.binance.com/api/v3/klines",
                      params={"symbol":"BTCUSDT","interval":"1M","limit":200},timeout=12)
        klines=r.json()
        dates=[pd.Timestamp(k[0],unit='ms') for k in klines]
        closes=[float(k[4]) for k in klines]
        highs=[float(k[2]) for k in klines]
        lows=[float(k[3]) for k in klines]
        hist=pd.DataFrame({"Close":closes,"High":highs,"Low":lows},index=dates)
    except:
        hist=pd.DataFrame()

    fig,ax=plt.subplots(figsize=(18,10))
    fig.patch.set_facecolor('#0d1117'); ax.set_facecolor('#0d1117')

    fases=[
        {"ini":"2012-11-01","fin":"2013-12-31","tipo":"bull","label":"Bull 12m"},
        {"ini":"2014-01-01","fin":"2015-01-31","tipo":"bear","label":"Bear 13m"},
        {"ini":"2015-02-01","fin":"2016-07-01","tipo":"rec","label":"Recovery 17m"},
        {"ini":"2016-07-01","fin":"2017-12-31","tipo":"bull","label":"Bull 18m"},
        {"ini":"2018-01-01","fin":"2019-02-28","tipo":"bear","label":"Bear 14m"},
        {"ini":"2019-03-01","fin":"2020-05-01","tipo":"rec","label":"Recovery 14m"},
        {"ini":"2020-05-01","fin":"2021-11-30","tipo":"bull","label":"Bull 19m"},
        {"ini":"2021-12-01","fin":"2022-11-30","tipo":"bear","label":"Bear 12m"},
        {"ini":"2022-12-01","fin":"2024-04-01","tipo":"rec","label":"Recovery 16m"},
        {"ini":"2024-04-01","fin":"2025-10-06","tipo":"bull","label":"Bull 18m (REAL)"},
        {"ini":"2025-10-07","fin":"2026-09-13","tipo":"bear","label":"Bear ← AHORA"},
        {"ini":"2026-09-13","fin":"2027-06-30","tipo":"rec","label":"Recovery (proyec.)"},
        {"ini":"2027-07-01","fin":"2029-06-30","tipo":"bull","label":"Bull (proyec. 2028-29)"},
    ]
    col={"bull":"#0a3a0a","bear":"#3a0a0a","rec":"#0a1a3a"}
    txt={"bull":"#00CC44","bear":"#FF3333","rec":"#4488FF"}
    for f in fases:
        ax.axvspan(pd.Timestamp(f["ini"]),pd.Timestamp(f["fin"]),alpha=0.22,color=col[f["tipo"]])

    if not hist.empty:
        p=hist["Close"]; pl=np.log10(p.clip(lower=0.01))
        ax.plot(p.index,pl,color='white',linewidth=2.5,zorder=5)
        pa=float(p.iloc[-1]); fa=p.index[-1]
        ax.plot(fa,np.log10(pa),'o',color='#00FFFF',markersize=16,zorder=10,
               markeredgecolor='white',markeredgewidth=2.5)
        ax.annotate(f'AHORA\n${pa:,.0f}',xy=(fa,np.log10(pa)),
                   xytext=(fa,np.log10(pa)+0.28),fontsize=10,color='#00FFFF',
                   fontweight='bold',ha='center',
                   bbox=dict(boxstyle='round,pad=0.4',facecolor='#0d1117',edgecolor='#00FFFF',linewidth=2.5,alpha=0.95),
                   arrowprops=dict(arrowstyle='->',color='#00FFFF',lw=2))
        ax.plot(pd.Timestamp("2025-10-06"),np.log10(126080),'*',
               color='#FFD700',markersize=22,zorder=10,markeredgecolor='white',markeredgewidth=1.5)
        ax.annotate('ATH REAL\n$126,080\nOct 2025',xy=(pd.Timestamp("2025-10-06"),np.log10(126080)),
                   xytext=(pd.Timestamp("2025-10-06"),np.log10(126080)+0.22),
                   fontsize=9,color='#FFD700',fontweight='bold',ha='center',
                   bbox=dict(boxstyle='round,pad=0.3',facecolor='#0d1117',edgecolor='#FFD700',linewidth=2,alpha=0.95),
                   arrowprops=dict(arrowstyle='->',color='#FFD700',lw=1.5))
        ax.axhspan(np.log10(58000),np.log10(78000),alpha=0.08,color='#00CC44',zorder=1)
        ax.text(pd.Timestamp("2025-06-01"),np.log10(66000),'ZONA SUELO\n$58K-$78K',
               fontsize=8,color='#00CC44',fontweight='bold',ha='center',va='center',
               bbox=dict(boxstyle='round,pad=0.2',facecolor='#0d1117',edgecolor='#00CC44',alpha=0.7))

    ax.fill_between([pd.Timestamp("2028-03-01"),pd.Timestamp("2029-06-01")],
                   [np.log10(200000),np.log10(200000)],[np.log10(295000),np.log10(295000)],
                   alpha=0.20,color='#FFD700',zorder=2)
    ax.plot(pd.Timestamp("2029-06-01"),np.log10(250000),'^',
           color='#FFD700',markersize=18,zorder=10,markeredgecolor='white',markeredgewidth=1.5)
    ax.annotate('OBJETIVO\n$200K-295K\n(2029 est.)',xy=(pd.Timestamp("2029-06-01"),np.log10(250000)),
               xytext=(pd.Timestamp("2029-06-01"),np.log10(250000)+0.20),
               fontsize=9,color='#FFD700',fontweight='bold',ha='center',
               bbox=dict(boxstyle='round,pad=0.4',facecolor='#0d1117',edgecolor='#FFD700',linewidth=2,alpha=0.95,linestyle='dashed'),
               arrowprops=dict(arrowstyle='->',color='#FFD700',lw=1.5))

    for fecha,label in [("2012-11-28","1st\nNov 2012"),("2016-07-09","2nd\nJul 2016"),
                         ("2020-05-11","3rd\nMay 2020"),("2024-04-19","4th\nApr 2024"),
                         ("2028-03-15","5th\nMar 2028")]:
        ax.axvline(x=pd.Timestamp(fecha),color='#9966FF',linewidth=1.8,linestyle='--',alpha=0.85,zorder=3)
        ax.text(pd.Timestamp(fecha),1.65,label,fontsize=7.5,color='#BB99FF',ha='center',va='bottom',
               fontweight='bold',bbox=dict(boxstyle='round,pad=0.2',facecolor='#0d1117',edgecolor='#9966FF',alpha=0.85))

    for f in fases:
        mid=pd.Timestamp(f["ini"])+(pd.Timestamp(f["fin"])-pd.Timestamp(f["ini"]))/2
        ax.text(mid,5.15,f["label"],fontsize=7.5,color=txt[f["tipo"]],ha='center',va='top',fontweight='bold',alpha=0.9)

    pe=[100,1000,5000,20000,50000,100000,200000,500000]
    ax.set_yticks([np.log10(p) for p in pe])
    ax.set_yticklabels([f'${p:,}' for p in pe],color='#AAAAAA',fontsize=9)
    ax.xaxis.set_major_locator(mdates.YearLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y'))
    ax.tick_params(axis='x',colors='#AAAAAA',labelsize=9)
    ax.set_xlim(pd.Timestamp("2012-01-01"),pd.Timestamp("2030-06-01"))
    ax.set_ylim(1.6,5.55)
    ax.set_title('BITCOIN — CICLO DE 4 AÑOS (HALVINGS) | Datos reales + Proyección 2028-2029',
                fontsize=14,color='white',fontweight='bold',pad=15)
    ax.set_ylabel('Precio USD (escala log)',color='#AAAAAA',fontsize=10)
    ax.grid(axis='y',color='#333333',linestyle='--',alpha=0.4)
    ax.grid(axis='x',color='#222222',linestyle='--',alpha=0.3)
    for spine in ax.spines.values(): spine.set_color('#333333')
    ley=[mpatches.Patch(color='#00CC44',alpha=0.7,label='Bull Phase'),
         mpatches.Patch(color='#FF3333',alpha=0.7,label='Bear Phase'),
         mpatches.Patch(color='#4488FF',alpha=0.7,label='Recovery'),
         mpatches.Patch(color='#9966FF',alpha=0.7,label='Halving'),
         mpatches.Patch(color='#FFD700',alpha=0.9,label='ATH / Objetivo'),
         mpatches.Patch(color='#00FFFF',alpha=0.9,label='Precio actual')]
    ax.legend(handles=ley,loc='upper left',facecolor='#1a1a2e',edgecolor='#444444',
             labelcolor='white',fontsize=9,framealpha=0.95)
    precio_txt=f"${hist['Close'].iloc[-1]:,.0f}" if not hist.empty else "N/D"
    fig.text(0.5,0.01,f"ATH: $126,080 (Oct 2025)  |  Ahora: {precio_txt}  |  Suelo est.: $58K-$78K  |  Objetivo 2029: $200K-$295K  |  5th Halving: Mar 2028",
             ha='center',fontsize=8.5,color='#888888',
             bbox=dict(facecolor='#1a1a2e',alpha=0.6,boxstyle='round'))
    plt.tight_layout(rect=[0,0.04,1,1])
    buf=io.BytesIO()
    plt.savefig(buf,format='png',dpi=130,facecolor='#0d1117',bbox_inches='tight')
    plt.close(); buf.seek(0)
    return buf

@bot.message_handler(commands=["halvingbtc"])
def cmd_halvingbtc(msg):
    if not allowed(msg): return
    m = bot.send_message(msg.chat.id,"Generando ciclo de 4 años de Bitcoin... (15s)")
    try:
        chart=chart_halving()
        import datetime as dt
        meses=(dt.date.today()-dt.date(2024,4,19)).days//30
        try:
            r=requests.get("https://api.binance.com/api/v3/ticker/price",params={"symbol":"BTCUSDT"},timeout=5)
            pa=float(r.json()["price"])
        except: pa=0
        caption=(f"BITCOIN — CICLO DE 4 AÑOS\n\n4th Halving: 19 Abril 2024\n"
                 f"Meses desde halving: {meses}\nPrecio actual: ${pa:,.0f}\n\n"
                 f"ATH real: $126,080 (Oct 2025)\nCaída: ~{round((1-pa/126080)*100) if pa>0 else '?'}%\n\n"
                 f"Suelo estimado: $58K-$78K (¿ya visto?)\nObjetivo 2029: $200K-$295K\n5th Halving: ~Mar 2028")
        bot.delete_message(msg.chat.id,m.message_id)
        bot.send_photo(msg.chat.id,chart,caption=caption[:1020])
        prompt=(f"Ciclo halving BTC: {meses} meses desde 4th halving, precio ${pa:,.0f}\n"
                f"ATH ${126080:,} (Oct 2025), caída ~{round((1-pa/126080)*100) if pa>0 else '?'}%\n\n"
                f"1. ¿Ya vimos el suelo? Argumentos a favor y en contra\n"
                f"2. Diferencias con ciclos anteriores\n3. Proyección realista 2028-2029\n4. Estrategia concreta ahora")
        safe_send(msg.chat.id,f"ANÁLISIS IA — CICLO HALVING\n\n{ask_ai(prompt,2500)}")
    except Exception as e:
        log.error(f"halvingbtc: {e}")
        safe_send(msg.chat.id,f"Error: {e}",message_id=m.message_id)

@bot.message_handler(commands=["start","ayuda"])
def cmd_start(msg):
    if not allowed(msg): return
    safe_send(msg.chat.id,
        "AnalisisPro — Comandos:\n\n"
        "/valor BTC-USD — Índice barato/caro 0-100 para crypto\n"
        "/valor TSLA — Índice para acciones US\n"
        "/valor SAN.MC — Acciones españolas\n\n"
        "/fundamental NVDA — Análisis fundamental 0-100\n"
        "/fundamental NKE — Con velocímetro y IA\n\n"
        "/halvingbtc — Ciclo 4 años Bitcoin con gráfico\n\n"
        "Tickers crypto: BTC-USD ETH-USD SOL-USD XRP-USD\n"
        "Tickers acciones: TSLA NVDA AAPL NKE SAN.MC BMW.DE")

if __name__ == "__main__":
    # FIX 409: si el contenedor anterior no llegó a cerrar su getUpdates a
    # tiempo, esto libera el "lock" de Telegram antes de empezar a hacer
    # polling, en vez de chocar con la sesión previa.
    try:
        bot.remove_webhook()
        time.sleep(1)
    except Exception as e:
        log.warning(f"remove_webhook al arrancar: {e}")
    log.info("AnalisisPro Bot arrancado")
    bot.infinity_polling(timeout=60, long_polling_timeout=60, skip_pending=True)
