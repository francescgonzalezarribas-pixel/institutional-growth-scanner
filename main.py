"""
Financial Telegram Bot - Version Completa v6
- Señales con confirmacion 2 timeframes + filtro mercado general
- Stop loss dinamico basado en ATR
- /seguimiento - tracking trades abiertos con P&L real
- /resumen_semana - resumen viernes de señales
- /ayuda - guia de comandos
- Derivados ETH y SOL en Binance
- Alertas automaticas: funding rate, Fear&Greed, VIX
- Job lunes plan semana, job domingo resumen crypto
"""

import os, io, logging, time, feedparser
import yfinance as yf
import requests, pytz
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import mplfinance as mpf
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from mistralai.client import MistralClient as Mistral
from mistralai.models.chat_completion import ChatMessage
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from collections import defaultdict

TELEGRAM_TOKEN  = os.environ["TELEGRAM_TOKEN"]
MISTRAL_API_KEY = os.environ["MISTRAL_API_KEY"]
ALLOWED_USER_ID = int(os.environ.get("ALLOWED_USER_ID", 0))
MADRID = pytz.timezone("Europe/Madrid")

SYSTEM = """Eres un analista financiero senior. Reglas:
- Responde SIEMPRE en espanol
- Sin markdown especial, texto plano
- Maximo 4 parrafos o listas cortas
- Da conclusiones concretas y accionables
- Precios exactos en tus recomendaciones"""

ai_client = Mistral(api_key=MISTRAL_API_KEY)
bot = telebot.TeleBot(TELEGRAM_TOKEN)
ALERTS = defaultdict(list)
SEGUIMIENTO = defaultdict(list)  # trades abiertos: {ticker, entrada, tp1, tp2, stop, fecha, lado}

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

# Nombres completos
NOMBRES = {
    "SAN.MC":"Banco Santander","BBVA.MC":"BBVA","ITX.MC":"Inditex",
    "REP.MC":"Repsol","TEF.MC":"Telefonica","IBE.MC":"Iberdrola",
    "ELE.MC":"Endesa","AMS.MC":"Amadeus IT",
    "OR.PA":"L'Oreal","BNP.PA":"BNP Paribas","AIR.PA":"Airbus",
    "MC.PA":"LVMH","TTE.PA":"TotalEnergies","LVMH.PA":"LVMH","BN.PA":"Danone",
    "BMW.DE":"BMW","BAS.DE":"BASF","DTE.DE":"Deutsche Telekom",
    "VOW3.DE":"Volkswagen","SIE.DE":"Siemens","SAP":"SAP",
    "HSBA.L":"HSBC","BP.L":"BP","SHEL.L":"Shell","AZN.L":"AstraZeneca","RIO.L":"Rio Tinto",
    "ASML":"ASML Holding","NESN.SW":"Nestle","NOVO-B.CO":"Novo Nordisk",
    "AAPL":"Apple","MSFT":"Microsoft","NVDA":"Nvidia","AMZN":"Amazon",
    "GOOGL":"Alphabet","META":"Meta","TSLA":"Tesla","AMD":"AMD","INTC":"Intel",
    "CRM":"Salesforce","ORCL":"Oracle",
    "JPM":"JPMorgan","GS":"Goldman Sachs","BAC":"Bank of America","V":"Visa","MA":"Mastercard",
    "JNJ":"Johnson & Johnson","UNH":"UnitedHealth","PFE":"Pfizer",
    "XOM":"ExxonMobil","CVX":"Chevron",
    "WMT":"Walmart","HD":"Home Depot","MCD":"McDonalds","KO":"Coca-Cola","PEP":"PepsiCo",
    "BA":"Boeing","CAT":"Caterpillar","NFLX":"Netflix","DIS":"Disney",
    "BTC-USD":"Bitcoin","ETH-USD":"Ethereum","SOL-USD":"Solana","BNB-USD":"BNB",
    "SPY":"S&P 500 ETF","QQQ":"Nasdaq 100 ETF","IWM":"Russell 2000 ETF","DIA":"Dow Jones ETF",
    "VTI":"Total Market ETF","VEA":"Europa ETF","EWG":"DAX ETF","EWQ":"CAC 40 ETF","EZU":"Eurozona ETF",
    "XLK":"Tech ETF","XLF":"Finanzas ETF","XLE":"Energia ETF","XLV":"Salud ETF",
    "XLI":"Industrial ETF","XLY":"Consumo ETF","XLP":"Consumo Basico ETF",
    "XLU":"Utilities ETF","XLB":"Materiales ETF","XLRE":"Inmobiliario ETF","XLC":"Comunicaciones ETF",
    "GLD":"Oro ETF","SLV":"Plata ETF","USO":"Petroleo ETF","PDBC":"Commodities ETF",
    "ARKK":"ARK Innovation ETF","BOTZ":"Robotica IA ETF","ICLN":"Energia Limpia ETF",
    "HACK":"Ciberseguridad ETF","SOXX":"Semiconductores ETF",
    "MCHI":"China ETF","EEM":"Emergentes ETF",
    "LUNR":"Intuitive Machines","RKLB":"Rocket Lab","ASTS":"AST SpaceMobile",
    "ACHR":"Archer Aviation","JOBY":"Joby Aviation",
}

def nombre(ticker):
    return NOMBRES.get(ticker, ticker)

def es_dia_laborable():
    """Lunes=0 ... Viernes=4. Sabado=5, Domingo=6."""
    return datetime.now(MADRID).weekday() < 5

def es_mercado_eu_abierto():
    now = datetime.now(MADRID)
    return es_dia_laborable() and 9 <= now.hour < 18

def es_mercado_us_abierto():
    now = datetime.now(MADRID)
    return es_dia_laborable() and 15 <= now.hour < 23

IBEX = ["SAN.MC","BBVA.MC","ITX.MC","REP.MC","TEF.MC","IBE.MC","ELE.MC","AMS.MC"]
CAC = ["OR.PA","BNP.PA","AIR.PA","MC.PA","TTE.PA","LVMH.PA","BN.PA"]
DAX_S = ["BMW.DE","BAS.DE","DTE.DE","VOW3.DE","SIE.DE","SAP"]
FTSE_S = ["HSBA.L","BP.L","SHEL.L","AZN.L","RIO.L"]
OTHER_EU = ["ASML","NESN.SW","NOVO-B.CO"]
EU_STOCKS = IBEX + CAC + DAX_S + FTSE_S + OTHER_EU

US_STOCKS = [
    "AAPL","MSFT","NVDA","AMZN","GOOGL","META","TSLA","AMD","INTC","CRM","ORCL",
    "JPM","GS","BAC","V","MA","JNJ","UNH","PFE","XOM","CVX",
    "WMT","HD","MCD","KO","PEP","BA","CAT","NFLX","DIS",
]

CRYPTO = ["BTC-USD","ETH-USD","SOL-USD","BNB-USD"]
METALES = {"GC=F":"Oro","SI=F":"Plata","PL=F":"Platino","HG=F":"Cobre","PA=F":"Paladio"}

INDICES = {
    "^GSPC":"S&P 500","^DJI":"Dow Jones","^IXIC":"Nasdaq",
    "^STOXX50E":"Euro Stoxx 50","^GDAXI":"DAX","^FTSE":"FTSE 100",
    "^IBEX":"IBEX 35","^FCHI":"CAC 40",
}

MACRO_TICKERS = {
    "^VIX":"VIX Miedo","DX-Y.NYB":"DXY Dolar","^TNX":"US10Y Bono",
    "GC=F":"Oro","CL=F":"Petroleo WTI",
}

ETFS_INDICES = ["SPY","QQQ","IWM","DIA","VTI","VEA","EWG","EZU"]
ETFS_TEMATICOS = ["GLD","SLV","USO","ARKK","BOTZ","ICLN","HACK","SOXX","EEM","MCHI"]
ETFS_ESPECIALES = ["LUNR","RKLB","ASTS","ACHR","JOBY"]

SECTORES_SP500 = {
    "Tecnologia": ["AAPL","MSFT","NVDA","AMD","INTC","CRM","ORCL","XLK"],
    "Finanzas": ["JPM","GS","BAC","V","MA","XLF"],
    "Salud": ["JNJ","UNH","PFE","XLV"],
    "Energia": ["XOM","CVX","XLE"],
    "Consumo Discrecional": ["TSLA","AMZN","HD","MCD","NFLX","XLY"],
    "Consumo Basico": ["WMT","KO","PEP","XLP"],
    "Industrial": ["BA","CAT","XLI"],
    "Comunicaciones": ["GOOGL","META","DIS","XLC"],
    "Materiales": ["XLB"],
    "Utilities": ["XLU"],
    "Inmobiliario": ["XLRE"],
}

RSS_FEEDS = [
    "https://feeds.marketwatch.com/marketwatch/topstories/",
    "https://news.google.com/rss/search?q=stock+market+europe+usa&hl=en&gl=US&ceid=US:en",
    "https://feeds.reuters.com/reuters/businessNews",
]

RSS_IMPACTO = [
    "https://news.google.com/rss/search?q=merger+acquisition+takeover+2026&hl=en&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=earnings+surprise+beat+2026&hl=en&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=FDA+approval+drug+2026&hl=en&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=SpaceX+NASA+contract+IPO+2026&hl=en&gl=US&ceid=US:en",
]

IPOS_WATCH = [
    {"nombre":"SpaceX","ticker":None,"sector":"Aeroespacial","valor":"$1.5T","estado":"Proxima","bolsa":"NYSE"},
    {"nombre":"OpenAI","ticker":None,"sector":"IA","valor":"$1T","estado":"Proxima","bolsa":"NASDAQ"},
    {"nombre":"Kraken","ticker":None,"sector":"Crypto","valor":"$20B","estado":"Proxima","bolsa":"NASDAQ"},
    {"nombre":"Revolut","ticker":None,"sector":"Fintech","valor":"$75B","estado":"Proxima","bolsa":"NASDAQ"},
    {"nombre":"Canva","ticker":None,"sector":"SaaS","valor":"$42B","estado":"Proxima","bolsa":"NYSE"},
    {"nombre":"Cerebras","ticker":"CBRS","sector":"Chips IA","valor":"$48B","estado":"Reciente","bolsa":"NASDAQ"},
    {"nombre":"Lincoln Intl","ticker":"LCLN","sector":"Banca","valor":"$1.94B","estado":"Esta semana","bolsa":"NYSE"},
]


# Mapa ticker yfinance -> id CoinGecko para precio real time
COINGECKO_IDS = {
    "BTC-USD": "bitcoin",
    "ETH-USD": "ethereum",
    "SOL-USD": "solana",
    "BNB-USD": "binancecoin",
}

def get_realtime_price(ticker):
    """Precio en tiempo real. Intenta Binance primero, luego CoinGecko."""
    symbol_map = {
        "BTC-USD": "BTCUSDT",
        "ETH-USD": "ETHUSDT",
        "SOL-USD": "SOLUSDT",
        "BNB-USD": "BNBUSDT",
    }
    binance_sym = symbol_map.get(ticker)

    # Intento 1: Binance (sin auth, tiempo real)
    if binance_sym:
        try:
            r = requests.get(
                f"https://api.binance.com/api/v3/ticker/24hr?symbol={binance_sym}",
                timeout=6
            )
            data = r.json()
            return {
                "price": round(float(data["lastPrice"]), 2),
                "d1": round(float(data["priceChangePercent"]), 2),
            }
        except Exception as e:
            log.warning(f"Binance realtime {ticker}: {e}")

    # Intento 2: CoinGecko (fallback)
    cg_id = COINGECKO_IDS.get(ticker)
    if cg_id:
        try:
            r = requests.get(
                f"https://api.coingecko.com/api/v3/simple/price"
                f"?ids={cg_id}&vs_currencies=usd&include_24hr_change=true",
                timeout=8
            )
            data = r.json()[cg_id]
            return {
                "price": round(data["usd"], 2),
                "d1": round(data.get("usd_24h_change", 0), 2),
            }
        except Exception as e:
            log.warning(f"CoinGecko realtime {ticker}: {e}")

    return None


def calc_rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_g = gain.ewm(com=period-1, min_periods=period).mean()
    avg_l = loss.ewm(com=period-1, min_periods=period).mean()
    rs = avg_g / avg_l.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def calc_macd(series, fast=12, slow=26, signal=9):
    ema_f = series.ewm(span=fast, adjust=False).mean()
    ema_s = series.ewm(span=slow, adjust=False).mean()
    macd = ema_f - ema_s
    sig = macd.ewm(span=signal, adjust=False).mean()
    return macd, sig


def fetch_quote(ticker, period="3mo"):
    # Intenta con el periodo solicitado, luego fallback
    for p in [period, "1mo", "3mo"]:
        try:
            hist = yf.Ticker(ticker).history(period=p)
            if hist.empty or len(hist) < 5:
                continue
            c, h, lo = hist["Close"], hist["High"], hist["Low"]
            vol = hist["Volume"]
            price = c.iloc[-1]
            d1 = (price - c.iloc[-2]) / c.iloc[-2] * 100 if len(c) > 1 else 0
            d5 = (price - c.iloc[-6]) / c.iloc[-6] * 100 if len(c) > 5 else 0
            d20 = (price - c.iloc[-21]) / c.iloc[-21] * 100 if len(c) > 20 else 0
            pivot = (h.iloc[-1] + lo.iloc[-1] + c.iloc[-1]) / 3
            r1 = 2*pivot - lo.iloc[-1]
            s1 = 2*pivot - h.iloc[-1]
            hi20 = h.tail(20).max() if len(h) >= 20 else h.max()
            lo20 = lo.tail(20).min() if len(lo) >= 20 else lo.min()
            rng = hi20 - lo20
            r2 = hi20 + rng * 0.382
            s2 = lo20 - rng * 0.382
            rsi = calc_rsi(c).iloc[-1] if len(c) >= 14 else 50
            macd_l, macd_s = calc_macd(c)
            macd_cross_up = (len(macd_l) >= 2 and
                             macd_l.iloc[-1] > macd_s.iloc[-1] and
                             macd_l.iloc[-2] <= macd_s.iloc[-2])
            avg_vol = vol.tail(20).mean() if len(vol) >= 20 else vol.mean()
            vol_rel = vol.iloc[-1] / avg_vol if avg_vol > 0 else 1.0
            # ATR 14 para stop loss dinamico
            high_low = h - lo
            high_close = (h - c.shift()).abs()
            low_close  = (lo - c.shift()).abs()
            tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
            atr = tr.ewm(span=14, adjust=False).mean().iloc[-1]

            ema20_s = c.ewm(span=20, adjust=False).mean()
            ema50_s = c.ewm(span=50, adjust=False).mean()
            ema20_v = ema20_s.iloc[-1]
            ema50_v = ema50_s.iloc[-1]
            sobre_ema20 = price > ema20_v
            sobre_ema50 = price > ema50_v
            # EMA20 sobre EMA50 = tendencia alcista
            tendencia_alcista = ema20_v > ema50_v
            # EMA20 con pendiente positiva (subiendo)
            ema20_subiendo = ema20_s.iloc[-1] > ema20_s.iloc[-3] if len(ema20_s) >= 3 else False
            # Breakout: distancia al maximo de 20 dias
            max20 = h.tail(20).max()
            dist_breakout = round((max20 - price) / max20 * 100, 2) if max20 > 0 else 99
            cerca_breakout = dist_breakout <= 1.5
            # Fuerza relativa: rendimiento mensual superior al mercado
            fuerza_relativa = d20 > 2.0
            hi52 = round(h.tail(252).max(), 2) if len(h) >= 252 else round(h.max(), 2)
            lo52 = round(lo.tail(252).min(), 2) if len(lo) >= 252 else round(lo.min(), 2)

            # Para crypto: sobreescribir precio con dato en tiempo real
            if ticker in COINGECKO_IDS:
                rt = get_realtime_price(ticker)
                if rt:
                    price = rt["price"]
                    d1 = rt["d1"]

            return {
                "ticker": ticker,
                "nombre": nombre(ticker),
                "price": round(price, 4) if price < 10 else round(price, 2),
                "d1": round(d1, 2), "d5": round(d5, 2), "d20": round(d20, 2),
                "pivot": round(pivot, 2),
                "r1": round(r1, 2), "r2": round(r2, 2),
                "s1": round(s1, 2), "s2": round(s2, 2),
                "hi52": hi52, "lo52": lo52,
                "vol_rel": round(vol_rel, 2),
                "rsi": round(rsi, 1),
                "macd_cross_up": macd_cross_up,
                "sobre_ema20": sobre_ema20,
                "sobre_ema50": sobre_ema50,
                "tendencia_alcista": tendencia_alcista,
                "ema20_subiendo": ema20_subiendo,
                "ema20": round(ema20_v, 2),
                "ema50": round(ema50_v, 2),
                "cerca_breakout": cerca_breakout,
                "dist_breakout": dist_breakout,
                "fuerza_relativa": fuerza_relativa,
                "max20": round(max20, 2),
                "atr": round(atr, 2),
            }
        except Exception as e:
            log.warning(f"fetch_quote {ticker} period={p}: {e}")
            continue
    return None


def get_binance_derivatives(symbol="BTCUSDT"):
    """Funding rate, open interest, long/short ratio y liquidaciones desde Binance Futures."""
    resultado = {}

    # 1. Funding Rate
    try:
        r = requests.get(
            f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={symbol}",
            timeout=6
        )
        data = r.json()
        funding = round(float(data["lastFundingRate"]) * 100, 4)
        if funding > 0.05:
            funding_señal = "MUY POSITIVO: longs pagando mucho, posible squeeze bajista"
        elif funding > 0.01:
            funding_señal = "POSITIVO: mercado alcista con coste"
        elif funding < -0.01:
            funding_señal = "NEGATIVO: shorts pagando, posible rebote"
        else:
            funding_señal = "NEUTRO: mercado equilibrado"
        resultado["funding"] = {"valor": funding, "señal": funding_señal}
    except Exception as e:
        log.warning(f"Funding rate: {e}")

    # 2. Open Interest
    try:
        r = requests.get(
            f"https://fapi.binance.com/fapi/v1/openInterest?symbol={symbol}",
            timeout=6
        )
        oi = round(float(r.json()["openInterest"]), 0)
        resultado["open_interest"] = {"btc": oi}
    except Exception as e:
        log.warning(f"Open Interest: {e}")

    # 3. Open Interest en USD (historico para ver tendencia)
    try:
        r = requests.get(
            f"https://fapi.binance.com/futures/data/openInterestHist"
            f"?symbol={symbol}&period=1h&limit=5",
            timeout=6
        )
        hist_oi = r.json()
        if len(hist_oi) >= 2:
            oi_actual = float(hist_oi[-1]["sumOpenInterestValue"])
            oi_anterior = float(hist_oi[-2]["sumOpenInterestValue"])
            oi_cambio = round((oi_actual - oi_anterior) / oi_anterior * 100, 2)
            oi_usd_b = round(oi_actual / 1e9, 2)
            if oi_cambio > 1:
                oi_señal = "SUBIENDO: mas posiciones abiertas, tendencia se refuerza"
            elif oi_cambio < -1:
                oi_señal = "BAJANDO: posiciones cerrando, posible agotamiento"
            else:
                oi_señal = "ESTABLE"
            resultado["open_interest"]["usd_b"] = oi_usd_b
            resultado["open_interest"]["cambio_pct"] = oi_cambio
            resultado["open_interest"]["señal"] = oi_señal
    except Exception as e:
        log.warning(f"OI hist: {e}")

    # 4. Long/Short Ratio (cuentas globales)
    try:
        r = requests.get(
            f"https://fapi.binance.com/futures/data/globalLongShortAccountRatio"
            f"?symbol={symbol}&period=1h&limit=2",
            timeout=6
        )
        data = r.json()
        if data:
            ratio = round(float(data[-1]["longShortRatio"]), 3)
            long_pct = round(float(data[-1]["longAccount"]) * 100, 1)
            short_pct = round(float(data[-1]["shortAccount"]) * 100, 1)
            if ratio > 1.5:
                ls_señal = "MUCHOS LONGS: riesgo de long squeeze"
            elif ratio < 0.7:
                ls_señal = "MUCHOS SHORTS: posible short squeeze alcista"
            else:
                ls_señal = "EQUILIBRADO"
            resultado["long_short"] = {
                "ratio": ratio,
                "long_pct": long_pct,
                "short_pct": short_pct,
                "señal": ls_señal,
            }
    except Exception as e:
        log.warning(f"Long/Short ratio: {e}")

    # 5. Liquidaciones ultimas 24h
    try:
        r = requests.get(
            f"https://fapi.binance.com/futures/data/takerlongshortRatio"
            f"?symbol={symbol}&period=1h&limit=24",
            timeout=6
        )
        # Liquidaciones forzadas via force orders
        r2 = requests.get(
            f"https://fapi.binance.com/fapi/v1/allForceOrders?symbol={symbol}&limit=100",
            timeout=6
        )
        orders = r2.json()
        liq_long = sum(float(o["origQty"]) * float(o["price"])
                      for o in orders if o.get("side") == "SELL")
        liq_short = sum(float(o["origQty"]) * float(o["price"])
                       for o in orders if o.get("side") == "BUY")
        resultado["liquidaciones"] = {
            "longs_m": round(liq_long / 1e6, 1),
            "shorts_m": round(liq_short / 1e6, 1),
        }
    except Exception as e:
        log.warning(f"Liquidaciones: {e}")

    return resultado


def get_fear_greed():
    """Obtiene el Fear & Greed Index de crypto."""
    try:
        r = requests.get("https://api.alternative.me/fng/?limit=2", timeout=8)
        data = r.json()["data"]
        hoy = data[0]
        ayer = data[1]
        valor = int(hoy["value"])
        clasificacion = hoy["value_classification"]
        valor_ayer = int(ayer["value"])
        return {
            "valor": valor,
            "clasificacion": clasificacion,
            "ayer": valor_ayer,
            "cambio": valor - valor_ayer,
        }
    except Exception as e:
        log.warning(f"Fear&Greed: {e}")
        return None


def get_btc_dominance():
    """Dominancia de BTC en el mercado crypto total."""
    try:
        r = requests.get("https://api.coingecko.com/api/v3/global", timeout=10)
        data = r.json()["data"]
        btc_dom = data["market_cap_percentage"]["btc"]
        eth_dom = data["market_cap_percentage"]["eth"]
        total_mcap = data["total_market_cap"]["usd"]
        btc_mcap = data["total_market_cap_by_currency"]["usd"] if "total_market_cap_by_currency" in data else None
        return {
            "btc_dom": round(btc_dom, 2),
            "eth_dom": round(eth_dom, 2),
            "total_mcap_b": round(total_mcap / 1e9, 0),
        }
    except Exception as e:
        log.warning(f"BTC dominance: {e}")
        return None


def get_usdt_dominance():
    """USDT dominance real: mcap USDT / mcap total crypto. Rango normal 5-9%."""
    try:
        r = requests.get("https://api.coingecko.com/api/v3/global", timeout=10)
        data = r.json()["data"]
        total_mcap = data["total_market_cap"]["usd"]
        # USDT mcap separado
        r2 = requests.get(
            "https://api.coingecko.com/api/v3/coins/tether"
            "?localization=false&tickers=false&market_data=true&community_data=false&developer_data=false",
            timeout=10
        )
        usdt_mcap = r2.json()["market_data"]["market_cap"]["usd"]
        usdt_dom = round(usdt_mcap / total_mcap * 100, 2)
        # Validacion: si sale fuera de rango 3-15% es error
        if usdt_dom < 3 or usdt_dom > 15:
            log.warning(f"USDT dom fuera de rango: {usdt_dom}%, descartado")
            return None
        return {"usdt_dom": usdt_dom, "usdt_mcap_b": round(usdt_mcap/1e9, 1)}
    except Exception as e:
        log.warning(f"USDT dominance: {e}")
        return None


def analisis_btc_profundo():
    """Analisis completo de BTC: precio, S/R, Fear&Greed, dominancias, tendencia."""
    resultado = {}

    # Precio y tecnicos BTC
    btc = fetch_quote("BTC-USD", "6mo")
    resultado["btc"] = btc

    # ETH y SOL para contexto altcoins
    eth = fetch_quote("ETH-USD", "1mo")
    sol = fetch_quote("SOL-USD", "1mo")
    resultado["eth"] = eth
    resultado["sol"] = sol

    # Fear & Greed
    fg = get_fear_greed()
    resultado["fear_greed"] = fg

    # Dominancias
    dom = get_btc_dominance()
    resultado["dominancia"] = dom

    # USDT dominance
    usdt = get_usdt_dominance()
    resultado["usdt"] = usdt

    # Datos Binance Futures: funding, OI, long/short, liquidaciones
    deriv = get_binance_derivatives("BTCUSDT")
    resultado["derivados"] = deriv

    # Soportes y resistencias clave BTC (niveles psicologicos + tecnicos)
    if btc:
        price = btc["price"]
        niveles_clave = []
        psicologicos = [100000, 90000, 80000, 70000, 60000, 50000, 40000, 30000]
        for n in psicologicos:
            dist = (n - price) / price * 100
            if abs(dist) < 30:
                tipo = "RESISTENCIA" if n > price else "SOPORTE"
                niveles_clave.append({"nivel": n, "tipo": tipo, "dist_pct": round(dist, 1)})
        resultado["niveles_psicologicos"] = niveles_clave

    return resultado


def detectar_ciclo(nombre_mercado, rsi, rsi_semanal, fg=None, vix=None,
                   sobre_ema20=False, sobre_ema50=False, tendencia_alcista=False,
                   d20=0, d5=0, funding=None, vol_rel=1.0,
                   dist_desde_maximo=None, recuperacion_desde_minimo=None,
                   d_anual=None):
    """
    Detecta la fase del ciclo de mercado con perspectiva anual.
    dist_desde_maximo: % caida desde el maximo de 52 semanas (negativo = bajada)
    recuperacion_desde_minimo: % subida desde el minimo de 52 semanas
    d_anual: rendimiento anual del activo
    """
    FASES = [
        (0,  "DEPRESION",     "El mercado no tiene fondo. Todo el mundo vendio.",        "#8B0000", "Mi dinero esta perdido. Soy un idiota."),
        (1,  "INCREDULIDAD",  "Primer rebote pero nadie lo cree.",                       "#B22222", "Este rally fracasara como los demas."),
        (2,  "ESPERANZA",     "Se empieza a ver luz. Rebotes sostenidos.",               "#CD853F", "Una recuperacion es posible."),
        (3,  "OPTIMISMO",     "El mercado sube. La gente empieza a entrar.",             "#DAA520", "Este repunte es real."),
        (4,  "CREENCIA",      "Tendencia alcista confirmada. Todos invierten.",          "#9ACD32", "Es hora de invertir a fondo."),
        (5,  "EMOCION",       "Ganancias rapidas. FOMO evidente.",                       "#32CD32", "Comprare mas con margen!"),
        (6,  "EUFORIA",       "MAXIMO. Punto de mayor riesgo.",                          "#00FF00", "Soy un genio! Todos vamos a ser ricos!"),
        (7,  "COMPLACENCIA",  "Primer bajada ignorada. Solo es una correccion.",         "#7CFC00", "Solo necesitamos calmarnos para el proximo repunte."),
        (8,  "ANSIEDAD",      "Las caidas se aceleran. Margin calls.",                   "#FFD700", "Por que recibo llamadas de margen?"),
        (9,  "NEGACION",      "El mercado cae pero la gente aguanta.",                   "#FFA500", "Mis inversiones estan en buenas empresas. Volvera."),
        (10, "PANICO",        "Venta masiva. Todo el mundo sale.",                       "#FF6347", "Todos estan vendiendo. Necesito salir!"),
        (11, "CAPITULACION",  "Rendicion total. Ventas al precio que sea.",              "#FF4500", "Estoy perdiendo todo. No puedo mas."),
        (12, "IRA",           "El suelo. Busqueda de culpables.",                        "#FF0000", "Quien vendio en corto? Por que el gobierno lo permite?"),
    ]

    score = 0

    # ── PERSPECTIVA ANUAL (mas peso que indicadores cortos) ──────────────────

    # Distancia desde el maximo anual
    # -5% = cerca del techo (complacencia/ansiedad)
    # -20% = caida importante (negacion/panico)
    # -40% = capitulacion/ira
    # -60%+ = depresion
    if dist_desde_maximo is not None:
        if dist_desde_maximo > -5:       score += 4   # cerca del maximo = euforia/complacencia
        elif dist_desde_maximo > -10:    score += 2   # leve correccion = ansiedad
        elif dist_desde_maximo > -20:    score -= 1   # correccion moderada = negacion
        elif dist_desde_maximo > -35:    score -= 3   # caida fuerte = panico
        elif dist_desde_maximo > -50:    score -= 5   # crash = capitulacion/ira
        else:                            score -= 7   # destruccion = depresion

    # Recuperacion desde el minimo anual
    # Si ha rebotado mucho desde minimos = saliendo del suelo (incredulidad/esperanza)
    if recuperacion_desde_minimo is not None:
        if recuperacion_desde_minimo > 50:   score += 3  # rebote fuerte = esperanza/optimismo
        elif recuperacion_desde_minimo > 25: score += 2  # rebote moderado = incredulidad
        elif recuperacion_desde_minimo > 10: score += 1  # rebote inicial = suelo probable

    # Rendimiento anual
    if d_anual is not None:
        if d_anual > 50:    score += 3   # año excelente = euforia
        elif d_anual > 20:  score += 2   # año bueno = emocion/creencia
        elif d_anual > 5:   score += 1   # año positivo = optimismo
        elif d_anual < -30: score -= 3   # año catastrofico = capitulacion/ira
        elif d_anual < -15: score -= 2   # año malo = panico
        elif d_anual < -5:  score -= 1   # año negativo = negacion

    # ── INDICADORES TECNICOS (confirmacion) ──────────────────────────────────

    # RSI diario
    if rsi > 75:   score += 2
    elif rsi > 65: score += 1
    elif rsi < 30: score -= 2
    elif rsi < 40: score -= 1

    # RSI semanal
    if rsi_semanal:
        if rsi_semanal > 70:   score += 2
        elif rsi_semanal > 60: score += 1
        elif rsi_semanal < 35: score -= 2
        elif rsi_semanal < 45: score -= 1

    # EMAs
    if tendencia_alcista and sobre_ema20:  score += 2
    elif sobre_ema20:                      score += 1
    elif not sobre_ema50:                  score -= 1

    # Momentum mensual
    if d20 > 15:    score += 2
    elif d20 > 8:   score += 1
    elif d20 < -10: score -= 2
    elif d20 < -5:  score -= 1

    # Fear & Greed (crypto)
    if fg:
        if fg > 80:   score += 3
        elif fg > 65: score += 2
        elif fg > 50: score += 1
        elif fg < 20: score -= 2
        elif fg < 35: score -= 1

    # VIX (bolsa)
    if vix:
        if vix > 35:   score -= 3
        elif vix > 25: score -= 2
        elif vix > 20: score -= 1
        elif vix < 13: score += 2
        elif vix < 16: score += 1

    # Funding rate (crypto)
    if funding is not None:
        if funding > 0.05:    score += 3
        elif funding > 0.02:  score += 1
        elif funding < -0.01: score -= 2

    # Mapear score a fase
    score = max(-12, min(15, score))
    if score >= 12:    fase = 6   # Euforia
    elif score >= 9:   fase = 5   # Emocion
    elif score >= 7:   fase = 4   # Creencia
    elif score >= 5:   fase = 7   # Complacencia
    elif score >= 3:   fase = 3   # Optimismo
    elif score >= 1:   fase = 2   # Esperanza
    elif score == 0:   fase = 1   # Incredulidad
    elif score >= -2:  fase = 8   # Ansiedad
    elif score >= -4:  fase = 9   # Negacion
    elif score >= -6:  fase = 10  # Panico
    elif score >= -8:  fase = 11  # Capitulacion
    elif score >= -10: fase = 12  # Ira
    else:              fase = 0   # Depresion

    return {
        "fase_num": fase,
        "nombre": FASES[fase][1],
        "descripcion": FASES[fase][2],
        "color": FASES[fase][3],
        "emocion": FASES[fase][4],
        "score": score,
    }


def generate_cycle_chart(mercados):
    """
    Genera imagen del ciclo de mercado con los puntos actuales marcados.
    mercados: lista de dicts con {nombre, fase_num, color_punto}
    """
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    import numpy as np

    fig, ax = plt.subplots(figsize=(14, 8))
    fig.patch.set_facecolor('#0d1117')
    ax.set_facecolor('#0d1117')

    # Generar la curva del ciclo de mercado
    t = np.linspace(0, 4 * np.pi, 1000)
    # Curva personalizada: subida suave, bajada brusca, recuperacion lenta
    y = (np.sin(t - np.pi/2) +
         0.3 * np.sin(2*t) +
         0.15 * np.sin(3*t) +
         0.05 * np.sin(5*t))
    # Normalizar
    y = (y - y.min()) / (y.max() - y.min())

    # Colorear la curva por segmentos
    colores_curva = [
        '#8B0000','#B22222','#CD853F','#DAA520','#9ACD32',
        '#32CD32','#00FF00','#7CFC00','#FFD700','#FFA500',
        '#FF6347','#FF4500','#FF0000'
    ]
    segmentos = len(colores_curva)
    paso = len(t) // segmentos
    for i in range(segmentos):
        inicio = i * paso
        fin = min((i+1) * paso + 1, len(t))
        ax.plot(t[inicio:fin], y[inicio:fin],
                color=colores_curva[i], linewidth=4, alpha=0.9)

    # Posiciones aproximadas de cada fase en la curva
    FASE_POSICION = {
        0: 0.08,   # Depresion
        1: 0.12,   # Incredulidad
        2: 0.18,   # Esperanza
        3: 0.25,   # Optimismo
        4: 0.32,   # Creencia
        5: 0.40,   # Emocion
        6: 0.50,   # Euforia (cuspide)
        7: 0.58,   # Complacencia
        8: 0.65,   # Ansiedad
        9: 0.72,   # Negacion
        10: 0.78,  # Panico
        11: 0.83,  # Capitulacion
        12: 0.90,  # Ira
    }

    FASE_NOMBRES = [
        "DEPRESION","INCREDULIDAD","ESPERANZA","OPTIMISMO","CREENCIA",
        "EMOCION","EUFORIA","COMPLACENCIA","ANSIEDAD","NEGACION",
        "PANICO","CAPITULACION","IRA"
    ]

    # Etiquetas de fases en la curva
    for fase_n, pos_pct in FASE_POSICION.items():
        idx = int(pos_pct * len(t))
        idx = min(idx, len(t)-1)
        offset_y = 0.06 if fase_n in [6,7] else (-0.08 if fase_n in [11,12,0] else 0.05)
        ax.annotate(FASE_NOMBRES[fase_n],
                   xy=(t[idx], y[idx]),
                   xytext=(t[idx], y[idx] + offset_y),
                   fontsize=7, color='#888888',
                   ha='center', va='center',
                   fontweight='bold')

    # Marcar los mercados actuales
    colores_mercado = ['#00FFFF', '#FFD700', '#FF69B4', '#7FFF00']
    for i, m in enumerate(mercados):
        fase_n = m["fase_num"]
        pos_pct = FASE_POSICION[fase_n]
        idx = int(pos_pct * len(t))
        idx = min(idx, len(t)-1)
        color_m = colores_mercado[i % len(colores_mercado)]

        # Punto grande
        ax.plot(t[idx], y[idx], 'o',
                color=color_m, markersize=18,
                markeredgecolor='white', markeredgewidth=2,
                zorder=10)

        # Etiqueta del mercado
        offset = 0.12 + i * 0.05
        ax.annotate(f"{m['nombre']}\n{m['fase']}",
                   xy=(t[idx], y[idx]),
                   xytext=(t[idx], y[idx] + offset),
                   fontsize=9, color=color_m,
                   ha='center', va='bottom',
                   fontweight='bold',
                   arrowprops=dict(arrowstyle='->', color=color_m, lw=1.5),
                   bbox=dict(boxstyle='round,pad=0.3', facecolor='#1a1a2e',
                            edgecolor=color_m, alpha=0.9))

    # Etiquetas de zona
    ax.text(t[int(0.35*len(t))], 0.15, 'EXPANSION', fontsize=11,
            color='#32CD32', alpha=0.5, ha='center', fontweight='bold')
    ax.text(t[int(0.75*len(t))], 0.15, 'CONTRACCION', fontsize=11,
            color='#FF6347', alpha=0.5, ha='center', fontweight='bold')

    ax.set_title('CICLO DE MERCADO - DONDE ESTAMOS AHORA',
                fontsize=14, color='white', fontweight='bold', pad=15)
    ax.set_xlabel('TIEMPO', color='#888888', fontsize=10)
    ax.set_ylabel('PRECIO', color='#888888', fontsize=10)
    ax.tick_params(colors='#888888')
    ax.spines['bottom'].set_color('#333333')
    ax.spines['left'].set_color('#333333')
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.set_xticks([])
    ax.set_yticks([])

    # Leyenda
    legend_elements = [mpatches.Patch(facecolor=colores_mercado[i],
                       label=m['nombre']) for i, m in enumerate(mercados)]
    ax.legend(handles=legend_elements, loc='lower right',
             facecolor='#1a1a2e', edgecolor='#333333',
             labelcolor='white', fontsize=9)

    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=120,
                facecolor='#0d1117', bbox_inches='tight')
    plt.close()
    buf.seek(0)
    return buf


def generate_chart(ticker, entry, tp1, tp2, stop):
    try:
        hist = yf.Ticker(ticker).history(period="1mo", interval="1d")
        if hist.empty or len(hist) < 5:
            return None
        hist.index = pd.to_datetime(hist.index)
        if hist.index.tz is not None:
            hist.index = hist.index.tz_localize(None)
        ap = [
            mpf.make_addplot([entry]*len(hist), color='cyan', linestyle='dashed', width=1.5),
            mpf.make_addplot([tp1]*len(hist), color='lime', linestyle='dashed', width=1.5),
            mpf.make_addplot([tp2]*len(hist), color='green', linestyle='dashed', width=1.5),
            mpf.make_addplot([stop]*len(hist), color='red', linestyle='dashed', width=1.5),
        ]
        s = mpf.make_mpf_style(base_mpf_style='nightclouds', gridstyle='')
        buf = io.BytesIO()
        titulo = f'\n{nombre(ticker)} ({ticker})  Entrada:{entry}  TP1:{tp1}  TP2:{tp2}  Stop:{stop}'
        mpf.plot(hist, type='candle', style=s, figsize=(10, 5),
                title=titulo, addplot=ap,
                savefig=dict(fname=buf, dpi=100, bbox_inches='tight'))
        buf.seek(0)
        return buf
    except Exception as e:
        log.warning(f"Chart error {ticker}: {e}")
        return None


def mercado_en_tendencia_alcista():
    """Comprueba si S&P500 y DAX estan en tendencia alcista. Filtro global."""
    alcistas = 0
    for ticker in ["^GSPC", "^GDAXI"]:
        d = fetch_quote(ticker, "3mo")
        if d and d["tendencia_alcista"] and d["d5"] > -3.0:
            alcistas += 1
    return alcistas >= 1  # Al menos uno alcista para dar señales


def fetch_weekly_rsi(ticker):
    """RSI semanal para confirmacion 2 timeframes."""
    try:
        hist = yf.Ticker(ticker).history(period="1y", interval="1wk")
        if hist.empty or len(hist) < 14:
            return None
        return round(calc_rsi(hist["Close"]).iloc[-1], 1)
    except:
        return None


def get_top_signals(stocks, n=4):
    mercado_ok = mercado_en_tendencia_alcista()
    candidatos = []
    for t in stocks:
        d = fetch_quote(t, "3mo")
        if not d:
            continue

        # Filtros obligatorios
        if d["rsi"] > 65:
            continue
        if d["vol_rel"] < 0.8:
            continue

        # Filtro mercado: si mercado bajista, solo señales con RSI muy bajo
        if not mercado_ok and d["rsi"] > 40:
            continue

        score = 0
        motivos = []

        # 1. RSI
        if d["rsi"] < 30:
            score += 5
            motivos.append(f"RSI {d['rsi']} sobreventa fuerte")
        elif d["rsi"] < 40:
            score += 4
            motivos.append(f"RSI {d['rsi']} zona ideal entrada")
        elif d["rsi"] < 55:
            score += 3
            motivos.append(f"RSI {d['rsi']} saludable")
        elif d["rsi"] < 65:
            score += 1
            motivos.append(f"RSI {d['rsi']} neutral")

        # 2. Confirmacion RSI semanal
        rsi_w = fetch_weekly_rsi(t)
        if rsi_w is not None:
            if rsi_w < 50:
                score += 2
                motivos.append(f"RSI semanal {rsi_w} confirma (2 timeframes)")
            elif rsi_w < 60:
                score += 1

        # 3. MACD cruce alcista
        if d["macd_cross_up"]:
            score += 4
            motivos.append("MACD cruce alcista")

        # 4. Volumen
        if d["vol_rel"] >= 2.0:
            score += 4
            motivos.append(f"Volumen {d['vol_rel']}x fuerte")
        elif d["vol_rel"] >= 1.5:
            score += 3
            motivos.append(f"Volumen {d['vol_rel']}x elevado")
        elif d["vol_rel"] >= 1.0:
            score += 1
            motivos.append(f"Volumen {d['vol_rel']}x normal")

        # 5. Tendencia EMA
        if d["tendencia_alcista"] and d["ema20_subiendo"]:
            score += 4
            motivos.append("EMA20 > EMA50 y subiendo")
        elif d["tendencia_alcista"]:
            score += 2
            motivos.append("EMA20 > EMA50")
        elif d["sobre_ema20"]:
            score += 1
            motivos.append("Precio sobre EMA20")

        # 6. Breakout
        if d["cerca_breakout"]:
            score += 3
            motivos.append(f"Breakout inminente a {d['max20']} ({d['dist_breakout']}%)")
        elif d["dist_breakout"] <= 3.0:
            score += 1
            motivos.append(f"Cerca maximo 20d ({d['dist_breakout']}%)")

        # 7. Fuerza relativa
        if d["fuerza_relativa"]:
            score += 2
            motivos.append(f"Fuerza relativa: +{d['d20']}% mensual")

        # 8. Momentum
        if d["d1"] >= 1.5:
            score += 1
        if d["d5"] >= 3.0:
            score += 1

        # 9. Soporte cercano
        for nivel in [d["s1"], d["s2"]]:
            if nivel > 0 and abs(d["price"] - nivel) / nivel * 100 <= 1.5:
                score += 2
                motivos.append(f"Cerca soporte {nivel}")
                break

        # Umbral minimo
        if score < 7:
            continue

        entry = d["price"]
        atr = d.get("atr", entry * 0.02)

        # Stop loss dinamico basado en ATR (1.5x ATR)
        stop_atr  = round(entry - atr * 1.5, 2)
        stop_sr   = round(d["s1"] * 0.985, 2)
        # Usar el stop mas cercano al precio (mas conservador)
        stop = max(stop_atr, stop_sr) if stop_sr > 0 else stop_atr
        if stop <= 0 or entry - stop > entry * 0.08:
            stop = round(entry * 0.97, 2)

        risk = entry - stop
        if risk <= 0:
            continue

        tp1 = round(entry + risk * 1.5, 2)
        tp2 = round(entry + risk * 3.0, 2)
        rr  = round((tp1 - entry) / risk, 2)

        if not mercado_ok:
            motivos.insert(0, "AVISO: mercado general bajista, operar con cautela")

        candidatos.append({
            **d, "score": score, "motivos": motivos,
            "direction": "COMPRAR",
            "entry": entry, "stop": stop, "tp1": tp1, "tp2": tp2, "rr": rr,
            "atr": round(atr, 2), "rsi_semanal": rsi_w,
        })

    candidatos.sort(key=lambda x: x["score"], reverse=True)
    return candidatos[:n]


def scan_anomalias(stocks):
    anomalias = []
    for t in stocks:
        try:
            hist = yf.Ticker(t).history(period="1mo")
            if hist.empty or len(hist) < 10:
                continue
            vol = hist["Volume"]
            c = hist["Close"]
            avg_vol = vol.tail(20).mean()
            vol_rel = vol.iloc[-1] / avg_vol if avg_vol > 0 else 1.0
            d1 = (c.iloc[-1] - c.iloc[-2]) / c.iloc[-2] * 100
            if vol_rel >= 2.5:
                anomalias.append({
                    "ticker": t, "nombre": nombre(t),
                    "vol_rel": round(vol_rel, 1),
                    "d1": round(d1, 2), "price": round(c.iloc[-1], 2),
                })
        except:
            pass
    anomalias.sort(key=lambda x: x["vol_rel"], reverse=True)
    return anomalias[:8]


def analisis_sectores():
    resultados = []
    for sector, tickers in SECTORES_SP500.items():
        rsis, d5s, vol_rels, sobre_ema = [], [], [], []
        for t in tickers:
            d = fetch_quote(t, "3mo")
            if d:
                rsis.append(d["rsi"])
                d5s.append(d["d5"])
                vol_rels.append(d["vol_rel"])
                sobre_ema.append(1 if d["sobre_ema20"] else 0)
        if not rsis:
            continue
        rsi_med = round(np.mean(rsis), 1)
        d5_med = round(np.mean(d5s), 2)
        vol_med = round(np.mean(vol_rels), 2)
        pct_sobre_ema = round(np.mean(sobre_ema) * 100, 0)
        if rsi_med > 55 and d5_med > 1.0 and pct_sobre_ema >= 60:
            estado = "BULL"
        elif rsi_med < 40 or d5_med < -2.0:
            estado = "BEAR"
        else:
            estado = "NEUTRO"
        resultados.append({
            "sector": sector, "estado": estado,
            "rsi": rsi_med, "d5": d5_med,
            "vol": vol_med, "sobre_ema": pct_sobre_ema,
        })
    resultados.sort(key=lambda x: (x["estado"] == "BULL", x["d5"]), reverse=True)
    return resultados


def bull_detector():
    senales = []
    sectores = analisis_sectores()
    for b in [s for s in sectores if s["estado"] == "BULL"]:
        senales.append({
            "tipo": "SECTOR", "nombre": b["sector"], "fuerza": b["d5"],
            "rsi": b["rsi"], "detalle": f"RSI {b['rsi']} | semana {b['d5']:+.1f}% | {b['sobre_ema']}% sobre EMA20",
        })
    for t in ETFS_TEMATICOS + ETFS_INDICES[:4]:
        d = fetch_quote(t, "3mo")
        if not d:
            continue
        if d["d20"] > 5.0 and d["rsi"] > 50 and d["rsi"] < 72 and d["vol_rel"] >= 1.0:
            senales.append({
                "tipo": "ETF", "nombre": d["nombre"], "ticker": t,
                "fuerza": d["d20"], "rsi": d["rsi"],
                "detalle": f"RSI {d['rsi']} | mes {d['d20']:+.1f}% | vol {d['vol_rel']}x",
            })
    for t in CRYPTO:
        d = fetch_quote(t, "3mo")
        if not d:
            continue
        if d["d20"] > 10.0 and d["rsi"] > 50 and d["rsi"] < 75:
            senales.append({
                "tipo": "CRYPTO", "nombre": d["nombre"], "ticker": t,
                "fuerza": d["d20"], "rsi": d["rsi"],
                "detalle": f"RSI {d['rsi']} | mes {d['d20']:+.1f}% | vol {d['vol_rel']}x",
            })
    senales.sort(key=lambda x: x["fuerza"], reverse=True)
    return senales[:8]


def get_noticias_impacto():
    todas = []
    for url in RSS_IMPACTO:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:4]:
                title = entry.get("title", "").strip()
                if title and title not in [t["title"] for t in todas]:
                    todas.append({"title": title})
        except:
            pass
    return todas[:15]


def run_backtest(stocks):
    results = []
    for t in stocks[:12]:
        try:
            hist = yf.Ticker(t).history(period="3mo")
            if hist.empty or len(hist) < 30:
                continue
            c = hist["Close"]
            rsi = calc_rsi(c)
            macd_l, macd_s = calc_macd(c)
            wins = losses = total = 0
            for i in range(20, len(c)-6):
                if (rsi.iloc[i] < 45 and rsi.iloc[i] < 65 and
                        macd_l.iloc[i] > macd_s.iloc[i] and
                        macd_l.iloc[i-1] <= macd_s.iloc[i-1]):
                    entry = c.iloc[i]
                    tp = entry * 1.03
                    sl = entry * 0.98
                    total += 1
                    for j in range(i+1, min(i+8, len(c))):
                        if c.iloc[j] >= tp:
                            wins += 1; break
                        elif c.iloc[j] <= sl:
                            losses += 1; break
            if total > 0:
                results.append({
                    "ticker": t, "nombre": nombre(t),
                    "total": total, "wins": wins, "losses": losses,
                    "winrate": round(wins/total*100, 1),
                })
        except Exception as e:
            log.warning(f"Backtest {t}: {e}")
    results.sort(key=lambda x: x["winrate"], reverse=True)
    return results


def allowed(message):
    if ALLOWED_USER_ID == 0:
        return True
    return message.from_user.id == ALLOWED_USER_ID


def ask_ai(prompt, max_chars=3000):
    for attempt in range(3):
        try:
            resp = ai_client.chat(
                model="mistral-small-latest",
                messages=[
                    ChatMessage(role="system", content=SYSTEM),
                    ChatMessage(role="user", content=prompt)
                ],
            )
            texto = resp.choices[0].message.content
            return texto[:max_chars] if len(texto) > max_chars else texto
        except Exception as e:
            if attempt < 2:
                time.sleep(3)
            else:
                log.error(f"Mistral: {e}")
                return "Error IA. Intenta en unos segundos."


def safe_send(chat_id, text, message_id=None):
    if len(text) > 4000:
        text = text[:4000] + "\n...truncado"
    try:
        if message_id:
            bot.edit_message_text(text, chat_id, message_id)
        else:
            bot.send_message(chat_id, text)
    except Exception as e:
        log.error(f"Send error: {e}")


def send_signal(chat_id, s):
    pct_tp1 = (s['tp1']/s['entry']-1)*100
    pct_tp2 = (s['tp2']/s['entry']-1)*100
    pct_sl  = (s['stop']/s['entry']-1)*100
    rsi_txt = (f"{s['rsi']} (zona ideal)" if 35 <= s['rsi'] <= 50
               else f"{s['rsi']} (sobreventa)" if s['rsi'] < 35
               else f"{s['rsi']}")
    tendencia  = "ALCISTA" if s.get("tendencia_alcista") else "LATERAL"
    ema_txt    = f"EMA20:{s.get('ema20','-')} EMA50:{s.get('ema50','-')}"
    breakout   = (f"SI a {s.get('max20',0)} ({s.get('dist_breakout',99)}%)"
                  if s.get("cerca_breakout") else "NO")
    fuerza_txt = f"SI (+{s['d20']}% mes)" if s.get("fuerza_relativa") else "NO"
    rsi_w_txt  = f"{s['rsi_semanal']}" if s.get("rsi_semanal") else "N/D"
    motivos    = "\n  ".join(s.get("motivos", []))
    text = (f"SENAL: {s['nombre']} ({s['ticker']})\n"
            f"Accion:   {s['direction']}\n"
            f"Entrada:  {s['entry']}\n"
            f"TP1:      {s['tp1']} ({pct_tp1:+.1f}%)\n"
            f"TP2:      {s['tp2']} ({pct_tp2:+.1f}%)\n"
            f"Stop:     {s['stop']} ({pct_sl:+.1f}%) [ATR:{s.get('atr','-')}]\n"
            f"R/R:      {s['rr']}x\n"
            f"Score:    {s['score']}/24\n\n"
            f"CRITERIOS:\n"
            f"RSI diario:    {rsi_txt}\n"
            f"RSI semanal:   {rsi_w_txt}\n"
            f"Volumen:       {s['vol_rel']}x media\n"
            f"Tendencia EMA: {tendencia} ({ema_txt})\n"
            f"Breakout:      {breakout}\n"
            f"Fuerza relat.: {fuerza_txt}\n"
            f"MACD cruce:    {'SI' if s['macd_cross_up'] else 'NO'}\n\n"
            f"Por que entra:\n  {motivos}")
    chart = generate_chart(s['ticker'], s['entry'], s['tp1'], s['tp2'], s['stop'])
    if chart:
        try:
            caption = (f"{s['nombre']} | {s['direction']}\n"
                       f"Entrada:{s['entry']} TP1:{s['tp1']} TP2:{s['tp2']} Stop:{s['stop']}\n"
                       f"R/R:{s['rr']}x | RSI:{s['rsi']} | Score:{s['score']}/24")
            bot.send_photo(chat_id, chart, caption=caption)
            time.sleep(0.5)
            safe_send(chat_id, text)
            return
        except Exception as e:
            log.warning(f"Photo error: {e}")
    safe_send(chat_id, text)


def get_news(max_items=10):
    titulares = []
    for url in RSS_FEEDS:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:3]:
                title = entry.get("title", "").strip()
                if title and title not in titulares:
                    titulares.append(title)
            if len(titulares) >= max_items:
                break
        except:
            pass
    return titulares[:max_items]


def get_economic_calendar():
    eventos = []
    try:
        r = requests.get("https://nfs.faireconomy.media/ff_calendar_thisweek.json", timeout=8)
        eventos = [e for e in r.json() if e.get("impact") == "High"][:12]
    except:
        pass
    for t in ["NVDA","AAPL","MSFT","AMZN","META","TSLA","JPM"]:
        try:
            cal = yf.Ticker(t).calendar
            if cal is not None and "Earnings Date" in cal:
                fecha = str(cal["Earnings Date"][0])[:10]
                if datetime.now().strftime("%Y-%m") in fecha:
                    eventos.append({"date": fecha, "country": "US",
                                    "title": f"Earnings {nombre(t)}", "impact": "High"})
        except:
            pass
    return eventos


def get_ipo_news():
    try:
        feed = feedparser.parse("https://news.google.com/rss/search?q=IPO+2026+NYSE+NASDAQ&hl=en&gl=US&ceid=US:en")
        return [e.get("title", "").strip() for e in feed.entries[:8] if e.get("title")]
    except:
        return []


def scan_sr_alerts(stocks):
    alerts = []
    for t in stocks:
        d = fetch_quote(t, "3mo")
        if not d:
            continue
        for nivel, nom in [(d["s1"],"S1"),(d["s2"],"S2"),(d["r1"],"R1"),(d["r2"],"R2")]:
            dist = abs(d["price"] - nivel) / nivel * 100
            if dist <= 1.5:
                tipo = "SOPORTE" if "S" in nom else "RESIST"
                alerts.append(f"{tipo} {d['nombre']} ({t}) cerca {nom} {nivel} precio {d['price']} ({dist:.1f}%)")
    return alerts


def scan_explosions(stocks):
    out = []
    for t in stocks:
        d = fetch_quote(t, "3mo")
        if not d:
            continue
        dist_hi = (d["hi52"] - d["price"]) / d["hi52"] * 100
        if d["vol_rel"] >= 1.8 and d["d5"] >= 3.0 and dist_hi <= 8.0:
            out.append({"ticker": t, "nombre": d["nombre"], "d5": d["d5"],
                        "vol_rel": d["vol_rel"], "price": d["price"], "r1": d["r1"]})
    out.sort(key=lambda x: x["vol_rel"]*x["d5"], reverse=True)
    return out[:5]


def arrow(v):
    return f"{'(+)' if v >= 0 else '(-)'} {v:+.2f}%"


def semaforo(estado):
    if estado == "BULL":  return "[BULL]"
    if estado == "BEAR":  return "[BEAR]"
    return "[=]"


def main_kb():
    kb = InlineKeyboardMarkup()
    kb.row(InlineKeyboardButton("Noticias", callback_data="noticias"),
           InlineKeyboardButton("Mercados", callback_data="mercados"))
    kb.row(InlineKeyboardButton("Senales EU", callback_data="senales_eu"),
           InlineKeyboardButton("Senales EEUU", callback_data="senales_us"))
    kb.row(InlineKeyboardButton("ETFs", callback_data="etfs"),
           InlineKeyboardButton("Sectores", callback_data="sectores"))
    kb.row(InlineKeyboardButton("Bull Detector", callback_data="bull_detector"),
           InlineKeyboardButton("Anomalias", callback_data="anomalias"))
    kb.row(InlineKeyboardButton("BTC Analisis", callback_data="btc"),
           InlineKeyboardButton("Crypto", callback_data="crypto"))
    kb.row(InlineKeyboardButton("Noticias Impacto", callback_data="noticias_impacto"),
           InlineKeyboardButton("Macro", callback_data="macro"))
    kb.row(InlineKeyboardButton("Oportunidades", callback_data="oportunidades"),
           InlineKeyboardButton("Explosiones", callback_data="explosiones"))
    kb.row(InlineKeyboardButton("Calendario", callback_data="calendario"),
           InlineKeyboardButton("S/R Scanner", callback_data="sr_scan"))
    kb.row(InlineKeyboardButton("Metales", callback_data="metales"),
           InlineKeyboardButton("IPOs", callback_data="ipos"))
    kb.row(InlineKeyboardButton("Backtest", callback_data="backtest"),
           InlineKeyboardButton("Alertas", callback_data="alertas"))
    kb.row(InlineKeyboardButton("Ciclo Mercado", callback_data="ciclo"),
           InlineKeyboardButton("Resumen Semana", callback_data="resumen_semana"))
    return kb


# ── HANDLERS ──────────────────────────────────────────────────────────────────

@bot.message_handler(commands=["start"])
def cmd_start(msg):
    if not allowed(msg): return
    bot.send_message(msg.chat.id,
        "Financial Bot - Version Completa\n\n"
        "CRYPTO:\n"
        "/btc - Analisis profundo Bitcoin (Fear&Greed, dominancias, S/R)\n"
        "/crypto - BTC ETH SOL BNB\n\n"
        "MERCADO:\n"
        "/senales_eu /senales_us - Senales con grafico\n"
        "/etfs - ETFs indices, sectoriales, tematicos\n"
        "/sectores - Semaforo 11 sectores SP500\n"
        "/bull_detector - Bull runs nacientes\n"
        "/anomalias - Volumen anomalo posible rumor\n"
        "/noticias_impacto - M&A, earnings, FDA\n\n"
        "HERRAMIENTAS:\n"
        "/alerta TICKER PRECIO\n"
        "/alertas /borra_alerta 1\n"
        "/riesgo CAPITAL % TICKER ENTRADA STOP\n"
        "/analisis TICKER\n"
        "/macro /backtest /calendario\n"
        "/metales /ipos /oportunidades\n"
        "Pregunta libre - IA con precio actual",
        reply_markup=main_kb()
    )


@bot.message_handler(commands=["btc"])
def cmd_btc(msg):
    if not allowed(msg): return
    m = bot.send_message(msg.chat.id, "Analizando Bitcoin... precio real + Fear&Greed + dominancias + derivados Binance")
    datos = analisis_btc_profundo()
    btc   = datos.get("btc")
    fg    = datos.get("fear_greed")
    dom   = datos.get("dominancia")
    usdt  = datos.get("usdt")
    eth   = datos.get("eth")
    sol   = datos.get("sol")
    deriv = datos.get("derivados", {})
    niveles = datos.get("niveles_psicologicos", [])

    if not btc:
        safe_send(msg.chat.id, "Error obteniendo datos de Bitcoin.", message_id=m.message_id)
        return

    lines = [f"BITCOIN - {datetime.now().strftime('%d/%m %H:%M')}",
             f"Precio:   {btc['price']:,.0f} USD",
             f"Hoy:      {arrow(btc['d1'])}",
             f"Semana:   {arrow(btc['d5'])}",
             f"Mes:      {arrow(btc['d20'])}",
             f"RSI:      {btc['rsi']}",
             f"MACD:     {'Cruce alcista' if btc['macd_cross_up'] else 'Sin cruce'}",
             f"EMA20:    {'SOBRE' if btc['sobre_ema20'] else 'BAJO'} ({btc['ema20']:,.0f})",
             f"EMA50:    {btc['ema50']:,.0f}",
             "",
             "SOPORTES Y RESISTENCIAS:",
             f"R2: {btc['r2']:,.0f} | R1: {btc['r1']:,.0f}",
             f"Pivot: {btc['pivot']:,.0f}",
             f"S1: {btc['s1']:,.0f} | S2: {btc['s2']:,.0f}",
             f"Rango 52s: {btc['lo52']:,.0f} - {btc['hi52']:,.0f}",
             ""]

    if niveles:
        lines.append("NIVELES PSICOLOGICOS PROXIMOS:")
        for n in niveles[:5]:
            lines.append(f"{n['tipo']}: {n['nivel']:,} ({n['dist_pct']:+.1f}%)")
        lines.append("")

    # Fear & Greed
    if fg:
        tendencia_fg = "subiendo" if fg["cambio"] > 0 else "bajando"
        lines.append(f"FEAR & GREED: {fg['valor']}/100 - {fg['clasificacion']}")
        lines.append(f"Ayer: {fg['ayer']} (esta {tendencia_fg})")
        lines.append("")

    # Dominancias
    if dom:
        lines.append(f"DOMINANCIA BTC: {dom['btc_dom']}%")
        lines.append(f"DOMINANCIA ETH: {dom['eth_dom']}%")
        lines.append(f"Market Cap Total: {dom['total_mcap_b']:,.0f}B USD")
        lines.append("")

    if usdt:
        if usdt["usdt_dom"] > 8.0:
            usdt_señal = "ZONA ALTA: dinero en stablecoins, mercado temeroso"
        elif usdt["usdt_dom"] < 4.5:
            usdt_señal = "ZONA BAJA: dinero saliendo de USDT hacia crypto"
        else:
            usdt_señal = "ZONA MEDIA: neutral"
        lines.append(f"USDT DOMINANCE: {usdt['usdt_dom']}% -> {usdt_señal}")
        lines.append("")

    # Derivados Binance
    lines.append("DERIVADOS BINANCE FUTURES:")
    if "funding" in deriv:
        f = deriv["funding"]
        lines.append(f"Funding Rate: {f['valor']:+.4f}% -> {f['señal']}")
    if "open_interest" in deriv:
        oi = deriv["open_interest"]
        oi_txt = f"OI: {oi.get('usd_b', '?')}B USD"
        if "cambio_pct" in oi:
            oi_txt += f" ({oi['cambio_pct']:+.2f}% ultima hora) -> {oi.get('señal','')}"
        lines.append(oi_txt)
    if "long_short" in deriv:
        ls = deriv["long_short"]
        lines.append(f"Long/Short: {ls['ratio']} ({ls['long_pct']}% longs / {ls['short_pct']}% shorts) -> {ls['señal']}")
    if "liquidaciones" in deriv:
        liq = deriv["liquidaciones"]
        lines.append(f"Liquidaciones recientes: Longs {liq['longs_m']}M | Shorts {liq['shorts_m']}M USD")
    lines.append("")

    # Altcoins
    if eth:
        lines.append(f"Ethereum: {eth['price']:,.0f} | RSI {eth['rsi']} | semana {eth['d5']:+.2f}%")
    if sol:
        lines.append(f"Solana:   {sol['price']:,.0f} | RSI {sol['rsi']} | semana {sol['d5']:+.2f}%")

    snap = "\n".join(lines)

    # Construir prompt IA con todos los datos
    fg_txt    = f"Fear&Greed: {fg['valor']}/100 ({fg['clasificacion']})" if fg else "Fear&Greed: no disponible"
    dom_txt   = f"BTC dominance: {dom['btc_dom']}%, ETH: {dom['eth_dom']}%" if dom else "Dominancia: no disponible"
    usdt_txt  = f"USDT dominance: {usdt['usdt_dom']}% (rango normal 5-9%)" if usdt else "USDT dominance: dato no disponible, NO lo uses en el analisis"
    niveles_txt = " | ".join([f"{n['tipo']} {n['nivel']:,} ({n['dist_pct']:+.1f}%)" for n in niveles[:5]])

    funding_txt = ""
    oi_txt_ai   = ""
    ls_txt_ai   = ""
    liq_txt_ai  = ""
    if "funding" in deriv:
        funding_txt = f"Funding rate: {deriv['funding']['valor']:+.4f}% ({deriv['funding']['señal']})"
    if "open_interest" in deriv:
        oi = deriv["open_interest"]
        oi_txt_ai = f"Open Interest: {oi.get('usd_b','?')}B USD, cambio ultima hora: {oi.get('cambio_pct','?')}% ({oi.get('señal','')})"
    if "long_short" in deriv:
        ls = deriv["long_short"]
        ls_txt_ai = f"Long/Short ratio: {ls['ratio']} ({ls['long_pct']}% longs) -> {ls['señal']}"
    if "liquidaciones" in deriv:
        liq = deriv["liquidaciones"]
        liq_txt_ai = f"Liquidaciones recientes: longs liquidados {liq['longs_m']}M USD, shorts {liq['shorts_m']}M USD"

    prompt = (f"Analisis profundo Bitcoin:\n"
              f"Precio: {btc['price']:,.0f} | RSI: {btc['rsi']} | MACD: {btc['macd_cross_up']}\n"
              f"Hoy: {btc['d1']}% | Semana: {btc['d5']}% | Mes: {btc['d20']}%\n"
              f"EMA20: {'sobre' if btc['sobre_ema20'] else 'bajo'} ({btc['ema20']:,.0f}) | EMA50: {btc['ema50']:,.0f}\n"
              f"S/R: R1={btc['r1']:,.0f} S1={btc['s1']:,.0f} Pivot={btc['pivot']:,.0f}\n"
              f"Niveles psicologicos: {niveles_txt}\n"
              f"{fg_txt}\n{dom_txt}\n{usdt_txt}\n"
              f"{funding_txt}\n{oi_txt_ai}\n{ls_txt_ai}\n{liq_txt_ai}\n\n"
              "1. Sesgo actual alcista o bajista y por que (usa todos los datos)\n"
              "2. Que dicen el funding rate y open interest sobre la salud del mercado\n"
              "3. Que dice el long/short ratio: hay riesgo de squeeze?\n"
              "4. USDT dominance y dominancia BTC: hay dinero listo para entrar?\n"
              "5. Setup concreto: entrada, stop y objetivo con precios exactos")

    texto = ask_ai(prompt, max_chars=3500)
    safe_send(msg.chat.id, snap, message_id=m.message_id)
    time.sleep(1)
    safe_send(msg.chat.id, f"ANALISIS IA\n\n{texto}")


@bot.message_handler(commands=["etfs"])
def cmd_etfs(msg):
    if not allowed(msg): return
    m = bot.send_message(msg.chat.id, "Escaneando ETFs...")
    todos = ETFS_INDICES + ETFS_TEMATICOS[:6]
    signals = get_top_signals(todos, n=3)
    lines_idx, lines_tem = [], []
    for t in ETFS_INDICES:
        d = fetch_quote(t, "1mo")
        if d:
            lines_idx.append(f"{arrow(d['d1'])} {d['nombre']}: {d['price']} | semana {arrow(d['d5'])} | RSI {d['rsi']}")
    for t in ETFS_TEMATICOS:
        d = fetch_quote(t, "1mo")
        if d:
            lines_tem.append(f"{arrow(d['d1'])} {d['nombre']}: {d['price']} | semana {arrow(d['d5'])} | RSI {d['rsi']}")
    snap = "INDICES\n" + "\n".join(lines_idx) + "\n\nTEMATICOS\n" + "\n".join(lines_tem)
    safe_send(msg.chat.id, f"ETFs {datetime.now().strftime('%d/%m %H:%M')}\n\n{snap}", message_id=m.message_id)
    if signals:
        time.sleep(1)
        safe_send(msg.chat.id, "MEJORES SETUPS ETF:")
        for s in signals:
            send_signal(msg.chat.id, s)
            time.sleep(1)
    else:
        prompt = "ETFs:\n" + "\n".join(lines_idx[:5]) + "\n\n1. Mejor momentum\n2. Cual evitar\n3. Mejor ETF proximos 30 dias"
        texto = ask_ai(prompt)
        safe_send(msg.chat.id, texto)


@bot.message_handler(commands=["anomalias"])
def cmd_anomalias(msg):
    if not allowed(msg): return
    m = bot.send_message(msg.chat.id, "Buscando volumen anomalo 2.5x+...")
    todos = US_STOCKS + EU_STOCKS[:10] + ETFS_ESPECIALES
    anomalias = scan_anomalias(todos)
    if not anomalias:
        safe_send(msg.chat.id, "Sin anomalias de volumen detectadas.", message_id=m.message_id)
        return
    rows = [f"{a['nombre']} ({a['ticker']}): vol {a['vol_rel']}x | hoy {a['d1']:+.2f}% | precio {a['price']}"
            for a in anomalias]
    bloque = "\n".join(rows)
    prompt = (f"Anomalias volumen:\n{bloque}\n\n1. Cual es mas sospechosa\n2. M&A, earnings, FDA o insider?\n3. Como operar\n4. Mejor potencial vs trampa")
    texto = ask_ai(prompt)
    safe_send(msg.chat.id, f"ANOMALIAS VOLUMEN {datetime.now().strftime('%d/%m %H:%M')}\n\n{bloque}\n\n{texto}", message_id=m.message_id)


@bot.message_handler(commands=["sectores"])
def cmd_sectores(msg):
    if not allowed(msg): return
    m = bot.send_message(msg.chat.id, "Analizando 11 sectores SP500... (20s)")
    sectores = analisis_sectores()
    if not sectores:
        safe_send(msg.chat.id, "Sin datos.", message_id=m.message_id)
        return
    bulls = [s for s in sectores if s["estado"] == "BULL"]
    bears = [s for s in sectores if s["estado"] == "BEAR"]
    neutros = [s for s in sectores if s["estado"] == "NEUTRO"]
    lines = [f"{semaforo(s['estado'])} {s['sector']}: RSI {s['rsi']} | semana {s['d5']:+.1f}% | {s['sobre_ema']}% sobre EMA20"
             for s in sectores]
    snap = "\n".join(lines)
    prompt = (f"Sectores SP500:\n{snap}\n\n1. Sector mas fuerte y por que\n2. Rotacion sectorial detectada\n3. Sectores a evitar\n4. Mejor ETF sectorial ahora")
    texto = ask_ai(prompt)
    safe_send(msg.chat.id,
        f"SECTORES SP500 {datetime.now().strftime('%d/%m %H:%M')}\nBulls:{len(bulls)} Neutros:{len(neutros)} Bears:{len(bears)}\n\n{snap}\n\n{texto}",
        message_id=m.message_id)


@bot.message_handler(commands=["bull_detector"])
def cmd_bull_detector(msg):
    if not allowed(msg): return
    m = bot.send_message(msg.chat.id, "Detectando bull runs nacientes...")
    senales = bull_detector()
    if not senales:
        safe_send(msg.chat.id, "No se detectan bull runs claros ahora mismo.", message_id=m.message_id)
        return
    lines = []
    for s in senales:
        ticker_txt = f" ({s['ticker']})" if "ticker" in s else ""
        lines.append(f"[{s['tipo']}] {s['nombre']}{ticker_txt}: {s['detalle']}")
    bloque = "\n".join(lines)
    prompt = (f"Bull runs detectados:\n{bloque}\n\n1. Cual tiene mas probabilidad de ser real\n2. Bull market general o sectorial\n3. Como posicionarse: ETF concreto, entrada, stop\n4. Que podria truncarlo")
    texto = ask_ai(prompt)
    safe_send(msg.chat.id, f"BULL DETECTOR {datetime.now().strftime('%d/%m %H:%M')}\n\n{bloque}\n\n{texto}", message_id=m.message_id)


@bot.message_handler(commands=["noticias_impacto"])
def cmd_noticias_impacto(msg):
    if not allowed(msg): return
    m = bot.send_message(msg.chat.id, "Buscando noticias de alto impacto...")
    noticias = get_noticias_impacto()
    if not noticias:
        safe_send(msg.chat.id, "Sin noticias de alto impacto ahora mismo.", message_id=m.message_id)
        return
    lines = [f"- {n['title']}" for n in noticias[:12]]
    bloque = "\n".join(lines)
    prompt = (f"Noticias impacto:\n{bloque}\n\n1. Mayor impacto en bolsa\n2. OPA o fusion relevante\n3. Earnings sorpresa\n4. Acciones afectadas y como operar")
    texto = ask_ai(prompt)
    safe_send(msg.chat.id, f"NOTICIAS IMPACTO {datetime.now().strftime('%d/%m %H:%M')}\n\n{bloque}\n\n{texto}", message_id=m.message_id)


@bot.message_handler(commands=["noticias"])
def cmd_noticias(msg):
    if not allowed(msg): return
    m = bot.send_message(msg.chat.id, "Leyendo feeds RSS...")
    titulares = get_news(10)
    if titulares:
        bloque = "\n".join(f"- {t}" for t in titulares)
        prompt = f"Noticias:\n{bloque}\n\n1. 3 titulares clave\n2. Sentimiento\n3. Que esperar EU y EEUU\n4. Sectores a vigilar"
    else:
        prompt = "Estado mercados EU y EEUU hoy."
    texto = ask_ai(prompt)
    safe_send(msg.chat.id, f"Noticias {datetime.now().strftime('%d/%m %H:%M')}\n\n{texto}", message_id=m.message_id)


@bot.message_handler(commands=["mercados"])
def cmd_mercados(msg):
    if not allowed(msg): return
    m = bot.send_message(msg.chat.id, "Cargando mercados...")
    lines, data_ai = [], []
    for t, nom in INDICES.items():
        d = fetch_quote(t, "1mo")
        if d:
            lines.append(f"{arrow(d['d1'])} {nom}: {d['price']:,.0f} | semana {arrow(d['d5'])}")
            data_ai.append(f"{nom}: {d['price']:,.0f} ({d['d1']:+.2f}% hoy)")
    snap = "\n".join(lines) or "Sin datos"
    texto = ask_ai("Snapshot:\n" + "\n".join(data_ai) + "\n\nLectura global, divergencias EU/EEUU, que vigilar.")
    safe_send(msg.chat.id, f"Mercados {datetime.now().strftime('%d/%m %H:%M')}\n\n{snap}\n\n{texto}", message_id=m.message_id)


@bot.message_handler(commands=["senales_eu"])
def cmd_senales_eu(msg):
    if not allowed(msg): return
    m = bot.send_message(msg.chat.id, "Escaneando Europa...")
    signals = get_top_signals(EU_STOCKS, n=3)
    if not signals:
        safe_send(msg.chat.id, "Sin senales validas en Europa ahora mismo.", message_id=m.message_id)
        return
    bot.delete_message(msg.chat.id, m.message_id)
    safe_send(msg.chat.id, f"SENALES EUROPA {datetime.now().strftime('%d/%m %H:%M')}\n{len(signals)} oportunidades:")
    for s in signals:
        send_signal(msg.chat.id, s)
        time.sleep(1)


@bot.message_handler(commands=["senales_us"])
def cmd_senales_us(msg):
    if not allowed(msg): return
    m = bot.send_message(msg.chat.id, "Escaneando EEUU...")
    signals = get_top_signals(US_STOCKS + list(CRYPTO), n=3)
    if not signals:
        safe_send(msg.chat.id, "Sin senales validas en EEUU ahora mismo.", message_id=m.message_id)
        return
    bot.delete_message(msg.chat.id, m.message_id)
    safe_send(msg.chat.id, f"SENALES EEUU {datetime.now().strftime('%d/%m %H:%M')}\n{len(signals)} oportunidades:")
    for s in signals:
        send_signal(msg.chat.id, s)
        time.sleep(1)


@bot.message_handler(commands=["macro"])
def cmd_macro(msg):
    if not allowed(msg): return
    m = bot.send_message(msg.chat.id, "Cargando macro...")
    lines, data_ai = [], []
    for t, nom in MACRO_TICKERS.items():
        d = fetch_quote(t, "1mo")
        if d:
            lines.append(f"{arrow(d['d1'])} {nom}: {d['price']:,.2f} | semana {arrow(d['d5'])}")
            data_ai.append(f"{nom}: {d['price']:,.2f} ({d['d1']:+.2f}%)")
    snap = "\n".join(lines) or "Sin datos"
    texto = ask_ai("Macro:\n" + "\n".join(data_ai) + "\n\n1. Risk-on o risk-off\n2. VIX\n3. DXY impacto\n4. Que hacer")
    safe_send(msg.chat.id, f"MACRO {datetime.now().strftime('%d/%m %H:%M')}\n\n{snap}\n\n{texto}", message_id=m.message_id)


@bot.message_handler(commands=["backtest"])
def cmd_backtest(msg):
    if not allowed(msg): return
    m = bot.send_message(msg.chat.id, "Ejecutando backtest 3 meses... (30s)")
    stocks = US_STOCKS[:8] + [s for s in EU_STOCKS if ".MC" in s or ".PA" in s][:5]
    results = run_backtest(stocks)
    if not results:
        safe_send(msg.chat.id, "Sin datos.", message_id=m.message_id)
        return
    lines = []
    total_wins = total_ops = 0
    for r in results:
        total_wins += r["wins"]
        total_ops += r["total"]
        bar = "W"*r["wins"] + "L"*r["losses"]
        lines.append(f"{r['nombre']} ({r['ticker']}): {r['winrate']}% - {r['wins']}W/{r['losses']}L  {bar}")
    global_wr = round(total_wins/total_ops*100, 1) if total_ops > 0 else 0
    safe_send(msg.chat.id,
        f"BACKTEST 3 MESES\nCriterio: RSI<45 + cruce MACD / TP +3% SL -2%\n"
        f"Acierto global: {global_wr}% ({total_wins}W de {total_ops} ops)\n\n" + "\n".join(lines),
        message_id=m.message_id)


@bot.message_handler(commands=["alerta"])
def cmd_alerta(msg):
    if not allowed(msg): return
    parts = msg.text.split()
    if len(parts) < 3:
        safe_send(msg.chat.id, "Uso: /alerta TICKER PRECIO\nEj: /alerta NVDA 240\nEj: /alerta BTC-USD 90000")
        return
    ticker = parts[1].upper()
    try:
        precio = float(parts[2])
    except:
        safe_send(msg.chat.id, "Precio invalido.")
        return
    d = fetch_quote(ticker, "1mo")
    if not d:
        safe_send(msg.chat.id, f"Ticker {ticker} no encontrado. Comprueba que sea correcto.\nEj: NVDA, AAPL, BTC-USD, SAN.MC")
        return
    direction = "sube a" if precio > d["price"] else "baja a"
    ALERTS[msg.chat.id].append({"ticker": ticker, "nombre": d["nombre"], "price": precio, "direction": direction, "triggered": False})
    activas = len([a for a in ALERTS[msg.chat.id] if not a["triggered"]])
    safe_send(msg.chat.id,
        f"Alerta creada\n{d['nombre']} ({ticker}) ahora: {d['price']}\n"
        f"Te aviso cuando {direction} {precio}\nAlertas activas: {activas}")


@bot.message_handler(commands=["alertas"])
def cmd_alertas(msg):
    if not allowed(msg): return
    activas = [a for a in ALERTS[msg.chat.id] if not a["triggered"]]
    if not activas:
        safe_send(msg.chat.id, "No tienes alertas activas.\nCrea una con /alerta NVDA 240")
        return
    lines = []
    for i, a in enumerate(activas, 1):
        d = fetch_quote(a["ticker"], "1mo")
        actual = d["price"] if d else "?"
        lines.append(f"{i}. {a['nombre']} ({a['ticker']}) cuando {a['direction']} {a['price']} (ahora: {actual})")
    safe_send(msg.chat.id, f"Alertas activas ({len(activas)}):\n\n" + "\n".join(lines))


@bot.message_handler(commands=["borra_alerta"])
def cmd_borra_alerta(msg):
    if not allowed(msg): return
    parts = msg.text.split()
    if len(parts) < 2:
        safe_send(msg.chat.id, "Uso: /borra_alerta NUMERO\nEj: /borra_alerta 1")
        return
    try:
        idx = int(parts[1]) - 1
        activas = [a for a in ALERTS[msg.chat.id] if not a["triggered"]]
        if 0 <= idx < len(activas):
            activas[idx]["triggered"] = True
            safe_send(msg.chat.id, f"Alerta {parts[1]} eliminada.")
        else:
            safe_send(msg.chat.id, "Numero invalido.")
    except:
        safe_send(msg.chat.id, "Uso: /borra_alerta NUMERO")


@bot.message_handler(commands=["riesgo"])
def cmd_riesgo(msg):
    if not allowed(msg): return
    parts = msg.text.split()
    if len(parts) < 6:
        safe_send(msg.chat.id, "Uso: /riesgo CAPITAL RIESGO% TICKER ENTRADA STOP\nEj: /riesgo 10000 2 NVDA 890 865")
        return
    try:
        capital = float(parts[1])
        riesgo = float(parts[2])
        ticker = parts[3].upper()
        entrada = float(parts[4])
        stop = float(parts[5])
        nom = nombre(ticker)
        riesgo_e = capital * riesgo / 100
        riesgo_a = abs(entrada - stop)
        if riesgo_a == 0:
            safe_send(msg.chat.id, "Entrada y stop no pueden ser iguales.")
            return
        acciones = int(riesgo_e / riesgo_a)
        valor_pos = acciones * entrada
        tp1 = round(entrada + riesgo_a * 1.5, 2)
        tp2 = round(entrada + riesgo_a * 3.0, 2)
        safe_send(msg.chat.id,
            f"GESTION DE RIESGO\n{nom} ({ticker})\n\n"
            f"Capital:          {capital:,.0f}\n"
            f"Riesgo maximo:    {riesgo}% = {riesgo_e:,.0f}\n"
            f"Entrada:          {entrada}\n"
            f"Stop loss:        {stop} (-{riesgo_a:.2f} por accion)\n\n"
            f"ACCIONES A COMPRAR: {acciones}\n"
            f"Valor posicion:     {valor_pos:,.0f}\n\n"
            f"TP1 (R/R 1.5x): {tp1}\n"
            f"TP2 (R/R 3.0x): {tp2}")
    except Exception as e:
        safe_send(msg.chat.id, f"Error: {e}")


@bot.message_handler(commands=["oportunidades"])
def cmd_oportunidades(msg):
    if not allowed(msg): return
    m = bot.send_message(msg.chat.id, "Escaneando oportunidades...")
    rows = []
    for t in (US_STOCKS[:7] + EU_STOCKS[:6]):
        d = fetch_quote(t, "3mo")
        if d:
            rows.append(f"{d['nombre']} ({t}): RSI={d['rsi']}, MACD={'SI' if d['macd_cross_up'] else 'NO'}, vol={d['vol_rel']}x, 5d={d['d5']}%")
    prompt = "Datos:\n" + "\n".join(rows) + "\n\n1. 2-3 mejores setups\n2. Acciones a evitar\n3. Trade concreto entrada/objetivo/stop\n4. Riesgo 1-10"
    texto = ask_ai(prompt)
    safe_send(msg.chat.id, f"Oportunidades\n\n{texto}", message_id=m.message_id)


@bot.message_handler(commands=["explosiones"])
def cmd_explosiones(msg):
    if not allowed(msg): return
    m = bot.send_message(msg.chat.id, "Buscando explosiones...")
    candidates = scan_explosions(US_STOCKS + EU_STOCKS)
    if not candidates:
        safe_send(msg.chat.id, "Sin senales de explosion.", message_id=m.message_id)
        return
    rows = [f"{c['nombre']} ({c['ticker']}): {c['price']} | semana {c['d5']:+.1f}% | vol {c['vol_rel']}x" for c in candidates]
    bloque = "\n".join(rows)
    texto = ask_ai(f"Explosiones:\n{bloque}\n\n1. Por que podria subir\n2. Nivel a superar\n3. Riesgo")
    safe_send(msg.chat.id, f"Posibles Explosiones\n\n{bloque}\n\n{texto}", message_id=m.message_id)


@bot.message_handler(commands=["calendario"])
def cmd_calendario(msg):
    if not allowed(msg): return
    m = bot.send_message(msg.chat.id, "Cargando calendario...")
    eventos = get_economic_calendar()
    if eventos:
        eventos_sorted = sorted(eventos, key=lambda x: x.get("date",""))
        lines = [f"{e.get('date','')[:10]} [{e.get('country','').upper()}] {e.get('title','')}" for e in eventos_sorted]
        bloque = "\n".join(lines)
        texto = ask_ai(f"Eventos:\n{bloque}\n\n1. 3 mas importantes\n2. Que esperar\n3. Sectores afectados")
        safe_send(msg.chat.id, f"Calendario\n\n{bloque}\n\n{texto}", message_id=m.message_id)
    else:
        texto = ask_ai("Eventos clave esta semana: Fed, BCE, inflacion, empleo, earnings.")
        safe_send(msg.chat.id, f"Calendario\n\n{texto}", message_id=m.message_id)


@bot.message_handler(commands=["sr"])
def cmd_sr(msg):
    if not allowed(msg): return
    m = bot.send_message(msg.chat.id, "Escaneando S/R...")
    alerts = scan_sr_alerts(US_STOCKS[:10] + EU_STOCKS[:10])
    if alerts:
        bloque = "\n".join(alerts)
        texto = ask_ai(f"S/R:\n{bloque}\n\n1. Rebote o ruptura\n2. Confirmacion\n3. Operativa")
        safe_send(msg.chat.id, f"S/R Scanner\n\n{bloque}\n\n{texto}", message_id=m.message_id)
    else:
        safe_send(msg.chat.id, "Sin acciones en zona critica.", message_id=m.message_id)


@bot.message_handler(commands=["metales"])
def cmd_metales(msg):
    if not allowed(msg): return
    m = bot.send_message(msg.chat.id, "Analizando metales...")
    lines, data_ai = [], []
    for t, nom in METALES.items():
        d = fetch_quote(t, "3mo")
        if d:
            lines.append(f"{arrow(d['d1'])} {nom}: {d['price']:,.2f} | semana {arrow(d['d5'])} | RSI {d['rsi']}")
            data_ai.append(f"{nom}: precio={d['price']}, RSI={d['rsi']}, 1d={d['d1']}%, S1={d['s1']}, R1={d['r1']}")
    snap = "\n".join(lines) or "Sin datos"
    texto = ask_ai("Metales:\n" + "\n".join(data_ai) + "\n\n1. Tendencia\n2. COMPRAR/VENDER/ESPERAR\n3. Entrada stop objetivo\n4. Catalizador macro")
    safe_send(msg.chat.id, f"Metales {datetime.now().strftime('%d/%m %H:%M')}\n\n{snap}\n\n{texto}", message_id=m.message_id)


@bot.message_handler(commands=["ipos"])
def cmd_ipos(msg):
    if not allowed(msg): return
    m = bot.send_message(msg.chat.id, "Cargando IPOs...")
    lines = []
    for ipo in IPOS_WATCH:
        if ipo["ticker"]:
            d = fetch_quote(ipo["ticker"], "1mo")
            if d:
                lines.append(f"{ipo['nombre']} ({ipo['ticker']}) {d['price']} | hoy {d['d1']:+.2f}% | RSI {d['rsi']}\n  Val: {ipo['valor']} | {ipo['bolsa']}")
            else:
                lines.append(f"{ipo['nombre']} ({ipo['ticker']}) - {ipo['estado']}\n  Val: {ipo['valor']}")
        else:
            lines.append(f"{ipo['nombre']} - {ipo['estado']}\n  Sector: {ipo['sector']} | Val: {ipo['valor']}")
    snap = "\n\n".join(lines)
    noticias_txt = "\n".join(f"- {n}" for n in get_ipo_news())
    texto = ask_ai(f"IPOs:\n{snap}\n\nNoticias:\n{noticias_txt}\n\n1. SI/NO/ESPERAR\n2. Riesgo\n3. Mejor potencial\n4. Debut o esperar")
    safe_send(msg.chat.id, f"IPOs {datetime.now().strftime('%d/%m %H:%M')}\n\n{snap}\n\n{texto}", message_id=m.message_id)


@bot.message_handler(commands=["analisis"])
def cmd_analisis(msg):
    if not allowed(msg): return
    parts = msg.text.split()
    if len(parts) < 2:
        safe_send(msg.chat.id, "Uso: /analisis TICKER\nEj: /analisis NVDA o /analisis SPY o /analisis SAN.MC")
        return
    ticker = parts[1].upper()
    m = bot.send_message(msg.chat.id, f"Analizando {nombre(ticker)}...")
    d = fetch_quote(ticker, "6mo")
    if not d:
        safe_send(msg.chat.id, f"Sin datos para {ticker}.\nComprueba el ticker: NVDA, AAPL, BTC-USD, SAN.MC", message_id=m.message_id)
        return
    prompt = (f"Empresa/ETF: {d['nombre']} ({ticker})\n"
              f"Precio: {d['price']} | 1d: {d['d1']}% | 5d: {d['d5']}% | 20d: {d['d20']}%\n"
              f"RSI: {d['rsi']} | MACD cruce alcista: {d['macd_cross_up']} | Vol: {d['vol_rel']}x\n"
              f"EMA20: {d['ema20']} ({'sobre' if d['sobre_ema20'] else 'bajo'})\n"
              f"R2: {d['r2']} R1: {d['r1']} | Pivot: {d['pivot']} | S1: {d['s1']} S2: {d['s2']}\n"
              f"Max52: {d['hi52']} | Min52: {d['lo52']}\n\n"
              "1. Posicion tecnica\n2. Niveles clave\n3. Escenario alcista vs bajista\n4. Sesgo operativo\n5. Entrada, stop y objetivo")
    texto = ask_ai(prompt)
    header = (f"{d['nombre']} ({ticker})\n"
              f"Precio {d['price']} | Hoy {d['d1']:+.2f}% | Semana {d['d5']:+.2f}% | Mes {d['d20']:+.2f}%\n"
              f"RSI {d['rsi']} | Vol {d['vol_rel']}x | MACD: {'SI' if d['macd_cross_up'] else 'NO'}\n"
              f"EMA20: {'SOBRE' if d['sobre_ema20'] else 'BAJO'} ({d['ema20']})\n"
              f"R2 {d['r2']} | R1 {d['r1']} | Pivot {d['pivot']} | S1 {d['s1']} | S2 {d['s2']}\n"
              f"Rango 52s: {d['lo52']} - {d['hi52']}\n\n")
    safe_send(msg.chat.id, header + texto, message_id=m.message_id)


@bot.message_handler(commands=["crypto"])
def cmd_crypto(msg):
    if not allowed(msg): return
    m = bot.send_message(msg.chat.id, "Cargando crypto...")
    lines, data_ai = [], []
    for t in CRYPTO:
        d = fetch_quote(t, "1mo")
        if d:
            lines.append(f"{arrow(d['d1'])} {d['nombre']}: {d['price']:,.0f} | semana {arrow(d['d5'])} | RSI {d['rsi']}")
            data_ai.append(f"{d['nombre']}: {d['price']:,.0f} ({d['d1']:+.2f}%) RSI={d['rsi']} S1={d['s1']} R1={d['r1']}")
    texto = ask_ai("Crypto:\n" + "\n".join(data_ai) + "\n\n1. Lectura tecnica global\n2. Mejor setup\n3. Niveles criticos Bitcoin\n4. Hay altseason?")
    safe_send(msg.chat.id, f"Crypto {datetime.now().strftime('%H:%M')}\n\n" + "\n".join(lines) + f"\n\n{texto}", message_id=m.message_id)


@bot.message_handler(commands=["seguimiento"])
def cmd_seguimiento(msg):
    if not allowed(msg): return
    partes = msg.text.split()
    if len(partes) < 2:
        activos = [t for t in SEGUIMIENTO[msg.chat.id] if t.get("abierto", True)]
        if not activos:
            safe_send(msg.chat.id,
                "No tienes trades en seguimiento.\n\n"
                "Para añadir un trade:\n"
                "/seguimiento add TICKER ENTRADA STOP TP1 TP2\n"
                "Ej: /seguimiento add NVDA 890 865 920 950\n\n"
                "Para cerrar:\n"
                "/seguimiento close 1")
            return
        lines = [f"TRADES ABIERTOS {datetime.now().strftime('%d/%m %H:%M')}"]
        total_pnl = 0
        for i, t in enumerate(activos, 1):
            d = fetch_quote(t["ticker"], "1mo")
            if not d:
                lines.append(f"{i}. {t['ticker']} - sin datos")
                continue
            precio_actual = d["price"]
            pnl_pct = (precio_actual - t["entrada"]) / t["entrada"] * 100
            pnl_abs = round(precio_actual - t["entrada"], 2)
            total_pnl += pnl_pct
            estado = "TP1 alcanzado" if precio_actual >= t["tp1"] else (
                     "STOP cercano" if precio_actual <= t["stop"] * 1.01 else "En curso")
            lines.append(
                f"{i}. {nombre(t['ticker'])} ({t['ticker']})\n"
                f"   Entrada:{t['entrada']} Actual:{precio_actual} P&L:{pnl_pct:+.2f}%\n"
                f"   TP1:{t['tp1']} TP2:{t['tp2']} Stop:{t['stop']} -> {estado}"
            )
        lines.append(f"\nP&L medio: {total_pnl/len(activos):+.2f}%")
        safe_send(msg.chat.id, "\n".join(lines))
        return

    if partes[1] == "add" and len(partes) >= 7:
        ticker  = partes[2].upper()
        entrada = float(partes[3])
        stop    = float(partes[4])
        tp1     = float(partes[5])
        tp2     = float(partes[6])
        SEGUIMIENTO[msg.chat.id].append({
            "ticker": ticker, "entrada": entrada, "stop": stop,
            "tp1": tp1, "tp2": tp2, "abierto": True,
            "fecha": datetime.now().strftime("%d/%m %H:%M"),
        })
        safe_send(msg.chat.id,
            f"Trade abierto\n{nombre(ticker)} ({ticker})\n"
            f"Entrada: {entrada} | Stop: {stop}\n"
            f"TP1: {tp1} | TP2: {tp2}\n"
            f"Trades activos: {len([t for t in SEGUIMIENTO[msg.chat.id] if t.get('abierto')])}")

    elif partes[1] == "close" and len(partes) >= 3:
        try:
            idx = int(partes[2]) - 1
            activos = [t for t in SEGUIMIENTO[msg.chat.id] if t.get("abierto", True)]
            if 0 <= idx < len(activos):
                t = activos[idx]
                d = fetch_quote(t["ticker"], "1mo")
                precio_cierre = d["price"] if d else t["entrada"]
                pnl = (precio_cierre - t["entrada"]) / t["entrada"] * 100
                t["abierto"] = False
                safe_send(msg.chat.id,
                    f"Trade cerrado\n{nombre(t['ticker'])}\n"
                    f"Entrada: {t['entrada']} -> Cierre: {precio_cierre}\n"
                    f"P&L: {pnl:+.2f}%")
            else:
                safe_send(msg.chat.id, "Numero invalido.")
        except:
            safe_send(msg.chat.id, "Uso: /seguimiento close NUMERO")
    else:
        safe_send(msg.chat.id,
            "Uso:\n"
            "/seguimiento - ver trades abiertos con P&L\n"
            "/seguimiento add TICKER ENTRADA STOP TP1 TP2\n"
            "/seguimiento close NUMERO")


@bot.message_handler(commands=["resumen_semana"])
def cmd_resumen_semana(msg):
    if not allowed(msg): return
    m = bot.send_message(msg.chat.id, "Generando resumen de la semana...")
    lines_eu, lines_us = [], []
    for t in EU_STOCKS[:8]:
        d = fetch_quote(t, "1mo")
        if d and abs(d["d5"]) > 2:
            lines_eu.append(f"{d['nombre']} ({t}): {d['d5']:+.1f}% semana | RSI {d['rsi']}")
    for t in US_STOCKS[:10]:
        d = fetch_quote(t, "1mo")
        if d and abs(d["d5"]) > 2:
            lines_us.append(f"{d['nombre']} ({t}): {d['d5']:+.1f}% semana | RSI {d['rsi']}")
    # BTC semanal
    btc = fetch_quote("BTC-USD", "1mo")
    fg  = get_fear_greed()
    btc_txt  = f"BTC: {btc['price']:,.0f} ({btc['d5']:+.1f}% semana)" if btc else ""
    fg_txt   = f"Fear&Greed: {fg['valor']}/100 ({fg['clasificacion']})" if fg else ""
    eu_txt   = "\n".join(lines_eu[:5]) or "Sin movimientos destacados"
    us_txt   = "\n".join(lines_us[:5]) or "Sin movimientos destacados"
    prompt = (f"Resumen semana:\n"
              f"Europa destacados:\n{eu_txt}\n\n"
              f"EEUU destacados:\n{us_txt}\n\n"
              f"Crypto: {btc_txt} | {fg_txt}\n\n"
              "1. Ganadores y perdedores de la semana\n"
              "2. Que sectores lideraron\n"
              "3. Que esperar la proxima semana\n"
              "4. Niveles clave a vigilar el lunes")
    texto = ask_ai(prompt)
    safe_send(msg.chat.id,
        f"RESUMEN SEMANA {datetime.now().strftime('%d/%m')}\n\n"
        f"EUROPA:\n{eu_txt}\n\nEEUU:\n{us_txt}\n\n{btc_txt} | {fg_txt}\n\n{texto}",
        message_id=m.message_id)


@bot.message_handler(commands=["ciclo"])
def cmd_ciclo(msg):
    if not allowed(msg): return
    m = bot.send_message(msg.chat.id, "Analizando ciclo de mercado... BTC, SP500 y Europa")

    mercados_data = []

    # BTC
    btc = fetch_quote("BTC-USD", "1y")
    fg  = get_fear_greed()
    deriv = get_binance_derivatives("BTCUSDT")
    funding = deriv.get("funding", {}).get("valor") if deriv else None
    if btc:
        fg_val = fg["valor"] if fg else None
        dist_max_btc = round((btc["price"] - btc["hi52"]) / btc["hi52"] * 100, 1)
        recup_min_btc = round((btc["price"] - btc["lo52"]) / btc["lo52"] * 100, 1)
        d_anual_btc = btc.get("d20", 0)  # usamos d20 como proxy si no tenemos anual
        try:
            hist_anual = yf.Ticker("BTC-USD").history(period="1y")
            if not hist_anual.empty and len(hist_anual) > 50:
                precio_hace_1y = hist_anual["Close"].iloc[0]
                d_anual_btc = round((btc["price"] - precio_hace_1y) / precio_hace_1y * 100, 1)
        except:
            pass
        ciclo_btc = detectar_ciclo(
            "Bitcoin", btc["rsi"],
            fetch_weekly_rsi("BTC-USD"),
            fg=fg_val, funding=funding,
            sobre_ema20=btc["sobre_ema20"],
            sobre_ema50=btc["sobre_ema50"],
            tendencia_alcista=btc["tendencia_alcista"],
            d20=btc["d20"], d5=btc["d5"],
            vol_rel=btc["vol_rel"],
            dist_desde_maximo=dist_max_btc,
            recuperacion_desde_minimo=recup_min_btc,
            d_anual=d_anual_btc,
        )
        mercados_data.append({
            "nombre": "BTC",
            "fase": ciclo_btc["nombre"],
            "fase_num": ciclo_btc["fase_num"],
            "descripcion": ciclo_btc["descripcion"],
            "emocion": ciclo_btc["emocion"],
            "color": ciclo_btc["color"],
        })

    # SP500
    spx = fetch_quote("^GSPC", "1y")
    vix = fetch_quote("^VIX", "1mo")
    vix_val = vix["price"] if vix else None
    if spx:
        dist_max_spx = round((spx["price"] - spx["hi52"]) / spx["hi52"] * 100, 1)
        recup_min_spx = round((spx["price"] - spx["lo52"]) / spx["lo52"] * 100, 1)
        d_anual_spx = spx.get("d20", 0)
        try:
            hist_anual = yf.Ticker("^GSPC").history(period="1y")
            if not hist_anual.empty and len(hist_anual) > 50:
                precio_hace_1y = hist_anual["Close"].iloc[0]
                d_anual_spx = round((spx["price"] - precio_hace_1y) / precio_hace_1y * 100, 1)
        except:
            pass
        ciclo_spx = detectar_ciclo(
            "SP500", spx["rsi"],
            fetch_weekly_rsi("^GSPC"),
            vix=vix_val,
            sobre_ema20=spx["sobre_ema20"],
            sobre_ema50=spx["sobre_ema50"],
            tendencia_alcista=spx["tendencia_alcista"],
            d20=spx["d20"], d5=spx["d5"],
            vol_rel=spx["vol_rel"],
            dist_desde_maximo=dist_max_spx,
            recuperacion_desde_minimo=recup_min_spx,
            d_anual=d_anual_spx,
        )
        mercados_data.append({
            "nombre": "SP500",
            "fase": ciclo_spx["nombre"],
            "fase_num": ciclo_spx["fase_num"],
            "descripcion": ciclo_spx["descripcion"],
            "emocion": ciclo_spx["emocion"],
            "color": ciclo_spx["color"],
        })

    # DAX
    dax = fetch_quote("^GDAXI", "1y")
    if dax:
        dist_max_dax = round((dax["price"] - dax["hi52"]) / dax["hi52"] * 100, 1)
        recup_min_dax = round((dax["price"] - dax["lo52"]) / dax["lo52"] * 100, 1)
        d_anual_dax = dax.get("d20", 0)
        try:
            hist_anual = yf.Ticker("^GDAXI").history(period="1y")
            if not hist_anual.empty and len(hist_anual) > 50:
                precio_hace_1y = hist_anual["Close"].iloc[0]
                d_anual_dax = round((dax["price"] - precio_hace_1y) / precio_hace_1y * 100, 1)
        except:
            pass
        ciclo_dax = detectar_ciclo(
            "DAX", dax["rsi"],
            fetch_weekly_rsi("^GDAXI"),
            vix=vix_val,
            sobre_ema20=dax["sobre_ema20"],
            sobre_ema50=dax["sobre_ema50"],
            tendencia_alcista=dax["tendencia_alcista"],
            d20=dax["d20"], d5=dax["d5"],
            vol_rel=dax["vol_rel"],
            dist_desde_maximo=dist_max_dax,
            recuperacion_desde_minimo=recup_min_dax,
            d_anual=d_anual_dax,
        )
        mercados_data.append({
            "nombre": "DAX",
            "fase": ciclo_dax["nombre"],
            "fase_num": ciclo_dax["fase_num"],
            "descripcion": ciclo_dax["descripcion"],
            "emocion": ciclo_dax["emocion"],
            "color": ciclo_dax["color"],
        })

    if not mercados_data:
        safe_send(msg.chat.id, "Error obteniendo datos.", message_id=m.message_id)
        return

    # Generar grafico
    try:
        chart = generate_cycle_chart(mercados_data)
        # Texto resumen
        lines = [f"CICLO DE MERCADO {datetime.now().strftime('%d/%m %H:%M')}\n"]
        for md in mercados_data:
            lines.append(f"{md['nombre']}: {md['fase']}")
            lines.append(f"  {md['descripcion']}")
            lines.append(f"  Emocion: {md['emocion']}")
            lines.append("")
        # Analisis IA
        datos_ia = "\n".join([f"{md['nombre']}: fase {md['fase']} - {md['descripcion']}" for md in mercados_data])
        if fg:
            datos_ia += f"\nFear&Greed BTC: {fg['valor']}/100"
        if vix_val:
            datos_ia += f"\nVIX: {vix_val}"
        prompt = (f"Ciclo de mercado actual:\n{datos_ia}\n\n"
                  "1. En que fase real estamos en cada mercado y por que\n"
                  "2. Que suele pasar a continuacion segun el ciclo\n"
                  "3. Que deberia hacer un inversor en esta fase\n"
                  "4. Cuanto tiempo suelen durar estas fases historicamente")
        texto_ia = ask_ai(prompt)
        caption = "\n".join(lines) + texto_ia
        if len(caption) > 1020:
            bot.send_photo(msg.chat.id, chart,
                          caption="\n".join(lines[:8]))
            bot.delete_message(msg.chat.id, m.message_id)
            safe_send(msg.chat.id, "\n".join(lines) + "\n" + texto_ia)
        else:
            bot.delete_message(msg.chat.id, m.message_id)
            bot.send_photo(msg.chat.id, chart, caption=caption)
    except Exception as e:
        log.error(f"Ciclo chart error: {e}")
        lines = [f"CICLO DE MERCADO {datetime.now().strftime('%d/%m %H:%M')}\n"]
        for md in mercados_data:
            lines.append(f"{md['nombre']}: {md['fase']}")
            lines.append(f"  {md['descripcion']}")
        safe_send(msg.chat.id, "\n".join(lines), message_id=m.message_id)


@bot.message_handler(commands=["ayuda"])
def cmd_ayuda(msg):
    if not allowed(msg): return
    safe_send(msg.chat.id,
        "GUIA DE COMANDOS\n\n"
        "CRYPTO:\n"
        "/btc - Analisis profundo BTC con derivados Binance\n"
        "/crypto - BTC ETH SOL BNB precios y RSI\n\n"
        "SENALES:\n"
        "/senales_eu - Senales Europa (RSI+MACD+2 timeframes)\n"
        "/senales_us - Senales EEUU y crypto\n"
        "/etfs - ETFs con señales\n\n"
        "MERCADO:\n"
        "/mercados - Indices EU y EEUU\n"
        "/sectores - Semaforo 11 sectores SP500\n"
        "/bull_detector - Bull runs nacientes\n"
        "/anomalias - Volumen anomalo posible rumor\n"
        "/noticias_impacto - M&A, earnings, FDA\n"
        "/explosiones - Momentum explosivo\n"
        "/macro - VIX, DXY, bonos, oro, petroleo\n\n"
        "HERRAMIENTAS:\n"
        "/seguimiento - Ver P&L trades abiertos\n"
        "/seguimiento add NVDA 890 865 920 950\n"
        "/seguimiento close 1\n"
        "/alerta NVDA 950 - Avisa cuando llegue\n"
        "/alertas - Ver alertas activas\n"
        "/borra_alerta 1\n"
        "/riesgo 10000 2 NVDA 890 865\n"
        "/analisis TICKER - Analisis completo\n"
        "/backtest - Historico aciertos sistema\n"
        "/resumen_semana - Balance semanal\n\n"
        "OTROS:\n"
        "/metales /ipos /calendario /sr\n"
        "Pregunta libre - IA responde con precio real")


@bot.message_handler(func=lambda m: True)
def handle_text(msg):
    if not allowed(msg): return
    # Inyectar precio real si mencionan un ticker conocido
    texto_lower = msg.text.lower()
    ticker_detectado = None
    for t, n in NOMBRES.items():
        if n.lower() in texto_lower or t.lower() in texto_lower:
            ticker_detectado = t
            break
    precio_txt = ""
    if ticker_detectado:
        d = fetch_quote(ticker_detectado, "1mo")
        if d:
            precio_txt = f"\nDatos actuales {d['nombre']}: precio={d['price']}, RSI={d['rsi']}, hoy={d['d1']}%, semana={d['d5']}%\n"
    prompt = f"Pregunta: {msg.text}{precio_txt}\nResponde como analista financiero con datos actuales."
    resp = ask_ai(prompt)
    safe_send(msg.chat.id, resp)


@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    bot.answer_callback_query(call.id)
    call.message.from_user = call.from_user
    handlers = {
        "noticias": cmd_noticias, "mercados": cmd_mercados,
        "senales_eu": cmd_senales_eu, "senales_us": cmd_senales_us,
        "etfs": cmd_etfs, "sectores": cmd_sectores,
        "bull_detector": cmd_bull_detector, "anomalias": cmd_anomalias,
        "btc": cmd_btc, "crypto": cmd_crypto,
        "noticias_impacto": cmd_noticias_impacto,
        "oportunidades": cmd_oportunidades, "explosiones": cmd_explosiones,
        "calendario": cmd_calendario, "sr_scan": cmd_sr,
        "macro": cmd_macro, "metales": cmd_metales,
        "ipos": cmd_ipos, "backtest": cmd_backtest,
        "alertas": cmd_alertas, "seguimiento": cmd_seguimiento,
        "resumen_semana": cmd_resumen_semana, "ayuda": cmd_ayuda,
        "ciclo": cmd_ciclo,
        "riesgo_info": lambda m: safe_send(m.chat.id, "Uso: /riesgo CAPITAL RIESGO% TICKER ENTRADA STOP\nEj: /riesgo 10000 2 NVDA 890 865"),
    }
    fn = handlers.get(call.data)
    if fn:
        fn(call.message)


# ── JOBS AUTOMATICOS ──────────────────────────────────────────────────────────

def job_senales_eu():
    """9:00 lunes-viernes — Senales Europa."""
    if not es_dia_laborable():
        return
    safe_send(ALLOWED_USER_ID, f"BUENOS DIAS - Senales Europa {datetime.now().strftime('%d/%m')}")
    signals = get_top_signals(EU_STOCKS, n=3)
    if signals:
        for s in signals:
            send_signal(ALLOWED_USER_ID, s)
            time.sleep(2)
    else:
        safe_send(ALLOWED_USER_ID, "Sin senales validas en Europa esta manana.")
    titulares = get_news(6)
    bloque = "\n".join(f"- {t}" for t in titulares) if titulares else "Sin noticias"
    eventos = get_economic_calendar()
    cal_hoy = [e for e in eventos if datetime.now().strftime("%Y-%m-%d") in e.get("date","")]
    cal_txt = "\n".join(f"- {e.get('title','')}" for e in cal_hoy) or "Sin eventos clave"
    texto = ask_ai(f"Briefing {datetime.now().strftime('%A %d/%m')}:\nNoticias:\n{bloque}\nEventos:\n{cal_txt}\n\nResumen y niveles clave DAX e IBEX.")
    safe_send(ALLOWED_USER_ID, f"Briefing Manana\n\n{texto}")


def job_senales_us():
    """15:00 lunes-viernes — Senales EEUU."""
    if not es_dia_laborable():
        return
    safe_send(ALLOWED_USER_ID, f"PREMERCADO EEUU {datetime.now().strftime('%d/%m %H:%M')}")
    signals = get_top_signals(US_STOCKS + list(CRYPTO), n=3)
    if signals:
        for s in signals:
            send_signal(ALLOWED_USER_ID, s)
            time.sleep(2)
    else:
        safe_send(ALLOWED_USER_ID, "Sin senales validas en EEUU para esta sesion.")


def job_close_eu():
    """17:35 lunes-viernes — Cierre Europa."""
    if not es_dia_laborable():
        return
    lines, data_ai = [], []
    for t, nom in list(INDICES.items())[3:]:
        d = fetch_quote(t, "5d")
        if d:
            lines.append(f"{arrow(d['d1'])} {nom}: {d['price']:,.0f}")
            data_ai.append(f"{nom}: {d['d1']:+.2f}%")
    snap = "\n".join(lines)
    texto = ask_ai("Cierre EU:\n" + "\n".join(data_ai) + "\n\nResumen y que esperar de EEUU.")
    safe_send(ALLOWED_USER_ID, f"Cierre Europa {datetime.now().strftime('%d/%m %H:%M')}\n\n{snap}\n\n{texto}")


def job_close_us():
    """22:05 lunes-viernes — Cierre EEUU."""
    if not es_dia_laborable():
        return
    lines, data_ai = [], []
    for t, nom in list(INDICES.items())[:3]:
        d = fetch_quote(t, "5d")
        if d:
            lines.append(f"{arrow(d['d1'])} {nom}: {d['price']:,.0f}")
            data_ai.append(f"{nom}: {d['d1']:+.2f}%")
    snap = "\n".join(lines)
    texto = ask_ai("Cierre EEUU:\n" + "\n".join(data_ai) + "\n\nResumen y perspectiva manana.")
    safe_send(ALLOWED_USER_ID, f"Cierre EEUU {datetime.now().strftime('%d/%m %H:%M')}\n\n{snap}\n\n{texto}")


def job_crypto_weekend():
    """Sabado y domingo 10:00 — Solo crypto (mercado 24/7)."""
    if es_dia_laborable():
        return
    signals = get_top_signals(list(CRYPTO), n=3)
    fg = get_fear_greed()
    fg_txt = f"Fear&Greed: {fg['valor']}/100 ({fg['clasificacion']})" if fg else ""
    lines = []
    for t in CRYPTO:
        d = fetch_quote(t, "1mo")
        if d:
            lines.append(f"{arrow(d['d1'])} {d['nombre']}: {d['price']:,.0f} | semana {arrow(d['d5'])} | RSI {d['rsi']}")
    snap = "\n".join(lines)
    header = f"CRYPTO WEEKEND {datetime.now().strftime('%d/%m %H:%M')}\n{fg_txt}\n\n{snap}"
    safe_send(ALLOWED_USER_ID, header)
    if signals:
        for s in signals:
            send_signal(ALLOWED_USER_ID, s)
            time.sleep(2)


def job_check_alerts():
    """Cada 5 min — Verifica alertas de precio."""
    for chat_id, alert_list in ALERTS.items():
        for alert in alert_list:
            if alert["triggered"]:
                continue
            try:
                d = fetch_quote(alert["ticker"], "1mo")
                if not d:
                    continue
                triggered = False
                if "sube" in alert["direction"] and d["price"] >= alert["price"]:
                    triggered = True
                elif "baja" in alert["direction"] and d["price"] <= alert["price"]:
                    triggered = True
                if triggered:
                    alert["triggered"] = True
                    safe_send(chat_id, f"ALERTA ACTIVADA\n{alert['nombre']} ({alert['ticker']}) ha {alert['direction']} {alert['price']}\nPrecio actual: {d['price']}")
            except Exception as e:
                log.warning(f"Alert check: {e}")


def job_anomalias_scanner():
    """Cada 2h dias laborables — Anomalias volumen."""
    if not es_dia_laborable():
        return
    todos = US_STOCKS + EU_STOCKS[:10] + ETFS_ESPECIALES
    anomalias = scan_anomalias(todos)
    if not anomalias:
        return
    top = anomalias[:4]
    rows = [f"{a['nombre']} ({a['ticker']}): vol {a['vol_rel']}x | hoy {a['d1']:+.2f}%" for a in top]
    bloque = "\n".join(rows)
    texto = ask_ai(f"Anomalias volumen:\n{bloque}\n\nHay noticia detras? Como operar.")
    safe_send(ALLOWED_USER_ID, f"ANOMALIA VOLUMEN {datetime.now().strftime('%H:%M')}\n\n{bloque}\n\n{texto}")


def job_noticias_impacto():
    """Cada 3h — Noticias alto impacto."""
    noticias = get_noticias_impacto()
    if not noticias:
        return
    lines = [f"- {n['title']}" for n in noticias[:6]]
    bloque = "\n".join(lines)
    prompt = f"Noticias impacto:\n{bloque}\n\nHay algo importante que afecte a bolsa ahora mismo?\nSi SI: que y como operar.\nSi NO: responde solo SIN NOVEDAD."
    texto = ask_ai(prompt)
    if "SIN NOVEDAD" in texto.upper():
        return
    safe_send(ALLOWED_USER_ID, f"NOTICIA IMPACTO {datetime.now().strftime('%H:%M')}\n\n{texto}")


def job_bull_detector():
    """Cada 4h — Bull runs."""
    senales = bull_detector()
    if not senales:
        return
    lines = []
    for s in senales[:4]:
        ticker_txt = f" ({s['ticker']})" if "ticker" in s else ""
        lines.append(f"[{s['tipo']}] {s['nombre']}{ticker_txt}: {s['detalle']}")
    bloque = "\n".join(lines)
    texto = ask_ai(f"Bull runs:\n{bloque}\n\nEs momento de entrar? ETF o accion concreta. Entrada y stop.")
    safe_send(ALLOWED_USER_ID, f"BULL DETECTOR {datetime.now().strftime('%H:%M')}\n\n{bloque}\n\n{texto}")


def job_plan_semana():
    """Lunes 8:00 — Plan de la semana."""
    if datetime.now(MADRID).weekday() != 0:
        return
    eventos = get_economic_calendar()
    cal_txt = "\n".join(f"- {e.get('title','')} ({e.get('date','')[:10]})" for e in eventos[:8]) or "Sin eventos clave"
    btc = fetch_quote("BTC-USD", "1mo")
    btc_txt = f"BTC: {btc['price']:,.0f} ({btc['d5']:+.1f}% semana pasada)" if btc else ""
    spx = fetch_quote("^GSPC", "1mo")
    spx_txt = f"S&P500: {spx['price']:,.0f} | RSI {spx['rsi']}" if spx else ""
    prompt = (f"Plan semana {datetime.now().strftime('%d/%m')}:\n"
              f"Eventos clave:\n{cal_txt}\n\n"
              f"Contexto mercado: {spx_txt} | {btc_txt}\n\n"
              "1. Los 3 eventos mas importantes y su impacto esperado\n"
              "2. Sectores a vigilar esta semana\n"
              "3. Niveles clave S&P500 y BTC\n"
              "4. Sesgo del mercado: alcista, bajista o lateral")
    texto = ask_ai(prompt)
    safe_send(ALLOWED_USER_ID, f"PLAN SEMANA {datetime.now().strftime('%d/%m')}\n\n{cal_txt}\n\n{texto}")


def job_resumen_domingo():
    """Domingo 20:00 — Resumen semanal crypto."""
    if datetime.now(MADRID).weekday() != 6:
        return
    btc = fetch_quote("BTC-USD", "1mo")
    eth = fetch_quote("ETH-USD", "1mo")
    sol = fetch_quote("SOL-USD", "1mo")
    fg  = get_fear_greed()
    dom = get_btc_dominance()
    deriv = get_binance_derivatives("BTCUSDT")
    lines = [f"RESUMEN SEMANAL CRYPTO {datetime.now().strftime('%d/%m')}"]
    if btc:
        lines.append(f"Bitcoin:  {btc['price']:,.0f} | semana {btc['d5']:+.1f}% | RSI {btc['rsi']}")
    if eth:
        lines.append(f"Ethereum: {eth['price']:,.0f} | semana {eth['d5']:+.1f}% | RSI {eth['rsi']}")
    if sol:
        lines.append(f"Solana:   {sol['price']:,.0f} | semana {sol['d5']:+.1f}% | RSI {sol['rsi']}")
    if fg:
        lines.append(f"Fear&Greed: {fg['valor']}/100 ({fg['clasificacion']})")
    if dom:
        lines.append(f"BTC Dominance: {dom['btc_dom']}%")
    if "funding" in deriv:
        lines.append(f"Funding Rate BTC: {deriv['funding']['valor']:+.4f}%")
    snap = "\n".join(lines)
    prompt = (f"Resumen semanal crypto:\n{snap}\n\n"
              "1. Como ha ido la semana para BTC y altcoins\n"
              "2. Que dice el Fear and Greed sobre el sentimiento\n"
              "3. Perspectiva para la proxima semana\n"
              "4. Nivel clave a vigilar en BTC")
    texto = ask_ai(prompt)
    safe_send(ALLOWED_USER_ID, f"{snap}\n\n{texto}")


def job_alerta_funding():
    """Cada hora — Alerta si funding rate extremo."""
    deriv = get_binance_derivatives("BTCUSDT")
    if "funding" not in deriv:
        return
    fr = deriv["funding"]["valor"]
    if fr > 0.05:
        safe_send(ALLOWED_USER_ID,
            f"ALERTA FUNDING RATE BTC\n"
            f"Funding: {fr:+.4f}% (muy alto)\n"
            f"Longs pagando demasiado — posible long squeeze inminente\n"
            f"Considera reducir posiciones largas")
    elif fr < -0.02:
        safe_send(ALLOWED_USER_ID,
            f"ALERTA FUNDING RATE BTC\n"
            f"Funding: {fr:+.4f}% (negativo)\n"
            f"Shorts pagando — posible rebote alcista\n"
            f"Zona de posible entrada contrarian")


def job_alerta_fear_greed():
    """Cada 6h — Alerta si Fear&Greed extremo."""
    fg = get_fear_greed()
    if not fg:
        return
    if fg["valor"] <= 15:
        safe_send(ALLOWED_USER_ID,
            f"ALERTA CAPITULACION\n"
            f"Fear&Greed: {fg['valor']}/100 ({fg['clasificacion']})\n"
            f"Miedo extremo historico — suelos importantes suelen formarse aqui\n"
            f"Revisar /btc para setup de entrada")
    elif fg["valor"] >= 85:
        safe_send(ALLOWED_USER_ID,
            f"ALERTA EUFORIA\n"
            f"Fear&Greed: {fg['valor']}/100 ({fg['clasificacion']})\n"
            f"Codicia extrema — zona de riesgo alto para nuevas entradas\n"
            f"Considera tomar ganancias parciales")


def job_alerta_vix():
    """Cada 2h dias laborables — Alerta si VIX alto."""
    if not es_dia_laborable():
        return
    d = fetch_quote("^VIX", "1mo")
    if not d:
        return
    if d["price"] > 35:
        safe_send(ALLOWED_USER_ID,
            f"ALERTA VIX CRITICO\n"
            f"VIX: {d['price']:.1f} (panico extremo)\n"
            f"Mercado en modo sell-off — evitar nuevas entradas\n"
            f"Historicamente estos niveles preceden rebotes fuertes")
    elif d["price"] > 25 and d["d1"] > 10:
        safe_send(ALLOWED_USER_ID,
            f"AVISO VIX ELEVADO\n"
            f"VIX: {d['price']:.1f} ({d['d1']:+.1f}% hoy)\n"
            f"Volatilidad subiendo — reducir tamaño de posiciones")


def job_sr_scanner():
    """Cada 2h dias laborables — S/R."""
    if not es_dia_laborable():
        return
    alerts = scan_sr_alerts(US_STOCKS[:8] + EU_STOCKS[:8])
    if not alerts:
        return
    bloque = "\n".join(alerts[:6])
    texto = ask_ai(f"S/R:\n{bloque}\n\nTop 2 mas interesantes y operativa.")
    safe_send(ALLOWED_USER_ID, f"Alerta S/R {datetime.now().strftime('%H:%M')}\n\n{bloque}\n\n{texto}")


def job_explosion_scanner():
    """Cada 3h dias laborables — Explosiones."""
    if not es_dia_laborable():
        return
    candidates = scan_explosions(US_STOCKS + EU_STOCKS)
    if not candidates:
        return
    rows = [f"{c['nombre']} ({c['ticker']}): semana {c['d5']:+.1f}% | vol {c['vol_rel']}x" for c in candidates[:3]]
    bloque = "\n".join(rows)
    texto = ask_ai(f"Explosiones:\n{bloque}\n\nAnalisis y niveles.")
    safe_send(ALLOWED_USER_ID, f"Explosiones {datetime.now().strftime('%H:%M')}\n\n{bloque}\n\n{texto}")


def job_metales_scanner():
    """Cada 4h — Metales."""
    data_ai = []
    for t, nom in METALES.items():
        d = fetch_quote(t, "3mo")
        if d:
            data_ai.append(f"{nom}: precio={d['price']}, RSI={d['rsi']}, 1d={d['d1']}%, S1={d['s1']}, R1={d['r1']}")
    if not data_ai:
        return
    texto = ask_ai("Metales:\n" + "\n".join(data_ai) + "\n\nHay senal clara? Si SI: cual, entrada, stop, objetivo. Si NO: responde solo SIN SENAL.")
    if "SIN SENAL" in texto.upper():
        return
    safe_send(ALLOWED_USER_ID, f"Alerta Metales {datetime.now().strftime('%H:%M')}\n\n{texto}")


if __name__ == "__main__":
    if ALLOWED_USER_ID:
        scheduler = BackgroundScheduler(timezone=MADRID)
        # Dias laborables
        scheduler.add_job(job_senales_eu,        "cron", hour=9,  minute=0)
        scheduler.add_job(job_senales_us,        "cron", hour=15, minute=0)
        scheduler.add_job(job_close_eu,          "cron", hour=17, minute=35)
        scheduler.add_job(job_close_us,          "cron", hour=22, minute=5)
        # Lunes plan semana
        scheduler.add_job(job_plan_semana,       "cron", hour=8,  minute=0)
        # Domingo resumen crypto
        scheduler.add_job(job_resumen_domingo,   "cron", hour=20, minute=0)
        # Fin de semana crypto
        scheduler.add_job(job_crypto_weekend,    "cron", hour=10, minute=0)
        # Alertas precio siempre
        scheduler.add_job(job_check_alerts,      "interval", minutes=5)
        # Alerta funding rate cada hora
        scheduler.add_job(job_alerta_funding,    "interval", hours=1)
        # Alerta Fear&Greed cada 6h
        scheduler.add_job(job_alerta_fear_greed, "interval", hours=6)
        # Alerta VIX cada 2h laborables
        scheduler.add_job(job_alerta_vix,        "interval", hours=2)
        # Scanners
        scheduler.add_job(job_anomalias_scanner, "interval", hours=2)
        scheduler.add_job(job_noticias_impacto,  "interval", hours=3)
        scheduler.add_job(job_bull_detector,     "interval", hours=4)
        scheduler.add_job(job_explosion_scanner, "interval", hours=3)
        scheduler.add_job(job_metales_scanner,   "interval", hours=4)
        scheduler.add_job(job_sr_scanner,        "interval", hours=2)
        scheduler.start()
        log.info("Jobs automaticos activados")
    log.info("Financial Bot arrancado - Version Completa v6")
    bot.infinity_polling(timeout=60, long_polling_timeout=60)
