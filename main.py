#!/usr/bin/env python3
"""AnalisisPro Bot — Uso personal (sin suscripciones) + /valor /fundamental /halvingbtc"""
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
from datetime import datetime, timedelta, timezone

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

# ═══ ACCESO — bot de uso personal, sin suscripciones ═══════════
# Solo responde a ALLOWED_USER_ID (tu chat_id de Telegram). Así se evita
# cualquier duda sobre uso comercial de fuentes de datos como yfinance,
# pensadas por sus propios términos para uso personal/educativo.
def is_premium(chat_id):
    return chat_id == ALLOWED_USER_ID

SYSTEM = """Eres un analista financiero senior. Responde SIEMPRE en español. Sin markdown.
Máximo 4 párrafos concisos y bien fundamentados.

Regla estricta, sin excepciones: NUNCA des recomendaciones de operativa. Esto incluye precios de
entrada o salida, niveles de stop-loss, objetivos de toma de beneficios, tamaño o reparto de
posición/capital, ni decir explícita o implícitamente si hay que comprar, vender o esperar. Da
información y contexto para que decida el usuario, nunca una instrucción de qué hacer con su
dinero. Si la pregunta te pide una 'estrategia concreta', respóndela igualmente pero en términos de
qué factores vigilar y qué señales conviene combinar, nunca en precios, porcentajes de capital o
niveles de entrada/salida."""

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
    No lo envía a quien no tiene acceso (solo tú puedes usar el bot)."""
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
        # El bot envía texto plano: Telegram mostraría los ** de negrita y los # de títulos tal cual.
        t = t.replace("**", "").replace("__", "")
        t = re.sub(r"^\s*#{1,6}\s*", "", t, flags=re.M)
        if not t:
            log.warning("ask_ai: respuesta vacía")
            return "IA no disponible."
        return t[:max_chars]
    except Exception as e:
        log.error(f"Groq: {e}")
        return "IA no disponible."

def calc_rsi(s, period=14):
    # Suavizado de Wilder (el que usa TradingView y prácticamente todas las plataformas), no una
    # media simple de las últimas 'period' velas. La media simple "olvida" de golpe cada vela en
    # cuanto sale de la ventana; Wilder arrastra memoria de todo lo anterior, cada vez más diluida.
    # Con ewm(adjust=False) el arranque no es matemáticamente idéntico al de Wilder (que empieza con
    # una media simple de las primeras 'period' velas), pero para el resto de la serie converge al
    # mismo resultado: con 60+ semanas de por medio, como usamos aquí, la diferencia es despreciable.
    d = s.diff()
    g = d.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    l = (-d.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
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
            raise RuntimeError(f"sin datos en Stooq (HTTP {r.status_code}, respuesta: {r.text[:100]!r})")
        from io import StringIO
        df = pd.read_csv(StringIO(r.text))
        if df.empty or len(df)<5:
            raise RuntimeError(f"Stooq: pocos datos ({len(df)} filas, HTTP {r.status_code}, "
                               f"respuesta: {r.text[:100]!r})")
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

def fetch_yahoo_directo(ticker, days=220):
    """Yahoo, pero por la API de gráficos directa (la misma que usa su propia web), no la
    librería yfinance. Ya la probamos a fondo en /rotacion y respondía bien desde Railway aunque
    yfinance esté bloqueado por IP compartida — parece un bloqueo específico de cómo se conecta
    esa librería, no de Yahoo en general. Se usa para símbolos que Stooq tiene muertos (los "^",
    confirmado en los logs) y que Twelve Data rechaza o exige plan de pago (^GSPC, ^VIX)."""
    ck = f"yhd:{ticker}"
    cached = cache_get(ck)
    if cached is not None:
        return cached
    ua = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
    def _do(host):
        r = requests.get(f"https://{host}.finance.yahoo.com/v8/finance/chart/{ticker}",
                         params={"range": f"{days + 30}d", "interval": "1d"}, headers=ua, timeout=10)
        if r.status_code != 200:
            raise RuntimeError(f"Yahoo HTTP {r.status_code}")
        res = ((r.json().get("chart") or {}).get("result") or [None])[0]
        if not res or not res.get("timestamp"):
            raise RuntimeError("Yahoo: sin datos")
        ind = (res.get("indicators") or {}).get("quote", [{}])[0]
        c, h, lo, vol = ind.get("close"), ind.get("high"), ind.get("low"), ind.get("volume")
        if not c or sum(1 for v in c if v is not None) < 5:
            raise RuntimeError("Yahoo: pocos datos")
        idx = [i for i, v in enumerate(c) if v is not None]
        cs = pd.Series([c[i] for i in idx]); hs = pd.Series([(h[i] if h[i] is not None else c[i]) for i in idx])
        ls = pd.Series([(lo[i] if lo[i] is not None else c[i]) for i in idx])
        vs = pd.Series([(vol[i] if vol and i < len(vol) and vol[i] is not None else 1e6) for i in idx])
        return build_quote(ticker, cs, hs, ls, vs)
    res = None
    for host in ("query1", "query2"):
        res = with_retry(lambda: _do(host), tries=2, base_delay=1.5, what=f"yahoo_directo {ticker} ({host})")
        if res:
            break
    if res:
        cache_set(ck, res)
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
    if t.startswith("^"):
        # Los índices (S&P 500, Nasdaq, VIX, IBEX, DAX...) tienen Stooq confirmado muerto desde
        # Railway (siempre devuelve la página de "no indexar", no datos) y Twelve Data los
        # rechaza o los exige de pago según el símbolo. Yahoo directo va primero aquí; solo si
        # también falla se prueba la cadena de siempre, por si algún día alguno vuelve a servir.
        res = fetch_yahoo_directo(t)
        if res is None:
            res = fetch_stooq(t)
        return res
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
        safe_send(msg.chat.id, "Este bot es de uso personal y no está disponible para otros usuarios.")
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
              f"1. ¿Qué dice este índice sobre la valoración actual?\n2. Riesgo principal\n"
              f"3. Qué otras señales conviene mirar junto a este índice antes de sacar conclusiones")
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
        safe_send(msg.chat.id, "Este bot es de uso personal y no está disponible para otros usuarios.")
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
        safe_send(msg.chat.id, "Este bot es de uso personal y no está disponible para otros usuarios.")
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
                f"2. Diferencias con ciclos anteriores\n3. Proyección realista 2028-2029\n"
                f"4. Qué conviene vigilar a partir de aquí, sin dar niveles de entrada ni de salida")
        safe_send(msg.chat.id,f"ANÁLISIS IA — CICLO HALVING\n\n{ask_ai(prompt,2500)}")
    except Exception as e:
        log.error(f"halvingbtc: {e}")
        safe_send(msg.chat.id,f"Error: {e}",message_id=m.message_id)

@bot.message_handler(commands=["start","ayuda"])
def cmd_start(msg):
    chat_id = msg.from_user.id
    nombre = msg.from_user.first_name or "inversor"
    if not is_premium(chat_id):
        safe_send(chat_id, "Este bot es de uso personal y no está disponible para otros usuarios.")
        return
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
        "/insiders TICKER — Compras/ventas de directivos (SEC Form 4)\n"
        "/compresion — Compresión de precio de BTC (volatilidad 30 días)\n"
        "/liquidaciones [BTC|ETH|SOL|HYPE] — Mapa de liquidaciones estimado (sin moneda, BTC)\n"
        "/rsiminimos — Cripto y acciones cerca de su mínimo de RSI en 2 años\n"
        "/vwap TICKER — Precio medio ponderado por volumen de hoy, cripto o acción\n"
        "/directo — Enlace a la cinta de precios cripto en directo\n\n"
        "/guia — Explicación completa de cada comando\n"
        "/dyor — Aviso legal (léelo antes de usar el bot para decidir)\n\n"
        "Además, cada 2h (9-21h) recibes un resumen automático de mercados y "
        "cada mañana a las 8h un resumen diario con Fear & Greed y noticias destacadas.\n\n"
        "Tickers: casi cualquiera funciona, no hace falta que esté en una lista.\n"
        "Crypto: escribe el símbolo con o sin -USD (BTC, BTC-USD, PEPE...).\n"
        "Acciones internacionales: ticker + sufijo de bolsa (SAN.MC, BMW.DE, VOD.L...).")

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
        safe_send(msg.chat.id, "Este bot es de uso personal y no está disponible para otros usuarios.")
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
        safe_send(msg.chat.id, "Este bot es de uso personal y no está disponible para otros usuarios.")
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
              "3. Qué otras señales conviene mirar junto a esta antes de sacar conclusiones")
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
        safe_send(msg.chat.id, "Este bot es de uso personal y no está disponible para otros usuarios.")
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
        safe_send(msg.chat.id, "Este bot es de uso personal y no está disponible para otros usuarios.")
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
        safe_send(msg.chat.id, "Este bot es de uso personal y no está disponible para otros usuarios.")
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
    "Chainlink": "LINK-USD", "Polkadot": "DOT-USD", "Hedera": "HBAR-USD", "Sui": "SUI-USD",
}
# HYPE NO está en el mercado spot de Binance global (solo en Binance.US, una
# plataforma distinta con otra API, o en el propio DEX de Hyperliquid) — así
# que no se puede traer con fetch_binance como el resto. Usamos CoinGecko
# (gratis, sin API key) solo para este.
# Ojo con "PURR": el ticker PURR es AMBIGUO. El memecoin cripto de Hyperliquid
# iba aquí antes (CoinGecko "purr-2"), pero se retiró: lo que de verdad se
# sigue es la ACCIÓN Hyperliquid Strategies Inc. (Nasdaq: PURR, va en
# BROADCAST_STOCKS más abajo), una empresa que compra HYPE como tesorería,
# sin relación directa con el memecoin más allá del nombre.
BROADCAST_CRYPTO_COINGECKO = {
    "Hyperliquid": "hyperliquid",
}
BROADCAST_STOCKS = {
    "Apple": "AAPL", "Microsoft": "MSFT", "Nvidia": "NVDA", "Amazon": "AMZN",
    "Google": "GOOGL", "Meta": "META", "Tesla": "TSLA", "JPMorgan": "JPM",
    "Netflix": "NFLX", "SpaceX": "SPCX", "Strategy (Saylor)": "MSTR",
    "Walmart": "WMT", "Coca-Cola": "KO", "PURR (Hyperliquid Strategies)": "PURR",
}
BROADCAST_INDICES = {
    "S&P 500": "^GSPC", "Nasdaq": "^IXIC", "IBEX 35": "^IBEX", "DAX": "^GDAXI", "CAC 40": "^FCHI",
}
# Sin ETFs en el resumen: VWCE/VGLA (Xetra) no tienen ninguna fuente gratuita que
# funcione desde Railway (Stooq bloquea la IP, Twelve Data lo da solo en plan de
# pago y Yahoo limita las peticiones). El grupo queda vacío y no se dibuja.
BROADCAST_ETF = {}
BROADCAST_COMMODITIES = {
    "Oro": "GC=F",
}
BROADCAST_FALLBACK = {"^IXIC": "QQQ", "^GSPC": "SPY"}  # mismo fix que ya vimos con /mercados

# Límite para las llamadas a CoinGecko (HYPE/PURR en el resumen automático) —
# evita reventar el rate-limit gratuito de CoinGecko (mismo tipo de arreglo
# que ya vimos con Twelve Data).
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

_BROADCAST_FALTAN = []   # nombres sin dato en el último resumen (se avisa en el gráfico)

def fetch_finnhub_cambio(ticker):
    """Variación diaria de una acción de EEUU vía Finnhub /quote (con clave, sin
    scraping). Solo se usa en el resumen, que únicamente necesita el % del día:
    evita pasar por Stooq -> Twelve Data (7 llamadas/min), que era lo que retrasaba
    el envío varios minutos."""
    if not FINNHUB_API_KEY:
        return None
    ck = f"fh_q:{ticker}"
    cached = cache_get(ck)
    if cached is not None: return cached
    def _do():
        r = requests.get("https://finnhub.io/api/v1/quote",
                         params={"symbol": ticker, "token": FINNHUB_API_KEY}, timeout=8)
        j = r.json()
        if r.status_code != 200 or not j.get("c"):
            raise RuntimeError(f"Finnhub quote: HTTP {r.status_code} {str(j)[:80]}")
        return {"d1": float(j.get("dp") or 0.0), "price": j["c"]}
    res = with_retry(_do, tries=2, base_delay=1, what=f"finnhub quote {ticker}")
    if res: cache_set(ck, res)
    return res

def calcular_broadcast():
    """Recorre los ~30 activos con una pequeña pausa entre cada uno —
    lección aprendida de cuando /mercados reventaba el límite de Twelve
    Data al pedir muchos tickers en ráfaga."""
    global _BROADCAST_FALTAN
    t0 = time.time()
    faltan = []
    resultados = []
    for grupo, tickers in [("🪙 Cripto", BROADCAST_CRYPTO), ("📈 Acciones", BROADCAST_STOCKS),
                            ("🌍 Índices", BROADCAST_INDICES), ("📦 ETF", BROADCAST_ETF),
                            ("🥇 Materias primas", BROADCAST_COMMODITIES)]:
        for nombre, ticker in tickers.items():
            d = fetch_finnhub_cambio(ticker) if grupo == "📈 Acciones" else None
            if not d:
                d = get_quote(ticker)
            if not d and ticker in BROADCAST_FALLBACK:
                d = get_quote(BROADCAST_FALLBACK[ticker])
            if d:
                resultados.append({"grupo": grupo, "nombre": nombre, "d1": d["d1"]})
            else:
                faltan.append(nombre)
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
        else:
            faltan.append(nombre)
        time.sleep(0.3)
    _BROADCAST_FALTAN = faltan
    log.info(f"calcular_broadcast: {len(resultados)} activos en {time.time()-t0:.0f}s; "
             f"sin datos: {faltan or 'ninguno'}")
    return resultados

def _bolsa_eeuu_abierta():
    """Aproximado: lunes-viernes 9:30-16:00 hora de Nueva York (no contempla festivos)."""
    try:
        ny = datetime.now(pytz.timezone("America/New_York"))
        return ny.weekday() < 5 and (9, 30) <= (ny.hour, ny.minute) < (16, 0)
    except Exception:
        return True

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
    nota_cierre = "" if _bolsa_eeuu_abierta() else \
        "Acciones de EEUU, S&P 500 y Nasdaq: variación de la última sesión (bolsa de EEUU cerrada)"
    ax.set_title(f'RESUMEN DE MERCADOS — {fecha_txt}', color='white', fontsize=17,
                 fontweight='bold', loc='left', pad=(40 if nota_cierre else 18))
    if nota_cierre:
        ax.text(0, 1.006, nota_cierre, transform=ax.transAxes, fontsize=11, color='#FFB84D',
                va='bottom', ha='left')
    if _BROADCAST_FALTAN:
        ax.text(0, -0.012, "Sin datos ahora: " + ", ".join(_BROADCAST_FALTAN), transform=ax.transAxes,
                fontsize=10, color='#888888', va='top', ha='left')

    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=130, facecolor='#0d1117', bbox_inches='tight')
    plt.close()
    buf.seek(0)
    return buf.getvalue()

def _lista_suscriptores_activos():
    """Bot de uso personal: el único destinatario de los envíos automáticos eres tú."""
    return [ALLOWED_USER_ID] if ALLOWED_USER_ID else []

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
        safe_send(msg.chat.id, "Este bot es de uso personal y no está disponible para otros usuarios.")
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

@bot.message_handler(commands=["diagnostico"])
def cmd_diagnostico(msg):
    """Solo administrador. Prueba cada fuente de datos con un ticker y cuenta en el propio
    chat qué contesta cada una (sin tener que rebuscar en los logs de Railway)."""
    if msg.from_user.id != ALLOWED_USER_ID:
        return
    parts = msg.text.split()
    if len(parts) < 2:
        safe_send(msg.chat.id, "Uso: /diagnostico TICKER\nEjemplos: /diagnostico VWCE.DE  /diagnostico AAPL")
        return
    t = normalize_ticker(parts[1])
    m = bot.send_message(msg.chat.id, f"Probando las fuentes de datos para {t}... (10-20s)")
    secretos = [k for k in (TWELVEDATA_API_KEY, FINNHUB_API_KEY, GROQ_API_KEY, TELEGRAM_TOKEN) if k]
    def limpia(x):
        x = str(x)
        for k in secretos:
            x = x.replace(k, "***")      # nunca mostrar claves, ni dentro de un mensaje de error
        return x[:170]
    L = [f"🔧 DIAGNÓSTICO — {t}\n"]

    # 1) Stooq (misma traducción de símbolo que usa el bot)
    import datetime as dt
    if t in STOOQ_MAP: st = STOOQ_MAP[t]
    elif t.endswith((".MC", ".DE", ".PA")): st = t.lower()
    elif t.endswith(".L"): st = t.lower().replace(".l", ".uk")
    elif "." not in t: st = f"{t.lower()}.us"
    else: st = t.lower()
    NAVEGADOR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                               "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
                 "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                 "Accept-Language": "es-ES,es;q=0.9,en;q=0.8", "Referer": "https://stooq.com/"}
    def texto_html(x):
        x = re.sub(r"<(script|style).*?</\1>", " ", x, flags=re.S | re.I)
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", x)).strip()
    d1 = (dt.date.today() - dt.timedelta(days=250)).strftime("%Y%m%d")
    d2 = dt.date.today().strftime("%Y%m%d")
    for etiqueta, host, cab in [("Stooq (como el bot)", "stooq.com", {"User-Agent": "Mozilla/5.0"}),
                                ("Stooq (cabeceras de navegador)", "stooq.com", NAVEGADOR),
                                ("Stooq .pl (navegador)", "stooq.pl", NAVEGADOR)]:
        try:
            r = requests.get(f"https://{host}/q/d/l/?s={st}&d1={d1}&d2={d2}&i=d", timeout=10, headers=cab)
            if r.text.lstrip().startswith("<"):
                L.append(f"{etiqueta} [{st}]: HTTP {r.status_code} · devuelve una PÁGINA WEB, no datos. "
                         f"Dice: {limpia(texto_html(r.text))[:150]!r}")
            else:
                filas = max(0, r.text.count("\n") - 1)
                L.append(f"{etiqueta} [{st}]: HTTP {r.status_code} · ~{filas} filas · "
                         f"inicio: {limpia(r.text[:60])!r}")
        except Exception as e:
            L.append(f"{etiqueta} [{st}]: error → {limpia(e)}")

    # 2) Twelve Data (símbolo tal cual el bot; y, si es de Xetra, con exchange=XETR)
    if TWELVEDATA_API_KEY:
        variantes = [{"symbol": TWELVEDATA_SYMBOL_MAP.get(t, t)}]
        if t.endswith(".DE"):
            variantes.append({"symbol": t[:-3], "exchange": "XETR"})
        for v in variantes:
            etiqueta = ", ".join(f"{k}={x}" for k, x in v.items())
            try:
                r = requests.get("https://api.twelvedata.com/time_series",
                                 params={**v, "interval": "1day", "outputsize": 5,
                                         "apikey": TWELVEDATA_API_KEY}, timeout=10)
                j = r.json()
                if "values" in j and j["values"]:
                    L.append(f"Twelve Data ({etiqueta}): OK · último cierre {j['values'][0]['close']} "
                             f"({j['values'][0]['datetime']})")
                else:
                    L.append(f"Twelve Data ({etiqueta}): {limpia(j.get('message', j))}")
            except Exception as e:
                L.append(f"Twelve Data ({etiqueta}): error → {limpia(e)}")
    else:
        L.append("Twelve Data: sin clave (falta TWELVEDATA_API_KEY)")

    # 3) Yahoo (yfinance): suele estar bloqueado desde Railway
    try:
        import yfinance as yf
        h = yf.Ticker(t).history(period="5d")
        if h is not None and not h.empty:
            L.append(f"Yahoo (yfinance): OK · {len(h)} filas")
        else:
            L.append("Yahoo (yfinance): sin datos (probable bloqueo desde Railway)")
    except Exception as e:
        L.append(f"Yahoo (yfinance): error → {limpia(e)}")

    # 3b) Yahoo directo, sin la librería yfinance
    for host in ("query1", "query2"):
        try:
            r = requests.get(f"https://{host}.finance.yahoo.com/v8/finance/chart/{t}",
                             params={"range": "5d", "interval": "1d"}, headers=NAVEGADOR, timeout=10)
            if r.status_code == 200:
                res = (r.json().get("chart") or {}).get("result")
                if res:
                    cierres = [c for c in res[0]["indicators"]["quote"][0]["close"] if c is not None]
                    L.append(f"Yahoo directo ({host}): OK · precio {res[0]['meta'].get('regularMarketPrice')} "
                             f"· {len(cierres)} cierres")
                else:
                    L.append(f"Yahoo directo ({host}): HTTP 200 pero sin datos")
            else:
                L.append(f"Yahoo directo ({host}): HTTP {r.status_code} · {limpia(r.text[:90])!r}")
        except Exception as e:
            L.append(f"Yahoo directo ({host}): error → {limpia(e)}")

    # 4) Finnhub, solo para tickers de EEUU sin sufijo
    if re.fullmatch(r"[A-Z]{1,5}", t):
        if FINNHUB_API_KEY:
            try:
                r = requests.get("https://finnhub.io/api/v1/quote",
                                 params={"symbol": t, "token": FINNHUB_API_KEY}, timeout=8)
                j = r.json()
                L.append(f"Finnhub: HTTP {r.status_code} · precio {j.get('c')} · cambio {j.get('dp')}%")
            except Exception as e:
                L.append(f"Finnhub: error → {limpia(e)}")
        else:
            L.append("Finnhub: sin clave (falta FINNHUB_API_KEY)")
    else:
        L.append("Finnhub: no aplica (solo acciones de EEUU sin sufijo)")
    safe_send(msg.chat.id, "\n".join(L), message_id=m.message_id)

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

# Estas tres constantes las usa /rsiminimos para su universo de cripto (mismo filtro de
# liquidez que tenía /calientes, ya retirado).
CALIENTES_MIN_USDT = 10_000_000   # liquidez mínima 24h: evita monedas pequeñas, fáciles de manipular
CALIENTES_UNIVERSO = 120          # cuántas monedas (las más líquidas) se analizan
_CALIENTES_EXCLUIR = {"usdc", "fdusd", "tusd", "usde", "usds", "usdp", "busd", "dai", "eur",
                      "eurc", "bfusd", "xusd", "usd1", "rlusd", "pyusd", "paxg", "xaut"}
# ═══ /COMPRESION — Compresión de precio de BTC (30 días) ═══
# Aproximación propia, con datos de Binance, de indicadores tipo "Price Compression
# Score" (CryptoQuant): mide lo ESTRECHO que está el rango de precio de los últimos 30
# días frente a los rangos de los últimos 12 meses. 100% = el rango más estrecho del
# año; 0% = el más ancho. Es volatilidad, NO dirección. La fórmula exacta de
# CryptoQuant no es pública, así que los números no coincidirán al 100%.

COMPRESION_VENTANA = 30
COMPRESION_HISTORIA = 365

def _zona_compresion(s):
    if s >= 90: return "COMPRESIÓN EXTREMA", '#FF3333'
    if s >= 75: return "COMPRESIÓN FUERTE", '#FF7700'
    if s >= 60: return "COMPRESIÓN", '#FFCC00'
    if s >= 40: return "NORMAL", '#99DD00'
    return "EXPANDIDO", '#3388FF'

def calcular_compresion():
    ck = "compresion_btc"
    cached = cache_get(ck)
    if cached is not None:
        return cached
    V = COMPRESION_VENTANA
    h = fetch_btc_price_history_long(days=COMPRESION_HISTORIA + V + 60)
    if not h:
        return None
    closes = pd.Series(h["closes"]).astype(float).reset_index(drop=True)
    fechas = list(h["fechas"])
    n = len(closes)
    if n < V + 120:
        return None
    rmax = closes.rolling(V).max()
    rmin = closes.rolling(V).min()
    rango = ((rmax - rmin) / rmin * 100).values
    scores = np.full(n, np.nan)
    for i in range(V - 1, n):
        base = rango[max(V - 1, i - COMPRESION_HISTORIA + 1): i + 1]
        if len(base) >= 90:                       # mínimo de historia para que el % signifique algo
            scores[i] = 100.0 * float(np.mean(base > rango[i]))   # % de días con un rango MÁS ANCHO que hoy
    ult = n - 1
    if np.isnan(scores[ult]):
        return None
    def _sc(k):
        v = scores[ult - k] if ult - k >= 0 else np.nan
        return None if np.isnan(v) else float(v)
    win = closes.iloc[-V:].values
    precio, max30, min30 = float(closes.iloc[-1]), float(rmax.iloc[-1]), float(rmin.iloc[-1])
    primero = int(np.argmax(~np.isnan(scores)))
    desde = max(n - COMPRESION_HISTORIA, primero)
    res = {"fechas": fechas[desde:], "closes": closes.values[desde:],
           "rmax": rmax.values[desde:], "rmin": rmin.values[desde:], "scores": scores[desde:],
           "score": float(scores[ult]), "score_7d": _sc(7), "score_30d": _sc(30),
           "precio": precio, "max30": max30, "min30": min30, "rango_pct": float(rango[ult]),
           "pos": (precio - min30) / (max30 - min30) * 100 if max30 > min30 else 50.0,
           "dias_max": int(V - 1 - np.argmax(win)), "dias_min": int(V - 1 - np.argmin(win)),
           "hora": datetime.now(MADRID).strftime("%d/%m %H:%M")}
    cache_set(ck, res)
    return res

def chart_compresion(res):
    score = res["score"]
    zona, zc = _zona_compresion(score)
    fig = plt.figure(figsize=(11, 14))
    fig.patch.set_facecolor('#0d1117')
    fig.text(0.5, 0.975, "BTC — COMPRESIÓN DE PRECIO (30 días)", ha='center', color='white',
             fontsize=19, fontweight='bold')
    fig.text(0.5, 0.953, f"{res['hora']} (Madrid)  ·  rango de los últimos 30 días frente al de los últimos 12 meses",
             ha='center', color='#999999', fontsize=11)

    # ── Velocímetro con aguja (0 = expandido, 100 = compresión extrema) ──
    # Dibujado con cuñas en ejes cartesianos (no polares): la geometría es exacta y controlable.
    from matplotlib.patches import Wedge
    ax = fig.add_axes([0.08, 0.60, 0.84, 0.33])
    ax.set_facecolor('#0d1117'); ax.axis('off')
    ax.set_xlim(-1.3, 1.3); ax.set_ylim(-0.15, 1.15); ax.set_aspect('equal', adjustable='box')
    for i in range(100):
        c = '#3388FF' if i < 40 else '#99DD00' if i < 60 else '#FFCC00' if i < 75 else '#FF7700' if i < 90 else '#FF3333'
        ax.add_patch(Wedge((0, 0), 1.0, 180 - (i + 1) * 1.8, 180 - i * 1.8, width=0.30, color=c, ec='none'))
    ang = np.radians(180 - score * 1.8)
    ax.plot([0, 0.86 * np.cos(ang)], [0, 0.86 * np.sin(ang)], color='white', linewidth=7,
            solid_capstyle='round', zorder=5)
    ax.plot(0, 0, 'o', color='white', markersize=24, zorder=6)
    ax.plot(0, 0, 'o', color='#0d1117', markersize=12, zorder=7)
    for val, txt in [(0, '0%\nEXPANDIDO'), (25, '25'), (50, '50\nNORMAL'), (75, '75'), (100, '100%\nEXTREMA')]:
        a = np.radians(180 - val * 1.8)
        r = 1.13 if val in (0, 100) else 1.10
        ha = 'right' if val == 0 else 'left' if val == 100 else 'center'
        ax.text(r * np.cos(a) if val not in (0, 100) else (1.0 if val == 100 else -1.0), 
                r * np.sin(a) if val not in (0, 100) else -0.06,
                txt, color='white', fontsize=10, fontweight='bold', ha='center',
                va='top' if val in (0, 100) else 'bottom')
    fig.text(0.5, 0.590, f"{score:.0f}%", ha='center', va='center', fontsize=40, color='white', fontweight='bold')
    fig.text(0.5, 0.552, zona, ha='center', va='center', fontsize=15, color=zc, fontweight='bold')
    s7 = res["score_7d"]
    if s7 is not None:
        d = score - s7
        flecha = "▲" if d > 2 else "▼" if d < -2 else "▶"
        fig.text(0.5, 0.527, f"{flecha} hace 7 días: {s7:.0f}%   ({d:+.0f} puntos)", ha='center',
                 va='center', fontsize=12, color='#CCCCCC')

    # ── Dónde está el precio dentro del rango de 30 días ──
    axr = fig.add_axes([0.10, 0.445, 0.80, 0.055])
    axr.set_facecolor('#0d1117'); axr.axis('off')
    axr.set_xlim(-2, 102); axr.set_ylim(-1, 1.6)
    axr.plot([0, 100], [0, 0], color='#444444', linewidth=10, solid_capstyle='round', zorder=1)
    axr.plot([res['pos']], [0], marker='v', color=zc, markersize=20, markeredgecolor='white',
             markeredgewidth=1.5, zorder=5, clip_on=False)
    ha_ahora = 'right' if res['pos'] > 70 else 'left' if res['pos'] < 30 else 'center'
    axr.text(res['pos'], 0.95, f"AHORA ${res['precio']:,.0f}  ({res['pos']:.0f}% del rango)", ha=ha_ahora,
             va='bottom', color='white', fontsize=11, fontweight='bold')
    axr.text(0, -0.55, f"mín 30d\n${res['min30']:,.0f}", ha='left', va='top', color='#66DD66', fontsize=10)
    axr.text(100, -0.55, f"máx 30d\n${res['max30']:,.0f}", ha='right', va='top', color='#66B2FF', fontsize=10)

    # ── Historia: precio con su rango de 30 días, y debajo el score ──
    fechas = [pd.Timestamp(f) for f in res["fechas"]]
    ax1 = fig.add_axes([0.11, 0.225, 0.84, 0.16])
    ax2 = fig.add_axes([0.11, 0.055, 0.84, 0.135], sharex=ax1)
    for a in (ax1, ax2):
        a.set_facecolor('#0d1117')
        for sp in a.spines.values(): sp.set_color('#333333')
        a.tick_params(colors='#AAAAAA', labelsize=10)
        a.grid(color='#222222', linestyle='--', alpha=0.3)
    ax1.plot(fechas, res["closes"], color='white', linewidth=1.5, zorder=4)
    ax1.plot(fechas, res["rmax"], color='#66B2FF', linestyle=':', linewidth=1.2)
    ax1.plot(fechas, res["rmin"], color='#66DD66', linestyle=':', linewidth=1.2)
    ax1.fill_between(fechas, res["rmin"], res["rmax"], color='#4488FF', alpha=0.10)
    ax1.yaxis.set_major_formatter(lambda x, _: f"${x/1000:.0f}K")
    ax1.set_title("Precio y rango de 30 días (punteado)", color='#AAAAAA', fontsize=11, loc='left')
    ax1.tick_params(labelbottom=False)
    sc = np.array(res["scores"], dtype=float)
    ax2.plot(fechas, sc, color='#DDDDDD', linewidth=1.3, zorder=4)
    ax2.fill_between(fechas, 0, sc, where=(sc >= 75), color='#FF7700', alpha=0.45, zorder=3, interpolate=True)
    ax2.fill_between(fechas, 0, sc, where=(sc < 75), color='#4488FF', alpha=0.15, zorder=2, interpolate=True)
    for nivel, col in [(40, '#3388FF'), (60, '#FFCC00'), (75, '#FF7700'), (90, '#FF3333')]:
        ax2.axhline(nivel, color=col, linestyle='--', linewidth=0.9, alpha=0.6)
    ax2.set_ylim(0, 100)
    ax2.plot(fechas[-1], sc[-1], 'o', color=zc, markersize=11, markeredgecolor='white', markeredgewidth=2, zorder=6)
    ax2.set_title("Compresión (%): más alto = rango más estrecho que casi todo el año", color='#AAAAAA',
                  fontsize=11, loc='left')
    ax2.xaxis.set_major_formatter(mdates.DateFormatter('%b %y'))
    fig.text(0.5, 0.012, "Aproximación propia con datos de Binance (no coincide exactamente con CryptoQuant). "
             "Mide volatilidad, no dirección.", ha='center', color='#777777', fontsize=9)
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=120, facecolor='#0d1117')
    plt.close()
    buf.seek(0)
    return buf

def texto_compresion(res):
    score = res["score"]
    zona, _ = _zona_compresion(score)
    L = ["📖 QUÉ ES LA COMPRESIÓN DE PRECIO\n",
         "Mide lo estrecho que está el rango de precio de BTC en los últimos 30 días, comparado con los "
         "últimos 12 meses. 100% = el rango más estrecho del año; 0% = el más ancho.\n",
         "• Alta: el precio lleva semanas en un pasillo estrecho. Suele anteceder a un movimiento fuerte, "
         "pero NO dice si será al alza o a la baja.",
         "• Baja: ya hubo un movimiento grande hace poco; la volatilidad ya se ha liberado.\n",
         f"📊 AHORA: {score:.0f}% — {zona.lower()}",
         f"BTC ${res['precio']:,.0f}. En 30 días ha oscilado entre ${res['min30']:,.0f} y ${res['max30']:,.0f} "
         f"(un rango del {res['rango_pct']:.1f}%). Está al {res['pos']:.0f}% de ese rango."]
    if res["pos"] >= 85:
        L.append(f"Está pegado a la parte alta: superar ${res['max30']:,.0f} sería salir del rango por arriba.")
    elif res["pos"] <= 15:
        L.append(f"Está pegado a la parte baja: perder ${res['min30']:,.0f} sería salir del rango por abajo.")
    if res["score_7d"] is not None and res["score_30d"] is not None:
        L.append(f"Hace 7 días: {res['score_7d']:.0f}%. Hace 30 días: {res['score_30d']:.0f}%.")
    avisos = []
    if res["score_7d"] is not None and res["score"] - res["score_7d"] >= 40:
        avisos.append("El score ha subido de golpe en pocos días. Suele pasar cuando un movimiento fuerte sale "
                      "de la ventana de 30 días (el rango se estrecha solo), no porque el mercado se haya "
                      "calmado de repente.")
    for nombre, dias in (("máximo", res["dias_max"]), ("mínimo", res["dias_min"])):
        if dias >= 25:
            avisos.append(f"El {nombre} del rango es de hace {dias} días: saldrá de la ventana en unos "
                          f"{30 - dias} días y el rango cambiará solo por eso.")
    if avisos:
        L.append("\n⚠️ Ojo con el efecto de ventana:\n" + "\n".join(f"• {a}" for a in avisos))
    L.append("\nNo es una señal de compra ni de venta: avisa de que el mercado puede moverse, no hacia dónde.")
    return "\n".join(L)

@bot.message_handler(commands=["compresion"])
@con_dyor
def cmd_compresion(msg):
    if not is_premium(msg.from_user.id):
        safe_send(msg.chat.id, "Este bot es de uso personal y no está disponible para otros usuarios.")
        return
    m = bot.send_message(msg.chat.id, "Calculando la compresión de precio de BTC... (10-15s)")
    res = calcular_compresion()
    if not res:
        safe_send(msg.chat.id, "No he podido obtener el histórico de BTC ahora mismo. Reintenta en un momento.",
                  message_id=m.message_id)
        return
    zona, _ = _zona_compresion(res["score"])
    caption = (f"📉 COMPRESIÓN DE PRECIO — BTC\n{res['score']:.0f}% · {zona.lower()}\n"
               f"BTC ${res['precio']:,.0f} · rango 30d ${res['min30']:,.0f}–${res['max30']:,.0f}")
    try:
        img = chart_compresion(res)
        bot.delete_message(msg.chat.id, m.message_id)
        bot.send_photo(msg.chat.id, img, caption=caption[:1020])
    except Exception as e:
        log.warning(f"chart_compresion: {e}")
        safe_send(msg.chat.id, caption, message_id=m.message_id)
    safe_send(msg.chat.id, texto_compresion(res))
    s7 = f"{res['score_7d']:.0f}%" if res["score_7d"] is not None else "N/D"
    prompt = (f"Compresión de precio de BTC (aproximación propia con datos de Binance): {res['score']:.0f}% "
              f"→ {zona}. Significa que el rango de precio de los últimos 30 días es más estrecho que el "
              f"{res['score']:.0f}% de los rangos de 30 días de los últimos 12 meses. "
              f"BTC ${res['precio']:,.0f}; en 30 días ha oscilado entre ${res['min30']:,.0f} y "
              f"${res['max30']:,.0f} ({res['rango_pct']:.1f}%); está al {res['pos']:.0f}% de ese rango. "
              f"Hace 7 días el score era {s7}. El máximo del rango es de hace {res['dias_max']} días y el "
              f"mínimo de hace {res['dias_min']} días.\n\n"
              "Datos ya interpretados, úsalos tal cual. No inventes cifras ni catalizadores. Recuerda que "
              "la compresión mide volatilidad y no dirección.\n"
              "Reglas: NO des recomendaciones de operativa: nada de stops, objetivos de precio, toma de "
              "beneficios, tamaño de posición ni 'compre' o 'venda'. Limítate a explicar qué significan los "
              "datos y qué señales habría que vigilar.\n\n"
              "1. ¿Qué implica este nivel de compresión y qué NO implica?\n"
              "2. ¿Podría el score actual deberse en parte a que un movimiento fuerte ha salido de la "
              "ventana de 30 días? ¿Cómo distinguirlo?\n"
              "3. Qué habría que vigilar para confirmar una ruptura del rango y riesgos de falsas rupturas")
    safe_send(msg.chat.id, f"ANÁLISIS IA\n\n{ask_ai(prompt)}")

# ═══ /LIQUIDACIONES — Mapa de calor de liquidaciones ESTIMADO (BTC, ETH, SOL) ═══
# Estimación propia con datos públicos de Binance Futures (sin claves): interés abierto y
# ratio largos/cortos cada 15 min (si Binance no da esa resolución, cada hora) de los últimos
# 30 días, más las velas. Modelo: cada vez que sube el interés abierto se "abren" posiciones
# nuevas al precio medio de esa vela, repartidas entre largos y cortos (según el ratio) y entre
# niveles de apalancamiento supuestos. Cada una tiene un precio de liquidación; si el precio lo
# toca después, la posición se da por liquidada y desaparece; si baja el interés abierto, se
# reduce lo que queda. NO es dato real de liquidaciones: es un modelo, como los de Glassnode o
# Coinglass (que usan supuestos distintos), así que no coincidirá con ellos.
# Salen tres imágenes: corto plazo, zoom de 24 h con bloques por nivel (estilo TradingView) y visión de 30 días.
# Monedas disponibles: las de LIQ_MONEDAS (para añadir otra, comprobar que tenga perpetuo USDT con el
# mismo nombre en Binance y en Bybit; las que llevan multiplicador, tipo 1000PEPEUSDT, necesitan ajuste extra).

LIQ_DIAS = 30
LIQ_TIERS = {5: 0.10, 10: 0.25, 25: 0.30, 50: 0.20, 100: 0.15}   # apalancamiento supuesto -> peso
# HYPE: Binance da como máximo 75x (según el anuncio de su perpetuo), así que no hay tramo de 100x
LIQ_TIERS_POR_SIMBOLO = {"HYPEUSDT": {5: 0.10, 10: 0.25, 25: 0.30, 50: 0.25, 75: 0.10}}
LIQ_MMR = 0.005                                                   # margen de mantenimiento aprox.
LIQ_CALENTAMIENTO_H = 48        # las primeras horas del modelo no son fiables (no vemos lo anterior)
LIQ_NBINS = 600                 # ~0,1% de precio por fila
LIQ_SEED_SIGMA = 0.05           # al empezar la ventana no sabemos a qué precio se abrieron las posiciones ya existentes:
LIQ_SEED_RANGO = 0.15           # se reparten alrededor del precio de inicio (campana de ±5%, hasta ±15%), no en un punto
LIQ_PASOS_MS = {"5m": 300000, "15m": 900000, "30m": 1800000, "1h": 3600000}
LIQ_MONEDAS = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT", "HYPE": "HYPEUSDT"}

def _dec_precio(p):
    """Decimales según la magnitud del precio (BTC 0, SOL 1-2, monedas baratas 4)."""
    return 0 if p >= 1000 else 1 if p >= 100 else 2 if p >= 1 else 4

def _fp(x):
    return f"${x:,.{_dec_precio(x)}f}"

def _fut_get(path, params):
    def _do():
        r = requests.get("https://fapi.binance.com" + path, params=params, timeout=12)
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code} {r.text[:80]}")
        return r.json()
    return with_retry(_do, tries=3, base_delay=2, what=f"fapi {path}")

def _fut_hist_paginado(path, symbol, dias, periodo):
    """Serie de /futures/data/* (máx. 500 por llamada) troceada en tramos."""
    paso = LIQ_PASOS_MS[periodo]
    fin = int(time.time() * 1000)
    ini = fin - dias * 86400 * 1000 + 12 * 3600 * 1000     # 12 h de margen: Binance solo guarda 30 días
    out, t = {}, ini
    while t < fin:
        t2 = min(t + 499 * paso, fin)
        datos = _fut_get(path, {"symbol": symbol, "period": periodo, "limit": 500,
                                "startTime": t, "endTime": t2})
        if datos is None:
            return None
        for d in datos:
            out[int(d["timestamp"]) // paso] = d
        t = t2 + 1
    return out

def _fut_klines(symbol, periodo, dias):
    paso = LIQ_PASOS_MS[periodo]
    fin = int(time.time() * 1000)
    t, kl = fin - dias * 86400 * 1000, []
    while t < fin:
        datos = _fut_get("/fapi/v1/klines", {"symbol": symbol, "interval": periodo,
                                             "startTime": t, "limit": 1500})
        if not datos:
            break
        kl += datos
        t = int(datos[-1][0]) + paso
        if len(datos) < 1500:
            break
    return kl or None

def _bybit_get(path, params):
    """Una petición a Bybit con reintentos. Devuelve la lista 'result.list' o None si falla."""
    def _do():
        r = requests.get(f"https://api.bybit.com{path}", params=params, timeout=12)
        try:
            j = r.json()
        except Exception:
            raise RuntimeError(f"HTTP {r.status_code}: respuesta no es JSON")
        if r.status_code != 200 or j.get("retCode") not in (0, None):
            raise RuntimeError(f"HTTP {r.status_code} retCode={j.get('retCode')} {str(j.get('retMsg'))[:80]}")
        return (j.get("result") or {}).get("list") or []
    return with_retry(_do, tries=2, base_delay=1.5, what=f"bybit {path}")

def _bybit_paginado_1h(path, extra_params, dias):
    """Pagina hacia atrás en el tiempo (Bybit no garantiza el orden) hasta cubrir 'dias' días a 1h,
    o hasta 20 páginas como tope de seguridad."""
    HORA_MS = 3600_000
    fin_total = int(time.time() * 1000)
    ini_total = fin_total - dias * 86400_000
    out, cursor_end, vistas = {}, fin_total, set()
    for _ in range(20):
        lote = _bybit_get(path, {**extra_params, "startTime": ini_total, "endTime": cursor_end, "limit": 200})
        if not lote:
            break
        tss = []
        for d in lote:
            ts = int(d["timestamp"]); tss.append(ts)
            out[ts // HORA_MS] = d
        nuevo_end = min(tss) - 1
        if nuevo_end >= cursor_end or nuevo_end in vistas or nuevo_end <= ini_total:
            break
        vistas.add(nuevo_end); cursor_end = nuevo_end
        if len(lote) < 200:
            break
        time.sleep(0.15)
    return out

def fetch_bybit_oi_ls_1h(dias=LIQ_DIAS, symbol="BTCUSDT"):
    """Interés abierto (en unidades de la moneda) y proporción de cuentas en largo de Bybit (lineal),
    cada hora. Best-effort: cualquier fallo (símbolo, formato, límite de peticiones) hace que se devuelva
    None y el modelo siga solo con Binance, como hacía antes de tener esta fuente."""
    ck = f"bybit_oi_ls_1h:{symbol}:{dias}"
    cached = cache_get(ck)
    if cached is not None:
        return cached
    try:
        oi_raw = _bybit_paginado_1h("/v5/market/open-interest",
                                    {"category": "linear", "symbol": symbol, "intervalTime": "1h"}, dias)
        if len(oi_raw) < dias * 12:       # menos de la mitad de las horas esperadas: no nos fiamos
            log.warning(f"fetch_bybit_oi_ls_1h {symbol}: solo {len(oi_raw)} horas de {dias*24} esperadas, se descarta")
            return None
        ls_raw = _bybit_paginado_1h("/v5/market/account-ratio",
                                    {"category": "linear", "symbol": symbol, "period": "1h"}, dias)
        oi = {h: float(d["openInterest"]) for h, d in oi_raw.items()}
        ls = {h: float(d["buyRatio"]) for h, d in ls_raw.items()} if ls_raw else {}
        res = {"oi_btc": oi, "long_share": ls}
        cache_set(ck, res)
        return res
    except Exception as e:
        log.warning(f"fetch_bybit_oi_ls_1h {symbol}: {e}")
        return None

def _forward_fill_por_hora(dic, horas, default=np.nan):
    """Para cada hora pedida, el último valor conocido en o antes de esa hora (relleno hacia delante).
    'horas' son claves de hora entera (timestamp // 3600)."""
    if not dic:
        return np.full(len(horas), default, dtype=float)
    claves = np.array(sorted(dic.keys()))
    valores = np.array([dic[k] for k in claves], dtype=float)
    idx = np.searchsorted(claves, horas, side="right") - 1
    return np.where(idx >= 0, valores[np.clip(idx, 0, len(valores) - 1)], default)

def _enriquecer_con_bybit(d, symbol="BTCUSDT"):
    """Suma el interés abierto de Bybit (pasado a USD con el precio de Binance) al de Binance, y
    recalcula la proporción de largos como media ponderada por el interés abierto de cada exchange.
    Si Bybit falla o llega incompleto, 'd' se devuelve sin tocar: el modelo sigue solo con Binance,
    exactamente como antes de tener esta fuente."""
    d["fuentes"] = ["Binance"]
    try:
        by = fetch_bybit_oi_ls_1h(symbol=symbol)
        if not by or not by["oi_btc"]:
            return d
        horas = (d["t"] // 3600).astype(np.int64)
        oi_btc = _forward_fill_por_hora(by["oi_btc"], horas)      # en unidades de la moneda (BTC, ETH, SOL...)
        ls_by = _forward_fill_por_hora(by["long_share"], horas, default=0.5)
        cobertura = float(np.mean(~np.isnan(oi_btc)))
        if cobertura < 0.5:               # menos de la mitad de las velas tienen dato de Bybit: se descarta
            log.warning(f"_enriquecer_con_bybit {symbol}: cobertura {cobertura*100:.0f}%, se descarta")
            return d
        oi_usd = np.nan_to_num(oi_btc, nan=0.0) * d["c"]
        ls_by = np.nan_to_num(ls_by, nan=0.5)
        total = d["oi"] + oi_usd
        d["long_share"] = np.where(total > 0, (d["oi"] * d["long_share"] + oi_usd * ls_by) / total,
                                   d["long_share"])
        d["oi"] = total
        d["fuentes"].append("Bybit")
    except Exception as e:
        log.warning(f"_enriquecer_con_bybit {symbol}: {e}")
    return d

def _liq_datos(symbol, periodo, dias=LIQ_DIAS):
    """Descarga y alinea por vela: precios, interés abierto (USD) y proporción de largos."""
    paso = LIQ_PASOS_MS[periodo]
    kl = _fut_klines(symbol, periodo, dias)
    oi = _fut_hist_paginado("/futures/data/openInterestHist", symbol, dias, periodo)
    ls = _fut_hist_paginado("/futures/data/globalLongShortAccountRatio", symbol, dias, periodo)
    if not kl or not oi or len(kl) < 100:
        return None
    claves = [int(k[0]) // paso for k in kl]
    o = np.array([float(k[1]) for k in kl]); h = np.array([float(k[2]) for k in kl])
    lo = np.array([float(k[3]) for k in kl]); c = np.array([float(k[4]) for k in kl])
    oi_v, last = [], None
    for cl in claves:                     # interés abierto en USD; si falta una vela, arrastra la anterior
        d = oi.get(cl)
        if d is not None:
            last = float(d["sumOpenInterestValue"])
        oi_v.append(last)
    ls_v, lastl = [], 0.5
    for cl in claves:
        d = (ls or {}).get(cl)
        if d is not None:
            lastl = float(d["longAccount"])
        ls_v.append(lastl)
    ok = next((i for i, v in enumerate(oi_v) if v is not None), None)
    if ok is None:
        return None
    sl = slice(ok, None)                  # empezamos donde hay dato de interés abierto
    d = {"t": np.array(claves[sl]) * (paso // 1000), "o": o[sl], "h": h[sl], "l": lo[sl], "c": c[sl],
         "oi": np.array(oi_v[sl], dtype=float), "long_share": np.array(ls_v[sl], dtype=float),
         "paso_min": paso // 60000, "tiers": LIQ_TIERS_POR_SIMBOLO.get(symbol, LIQ_TIERS)}
    return _enriquecer_con_bybit(d, symbol)

def _semilla(p):
    """Precios de entrada (y pesos) de las posiciones que ya existían al empezar la ventana."""
    offs = np.arange(-LIQ_SEED_RANGO, LIQ_SEED_RANGO + 1e-9, 0.001)     # una entrada cada 0,1%: densidad continua
    w = np.exp(-0.5 * (offs / LIQ_SEED_SIGMA) ** 2)
    return p * (1 + offs), w / w.sum()

def modelo_liquidaciones(d, bins):
    """Devuelve matrices (velas x bins) con el USD estimado de liquidaciones de largos y de cortos."""
    n, nb = len(d["oi"]), len(bins) - 1
    typ = (d["h"] + d["l"] + d["c"]) / 3
    liq = np.zeros(0); amt = np.zeros(0); largo = np.zeros(0, dtype=bool); previo = np.zeros(0, dtype=bool)
    Lm, Sm, Pm = np.zeros((n, nb)), np.zeros((n, nb)), np.zeros((n, nb))
    pl_fin, ps_fin = np.zeros(nb), np.zeros(nb)
    for i in range(n):
        liq_l = liq_s = 0.0
        if i == 0:
            delta = d["oi"][0]            # arranque: se supone todo el interés abierto abierto ahora
        else:
            ml = largo & (liq >= d["l"][i]) & (amt > 0)     # el mínimo de la vela alcanza al largo
            ms = (~largo) & (liq <= d["h"][i]) & (amt > 0)  # el máximo alcanza al corto
            liq_l, liq_s = amt[ml].sum(), amt[ms].sum()
            amt[ml | ms] = 0.0
            delta = d["oi"][i] - d["oi"][i - 1]
        if delta > 0:                     # posiciones nuevas
            p, sh = typ[i], d["long_share"][i]
            entradas, pesos = ([p], [1.0]) if i > 0 else _semilla(p)
            nl, na, nlg = [], [], []
            for ep, we in zip(entradas, pesos):
                for lev, w in d.get("tiers", LIQ_TIERS).items():
                    nl += [ep * (1 - 1 / lev + LIQ_MMR), ep * (1 + 1 / lev - LIQ_MMR)]
                    na += [delta * sh * w * we, delta * (1 - sh) * w * we]
                    nlg += [True, False]
            if i == 0:                    # las que ya estarían liquidadas al precio de inicio no existen: se descartan
                nl, na, nlg = np.array(nl), np.array(na), np.array(nlg)
                vivas = np.where(nlg, nl < p, nl > p)
                if vivas.any():
                    na = na * (delta / na[vivas].sum())         # y se renormaliza al interés abierto real
                    nl, na, nlg = nl[vivas], na[vivas], nlg[vivas]
            liq, amt, largo = np.concatenate([liq, nl]), np.concatenate([amt, na]), np.concatenate([largo, nlg])
            previo = np.concatenate([previo, np.full(len(nl), i == 0)])   # la semilla es "previa a la ventana"
        elif delta < 0 and amt.sum() > 0:  # cierres voluntarios = bajada de OI que no fue liquidación
            cierre = max(0.0, -delta - (liq_l + liq_s))
            amt = amt * max(0.0, 1 - cierre / amt.sum())
        k = amt > 1.0
        liq, amt, largo, previo = liq[k], amt[k], largo[k], previo[k]
        if len(amt):
            Lm[i] = np.histogram(liq[largo], bins=bins, weights=amt[largo])[0]
            Sm[i] = np.histogram(liq[~largo], bins=bins, weights=amt[~largo])[0]
            Pm[i] = np.histogram(liq[previo], bins=bins, weights=amt[previo])[0]
            if i == n - 1:
                pl_fin = np.histogram(liq[previo & largo], bins=bins, weights=amt[previo & largo])[0]
                ps_fin = np.histogram(liq[previo & ~largo], bins=bins, weights=amt[previo & ~largo])[0]
    return Lm, Sm, Pm, pl_fin, ps_fin

def _suavizar(M, eje=1):
    k = np.array([1, 2, 3, 2, 1], dtype=float); k /= k.sum()
    return np.apply_along_axis(lambda v: np.convolve(v, k, mode="same"), eje, M)

def _picos(vec, centros, n=3, sep=0.012):
    """Los n mayores picos separados al menos 'sep' (1,2%). Devuelve (precio, USD del cluster ±0,6%, valor del bin)."""
    idx, elegidos = np.argsort(vec)[::-1], []
    for i in idx:
        if vec[i] <= 0 or len(elegidos) >= n:
            break
        if all(abs(centros[i] / centros[j] - 1) > sep for j in elegidos):
            elegidos.append(i)
    return [(float(centros[i]), float(vec[np.abs(centros / centros[i] - 1) <= 0.006].sum()), float(vec[i]))
            for i in elegidos]

def _e(t):
    """Escapa el $ para que matplotlib no lo tome por fórmula matemática."""
    return t.replace("$", r"\$")

def _usd(v):
    return f"${v/1e9:.2f}B" if v >= 1e9 else f"${v/1e6:.0f}M"

def calcular_liquidaciones(symbol="BTCUSDT"):
    ck = f"liq:{symbol}"
    cached = cache_get(ck)
    if cached is not None:
        return cached
    d = None
    for periodo in ("15m", "1h"):         # primero 15 min; si Binance no da esa resolución, cada hora
        d = _liq_datos(symbol, periodo)
        if d and len(d["oi"]) >= (LIQ_CALENTAMIENTO_H + 30) * 60 // d["paso_min"]:
            break
        d = None
    if not d:
        return None
    precio = float(d["c"][-1])
    bins = np.linspace(precio * 0.72, precio * 1.32, LIQ_NBINS + 1)
    Lm, Sm, Pm, pl_fin, ps_fin = modelo_liquidaciones(d, bins)
    centros = (bins[:-1] + bins[1:]) / 2
    L, S = _suavizar(Lm[-1:], 1)[0], _suavizar(Sm[-1:], 1)[0]
    PL, PS = _suavizar(pl_fin[None, :], 1)[0], _suavizar(ps_fin[None, :], 1)[0]
    Ln, Sn = np.maximum(L - PL, 0), np.maximum(S - PS, 0)      # solo lo nacido DENTRO de la ventana
    def suma(vec, a, b):
        return float(vec[(centros >= a) & (centros <= b)].sum())
    tot10 = suma(S, precio, precio * 1.10) + suma(L, precio * 0.90, precio)
    prev10 = suma(PS, precio, precio * 1.10) + suma(PL, precio * 0.90, precio)
    res = {"symbol": symbol, "moneda": symbol[:-4],
           "precio": precio, "bins": bins, "centros": centros, "t": d["t"], "paso_min": d["paso_min"],
           "o": d["o"], "h": d["h"], "l": d["l"], "c": d["c"], "close": d["c"],
           "Lm": Lm, "Sm": Sm, "Pm": Pm, "L": L, "S": S,
           "previo_pct": (prev10 / tot10 * 100) if tot10 > 0 else 0.0,
           "oi": float(d["oi"][-1]), "long_share": float(d["long_share"][-1]), "fuentes": d["fuentes"],
           "cortos_5": suma(S, precio, precio * 1.05), "cortos_10": suma(S, precio, precio * 1.10),
           "largos_5": suma(L, precio * 0.95, precio), "largos_10": suma(L, precio * 0.90, precio),
           "picos_cortos": _picos(np.where(centros > precio, Sn, 0), centros),
           "picos_largos": _picos(np.where(centros < precio, Ln, 0), centros),
           "hora": datetime.now(MADRID).strftime("%d/%m %H:%M")}
    cache_set(ck, res)
    return res

def _hora_madrid(ts):
    try:
        return datetime.fromtimestamp(int(ts), MADRID)
    except Exception:
        return datetime.fromtimestamp(int(ts))

def chart_liquidaciones_calor(res):
    """Visión de 30 días: mapa de calor en el tiempo + estado actual por niveles."""
    from matplotlib.colors import PowerNorm
    p = res["precio"]
    ini = LIQ_CALENTAMIENTO_H * 60 // res["paso_min"]
    fechas = mdates.date2num([datetime.utcfromtimestamp(int(t)) for t in res["t"][ini:]])
    M = _suavizar(res["Lm"] + res["Sm"])[ini:].T           # bins x tiempo
    vmax = float(np.percentile(M[M > 0], 99)) if (M > 0).any() else 1.0
    fig = plt.figure(figsize=(12, 10.5))
    fig.patch.set_facecolor('#0d1117')
    gs = fig.add_gridspec(1, 2, width_ratios=[3.3, 1.25], left=0.075, right=0.985, top=0.80, bottom=0.09, wspace=0.03)
    ax1 = fig.add_subplot(gs[0]); ax2 = fig.add_subplot(gs[1], sharey=ax1)
    for a in (ax1, ax2):
        a.set_facecolor('#0d1117')
        for sp in a.spines.values(): sp.set_color('#333333')
    cl = res["close"][ini:]
    ylo, yhi = min(p * 0.85, float(cl.min()) * 0.95), max(p * 1.12, float(cl.max()) * 1.05)
    ax1.imshow(M, extent=[fechas[0], fechas[-1], res["bins"][0], res["bins"][-1]], origin='lower',
               aspect='auto', cmap='inferno', norm=PowerNorm(gamma=0.5, vmin=0, vmax=vmax),
               interpolation='bilinear', zorder=1)
    ax1.plot(fechas, cl, color='white', linewidth=1.4, zorder=5)
    ax1.axhline(p, color='#00FFFF', linestyle='--', linewidth=1, alpha=0.8, zorder=4)
    ax1.set_ylim(ylo, yhi); ax1.set_xlim(fechas[0], fechas[-1])
    ax1.xaxis_date(); ax1.xaxis.set_major_formatter(mdates.DateFormatter('%d %b'))
    ax1.yaxis.set_major_formatter(lambda x, _: f"${x/1000:.0f}K")
    ax1.tick_params(colors='#AAAAAA', labelsize=11)
    ax1.set_title("Mapa de calor en el tiempo (más claro = más liquidaciones estimadas)", color='#AAAAAA',
                  fontsize=11, loc='left')

    paso = float(res["bins"][1] - res["bins"][0])
    c = res["centros"]
    S = np.where(c > p, res["S"], 0); L = np.where(c < p, res["L"], 0)
    ax2.barh(c, S, height=paso * 1.02, color='#FF4D4D', alpha=0.9, zorder=3)
    ax2.barh(c, L, height=paso * 1.02, color='#22CC66', alpha=0.9, zorder=3)
    ax2.axhline(p, color='#00FFFF', linestyle='--', linewidth=1, zorder=4)
    vis = (c >= ylo) & (c <= yhi)
    xmax = max(float(S[vis].max(initial=0)), float(L[vis].max(initial=0)), 1.0) * 1.9
    ax2.set_xlim(0, xmax); ax2.set_xticks([])
    ax2.tick_params(left=False, labelleft=False)
    ax2.set_title("Ahora: cortos ▲  ·  largos ▼", color='#AAAAAA', fontsize=11, loc='left')
    for precio_p, total, val in res["picos_cortos"][:3]:
        ax2.text(val * 1.08, precio_p, _e(f"${precio_p/1000:.1f}K · {_usd(total)}"), color='#FF9999',
                 fontsize=10, va='center', ha='left', clip_on=True)
    for precio_p, total, val in res["picos_largos"][:3]:
        ax2.text(val * 1.08, precio_p, _e(f"${precio_p/1000:.1f}K · {_usd(total)}"), color='#88EEAA',
                 fontsize=10, va='center', ha='left', clip_on=True)
    ax1.text(fechas[0] + (fechas[-1] - fechas[0]) * 0.01, p, _e(f"AHORA ${p:,.0f}"), color='#00FFFF', fontsize=10.5,
             fontweight='bold', va='bottom', ha='left', zorder=6,
             bbox=dict(boxstyle='round,pad=0.2', facecolor='#0d1117', edgecolor='none', alpha=0.8))

    fig.text(0.5, 0.965, "BTC — MAPA DE LIQUIDACIONES ESTIMADO (30 días)", ha='center', color='white', fontsize=19, fontweight='bold')
    fig.text(0.5, 0.932, f"{res['hora']} (Madrid)  ·  ESTIMACIÓN PROPIA con interés abierto de Binance Futures, últimos {LIQ_DIAS} días",
             ha='center', color='#FFB84D', fontsize=11)
    fig.text(0.5, 0.902, _e(f"Cortos por encima (se liquidan comprando): hasta +5% {_usd(res['cortos_5'])}  ·  hasta +10% {_usd(res['cortos_10'])}"),
             ha='center', color='#FF9999', fontsize=11.5, fontweight='bold')
    fig.text(0.5, 0.872, _e(f"Largos por debajo (se liquidan vendiendo): hasta −5% {_usd(res['largos_5'])}  ·  hasta −10% {_usd(res['largos_10'])}"),
             ha='center', color='#88EEAA', fontsize=11.5, fontweight='bold')
    fig.text(0.5, 0.835, "Es un modelo con supuestos (apalancamiento repartido de 5x a 100x): no son liquidaciones reales ni coincide con Glassnode o Coinglass.",
             ha='center', color='#777777', fontsize=9)
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=120, facecolor='#0d1117')
    plt.close()
    buf.seek(0)
    return buf


LIQ_CORTO_DIAS = 3               # 48h de calentamiento + ~24h limpias para el zoom corto
LIQ_CORTO_HORAS_ZOOM = 8         # cuántas horas finales se muestran en el zoom corto

def calcular_liquidaciones_corto(symbol="BTCUSDT"):
    """Igual que calcular_liquidaciones pero solo con velas de 5 minutos y una ventana corta
    (3 días), para un mapa más fino y concentrado en lo más reciente. Si Binance no da datos de
    interés abierto a 5 minutos (o llegan incompletos), devuelve None sin afectar a las otras
    dos vistas de /liquidaciones, que siguen calculándose por separado."""
    ck = f"liqcorto:{symbol}"
    cached = cache_get(ck)
    if cached is not None:
        return cached
    d = _liq_datos(symbol, "5m", dias=LIQ_CORTO_DIAS)
    if not d or len(d["oi"]) < (LIQ_CALENTAMIENTO_H + 6) * 60 // 5:
        return None
    precio = float(d["c"][-1])
    bins = np.linspace(precio * 0.85, precio * 1.15, LIQ_NBINS + 1)   # rango más estrecho: movimientos de pocos días
    Lm, Sm, Pm, pl_fin, ps_fin = modelo_liquidaciones(d, bins)
    centros = (bins[:-1] + bins[1:]) / 2
    L, S = _suavizar(Lm[-1:], 1)[0], _suavizar(Sm[-1:], 1)[0]
    PL, PS = _suavizar(pl_fin[None, :], 1)[0], _suavizar(ps_fin[None, :], 1)[0]
    Ln, Sn = np.maximum(L - PL, 0), np.maximum(S - PS, 0)
    def suma(vec, a, b):
        return float(vec[(centros >= a) & (centros <= b)].sum())
    res = {"symbol": symbol, "moneda": symbol[:-4],
           "precio": precio, "bins": bins, "centros": centros, "t": d["t"], "paso_min": d["paso_min"],
           "o": d["o"], "h": d["h"], "l": d["l"], "c": d["c"], "close": d["c"],
           "Lm": Lm, "Sm": Sm, "Pm": Pm, "L": L, "S": S, "fuentes": d["fuentes"],
           "cortos_5": suma(S, precio, precio * 1.05), "largos_5": suma(L, precio * 0.95, precio),
           "picos_cortos": _picos(np.where(centros > precio, Sn, 0), centros),
           "picos_largos": _picos(np.where(centros < precio, Ln, 0), centros),
           "hora": datetime.now(MADRID).strftime("%d/%m %H:%M")}
    cache_set(ck, res)
    return res

def chart_liquidaciones_corto(res):
    """Zoom corto plazo: últimas LIQ_CORTO_HORAS_ZOOM horas con velas de 5 min, el mismo estilo
    que el zoom de 24h pero mucho más concentrado."""
    paso = res["paso_min"]
    return _grafico_bloques(
        res, horas=LIQ_CORTO_HORAS_ZOOM, vela_min=paso, pad_pct=0.010, agrup_bins=1, ext=10,
        pcts=[86, 94, 98.2, 99.7],
        titulo=f"{res['moneda']} — LIQUIDACIONES ESTIMADAS · CORTO PLAZO ({LIQ_CORTO_HORAS_ZOOM} H, velas de {paso} min)",
        subtitulo=f"{res['hora']} (Madrid)  ·  ESTIMACIÓN PROPIA con interés abierto de {_texto_fuentes(res['fuentes'])}",
        aclaracion="Mismo modelo, con más detalle: interés abierto cada 5 min en vez de cada 15. "
                   "Cada bloque es un nivel sin tocar; se prolonga a la derecha hasta que lo toque.",
        fmt_x=lambda dt_: dt_.strftime("%H:%M"), cada_velas=max(1, 30 // paso), resumen=False,
        calentamiento=False, tf_txt=f"{paso}m")

def _grafico_bloques(res, horas, vela_min, pad_pct, agrup_bins, ext, pcts, titulo, subtitulo, aclaracion,
                     fmt_x, cada_velas, resumen, calentamiento, tf_txt):
    """Velas + un bloque por vela y nivel de liquidación estimado que sigue sin tocar (estilo TradingView).
    Sirve para el zoom de 24 h y para los 30 días: solo cambian el tamaño de vela, el zoom y los umbrales."""
    from matplotlib.collections import PatchCollection
    from matplotlib.patches import Rectangle, Patch
    p, paso = res["precio"], res["paso_min"]
    dec = _dec_precio(p)
    n_tot = len(res["c"])
    ini = (LIQ_CALENTAMIENTO_H * 60 // paso) if calentamiento else 0
    k = max(1, vela_min // paso)                              # pasos del modelo que forman una vela
    nc = min(n_tot - ini, horas * 60 // paso) // k
    sl = slice(n_tot - nc * k, n_tot)                         # alineado al final: la última vela es la actual
    o = res["o"][sl].reshape(nc, k)[:, 0]; c = res["c"][sl].reshape(nc, k)[:, -1]
    h = res["h"][sl].reshape(nc, k).max(axis=1); l = res["l"][sl].reshape(nc, k).min(axis=1)
    tt = res["t"][sl].reshape(nc, k)[:, 0]
    M = (res["Lm"] + res["Sm"])[sl].reshape(nc, k, -1)[:, -1, :]      # estado al final de cada vela
    PM = res["Pm"][sl].reshape(nc, k, -1)[:, -1, :]                   # la parte previa a la ventana
    pad = pad_pct * p
    ylo, yhi = float(l.min()) - pad, float(h.max()) + pad
    cen = res["centros"]
    paso_bin = float(res["bins"][1] - res["bins"][0])
    bi = np.where((cen >= ylo) & (cen <= yhi))[0]
    V, PV = M[:, bi], PM[:, bi]
    cen_v = cen[bi]
    if agrup_bins > 1:                                        # filas más gruesas para ventanas largas
        r = V.shape[1] // agrup_bins
        V = V[:, :r * agrup_bins].reshape(nc, r, agrup_bins).sum(axis=2)
        PV = PV[:, :r * agrup_bins].reshape(nc, r, agrup_bins).sum(axis=2)
        cen_v = cen_v[:r * agrup_bins].reshape(r, agrup_bins).mean(axis=1)
    N = np.maximum(V - PV, 0.0)                               # lo nacido dentro de la ventana: datos
    pos = N[N > 0]
    umbrales = np.percentile(pos, pcts) if len(pos) else np.array([np.inf] * 4)
    idx = np.digitize(N, umbrales)                            # 0 = no se dibuja; 1..4 = intensidad relativa
    gris = (PV >= umbrales[1]) & (idx == 0)                   # posiciones previas a la ventana: incierto (solo las más pesadas)
    COL = {1: '#1f6f7a', 2: '#2f9a2f', 3: '#a9a92a', 4: '#c62828'}
    hb = paso_bin * agrup_bins * 0.72
    fig = plt.figure(figsize=(12, 10 if resumen else 9.5))
    fig.patch.set_facecolor('#131722')
    top = 0.735 if resumen else 0.83
    ax = fig.add_axes([0.08, 0.13, 0.76, top - 0.13])
    ax.set_facecolor('#131722')
    for sp in ax.spines.values(): sp.set_color('#2a2e39')
    GRIS = '#555c6b'
    parches, cols = [], []
    for j, kk in zip(*np.nonzero(gris)):
        parches.append(Rectangle((j + 0.12, cen_v[kk] - hb / 2), 0.76, hb)); cols.append(GRIS)
    for j, kk in zip(*np.nonzero(idx)):
        parches.append(Rectangle((j + 0.12, cen_v[kk] - hb / 2), 0.76, hb)); cols.append(COL[int(idx[j, kk])])
    ax.add_collection(PatchCollection(parches, facecolors=cols, edgecolors='none', zorder=2))
    proy, pcols = [], []
    for kk in np.nonzero(idx[-1] | gris[-1])[0]:              # niveles aún vivos: se prolongan, apagados
        colp = COL[int(idx[-1, kk])] if idx[-1, kk] else GRIS
        for j in range(nc, nc + ext):
            proy.append(Rectangle((j + 0.12, cen_v[kk] - hb / 2), 0.76, hb)); pcols.append(colp)
    ax.add_collection(PatchCollection(proy, facecolors=pcols, edgecolors='none', alpha=0.48, zorder=2))
    ancho_v = 0.64 if nc <= 120 else 0.72
    for j in range(nc):                                       # velas
        col = '#26a69a' if c[j] >= o[j] else '#ef5350'
        ax.plot([j + 0.5, j + 0.5], [l[j], h[j]], color=col, linewidth=1 if nc <= 120 else 0.8, zorder=4)
        ax.add_patch(Rectangle((j + 0.5 - ancho_v / 2, min(o[j], c[j])), ancho_v, max(abs(c[j] - o[j]), p * 0.00006),
                               facecolor=col, edgecolor=col, zorder=5))
    ax.axhline(p, color='#26a69a', linestyle=':', linewidth=1, zorder=3)
    for picos, col in ((res["picos_cortos"], '#FF8888'), (res["picos_largos"], '#77DD99')):
        for precio_p, total, _v in picos[:3]:
            if ylo <= precio_p <= yhi:
                ax.axhline(precio_p, color=col, linestyle=':', linewidth=0.6, alpha=0.5, zorder=1)
                ax.text(nc + ext + 0.4, precio_p, _e(f"{_fp(precio_p)} · {_usd(total)}"), color=col,
                        fontsize=10, va='center', ha='left', clip_on=False)
    ax.set_xlim(0, nc + ext); ax.set_ylim(ylo, yhi)
    pos_x = list(range(0, nc, cada_velas))
    ax.set_xticks([x + 0.5 for x in pos_x])
    ax.set_xticklabels([fmt_x(_hora_madrid(tt[x])) for x in pos_x], color='#AAAAAA', fontsize=11)
    ax.tick_params(axis='y', colors='#AAAAAA', labelsize=11)
    ax.yaxis.set_major_formatter(lambda x, _: f"{x:,.{dec}f}")
    ax.grid(color='#1f2330', linestyle='-', linewidth=0.6, zorder=0)
    ax.text(nc + ext - 0.3, p + p * 0.0022, _e(f"AHORA {_fp(p)}"), color='#26a69a', fontsize=10.5, fontweight='bold',
            va='bottom', ha='right', zorder=6,
            bbox=dict(boxstyle='round,pad=0.2', facecolor='#131722', edgecolor='none', alpha=0.85))
    fig.text(0.5, 0.965, titulo, ha='center', color='white', fontsize=18, fontweight='bold')
    fig.text(0.5, 0.932, subtitulo, ha='center', color='#FFB84D', fontsize=11)
    fig.text(0.5, 0.900, aclaracion, ha='center', color='#AAAAAA', fontsize=10)
    fig.text(0.5, 0.874, "El color es la intensidad RELATIVA dentro de esta ventana, no dólares absolutos.",
             ha='center', color='#777777', fontsize=9.5)
    # Cabecera de precio en su propia franja, ENCIMA del eje: dentro del eje pisaba la etiqueta de precio
    # más alta del eje Y (quedaba tapada por el fondo de este recuadro).
    chg = float(c[-1] - c[-2]) if nc > 1 else 0.0
    pct = chg / float(c[-2]) * 100 if nc > 1 and c[-2] else 0.0
    cab = (f"{res['moneda']}/USDT · {tf_txt}   O {o[-1]:,.{dec}f}   H {h[-1]:,.{dec}f}   L {l[-1]:,.{dec}f}   "
           f"C {c[-1]:,.{dec}f}   {chg:+,.{dec}f} ({pct:+.2f}%)")
    fig.text(0.5, top + 0.018, cab, ha='center', va='bottom', fontsize=10.5, family='DejaVu Sans Mono',
             color=('#26a69a' if chg >= 0 else '#ef5350'))
    if resumen:
        fig.text(0.5, 0.842, _e(f"Cortos por encima (se liquidan comprando): hasta +5% {_usd(res['cortos_5'])}  ·  hasta +10% {_usd(res['cortos_10'])}"),
                 ha='center', color='#FF9999', fontsize=11.5, fontweight='bold')
        fig.text(0.5, 0.815, _e(f"Largos por debajo (se liquidan vendiendo): hasta −5% {_usd(res['largos_5'])}  ·  hasta −10% {_usd(res['largos_10'])}"),
                 ha='center', color='#88EEAA', fontsize=11.5, fontweight='bold')
        fig.text(0.5, 0.790, f"En gris, posiciones previas a la ventana (ubicación incierta): {res['previo_pct']:.0f}% de lo estimado dentro de ±10%",
                 ha='center', color='#9AA0AE', fontsize=10)
    ax.legend(handles=[Patch(color=COL[1], label='baja'), Patch(color=COL[2], label='media'),
                       Patch(color=COL[3], label='alta'), Patch(color=COL[4], label='máxima'),
                       Patch(color=GRIS, label='previa a la ventana (incierta)')],
              loc='upper center', bbox_to_anchor=(0.5, -0.07), ncol=5, frameon=False, labelcolor='#CCCCCC', fontsize=10.5)
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=120, facecolor='#131722')
    plt.close()
    buf.seek(0)
    return buf

def _texto_fuentes(fuentes):
    if len(fuentes) > 1:
        return " + ".join(fuentes) + " Futures"
    return f"{fuentes[0]} Futures (Bybit no disponible esta vez)"

def chart_liquidaciones_zoom(res):
    """Zoom de 24 h: velas de la resolución del modelo (15 min si Binance la da)."""
    paso = res["paso_min"]
    return _grafico_bloques(
        res, horas=24, vela_min=paso, pad_pct=0.018, agrup_bins=1, ext=8, pcts=[86, 94, 98.2, 99.7],
        titulo=f"{res['moneda']} — LIQUIDACIONES ESTIMADAS · ZOOM 24 H (velas de {paso} min)",
        subtitulo=f"{res['hora']} (Madrid)  ·  ESTIMACIÓN PROPIA con interés abierto de {_texto_fuentes(res['fuentes'])}",
        aclaracion="Cada bloque es un nivel de liquidación estimado que el precio todavía no ha tocado. "
                   "Se prolonga a la derecha hasta que lo toque.",
        fmt_x=lambda dt_: dt_.strftime("%H:%M"), cada_velas=max(1, 180 // paso), resumen=False, calentamiento=False,
        tf_txt=f"{paso}m")

def chart_liquidaciones(res):
    """Visión de 30 días con el MISMO estilo que el zoom: velas de 4 h y un bloque por nivel sin tocar."""
    return _grafico_bloques(
        res, horas=24 * LIQ_DIAS, vela_min=240, pad_pct=0.03, agrup_bins=3, ext=12, pcts=[88, 95, 98.5, 99.7],
        titulo=f"{res['moneda']} — LIQUIDACIONES ESTIMADAS · 30 DÍAS (velas de 4 h)",
        subtitulo=f"{res['hora']} (Madrid)  ·  ESTIMACIÓN PROPIA con interés abierto de {_texto_fuentes(res['fuentes'])}",
        aclaracion="Cada bloque es un nivel de liquidación estimado que el precio todavía no ha tocado. "
                   "Se prolonga a la derecha hasta que lo toque.",
        fmt_x=lambda dt_: dt_.strftime("%d %b"), cada_velas=30, resumen=True, calentamiento=True, tf_txt="4h")

def texto_liquidaciones(res, res_corto=None):
    p = res["precio"]
    mon = res["moneda"]
    if res_corto:
        intro_img = (f"🖼 Imagen 1: corto plazo ({LIQ_CORTO_HORAS_ZOOM} h, velas de {res_corto['paso_min']} min, "
                     f"el más fino de los tres). Imagen 2: zoom de 24 h (velas de {res['paso_min']} min). "
                     "Imagen 3: los 30 días (velas de 4 h).\n")
    else:
        intro_img = (f"🖼 Imagen 1: zoom de 24 h con velas de {res['paso_min']} min; cada bloque es un nivel aún sin "
                     "tocar y se prolonga a la derecha. Imagen 2: los 30 días con el mismo estilo (velas de 4 h).\n")
    L = ["📖 QUÉ ES ESTE MAPA\n",
         f"Estima en qué precios se liquidarían más posiciones apalancadas de {mon} si el precio llegase hasta allí. "
         "Al liquidarse, el exchange cierra la posición a la fuerza: un corto liquidado COMPRA y un largo liquidado "
         "VENDE, así que las zonas con muchas liquidaciones pueden acelerar el movimiento… o quedarse en nada.\n",
         intro_img,
        (f"Fuentes: {' + '.join(res['fuentes'])}. Bybit solo se actualiza cada hora (Binance cada "
         f"{res['paso_min']} min), así que entre horas se usa su último dato conocido.\n"
         if len(res["fuentes"]) > 1 else
         "Fuente: solo Binance — Bybit no ha podido consultarse esta vez.\n"),
         f"📊 AHORA — {mon} {_fp(p)}"]
    if res["picos_cortos"]:
        a = ", ".join(f"{_fp(x)} ({(x/p-1)*100:+.1f}%, ~{_usd(t)})" for x, t, _ in res["picos_cortos"])
        L.append(f"🔴 Zonas de cortos por encima: {a}")
    if res["picos_largos"]:
        b = ", ".join(f"{_fp(x)} ({(x/p-1)*100:+.1f}%, ~{_usd(t)})" for x, t, _ in res["picos_largos"])
        L.append(f"🟢 Zonas de largos por debajo: {b}")
    ratio = res["cortos_10"] / res["largos_10"] if res["largos_10"] > 0 else None
    if ratio:
        L.append(f"Dentro de ±10%: cortos {_usd(res['cortos_10'])} frente a largos {_usd(res['largos_10'])} "
                 f"({ratio:.2f} cortos por cada largo).")
    L.append(f"De lo estimado dentro de ±10%, un {res['previo_pct']:.0f}% son posiciones previas a la ventana (en gris, "
             "ubicación incierta). Los grupos etiquetados son solo de lo nacido dentro de los 30 días.")
    L.append(f"Interés abierto de Binance: {_usd(res['oi'])} · {res['long_share']*100:.0f}% de las cuentas en largo.\n")
    L.append("⚠️ Cómo leerlo con cabeza:\n"
             "• Es un MODELO con supuestos (reparto de apalancamiento, margen). Otros mapas usan otros supuestos y "
             "saldrán distintos.\n"
             "• Solo usa 30 días de datos: lo anterior no lo ve, y las primeras 48 h se descartan. Las posiciones "
             "que ya existían al empezar la ventana se dibujan en GRIS y se reparten de forma difusa alrededor "
             "del precio de entonces: no sabemos dónde se abrieron.\n"
             "• No distingue entre posiciones cubiertas o con margen cruzado.\n"
             "• Los colores del zoom son intensidad relativa dentro de la ventana.\n"
             "• Que haya liquidaciones estimadas en una zona no significa que el precio vaya hacia allí.")
    if mon != "BTC":
        L.append(f"• En {mon} hay menos interés abierto y más ruido que en BTC: es una estimación aún más aproximada.")
    if mon == "HYPE":
        L.append("• El interés abierto de HYPE en Hyperliquid (su propio exchange) NO entra en este modelo, solo Binance y Bybit: "
                 "el mapa ve solo una parte del mercado.")
    return "\n".join(L)

@bot.message_handler(commands=["liquidaciones", "mapacalor"])
@con_dyor
def cmd_liquidaciones(msg):
    if not is_premium(msg.from_user.id):
        safe_send(msg.chat.id, "Este bot es de uso personal y no está disponible para otros usuarios.")
        return
    partes = msg.text.split()
    moneda = partes[1].upper().replace("-USD", "").replace("USDT", "") if len(partes) > 1 else "BTC"
    symbol = LIQ_MONEDAS.get(moneda)
    if not symbol:
        safe_send(msg.chat.id, "Uso: /liquidaciones [MONEDA]\n\nDisponibles: " + ", ".join(LIQ_MONEDAS) +
                  "\nSin moneda, se muestra BTC.")
        return
    m = bot.send_message(msg.chat.id, f"Calculando el mapa de liquidaciones de {moneda}... (20-40s)")
    try:
        res = calcular_liquidaciones(symbol)
    except Exception as e:
        log.warning(f"calcular_liquidaciones {symbol}: {e}")
        res = None
    if not res:
        safe_send(msg.chat.id, "No he podido obtener los datos de Binance Futures ahora mismo. Reintenta en un momento.",
                  message_id=m.message_id)
        return
    try:
        res_corto = calcular_liquidaciones_corto(symbol)
    except Exception as e:
        log.warning(f"calcular_liquidaciones_corto {symbol}: {e}")
        res_corto = None
    caption = (f"🔥 LIQUIDACIONES ESTIMADAS — {moneda}\n{moneda} {_fp(res['precio'])}\n"
               f"Cortos hasta +5%: {_usd(res['cortos_5'])} · Largos hasta −5%: {_usd(res['largos_5'])}\n"
               + (f"Corto plazo ({LIQ_CORTO_HORAS_ZOOM} h, imagen 1), zoom 24 h (imagen 2) y 30 días (imagen 3)"
                  if res_corto else "Zoom de 24 h (imagen 1) y visión de 30 días (imagen 2)"))
    try:
        bot.delete_message(msg.chat.id, m.message_id)
    except Exception:
        pass
    imagenes = [("zoom 24h", chart_liquidaciones_zoom, res, caption), ("30 días", chart_liquidaciones, res, None)]
    if res_corto:
        imagenes.insert(0, ("corto plazo", chart_liquidaciones_corto, res_corto, caption))
        imagenes[1] = ("zoom 24h", chart_liquidaciones_zoom, res, None)
    for nombre, fn, res_img, cap in imagenes:
        try:
            img = fn(res_img)
            if cap:
                bot.send_photo(msg.chat.id, img, caption=cap[:1020])
            else:
                bot.send_photo(msg.chat.id, img)
        except Exception as e:
            log.warning(f"chart_liquidaciones ({nombre}): {e}")
            if cap:
                safe_send(msg.chat.id, cap)
    safe_send(msg.chat.id, texto_liquidaciones(res, res_corto))
    pc = ", ".join(f"{_fp(x)} ({(x/res['precio']-1)*100:+.1f}%, ~{_usd(t)})" for x, t, _ in res["picos_cortos"]) or "ninguna"
    pl = ", ".join(f"{_fp(x)} ({(x/res['precio']-1)*100:+.1f}%, ~{_usd(t)})" for x, t, _ in res["picos_largos"]) or "ninguna"
    prompt = (f"Mapa de liquidaciones ESTIMADO de {moneda} (modelo propio con interés abierto de Binance Futures de los "
              f"últimos 30 días, no datos reales de liquidaciones). {moneda} {_fp(res['precio'])}.\n"
              f"Zonas con más liquidaciones estimadas de CORTOS por encima del precio: {pc}.\n"
              f"Zonas con más liquidaciones estimadas de LARGOS por debajo: {pl}.\n"
              f"Interés abierto combinado de {' + '.join(res['fuentes'])}.\n"
              f"Dentro de ±5%: cortos {_usd(res['cortos_5'])}, largos {_usd(res['largos_5'])}. "
              f"Dentro de ±10%: cortos {_usd(res['cortos_10'])}, largos {_usd(res['largos_10'])}. De eso, un "
              f"{res['previo_pct']:.0f}% son posiciones previas a la ventana de 30 días, de ubicación incierta.\n\n"
              "Datos ya interpretados, úsalos tal cual. No inventes cifras ni noticias. Reglas: NO des "
              "recomendaciones de operativa (stops, objetivos, toma de beneficios, tamaño de posición, "
              "'compre' o 'venda').\n\n"
              "1. ¿Qué significan estas zonas y por qué las liquidaciones pueden acelerar un movimiento?\n"
              "2. ¿Qué NO se puede afirmar con un modelo estimado como este?\n"
              "3. ¿Qué otras señales conviene mirar junto a este mapa (interés abierto, funding, volumen)?")
    safe_send(msg.chat.id, f"ANÁLISIS IA\n\n{ask_ai(prompt)}")

# Lista de símbolos del S&P 500 (constituyentes reales, sacados de la tabla pública de Wikipedia
# el 23/09/2026 — https://en.wikipedia.org/wiki/List_of_S%26P_500_companies). El índice cambia
# unos pocos valores al año (fusiones, exclusiones); esta lista puede quedar desactualizada en
# alguno de esos casos, no se actualiza sola.
SP500_TICKERS = [
    'MMM', 'AOS', 'ABT', 'ABBV', 'ACN', 'ADBE', 'AMD', 'AES', 'AFL', 'A', 'APD', 'ABNB',
    'AKAM', 'ALB', 'ARE', 'ALGN', 'ALLE', 'LNT', 'ALL', 'GOOGL', 'GOOG', 'MO', 'AMZN', 'AMCR',
    'AEE', 'AEP', 'AXP', 'AIG', 'AMT', 'AWK', 'AMP', 'AME', 'AMGN', 'APH', 'ADI', 'AON',
    'APA', 'APO', 'AAPL', 'AMAT', 'APP', 'APTV', 'ACGL', 'ADM', 'ARES', 'ANET', 'AJG', 'AIZ',
    'T', 'ATO', 'ADSK', 'ADP', 'AZO', 'AVB', 'AVY', 'AXON', 'BKR', 'BALL', 'BAC', 'BAX',
    'BDX', 'BRK.B', 'BBY', 'TECH', 'BIIB', 'BLK', 'BX', 'XYZ', 'BNY', 'BA', 'BKNG', 'BSX',
    'BMY', 'AVGO', 'BR', 'BRO', 'BF.B', 'BLDR', 'BG', 'BXP', 'CHRW', 'CDNS', 'CPT', 'COF',
    'CAH', 'CCL', 'CARR', 'CVNA', 'CASY', 'CAT', 'CBOE', 'CBRE', 'CDW', 'COR', 'CNC', 'CNP',
    'CF', 'CRL', 'SCHW', 'CHTR', 'CVX', 'CMG', 'CB', 'CHD', 'CIEN', 'CI', 'CINF', 'CTAS',
    'CSCO', 'C', 'CFG', 'CLX', 'CME', 'CMS', 'KO', 'CTSH', 'COHR', 'COIN', 'CL', 'CMCSA',
    'FIX', 'COP', 'ED', 'STZ', 'CEG', 'COO', 'CPRT', 'GLW', 'CPAY', 'CTVA', 'CSGP', 'COST',
    'CRH', 'CRWD', 'CCI', 'CSX', 'CMI', 'CVS', 'DHR', 'DRI', 'DDOG', 'DVA', 'DECK', 'DE',
    'DELL', 'DAL', 'DVN', 'DXCM', 'FANG', 'DLR', 'DG', 'DLTR', 'D', 'DPZ', 'DASH', 'DOV',
    'DOW', 'DHI', 'DTE', 'DUK', 'DD', 'ETN', 'EBAY', 'ECHO', 'ECL', 'EIX', 'EW', 'EA',
    'ELV', 'EME', 'EMR', 'ETR', 'EOG', 'EQT', 'EFX', 'EQIX', 'EQR', 'ERIE', 'ESS', 'EL',
    'EG', 'EVRG', 'ES', 'EXC', 'EXE', 'EXPE', 'EXPD', 'EXR', 'XOM', 'FFIV', 'FDS', 'FICO',
    'FAST', 'FRT', 'FDX', 'FDXF', 'FIS', 'FITB', 'FSLR', 'FE', 'FISV', 'FLEX', 'F', 'FTNT',
    'FTV', 'FOXA', 'FOX', 'BEN', 'FCX', 'GRMN', 'IT', 'GE', 'GEHC', 'GEV', 'GEN', 'GNRC',
    'GD', 'GIS', 'GM', 'GPC', 'GILD', 'GPN', 'GL', 'GDDY', 'GS', 'HAL', 'HIG', 'HAS',
    'HCA', 'DOC', 'HSIC', 'HSY', 'HPE', 'HLT', 'HD', 'HONA', 'HON', 'HRL', 'HST', 'HWM',
    'HPQ', 'HUBB', 'HUM', 'HBAN', 'HII', 'IBM', 'IEX', 'IDXX', 'ITW', 'INCY', 'IR', 'PODD',
    'INTC', 'IBKR', 'ICE', 'IFF', 'IP', 'INTU', 'ISRG', 'IVZ', 'INVH', 'IQV', 'IRM', 'JBHT',
    'JBL', 'JKHY', 'J', 'JNJ', 'JCI', 'JPM', 'KVUE', 'KDP', 'KEY', 'KEYS', 'KMB', 'KIM',
    'KMI', 'KKR', 'KLAC', 'KHC', 'KR', 'LHX', 'LH', 'LRCX', 'LVS', 'LDOS', 'LEN', 'LII',
    'LLY', 'LIN', 'LYV', 'LMT', 'L', 'LOW', 'LULU', 'LITE', 'LYB', 'MTB', 'MPC', 'MAR',
    'MRSH', 'MLM', 'MRVL', 'MAS', 'MA', 'MKC', 'MCD', 'MCK', 'MDT', 'MRK', 'META', 'MET',
    'MTD', 'MGM', 'MCHP', 'MU', 'MSFT', 'MAA', 'MRNA', 'TAP', 'MDLZ', 'MPWR', 'MNST', 'MCO',
    'MS', 'MOS', 'MSI', 'MSCI', 'NDAQ', 'NTAP', 'NFLX', 'NEM', 'NWSA', 'NWS', 'NEE', 'NKE',
    'NI', 'NDSN', 'NSC', 'NTRS', 'NOC', 'NCLH', 'NRG', 'NUE', 'NVDA', 'NVR', 'NXPI', 'ORLY',
    'OXY', 'ODFL', 'OMC', 'ON', 'OKE', 'ORCL', 'OTIS', 'PCAR', 'PKG', 'PLTR', 'PANW', 'PSKY',
    'PH', 'PAYX', 'PYPL', 'PNR', 'PEP', 'PFE', 'PCG', 'PM', 'PSX', 'PNW', 'PNC', 'PPG',
    'PPL', 'PFG', 'PG', 'PGR', 'PLD', 'PRU', 'PEG', 'PTC', 'PSA', 'PHM', 'PWR', 'QCOM',
    'DGX', 'Q', 'RL', 'RJF', 'RTX', 'O', 'REG', 'REGN', 'RF', 'RSG', 'RMD', 'RVTY',
    'HOOD', 'ROK', 'ROL', 'ROP', 'ROST', 'RCL', 'SPGI', 'CRM', 'SNDK', 'SBAC', 'SLB', 'STX',
    'SRE', 'NOW', 'SHW', 'SPG', 'SWKS', 'SJM', 'SW', 'SNA', 'SOLV', 'SO', 'LUV', 'SWK',
    'SBUX', 'STT', 'STLD', 'STE', 'SYK', 'SMCI', 'SYF', 'SNPS', 'SYY', 'TMUS', 'TROW', 'TTWO',
    'TPR', 'TRGP', 'TGT', 'TEL', 'TDY', 'TER', 'TSLA', 'TXN', 'TPL', 'TXT', 'TMO', 'TJX',
    'TKO', 'TTD', 'TSCO', 'TT', 'TDG', 'TRV', 'TRMB', 'TFC', 'TYL', 'TSN', 'USB', 'UBER',
    'UDR', 'ULTA', 'UNP', 'UAL', 'UPS', 'URI', 'UNH', 'UHS', 'VLO', 'VEEV', 'VTR', 'VLTO',
    'VRSN', 'VRSK', 'VZ', 'VRTX', 'VRT', 'VTRS', 'VICI', 'V', 'VST', 'VMC', 'WRB', 'GWW',
    'WAB', 'WMT', 'DIS', 'WBD', 'WM', 'WAT', 'WEC', 'WFC', 'WELL', 'WST', 'WDC', 'WY',
    'WSM', 'WMB', 'WTW', 'WDAY', 'WYNN', 'XEL', 'XYL', 'YUM', 'ZBRA', 'ZBH', 'ZTS',
]
# ═══ /RSIMINIMOS — Activos tocando mínimos de RSI semanal (cripto + S&P 500) ═══
# "Tocando mínimos" = el RSI semanal ACTUAL está muy cerca del RSI más bajo que ese mismo activo
# ha tenido en los últimos RSIMIN_VENTANA_SEM (2 años). No es un umbral fijo tipo "RSI < 30": un
# activo puede llevar RSI 45 y aun así estar en su peor lectura en 2 años si nunca baja de ahí.
# Fuentes: Binance (velas semanales, cripto, sin clave) y Alpaca Markets (velas diarias del S&P
# 500 real, agregadas a semanales aquí — Alpaca no cobra por esto en su plan gratuito, feed IEX).
# Ni Binance ni Alpaca necesitan estar de acuerdo en nada: cada universo se calcula por separado
# y se combinan solo al final, en la clasificación.

RSIMIN_VENTANA_SEM = 104        # 2 años de velas semanales para buscar el mínimo propio de cada activo
RSIMIN_MIN_SEMANAS = 60         # por debajo de esto (activo muy nuevo) no se usa: RSI poco fiable
RSIMIN_TOP = 20                 # cuántos activos entran en el gráfico
RSIMIN_CACHE_H = 6
RSIMIN_CACHE_FILE = os.environ.get("RSIMIN_CACHE_FILE", _p("rsiminimos_cache.json"))
ALPACA_API_KEY = os.environ.get("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.environ.get("ALPACA_SECRET_KEY", "")

# Con 6 peticiones a la vez (ThreadPoolExecutor) Alpaca devolvía 429 en casi todas: el límite real
# (o al menos el que aguanta en ráfaga) es más estricto que las 200/min anunciadas. Con este candado
# TODAS las peticiones, vengan del hilo que vengan, se espacian contra el mismo cupo compartido.
_ALPACA_LOCK = threading.Lock()
_ALPACA_CALL_TIMES = []
_ALPACA_MAX_PER_MIN = 150       # por debajo de lo anunciado, con margen de sobra

def _throttle_alpaca():
    with _ALPACA_LOCK:
        while True:
            now = time.time()
            _ALPACA_CALL_TIMES[:] = [t for t in _ALPACA_CALL_TIMES if now - t < 60]
            if len(_ALPACA_CALL_TIMES) < _ALPACA_MAX_PER_MIN:
                _ALPACA_CALL_TIMES.append(now)
                return
            time.sleep(max(60 - (now - _ALPACA_CALL_TIMES[0]) + 0.05, 0.05))

def _rsimin_universo_cripto():
    """Las monedas más líquidas de Binance (mismo filtro de liquidez que /calientes), como lista
    de (symbol Binance, ticker corto)."""
    def _tickers():
        r = requests.get("https://api.binance.com/api/v3/ticker/24hr", timeout=15)
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}")
        return r.json()
    tk = with_retry(_tickers, tries=2, base_delay=2, what="ticker 24hr Binance (rsiminimos)")
    if not tk:
        return []
    cands = []
    for t in tk:
        s = t.get("symbol", "")
        if not s.endswith("USDT"):
            continue
        base = s[:-4]
        if base.lower() in _CALIENTES_EXCLUIR:
            continue
        try:
            qv = float(t.get("quoteVolume") or 0)
        except (ValueError, TypeError):
            continue
        if qv >= CALIENTES_MIN_USDT:
            cands.append((s, base, qv))
    cands.sort(key=lambda x: -x[2])
    return [(s, base) for s, base, _ in cands[:CALIENTES_UNIVERSO]]

def _rsimin_rsi_cripto(symbol):
    """RSI semanal (serie) de una moneda de Binance, últimas ~2,3 años."""
    def _do():
        r = requests.get("https://api.binance.com/api/v3/klines",
                         params={"symbol": symbol, "interval": "1w", "limit": RSIMIN_VENTANA_SEM + 20},
                         timeout=10)
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}")
        return r.json()
    kl = with_retry(_do, tries=2, base_delay=1.5, what=f"rsiminimos binance {symbol}")
    if not kl or len(kl) < RSIMIN_MIN_SEMANAS + 14:
        return None
    if kl[-1][6] > int(time.time() * 1000):
        kl = kl[:-1]                      # fuera la vela semanal en curso, incompleta
    cierres = pd.Series([float(k[4]) for k in kl])
    return calc_rsi(cierres, 14)

def _alpaca_get(path, params):
    def _do():
        _throttle_alpaca()
        r = requests.get(f"https://data.alpaca.markets{path}", params=params,
                         headers={"APCA-API-KEY-ID": ALPACA_API_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY},
                         timeout=12)
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:100]}")
        return r.json()
    return with_retry(_do, tries=2, base_delay=1.5, what=f"alpaca {path}")

def _rsimin_rsi_accion(ticker):
    """RSI semanal (serie) de una acción, agregando velas diarias de Alpaca. None si Alpaca falla,
    no tiene clave configurada, o no hay suficiente historial."""
    if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
        return None
    desde = (datetime.now(timezone.utc) - timedelta(days=int(RSIMIN_VENTANA_SEM * 7 * 1.15))).strftime("%Y-%m-%d")
    barras, cursor = [], None
    for _ in range(6):                    # tope de seguridad: nunca deberían hacer falta tantas páginas
        params = {"timeframe": "1Day", "start": desde, "limit": 1000, "feed": "iex", "adjustment": "split"}
        if cursor:
            params["page_token"] = cursor
        j = _alpaca_get(f"/v2/stocks/{ticker}/bars", params)
        if j is None:
            return None
        barras += j.get("bars") or []
        cursor = j.get("next_page_token")
        if not cursor:
            break
    if len(barras) < RSIMIN_MIN_SEMANAS * 5:      # ~5 sesiones por semana
        return None
    diarios = pd.Series([float(b["c"]) for b in barras],
                        index=pd.to_datetime([b["t"] for b in barras]))
    semanal = diarios.resample("W-FRI").last().dropna()
    if len(semanal) < RSIMIN_MIN_SEMANAS + 14:
        return None
    return calc_rsi(semanal, 14)

def calcular_rsiminimos():
    ck = "rsiminimos"
    cached = cache_get(ck)
    if cached is not None:
        return cached
    ahora = time.time()
    if not _RSIMIN_MEM["filas"] is None and ahora - _RSIMIN_MEM["ts"] < RSIMIN_CACHE_H * 3600:
        cache_set(ck, _RSIMIN_MEM["res"])
        return _RSIMIN_MEM["res"]
    try:
        with open(RSIMIN_CACHE_FILE, "r") as f:
            disco = json.load(f)
        if ahora - disco.get("ts", 0) < RSIMIN_CACHE_H * 3600 and disco.get("res"):
            _RSIMIN_MEM.update(ts=disco["ts"], filas=disco["res"]["filas"], res=disco["res"])
            cache_set(ck, disco["res"])
            return disco["res"]
    except Exception:
        pass

    from concurrent.futures import ThreadPoolExecutor
    filas, sin_alpaca = [], not (ALPACA_API_KEY and ALPACA_SECRET_KEY)

    cripto = _rsimin_universo_cripto()
    def _job_cripto(par):
        symbol, base = par
        try:
            serie = _rsimin_rsi_cripto(symbol)
        except Exception as e:
            log.warning(f"rsiminimos cripto {symbol}: {e}")
            return None
        return _rsimin_evaluar(base, "cripto", serie)
    with ThreadPoolExecutor(max_workers=5) as ex:
        for r in ex.map(_job_cripto, cripto):
            if r:
                filas.append(r)

    if not sin_alpaca:
        def _job_accion(ticker):
            try:
                serie = _rsimin_rsi_accion(ticker)
            except Exception as e:
                log.warning(f"rsiminimos accion {ticker}: {e}")
                return None
            return _rsimin_evaluar(ticker, "acción", serie)
        with ThreadPoolExecutor(max_workers=3) as ex:
            for r in ex.map(_job_accion, SP500_TICKERS):
                if r:
                    filas.append(r)

    if not filas:
        return None
    filas.sort(key=lambda f: f["dist"])
    res = {"filas": filas, "top": filas[:RSIMIN_TOP], "n_cripto": sum(1 for f in filas if f["tipo"] == "cripto"),
           "n_acciones": sum(1 for f in filas if f["tipo"] == "acción"), "sin_alpaca": sin_alpaca,
           "universo_cripto": len(cripto), "universo_acciones": len(SP500_TICKERS) if not sin_alpaca else 0,
           "hora": datetime.now(MADRID).strftime("%d/%m %H:%M")}
    _RSIMIN_MEM.update(ts=ahora, filas=filas, res=res)
    try:
        tmp = RSIMIN_CACHE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"ts": ahora, "res": res}, f)
        os.replace(tmp, RSIMIN_CACHE_FILE)
    except Exception as e:
        log.warning(f"rsiminimos cache disco: {e}")
    cache_set(ck, res)
    return res

_RSIMIN_MEM = {"ts": 0, "filas": None, "res": None}

def _rsimin_evaluar(nombre, tipo, serie):
    if serie is None:
        return None
    v = serie.dropna()
    if len(v) < RSIMIN_MIN_SEMANAS:
        return None
    ventana = v.iloc[-RSIMIN_VENTANA_SEM:] if len(v) > RSIMIN_VENTANA_SEM else v
    actual = float(ventana.iloc[-1])
    idx_min = int(np.argmin(ventana.values))
    minimo = float(ventana.iloc[idx_min])
    maximo = float(ventana.max())
    semanas_desde_min = len(ventana) - 1 - idx_min
    return {"nombre": nombre, "tipo": tipo, "actual": actual, "minimo": minimo, "maximo": maximo,
            "dist": actual - minimo, "semanas_desde_min": semanas_desde_min, "n_semanas": len(ventana)}

def chart_rsiminimos(res):
    filas = list(reversed(res["top"]))          # el más cerca de su mínimo, arriba
    n = len(filas)
    fig = plt.figure(figsize=(11, max(6, 1.6 + n * 0.42)))
    fig.patch.set_facecolor('#0d1117')
    ax = fig.add_axes([0.24, 0.10, 0.70, 0.78])
    ax.set_facecolor('#0d1117')
    COL = {"cripto": "#f0b90b", "acción": "#3b82f6"}
    ys = np.arange(n)
    for y, f in zip(ys, filas):
        col = COL[f["tipo"]]
        ax.plot([f["minimo"], f["maximo"]], [y, y], color=col, alpha=0.35, linewidth=4, solid_capstyle='round', zorder=2)
        ax.plot(f["minimo"], y, '|', color=col, markersize=10, markeredgewidth=2, zorder=3)
        ax.plot(f["maximo"], y, '|', color=col, markersize=10, markeredgewidth=2, zorder=3)
        ax.plot(f["actual"], y, 'v', color=col, markersize=15, markeredgecolor='white', markeredgewidth=1.3, zorder=5)
        ax.text(-2, y, f["nombre"], ha='right', va='center', color='white', fontsize=10.5, fontweight='bold')
        ax.text(102, y, f"{f['actual']:.0f}", ha='left', va='center', color=col, fontsize=10.5, fontweight='bold')
    ax.axvline(30, color='#ef4444', linestyle=':', linewidth=1, alpha=0.6, zorder=1)
    ax.text(30, n - 0.3, ' 30', color='#ef4444', fontsize=8.5, va='bottom', ha='left', alpha=0.8)
    ax.set_xlim(0, 100); ax.set_ylim(-1, n)
    ax.set_yticks([])
    ax.set_xlabel("RSI semanal", color='#AAAAAA', fontsize=10.5)
    ax.tick_params(axis='x', colors='#777777', labelsize=9)
    for sp in ax.spines.values(): sp.set_color('#333333')
    ax.grid(axis='x', color='#1f2330', linestyle='--', linewidth=0.6, zorder=0)
    fig.text(0.5, 0.965, "RSI SEMANAL — MÁS CERCA DE SU MÍNIMO DE 2 AÑOS", ha='center', color='white',
             fontsize=16, fontweight='bold')
    fig.text(0.5, 0.938, f"{res['hora']} (Madrid)  ·  ▼ = RSI actual  ·  la barra es su rango de 2 años (mín–máx)",
             ha='center', color='#FFB84D', fontsize=10.5)
    fig.text(0.5, 0.915, f"Cripto: {res['n_cripto']} de {res['universo_cripto']} analizadas (Binance)  ·  "
             f"Acciones: {res['n_acciones']} de {res['universo_acciones']} (S&P 500, Alpaca)",
             ha='center', color='#999999', fontsize=9.5)
    ax.plot([], [], color=COL["cripto"], linewidth=4, label='Cripto')
    ax.plot([], [], color=COL["acción"], linewidth=4, label='Acción (S&P 500)')
    ax.legend(loc='lower right', frameon=False, labelcolor='#CCCCCC', fontsize=9.5)
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=120, facecolor='#0d1117')
    plt.close()
    buf.seek(0)
    return buf

def texto_rsiminimos(res):
    L = ["📖 QUÉ ES ESTE LISTADO\n",
         "Para cada activo, calcula el RSI semanal (14 semanas) y lo compara con el rango que ese "
         "mismo activo ha tenido en las últimas 2 años. No es un umbral fijo: un activo puede llevar "
         "RSI 45 y aun así estar en su peor lectura de los últimos 2 años, si nunca ha bajado de ahí. "
         "Los de arriba del gráfico son los que ahora mismo están más cerca de su propio mínimo.\n"]
    for f in res["top"][:10]:
        pos = ("en su mínimo de 2 años" if f["semanas_desde_min"] == 0 else
               f"su mínimo fue hace {f['semanas_desde_min']} semanas")
        L.append(f"• {f['nombre']} ({f['tipo']}): RSI {f['actual']:.0f} — {pos} "
                 f"(rango 2a: {f['minimo']:.0f}-{f['maximo']:.0f})")
    if res["sin_alpaca"]:
        L.append("\n⚠️ No hay clave de Alpaca configurada: solo se ha podido analizar cripto, sin acciones.")
    L.append("\n⚠️ Cómo leerlo con cabeza:\n"
             "• Un RSI en mínimos no significa que vaya a rebotar. A veces se queda ahí semanas o "
             "meses porque el activo sigue cayendo (lo que se llama quedarse 'pegado' a sobreventa).\n"
             "• Es una lectura técnica sobre el pasado reciente, no una predicción ni una señal de compra.\n"
             "• El RSI de las acciones se calcula agregando velas diarias a semanales; el de las "
             "criptos usa velas semanales directas de Binance.")
    return "\n".join(L)

@bot.message_handler(commands=["reset_rsiminimos"])
def cmd_reset_rsiminimos(msg):
    if not allowed(msg):
        return
    _RSIMIN_MEM.update(ts=0, filas=None, res=None)
    _CACHE.pop("rsiminimos", None)
    try:
        os.remove(RSIMIN_CACHE_FILE)
        disco = "borrada"
    except FileNotFoundError:
        disco = "no existía"
    safe_send(msg.chat.id, f"Caché de /rsiminimos borrada (archivo: {disco}). El próximo /rsiminimos "
             "recalcula desde cero.")

@bot.message_handler(commands=["rsiminimos"])
@con_dyor
def cmd_rsiminimos(msg):
    if not is_premium(msg.from_user.id):
        safe_send(msg.chat.id, "Este bot es de uso personal y no está disponible para otros usuarios.")
        return
    m = bot.send_message(msg.chat.id, "Escaneando cripto y el S&P 500 en busca de mínimos de RSI... "
                                      "(la primera vez del día puede tardar 2-3 min)")
    try:
        res = calcular_rsiminimos()
    except Exception as e:
        log.warning(f"calcular_rsiminimos: {e}")
        res = None
    if not res:
        safe_send(msg.chat.id, "No he podido calcularlo ahora mismo. Reintenta en un rato.",
                  message_id=m.message_id)
        return
    top3 = ", ".join(f"{f['nombre']} (RSI {f['actual']:.0f})" for f in res["top"][:3])
    caption = f"📉 RSI SEMANAL EN MÍNIMOS\nMás cerca de su mínimo de 2 años: {top3}"
    try:
        img = chart_rsiminimos(res)
        bot.delete_message(msg.chat.id, m.message_id)
        bot.send_photo(msg.chat.id, img, caption=caption[:1020])
    except Exception as e:
        log.warning(f"chart_rsiminimos: {e}")
        safe_send(msg.chat.id, caption, message_id=m.message_id)
    safe_send(msg.chat.id, texto_rsiminimos(res))
    lista = "\n".join(f"- {f['nombre']} ({f['tipo']}): RSI {f['actual']:.0f}, rango 2a {f['minimo']:.0f}-{f['maximo']:.0f}, "
                      f"mínimo hace {f['semanas_desde_min']} semanas" for f in res["top"])
    prompt = ("Listado de activos (cripto y acciones del S&P 500) cuyo RSI SEMANAL está ahora mismo "
              f"más cerca de su propio mínimo de los últimos 2 años:\n{lista}\n\n"
              "Datos ya calculados, úsalos tal cual, no inventes cifras ni noticias. Reglas: NO des "
              "recomendaciones de operativa (comprar, vender, entradas, stops, objetivos). No afirmes "
              "que vayan a rebotar. Sin negritas ni formato markdown.\n\n"
              "1. ¿Qué tienen en común, si algo, los activos que aparecen en esta lista?\n"
              "2. ¿Por qué un RSI en mínimos no implica que el precio vaya a girar?\n"
              "3. ¿Qué otras señales conviene mirar junto a esto antes de sacar conclusiones?")
    safe_send(msg.chat.id, f"ANÁLISIS IA\n\n{ask_ai(prompt)}")

def _scheduler_loop():
    global _ultimo_broadcast_key, _ultimo_resumen_diario_key
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
        except Exception as e:
            log.error(f"_scheduler_loop: {e}")
        time.sleep(60)
# ═══ /VWAP — Precio medio ponderado por volumen de la sesión actual ═══
# El VWAP no es una media cualquiera: es el precio medio al que se ha negociado un activo DESDE
# que empezó la sesión actual, ponderado por el volumen de cada vela (los tramos con más volumen
# pesan más). Se reinicia al empezar cada sesión nueva. Necesita velas intradía para tener
# sentido — con velas diarias o de varias horas apenas habría puntos por sesión.
# Cripto (Binance, sin clave): sesión = desde las 00:00 UTC.
# Acciones (Alpaca): sesión = desde la apertura de Wall Street (15:30 hora española en verano,
# 16:30 en invierno); si el mercado está cerrado, se usa la última sesión completa.

VWAP_VELA_MIN = 15
VWAP_HORAS_ZOOM = 10        # cuánto se muestra alrededor de la sesión (con margen a los lados)

def _vwap_ticker_a_fuente(ticker):
    """Decide si un ticker es cripto (Binance) o acción (Alpaca), reutilizando el mismo mapa que
    ya usa /valor. Devuelve ('cripto', simbolo_binance) o ('accion', ticker_normalizado)."""
    t = normalize_ticker(ticker)
    simbolo_binance = BINANCE_MAP.get(t)
    if simbolo_binance:
        return "cripto", simbolo_binance
    return "accion", t

def _vwap_velas_cripto(simbolo_binance):
    """Velas de 15 min de Binance, suficientes para cubrir la sesión UTC de hoy y algo de ayer
    de margen. Sin clave, mismo endpoint que el resto del bot."""
    def _do():
        r = requests.get("https://api.binance.com/api/v3/klines",
                         params={"symbol": simbolo_binance, "interval": f"{VWAP_VELA_MIN}m", "limit": 200},
                         timeout=10)
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}")
        return r.json()
    kl = with_retry(_do, tries=2, base_delay=1.5, what=f"vwap binance {simbolo_binance}")
    if not kl:
        return None
    if kl[-1][6] > int(time.time() * 1000):
        kl = kl[:-1]                      # fuera la vela en curso, incompleta
    t = np.array([int(k[0]) // 1000 for k in kl])
    o = np.array([float(k[1]) for k in kl]); h = np.array([float(k[2]) for k in kl])
    l = np.array([float(k[3]) for k in kl]); c = np.array([float(k[4]) for k in kl])
    v = np.array([float(k[5]) for k in kl])
    hoy_utc = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    inicio = int(hoy_utc.timestamp())
    m = t >= inicio
    if m.sum() < 2:                       # sesión recién empezada: coge la de ayer de referencia
        inicio -= 86400
        m = t >= inicio
    return {"t": t[m], "o": o[m], "h": h[m], "l": l[m], "c": c[m], "v": v[m],
            "reset_txt": "00:00 UTC", "fuente": "Binance"}

def _vwap_velas_accion(ticker):
    """Velas de 15 min de Alpaca (feed IEX), agrupadas por sesión de Wall Street; se queda con
    la sesión más reciente (la de hoy si el mercado está abierto, si no la última completa)."""
    if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
        return None
    desde = (datetime.now(timezone.utc) - timedelta(days=6)).strftime("%Y-%m-%d")
    barras, cursor = [], None
    for _ in range(4):
        params = {"timeframe": f"{VWAP_VELA_MIN}Min", "start": desde, "limit": 1000, "feed": "iex"}
        if cursor:
            params["page_token"] = cursor
        j = _alpaca_get(f"/v2/stocks/{ticker}/bars", params)
        if j is None:
            return None
        barras += j.get("bars") or []
        cursor = j.get("next_page_token")
        if not cursor:
            break
    if len(barras) < 2:
        return None
    ny = pytz.timezone("America/New_York")
    fechas_ny = [datetime.fromisoformat(b["t"].replace("Z", "+00:00")).astimezone(ny) for b in barras]
    dia_sesion = max(f.date() for f in fechas_ny)      # la sesión más reciente presente en los datos
    idx = [i for i, f in enumerate(fechas_ny) if f.date() == dia_sesion]
    if len(idx) < 2:
        return None
    t = np.array([int(fechas_ny[i].timestamp()) for i in idx])
    o = np.array([float(barras[i]["o"]) for i in idx]); h = np.array([float(barras[i]["h"]) for i in idx])
    l = np.array([float(barras[i]["l"]) for i in idx]); c = np.array([float(barras[i]["c"]) for i in idx])
    v = np.array([float(barras[i]["v"]) for i in idx])
    return {"t": t, "o": o, "h": h, "l": l, "c": c, "v": v,
            "reset_txt": "apertura de Wall Street", "fuente": "Alpaca"}

def _vwap_calcular(d):
    """VWAP acumulado desde el inicio de la sesión, más bandas de ±1 y ±2 desviaciones (ponderadas
    por volumen, la misma idea que un Bollinger pero centrado en el VWAP en vez de una media simple)."""
    tp = (d["h"] + d["l"] + d["c"]) / 3
    cum_v = np.cumsum(d["v"])
    cum_v_seguro = np.where(cum_v > 0, cum_v, 1e-9)
    vwap = np.cumsum(tp * d["v"]) / cum_v_seguro
    var = np.cumsum(d["v"] * (tp - vwap) ** 2) / cum_v_seguro
    std = np.sqrt(np.maximum(var, 0))
    return vwap, std

def calcular_vwap(ticker):
    ck = f"vwap:{ticker}"
    cached = cache_get(ck)
    if cached is not None:
        return cached
    tipo, simbolo = _vwap_ticker_a_fuente(ticker)
    d = _vwap_velas_cripto(simbolo) if tipo == "cripto" else _vwap_velas_accion(simbolo)
    if not d:
        return None
    vwap, std = _vwap_calcular(d)
    precio = float(d["c"][-1])
    v_actual, s_actual = float(vwap[-1]), float(std[-1])
    dist_pct = (precio - v_actual) / v_actual * 100 if v_actual else 0.0
    dist_sigma = (precio - v_actual) / s_actual if s_actual > 0 else 0.0
    res = {**d, "vwap": vwap, "std": std, "precio": precio, "tipo": tipo, "ticker_mostrado": ticker.upper(),
           "dist_pct": dist_pct, "dist_sigma": dist_sigma,
           "hora": datetime.now(MADRID).strftime("%d/%m %H:%M")}
    cache_set(ck, res)
    return res

def chart_vwap(res):
    n = len(res["c"])
    fig = plt.figure(figsize=(11, 8.5))
    fig.patch.set_facecolor('#0d1117')
    ax = fig.add_axes([0.09, 0.11, 0.86, 0.70])
    ax.set_facecolor('#0d1117')
    for sp in ax.spines.values(): sp.set_color('#333333')
    x = np.arange(n)
    vwap, std = res["vwap"], res["std"]
    ax.fill_between(x, vwap - 2 * std, vwap + 2 * std, color='#3b82f6', alpha=0.16, zorder=1, label='±2σ')
    ax.fill_between(x, vwap - std, vwap + std, color='#3b82f6', alpha=0.30, zorder=1, label='±1σ (rango normal)')
    ax.plot(x, vwap, color='#f0b90b', linewidth=2, zorder=3, label='VWAP')
    ancho = 0.62
    for i in range(n):
        col = '#26a69a' if res["c"][i] >= res["o"][i] else '#ef5350'
        ax.plot([i, i], [res["l"][i], res["h"][i]], color=col, linewidth=1, zorder=2)
        ax.add_patch(plt.Rectangle((i - ancho / 2, min(res["o"][i], res["c"][i])), ancho,
                                   max(abs(res["c"][i] - res["o"][i]), res["precio"] * 0.0003),
                                   facecolor=col, edgecolor=col, zorder=4))
    ax.axhline(res["precio"], color='white', linestyle=':', linewidth=0.8, alpha=0.6, zorder=2)
    ext = max(2, n // 12)
    ax.text(n + ext - 0.5, res["precio"], f"AHORA ${res['precio']:,.2f}", color='white', fontsize=10.5,
           fontweight='bold', va='center', ha='right', zorder=6,
           bbox=dict(boxstyle='round,pad=0.2', facecolor='#0d1117', edgecolor='none', alpha=0.85))
    cada = max(1, (60 // VWAP_VELA_MIN) * 2)
    pos_x = list(range(0, n, cada))
    ax.set_xticks(pos_x)
    tz = pytz.timezone("America/New_York") if res["tipo"] == "accion" else timezone.utc
    ax.set_xticklabels([datetime.fromtimestamp(res["t"][i], tz).strftime("%H:%M") for i in pos_x],
                       color='#AAAAAA', fontsize=9.5)
    ax.set_xlim(-1, n + ext)
    ax.tick_params(axis='y', colors='#AAAAAA', labelsize=10)
    ax.grid(color='#1f2330', linestyle='--', linewidth=0.6, zorder=0)
    ax.legend(loc='upper left', frameon=False, labelcolor='#CCCCCC', fontsize=10)
    fig.text(0.5, 0.965, f"{res['ticker_mostrado']} — VWAP DE HOY (velas de {VWAP_VELA_MIN} min)",
             ha='center', color='white', fontsize=17, fontweight='bold')
    fig.text(0.5, 0.935, f"{res['hora']} (Madrid)  ·  sesión desde {res['reset_txt']}  ·  fuente: {res['fuente']}",
             ha='center', color='#FFB84D', fontsize=10.5)
    signo = "por encima" if res["dist_pct"] >= 0 else "por debajo"
    fig.text(0.5, 0.905, f"Precio {abs(res['dist_pct']):.2f}% {signo} del VWAP ({res['dist_sigma']:+.1f}σ)",
             ha='center', color='#CCCCCC', fontsize=11)
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=120, facecolor='#0d1117')
    plt.close()
    buf.seek(0)
    return buf

def texto_vwap(res):
    signo = "por encima" if res["dist_pct"] >= 0 else "por debajo"
    if abs(res["dist_sigma"]) < 1:
        zona = "dentro de su rango normal de hoy (±1σ)"
    elif abs(res["dist_sigma"]) < 2:
        zona = "en la banda ancha, algo estirado respecto a hoy (entre 1σ y 2σ)"
    else:
        zona = "fuera de su rango habitual de hoy (más de 2σ)"
    L = ["📖 QUÉ ES EL VWAP\n",
         "El precio medio al que se ha negociado el activo desde que empezó la sesión, ponderado "
         "por el volumen de cada tramo (los momentos con más volumen pesan más que los de poco "
         "volumen). Se reinicia cada sesión. Las bandas son la desviación del precio respecto a "
         "ese VWAP, ponderada igual: dicen si el movimiento de hoy es normal o se ha salido de lo "
         "habitual.\n",
         f"📊 {res['ticker_mostrado']}: ${res['precio']:,.2f}",
         f"VWAP de hoy: ${res['vwap'][-1]:,.2f} — el precio está un {abs(res['dist_pct']):.2f}% {signo}, "
         f"{zona}.",
         "\n⚠️ Cómo leerlo con cabeza:\n"
         "• Esto describe dónde está el precio ahora respecto al promedio de hoy, no predice hacia "
         "dónde va a ir.\n"
         "• Con cripto la sesión se reinicia a las 00:00 UTC aunque el mercado no cierre nunca; con "
         "acciones, a la apertura de Wall Street.\n"
         "• Muy al principio de la sesión hay pocas velas y las bandas pueden ser poco fiables."]
    return "\n".join(L)

URL_CINTA_DIRECTO = os.environ.get(
    "URL_CINTA_DIRECTO",
    "https://francescgonzalezarribas-pixel.github.io/dashboard/cinta_bolsa.html"
)

@bot.message_handler(commands=["directo"])
def cmd_directo(msg):
    if not is_premium(msg.from_user.id):
        safe_send(msg.chat.id, "Este bot es de uso personal y no está disponible para otros usuarios.")
        return
    safe_send(msg.chat.id,
        "📡 CINTA DE PRECIOS EN DIRECTO\n\n"
        "35 criptos moviéndose en tiempo real, con aviso cuando alguna se mueve más de un 5% hoy.\n\n"
        f"{URL_CINTA_DIRECTO}\n\n"
        "Es una página aparte (no dentro de Telegram): tócala para abrirla en el navegador.")

@bot.message_handler(commands=["vwap"])
@con_dyor
def cmd_vwap(msg):
    if not is_premium(msg.from_user.id):
        safe_send(msg.chat.id, "Este bot es de uso personal y no está disponible para otros usuarios.")
        return
    partes = msg.text.split(maxsplit=1)
    ticker = partes[1].strip().upper() if len(partes) > 1 else "BTC"
    m = bot.send_message(msg.chat.id, f"Calculando el VWAP de {ticker}...")
    try:
        res = calcular_vwap(ticker)
    except Exception as e:
        log.warning(f"calcular_vwap {ticker}: {e}")
        res = None
    if not res:
        motivo = ("" if (ALPACA_API_KEY and ALPACA_SECRET_KEY) else
                  " (si es una acción, revisa que ALPACA_API_KEY/ALPACA_SECRET_KEY estén puestas)")
        safe_send(msg.chat.id, f"No he podido calcular el VWAP de {ticker} ahora mismo.{motivo} "
                 "Prueba con otro ticker o vuelve a intentarlo en un rato.", message_id=m.message_id)
        return
    signo = "por encima" if res["dist_pct"] >= 0 else "por debajo"
    caption = (f"📊 VWAP — {res['ticker_mostrado']}\n${res['precio']:,.2f}  ·  "
               f"{abs(res['dist_pct']):.2f}% {signo} del VWAP de hoy")
    try:
        img = chart_vwap(res)
        bot.delete_message(msg.chat.id, m.message_id)
        bot.send_photo(msg.chat.id, img, caption=caption[:1020])
    except Exception as e:
        log.warning(f"chart_vwap {res['ticker_mostrado']}: {e}")
        safe_send(msg.chat.id, caption, message_id=m.message_id)
    safe_send(msg.chat.id, texto_vwap(res))
    prompt = (f"VWAP de {res['ticker_mostrado']} ({'cripto, sesión desde 00:00 UTC' if res['tipo']=='cripto' else 'acción, sesión de Wall Street'}). "
              f"Precio actual ${res['precio']:,.2f}, VWAP ${res['vwap'][-1]:,.2f}, "
              f"desviación {res['dist_pct']:+.2f}% ({res['dist_sigma']:+.1f} desviaciones estándar de la sesión).\n\n"
              "Datos ya calculados, úsalos tal cual. No inventes cifras ni noticias.\n\n"
              "1. ¿Qué dice esta posición respecto al VWAP sobre cómo ha ido la sesión de hoy?\n"
              "2. ¿Qué significa que el precio esté a esa distancia en desviaciones estándar?\n"
              "3. Qué otras señales conviene mirar junto al VWAP antes de sacar conclusiones")
    safe_send(msg.chat.id, f"ANÁLISIS IA\n\n{ask_ai(prompt)}")

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
        safe_send(msg.chat.id, "Este bot es de uso personal y no está disponible para otros usuarios.")
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
        safe_send(msg.chat.id, "Este bot es de uso personal y no está disponible para otros usuarios.")
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

━━━ /ballenas TICKER ━━━
Muros de compra/venta grandes en el order book (solo cripto).

━━━ /cartera NOMBRE ━━━
Cartera trimestral (13F) de grandes inversores — Buffett, Ackman, Burry y 15 más. Datos oficiales SEC.

━━━ /insiders TICKER ━━━
Compras/ventas de directivos en mercado abierto (SEC Form 4). Avisa si hay compra agrupada (3+ insiders a la vez).

━━━ /macro ━━━
Tipos Fed, inflación, paro, bonos (FRED) + derivados cripto (Binance).

━━━ /compresion ━━━
Mide lo estrecho que está el rango de precio de BTC en los últimos 30 días frente a los últimos 12 meses, con un velocímetro (0% expandido, 100% compresión extrema), dónde está el precio dentro del rango y la historia. Una compresión alta suele anteceder a un movimiento fuerte, pero no dice hacia dónde. Aproximación propia con datos de Binance, no coincide exactamente con CryptoQuant.

━━━ /liquidaciones [MONEDA] ━━━
Mapa de calor ESTIMADO de dónde se liquidarían más posiciones apalancadas (cortos por encima del precio, largos por debajo). Disponible para BTC, ETH, SOL y HYPE (/liquidaciones ETH; sin moneda, BTC). Incluye corto plazo, zoom de 24 h estilo TradingView (velas y un bloque por nivel sin tocar) y la visión de 30 días. Es un modelo propio con el interés abierto de Binance y Bybit Futures: no son liquidaciones reales y no coincide con Glassnode o Coinglass. En HYPE no entra el interés abierto de Hyperliquid, su propio exchange.

━━━ /vwap TICKER ━━━
Precio medio ponderado por volumen de la sesión actual (VWAP), con bandas de ±1 y ±2 desviaciones. Cripto por Binance (sesión desde 00:00 UTC), acciones por Alpaca (sesión desde la apertura de Wall Street). Sin ticker, BTC por defecto. Describe dónde está el precio hoy, no predice hacia dónde va.

━━━ /rsiminimos ━━━
Cripto (Binance) y acciones del S&P 500 (Alpaca) cuyo RSI semanal está más cerca de su propio mínimo de los últimos 2 años. No es un umbral fijo: compara cada activo con su propio rango. Un RSI en mínimos no implica que vaya a rebotar.""",

"""📖 GUÍA DE COMANDOS (3/3) — Noticias y automatizaciones

━━━ /noticias ━━━
Titulares de bolsa/economía/cripto de varias fuentes, con análisis de IA basado solo en los titulares reales.

━━━ /ticker ━━━
Resumen visual al momento de ~30 activos (cripto, acciones, índices, oro).

━━━ Automatizaciones (sin comando) ━━━
• Cada 2h (9-21h): mismo resumen visual de /ticker, automático
• Cada mañana 8h: resumen diario (BTC, Fear&Greed, titulares)
• Alertas de noticias muy relevantes, cuando la IA las detecta

━━━ Importante ━━━
Usa /dyor para leer el aviso legal antes de tomar decisiones con lo que veas aquí."""
]

@bot.message_handler(commands=["guia"])
def cmd_guia(msg):
    if not is_premium(msg.from_user.id):
        safe_send(msg.chat.id, "Este bot es de uso personal y no está disponible para otros usuarios.")
        return
    for parte in GUIA_PARTES:
        safe_send(msg.chat.id, parte)
        time.sleep(0.3)


# ═══ /DYOR — Aviso legal / descargo de responsabilidad ══════════
TEXTO_DYOR = """⚠️ AVISO IMPORTANTE — LÉEME

Este bot es una herramienta de ANÁLISIS INFORMATIVO, no un servicio de asesoramiento financiero. Antes de usarlo, ten esto claro:

📊 No es una recomendación de inversión. Ningún comando (/valor, /ciclo, análisis de IA...) te dice qué comprar, vender, o cuándo. Son indicadores y datos para que TÚ decidas con tu propio criterio.

🤖 La IA puede equivocarse. Los análisis generados son orientativos, no verdad absoluta.

📡 Los datos pueden fallar o tener errores. Este bot depende de fuentes gratuitas de terceros (Binance, Stooq, SEC, CFTC, FRED, AAII...). A veces fallan, se retrasan, o cambian sin avisar. Verifica cifras importantes antes de actuar.

💸 Invertir conlleva riesgo real de pérdida. Rendimientos pasados no garantizan resultados futuros. Nunca inviertas dinero que no puedas permitirte perder.

🧑‍💼 No somos asesores financieros regulados. Para decisiones importantes, consulta con un profesional cualificado.

En resumen: DYOR — Do Your Own Research. Usa este bot como una herramienta más en tu proceso de análisis, nunca como la única fuente de tu decisión."""

@bot.message_handler(commands=["dyor"])
def cmd_dyor(msg):
    safe_send(msg.chat.id, TEXTO_DYOR)


# ═══ Menú de comandos de Telegram (lo que sale al pulsar "/") ═══
# El orden de esta lista es el orden del menú: /dyor va el primero.
MENU_COMANDOS = [
    ("dyor", "⚠️ Aviso legal — léelo antes de usar el bot"),
    ("start", "Inicio y lista de comandos"),
    ("guia", "Explicación completa de cada comando"),
    ("valor", "Índice barato/caro 0-100 de un activo"),
    ("fundamental", "Análisis fundamental 0-100 de una acción"),
    ("halvingbtc", "Ciclo de 4 años de Bitcoin"),
    ("ciclo", "Fase actual de BTC en el ciclo de mercado"),
    ("dominancia", "Zonas de compra/venta de BTC (Fear & Greed)"),
    ("ballenas", "Muros de órdenes grandes en Binance"),
    ("cartera", "Carteras 13F de grandes inversores"),
    ("insiders", "Compras/ventas de directivos (SEC Form 4)"),
    ("macro", "Tipos, inflación, paro y derivados cripto"),
    ("compresion", "Compresión de precio de BTC (volatilidad 30 días)"),
    ("liquidaciones", "Mapa de liquidaciones estimado (BTC, ETH, SOL, HYPE)"),
    ("rsiminimos", "Cripto y acciones del S&P 500 cerca de su mínimo de RSI (2 años)"),
    ("vwap", "VWAP de hoy con bandas — cripto o acciones, TICKER opcional"),
    ("noticias", "Noticias de bolsa, economía y cripto"),
    ("ticker", "Resumen de mercados al momento"),
    ("directo", "Enlace a la cinta de precios cripto en directo (35 monedas)"),
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





