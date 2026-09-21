#!/usr/bin/env python3
"""AnalisisPro Bot — Con suscripciones (5€/mes vía NOWPayments) + /valor /fundamental /halvingbtc"""
import os, io, json, re, time, logging, requests, threading
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
from datetime import datetime, timedelta

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)

TELEGRAM_TOKEN  = os.environ["TELEGRAM_TOKEN"]
GROQ_API_KEY    = os.environ["GROQ_API_KEY"]
ALLOWED_USER_ID = int(os.environ.get("ALLOWED_USER_ID", 0))
MADRID = pytz.timezone("Europe/Madrid")

DATA_DIR = os.environ.get("DATA_DIR", ".")
try:
    os.makedirs(DATA_DIR, exist_ok=True)
except Exception as _e:
    log.warning(f"DATA_DIR {DATA_DIR}: {_e}")

def _p(nombre):
    return os.path.join(DATA_DIR, nombre)

bot = telebot.TeleBot(TELEGRAM_TOKEN)
ai  = Groq(api_key=GROQ_API_KEY)

# ═══ SISTEMA DE SUSCRIPCIONES ════════════════════════════════
NOWPAYMENTS_KEY  = os.environ.get("NOWPAYMENTS_API_KEY", "")
WALLET_USDT      = os.environ.get("WALLET_USDT", "")
PRECIO_MENSUAL   = 5  # EUR
TRIAL_DIAS       = 7
SUBS_FILE        = os.environ.get("SUBS_FILE", _p("subscribers.json"))

def _load_subs():
    try:
        with open(SUBS_FILE, "r") as f:
            raw = json.load(f)
        out = {}
        for cid, s in raw.items():
            out[int(cid)] = {
                "activo": s.get("activo", False),
                "expiry": datetime.fromisoformat(s["expiry"]) if s.get("expiry") else None,
                "trial_used": s.get("trial_used", False),
            }
        return out
    except Exception as e:
        log.info(f"_load_subs: sin fichero previo o vacío ({e})")
        return {}

_SUBS_LOCK = threading.Lock()

def _save_subs():
    try:
        with _SUBS_LOCK:
            raw = {}
            for cid, s in list(SUSCRIPTORES.items()):
                raw[str(cid)] = {
                    "activo": s.get("activo", False),
                    "expiry": s["expiry"].isoformat() if s.get("expiry") else None,
                    "trial_used": s.get("trial_used", False),
                }
            tmp = SUBS_FILE + ".tmp"
            with open(tmp, "w") as f:
                json.dump(raw, f)
            os.replace(tmp, SUBS_FILE)  # escritura atómica: nunca deja el JSON a medias
    except Exception as e:
        log.warning(f"_save_subs: {e}")

# {chat_id: {"activo":bool, "expiry":datetime, "trial_used":bool}}
SUSCRIPTORES = _load_subs()

def is_premium(chat_id):
    if chat_id == ALLOWED_USER_ID: return True
    sub = SUSCRIPTORES.get(chat_id, {})
    if not sub.get("activo"): return False
    expiry = sub.get("expiry")
    if expiry and datetime.now() > expiry:
        SUSCRIPTORES[chat_id]["activo"] = False
        _save_subs()
        return False
    return True

def activar(chat_id, dias=30):
    ahora = datetime.now()
    sub = SUSCRIPTORES.get(chat_id, {})
    if sub.get("activo") and sub.get("expiry") and sub["expiry"] > ahora:
        nueva = sub["expiry"] + timedelta(days=dias)
    else:
        nueva = ahora + timedelta(days=dias)
    SUSCRIPTORES[chat_id] = {
        "activo": True, "expiry": nueva,
        "trial_used": sub.get("trial_used", False),
    }
    _save_subs()
    return nueva

def crear_pago():
    try:
        r = requests.post(
            "https://api.nowpayments.io/v1/invoice",
            headers={"x-api-key": NOWPAYMENTS_KEY, "Content-Type": "application/json"},
            json={
                "price_amount": PRECIO_MENSUAL,
                "price_currency": "eur",
                "pay_currency": "usdttrc20",
                "order_id": f"premium_{int(datetime.now().timestamp())}",
                "order_description": "AnalisisPro Premium 1 mes",
            }, timeout=10
        )
        data = r.json()
        return data.get("invoice_url"), data.get("id")
    except Exception as e:
        log.error(f"NOWPayments: {e}")
        return None, None


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

AVISO_DYOR = "⚠️ Información, no asesoramiento financiero. Lee /dyor antes de decidir."

def con_dyor(fn):
    """Tras un comando de análisis, envía en silencio un recordatorio de /dyor.
    No lo envía a quien no tiene acceso (esos reciben solo el aviso de suscripción)."""
    import functools
    @functools.wraps(fn)
    def wrapper(msg):
        fn(msg)
        try:
            if is_premium(msg.from_user.id):
                safe_send(msg.chat.id, AVISO_DYOR, disable_notification=True)
        except Exception as e:
            log.warning(f"aviso dyor: {e}")
    return wrapper

def ask_ai(prompt, max_chars=2500):
    try:
        r = ai.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=[{"role":"system","content":SYSTEM},
                      {"role":"user","content":prompt}],
            max_tokens=1024, temperature=0.7)
        t = (r.choices[0].message.content or "").strip()
        if not t:
            log.warning("ask_ai: respuesta vacía")
            return "IA no disponible."
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
    "DOT-USD":"DOTUSDT","MATIC-USD":"MATICUSDT","POL-USD":"POLUSDT",
    "TRX-USD":"TRXUSDT","LTC-USD":"LTCUSDT","SHIB-USD":"SHIBUSDT",
    "UNI-USD":"UNIUSDT","ATOM-USD":"ATOMUSDT","XLM-USD":"XLMUSDT",
    "NEAR-USD":"NEARUSDT","APT-USD":"APTUSDT","ARB-USD":"ARBUSDT",
    "OP-USD":"OPUSDT","FIL-USD":"FILUSDT","ICP-USD":"ICPUSDT",
    "HBAR-USD":"HBARUSDT","VET-USD":"VETUSDT","ALGO-USD":"ALGOUSDT",
    "SUI-USD":"SUIUSDT","TON-USD":"TONUSDT","INJ-USD":"INJUSDT",
    "RNDR-USD":"RNDRUSDT","PEPE-USD":"PEPEUSDT","WIF-USD":"WIFUSDT",
    "FET-USD":"FETUSDT","TIA-USD":"TIAUSDT","SEI-USD":"SEIUSDT",
    "STX-USD":"STXUSDT","IMX-USD":"IMXUSDT","GRT-USD":"GRTUSDT",
    "AAVE-USD":"AAVEUSDT","MKR-USD":"MKRUSDT","LDO-USD":"LDOUSDT",
    "SAND-USD":"SANDUSDT","MANA-USD":"MANAUSDT","AXS-USD":"AXSUSDT",
    "EOS-USD":"EOSUSDT","XTZ-USD":"XTZUSDT","THETA-USD":"THETAUSDT",
    "FTM-USD":"FTMUSDT","CHZ-USD":"CHZUSDT","ETC-USD":"ETCUSDT",
}
STOOQ_MAP = {
    "^GSPC":"^spx","^IXIC":"^ndx","^GDAXI":"^dax","^IBEX":"^ibex",
    "^FCHI":"^cac","^FTSE":"^ukx","^VIX":"^vix","^TNX":"^tnx",
    "DX-Y.NYB":"usdidx","GC=F":"xauusd","CL=F":"cl.f","SI=F":"xagusd","HG=F":"hg.f",
    "AAPL":"aapl.us","MSFT":"msft.us","NVDA":"nvda.us","TSLA":"tsla.us",
    "AMZN":"amzn.us","GOOGL":"googl.us","META":"meta.us","AMD":"amd.us",
    "INTC":"intc.us","IONQ":"ionq.us","PLTR":"pltr.us","COIN":"coin.us",
    "NKE":"nke.us","DIS":"dis.us","JPM":"jpm.us","BAC":"bac.us",
    "XOM":"xom.us","IBIT":"ibit.us","SPY":"spy.us","QQQ":"qqq.us",
    "GLD":"gld.us","MP":"mp.us","RKLB":"rklb.us","SMCI":"smci.us",
    "NFLX":"nflx.us","UBER":"uber.us","ABNB":"abnb.us",
}

def fetch_binance(symbol, days=220):
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

def fetch_btc_price_history_long(days):
    """Histórico largo de BTC-USD desde Binance, paginando peticiones (la
    API limita a 1000 velas por llamada, así que para pedir años de
    histórico hay que encadenar varias). A diferencia de fetch_binance,
    devuelve también las fechas reales de cada vela — necesario para poder
    alinear correctamente con series de otra fuente (p.ej. Fear & Greed)
    en vez de asumir que "los últimos N días" de una coinciden con los de
    la otra."""
    ck = f"binance_long:BTCUSDT:{days}"
    cached = cache_get(ck)
    if cached is not None: return cached
    all_klines = []
    end_time = None
    remaining = days
    try:
        while remaining > 0:
            limit = min(1000, remaining + 5)
            params = {"symbol": "BTCUSDT", "interval": "1d", "limit": limit}
            if end_time: params["endTime"] = end_time
            r = requests.get("https://api.binance.com/api/v3/klines", params=params, timeout=10)
            if r.status_code != 200:
                log.warning(f"fetch_btc_price_history_long: HTTP {r.status_code}")
                break
            batch = r.json()
            if not batch: break
            all_klines = batch + all_klines
            end_time = batch[0][0] - 1
            remaining -= len(batch)
            if len(batch) < limit: break  # llegamos al inicio del histórico disponible en Binance
            time.sleep(0.2)
        if len(all_klines) < 30:
            return None
        dedup = {k[0]: k for k in all_klines}
        rows = sorted(dedup.values(), key=lambda k: k[0])
        result = {"fechas": [pd.Timestamp(k[0], unit='ms') for k in rows],
                  "closes": pd.Series([float(k[4]) for k in rows])}
        cache_set(ck, result)
        return result
    except Exception as e:
        log.warning(f"fetch_btc_price_history_long: {e}")
        return None

TWELVEDATA_API_KEY = os.environ.get("TWELVEDATA_API_KEY", "")

# Alias para tickers crypto escritos sin sufijo -USD (p.ej. "BTC" -> "BTC-USD")
CRYPTO_ALIAS = {k.split("-")[0]: k for k in BINANCE_MAP}

def normalize_ticker(ticker):
    t = ticker.upper().strip()
    return CRYPTO_ALIAS.get(t, t)

# Twelve Data usa sus propios símbolos para índices/materias primas, distintos
# de los de Yahoo/Stooq (sin el prefijo "^" ni sufijos ".NYB"/"=F").
TWELVEDATA_SYMBOL_MAP = {
    "^GSPC":"SPX","^IXIC":"IXIC","^GDAXI":"DAX","^IBEX":"IBEX",
    "^FCHI":"CAC","^FTSE":"UKX","^VIX":"VIX","^TNX":"TNX",
    "DX-Y.NYB":"DXY","GC=F":"XAU/USD","CL=F":"WTI/USD","SI=F":"XAG/USD",
}

# Límite real de Twelve Data en el plan gratuito: 8 peticiones/minuto. Si
# varios tickers fallan en Stooq a la vez (p.ej. dentro de /ticker o la
# difusión automática, que recorren ~25 activos) y todos caen sobre Twelve
# Data en ráfaga, se revienta ese límite de golpe y se rompe la cadena de
# respaldo para todos a la vez (acaban también fallando en yfinance, que
# está bloqueado desde Railway). Este limitador global espacia las
# llamadas para no pasar nunca de ~7/min. (Se había añadido esto una vez
# ya, pero se perdió al recuperar una versión anterior del archivo.)
_TD_CALL_TIMES = []
_TD_MAX_PER_MIN = 7

def _throttle_twelvedata():
    global _TD_CALL_TIMES
    now = time.time()
    _TD_CALL_TIMES = [t for t in _TD_CALL_TIMES if now - t < 60]
    if len(_TD_CALL_TIMES) >= _TD_MAX_PER_MIN:
        wait = 60 - (now - _TD_CALL_TIMES[0]) + 0.5
        if wait > 0:
            time.sleep(wait)
        now = time.time()
        _TD_CALL_TIMES = [t for t in _TD_CALL_TIMES if now - t < 60]
    _TD_CALL_TIMES.append(time.time())

def fetch_twelvedata(ticker, days=220):
    """Fuente intermedia entre Stooq y yfinance. Usa API key (no scraping),
    así que no sufre los bloqueos por IP compartida que afectan a Yahoo desde
    Railway. Requiere la env var TWELVEDATA_API_KEY (plan gratuito: 800
    peticiones/día, 8/min — https://twelvedata.com/register)."""
    if not TWELVEDATA_API_KEY:
        return None
    sym = TWELVEDATA_SYMBOL_MAP.get(ticker, ticker)
    ck = f"td:{ticker}"
    cached = cache_get(ck)
    if cached is not None: return cached
    def _do():
        _throttle_twelvedata()
        r = requests.get("https://api.twelvedata.com/time_series",
                        params={"symbol":sym,"interval":"1day","outputsize":days,
                                "apikey":TWELVEDATA_API_KEY},timeout=10)
        j = r.json()
        if j.get("status")=="error" or "values" not in j:
            raise RuntimeError(f"Twelve Data: {j.get('message','sin datos')}")
        vals = list(reversed(j["values"]))  # Twelve Data devuelve orden descendente
        if len(vals)<5: raise RuntimeError("Twelve Data: pocos datos")
        c  = pd.Series([float(v["close"]) for v in vals])
        h  = pd.Series([float(v["high"]) for v in vals])
        lo = pd.Series([float(v["low"]) for v in vals])
        vol= pd.Series([float(v.get("volume") or 1e6) for v in vals])
        return build_quote(ticker, c, h, lo, vol)
    res = with_retry(_do, tries=2, base_delay=2, what=f"fetch_twelvedata {ticker}({sym})")
    if res: cache_set(ck, res)
    return res

def fetch_yfinance_fallback(ticker, days=220):
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

def fetch_stooq(ticker, days=220):
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
        log.debug(f"fetch_stooq {ticker}({st}): agotado, probando Twelve Data")
        res = fetch_twelvedata(t, days)
    if res is None:
        log.debug(f"fetch_stooq {ticker}: Twelve Data también agotado, probando yfinance")
        res = fetch_yfinance_fallback(t, days)
    if res: cache_set(ck, res)
    return res

def get_quote(ticker):
    t = normalize_ticker(ticker)
    if t in BINANCE_MAP:
        return fetch_binance(BINANCE_MAP[t])
    if t.endswith("-USD"):
        # Ticker con pinta de cripto pero no está en nuestro mapa fijo:
        # probamos directamente contra Binance (cubre cualquier par listado
        # ahí, no solo los ~50 que tenemos mapeados a mano).
        base = t[:-4]
        dyn = fetch_binance(f"{base}USDT")
        if dyn: return dyn
    if t == "SI=F":
        # Plata en concreto: XAG/USD es un par "tipo forex" muy estándar y
        # fiable en Twelve Data — más fiable ahí que el formato de futuros
        # que usa Stooq para este símbolo, que venía fallando. El oro
        # (GC=F) NO se toca aquí: ya funcionaba bien por Stooq, y meterlo
        # en esta prioridad solo le quitaría cuota de Twelve Data a otros
        # tickers sin necesidad.
        td = fetch_twelvedata(t)
        if td: return td
    res = fetch_stooq(t)
    if res is None and "-" not in t and "." not in t and "^" not in t and "=" not in t and len(t) <= 10:
        # Último recurso: si no parece encontrarse como acción, probamos si
        # es una cripto escrita sin sufijo (p.ej. "PEPE" en vez de PEPE-USD).
        # Excluimos "^" (índices, p.ej. ^GSPC) y "=" (materias primas, p.ej.
        # GC=F): nunca son pares cripto, probarlo solo malgasta una llamada
        # y ensucia el log con un HTTP 400 inevitable.
        dyn = fetch_binance(f"{t}USDT")
        if dyn: return dyn
    return res

def is_crypto_ticker(t):
    """Determina si un ticker (ya normalizado) debe tratarse como cripto:
    está en el mapa fijo, o tiene pinta de par cripto (-USD) y de hecho
    responde en Binance."""
    if t in BINANCE_MAP or t.endswith("-USD"):
        return True
    return False

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
    t = normalize_ticker(ticker)
    es_crypto = is_crypto_ticker(t)
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

        base = t[:-4] if t.endswith("-USD") else t
        sym = BINANCE_MAP.get(t, f"{base}USDT")
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
        vix, nota = None, ""
        if vix_d:
            vix = vix_d["price"]
        else:
            # El VIX real no está disponible (proveedor caído/sin cobertura
            # de índices). Usamos como proxy la volatilidad realizada
            # anualizada de SPY (el ETF que replica el S&P 500) — a
            # diferencia de "^GSPC", SPY es una acción normal y funciona de
            # forma fiable en Stooq/Twelve Data. Importante: NO usamos la
            # volatilidad del propio ticker analizado como último recurso,
            # porque eso mezcla "el mercado está nervioso" con "esta acción
            # en concreto es volátil por naturaleza" (p.ej. TSLA siempre
            # tendría ~10/10 aunque el mercado esté tranquilo).
            spy_d = get_quote("SPY")
            if spy_d:
                rets = spy_d["closes"].pct_change().dropna()
                window = min(20, len(rets))
                if window >= 5:
                    vix = round(float(rets.tail(window).std() * (252**0.5) * 100), 1)
                    nota = " (proxy SPY)"
        if vix is not None:
            if vix>35:   p=10; v=f"{vix} — pánico{nota}"
            elif vix>28: p=8;  v=f"{vix} — miedo{nota}"
            elif vix>22: p=6;  v=f"{vix} — moderado{nota}"
            elif vix>16: p=3;  v=f"{vix} — calma{nota}"
            else:        p=2;  v=f"{vix} — complacencia{nota}"
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
    ax.set_xticklabels(['0\nCARO','25','50\nNEUTRAL','75','100\nBARATO'],
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
    ax2.set_xlim(-3.5,13.5); ax2.set_ylim(-0.5,n-0.5)
    for idx,(nom,datos) in enumerate(reversed(list(comp.items()))):
        pts=datos['p']; val=datos['v']; y=idx
        ax2.barh(y,10,height=0.65,color='#1a1a2e',zorder=1)
        bc='#FF3333' if pts<=3 else '#FF7700' if pts<=5 else '#FFCC00' if pts<=7 else '#00CC44'
        ax2.barh(y,pts,height=0.65,color=bc,alpha=0.9,zorder=2)
        ax2.text(-0.2,y,nom,va='center',ha='right',color='white',fontsize=11,fontweight='bold')
        ax2.text(pts+0.15,y,str(val)[:38],va='center',ha='left',color='#CCCCCC',fontsize=9)
        ax2.text(11.2,y,f"{pts}/10",va='center',ha='left',color=bc,fontsize=11,fontweight='bold')
    buf=io.BytesIO()
    try: plt.tight_layout(pad=1.5)
    except: pass
    plt.savefig(buf,format='png',dpi=130,facecolor='#0d1117',bbox_inches='tight')
    plt.close(); buf.seek(0)
    return buf

@bot.message_handler(commands=["valor"])
@con_dyor
def cmd_valor(msg):
    if not is_premium(msg.from_user.id):
        safe_send(msg.chat.id, "Necesitas suscripción activa.\n\n/trial — 7 días gratis\n/premium — 5€/mes")
        return
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

FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY", "")

def fetch_fundamentales_finnhub(ticker):
    """Fallback cuando yfinance.info falla (bloqueo de Yahoo). Finnhub se
    autentica por API key, no por scraping, así que no sufre el bloqueo por
    IP compartida de Railway. Free tier: ~60 peticiones/min, incluye
    fundamentales básicos. Requiere env var FINNHUB_API_KEY (gratis en
    https://finnhub.io/register). Nota: algunos campos (FCF, caja, deuda,
    insiders) no están en el tier gratuito y quedan como None/ND — el
    scoring ya trata esos casos con normalidad."""
    if not FINNHUB_API_KEY:
        return None
    def _do():
        base = "https://finnhub.io/api/v1"
        p = {"symbol": ticker, "token": FINNHUB_API_KEY}
        rp = requests.get(f"{base}/stock/profile2", params=p, timeout=8).json()
        rq = requests.get(f"{base}/quote", params=p, timeout=8).json()
        rm = requests.get(f"{base}/stock/metric", params={**p,"metric":"all"}, timeout=8).json()
        m = rm.get("metric") or {}
        price = rq.get("c") or 0
        if not price or not rp:
            raise RuntimeError("Finnhub: sin datos fundamentales")
        def pct(*keys):
            for k in keys:
                v = m.get(k)
                if v is not None: return v/100
            return None
        return {
            "nombre": rp.get("name", ticker), "sector": rp.get("finnhubIndustry",""),
            "price": price, "d1": rq.get("dp", 0),
            "mktcap": (rp.get("marketCapitalization") or 0)*1e6,
            "pe": m.get("peExclExtraTTM") or m.get("peBasicExclExtraTTM") or m.get("peTTM"),
            "peg": m.get("pegTTM"),
            "ev_ebitda": m.get("currentEv/freeCashFlowTTM"),
            "margen_neto": pct("netProfitMarginTTM"), "margen_bruto": pct("grossMarginTTM"),
            "roe": pct("roeTTM"), "fcf": None,
            "caja": None, "deuda_total": None,
            "ebitda": None, "rev_growth": pct("revenueGrowthTTMYoy","revenueGrowthQuarterlyYoy"),
            "earn_growth": pct("epsGrowthTTMYoy"), "rev_ttm": None,
            "current_ratio": m.get("currentRatioQuarterly") or m.get("currentRatioAnnual"),
            "insider_pct": None,
            "div_yield": pct("dividendYieldIndicatedAnnual","dividendYieldAnnual"),
        }
    return with_retry(_do, tries=2, base_delay=2, what=f"fetch_fundamentales_finnhub {ticker}")

def fetch_fundamentales(ticker):
    ck = f"fund:{ticker}"
    cached = cache_get(ck)
    if cached is not None: return cached
    def _do():
        import yfinance as yf
        info = yf.Ticker(ticker).info
        if not info or not info.get("regularMarketPrice"):
            raise RuntimeError("sin datos fundamentales")
        dy = info.get("dividendYield")
        if dy and dy>1: dy=dy/100
        return {
            "nombre":info.get("longName",ticker),"sector":info.get("sector",""),
            "price":info.get("regularMarketPrice") or info.get("currentPrice",0),
            "d1":info.get("regularMarketChangePercent",0),
            "mktcap":info.get("marketCap",0),
            "pe":info.get("trailingPE"),"peg":info.get("pegRatio"),
            "ev_ebitda":info.get("enterpriseToEbitda"),
            "margen_neto":info.get("profitMargins"),"margen_bruto":info.get("grossMargins"),
            "roe":info.get("returnOnEquity"),"fcf":info.get("freeCashflow"),
            "caja":info.get("totalCash"),"deuda_total":info.get("totalDebt"),
            "ebitda":info.get("ebitda"),"rev_growth":info.get("revenueGrowth"),
            "earn_growth":info.get("earningsGrowth"),"rev_ttm":info.get("totalRevenue"),
            "current_ratio":info.get("currentRatio"),"insider_pct":info.get("heldPercentInsiders"),
            "div_yield":dy,
        }
    res = with_retry(_do, tries=2, base_delay=2, what=f"fetch_fundamentales {ticker}")
    if res is None:
        log.debug(f"fetch_fundamentales {ticker}: yfinance agotado, probando Finnhub")
        res = fetch_fundamentales_finnhub(ticker)
    if res: cache_set(ck, res)
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
@con_dyor
def cmd_fundamental(msg):
    if not is_premium(msg.from_user.id):
        safe_send(msg.chat.id, "Necesitas suscripción activa.\n\n/trial — 7 días gratis\n/premium — 5€/mes")
        return
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

    _hoy = pd.Timestamp.today().normalize()
    _bear_activo = _hoy <= pd.Timestamp("2026-09-13")
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
        {"ini":"2025-10-07","fin":"2026-09-13","tipo":"bear","label":"Bear ← AHORA" if _bear_activo else "Bear 11m"},
        {"ini":"2026-09-13","fin":"2027-06-30","tipo":"rec","label":"Recovery (proyec.)" if _bear_activo else "Recovery ← AHORA"},
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
@con_dyor
def cmd_halvingbtc(msg):
    if not is_premium(msg.from_user.id):
        safe_send(msg.chat.id, "Necesitas suscripción activa.\n\n/trial — 7 días gratis\n/premium — 5€/mes")
        return
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
    chat_id = msg.from_user.id
    nombre = msg.from_user.first_name or "inversor"
    if is_premium(chat_id):
        safe_send(chat_id,
            f"Bienvenido {nombre}! 👋\n\n"
            "⚠️ Antes de empezar, lee /dyor: esto es información, no asesoramiento financiero.\n\n"
            "/valor BTC-USD — Índice barato/caro 0-100 para crypto\n"
            "/valor TSLA — Índice para acciones US\n"
            "/valor SAN.MC — Acciones españolas\n\n"
            "/fundamental NVDA — Análisis fundamental 0-100\n"
            "/halvingbtc — Ciclo 4 años Bitcoin con gráfico\n"
            "/cartera Buffett — Cartera 13F de grandes inversores\n"
            "/dominancia — Zonas históricas de compra/venta BTC (USDT.D)\n"
            "/ballenas BTC — Muros de órdenes grandes (order book)\n"
            "/noticias — Noticias de bolsa, economía y cripto de varias fuentes\n"
            "/macro — Tipos, inflación, paro (FRED) + derivados cripto (Binance)\n"
            "/ticker — Resumen de mercados al momento (bajo demanda)\n"
            "/ciclo — Fase actual de BTC en el ciclo de mercado\n"
            "/suelo — Triple Suelo de Sentimiento (VIX + AAII + Fear&Greed)\n"
            "/curva — Curva de tipos EEUU (10 años vs 2 años)\n"
            "/insiders TICKER — Compras/ventas de directivos (SEC Form 4)\n"
            "/correlacion — Correlación BTC vs Nasdaq (risk-on/risk-off)\n"
            "/fuerza — Qué criptos aguantan o suben más que BTC (fuerza relativa)\n\n"
            "/guia — Explicación completa de cada comando\n"
            "/dyor — Aviso legal (léelo antes de usar el bot para decidir)\n\n"
            "Además, cada 2h (9-21h) recibes un resumen automático de mercados, "
            "y cada mañana a las 8h un resumen diario con Fear & Greed y noticias destacadas.\n\n"
            "Tickers: casi cualquiera funciona, no hace falta que esté en una lista.\n"
            "Crypto: escribe el símbolo con o sin -USD (BTC, BTC-USD, PEPE...).\n"
            "Acciones internacionales: ticker + sufijo de bolsa (SAN.MC, BMW.DE, VOD.L...).\n\n"
            "/mistatus — Ver tu suscripción\n"
            "/premium — Suscribirte o renovar tu acceso (5€/mes)")
        return
    safe_send(chat_id,
        f"Hola {nombre}! 👋\n\n"
        "AnalisisPro — Bot de análisis financiero con IA\n\n"
        "⚠️ Antes de empezar, lee /dyor: esto es información, no asesoramiento financiero.\n\n"
        "📊 Índice barato/caro con velocímetro\n"
        "🔍 Análisis fundamental 0-100\n"
        "📈 Ciclo Bitcoin con halvings y proyección\n\n"
        "🎁 Prueba 7 días GRATIS → /trial\n"
        f"💳 Suscripción → /premium ({PRECIO_MENSUAL}€/mes)")

@bot.message_handler(commands=["trial"])
def cmd_trial(msg):
    chat_id = msg.from_user.id
    sub = SUSCRIPTORES.get(chat_id, {})
    if sub.get("trial_used"):
        safe_send(chat_id, f"Ya usaste el trial gratuito.\n\nPara continuar: /premium ({PRECIO_MENSUAL}€/mes)")
        return
    if is_premium(chat_id):
        safe_send(chat_id, "Ya tienes acceso activo. Usa /mistatus para ver cuándo expira.")
        return
    expiry = activar(chat_id, dias=TRIAL_DIAS)
    SUSCRIPTORES[chat_id]["trial_used"] = True
    _save_subs()
    nombre = msg.from_user.first_name or "?"
    username = f"@{msg.from_user.username}" if msg.from_user.username else "sin username"
    safe_send(ALLOWED_USER_ID,
        f"🆕 NUEVO TRIAL\nNombre: {nombre}\nUsername: {username}\n"
        f"Chat ID: {chat_id}\nExpira: {expiry.strftime('%d/%m/%Y')}")
    safe_send(chat_id,
        f"✅ TRIAL ACTIVADO — 7 días gratis\n\n"
        f"Expira: {expiry.strftime('%d/%m/%Y')}\n\n"
        "Comandos disponibles:\n"
        "/valor BTC-USD\n/fundamental NVDA\n/halvingbtc\n\n"
        f"Al terminar el trial: /premium ({PRECIO_MENSUAL}€/mes)")

@bot.message_handler(commands=["premium"])
def cmd_premium(msg):
    chat_id = msg.from_user.id
    extra = ""
    if is_premium(chat_id):
        if chat_id == ALLOWED_USER_ID:
            safe_send(chat_id, "Eres el administrador: acceso premium permanente ✅")
            return
        # Trial o suscripción aún activos: se puede pagar ya, los 30 días se
        # suman al final del acceso actual (activar() ya lo hace así).
        expiry = SUSCRIPTORES.get(chat_id, {}).get("expiry")
        if expiry:
            extra = (f"Tu acceso actual expira el {expiry.strftime('%d/%m/%Y')}. "
                     "Si pagas ahora, los 30 días se suman a partir de esa fecha.\n\n")
    if WALLET_USDT:
        importe = importe_para(chat_id)
        if importe is None:
            safe_send(chat_id, "Ahora mismo no puedo asignarte un importe de pago. Contacta al administrador.")
            return
        safe_send(chat_id,
            extra + f"SUSCRIPCIÓN PREMIUM — {PRECIO_MENSUAL}€/mes\n\n"
            f"Envía EXACTAMENTE <code>{importe:.3f}</code> USDT por la red TRC20 (Tron) a:\n"
            f"<code>{WALLET_USDT}</code>\n\n"
            "⚠️ Este importe es solo tuyo: identifica tu pago. Si envías otra cantidad "
            "no se puede verificar automáticamente.\n"
            "⚠️ Solo USDT en red TRC20. Si pagas desde un exchange, suma su comisión "
            "de retirada aparte para que a la wallet lleguen exactamente esos USDT.\n\n"
            f"Tu importe se mantiene {PAGO_TTL_DIAS} días. Después de pagar, envía el hash:\n/verificar HASH",
            parse_mode="HTML")
        return
    enlace, _ = crear_pago()
    if enlace:
        safe_send(chat_id,
            extra + f"SUSCRIPCIÓN PREMIUM — {PRECIO_MENSUAL}€/mes\n\n"
            f"👇 Enlace de pago (USDT TRC20):\n{enlace}\n\n"
            "Tras pagar, avisa al administrador para que active tu acceso.")
    else:
        safe_send(chat_id, "Ahora mismo no puedo generar el pago. Contacta al administrador.")

USDT_TRC20_CONTRACT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"  # contrato oficial de USDT en Tron
TX_MAX_EDAD_H = 72
USED_TX_FILE = os.environ.get("USED_TX_FILE", _p("used_tx.json"))
_USED_TX_LOCK = threading.Lock()

def _load_used_tx():
    try:
        with open(USED_TX_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}

_USED_TX = _load_used_tx()  # {hash: chat_id}

def _save_used_tx():
    try:
        tmp = USED_TX_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(_USED_TX, f)
        os.replace(tmp, USED_TX_FILE)
    except Exception as e:
        log.warning(f"_save_used_tx: {e}")

# ── Importes únicos por usuario ──
# Cada usuario recibe un importe exacto (p.ej. 5.347 USDT). Como el importe
# identifica al pagador, nadie puede reclamar con su cuenta el hash de un
# pago ajeno: la cantidad de esa transacción no coincide con la suya.
PRECIO_USDT = float(os.environ.get("PRECIO_USDT", PRECIO_MENSUAL))
PAGOS_FILE = os.environ.get("PAGOS_PENDIENTES_FILE", _p("pagos_pendientes.json"))
PAGO_TTL_DIAS = 7
_PAGOS_LOCK = threading.Lock()

def _load_pagos():
    try:
        with open(PAGOS_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}

_PAGOS = _load_pagos()  # {str(chat_id): {"k": 1..999, "ts": epoch}}

def _save_pagos():
    try:
        tmp = PAGOS_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(_PAGOS, f)
        os.replace(tmp, PAGOS_FILE)
    except Exception as e:
        log.warning(f"_save_pagos: {e}")

def _purgar_pagos_caducados():
    ahora = time.time()
    for cid in [c for c, v in _PAGOS.items() if ahora - v.get("ts", 0) > PAGO_TTL_DIAS * 86400]:
        del _PAGOS[cid]

def importe_para(chat_id):
    """Importe único en USDT de este usuario. Crea uno si no tiene y renueva
    su caducidad (7 días). Devuelve None solo si los 999 huecos están ocupados."""
    with _PAGOS_LOCK:
        _purgar_pagos_caducados()
        key = str(chat_id)
        if key not in _PAGOS:
            usados = {v["k"] for v in _PAGOS.values()}
            k = abs(int(chat_id)) % 999 + 1
            for _ in range(999):
                if k not in usados:
                    break
                k = k % 999 + 1
            else:
                return None
            _PAGOS[key] = {"k": k, "ts": time.time()}
        else:
            _PAGOS[key]["ts"] = time.time()
        _save_pagos()
        return round(PRECIO_USDT + _PAGOS[key]["k"] / 1000, 3)

def importe_asignado(chat_id):
    """Solo lectura: el importe que se le asignó a este usuario, o None."""
    with _PAGOS_LOCK:
        _purgar_pagos_caducados()
        v = _PAGOS.get(str(chat_id))
        return round(PRECIO_USDT + v["k"] / 1000, 3) if v else None

def liberar_importe(chat_id):
    with _PAGOS_LOCK:
        if _PAGOS.pop(str(chat_id), None) is not None:
            _save_pagos()

def verificar_pago_usdt(tx_hash):
    """Devuelve (ok, cantidad_usdt, motivo)."""
    if not re.fullmatch(r"[0-9a-f]{64}", tx_hash):
        return False, 0.0, "El hash no tiene formato válido (64 caracteres hexadecimales)."
    if not WALLET_USDT:
        return False, 0.0, "El pago manual no está configurado. Contacta al administrador."
    r = requests.get("https://apilist.tronscan.org/api/transaction-info",
                     params={"hash": tx_hash}, timeout=10)
    data = r.json() or {}
    if not data.get("hash"):
        return False, 0.0, "No encuentro esa transacción. Espera unos minutos e inténtalo de nuevo."
    if data.get("contractRet") != "SUCCESS":
        return False, 0.0, "La transacción no fue exitosa en la blockchain."
    if not data.get("confirmed"):
        return False, 0.0, "Transacción no confirmada aún. Espera unos minutos e inténtalo de nuevo."
    ts = data.get("timestamp")
    if ts and time.time() - ts / 1000 > TX_MAX_EDAD_H * 3600:
        return False, 0.0, f"Esa transacción tiene más de {TX_MAX_EDAD_H}h. Contacta al administrador."

    transfers = list(data.get("trc20TransferInfo") or [])
    tti = data.get("tokenTransferInfo")
    if isinstance(tti, dict) and tti:
        transfers.append(tti)

    total = 0.0
    for t in transfers:
        contrato = t.get("contract_address") or t.get("address") or ""
        if contrato != USDT_TRC20_CONTRACT:   # el símbolo "USDT" se puede falsificar; el contrato no
            continue
        if t.get("to_address") != WALLET_USDT:
            continue
        try:
            decimales = int(t.get("decimals") or 6)
            total += float(t.get("amount_str", "0")) / (10 ** decimales)
        except (ValueError, TypeError):
            continue
    if total <= 0:
        log.info(f"verificar {tx_hash[:12]}: sin transferencia USDT a la wallet. "
                 f"Campos recibidos: {list(data.keys())[:25]}")
        return False, 0.0, "No veo un pago en USDT (TRC20) a la dirección indicada en esa transacción."
    return True, total, ""

@bot.message_handler(commands=["verificar"])
def cmd_verificar(msg):
    chat_id = msg.from_user.id
    parts = msg.text.split()
    if len(parts) < 2:
        safe_send(chat_id, "Uso: /verificar HASH_TRANSACCION")
        return
    tx_hash = parts[1].strip().lower()
    with _USED_TX_LOCK:
        if tx_hash in _USED_TX:
            safe_send(chat_id, "Ese hash ya fue utilizado para activar una suscripción.")
            return
    esperado = importe_asignado(chat_id)
    if esperado is None:
        safe_send(chat_id, "Primero usa /premium para obtener tu importe exacto a pagar.")
        return
    try:
        ok, cantidad, motivo = verificar_pago_usdt(tx_hash)
    except Exception as e:
        log.error(f"verificar: {e}")
        safe_send(chat_id, "No pude verificar automáticamente. Contacta al administrador.")
        return
    if not ok:
        safe_send(chat_id, motivo)
        return
    # El importe debe coincidir con el asignado a ESTE usuario (tolerancia: medio milésimo)
    if abs(cantidad - esperado) > 0.0005:
        safe_send(chat_id,
            f"He encontrado tu pago, pero el importe ({cantidad:.4f} USDT) no coincide con el "
            f"que te asigné ({esperado:.3f} USDT).\n\n"
            "Si enviaste otra cantidad o tu exchange descontó comisión, contacta al administrador.")
        return
    with _USED_TX_LOCK:
        if tx_hash in _USED_TX:   # re-comprobación por si dos /verificar llegaron a la vez
            safe_send(chat_id, "Ese hash ya fue utilizado para activar una suscripción.")
            return
        _USED_TX[tx_hash] = chat_id
        _save_used_tx()
    liberar_importe(chat_id)
    expiry = activar(chat_id, dias=30)
    safe_send(chat_id,
        f"✅ PAGO VERIFICADO — {cantidad:.3f} USDT\n\n"
        f"Acceso premium hasta {expiry.strftime('%d/%m/%Y')}\n\n"
        "Comandos: /valor /fundamental /halvingbtc")
    safe_send(ALLOWED_USER_ID,
        f"💰 NUEVO SUSCRIPTOR\nChat ID: {chat_id}\n"
        f"Nombre: {msg.from_user.first_name}\n"
        f"Pago: {cantidad:.3f} USDT\nTX: {tx_hash[:20]}...")

@bot.message_handler(commands=["mistatus"])
def cmd_mistatus(msg):
    chat_id = msg.from_user.id
    if chat_id == ALLOWED_USER_ID:
        activos = sum(1 for s in SUSCRIPTORES.values() if s.get("activo"))
        safe_send(chat_id, f"Eres el administrador.\nSuscriptores activos: {activos}")
        return
    sub = SUSCRIPTORES.get(chat_id)
    if not sub or not sub.get("activo"):
        safe_send(chat_id, f"No tienes suscripción activa.\n\n/trial — 7 días gratis\n/premium — {PRECIO_MENSUAL}€/mes")
        return
    expiry = sub.get("expiry")
    dias = (expiry - datetime.now()).days if expiry else 0
    safe_send(chat_id,
        "ESTADO DE TU SUSCRIPCIÓN\n\n"
        f"Expira: {expiry.strftime('%d/%m/%Y') if expiry else 'N/D'}\n"
        f"Días restantes: {dias}\n\n"
        f"{'⚠️ Renueva pronto con /premium' if dias<5 else '✅ Acceso activo'}")
# ═══ /CARTERA — Carteras de grandes inversores (13F oficial SEC) ═
# Fuente: filings 13F-HR presentados obligatoriamente ante la SEC cada
# trimestre. No dependemos de Dataroma ni de ningún scraper de terceros
# (Dataroma bloquea el acceso automatizado) — vamos directos a la fuente
# oficial y pública.

SEC_USER_AGENT = os.environ.get("SEC_USER_AGENT", "AnalisisProBot/1.0 (contacto: admin@example.com)")
SEC_HEADERS = {"User-Agent": SEC_USER_AGENT}
CIK_CACHE_FILE = os.environ.get("CIK_CACHE_FILE", _p("cik_cache.json"))

def _load_cik_cache():
    try:
        with open(CIK_CACHE_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}

def _save_cik_cache(cache):
    try:
        with open(CIK_CACHE_FILE, "w") as f:
            json.dump(cache, f)
    except Exception as e:
        log.warning(f"_save_cik_cache: {e}")

_CIK_CACHE = _load_cik_cache()

# Alias -> nombre oficial del gestor tal como aparece registrado en la SEC.
# Los 4 marcados fueron verificados manualmente contra EDGAR; el resto se
# resuelve en tiempo real buscando por nombre (ver resolve_cik) — si la
# búsqueda automática falla para alguno, lo ajustamos cuando veamos el log.
INVESTOR_ALIASES = {
    "buffett": "Berkshire Hathaway", "berkshire": "Berkshire Hathaway",              # verificado: CIK 1067983
    "ackman": "Pershing Square Capital Management",                                  # verificado: CIK 1336528
    "pershing": "Pershing Square Capital Management",
    "burry": "Scion Asset Management", "scion": "Scion Asset Management",            # verificado: CIK 1649339 (fondo cerrado nov-2025, último 13F disponible)
    "klarman": "Baupost Group", "baupost": "Baupost Group",                          # verificado: CIK 1061768
    "tepper": "Appaloosa Management LP", "appaloosa": "Appaloosa Management LP",
    "icahn": "Icahn Enterprises",
    "druckenmiller": "Duquesne Family Office", "duquesne": "Duquesne Family Office",
    "marks": "Oaktree Capital Management", "oaktree": "Oaktree Capital Management",
    "simons": "Renaissance Technologies", "renaissance": "Renaissance Technologies",
    "soros": "Soros Fund Management",
    "dalio": "Bridgewater Associates", "bridgewater": "Bridgewater Associates",
    "pabrai": "Pabrai Investment Funds",
    "lilu": "Himalaya Capital Management", "himalaya": "Himalaya Capital Management",
    "greenblatt": "Gotham Asset Management", "gotham": "Gotham Asset Management",
    "watsa": "Fairfax Financial Holdings", "fairfax": "Fairfax Financial Holdings",
    "akre": "Akre Capital Management",
    "coleman": "Tiger Global Management", "tiger": "Tiger Global Management",
    "calpers": "California Public Employees Retirement System",
}

def resolve_cik(query_name):
    """Busca el CIK (código de identificación en la SEC) de un gestor
    institucional por nombre, usando el buscador oficial de EDGAR. El
    resultado se cachea en disco (CIK_CACHE_FILE) para no repetir la
    búsqueda en cada consulta."""
    key = query_name.lower()
    if key in _CIK_CACHE:
        return _CIK_CACHE[key]
    try:
        r = requests.get("https://www.sec.gov/cgi-bin/browse-edgar",
                        params={"action":"getcompany","company":query_name,
                                "type":"13F-HR","dateb":"","owner":"include",
                                "count":"10","output":"atom"},
                        headers=SEC_HEADERS, timeout=10)
        m = re.search(r"CIK=(\d{4,10})", r.text) or re.search(r"<cik>(\d+)</cik>", r.text, re.IGNORECASE)
        if m:
            cik = m.group(1).zfill(10)
            _CIK_CACHE[key] = cik
            _save_cik_cache(_CIK_CACHE)
            return cik
    except Exception as e:
        log.warning(f"resolve_cik {query_name}: {e}")
    return None

def fetch_13f_filings_list(cik):
    """Lista de filings 13F-HR de un CIK (más reciente primero)."""
    try:
        r = requests.get(f"https://data.sec.gov/submissions/CIK{cik}.json",
                        headers=SEC_HEADERS, timeout=10)
        recent = r.json().get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        accns = recent.get("accessionNumber", [])
        dates = recent.get("filingDate", [])
        out = [{"accession": accns[i], "date": dates[i]}
               for i, f in enumerate(forms) if f == "13F-HR"]
        return out
    except Exception as e:
        log.warning(f"fetch_13f_filings_list {cik}: {e}")
        return []

def _parse_infotable_xml(xml_text):
    rows = []
    for block in re.findall(r"<infoTable>(.*?)</infoTable>", xml_text, re.DOTALL | re.IGNORECASE):
        def grab(tag):
            m = re.search(fr"<{tag}>(.*?)</{tag}>", block, re.DOTALL | re.IGNORECASE)
            return m.group(1).strip() if m else ""
        nombre = grab("nameOfIssuer")
        cusip = grab("cusip")
        try: valor = float(grab("value") or 0)
        except: valor = 0
        try: shares = float(grab("sshPrnamt") or 0)
        except: shares = 0
        rows.append({"nombre": nombre, "cusip": cusip, "valor": valor, "shares": shares})
    return rows

def fetch_13f_holdings(cik, accession):
    """Descarga y parsea la tabla de posiciones de un 13F-HR concreto.
    El fichero XML con las posiciones no siempre se llama igual según quién
    presenta el filing, así que buscamos dinámicamente entre los XML del
    filing cuál contiene <infoTable> en vez de asumir un nombre fijo."""
    accn_nodash = accession.replace("-", "")
    try:
        idx = requests.get(
            f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accn_nodash}/index.json",
            headers=SEC_HEADERS, timeout=10).json()
        items = idx.get("directory", {}).get("item", [])
        for it in items:
            name = it.get("name", "")
            if not name.lower().endswith(".xml"):
                continue
            url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accn_nodash}/{name}"
            xr = requests.get(url, headers=SEC_HEADERS, timeout=10)
            if "<infoTable>" in xr.text:
                return _parse_infotable_xml(xr.text)
    except Exception as e:
        log.warning(f"fetch_13f_holdings {cik}/{accession}: {e}")
    return None

def _agregar_por_cusip(holdings):
    """Suma en una sola posición todas las líneas del 13F que comparten el
    mismo CUSIP. Es habitual que un gestor grande (p.ej. Berkshire, con
    varias filiales — GEICO, National Indemnity...) declare la misma acción
    en varias líneas separadas; sin esto, la misma empresa aparecería
    repetida y con el % de cartera repartido de forma engañosa."""
    agrupado = {}
    for h in holdings:
        if not h["cusip"]:
            continue
        if h["cusip"] not in agrupado:
            agrupado[h["cusip"]] = {"nombre": h["nombre"], "cusip": h["cusip"], "valor": 0, "shares": 0}
        agrupado[h["cusip"]]["valor"] += h["valor"]
        agrupado[h["cusip"]]["shares"] += h["shares"]
    return list(agrupado.values())

def calcular_cartera(query):
    key = query.lower().strip()
    full_name = INVESTOR_ALIASES.get(key, query)
    cik = resolve_cik(full_name)
    if not cik:
        return None
    filings = fetch_13f_filings_list(cik)
    if not filings:
        return None
    actual_raw = fetch_13f_holdings(cik, filings[0]["accession"])
    if not actual_raw:
        return None
    anterior_raw = fetch_13f_holdings(cik, filings[1]["accession"]) if len(filings) > 1 else []
    actual = _agregar_por_cusip(actual_raw)
    anterior = _agregar_por_cusip(anterior_raw or [])
    prev_by_cusip = {h["cusip"]: h for h in anterior}
    actual_by_cusip = {h["cusip"]: h for h in actual}

    total_valor = sum(h["valor"] for h in actual)
    top = sorted(actual, key=lambda h: -h["valor"])[:10]

    nuevas, aumentadas, reducidas, cerradas = [], [], [], []
    for cusip, h in actual_by_cusip.items():
        prev = prev_by_cusip.get(cusip)
        if not prev:
            nuevas.append(h)
        elif h["shares"] > prev["shares"] * 1.05:
            aumentadas.append(h)
        elif h["shares"] < prev["shares"] * 0.95:
            reducidas.append(h)
    for cusip, h in prev_by_cusip.items():
        if cusip not in actual_by_cusip:
            cerradas.append(h)

    return {"gestor": full_name, "fecha": filings[0]["date"],
            "total_valor": total_valor, "n_posiciones": len(actual),
            "top": top, "nuevas": nuevas, "aumentadas": aumentadas,
            "reducidas": reducidas, "cerradas": cerradas}

@bot.message_handler(commands=["cartera"])
@con_dyor
def cmd_cartera(msg):
    if not is_premium(msg.from_user.id):
        safe_send(msg.chat.id, "Necesitas suscripción activa.\n\n/trial — 7 días gratis\n/premium — 5€/mes")
        return
    parts = msg.text.split(maxsplit=1)
    if len(parts) < 2:
        safe_send(msg.chat.id,
            "Uso: /cartera NOMBRE\n\n"
            "Disponibles: Buffett, Ackman, Burry, Klarman, Tepper, Icahn, "
            "Druckenmiller, Marks, Simons, Soros, Dalio, Pabrai, LiLu, "
            "Greenblatt, Watsa, Akre, Coleman, CalPERS\n\n"
            "Datos oficiales de la SEC (13F), trimestrales.")
        return
    query = parts[1]
    m = bot.send_message(msg.chat.id, f"Consultando cartera de {query} en la SEC... (10-20s)")
    res = calcular_cartera(query)
    if not res:
        safe_send(msg.chat.id,
            f"No he encontrado datos de 13F para \"{query}\". Prueba con otro nombre de la lista de /cartera.",
            message_id=m.message_id)
        return
    total_b = round(res["total_valor"]/1e9, 2)
    lines = [f"CARTERA — {res['gestor']}",
             f"13F al {res['fecha']} | Valor total: ${total_b}B | {res['n_posiciones']} posiciones\n",
             "TOP 10 POSICIONES:"]
    for h in res["top"]:
        pct = h["valor"]/res["total_valor"]*100 if res["total_valor"] else 0
        lines.append(f"• {h['nombre']} — {pct:.1f}% — ${round(h['valor']/1e6,1)}M")
    if res["nuevas"]:
        lines.append("\n🆕 NUEVAS POSICIONES:")
        for h in res["nuevas"][:8]: lines.append(f"• {h['nombre']}")
    if res["aumentadas"]:
        lines.append("\n📈 AUMENTADAS:")
        for h in res["aumentadas"][:8]: lines.append(f"• {h['nombre']}")
    if res["reducidas"]:
        lines.append("\n📉 REDUCIDAS:")
        for h in res["reducidas"][:8]: lines.append(f"• {h['nombre']}")
    if res["cerradas"]:
        lines.append("\n❌ POSICIONES CERRADAS:")
        for h in res["cerradas"][:8]: lines.append(f"• {h['nombre']}")
    safe_send(msg.chat.id, "\n".join(lines)[:4096], message_id=m.message_id)

    prompt = (f"Cartera 13F de {res['gestor']} al {res['fecha']}, valor ${total_b}B, {res['n_posiciones']} posiciones.\n"
              f"Top 3: {', '.join(h['nombre'] for h in res['top'][:3])}\n"
              f"Nuevas: {', '.join(h['nombre'] for h in res['nuevas'][:5]) or 'ninguna'}\n"
              f"Cerradas: {', '.join(h['nombre'] for h in res['cerradas'][:5]) or 'ninguna'}\n\n"
              "1. ¿Qué tesis de inversión revela este movimiento?\n2. ¿Qué sector está ganando/perdiendo peso?\n"
              "3. ¿Vale la pena replicar alguna de estas posiciones?")
    safe_send(msg.chat.id, f"ANÁLISIS IA\n\n{ask_ai(prompt)}")

@bot.message_handler(commands=["activar"])
def cmd_activar(msg):
    if msg.from_user.id != ALLOWED_USER_ID: return
    parts = msg.text.split()
    if len(parts) < 2:
        safe_send(msg.chat.id, "Uso: /activar CHAT_ID [dias]\nEj: /activar 123456789 30")
        return
    try:
        target = int(parts[1])
        dias = int(parts[2]) if len(parts) > 2 else 30
        expiry = activar(target, dias=dias)
        safe_send(msg.chat.id, f"✅ Activado {target} hasta {expiry.strftime('%d/%m/%Y')}")
        safe_send(target, f"✅ Acceso premium activado hasta {expiry.strftime('%d/%m/%Y')}\n\n/valor /fundamental /halvingbtc")
    except Exception as e:
        safe_send(msg.chat.id, f"Error: {e}")

@bot.message_handler(commands=["suscriptores"])
def cmd_suscriptores(msg):
    if msg.from_user.id != ALLOWED_USER_ID: return
    activos = [(cid, s) for cid, s in SUSCRIPTORES.items() if s.get("activo")]
    if not activos:
        safe_send(msg.chat.id, "Sin suscriptores activos.")
        return
    lines = [f"SUSCRIPTORES ACTIVOS: {len(activos)}\n"]
    for cid, s in activos:
        expiry = s.get("expiry")
        dias = (expiry - datetime.now()).days if expiry else 0
        lines.append(f"ID: {cid} — {dias} días restantes")
    safe_send(msg.chat.id, "\n".join(lines))

# ═══ /DOMINANCIA — Zonas históricas de compra/venta de BTC ══════
# Basado en el Fear & Greed Index de alternative.me: miedo extremo =
# históricamente buena zona de compra, codicia extrema = históricamente
# buena zona de venta. Gratis, sin API key, sin límite de peticiones,
# histórico completo desde 2018 — no dependemos de ningún plan de pago.
# (Se intentó primero con la dominancia real de USDT vía CoinGecko/CoinCap,
# pero ambos requieren plan de pago para histórico o agotan el free tier
# en la primera consulta — ver conversación.)

FEARGREED_CACHE_FILE = os.environ.get("FEARGREED_CACHE_FILE", _p("feargreed_cache.json"))
FEARGREED_CACHE_HOURS = 12  # el índice solo se actualiza una vez al día, no hace falta más

def _load_feargreed_cache():
    try:
        with open(FEARGREED_CACHE_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return None

def _save_feargreed_cache(data):
    try:
        with open(FEARGREED_CACHE_FILE, "w") as f:
            json.dump(data, f)
    except Exception as e:
        log.warning(f"_save_feargreed_cache: {e}")

def fetch_feargreed_history():
    """Histórico completo del Fear & Greed Index (alternative.me), day a
    día desde 2018. Se cachea en disco para no repetir la descarga completa
    en cada consulta."""
    cache = _load_feargreed_cache()
    if cache and (time.time() - cache.get("_fetched_at", 0)) < FEARGREED_CACHE_HOURS * 3600:
        return cache["series"]
    try:
        r = requests.get("https://api.alternative.me/fng/",
                        params={"limit": "0", "format": "json"}, timeout=15)
        data = r.json().get("data", [])
        series = [{"t": int(p["timestamp"]) * 1000,
                   "value": int(p["value"]),
                   "clase": p.get("value_classification", "")}
                  for p in data]
        series.sort(key=lambda x: x["t"])
        if series:
            _save_feargreed_cache({"_fetched_at": time.time(), "series": series})
            return series
    except Exception as e:
        log.warning(f"fetch_feargreed_history: {e}")
    return cache["series"] if cache else None

# Umbrales estándar del índice (los mismos que usa alternative.me para
# clasificar "Extreme Fear" / "Extreme Greed").
FEARGREED_COMPRA_UMBRAL = 25
FEARGREED_VENTA_UMBRAL = 75

def _runs_min_length(bools, min_len=3):
    """Tramos contiguos donde bools es True y duran al menos min_len puntos
    — evita pintar parpadeos de un solo día en el gráfico (ruido visual),
    sin tocar la clasificación numérica real (esa usa el valor diario tal
    cual, esto solo afecta a qué se sombrea)."""
    runs, i, n = [], 0, len(bools)
    while i < n:
        if bools[i]:
            j = i
            while j < n and bools[j]:
                j += 1
            if j - i >= min_len:
                runs.append((i, j))
            i = j
        else:
            i += 1
    return runs

def chart_feargreed(series, btc_data):
    fechas = [pd.Timestamp(p["t"], unit="ms") for p in series]
    valores = [p["value"] for p in series]
    margen = (fechas[-1] - fechas[0]) * 0.05

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 10), sharex=True,
                                    gridspec_kw={"height_ratios": [2, 1]})
    fig.patch.set_facecolor('#0d1117')
    for ax in (ax1, ax2): ax.set_facecolor('#0d1117')

    if btc_data is not None and btc_data.get("fechas"):
        ax1.plot(btc_data["fechas"], np.log10(btc_data["closes"]), color='white', linewidth=1.6, zorder=5)
    compra_bools = [v <= FEARGREED_COMPRA_UMBRAL for v in valores]
    venta_bools = [v >= FEARGREED_VENTA_UMBRAL for v in valores]
    for i, j in _runs_min_length(compra_bools, min_len=3):
        ax1.axvspan(fechas[i], fechas[j-1], color='#00CC44', alpha=0.18, zorder=0)
    for i, j in _runs_min_length(venta_bools, min_len=3):
        ax1.axvspan(fechas[i], fechas[j-1], color='#FF3333', alpha=0.18, zorder=0)
    ax1.set_title('BTC — Zonas históricas de compra/venta (Fear & Greed Index)', color='white', fontsize=13, fontweight='bold')
    ax1.set_ylabel('Precio BTC (log)', color='#AAAAAA')
    ax1.tick_params(colors='#AAAAAA')
    for spine in ax1.spines.values(): spine.set_color('#333333')
    ax1.grid(color='#222222', linestyle='--', alpha=0.3)

    ax2.plot(fechas, valores, color='#4488FF', linewidth=1.2, zorder=5)
    ax2.axhline(FEARGREED_COMPRA_UMBRAL, color='#00CC44', linestyle='--', linewidth=1.2)
    ax2.axhline(FEARGREED_VENTA_UMBRAL, color='#FF3333', linestyle='--', linewidth=1.2)
    for i, j in _runs_min_length(compra_bools, min_len=3):
        ax2.axvspan(fechas[i], fechas[j-1], color='#00CC44', alpha=0.15, zorder=0)
    for i, j in _runs_min_length(venta_bools, min_len=3):
        ax2.axvspan(fechas[i], fechas[j-1], color='#FF3333', alpha=0.15, zorder=0)
    ax2.plot(fechas[-1], valores[-1], 'o', color='#00FFFF', markersize=12, zorder=10,
              markeredgecolor='white', markeredgewidth=2)
    ax2.annotate(f'AHORA: {valores[-1]}', xy=(fechas[-1], valores[-1]),
                 xytext=(fechas[-1] + margen*0.15, min(90, valores[-1] + 10)),
                 fontsize=12, color='#00FFFF', fontweight='bold', ha='left', clip_on=False,
                 bbox=dict(boxstyle='round,pad=0.35', facecolor='#0d1117', edgecolor='#00FFFF', alpha=0.95))
    ax2.set_xlim(fechas[0], fechas[-1] + margen)
    ax2.set_ylim(0, 100)
    ax2.set_ylabel('Fear & Greed Index', color='#AAAAAA')
    ax2.tick_params(colors='#AAAAAA')
    for spine in ax2.spines.values(): spine.set_color('#333333')
    ax2.grid(color='#222222', linestyle='--', alpha=0.3)
    ax2.xaxis.set_major_formatter(mdates.DateFormatter('%b %Y'))

    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=130, facecolor='#0d1117', bbox_inches='tight')
    plt.close()
    buf.seek(0)
    return buf

@bot.message_handler(commands=["dominancia"])
@con_dyor
def cmd_dominancia(msg):
    if not is_premium(msg.from_user.id):
        safe_send(msg.chat.id, "Necesitas suscripción activa.\n\n/trial — 7 días gratis\n/premium — 5€/mes")
        return
    m = bot.send_message(msg.chat.id, "Calculando zonas de compra/venta BTC... (10-20s)")
    series = fetch_feargreed_history()
    if not series or len(series) < 30:
        safe_send(msg.chat.id,
            "No he podido obtener el histórico de Fear & Greed. Vuelve a intentarlo en unos minutos.",
            message_id=m.message_id)
        return
    dias_cubiertos = max(1, int((series[-1]["t"] - series[0]["t"]) / 86400000))
    btc_data = fetch_btc_price_history_long(dias_cubiertos + 10)

    try:
        chart = chart_feargreed(series, btc_data)
        bot.delete_message(msg.chat.id, m.message_id)
        bot.send_photo(msg.chat.id, chart)
    except Exception as e:
        log.warning(f"chart_feargreed: {e}")
        safe_send(msg.chat.id, "Tengo los datos pero falló al dibujar el gráfico. Reintenta en un momento.",
                  message_id=m.message_id)
        return

    valor_actual = series[-1]["value"]
    clase_actual = series[-1]["clase"]
    if valor_actual <= FEARGREED_COMPRA_UMBRAL:
        zona = "ZONA DE COMPRA — miedo extremo"
    elif valor_actual >= FEARGREED_VENTA_UMBRAL:
        zona = "ZONA DE VENTA — codicia extrema"
    else:
        zona = "NEUTRAL"
    safe_send(msg.chat.id,
        f"FEAR & GREED — {valor_actual}/100 ({clase_actual})\n\n"
        f"Zona de compra (histórico): índice ≤ {FEARGREED_COMPRA_UMBRAL}\n"
        f"Zona de venta (histórico): índice ≥ {FEARGREED_VENTA_UMBRAL}\n"
        f"Estado actual: {zona}\n\n"
        f"Basado en {len(series)} días de histórico (~{dias_cubiertos} días de rango).")

    prompt = (f"Fear & Greed Index actual: {valor_actual}/100 ({clase_actual}). "
              f"Zona de compra histórica: ≤{FEARGREED_COMPRA_UMBRAL}. Zona de venta histórica: ≥{FEARGREED_VENTA_UMBRAL}. "
              f"Estado: {zona}.\n\n"
              "1. ¿Qué nos dice esto sobre el sentimiento del mercado ahora mismo?\n"
              "2. ¿Es fiable esta señal por sí sola o hay que combinarla con algo más?\n"
              "3. Estrategia concreta dado este nivel de sentimiento")
    safe_send(msg.chat.id, f"ANÁLISIS IA\n\n{ask_ai(prompt)}")

# ═══ /BALLENAS — Muros de órdenes grandes (order book Binance) ══
# Inspirado en el "Whale Order Analysis" de Coinglass, pero con datos
# gratuitos y públicos de Binance (sin API key). LIMITACIÓN real a tener en
# cuenta: la API pública de Binance solo da una FOTO del libro de órdenes
# en este momento — a diferencia de Coinglass, no sabemos cuánto tiempo
# lleva puesta cada orden (su columna "3D 12H", "220D 13H"...). Solo
# mostramos tamaño y precio, no antigüedad.

def _cluster_levels(levels, cluster_pct=0.001):
    """Agrupa niveles de precio muy próximos entre sí (dentro de
    cluster_pct, 0.001 = 0.1%) en un único 'muro', sumando su valor. Sin
    esto, el libro de órdenes tiene muchos niveles casi pegados que se
    contarían como muros distintos y sus etiquetas se pisarían en el
    gráfico."""
    if not levels:
        return []
    levels = sorted(levels, key=lambda x: x[0])
    clusters = []
    precio_ref, usd_acum, qty_acum, precio_pond = levels[0][0], levels[0][2], levels[0][1], levels[0][0]*levels[0][2]
    for p, q, usd in levels[1:]:
        if abs(p - precio_ref) / precio_ref <= cluster_pct:
            usd_acum += usd
            qty_acum += q
            precio_pond += p * usd
            precio_ref = precio_pond / usd_acum if usd_acum else p
        else:
            clusters.append((precio_ref, qty_acum, usd_acum))
            precio_ref, usd_acum, qty_acum, precio_pond = p, usd, q, p*usd
    clusters.append((precio_ref, qty_acum, usd_acum))
    return clusters

def fetch_orderbook_walls(symbol, top_n=6, price_range_pct=0.15):
    """Descarga el libro de órdenes de Binance y devuelve los muros de
    compra/venta más grandes dentro de un rango de precio alrededor del
    precio actual (por defecto ±15%)."""
    try:
        r = requests.get("https://api.binance.com/api/v3/depth",
                        params={"symbol": symbol, "limit": 1000}, timeout=8)
        if r.status_code != 200:
            log.warning(f"fetch_orderbook_walls {symbol}: HTTP {r.status_code}")
            return None
        data = r.json()
        bids = [(float(p), float(q)) for p, q in data.get("bids", [])]
        asks = [(float(p), float(q)) for p, q in data.get("asks", [])]
        if not bids or not asks:
            return None
        mid_price = (bids[0][0] + asks[0][0]) / 2
        lo, hi = mid_price * (1 - price_range_pct), mid_price * (1 + price_range_pct)
        bid_raw = [(p, q, p*q) for p, q in bids if lo <= p <= hi]
        ask_raw = [(p, q, p*q) for p, q in asks if lo <= p <= hi]
        bid_walls = sorted(_cluster_levels(bid_raw), key=lambda x: -x[2])[:top_n]
        ask_walls = sorted(_cluster_levels(ask_raw), key=lambda x: -x[2])[:top_n]
        return {"mid": mid_price, "bids": bid_walls, "asks": ask_walls}
    except Exception as e:
        log.warning(f"fetch_orderbook_walls {symbol}: {e}")
        return None

def fetch_binance_intraday(symbol, interval="15m", limit=100):
    try:
        r = requests.get("https://api.binance.com/api/v3/klines",
                        params={"symbol": symbol, "interval": interval, "limit": limit}, timeout=8)
        if r.status_code != 200: return None
        kl = r.json()
        if len(kl) < 5: return None
        return {"fechas": [pd.Timestamp(k[0], unit='ms') for k in kl],
                "closes": pd.Series([float(k[4]) for k in kl])}
    except Exception as e:
        log.warning(f"fetch_binance_intraday {symbol}: {e}")
        return None

def _espaciar_etiquetas(precios, min_gap):
    """Dada una lista de precios (ascendente), devuelve las posiciones Y
    donde colocar cada etiqueta de texto, separadas al menos min_gap entre
    sí, para que no se pisen cuando varios muros están muy cerca en precio."""
    precios = sorted(precios)
    ys = [precios[0]]
    for p in precios[1:]:
        ys.append(max(p, ys[-1] + min_gap))
    return dict(zip(precios, ys))

def chart_ballenas(ticker, walls, intraday):
    # REDISEÑO: en vez de dibujar las etiquetas "fuera" del área de fechas
    # (con transformaciones de eje que varias veces se han recortado mal
    # al guardar la imagen), usamos un panel de texto COMPLETAMENTE APARTE
    # a la derecha — el mismo patrón que ya funciona bien en /valor y
    # /fundamental, que nunca han tenido este problema de recortes.
    fig = plt.figure(figsize=(14, 9))
    fig.patch.set_facecolor('#0d1117')
    ax = fig.add_axes([0.06, 0.11, 0.58, 0.80])   # gráfico de precio (izquierda)
    ax2 = fig.add_axes([0.68, 0.11, 0.30, 0.80])  # panel de etiquetas (derecha)
    ax.set_facecolor('#0d1117')
    ax2.set_facecolor('#0d1117')
    for spine in ('top', 'right', 'bottom'): ax2.spines[spine].set_visible(False)
    ax2.spines['left'].set_color('#333333')
    ax2.tick_params(axis='y', colors='#AAAAAA', left=True, labelleft=True)
    ax2.tick_params(axis='x', bottom=False, labelbottom=False)

    if intraday is not None:
        ax.plot(intraday["fechas"], intraday["closes"], color='white', linewidth=1.5, zorder=6)
        ax.set_xlim(intraday["fechas"][0], intraday["fechas"][-1])

    todos_usd = [w[2] for w in walls["bids"] + walls["asks"]] or [1]
    max_usd = max(todos_usd)
    todos_precios = [w[0] for w in walls["bids"] + walls["asks"]]
    p_min, p_max = min(todos_precios), max(todos_precios)
    min_gap = (p_max - p_min or walls["mid"]*0.01) * 0.09

    # Repartimos las etiquetas de los muros para que no se pisen entre sí.
    precios_combinados = sorted(set(
        [p for p, q, usd in walls["asks"]] + [p for p, q, usd in walls["bids"]]
    ))
    ys_combinado = _espaciar_etiquetas(precios_combinados, min_gap)
    ask_ys = {p: ys_combinado[p] for p, q, usd in walls["asks"]}
    bid_ys = {p: ys_combinado[p] for p, q, usd in walls["bids"]}

    for p, q, usd in walls["asks"]:
        alpha = 0.25 + 0.55 * (usd / max_usd)
        ax.axhspan(p*0.998, p*1.002, color='#FF3333', alpha=alpha, zorder=1)
    for p, q, usd in walls["bids"]:
        alpha = 0.25 + 0.55 * (usd / max_usd)
        ax.axhspan(p*0.998, p*1.002, color='#00CC44', alpha=alpha, zorder=1)
    ax.axhline(walls["mid"], color='#00FFFF', linestyle='--', linewidth=1.2, zorder=5)

    # "AHORA" va DENTRO del propio gráfico de precio, sobre el último punto
    # de la línea — así nunca compite por espacio con las etiquetas de los
    # muros en el panel de la derecha.
    if intraday is not None:
        x_ahora = intraday["fechas"][-1]
        y_offset = (p_max - p_min or walls["mid"]*0.01) * 0.05
        ax.plot(x_ahora, walls["mid"], 'o', color='#00FFFF', markersize=10, zorder=10,
                markeredgecolor='white', markeredgewidth=1.5)
        ax.text(x_ahora, walls["mid"] + y_offset, f"AHORA ${walls['mid']:,.1f}",
                color='#0d1117', fontsize=10, fontweight='bold', ha='right', va='bottom', zorder=11,
                bbox=dict(boxstyle='round,pad=0.3', facecolor='#00FFFF', edgecolor='none', alpha=0.95))

    ax.set_title(f'{ticker} — Muros de órdenes grandes (order book Binance)',
                 color='white', fontsize=13, fontweight='bold', loc='left')
    ax.set_ylabel('Precio USD', color='#AAAAAA')
    ax.tick_params(colors='#AAAAAA')
    for spine in ax.spines.values(): spine.set_color('#333333')
    ax.grid(color='#222222', linestyle='--', alpha=0.3)
    if intraday is not None:
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%d/%m %H:%M'))
        fig.autofmt_xdate()

    # El panel de etiquetas usa el MISMO rango de precio que el gráfico
    # (ax.get_ylim() ya está fijado por los datos que acabamos de dibujar),
    # así las etiquetas quedan a la misma altura visual que su muro
    # correspondiente, pero en un espacio propio donde el texto tiene todo
    # el ancho que necesita sin depender de ninguna transformación de fecha.
    ax2.set_ylim(ax.get_ylim())
    ax2.set_xlim(0, 1)

    for p, q, usd in walls["asks"]:
        y_label = ask_ys[p]
        if abs(y_label - p) > min_gap * 0.3:
            ax2.plot([0, 0.06], [p, y_label], color='#FF8888', linewidth=0.6, alpha=0.5, zorder=2)
        ax2.text(0.08, y_label, f"${p:,.0f} — ${usd/1e6:.2f}M", color='white', fontsize=10.5,
                 va='center', ha='left', fontweight='bold', zorder=8,
                 bbox=dict(boxstyle='round,pad=0.3', facecolor='#0d1117', edgecolor='#FF3333', alpha=0.9))
    for p, q, usd in walls["bids"]:
        y_label = bid_ys[p]
        if abs(y_label - p) > min_gap * 0.3:
            ax2.plot([0, 0.06], [p, y_label], color='#88FF88', linewidth=0.6, alpha=0.5, zorder=2)
        ax2.text(0.08, y_label, f"${p:,.0f} — ${usd/1e6:.2f}M", color='white', fontsize=10.5,
                 va='center', ha='left', fontweight='bold', zorder=8,
                 bbox=dict(boxstyle='round,pad=0.3', facecolor='#0d1117', edgecolor='#00CC44', alpha=0.9))

    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=130, facecolor='#0d1117')
    plt.close()
    buf.seek(0)
    return buf

def resolve_binance_symbol(ticker):
    t = normalize_ticker(ticker)
    if t in BINANCE_MAP:
        return BINANCE_MAP[t]
    if t.endswith("-USD"):
        return f"{t[:-4]}USDT"
    return f"{t}USDT"

@bot.message_handler(commands=["ballenas"])
@con_dyor
def cmd_ballenas(msg):
    if not is_premium(msg.from_user.id):
        safe_send(msg.chat.id, "Necesitas suscripción activa.\n\n/trial — 7 días gratis\n/premium — 5€/mes")
        return
    parts = msg.text.split()
    if len(parts) < 2:
        safe_send(msg.chat.id,
            "Uso: /ballenas TICKER\n\nEjemplos:\n/ballenas BTC\n/ballenas ETH\n/ballenas SOL\n\n"
            "Solo funciona con criptomonedas (no acciones).\n\n"
            "Muestra los muros de compra/venta más grandes del libro de órdenes de Binance, "
            "±15% alrededor del precio actual.\n\n"
            "Nota: a diferencia de Coinglass, no mostramos cuánto tiempo lleva puesta cada orden "
            "(la API pública de Binance solo da una foto del momento, no el histórico).")
        return
    ticker = parts[1].upper()
    symbol = resolve_binance_symbol(ticker)
    m = bot.send_message(msg.chat.id, f"Consultando libro de órdenes de {ticker}...")
    walls = fetch_orderbook_walls(symbol)
    if not walls:
        safe_send(msg.chat.id,
            f"\"{ticker}\" no está disponible en el libro de órdenes de Binance.\n\n"
            "IMPORTANTE: /ballenas de momento SOLO funciona con criptomonedas "
            "(BTC, ETH, SOL...). Las acciones (TSLA, NVDA...) no están soportadas "
            "todavía — las acciones nuevas de Binance no están en la API pública "
            "que usamos.",
            message_id=m.message_id)
        return
    intraday = fetch_binance_intraday(symbol)
    try:
        chart = chart_ballenas(ticker, walls, intraday)
        bot.delete_message(msg.chat.id, m.message_id)
        bot.send_photo(msg.chat.id, chart)
    except Exception as e:
        log.warning(f"chart_ballenas: {e}")
        safe_send(msg.chat.id, "Tengo los datos pero falló al dibujar el gráfico. Reintenta en un momento.",
                  message_id=m.message_id)
        return

    lines = [f"{ticker} — ${walls['mid']:,.2f}\n", "MUROS DE VENTA (resistencia):"]
    for p, q, usd in walls["asks"]:
        lines.append(f"  ${p:,.2f} — ${usd/1e6:.2f}M")
    lines.append("\nMUROS DE COMPRA (soporte):")
    for p, q, usd in walls["bids"]:
        lines.append(f"  ${p:,.2f} — ${usd/1e6:.2f}M")
    safe_send(msg.chat.id, "\n".join(lines))

    asks_txt = ", ".join(f"${p:,.0f} (${usd/1e6:.1f}M)" for p, q, usd in walls["asks"][:4])
    bids_txt = ", ".join(f"${p:,.0f} (${usd/1e6:.1f}M)" for p, q, usd in walls["bids"][:4])
    prompt = (f"{ticker} cotiza a ${walls['mid']:,.2f}. Muros de venta (resistencia) cercanos: {asks_txt}. "
              f"Muros de compra (soporte) cercanos: {bids_txt}.\n\n"
              "1. ¿Qué nivel de resistencia y soporte parecen más relevantes?\n"
              "2. ¿Qué estrategia de entrada/salida sugieren estos muros?\n"
              "3. Riesgo de que sean órdenes 'trampa' (spoofing) que se cancelan antes de ejecutarse")
    safe_send(msg.chat.id, f"ANÁLISIS IA\n\n{ask_ai(prompt)}")


# ═══ /NOTICIAS — Agregador de noticias financieras y cripto ═════
# Varias fuentes distintas vía RSS (gratis, sin API key, sin límite de
# peticiones): Investing.com (bolsa/economía/cripto), Cointelegraph en
# español y CoinDesk. Parseamos el XML con la librería estándar de Python
# (xml.etree.ElementTree), sin depender de ninguna librería externa nueva.
import xml.etree.ElementTree as ET

NOTICIAS_FEEDS = {
    "📈 Bolsa (Investing.com)": "https://es.investing.com/rss/news_25.rss",
    "🏦 Economía (Investing.com)": "https://es.investing.com/rss/news_14.rss",
    "🪙 Cripto (Investing.com)": "https://es.investing.com/rss/news_301.rss",
    "🪙 Cripto (Cointelegraph ES)": "https://es.cointelegraph.com/feed",
    "🪙 Cripto (CoinDesk)": "https://www.coindesk.com/arc/outboundfeeds/rss/",
}

def fetch_rss(url, max_items=4):
    try:
        r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            log.warning(f"fetch_rss {url}: HTTP {r.status_code}")
            return []
        root = ET.fromstring(r.content)
        items = []
        for item in root.findall(".//item")[:max_items]:
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            if title and link:
                items.append({"title": title, "link": link})
        return items
    except Exception as e:
        log.warning(f"fetch_rss {url}: {e}")
        return []

def fetch_todas_noticias():
    resultado = {}
    for nombre, url in NOTICIAS_FEEDS.items():
        items = fetch_rss(url, max_items=4)
        if items:
            resultado[nombre] = items
    return resultado

@bot.message_handler(commands=["noticias"])
@con_dyor
def cmd_noticias(msg):
    if not is_premium(msg.from_user.id):
        safe_send(msg.chat.id, "Necesitas suscripción activa.\n\n/trial — 7 días gratis\n/premium — 5€/mes")
        return
    m = bot.send_message(msg.chat.id, "Consultando noticias... (10-15s)")
    data = fetch_todas_noticias()
    if not data:
        safe_send(msg.chat.id, "No he podido obtener noticias ahora mismo. Reintenta en un momento.",
                  message_id=m.message_id)
        return

    lines = ["📰 NOTICIAS FINANCIERAS Y CRIPTO"]
    for fuente, items in data.items():
        lines.append(f"\n— {fuente} —")
        for it in items:
            titulo = it["title"][:100]
            lines.append(f"• {titulo}\n  {it['link']}")
    texto = "\n".join(lines)
    safe_send(msg.chat.id, texto[:4096], message_id=m.message_id)

    # Análisis con IA basado SOLO en los titulares reales que acabamos de
    # traer — sin inventar detalles que no estén en el titular (mismo
    # principio que en /mercados: no fabricar catalizadores concretos).
    titulares = []
    for fuente, items in data.items():
        for it in items[:3]:
            titulares.append(f"- {it['title']}")
    prompt = ("Estos son titulares reales y recientes de varias fuentes financieras y cripto:\n"
              + "\n".join(titulares) +
              "\n\nNo inventes datos, cifras ni detalles que no estén en los titulares — trabaja "
              "solo con lo que dicen literalmente.\n\n"
              "1. ¿Cuáles son los 2-3 temas que más se repiten o parecen más relevantes?\n"
              "2. ¿Se intuye algún sesgo de sentimiento general (optimismo/pesimismo) en el conjunto?\n"
              "3. Qué merece la pena seguir de cerca en los próximos días")
    safe_send(msg.chat.id, f"ANÁLISIS IA\n\n{ask_ai(prompt)}")


# ═══ /MACRO — Macroeconomía (FRED) + derivados cripto (Binance) ═
# Datos macro oficiales de la Reserva Federal (FRED, gratis, requiere API
# key — https://fred.stlouisfed.org/docs/api/api_key.html) y métricas de
# derivados cripto estilo Coinglass (open interest, ratio long/short,
# funding rate) vía la API pública de futuros de Binance, que ya usamos en
# el resto del bot — sin necesitar ninguna key nueva para esa parte.

FRED_API_KEY = os.environ.get("FRED_API_KEY", "")

# (nombre a mostrar, series_id de FRED, transformación, unidad)
# units="pc1" le pide a FRED el cambio interanual (%) directamente, para no
# tener que calcularlo nosotros a partir del índice bruto.
MACRO_SERIES = [
    ("Tipos Fed (Fed Funds Rate)", "FEDFUNDS", None, "%"),
    ("Inflación EE.UU. (CPI interanual)", "CPIAUCSL", "pc1", "%"),
    ("Paro EE.UU.", "UNRATE", None, "%"),
    ("Bono EE.UU. 10 años", "DGS10", None, "%"),
    ("Bono EE.UU. 2 años", "DGS2", None, "%"),
]

def fetch_fred_latest(series_id, units=None):
    if not FRED_API_KEY:
        return None
    try:
        params = {"series_id": series_id, "api_key": FRED_API_KEY,
                  "file_type": "json", "sort_order": "desc", "limit": 2}
        if units:
            params["units"] = units
        r = requests.get("https://api.stlouisfed.org/fred/series/observations",
                        params=params, timeout=10)
        if r.status_code != 200:
            log.warning(f"fetch_fred_latest {series_id}: HTTP {r.status_code} — {r.text[:200]}")
            return None
        obs = [o for o in r.json().get("observations", []) if o.get("value") not in (".", None, "")]
        if not obs:
            return None
        return {"valor": float(obs[0]["value"]), "fecha": obs[0]["date"],
                "anterior": float(obs[1]["value"]) if len(obs) > 1 else None}
    except Exception as e:
        log.warning(f"fetch_fred_latest {series_id}: {e}")
        return None

def fetch_derivados_cripto(symbol):
    """Métricas estilo Coinglass (open interest, ratio long/short, funding)
    vía la API pública de futuros de Binance — gratis, sin key, ya la
    usamos para el funding rate en /valor."""
    try:
        r_oi = requests.get("https://fapi.binance.com/fapi/v1/openInterest",
                            params={"symbol": symbol}, timeout=8)
        oi_coins = float(r_oi.json().get("openInterest", 0))
        r_ls = requests.get("https://fapi.binance.com/futures/data/globalLongShortAccountRatio",
                            params={"symbol": symbol, "period": "1h", "limit": 1}, timeout=8)
        ls_data = r_ls.json()
        ls_ratio = float(ls_data[0]["longShortRatio"]) if ls_data else None
        funding = get_funding(symbol)
        return {"oi_coins": oi_coins, "ls_ratio": ls_ratio, "funding": funding}
    except Exception as e:
        log.warning(f"fetch_derivados_cripto {symbol}: {e}")
        return None

@bot.message_handler(commands=["macro"])
@con_dyor
def cmd_macro(msg):
    if not is_premium(msg.from_user.id):
        safe_send(msg.chat.id, "Necesitas suscripción activa.\n\n/trial — 7 días gratis\n/premium — 5€/mes")
        return
    if not FRED_API_KEY:
        safe_send(msg.chat.id,
            "Falta configurar FRED_API_KEY en el servidor.\n\n"
            "Clave gratis en: https://fred.stlouisfed.org/docs/api/api_key.html")
        return
    m = bot.send_message(msg.chat.id, "Consultando datos macro y derivados... (10-15s)")

    macro_resultados = []
    for nombre, series_id, units, unidad in MACRO_SERIES:
        d = fetch_fred_latest(series_id, units)
        if d:
            macro_resultados.append((nombre, d, unidad))

    btc_price = get_quote("BTC-USD")
    eth_price = get_quote("ETH-USD")
    btc_deriv = fetch_derivados_cripto("BTCUSDT")
    eth_deriv = fetch_derivados_cripto("ETHUSDT")

    if not macro_resultados and not btc_deriv and not eth_deriv:
        safe_send(msg.chat.id, "No he podido obtener datos ahora mismo. Reintenta en un momento.",
                  message_id=m.message_id)
        return

    lines = ["📊 MACRO Y DERIVADOS CRIPTO\n", "— Macroeconomía EE.UU. (FRED) —"]
    if macro_resultados:
        for nombre, d, unidad in macro_resultados:
            cambio = f" (dato anterior: {d['anterior']:.2f}{unidad})" if d["anterior"] is not None else ""
            lines.append(f"• {nombre}: {d['valor']:.2f}{unidad} — {d['fecha']}{cambio}")
    else:
        lines.append("Sin datos disponibles.")

    lines.append("\n— Derivados Cripto (Binance) —")
    for nombre, dv, precio_d in [("Bitcoin", btc_deriv, btc_price), ("Ethereum", eth_deriv, eth_price)]:
        if dv:
            oi_usd_txt = ""
            if precio_d:
                oi_usd_txt = f" (${dv['oi_coins']*precio_d['price']/1e9:.2f}B)"
            ls_txt = f"{dv['ls_ratio']:.2f}" if dv["ls_ratio"] is not None else "N/D"
            fund_txt = f"{dv['funding']:+.4f}%" if dv["funding"] is not None else "N/D"
            lines.append(f"• {nombre}: Open Interest {dv['oi_coins']:,.0f}{oi_usd_txt} | "
                        f"Ratio Long/Short {ls_txt} | Funding {fund_txt}")
    safe_send(msg.chat.id, "\n".join(lines), message_id=m.message_id)

    resumen = "; ".join(f"{n}: {d['valor']:.2f}{u}" for n, d, u in macro_resultados)
    deriv_resumen = ""
    if btc_deriv:
        deriv_resumen += f"BTC ratio L/S {btc_deriv['ls_ratio']}, funding {btc_deriv['funding']:+.4f}%. "
    if eth_deriv:
        deriv_resumen += f"ETH ratio L/S {eth_deriv['ls_ratio']}, funding {eth_deriv['funding']:+.4f}%."
    prompt = (f"Datos macro EE.UU. (FRED): {resumen}.\n"
              f"Derivados cripto (Binance): {deriv_resumen}\n\n"
              "No inventes datos que no estén aquí.\n\n"
              "1. ¿Qué lectura general sugiere este panorama macro (tipos, inflación, paro, curva de tipos)?\n"
              "2. ¿Qué indica el posicionamiento en derivados cripto (ratio long/short, funding) sobre el "
              "sentimiento del mercado ahora mismo?\n"
              "3. Qué vigilar en las próximas semanas")
    safe_send(msg.chat.id, f"ANÁLISIS IA\n\n{ask_ai(prompt)}")


# ═══ DIFUSIÓN AUTOMÁTICA — resumen cada hora (9:00-22:00) + alertas ═
# de noticias relevantes. A diferencia de todo lo demás en este bot (que
# solo responde cuando el usuario escribe un comando), esto corre en
# segundo plano y escribe a los suscriptores por su cuenta.

BROADCAST_CRYPTO = {
    "Bitcoin": "BTC-USD", "Ethereum": "ETH-USD", "Solana": "SOL-USD", "BNB": "BNB-USD",
    "XRP": "XRP-USD", "Cardano": "ADA-USD", "Dogecoin": "DOGE-USD", "Avalanche": "AVAX-USD",
    "Chainlink": "LINK-USD", "Polkadot": "DOT-USD", "Hedera": "HBAR-USD",
}
# HYPE y PURR NO están en el mercado spot de Binance global (solo en
# Binance.US, una plataforma distinta con otra API, o en el propio DEX de
# Hyperliquid) — así que no se pueden traer con fetch_binance como el
# resto. Usamos CoinGecko (gratis, sin API key) solo para estos dos.
BROADCAST_CRYPTO_COINGECKO = {
    "Hyperliquid": "hyperliquid", "PURR": "purr-2",
}
BROADCAST_STOCKS = {
    "Apple": "AAPL", "Microsoft": "MSFT", "Nvidia": "NVDA", "Amazon": "AMZN",
    "Google": "GOOGL", "Meta": "META", "Tesla": "TSLA", "JPMorgan": "JPM",
    "Netflix": "NFLX", "SpaceX": "SPCX", "Strategy (Saylor)": "MSTR",
    "Walmart": "WMT", "Coca-Cola": "KO",
}
BROADCAST_INDICES = {
    "S&P 500": "^GSPC", "Nasdaq": "^IXIC", "IBEX 35": "^IBEX", "DAX": "^GDAXI", "CAC 40": "^FCHI",
}
# VWCE (Vanguard FTSE All-World UCITS ETF) cotiza en Xetra como VWCE.DE —
# el sufijo ".DE" ya lo reconoce fetch_stooq automáticamente, sin necesitar
# ningún mapeo especial. VGLA (FTSE Global All-Cap, lanzado 20 agosto
# 2026) igual, mismo patrón.
BROADCAST_ETF = {
    "VWCE (All-World)": "VWCE.DE",
    "VGLA (Global All-Cap)": "VGLA.DE",
}
BROADCAST_COMMODITIES = {
    "Oro": "GC=F",
}
BROADCAST_FALLBACK = {"^IXIC": "QQQ", "^GSPC": "SPY"}  # mismo fix que ya vimos con /mercados

# Límite compartido para TODAS las llamadas a CoinGecko (HYPE/PURR en el
# resumen automático + /fuerza pidiendo 100 monedas) — sin esto se pisan
# entre sí y revientan el rate-limit gratuito de CoinGecko (mismo problema
# que ya vimos con Twelve Data, mismo tipo de arreglo).
_CG_CALL_TIMES = []
_CG_MAX_PER_MIN = 8

def _throttle_coingecko():
    global _CG_CALL_TIMES
    now = time.time()
    _CG_CALL_TIMES = [t for t in _CG_CALL_TIMES if now - t < 60]
    if len(_CG_CALL_TIMES) >= _CG_MAX_PER_MIN:
        wait = 60 - (now - _CG_CALL_TIMES[0]) + 0.5
        if wait > 0:
            time.sleep(wait)
        now = time.time()
        _CG_CALL_TIMES = [t for t in _CG_CALL_TIMES if now - t < 60]
    _CG_CALL_TIMES.append(time.time())

def fetch_coingecko_simple(coin_id):
    """Fuente alternativa solo para cripto que no está en Binance spot
    (HYPE, PURR). Gratis, sin API key. Rate limit generoso para el poco
    uso que le damos aquí (2 monedas, una vez cada 2h)."""
    ck = f"cg:{coin_id}"
    cached = cache_get(ck)
    if cached is not None: return cached
    def _do():
        _throttle_coingecko()
        r = requests.get("https://api.coingecko.com/api/v3/simple/price",
                        params={"ids": coin_id, "vs_currencies": "usd",
                                "include_24hr_change": "true"}, timeout=10)
        j = r.json()
        if coin_id not in j:
            raise RuntimeError(f"CoinGecko: sin datos para {coin_id}")
        return {"price": j[coin_id]["usd"], "d1": j[coin_id].get("usd_24h_change", 0)}
    return with_retry(_do, tries=3, base_delay=5, what=f"fetch_coingecko_simple {coin_id}")

def calcular_broadcast():
    """Recorre los ~30 activos con una pequeña pausa entre cada uno —
    lección aprendida de cuando /mercados reventaba el límite de Twelve
    Data al pedir muchos tickers en ráfaga."""
    resultados = []
    for grupo, tickers in [("🪙 Cripto", BROADCAST_CRYPTO), ("📈 Acciones", BROADCAST_STOCKS),
                            ("🌍 Índices", BROADCAST_INDICES), ("📦 ETF", BROADCAST_ETF),
                            ("🥇 Materias primas", BROADCAST_COMMODITIES)]:
        for nombre, ticker in tickers.items():
            d = get_quote(ticker)
            if not d and ticker in BROADCAST_FALLBACK:
                d = get_quote(BROADCAST_FALLBACK[ticker])
            if d:
                resultados.append({"grupo": grupo, "nombre": nombre, "d1": d["d1"]})
            time.sleep(0.3)
    for nombre, coin_id in BROADCAST_CRYPTO_COINGECKO.items():
        d = fetch_coingecko_simple(coin_id)
        if d:
            # Insertamos justo después de los resultados de cripto que ya
            # tengamos (no en una posición fija: si algún cripto de
            # Binance ha fallado esta vez, len(BROADCAST_CRYPTO) ya no
            # coincidiría con dónde termina el grupo de verdad).
            pos_insercion = sum(1 for r in resultados if r["grupo"] == "🪙 Cripto")
            resultados.insert(pos_insercion,
                              {"grupo": "🪙 Cripto", "nombre": nombre, "d1": d["d1"]})
        time.sleep(0.3)
    return resultados

def chart_broadcast(resultados):
    n = len(resultados)
    fig, ax = plt.subplots(figsize=(13, max(7, n*0.5)))
    fig.patch.set_facecolor('#0d1117')
    ax.set_facecolor('#0d1117')

    nombres = [f"{r['nombre']}" for r in resultados]
    valores = [r["d1"] for r in resultados]
    colores = ['#00CC44' if v >= 0 else '#FF3333' for v in valores]
    y_pos = list(range(n))

    # FIX: antes las barras salían "hacia fuera" desde el cero (positivas a
    # la derecha, negativas a la izquierda), así que el % quedaba en un
    # lado distinto según el signo. Ahora TODAS las barras arrancan desde
    # la izquierda (junto al nombre) y crecen hacia la derecha según el
    # valor absoluto — así el orden de lectura es siempre el mismo:
    # nombre -> barra -> %, sea subida o bajada.
    abs_valores = [abs(v) for v in valores]
    ax.barh(y_pos, abs_valores, color=colores, height=0.55, zorder=3)

    max_abs = max(abs_valores) or 1
    for i, v in enumerate(valores):
        flecha = "▲" if v >= 0 else "▼"
        ax.text(abs(v) + max_abs*0.04, i, f"{flecha} {v:+.2f}%",
                va='center', ha='left', color=colores[i], fontweight='bold', fontsize=13)

    ax.set_yticks(y_pos)
    ax.set_yticklabels(nombres, color='white', fontsize=13, fontweight='bold')
    ax.invert_yaxis()
    ax.set_xlim(0, max_abs*1.55)
    ax.set_xticks([])
    for spine in ax.spines.values(): spine.set_visible(False)
    ax.tick_params(left=False)

    # Separadores entre grupos (Cripto / Acciones / Índices)
    grupo_actual = None
    for i, r in enumerate(resultados):
        if r["grupo"] != grupo_actual:
            if grupo_actual is not None:
                ax.axhline(i - 0.5, color='#333333', linewidth=1, zorder=1)
            grupo_actual = r["grupo"]

    fecha_txt = datetime.now(MADRID).strftime('%d/%m %H:%M')
    ax.set_title(f'RESUMEN DE MERCADOS — {fecha_txt}', color='white', fontsize=17,
                 fontweight='bold', loc='left', pad=18)

    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=130, facecolor='#0d1117', bbox_inches='tight')
    plt.close()
    buf.seek(0)
    return buf.getvalue()

def _lista_suscriptores_activos():
    ids = set(SUSCRIPTORES.keys())
    if ALLOWED_USER_ID:
        ids.add(ALLOWED_USER_ID)
    return [cid for cid in ids if is_premium(cid)]

# ── Noticias relevantes: solo avisamos de titulares NUEVOS desde la
# última vez, y solo si la IA los juzga realmente importantes (para no
# mandar una alerta cada hora con cualquier cosa) ──
NOTICIAS_VISTAS_FILE = os.environ.get("NOTICIAS_VISTAS_FILE", _p("noticias_vistas.json"))

def _cargar_noticias_vistas():
    try:
        with open(NOTICIAS_VISTAS_FILE, "r") as f:
            return set(json.load(f))
    except Exception:
        return set()

def _guardar_noticias_vistas(vistas):
    try:
        # nos quedamos solo con las últimas ~500 para que el fichero no crezca sin límite
        recientes = list(vistas)[-500:]
        with open(NOTICIAS_VISTAS_FILE, "w") as f:
            json.dump(recientes, f)
    except Exception as e:
        log.warning(f"_guardar_noticias_vistas: {e}")

def revisar_noticias_relevantes():
    vistas = _cargar_noticias_vistas()
    data = fetch_todas_noticias()
    nuevas = []
    for fuente, items in data.items():
        for it in items:
            if it["link"] not in vistas:
                nuevas.append(it)
    if not nuevas:
        return None
    for it in nuevas:
        vistas.add(it["link"])
    _guardar_noticias_vistas(vistas)

    titulares = "\n".join(f"- {it['title']}" for it in nuevas[:20])
    prompt = ("Estos son titulares NUEVOS (desde la última revisión) de fuentes financieras/cripto:\n"
              f"{titulares}\n\n"
              "Responde ÚNICAMENTE con los que consideres noticias realmente importantes y con "
              "potencial de mover mercados de forma notable (decisiones de bancos centrales, "
              "quiebras, hackeos grandes, cambios regulatorios importantes, datos macro "
              "sorprendentes...), cada uno en su propia línea empezando por '• '. No inventes "
              "nada que no esté en los titulares. Si ninguno te parece de verdad relevante, "
              "responde exactamente: NINGUNA")
    respuesta = ask_ai(prompt, max_chars=1200)
    if not respuesta or "NINGUNA" in respuesta.upper()[:30]:
        return None
    return respuesta

@bot.message_handler(commands=["ticker"])
@con_dyor
def cmd_ticker(msg):
    """Versión bajo demanda del resumen automático — para probarlo cuando
    quieras sin esperar a que llegue la hora en punto, o simplemente para
    consultarlo fuera del horario 9:00-22:00."""
    if not is_premium(msg.from_user.id):
        safe_send(msg.chat.id, "Necesitas suscripción activa.\n\n/trial — 7 días gratis\n/premium — 5€/mes")
        return
    m = bot.send_message(msg.chat.id, "Generando resumen de mercados... (15-20s)")
    resultados = calcular_broadcast()
    if not resultados:
        safe_send(msg.chat.id, "No he podido obtener datos ahora mismo. Reintenta en un momento.",
                  message_id=m.message_id)
        return
    try:
        img_bytes = chart_broadcast(resultados)
        bot.delete_message(msg.chat.id, m.message_id)
        bot.send_photo(msg.chat.id, io.BytesIO(img_bytes))
    except Exception as e:
        log.warning(f"cmd_ticker chart: {e}")
        lines = [f"{r['nombre']}: {r['d1']:+.2f}%" for r in resultados]
        safe_send(msg.chat.id, "\n".join(lines), message_id=m.message_id)

@bot.message_handler(commands=["scheduler_estado"])
def cmd_scheduler_estado(msg):
    if msg.from_user.id != ALLOWED_USER_ID: return
    ahora = datetime.now(MADRID)
    en_ventana = _debe_emitir_ahora(ahora)
    safe_send(msg.chat.id,
        f"Hora actual (Madrid): {ahora.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"¿Dentro de ventana de emisión (9-22h, cada 2h, min<15)?: {'Sí' if en_ventana else 'No'}\n"
        f"Última clave de difusión emitida: {_ultimo_broadcast_key or 'ninguna todavía'}\n"
        f"Último resumen diario: {_ultimo_resumen_diario_key or 'ninguno todavía'}\n"
        f"Suscriptores activos: {len(_lista_suscriptores_activos())}")

def ejecutar_broadcast_hora():
    destinatarios = _lista_suscriptores_activos()
    if not destinatarios:
        log.info("ejecutar_broadcast_hora: sin suscriptores activos, nada que enviar")
        return

    try:
        resultados = calcular_broadcast()
        if resultados:
            img_bytes = chart_broadcast(resultados)
            for cid in destinatarios:
                try:
                    bot.send_photo(cid, io.BytesIO(img_bytes))
                except Exception as e:
                    log.warning(f"broadcast precio -> {cid}: {e}")
                time.sleep(0.05)
    except Exception as e:
        log.error(f"ejecutar_broadcast_hora (precios): {e}")

    try:
        alerta = revisar_noticias_relevantes()
        if alerta:
            texto = f"🚨 NOTICIA RELEVANTE\n\n{alerta}"
            for cid in destinatarios:
                try:
                    safe_send(cid, texto)
                except Exception as e:
                    log.warning(f"broadcast noticia -> {cid}: {e}")
                time.sleep(0.05)
    except Exception as e:
        log.error(f"ejecutar_broadcast_hora (noticias): {e}")

_ultimo_broadcast_key = None

# ── Resumen diario matutino ──
# A diferencia del resumen cada 2 horas (que es un vistazo rápido de
# precios), esto es un mensaje una vez al día pensado como "primer
# vistazo de la mañana": Fear & Greed, BTC y los titulares más
# destacados — para generar el hábito de abrir el bot cada mañana.
_ultimo_resumen_diario_key = None
RESUMEN_DIARIO_HORA = 8

def calcular_resumen_diario():
    btc = get_quote("BTC-USD")
    fg_series = fetch_feargreed_history()
    fg_actual = fg_series[-1] if fg_series else None
    noticias = fetch_todas_noticias()
    return {"btc": btc, "fg": fg_actual, "noticias": noticias}

def texto_resumen_diario(datos):
    lines = ["☀️ BUENOS DÍAS — Resumen diario\n"]
    b = datos.get("btc")
    if b:
        flecha = "▲" if b["d1"] >= 0 else "▼"
        lines.append(f"🪙 Bitcoin: ${b['price']:,.0f} ({flecha} {b['d1']:+.2f}% en 24h)")
    fg = datos.get("fg")
    if fg:
        lines.append(f"😨 Fear & Greed: {fg['value']}/100 ({fg['clase']})")
    noticias = datos.get("noticias") or {}
    if noticias:
        lines.append("\n📰 Titulares destacados:")
        contador = 0
        for fuente, items in noticias.items():
            if items and contador < 5:
                lines.append(f"• {items[0]['title'][:100]}")
                contador += 1
    lines.append("\nUsa /noticias, /macro, /ciclo o /ticker para profundizar.")
    lines.append("⚠️ Información, no asesoramiento financiero. Lee /dyor.")
    return "\n".join(lines)

def ejecutar_resumen_diario():
    destinatarios = _lista_suscriptores_activos()
    if not destinatarios:
        log.info("ejecutar_resumen_diario: sin suscriptores activos, nada que enviar")
        return
    try:
        datos = calcular_resumen_diario()
        texto = texto_resumen_diario(datos)
        for cid in destinatarios:
            try:
                safe_send(cid, texto)
            except Exception as e:
                log.warning(f"resumen diario -> {cid}: {e}")
            time.sleep(0.05)
    except Exception as e:
        log.error(f"ejecutar_resumen_diario: {e}")

def _debe_emitir_resumen_diario(ahora):
    return ahora.hour == RESUMEN_DIARIO_HORA and ahora.minute < 15

def _debe_emitir_ahora(ahora):
    # Franja horaria: de 9:00 a 22:00 (Madrid), cada 2 horas en punto
    # (9, 11, 13, 15, 17, 19, 21) — antes era cada hora, se cambió a
    # petición del usuario.
    if not (9 <= ahora.hour <= 22):
        return False
    if (ahora.hour - 9) % 2 != 0:
        return False
    return ahora.minute < 15  # margen amplio por si hay un redeploy justo entonces

# ── Aviso de caducidad: un solo mensaje cuando quedan ≤30h de acceso ──
# Se guarda a qué caducidad se avisó (en el volumen), así un redeploy no repite
# el aviso, y si el usuario renueva (nueva caducidad) puede volver a avisarse.
AVISOS_CAD_FILE = os.environ.get("AVISOS_CADUCIDAD_FILE", _p("avisos_caducidad.json"))
AVISO_CAD_HORAS = 30

def _load_avisos_cad():
    try:
        with open(AVISOS_CAD_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}

_AVISOS_CAD = _load_avisos_cad()  # {str(chat_id): caducidad_iso ya avisada}

def _save_avisos_cad():
    try:
        tmp = AVISOS_CAD_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(_AVISOS_CAD, f)
        os.replace(tmp, AVISOS_CAD_FILE)
    except Exception as e:
        log.warning(f"_save_avisos_cad: {e}")

def revisar_caducidades():
    ahora = datetime.now()
    hubo_cambios = False
    for cid, s in list(SUSCRIPTORES.items()):
        if cid == ALLOWED_USER_ID or not s.get("activo") or not s.get("expiry"):
            continue
        exp = s["expiry"]
        horas = (exp - ahora).total_seconds() / 3600
        if not (0 < horas <= AVISO_CAD_HORAS):
            continue
        if _AVISOS_CAD.get(str(cid)) == exp.isoformat():
            continue  # ya avisado para esta caducidad
        safe_send(cid,
            f"⏳ TU ACCESO CADUCA PRONTO\n\n"
            f"Caduca el {exp.strftime('%d/%m/%Y')} (en unas {max(1, round(horas))} horas).\n\n"
            "Para no perderlo: /premium — los 30 días nuevos se suman al final de tu acceso "
            "actual, así que no pierdes nada por renovar antes.\n"
            "Si ya has pagado, envía /verificar HASH.")
        _AVISOS_CAD[str(cid)] = exp.isoformat()
        hubo_cambios = True
        log.info(f"aviso de caducidad enviado a {cid} (caduca {exp.isoformat()})")
        time.sleep(0.05)
    if hubo_cambios:
        _save_avisos_cad()

_ultimo_aviso_cad_key = None

def _scheduler_loop():
    global _ultimo_broadcast_key, _ultimo_resumen_diario_key, _ultimo_aviso_cad_key
    log.info("Scheduler de difusión automática arrancado")
    _n_check = 0
    while True:
        try:
            ahora = datetime.now(MADRID)
            _n_check += 1
            if _n_check % 10 == 0:  # latido cada ~10 min, para poder verificar en logs que sigue vivo
                log.info(f"Scheduler vivo — hora actual Madrid: {ahora.strftime('%Y-%m-%d %H:%M')}, "
                        f"última difusión: {_ultimo_broadcast_key}, último resumen diario: {_ultimo_resumen_diario_key}")
            if _debe_emitir_ahora(ahora):
                clave = ahora.strftime("%Y-%m-%d %H")
                if clave != _ultimo_broadcast_key:
                    _ultimo_broadcast_key = clave
                    log.info(f"Ejecutando broadcast automático ({clave})")
                    ejecutar_broadcast_hora()
            if _debe_emitir_resumen_diario(ahora):
                clave_dia = ahora.strftime("%Y-%m-%d")
                if clave_dia != _ultimo_resumen_diario_key:
                    _ultimo_resumen_diario_key = clave_dia
                    log.info(f"Ejecutando resumen diario ({clave_dia})")
                    ejecutar_resumen_diario()
            if 9 <= ahora.hour <= 21 and ahora.minute < 15:
                clave_cad = ahora.strftime("%Y-%m-%d %H")
                if clave_cad != _ultimo_aviso_cad_key:
                    _ultimo_aviso_cad_key = clave_cad
                    revisar_caducidades()
        except Exception as e:
            log.error(f"_scheduler_loop: {e}")
        time.sleep(60)

# ═══ /CICLO — Ciclo de mercado simplificado (Pico/Contracción/Suelo/
# Expansión/Recuperación/Prosperidad), con BTC marcado en su fase actual ═
# Reutiliza la misma lógica de "meses desde el halving" que ya usa
# /halvingbtc (ahí ya está verificada) como señal principal, y la afina con
# RSI y distancia al máximo histórico para situar el punto con más
# precisión dentro de esa fase.

def calcular_ciclo_btc():
    d = get_quote("BTC-USD")
    if not d:
        return None
    import datetime as dt

    # FIX de raíz: antes "distancia al máximo" se calculaba con solo 220
    # días de histórico (d["hi52"]), lo que en septiembre de 2026 ni
    # siquiera alcanza a ver el máximo histórico real de octubre de 2025
    # ($126,080) — así que comparaba el precio actual contra un "máximo"
    # equivocado, mucho más bajo que el real, y nunca llegaba a ver el
    # suelo real del ciclo (~$58,120, 25 junio 2026) tampoco. Ahora se usa
    # el histórico completo real (mismo mecanismo que ya usa /dominancia)
    # para encontrar el máximo y el mínimo de verdad.
    hist = fetch_btc_price_history_long(days=500)
    rsi = d["rsi"]
    price_now = d["price"]

    if hist and len(hist["closes"]) > 30:
        closes = hist["closes"]
        idx_ath = closes.idxmax()
        ath = float(closes.iloc[idx_ath])
        # Mínimo realizado DESPUÉS del máximo (el suelo real de este ciclo
        # bajista, si ya ha ocurrido) — no un mínimo hipotético.
        post_ath = closes.iloc[idx_ath:]
        low_after_ath = float(post_ath.min()) if len(post_ath) > 0 else ath
        dist_ath = (price_now - ath) / ath * 100
        # Posición de recuperación: 0 = justo en el mínimo realizado,
        # 1 = de vuelta en el máximo histórico. Si el precio actual ES el
        # mínimo (todavía cayendo), recovery_frac = 0.
        rango_total = ath - low_after_ath
        recovery_frac = ((price_now - low_after_ath) / rango_total) if rango_total > 0 else 0.5
        recovery_frac = max(0.0, min(1.0, recovery_frac))
    else:
        # Sin histórico largo disponible: fallback conservador con lo que
        # ya teníamos (peor, pero mejor que fallar del todo).
        ath = d["hi52"]
        dist_ath = (price_now - ath) / ath * 100 if ath > 0 else 0
        recovery_frac = 0.5
        low_after_ath = None

    # El suelo real ya ha pasado (recovery_frac > 0 y el mínimo no es el
    # precio de ahora mismo) -> estamos en la mitad ASCENDENTE del ciclo
    # (Suelo -> Expansión -> Recuperación -> Prosperidad), avanzando en
    # proporción a cuánto llevamos recuperado desde ese mínimo real hacia
    # el máximo anterior. RSI ajusta un poco dentro de ese tramo.
    rsi_ajuste = max(0.0, min(1.0, (rsi - 30) / 40))  # alto = sobrecompra = empuja más adelante
    avance = recovery_frac * 0.8 + rsi_ajuste * 0.2
    x_frac = 0.5 + avance * 0.5  # 0.5 = justo en el Suelo, 1.0 = de vuelta al Pico

    if recovery_frac < 0.15:
        fase = "Suelo (saliendo de mínimos)"
    elif recovery_frac < 0.45:
        fase = "Expansión temprana"
    elif recovery_frac < 0.75:
        fase = "Expansión / Recuperación"
    else:
        fase = "Recuperación avanzada (cerca de máximos previos)"

    meses = (dt.date.today() - dt.date(2024, 4, 19)).days // 30

    return {"x_frac": x_frac, "fase": fase, "meses": meses, "rsi": round(rsi, 1),
            "dist_ath": round(dist_ath, 1), "price": d["price"]}

def chart_ciclo_mercado(res):
    fig, ax = plt.subplots(figsize=(13, 8))
    fig.patch.set_facecolor('#0d1117')
    ax.set_facecolor('#0d1117')

    xs = np.linspace(0, 1, 400)
    ys = np.cos(2*np.pi*xs)

    # Degradado de color siguiendo la curva: rojo/naranja en el pico
    # (riesgo máximo), pasando por amarillo, hasta verde/azul en el suelo
    # (oportunidad máxima) — mismo código de colores que el resto del bot.
    for i in range(len(xs)-1):
        frac = (ys[i] + 1) / 2  # 1 en el pico, 0 en el suelo
        if frac > 0.8:   c = '#FF3333'
        elif frac > 0.55:c = '#FF9900'
        elif frac > 0.45:c = '#FFCC00'
        elif frac > 0.2: c = '#66CC66'
        else:             c = '#3388FF'
        ax.plot(xs[i:i+2], ys[i:i+2], color=c, linewidth=6, solid_capstyle='round', zorder=3)

    ax.fill_between(xs, ys, -1.3, color='#0d1117', zorder=1)
    ax.axhline(-1.15, color='#333333', linewidth=1, zorder=2)

    # Etiquetas de las fases
    def marcar(x, y, texto, sub, dy=0.22, ha='center'):
        ax.plot(x, y, 'o', color='white', markersize=8, zorder=5)
        ax.text(x, y+dy, texto, color='white', fontsize=13, fontweight='bold',
                ha=ha, va='bottom', zorder=6)
        if sub:
            ax.text(x, y+dy-0.11, sub, color='#999999', fontsize=9.5, ha=ha, va='bottom', zorder=6)

    marcar(0.0, 1.0, "PICO", "Riesgo financiero máximo", dy=0.18, ha='left')
    marcar(0.5, -1.0, "SUELO", "Oportunidad financiera máxima", dy=0.30)
    ax.text(0.78, -0.75, "Recuperación", color='#AAAAAA', fontsize=11, fontweight='bold', ha='center')
    ax.text(0.93, -0.35, "Prosperidad", color='#AAAAAA', fontsize=11, fontweight='bold', ha='center')
    ax.text(0.22, 0.0, "Contracción", color='#AAAAAA', fontsize=12, fontweight='bold', ha='center')
    ax.text(0.68, 0.0, "Expansión", color='#AAAAAA', fontsize=12, fontweight='bold', ha='center')

    # Marcador de BTC en su posición actual
    xf = res["x_frac"]
    yf = np.cos(2*np.pi*xf)
    ax.plot(xf, yf, 'o', color='#F7931A', markersize=22, zorder=10,
            markeredgecolor='white', markeredgewidth=2.5)
    ax.annotate(f"BTC AHORA\n${res['price']:,.0f}", xy=(xf, yf), xytext=(xf, yf+0.35),
                fontsize=11, color='#F7931A', fontweight='bold', ha='center', zorder=11,
                bbox=dict(boxstyle='round,pad=0.35', facecolor='#0d1117', edgecolor='#F7931A',
                          linewidth=2, alpha=0.95),
                arrowprops=dict(arrowstyle='->', color='#F7931A', lw=1.8))

    ax.set_xlim(-0.03, 1.03)
    ax.set_ylim(-1.35, 1.45)
    ax.axis('off')
    ax.set_title('CICLO DE MERCADO SIMPLIFICADO — BITCOIN', color='white', fontsize=16,
                 fontweight='bold', pad=10)

    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=130, facecolor='#0d1117', bbox_inches='tight')
    plt.close()
    buf.seek(0)
    return buf

@bot.message_handler(commands=["ciclo"])
@con_dyor
def cmd_ciclo(msg):
    if not is_premium(msg.from_user.id):
        safe_send(msg.chat.id, "Necesitas suscripción activa.\n\n/trial — 7 días gratis\n/premium — 5€/mes")
        return
    m = bot.send_message(msg.chat.id, "Calculando posición de BTC en el ciclo... (10-15s)")
    res = calcular_ciclo_btc()
    if not res:
        safe_send(msg.chat.id, "Sin datos de BTC ahora mismo. Reintenta en un momento.",
                  message_id=m.message_id)
        return
    try:
        chart = chart_ciclo_mercado(res)
        bot.delete_message(msg.chat.id, m.message_id)
        bot.send_photo(msg.chat.id, chart)
    except Exception as e:
        log.warning(f"chart_ciclo_mercado: {e}")
        safe_send(msg.chat.id, f"BTC está en fase: {res['fase']}", message_id=m.message_id)

    prompt = (f"Bitcoin cotiza a ${res['price']:,.0f}. Lleva {res['meses']} meses desde el último "
              f"halving (19 abril 2024). RSI 14d: {res['rsi']}. Distancia al máximo histórico: "
              f"{res['dist_ath']:+.1f}%. Según este contexto, ahora mismo se sitúa en la fase de "
              f"'{res['fase']}' dentro del ciclo de mercado clásico (Pico -> Contracción -> Suelo -> "
              f"Expansión -> Recuperación -> Prosperidad).\n\n"
              "1. ¿Qué implica estar en esta fase concreta del ciclo?\n"
              "2. ¿Qué señales confirmarían el paso a la siguiente fase?\n"
              "3. Estrategia razonable dado este punto del ciclo")
    safe_send(msg.chat.id, f"ANÁLISIS IA\n\n{ask_ai(prompt)}")


# ═══ /SUELO — Triple Suelo de Sentimiento (VIX + AAII + Fear & Greed) ═
# Metodología: alineación de tres métricas de pánico desde ángulos
# distintos. El NAAIM (gestores activos) hubiera sido el tercer ángulo
# "institucional puro", pero pasó a ser de pago desde el 1 de agosto de
# 2026 ($1.500/año para acceso API) — usamos Fear & Greed como sustituto
# razonable de esa pata institucional, con la limitación honesta de que
# no es lo mismo (mide sentimiento agregado del mercado, no exposición
# real de gestores).
from bs4 import BeautifulSoup

def fetch_aaii_sentiment():
    """Página pública de verdad, sin login — confirmado a mano. Tabla con
    Bullish/Neutral/Bearish semanales, la fila más reciente primero.
    FIX: antes solo miraba la PRIMERA tabla de la página (soup.find), que
    puede no ser la de datos si hay otras tablas antes en el HTML (menús,
    layout...) — ahora recorre todas las tablas hasta encontrar filas con
    el formato esperado. También manda cabeceras más completas de
    navegador, por si la web sirve algo distinto a peticiones muy básicas."""
    ck = "aaii_sentiment"
    cached = cache_get(ck)
    if cached is not None: return cached
    def _do():
        r = requests.get("https://www.aaii.com/sentimentsurvey/sent_results",
                        headers={
                            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
                            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                            "Accept-Language": "en-US,en;q=0.9",
                        }, timeout=15)
        if r.status_code != 200:
            raise RuntimeError(f"AAII: HTTP {r.status_code}")
        soup = BeautifulSoup(r.text, "html.parser")
        tablas = soup.find_all("table")
        for table in tablas:
            for fila in table.find_all("tr"):
                celdas = [td.get_text(strip=True) for td in fila.find_all("td")]
                if len(celdas) == 4:
                    try:
                        return {"fecha": celdas[0],
                                "bullish": float(celdas[1].replace("%", "")),
                                "neutral": float(celdas[2].replace("%", "")),
                                "bearish": float(celdas[3].replace("%", ""))}
                    except ValueError:
                        continue
        raise RuntimeError(f"AAII: sin fila válida en {len(tablas)} tablas ({len(r.text)} bytes de HTML)")
    res = with_retry(_do, tries=2, base_delay=2, what="fetch_aaii_sentiment")
    if res: cache_set(ck, res)
    return res

def fetch_cot_sp500(semanas=156):
    """Commitment of Traders (CFTC), API pública Socrata, sin key, sin
    registro — dato oficial del gobierno de EEUU. Mide el posicionamiento
    neto de los grandes especuladores (non-commercial) en futuros del
    E-mini S&P 500, como proxy institucional real (mejor que Fear & Greed,
    que solo mide sentimiento agregado del mercado, no posicionamiento)."""
    ck = f"cot_sp500:{semanas}"
    cached = cache_get(ck)
    if cached is not None: return cached
    def _do():
        r = requests.get("https://publicreporting.cftc.gov/resource/jun7-fc8e.json",
                        params={
                            "$where": "upper(market_and_exchange_names) like '%E-MINI S&P 500%'",
                            "$order": "report_date_as_yyyy_mm_dd DESC",
                            "$limit": str(semanas),
                        }, timeout=15)
        if r.status_code != 200:
            raise RuntimeError(f"CFTC COT: HTTP {r.status_code}")
        data = r.json()
        if not data:
            raise RuntimeError("CFTC COT: sin datos para E-mini S&P 500")
        filas = []
        for d in data:
            try:
                largo = float(d["noncomm_positions_long_all"])
                corto = float(d["noncomm_positions_short_all"])
                filas.append({"fecha": d["report_date_as_yyyy_mm_dd"][:10], "net": largo - corto})
            except (KeyError, ValueError):
                continue
        if not filas:
            raise RuntimeError(f"CFTC COT: no se pudo parsear ninguna fila de {len(data)} recibidas")
        return filas
    res = with_retry(_do, tries=2, base_delay=2, what="fetch_cot_sp500")
    if res: cache_set(ck, res)
    return res

def calcular_cot_score():
    filas = fetch_cot_sp500()
    if not filas:
        return None
    actual = filas[0]
    valores = [f["net"] for f in filas]
    percentil = sum(1 for v in valores if v <= actual["net"]) / len(valores) * 100
    # Cuanto más bajo el percentil (posicionamiento neto más bajista de lo
    # habitual en los últimos ~3 años), más "pánico institucional real".
    score = max(0.0, min(10.0, (30 - percentil) / 30 * 10))
    return {"fecha": actual["fecha"], "net": actual["net"], "percentil": round(percentil, 1),
            "score": round(score, 1), "n_semanas": len(filas)}

def calcular_suelo_mercado():
    vix = None
    vix_d = get_quote("^VIX")
    if vix_d:
        vix = vix_d["price"]
    else:
        spy_d = get_quote("SPY")
        if spy_d:
            rets = spy_d["closes"].pct_change().dropna()
            window = min(20, len(rets))
            if window >= 5:
                vix = round(float(rets.tail(window).std() * (252**0.5) * 100), 1)
    aaii = fetch_aaii_sentiment()
    fg = get_fear_greed()
    cot = calcular_cot_score()
    if vix is None and aaii is None and fg is None and cot is None:
        return None

    componentes = {}
    if vix is not None:
        score = max(0, min(10, (vix - 15) / 20 * 10))
        componentes["VIX (volatilidad)"] = {"score": round(score, 1), "valor": f"{vix:.1f}"}
    if aaii is not None:
        spread = aaii["bearish"] - aaii["bullish"]
        score = max(0, min(10, spread / 50 * 10))
        componentes["AAII (retail)"] = {"score": round(score, 1),
                                        "valor": f"Bull {aaii['bullish']:.1f}% / Bear {aaii['bearish']:.1f}%"}
    if cot is not None:
        componentes["COT (posicionamiento institucional)"] = {
            "score": cot["score"],
            "valor": f"Percentil {cot['percentil']}% ({cot['n_semanas']} sem., {cot['fecha']})"}
    if fg is not None:
        score = max(0, min(10, (50 - fg["valor"]) / 50 * 10))
        componentes["Fear & Greed (sentimiento general)"] = {"score": round(score, 1),
                                                              "valor": f"{fg['valor']}/100 ({fg['texto']})"}

    TOTAL_INDICADORES = 4
    n_extremos = sum(1 for c in componentes.values() if c["score"] >= 7)
    faltantes = []
    if vix is None: faltantes.append("VIX")
    if aaii is None: faltantes.append("AAII")
    if cot is None: faltantes.append("COT")
    if fg is None: faltantes.append("Fear & Greed")

    if faltantes:
        veredicto = (f"INCOMPLETO — falta {', '.join(faltantes)} esta vez (fallo puntual de la fuente); "
                     f"veredicto calculado solo con {len(componentes)}/{TOTAL_INDICADORES} indicadores")
    elif n_extremos == TOTAL_INDICADORES:
        veredicto = "ALINEACIÓN COMPLETA — suelo de sentimiento en los 4 indicadores"
    elif n_extremos >= 2:
        veredicto = "ALINEACIÓN PARCIAL — algunos indicadores en pánico, no todos"
    else:
        veredicto = "SIN ALINEACIÓN — no hay pánico generalizado ahora mismo"

    return {"componentes": componentes, "veredicto": veredicto, "n_extremos": n_extremos,
            "faltantes": faltantes, "aaii_fecha": aaii["fecha"] if aaii else None}

def chart_suelo_gauge(res):
    """Velocímetro estilo /valor: traduce los 4 indicadores de pánico a un
    único 0-100 de 'oportunidad de compra' (lógica contraria: más pánico
    en los indicadores = más cerca de comprar; menos pánico/más euforia =
    más cerca de tener cautela)."""
    scores = [c["score"] for c in res["componentes"].values()]
    score = sum(scores) / len(scores) * 10 if scores else 50  # 0-10 -> 0-100

    fig = plt.figure(figsize=(10, 6.5))
    fig.patch.set_facecolor('#0d1117')
    ax = fig.add_axes([0.05, 0.12, 0.90, 0.72], projection='polar')
    ax.set_facecolor('#0d1117')
    theta = np.linspace(np.pi, 0, 101)
    for i in range(100):
        if i < 30:    c = '#FF3333'
        elif i < 45:  c = '#FF7700'
        elif i < 65:  c = '#FFCC00'
        elif i < 80:  c = '#99DD00'
        else:         c = '#00CC44'
        ax.barh(1, theta[i]-theta[i+1], left=theta[i+1], height=0.45, color=c, edgecolor='none')
    angle = np.pi - (score/100*np.pi)
    ax.plot([angle, angle], [0, 1.10], color='white', linewidth=6, zorder=5)
    ax.plot(angle, 0, 'o', color='white', markersize=22, zorder=6)
    ax.plot(angle, 0, 'o', color='#0d1117', markersize=11, zorder=7)
    ax.set_ylim(0, 1.35); ax.set_theta_zero_location('E'); ax.set_theta_direction(1)
    ax.set_thetamin(0); ax.set_thetamax(180)
    ax.set_xticks([np.pi, 3*np.pi/4, np.pi/2, np.pi/4, 0])
    ax.set_xticklabels(['0\nCAUTELA', '25', '50\nNEUTRAL', '75', '100\nOPORTUNIDAD'],
                       color='white', fontsize=9, fontweight='bold')
    ax.set_yticks([]); ax.spines['polar'].set_visible(False); ax.grid(False)

    if score >= 80:   zona, zc = "PÁNICO GENERALIZADO — zona de oportunidad histórica", '#00CC44'
    elif score >= 65: zona, zc = "PÁNICO ELEVADO — buena zona para mirar entradas", '#99DD00'
    elif score >= 45: zona, zc = "NEUTRAL — sin señal clara", '#FFCC00'
    elif score >= 30: zona, zc = "POCO PÁNICO — precaución, mercado tranquilo", '#FF7700'
    else:             zona, zc = "SIN PÁNICO / EUFORIA — cuidado con sobrecompra", '#FF3333'

    fig.text(0.5, 0.19, f"{score:.0f}/100", ha='center', va='center', fontsize=32,
             color='white', fontweight='bold')
    fig.text(0.5, 0.135, zona, ha='center', va='center', fontsize=11, color=zc, fontweight='bold')
    fig.text(0.5, 0.97, "MEDIDOR — ¿MOMENTO DE COMPRAR O DE CAUTELA?",
             ha='center', fontsize=14, color='white', fontweight='bold')
    fig.text(0.5, 0.02,
             "Lógica contraria: a más pánico en VIX/AAII/COT/Fear&Greed, más cerca del extremo "
             "'oportunidad' — no al revés.", ha='center', fontsize=8, color='#888888')

    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=130, facecolor='#0d1117', bbox_inches='tight')
    plt.close()
    buf.seek(0)
    return buf

def chart_suelo_mercado(res):
    comp = res["componentes"]; n = len(comp)
    fig = plt.figure(figsize=(11, 3 + n*1.3))
    fig.patch.set_facecolor('#0d1117')
    ax = fig.add_axes([0.32, 0.12, 0.6, 0.72])
    ax.set_facecolor('#0d1117')
    ax.set_xlim(0, 10); ax.set_ylim(-0.5, n-0.5)
    for idx, (nombre, datos) in enumerate(reversed(list(comp.items()))):
        y = idx; score = datos["score"]
        ax.barh(y, 10, height=0.5, color='#1a1a2e', zorder=1)
        c = '#00CC44' if score < 4 else '#FFCC00' if score < 7 else '#FF3333'
        ax.barh(y, max(score, 0.25), height=0.5, color=c, zorder=2)  # mínimo visible, aunque el score real sea 0
        ax.text(-0.3, y, nombre, va='center', ha='right', color='white',
                fontsize=11, fontweight='bold', transform=ax.transData)
        ax.text(10.3, y, f"{score}/10", va='center', ha='left', color=c,
                fontsize=11, fontweight='bold')
        ax.text(0.15, y-0.32, datos["valor"], va='top', ha='left', color='#999999', fontsize=8.5)
    ax.axis('off')
    fig.text(0.5, 0.96, "TRIPLE SUELO DE SENTIMIENTO", ha='center', color='white',
             fontsize=15, fontweight='bold')
    if res.get("faltantes"):
        vc = '#999999'
    else:
        vc = '#00CC44' if res["n_extremos"] < 2 else '#FFCC00' if res["n_extremos"] < 3 else '#FF3333'
    fig.text(0.5, 0.04, res["veredicto"], ha='center', color=vc, fontsize=11, fontweight='bold')
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=130, facecolor='#0d1117', bbox_inches='tight')
    plt.close()
    buf.seek(0)
    return buf

@bot.message_handler(commands=["suelo"])
@con_dyor
def cmd_suelo(msg):
    if not is_premium(msg.from_user.id):
        safe_send(msg.chat.id, "Necesitas suscripción activa.\n\n/trial — 7 días gratis\n/premium — 5€/mes")
        return
    m = bot.send_message(msg.chat.id, "Consultando VIX, AAII, COT y Fear & Greed... (10-15s)")
    res = calcular_suelo_mercado()
    if not res:
        safe_send(msg.chat.id, "No he podido obtener ninguno de los cuatro indicadores ahora mismo.",
                  message_id=m.message_id)
        return
    try:
        gauge = chart_suelo_gauge(res)
        try:
            bot.delete_message(msg.chat.id, m.message_id)
        except Exception:
            pass
        bot.send_photo(msg.chat.id, gauge)
    except Exception as e:
        log.warning(f"chart_suelo_gauge: {e}")
        try:
            bot.delete_message(msg.chat.id, m.message_id)
        except Exception:
            pass

    try:
        chart = chart_suelo_mercado(res)
        bot.send_photo(msg.chat.id, chart)
    except Exception as e:
        log.warning(f"chart_suelo_mercado: {e}")
        lines = [f"{k}: {v['score']}/10 ({v['valor']})" for k, v in res["componentes"].items()]
        safe_send(msg.chat.id, "\n".join(lines) + f"\n\n{res['veredicto']}")

    # Explicación en texto plano de qué mide cada cosa y qué implica el
    # veredicto — las barras solas no dejan claro el "por qué".
    explicacion = ["📖 QUÉ SIGNIFICA CADA INDICADOR\n"]
    if res.get("faltantes"):
        explicacion.append(
            f"⚠️ Esta vez ha fallado la consulta de: {', '.join(res['faltantes'])} — el veredicto de "
            f"abajo se ha calculado solo con {len(res['componentes'])} de los 4 indicadores. "
            "Prueba /suelo de nuevo en un momento para tener la lectura completa.\n")
    explicacion.append(
        "• VIX: mide el miedo a través de la compra de opciones de protección. "
        "Por encima de 30 suele coincidir con ventas de pánico.")
    explicacion.append(
        "• AAII: encuesta semanal a inversores particulares de EEUU. Cuando los "
        "bajistas superan el 50% y los alcistas caen por debajo del 20%, es señal "
        "clásica de pánico minorista (indicador contrario: suele ser tardío en la caída).")
    explicacion.append(
        "• COT: informe semanal oficial de la CFTC (gobierno de EEUU). Mide el "
        "posicionamiento neto real de los grandes especuladores en futuros del S&P 500 "
        "— el ángulo institucional de verdad (el NAAIM hacía algo parecido, pero pasó "
        "a ser de pago desde agosto de 2026).")
    explicacion.append(
        "• Fear & Greed: sentimiento agregado del mercado en general, como complemento "
        "a los otros tres.")
    explicacion.append(f"\n{res['veredicto']}")
    if res["n_extremos"] >= 2:
        explicacion.append(
            "\nCuando varios de estos indicadores llegan a extremos a la vez, "
            "históricamente es la zona donde suelen formarse suelos de mercado — "
            "no es una garantía, pero sí una señal a vigilar de cerca.")
    else:
        explicacion.append(
            "\nTodavía no hay suficiente pánico acumulado en estos indicadores como "
            "para hablar de una señal de suelo clásica.")
    safe_send(msg.chat.id, "\n".join(explicacion))

    # FIX: el prompt anterior mandaba "VIX (volatilidad): 0/10 — 9.2",
    # formato ambiguo que llevó a la IA a interpretar mal la dirección de
    # la escala (dijo "VIX alto indica miedo" cuando 9.2 es un valor muy
    # BAJO, sin pánico) — probablemente por apoyarse en su conocimiento
    # general de "VIX alto = miedo" sin parsear bien nuestros números.
    # Ahora se lo damos ya interpretado en texto plano, sin dejarle nada
    # a la inferencia.
    def _interpretar(score):
        if score < 3: return "TRANQUILO, sin señales de pánico"
        if score < 7: return "MODERADO"
        return "EN PÁNICO / ESTRÉS ALTO"
    comp_txt = "\n".join(
        f"{k}: valor real = {v['valor']} → {_interpretar(v['score'])} "
        f"(score interno {v['score']}/10, donde 0=sin pánico y 10=pánico máximo)"
        for k, v in res["componentes"].items())
    prompt = (f"Indicadores de pánico de mercado ahora mismo, YA INTERPRETADOS — usa estas "
              f"interpretaciones tal cual, no las reinterpretes ni las contradigas:\n{comp_txt}\n\n"
              f"Veredicto del modelo: {res['veredicto']}\n\n"
              "Nota: el COT es el informe oficial de la CFTC (gobierno de EEUU) sobre posicionamiento "
              "real de grandes especuladores en futuros del S&P 500 — el ángulo institucional real "
              "(sustituye al NAAIM, que pasó a ser de pago desde agosto 2026).\n\n"
              "1. ¿Qué tan fiable es esta combinación de 4 indicadores para detectar un suelo real?\n"
              "2. Si hay alineación parcial o completa, ¿qué habría que vigilar para confirmarlo?\n"
              "3. Riesgo de actuar solo con esta señal")
    safe_send(msg.chat.id, f"ANÁLISIS IA\n\n{ask_ai(prompt)}")


# ═══ /CURVA — Curva de tipos EEUU (10 años vs 2 años) ═══════════
# El indicador de recesión más vigilado históricamente: cuando el bono a
# 2 años paga más que el de 10, el mercado espera que la Fed tenga que
# bajar tipos por debilidad económica futura — la curva se "invierte".
# Reutiliza FRED (ya en /macro), pidiendo la serie completa en vez de solo
# el último dato, para poder marcar desde cuándo está invertida (o no).

def fetch_fred_series_range(series_id, limit=500):
    if not FRED_API_KEY:
        return None
    try:
        # FIX: pedíamos sort_order="asc" con un límite — eso trae los N
        # datos MÁS ANTIGUOS de la serie (que arranca en los años 60-70),
        # no los más recientes. Con "desc" sí traemos los últimos N datos
        # (el orden dentro del diccionario no importa, solo qué fechas
        # capturamos).
        r = requests.get("https://api.stlouisfed.org/fred/series/observations",
                        params={"series_id": series_id, "api_key": FRED_API_KEY,
                                "file_type": "json", "sort_order": "desc", "limit": limit},
                        timeout=15)
        if r.status_code != 200:
            log.warning(f"fetch_fred_series_range {series_id}: HTTP {r.status_code}")
            return None
        obs = [o for o in r.json().get("observations", []) if o.get("value") not in (".", None, "")]
        return {o["date"]: float(o["value"]) for o in obs}
    except Exception as e:
        log.warning(f"fetch_fred_series_range {series_id}: {e}")
        return None

def calcular_curva_tipos():
    dgs10 = fetch_fred_series_range("DGS10", limit=500)
    dgs2 = fetch_fred_series_range("DGS2", limit=500)
    if not dgs10 or not dgs2:
        return None
    fechas_comunes = sorted(set(dgs10.keys()) & set(dgs2.keys()))
    if not fechas_comunes:
        return None
    spread = [{"fecha": f, "valor": round(dgs10[f] - dgs2[f], 3)} for f in fechas_comunes]

    actual = spread[-1]
    invertida_ahora = actual["valor"] < 0

    # Buscar desde cuándo lleva en el estado actual (invertida o no)
    desde = actual["fecha"]
    for punto in reversed(spread):
        if (punto["valor"] < 0) != invertida_ahora:
            break
        desde = punto["fecha"]
    import datetime as dt
    dias_en_estado = (dt.date.fromisoformat(actual["fecha"]) - dt.date.fromisoformat(desde)).days

    return {"spread": spread, "actual": actual["valor"], "fecha": actual["fecha"],
            "invertida": invertida_ahora, "desde": desde, "dias_en_estado": dias_en_estado}

def chart_curva_tipos(res):
    puntos = res["spread"][-260:]  # ~1 año de sesiones
    fechas = [datetime.strptime(p["fecha"], "%Y-%m-%d") for p in puntos]
    valores = [p["valor"] for p in puntos]

    fig, ax = plt.subplots(figsize=(12, 6.5))
    fig.patch.set_facecolor('#0d1117')
    ax.set_facecolor('#0d1117')

    ax.axhline(0, color='#666666', linewidth=1, zorder=2)
    ax.fill_between(fechas, valores, 0, where=[v < 0 for v in valores],
                    color='#FF3333', alpha=0.35, zorder=1, interpolate=True)
    ax.fill_between(fechas, valores, 0, where=[v >= 0 for v in valores],
                    color='#00CC44', alpha=0.25, zorder=1, interpolate=True)
    ax.plot(fechas, valores, color='white', linewidth=1.6, zorder=3)

    ultimo_color = '#FF3333' if res["invertida"] else '#00CC44'
    ax.plot(fechas[-1], valores[-1], 'o', color=ultimo_color, markersize=12, zorder=5,
           markeredgecolor='white', markeredgewidth=2)
    ax.annotate(f"{res['actual']:+.2f} pp", xy=(fechas[-1], valores[-1]),
               xytext=(fechas[-1], valores[-1] + (0.15 if valores[-1] >= 0 else -0.15)),
               fontsize=12, color=ultimo_color, fontweight='bold', ha='right',
               bbox=dict(boxstyle='round,pad=0.3', facecolor='#0d1117', edgecolor=ultimo_color, alpha=0.95))

    ax.set_title('CURVA DE TIPOS EEUU — Bono 10 años menos Bono 2 años', color='white',
                fontsize=14, fontweight='bold', loc='left', pad=12)
    ax.set_ylabel('Diferencia (puntos porcentuales)', color='#AAAAAA')
    ax.tick_params(colors='#AAAAAA')
    for spine in ax.spines.values(): spine.set_color('#333333')
    ax.grid(color='#222222', linestyle='--', alpha=0.3)
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%b %Y'))
    fig.autofmt_xdate()

    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=130, facecolor='#0d1117', bbox_inches='tight')
    plt.close()
    buf.seek(0)
    return buf

@bot.message_handler(commands=["curva"])
@con_dyor
def cmd_curva(msg):
    if not is_premium(msg.from_user.id):
        safe_send(msg.chat.id, "Necesitas suscripción activa.\n\n/trial — 7 días gratis\n/premium — 5€/mes")
        return
    if not FRED_API_KEY:
        safe_send(msg.chat.id,
            "Falta configurar FRED_API_KEY en el servidor.\n\n"
            "Clave gratis en: https://fred.stlouisfed.org/docs/api/api_key.html")
        return
    m = bot.send_message(msg.chat.id, "Consultando curva de tipos (FRED)... (10-15s)")
    res = calcular_curva_tipos()
    if not res:
        safe_send(msg.chat.id, "No he podido obtener la curva de tipos ahora mismo.",
                  message_id=m.message_id)
        return
    try:
        chart = chart_curva_tipos(res)
        bot.delete_message(msg.chat.id, m.message_id)
        bot.send_photo(msg.chat.id, chart)
    except Exception as e:
        log.warning(f"chart_curva_tipos: {e}")
        safe_send(msg.chat.id, f"Spread 10a-2a: {res['actual']:+.2f} pp ({res['fecha']})",
                  message_id=m.message_id)

    estado_txt = "INVERTIDA" if res["invertida"] else "NORMAL (no invertida)"
    safe_send(msg.chat.id,
        f"📖 CURVA DE TIPOS — {estado_txt}\n\n"
        f"Bono 10 años menos bono 2 años: {res['actual']:+.2f} puntos porcentuales ({res['fecha']})\n"
        f"En este estado desde: {res['desde']} ({res['dias_en_estado']} días)\n\n"
        "Cuando el bono a 2 años paga MÁS que el de 10 (spread negativo = invertida), "
        "el mercado espera que la Fed tenga que bajar tipos por debilidad económica futura. "
        "Es el indicador de recesión más vigilado de la historia — ha precedido a todas las "
        "recesiones de EEUU desde los años 50, aunque con un desfase que puede ir de meses "
        "a más de un año, y no siempre acierta (algún falso positivo).")

    prompt = (f"La curva de tipos EEUU (10 años menos 2 años) está en {res['actual']:+.2f} puntos "
              f"porcentuales a fecha {res['fecha']}, {'invertida' if res['invertida'] else 'no invertida'} "
              f"desde hace {res['dias_en_estado']} días.\n\n"
              "1. ¿Qué implica este estado concreto de la curva ahora mismo?\n"
              "2. Contexto histórico: ¿cuánto suele tardar en materializarse una recesión tras "
              "una inversión de este tipo?\n"
              "3. Qué otras señales conviene vigilar junto a esta")
    safe_send(msg.chat.id, f"ANÁLISIS IA\n\n{ask_ai(prompt)}")


# ═══ /INSIDERS TICKER — Compras de directivos (SEC Form 4) ══════
# Cuando varios directivos/consejeros compran acciones de su propia
# empresa (no venden, compran) a la vez, suele ser señal alcista fuerte —
# tienen información que el mercado no tiene. Datos oficiales de la SEC,
# misma infraestructura que ya usamos para /cartera (13F).

TICKER_CIK_MAP_FILE = os.environ.get("TICKER_CIK_MAP_FILE", _p("ticker_cik_map.json"))
_TICKER_CIK_MAP = None

def _cargar_ticker_cik_map():
    """Mapeo oficial ticker->CIK que publica la propia SEC (un solo fichero
    para las ~10.000 empresas cotizadas), cacheado en disco para no volver
    a descargarlo en cada consulta."""
    global _TICKER_CIK_MAP
    if _TICKER_CIK_MAP is not None:
        return _TICKER_CIK_MAP
    try:
        with open(TICKER_CIK_MAP_FILE, "r") as f:
            _TICKER_CIK_MAP = json.load(f)
            return _TICKER_CIK_MAP
    except Exception:
        pass
    try:
        r = requests.get("https://www.sec.gov/files/company_tickers.json",
                        headers=SEC_HEADERS, timeout=15)
        data = r.json()
        mapa = {v["ticker"].upper(): str(v["cik_str"]).zfill(10) for v in data.values()}
        _TICKER_CIK_MAP = mapa
        try:
            with open(TICKER_CIK_MAP_FILE, "w") as f:
                json.dump(mapa, f)
        except Exception as e:
            log.warning(f"_cargar_ticker_cik_map: no se pudo guardar en disco: {e}")
        return mapa
    except Exception as e:
        log.warning(f"_cargar_ticker_cik_map: {e}")
        return {}

def fetch_form4_recientes(ticker, limite=20):
    cik = _cargar_ticker_cik_map().get(ticker.upper())
    if not cik:
        return None
    try:
        r = requests.get("https://www.sec.gov/cgi-bin/browse-edgar",
                        params={"action": "getcompany", "CIK": cik, "type": "4",
                                "dateb": "", "owner": "include", "count": str(limite),
                                "output": "atom"},
                        headers=SEC_HEADERS, timeout=15)
        entradas = re.findall(r"<entry>.*?</entry>", r.text, re.DOTALL)
        filings = []
        for e in entradas:
            m_acc = re.search(r"accession-number>([\d\-]+)<", e)
            m_fecha = re.search(r"filing-date>([\d\-]+)<", e)
            if m_acc and m_fecha:
                filings.append({"accession": m_acc.group(1), "fecha": m_fecha.group(1)})
        return {"cik": cik, "filings": filings}
    except Exception as e:
        log.warning(f"fetch_form4_recientes {ticker}: {e}")
        return None

def _parsear_form4_xml(cik, accession):
    """Cada Form 4 es su propio documento XML con las transacciones. Solo
    nos interesan P (compra en mercado abierto) y S (venta en mercado
    abierto) — descartamos A (awards/grants, no son decisión del insider),
    opciones y ajustes fiscales, que son ruido para esta señal."""
    accn_nodash = accession.replace("-", "")
    try:
        idx = requests.get(
            f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accn_nodash}/index.json",
            headers=SEC_HEADERS, timeout=10).json()
        items = idx.get("directory", {}).get("item", [])
        for it in items:
            name = it.get("name", "")
            if not name.lower().endswith(".xml"):
                continue
            url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accn_nodash}/{name}"
            xr = requests.get(url, headers=SEC_HEADERS, timeout=10)
            xml = xr.text
            if "<rptOwnerName>" not in xml:
                continue  # no es el documento de propiedad (puede haber otros XML auxiliares)
            nombre_m = re.search(r"<rptOwnerName>(.*?)</rptOwnerName>", xml)
            nombre = nombre_m.group(1) if nombre_m else "Desconocido"
            transacciones = []
            for bloque in re.findall(r"<nonDerivativeTransaction>.*?</nonDerivativeTransaction>", xml, re.DOTALL):
                codigo_m = re.search(r"<transactionCode>(.*?)</transactionCode>", bloque)
                shares_m = re.search(r"<transactionShares>\s*<value>([\d.]+)</value>", bloque)
                precio_m = re.search(r"<transactionPricePerShare>\s*<value>([\d.]+)</value>", bloque)
                if codigo_m and codigo_m.group(1) in ("P", "S") and shares_m:
                    transacciones.append({
                        "codigo": codigo_m.group(1),
                        "shares": float(shares_m.group(1)),
                        "precio": float(precio_m.group(1)) if precio_m else 0,
                    })
            if transacciones:
                return {"nombre": nombre, "transacciones": transacciones}
    except Exception as e:
        log.warning(f"_parsear_form4_xml {cik}/{accession}: {e}")
    return None

def calcular_insiders(ticker):
    base = fetch_form4_recientes(ticker, limite=20)
    if not base or not base["filings"]:
        return None
    resultados = []
    for f in base["filings"][:15]:  # limitamos para no encadenar demasiadas peticiones
        parsed = _parsear_form4_xml(base["cik"], f["accession"])
        if parsed:
            for t in parsed["transacciones"]:
                resultados.append({"insider": parsed["nombre"], "fecha": f["fecha"],
                                    "codigo": t["codigo"], "shares": t["shares"], "precio": t["precio"]})
        time.sleep(0.2)
    if not resultados:
        return {"ticker": ticker.upper(), "compras": [], "ventas": [], "insiders_compradores": 0}
    compras = [r for r in resultados if r["codigo"] == "P"]
    ventas = [r for r in resultados if r["codigo"] == "S"]
    insiders_compradores = len(set(r["insider"] for r in compras))
    return {"ticker": ticker.upper(), "compras": compras, "ventas": ventas,
            "insiders_compradores": insiders_compradores}

@bot.message_handler(commands=["insiders"])
@con_dyor
def cmd_insiders(msg):
    if not is_premium(msg.from_user.id):
        safe_send(msg.chat.id, "Necesitas suscripción activa.\n\n/trial — 7 días gratis\n/premium — 5€/mes")
        return
    parts = msg.text.split()
    if len(parts) < 2:
        safe_send(msg.chat.id, "Uso: /insiders TICKER\n\nEjemplo: /insiders AAPL\n\n"
                                "Solo funciona con tickers de EEUU (los que reportan a la SEC).")
        return
    ticker = parts[1].upper()
    m = bot.send_message(msg.chat.id, f"Consultando Form 4 de {ticker} en la SEC... (15-25s)")
    res = calcular_insiders(ticker)
    if res is None:
        safe_send(msg.chat.id, f"No he encontrado \"{ticker}\" en el registro de la SEC. "
                                "Comprueba que sea un ticker de EEUU.", message_id=m.message_id)
        return

    lines = [f"👔 INSIDERS — {res['ticker']}",
             f"Últimas transacciones en mercado abierto (Form 4, últimas ~15 presentaciones)\n"]
    if res["compras"]:
        valor_total = sum(c["shares"]*c["precio"] for c in res["compras"])
        lines.append(f"🟢 COMPRAS: {len(res['compras'])} operaciones, "
                     f"{res['insiders_compradores']} insiders distintos, ~${valor_total/1e6:.2f}M")
        for c in res["compras"][:8]:
            lines.append(f"  • {c['insider']} — {c['shares']:,.0f} acc. a ${c['precio']:.2f} ({c['fecha']})")
    else:
        lines.append("🟢 COMPRAS: ninguna en este periodo")
    if res["ventas"]:
        valor_total = sum(v["shares"]*v["precio"] for v in res["ventas"])
        lines.append(f"\n🔴 VENTAS: {len(res['ventas'])} operaciones, ~${valor_total/1e6:.2f}M")
    else:
        lines.append("\n🔴 VENTAS: ninguna en este periodo")

    if res["insiders_compradores"] >= 3:
        lines.append(f"\n⚡ {res['insiders_compradores']} insiders distintos comprando en el mismo "
                     "periodo — señal de compra agrupada, más fuerte que una compra aislada.")
    safe_send(msg.chat.id, "\n".join(lines)[:4096], message_id=m.message_id)

    if not res["compras"] and not res["ventas"]:
        return  # sin transacciones reales que analizar, no llamamos a la IA con nada
    resumen = (f"{res['ticker']}: {len(res['compras'])} compras ({res['insiders_compradores']} insiders "
              f"distintos), {len(res['ventas'])} ventas, en las últimas ~15 presentaciones Form 4.")
    prompt = (f"Actividad de insiders (directivos/consejeros) en {res['ticker']}: {resumen}\n\n"
              "No inventes nombres ni cifras que no estén aquí.\n\n"
              "1. ¿Qué interpretación razonable tiene este patrón de compras/ventas?\n"
              "2. ¿Compra agrupada de varios insiders a la vez es más significativa que una compra "
              "aislada? ¿Por qué?\n"
              "3. Limitaciones de usar esto como señal (insiders también venden por motivos ajenos "
              "a la empresa: impuestos, diversificación, planes 10b5-1 automáticos...)")
    safe_send(msg.chat.id, f"ANÁLISIS IA\n\n{ask_ai(prompt)}")


# ═══ /CORRELACION — BTC vs Nasdaq (risk-on / risk-off) ══════════
# Mide si BTC se mueve pegado a las tech (mercado tratando a cripto como
# "activo de riesgo más") o si se ha desacoplado. FIX de fondo necesario:
# BTC cotiza 7 días/semana y Nasdaq solo entre semana, así que comparar
# "los últimos N valores" de cada serie sin más desalinea las fechas — hay
# que cruzar por fecha real y quedarnos solo con los días que Nasdaq
# cotizó de verdad.

def fetch_precio_fechas_stooq(ticker, dias=220):
    """Versión de fetch_stooq que conserva las fechas (fetch_stooq normal
    solo devuelve la serie de precios, sin fechas accesibles) — necesario
    aquí para poder cruzar por fecha real con el histórico de BTC.
    FIX: la primera versión solo tenía Stooq, sin ningún respaldo si
    fallaba — a diferencia del resto del bot, que siempre encadena
    Stooq -> Twelve Data -> yfinance. Le añadimos el mismo respaldo con
    Twelve Data (que también trae fecha por cada dato)."""
    import datetime as dt
    st = STOOQ_MAP.get(ticker, f"{ticker.lower()}.us")
    ck = f"stooq_fechas:{ticker}"
    cached = cache_get(ck)
    if cached is not None: return cached
    def _do_stooq():
        d1 = (dt.date.today()-dt.timedelta(days=dias+30)).strftime("%Y%m%d")
        d2 = dt.date.today().strftime("%Y%m%d")
        url = f"https://stooq.com/q/d/l/?s={st}&d1={d1}&d2={d2}&i=d"
        r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200 or "No data" in r.text or len(r.text) < 50:
            raise RuntimeError("sin datos en Stooq")
        from io import StringIO
        df = pd.read_csv(StringIO(r.text))
        if df.empty or len(df) < 10:
            raise RuntimeError("Stooq: pocos datos")
        df = df.sort_values("Date")
        return {"fechas": list(df["Date"]), "closes": df["Close"].astype(float).tolist()}
    res = with_retry(_do_stooq, tries=2, base_delay=2, what=f"fetch_precio_fechas_stooq {ticker}")
    if res is None and TWELVEDATA_API_KEY:
        def _do_td():
            sym = TWELVEDATA_SYMBOL_MAP.get(ticker, ticker)
            _throttle_twelvedata()
            r = requests.get("https://api.twelvedata.com/time_series",
                            params={"symbol": sym, "interval": "1day", "outputsize": dias,
                                    "apikey": TWELVEDATA_API_KEY}, timeout=10)
            j = r.json()
            if j.get("status") == "error" or "values" not in j:
                raise RuntimeError(f"Twelve Data: {j.get('message', 'sin datos')}")
            vals = list(reversed(j["values"]))
            if len(vals) < 10:
                raise RuntimeError("Twelve Data: pocos datos")
            return {"fechas": [v["datetime"] for v in vals],
                    "closes": [float(v["close"]) for v in vals]}
        res = with_retry(_do_td, tries=2, base_delay=2, what=f"fetch_precio_fechas_stooq(TD) {ticker}")
    if res: cache_set(ck, res)
    return res

def calcular_correlacion_btc_nasdaq(ventana=30):
    btc_hist = fetch_btc_price_history_long(days=200)
    nasdaq = fetch_precio_fechas_stooq("^IXIC", dias=200)
    if not nasdaq:
        nasdaq = fetch_precio_fechas_stooq("QQQ", dias=200)  # mismo fallback que ya usamos en /ticker
    if not btc_hist or not nasdaq:
        return None

    btc_por_fecha = {f.strftime("%Y-%m-%d"): float(c) for f, c in
                     zip(btc_hist["fechas"], btc_hist["closes"])}
    nasdaq_por_fecha = dict(zip(nasdaq["fechas"], nasdaq["closes"]))

    # Solo días donde AMBOS cotizaron de verdad — evita el desajuste de
    # fin de semana que tendría comparar "los últimos N valores" sin más.
    fechas_comunes = sorted(set(btc_por_fecha) & set(nasdaq_por_fecha))
    if len(fechas_comunes) < ventana + 5:
        return None

    btc_serie = pd.Series([btc_por_fecha[f] for f in fechas_comunes])
    nasdaq_serie = pd.Series([nasdaq_por_fecha[f] for f in fechas_comunes])
    btc_ret = btc_serie.pct_change().dropna()
    nasdaq_ret = nasdaq_serie.pct_change().dropna()

    corr_actual = round(float(btc_ret.tail(ventana).corr(nasdaq_ret.tail(ventana))), 2)
    corr_larga = round(float(btc_ret.tail(90).corr(nasdaq_ret.tail(90))), 2) if len(btc_ret) >= 90 else None

    # Serie de correlación rodante, para el gráfico de tendencia
    rodante = []
    for i in range(ventana, len(btc_ret)):
        c = btc_ret.iloc[i-ventana:i].corr(nasdaq_ret.iloc[i-ventana:i])
        rodante.append({"fecha": fechas_comunes[i+1], "valor": round(float(c), 3)})

    return {"corr_actual": corr_actual, "corr_larga": corr_larga, "ventana": ventana,
            "rodante": rodante, "n_dias": len(fechas_comunes)}

def chart_correlacion(res):
    puntos = res["rodante"][-180:]
    fechas = [datetime.strptime(p["fecha"], "%Y-%m-%d") for p in puntos]
    valores = [p["valor"] for p in puntos]

    fig, ax = plt.subplots(figsize=(12, 6.5))
    fig.patch.set_facecolor('#0d1117')
    ax.set_facecolor('#0d1117')

    ax.axhline(0, color='#666666', linewidth=1, zorder=2)
    ax.axhline(0.5, color='#333333', linewidth=1, linestyle='--', zorder=2)
    ax.axhline(-0.5, color='#333333', linewidth=1, linestyle='--', zorder=2)
    ax.fill_between(fechas, valores, 0, where=[v >= 0 for v in valores],
                    color='#FF9900', alpha=0.30, zorder=1, interpolate=True)
    ax.fill_between(fechas, valores, 0, where=[v < 0 for v in valores],
                    color='#3388FF', alpha=0.30, zorder=1, interpolate=True)
    ax.plot(fechas, valores, color='white', linewidth=1.6, zorder=3)

    ax.plot(fechas[-1], valores[-1], 'o', color='#F7931A', markersize=12, zorder=5,
           markeredgecolor='white', markeredgewidth=2)
    ax.annotate(f"{res['corr_actual']:+.2f}", xy=(fechas[-1], valores[-1]),
               xytext=(fechas[-1], valores[-1] + (0.08 if valores[-1] >= 0 else -0.08)),
               fontsize=12, color='#F7931A', fontweight='bold', ha='right',
               bbox=dict(boxstyle='round,pad=0.3', facecolor='#0d1117', edgecolor='#F7931A', alpha=0.95))

    ax.set_ylim(-1.05, 1.05)
    ax.set_title(f'BTC vs NASDAQ — Correlación rodante ({res["ventana"]} días)', color='white',
                fontsize=14, fontweight='bold', loc='left', pad=12)
    ax.set_ylabel('Coeficiente de correlación', color='#AAAAAA')
    ax.tick_params(colors='#AAAAAA')
    for spine in ax.spines.values(): spine.set_color('#333333')
    ax.grid(color='#222222', linestyle='--', alpha=0.3)
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%b %Y'))
    fig.autofmt_xdate()

    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=130, facecolor='#0d1117', bbox_inches='tight')
    plt.close()
    buf.seek(0)
    return buf

@bot.message_handler(commands=["correlacion"])
@con_dyor
def cmd_correlacion(msg):
    if not is_premium(msg.from_user.id):
        safe_send(msg.chat.id, "Necesitas suscripción activa.\n\n/trial — 7 días gratis\n/premium — 5€/mes")
        return
    m = bot.send_message(msg.chat.id, "Calculando correlación BTC-Nasdaq... (10-15s)")
    res = calcular_correlacion_btc_nasdaq()
    if not res:
        safe_send(msg.chat.id, "No he podido calcular la correlación ahora mismo (datos insuficientes).",
                  message_id=m.message_id)
        return
    try:
        chart = chart_correlacion(res)
        bot.delete_message(msg.chat.id, m.message_id)
        bot.send_photo(msg.chat.id, chart)
    except Exception as e:
        log.warning(f"chart_correlacion: {e}")
        safe_send(msg.chat.id, f"Correlación BTC-Nasdaq ({res['ventana']}d): {res['corr_actual']:+.2f}",
                  message_id=m.message_id)

    if res["corr_actual"] >= 0.6:
        lectura = "ALTA — BTC se mueve muy pegado al Nasdaq (mercado tratando a cripto como activo de riesgo más)"
    elif res["corr_actual"] >= 0.3:
        lectura = "MODERADA — cierta relación con las tech, pero con movimiento propio"
    elif res["corr_actual"] >= -0.3:
        lectura = "BAJA / SIN RELACIÓN CLARA — BTC se está moviendo bastante por su cuenta"
    else:
        lectura = "NEGATIVA — BTC moviéndose en dirección contraria al Nasdaq (poco habitual)"

    comparacion = ""
    if res["corr_larga"] is not None:
        diff = res["corr_actual"] - res["corr_larga"]
        if abs(diff) >= 0.15:
            comparacion = (f"\n\nHa cambiado bastante frente a los últimos 90 días "
                          f"({res['corr_larga']:+.2f}) — {'subiendo' if diff>0 else 'bajando'} la correlación.")

    linea_90d = f"Últimos 90 días: {res['corr_larga']:+.2f}\n" if res["corr_larga"] is not None else ""
    safe_send(msg.chat.id,
        f"📖 CORRELACIÓN BTC vs NASDAQ\n\n"
        f"Últimos {res['ventana']} días: {res['corr_actual']:+.2f}\n"
        f"{linea_90d}\n"
        f"Lectura: {lectura}{comparacion}\n\n"
        "Escala: +1 = se mueven exactamente igual, 0 = sin relación, -1 = se mueven exactamente al "
        "revés. Cuando la correlación sube mucho, suele ser señal de que el mercado está en modo "
        "'risk-on/risk-off' generalizado (todo sube o baja junto por sentimiento, no por fundamentales "
        "propios de cada activo).")

    prompt = (f"Correlación entre BTC y Nasdaq en los últimos {res['ventana']} días: {res['corr_actual']:+.2f} "
              f"(últimos 90 días: {res['corr_larga']})\n\n"
              "1. ¿Qué implica este nivel de correlación para un inversor con exposición a ambos?\n"
              "2. Si la correlación ha subido o bajado mucho recientemente, ¿qué podría explicarlo?\n"
              "3. ¿Cómo se debería interpretar esto junto al resto de indicadores del bot (Fear&Greed, VIX)?")
    safe_send(msg.chat.id, f"ANÁLISIS IA\n\n{ask_ai(prompt)}")


# ═══ /GUIA — Explicación de cada comando ════════════════════════
GUIA_PARTES = [
"""📖 GUÍA DE COMANDOS (1/3) — Análisis de precio y ciclos

━━━ /valor TICKER ━━━
Velocímetro 0-100 de "barato/caro" para un activo concreto. Combina EMA200, RSI, distancia al máximo/mínimo y, en cripto, Fear & Greed, funding, DXY, Google Trends y ciclo del halving.
Ejemplo: /valor BTC-USD, /valor TSLA

━━━ /fundamental TICKER ━━━
Velocímetro 0-100 de calidad fundamental de una empresa (solo acciones). 5 categorías: Valoración, Salud Financiera, Rentabilidad, Crecimiento, Potencial LP.
Ejemplo: /fundamental NVDA

━━━ /halvingbtc ━━━
Gráfico del ciclo de 4 años de BTC (halvings históricos + proyección).

━━━ /ciclo ━━━
BTC situado sobre la curva Pico→Contracción→Suelo→Expansión→Recuperación→Prosperidad. Usa el máximo y mínimo REALES de este ciclo, no supuestos.""",

"""📖 GUÍA DE COMANDOS (2/3) — Sentimiento y datos en vivo

━━━ /dominancia ━━━
Fear & Greed Index de BTC con histórico desde 2018, zonas de compra/venta.

━━━ /suelo ━━━
El más completo: VIX + AAII (retail) + COT (institucional real, CFTC) + Fear & Greed, combinados en un medidor 0-100. Mide el ánimo del MERCADO EN GENERAL, no una acción concreta.

━━━ /ballenas TICKER ━━━
Muros de compra/venta grandes en el order book (solo cripto).

━━━ /cartera NOMBRE ━━━
Cartera trimestral (13F) de grandes inversores — Buffett, Ackman, Burry y 15 más. Datos oficiales SEC.

━━━ /insiders TICKER ━━━
Compras/ventas de directivos en mercado abierto (SEC Form 4). Avisa si hay compra agrupada (3+ insiders a la vez).

━━━ /macro ━━━
Tipos Fed, inflación, paro, bonos (FRED) + derivados cripto (Binance).

━━━ /curva ━━━
Curva de tipos EEUU (10 años vs 2 años) — indicador de recesión más vigilado históricamente.

━━━ /correlacion ━━━
Correlación BTC vs Nasdaq — mide si cripto se mueve pegado a las tech (risk-on/risk-off).

━━━ /fuerza ━━━
Compara 100 criptos contra BTC en 3 plazos: 24 horas, 7 días y ~200 días — detecta cuáles lideran solo hoy o esta semana vs cuáles llevan meses haciéndolo (señal más sólida). No predice el futuro, describe divergencias ya en marcha.""",

"""📖 GUÍA DE COMANDOS (3/3) — Noticias y automatizaciones

━━━ /noticias ━━━
Titulares de bolsa/economía/cripto de varias fuentes, con análisis de IA basado solo en los titulares reales.

━━━ /ticker ━━━
Resumen visual al momento de ~30 activos (cripto, acciones, ETFs, índices, oro).

━━━ Automatizaciones (sin comando) ━━━
• Cada 2h (9-21h): mismo resumen visual de /ticker, automático
• Cada mañana 8h: resumen diario (BTC, Fear&Greed, titulares)
• Alertas de noticias muy relevantes, cuando la IA las detecta

━━━ Suscripción ━━━
/trial — 7 días gratis
/premium — 5€/mes
/verificar HASH — confirmar pago manual
/mistatus — ver tu suscripción

━━━ Importante ━━━
Usa /dyor para leer el aviso legal antes de tomar decisiones con lo que veas aquí."""
]

@bot.message_handler(commands=["guia"])
def cmd_guia(msg):
    if not is_premium(msg.from_user.id):
        safe_send(msg.chat.id, "Necesitas suscripción activa.\n\n/trial — 7 días gratis\n/premium — 5€/mes")
        return
    for parte in GUIA_PARTES:
        safe_send(msg.chat.id, parte)
        time.sleep(0.3)


# ═══ /DYOR — Aviso legal / descargo de responsabilidad ══════════
TEXTO_DYOR = """⚠️ AVISO IMPORTANTE — LÉEME

Este bot es una herramienta de ANÁLISIS INFORMATIVO, no un servicio de asesoramiento financiero. Antes de usarlo, ten esto claro:

📊 No es una recomendación de inversión. Ningún comando (/valor, /suelo, /ciclo, análisis de IA...) te dice qué comprar, vender, o cuándo. Son indicadores y datos para que TÚ decidas con tu propio criterio.

🤖 La IA puede equivocarse. Los análisis generados son orientativos, no verdad absoluta.

📡 Los datos pueden fallar o tener errores. Este bot depende de fuentes gratuitas de terceros (Binance, Stooq, SEC, CFTC, FRED, AAII...). A veces fallan, se retrasan, o cambian sin avisar. Verifica cifras importantes antes de actuar.

💸 Invertir conlleva riesgo real de pérdida. Rendimientos pasados no garantizan resultados futuros. Nunca inviertas dinero que no puedas permitirte perder.

🧑‍💼 No somos asesores financieros regulados. Para decisiones importantes, consulta con un profesional cualificado.

En resumen: DYOR — Do Your Own Research. Usa este bot como una herramienta más en tu proceso de análisis, nunca como la única fuente de tu decisión."""

@bot.message_handler(commands=["dyor"])
def cmd_dyor(msg):
    safe_send(msg.chat.id, TEXTO_DYOR)


# ═══ /FUERZA — Fuerza relativa cripto vs BTC ════════════════════
# Detecta qué criptos aguantan o suben MIENTRAS el mercado en general cae
# (o suben más que el mercado cuando todo sube) — no predice el futuro,
# describe divergencias que ya están pasando ahora mismo. Es el mismo
# patrón que HYPE/PURR mostraron: mantenerse fuerte mientras el resto
# sufre suele preceder a un liderazgo sostenido, aunque no es garantía.
# CoinGecko top 100 por capitalización, gratis, sin key (ya lo usamos
# para HYPE/PURR).

STABLECOINS_EXCLUIR = {"usdt", "usdc", "dai", "fdusd", "tusd", "usde", "usds",
                       "pyusd", "usdp", "frax", "gusd", "usdd"}

def fetch_top_cryptos_cambios(n=100):
    ck = "cg_markets_top100"
    cached = cache_get(ck)
    if cached is not None: return cached
    def _do():
        _throttle_coingecko()
        r = requests.get("https://api.coingecko.com/api/v3/coins/markets",
                        params={"vs_currency": "usd", "order": "market_cap_desc",
                                "per_page": str(n), "page": "1",
                                "price_change_percentage": "24h,7d,30d,200d"},
                        timeout=15)
        if r.status_code != 200:
            raise RuntimeError(f"CoinGecko markets: HTTP {r.status_code}")
        data = r.json()
        if not data:
            raise RuntimeError("CoinGecko markets: sin datos")
        return data
    res = with_retry(_do, tries=3, base_delay=5, what="fetch_top_cryptos_cambios")
    if res: cache_set(ck, res)
    return res

def calcular_fuerza_relativa():
    data = fetch_top_cryptos_cambios(100)
    if not data:
        return None
    btc = next((c for c in data if c["symbol"].lower() == "btc"), None)
    if not btc:
        return None
    btc_7d = btc.get("price_change_percentage_7d_in_currency") or 0
    btc_30d = btc.get("price_change_percentage_30d_in_currency") or 0
    btc_200d = btc.get("price_change_percentage_200d_in_currency") or 0
    btc_24h = btc.get("price_change_percentage_24h_in_currency") or 0

    # FIX: valores como "+528pp a 7 días" o "+38.000pp a 200 días" no son
    # una señal real — son casi siempre microcaps con tan poca liquidez
    # que su precio de referencia era casi cero (dispara el % aunque no
    # signifique nada útil), o directamente un error de datos puntual de
    # CoinGecko para esa moneda. Sin filtrarlos, aplastan la escala del
    # gráfico y esconden la señal de verdad del resto.
    LIMITE_24H = 100
    LIMITE_7D = 200
    LIMITE_200D = 2000

    filas = []
    sin_dato = []
    anomalos = []
    for c in data:
        sym = c["symbol"].lower()
        if sym in STABLECOINS_EXCLUIR or sym == "btc":
            continue
        cambio_7d = c.get("price_change_percentage_7d_in_currency")
        cambio_200d = c.get("price_change_percentage_200d_in_currency")
        if cambio_7d is None:
            sin_dato.append(c["symbol"].upper())
            continue
        fuerza_7d = cambio_7d - btc_7d
        fuerza_200d = (cambio_200d - btc_200d) if cambio_200d is not None else None
        cambio_24h = c.get("price_change_percentage_24h_in_currency")
        fuerza_24h = (cambio_24h - btc_24h) if cambio_24h is not None else None
        if (abs(fuerza_7d) > LIMITE_7D or (fuerza_200d is not None and abs(fuerza_200d) > LIMITE_200D)
                or (fuerza_24h is not None and abs(fuerza_24h) > LIMITE_24H)):
            anomalos.append(c["symbol"].upper())
            continue
        filas.append({
            "nombre": c["name"], "simbolo": c["symbol"].upper(),
            "cambio_24h": cambio_24h,
            "fuerza_24h": fuerza_24h,
            "cambio_7d": cambio_7d,
            "cambio_30d": c.get("price_change_percentage_30d_in_currency"),
            "cambio_200d": cambio_200d,
            "fuerza_7d": fuerza_7d,
            # Si no hay dato de 200d (moneda muy nueva, p.ej. HYPE/PURR que
            # llevan menos de 200 días cotizando), no inventamos un cero —
            # lo dejamos en None y lo tratamos aparte.
            "fuerza_200d": fuerza_200d,
            "rank": c.get("market_cap_rank"),
        })
    filas.sort(key=lambda x: x["fuerza_7d"], reverse=True)
    top_corto = filas[:15]

    filas_24h = [f for f in filas if f["fuerza_24h"] is not None]
    filas_24h.sort(key=lambda x: x["fuerza_24h"], reverse=True)
    top_24h = filas_24h[:15]

    filas_200d = [f for f in filas if f["fuerza_200d"] is not None]
    filas_200d.sort(key=lambda x: x["fuerza_200d"], reverse=True)
    top_largo = filas_200d[:15]

    # La señal más sólida: fuerte en AMBOS plazos a la vez, no solo esta
    # semana — esto es justo lo que distingue "ruido de una semana" de
    # "liderazgo sostenido" (lo que preguntabas: la próxima reina del
    # bullrun suele llevar así varios meses, no solo unos días).
    simbolos_top_corto = {f["simbolo"] for f in top_corto}
    doble_fuerza = [f for f in top_largo if f["simbolo"] in simbolos_top_corto]

    # Sin dato de 200d (monedas demasiado nuevas para tener ese histórico,
    # como HYPE o PURR) — no las escondemos, las señalamos aparte.
    demasiado_nuevas = [f["simbolo"] for f in filas if f["fuerza_200d"] is None]

    simbolos_en_top100 = {c["symbol"].upper() for c in data}

    return {"btc_24h": btc_24h, "btc_7d": btc_7d, "btc_30d": btc_30d, "btc_200d": btc_200d,
            "top_24h": top_24h, "top_corto": top_corto, "top_largo": top_largo, "doble_fuerza": doble_fuerza,
            "demasiado_nuevas": demasiado_nuevas, "anomalos": anomalos, "todas": filas,
            "sin_dato": sin_dato, "simbolos_en_top100": simbolos_en_top100}

def buscar_moneda_en_fuerza(res, simbolo):
    simbolo = simbolo.upper()
    for f in res["todas"]:
        if f["simbolo"] == simbolo:
            pos_corto = res["todas"].index(f) + 1
            if f["fuerza_200d"] is not None:
                orden_largo = sorted([x for x in res["todas"] if x["fuerza_200d"] is not None],
                                     key=lambda x: x["fuerza_200d"], reverse=True)
                pos_largo = orden_largo.index(f) + 1
                return (f"{simbolo}: fuerza 7d {f['fuerza_7d']:+.1f}pp (puesto #{pos_corto}) | "
                       f"fuerza 200d {f['fuerza_200d']:+.1f}pp (puesto #{pos_largo})")
            return (f"{simbolo}: fuerza 7d {f['fuerza_7d']:+.1f}pp (puesto #{pos_corto}) | "
                   f"sin dato de 200 días (moneda demasiado nueva para ese histórico)")
    if simbolo in res["sin_dato"]:
        return f"{simbolo}: está en el top 100 por capitalización, pero CoinGecko no tiene su dato de 7 días ahora mismo"
    if simbolo not in res["simbolos_en_top100"]:
        return f"{simbolo}: no está en el top 100 por capitalización ahora mismo"
    return f"{simbolo}: no encontrado (motivo desconocido)"

def chart_fuerza_relativa(top, campo, titulo, btc_valor):
    fig, ax = plt.subplots(figsize=(12, 8))
    fig.patch.set_facecolor('#0d1117')
    ax.set_facecolor('#0d1117')

    nombres = [f["simbolo"] for f in top]
    valores = [f[campo] for f in top]
    colores = ['#00CC44' if v >= 0 else '#FF3333' for v in valores]
    y_pos = list(range(len(top)))

    ax.barh(y_pos, valores, color=colores, height=0.6, zorder=3)
    ax.axvline(0, color='#666666', linewidth=1, zorder=2)
    max_abs = max(abs(v) for v in valores) or 1
    for i, f in enumerate(top):
        v = f[campo]
        offset = max_abs * 0.03
        ax.text(v + (offset if v >= 0 else -offset), i, f"{v:+.1f}pp",
                va='center', ha='left' if v >= 0 else 'right',
                color=colores[i], fontweight='bold', fontsize=10)

    ax.set_yticks(y_pos)
    ax.set_yticklabels(nombres, color='white', fontsize=11, fontweight='bold')
    ax.invert_yaxis()
    ax.set_xlim(-max_abs*1.3, max_abs*1.3)
    ax.set_xticks([])
    for spine in ax.spines.values(): spine.set_visible(False)
    ax.tick_params(left=False)

    ax.set_title(f'{titulo} (BTC: {btc_valor:+.1f}%)',
                color='white', fontsize=14, fontweight='bold', loc='left', pad=15)

    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=130, facecolor='#0d1117', bbox_inches='tight')
    plt.close()
    buf.seek(0)
    return buf

@bot.message_handler(commands=["fuerza"])
@con_dyor
def cmd_fuerza(msg):
    if not is_premium(msg.from_user.id):
        safe_send(msg.chat.id, "Necesitas suscripción activa.\n\n/trial — 7 días gratis\n/premium — 5€/mes")
        return
    m = bot.send_message(msg.chat.id, "Comparando 100 criptos contra BTC (24h, 7 días y 200 días)... (10-15s)")
    res = calcular_fuerza_relativa()
    if not res:
        safe_send(msg.chat.id, "No he podido calcular la fuerza relativa ahora mismo.",
                  message_id=m.message_id)
        return

    try:
        bot.delete_message(msg.chat.id, m.message_id)
    except Exception:
        pass

    try:
        chart_24h = chart_fuerza_relativa(res["top_24h"], "fuerza_24h",
                                          "FUERZA RELATIVA vs BTC — últimas 24 horas", res["btc_24h"])
        bot.send_photo(msg.chat.id, chart_24h)
    except Exception as e:
        log.warning(f"chart_fuerza_relativa (24h): {e}")

    try:
        chart_corto = chart_fuerza_relativa(res["top_corto"], "fuerza_7d",
                                            "FUERZA RELATIVA vs BTC — últimos 7 días", res["btc_7d"])
        bot.send_photo(msg.chat.id, chart_corto)
    except Exception as e:
        log.warning(f"chart_fuerza_relativa (7d): {e}")

    try:
        chart_largo = chart_fuerza_relativa(res["top_largo"], "fuerza_200d",
                                            "FUERZA RELATIVA vs BTC — últimos ~200 días (~6-7 meses)",
                                            res["btc_200d"])
        bot.send_photo(msg.chat.id, chart_largo)
    except Exception as e:
        log.warning(f"chart_fuerza_relativa (200d): {e}")

    lines = [f"📖 FUERZA RELATIVA — top 100 por capitalización\n",
             f"BTC: 24h {res['btc_24h']:+.1f}% | 7d {res['btc_7d']:+.1f}% | 30d {res['btc_30d']:+.1f}% | ~200d {res['btc_200d']:+.1f}%\n",
             "Tres plazos, tres preguntas distintas:\n"
             "• 24 horas = ¿quién se mueve mejor HOY? (lo más ruidoso, cambia de un día a otro)\n"
             "• 7 días = ¿quién está fuerte ESTA semana? (puede ser ruido)\n"
             "• ~200 días = ¿quién lleva MESES aguantando o liderando? (señal mucho más sólida — "
             "esto es lo que suele distinguir a una futura 'reina del bullrun' de un simple pico "
             "de una semana)\n"]

    if res["doble_fuerza"]:
        lines.append("⚡ FUERZA EN AMBOS PLAZOS A LA VEZ (la señal más fuerte):")
        for f in res["doble_fuerza"]:
            lines.append(f"  {f['simbolo']}: 7d {f['fuerza_7d']:+.1f}pp | 200d {f['fuerza_200d']:+.1f}pp")
        lines.append("")
    else:
        lines.append("⚡ Ninguna moneda está en el top 15 de AMBOS plazos a la vez ahora mismo.\n")

    lines.append("🏆 Top 8 — últimas 24 horas:")
    for f in res["top_24h"][:8]:
        lines.append(f"  {f['simbolo']}: {f['cambio_24h']:+.1f}% (vs BTC: {f['fuerza_24h']:+.1f}pp) — rank #{f['rank']}")

    lines.append("\n🏆 Top 8 — últimos 7 días:")
    for f in res["top_corto"][:8]:
        lines.append(f"  {f['simbolo']}: {f['cambio_7d']:+.1f}% (vs BTC: {f['fuerza_7d']:+.1f}pp) — rank #{f['rank']}")

    lines.append("\n🏆 Top 8 — últimos ~200 días:")
    for f in res["top_largo"][:8]:
        lines.append(f"  {f['simbolo']}: {f['cambio_200d']:+.1f}% (vs BTC: {f['fuerza_200d']:+.1f}pp) — rank #{f['rank']}")

    if res["demasiado_nuevas"]:
        nuevas_top = [s for s in res["demasiado_nuevas"] if s in {f["simbolo"] for f in res["todas"][:30]}]
        if nuevas_top:
            lines.append(f"\n📌 Sin dato de 200 días por ser muy recientes (no llevan tanto cotizando): "
                        f"{', '.join(nuevas_top[:10])} — no significa que no sean fuertes, solo que no "
                        "podemos medir su plazo largo todavía.")

    if res.get("anomalos"):
        lines.append(f"\n🚫 Excluidas por valores imposibles ({', '.join(res['anomalos'][:8])}) — "
                    "casi siempre microcaps con precio de referencia cercano a cero (el % se dispara "
                    "sin significar nada real) o un error de datos puntual, no una señal de verdad.")

    safe_send(msg.chat.id, "\n".join(lines)[:4096])

    dia_txt = ", ".join(f"{f['simbolo']} ({f['fuerza_24h']:+.1f}pp)" for f in res["top_24h"][:6])
    corto_txt = ", ".join(f"{f['simbolo']} ({f['fuerza_7d']:+.1f}pp)" for f in res["top_corto"][:6])
    largo_txt = ", ".join(f"{f['simbolo']} ({f['fuerza_200d']:+.1f}pp)" for f in res["top_largo"][:6])
    doble_txt = ", ".join(f["simbolo"] for f in res["doble_fuerza"]) or "ninguna"
    prompt = (f"Fuerza relativa cripto vs BTC:\n"
              f"Top 24 horas: {dia_txt}\n"
              f"Top 7 días: {corto_txt}\n"
              f"Top ~200 días (6-7 meses): {largo_txt}\n"
              f"Fuertes en AMBOS plazos a la vez: {doble_txt}\n\n"
              "No inventes catalizadores concretos que no estén aquí — si no sabes la razón real "
              "de por qué una moneda concreta está fuerte, dilo, no la inventes.\n\n"
              "1. ¿Qué diferencia hay entre estar fuerte solo 7 días y estar fuerte también a 200 días? "
              "¿Cuál es más significativo y por qué?\n"
              "2. Si hay monedas en ambas listas, ¿qué sugiere eso sobre su papel en el ciclo actual?\n"
              "3. Riesgos de perseguir fuerza relativa, tanto a corto como a largo plazo")
    safe_send(msg.chat.id, f"ANÁLISIS IA\n\n{ask_ai(prompt)}")


# ═══ Menú de comandos de Telegram (lo que sale al pulsar "/") ═══
# El orden de esta lista es el orden del menú: /dyor va el primero.
MENU_COMANDOS = [
    ("dyor", "⚠️ Aviso legal — léelo antes de usar el bot"),
    ("start", "Inicio y lista de comandos"),
    ("guia", "Explicación completa de cada comando"),
    ("trial", "Prueba gratuita de 7 días"),
    ("premium", "Suscripción premium"),
    ("verificar", "Confirmar tu pago con el hash de la transacción"),
    ("mistatus", "Ver el estado de tu suscripción"),
    ("valor", "Índice barato/caro 0-100 de un activo"),
    ("fundamental", "Análisis fundamental 0-100 de una acción"),
    ("halvingbtc", "Ciclo de 4 años de Bitcoin"),
    ("ciclo", "Fase actual de BTC en el ciclo de mercado"),
    ("dominancia", "Zonas de compra/venta de BTC (Fear & Greed)"),
    ("suelo", "Triple suelo de sentimiento (VIX, AAII, COT, F&G)"),
    ("ballenas", "Muros de órdenes grandes en Binance"),
    ("cartera", "Carteras 13F de grandes inversores"),
    ("insiders", "Compras/ventas de directivos (SEC Form 4)"),
    ("macro", "Tipos, inflación, paro y derivados cripto"),
    ("curva", "Curva de tipos EEUU (10 años vs 2 años)"),
    ("correlacion", "Correlación BTC vs Nasdaq"),
    ("fuerza", "Fuerza relativa de 100 criptos vs BTC (24h, 7d, 200d)"),
    ("noticias", "Noticias de bolsa, economía y cripto"),
    ("ticker", "Resumen de mercados al momento"),
]

if __name__ == "__main__":
    # FIX 409: si el contenedor anterior no llegó a cerrar su getUpdates a
    # tiempo, esto libera el "lock" de Telegram antes de empezar a hacer
    # polling, en vez de chocar con la sesión previa.
    try:
        bot.remove_webhook()
        time.sleep(1)
    except Exception as e:
        log.warning(f"remove_webhook al arrancar: {e}")
    try:
        bot.set_my_commands([telebot.types.BotCommand(c, d) for c, d in MENU_COMANDOS])
    except Exception as e:
        log.warning(f"set_my_commands: {e}")
    log.info("AnalisisPro Bot arrancado")
    threading.Thread(target=_scheduler_loop, daemon=True).start()
    bot.infinity_polling(timeout=60, long_polling_timeout=60, skip_pending=True)

