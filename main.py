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
from groq import Groq
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from collections import defaultdict

TELEGRAM_TOKEN  = os.environ["TELEGRAM_TOKEN"]
GROQ_API_KEY    = os.environ["GROQ_API_KEY"]
ALLOWED_USER_ID = int(os.environ.get("ALLOWED_USER_ID", 0))
MADRID = pytz.timezone("Europe/Madrid")

SYSTEM = """Eres un analista financiero senior. Reglas:
- Responde SIEMPRE en espanol
- Sin markdown especial, texto plano
- Maximo 4 parrafos o listas cortas
- Da conclusiones concretas y accionables
- Precios exactos en tus recomendaciones"""

ai_client = Groq(api_key=GROQ_API_KEY)
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

def es_festivo_eeuu():
    """Detecta si hoy es festivo en EEUU."""
    try:
        hist = yf.Ticker("^GSPC").history(period="5d")
        if hist.empty:
            return True
        ultimo_dia = hist.index[-1].date()
        hoy = datetime.now(MADRID).date()
        return ultimo_dia < hoy
    except:
        return False


def es_mercado_us_cerrado():
    """Mercado EEUU cerrado: fin de semana o festivo."""
    if not es_dia_laborable():
        return True
    return es_festivo_eeuu()


def es_festivo_eu():
    """Detecta si hoy es festivo en Europa."""
    try:
        hist = yf.Ticker("^GDAXI").history(period="5d")
        if hist.empty:
            return True
        ultimo_dia = hist.index[-1].date()
        hoy = datetime.now(MADRID).date()
        return ultimo_dia < hoy
    except:
        return False


def es_dia_laborable():
    """Lunes=0 ... Viernes=4. Sabado=5, Domingo=6."""
    return datetime.now(MADRID).weekday() < 5

def es_mercado_eu_abierto():
    now = datetime.now(MADRID)
    return es_dia_laborable() and 9 <= now.hour < 18

def es_mercado_us_abierto():
    now = datetime.now(MADRID)
    return es_dia_laborable() and 15 <= now.hour < 23

# IBEX 35 completo
IBEX = [
    "SAN.MC","BBVA.MC","ITX.MC","REP.MC","TEF.MC","IBE.MC","ELE.MC","AMS.MC",
    "ACX.MC","ACS.MC","CABK.MC","CIE.MC","COL.MC","ENG.MC","FER.MC","GRF.MC",
    "IAG.MC","MAP.MC","MEL.MC","MRL.MC","NTGY.MC","PHM.MC","RED.MC",
    "VIS.MC","AENA.MC","ALM.MC","BKT.MC","CLNX.MC","LOG.MC","MTS.MC",
    "ROVI.MC","SAB.MC","UNI.MC",
]
CAC = ["OR.PA","BNP.PA","AIR.PA","MC.PA","TTE.PA","BN.PA",
       "SU.PA","AI.PA","DSY.PA","VIE.PA","SGO.PA","RMS.PA"]
DAX_S = ["BMW.DE","BAS.DE","DTE.DE","VOW3.DE","SIE.DE","SAP",
         "ALV.DE","MUV2.DE","DBK.DE","ADS.DE","MBG.DE","BAYN.DE"]
FTSE_S = ["HSBA.L","BP.L","SHEL.L","AZN.L","RIO.L",
          "ULVR.L","GSK.L","LSEG.L","LLOY.L"]
OTHER_EU = ["ASML","NESN.SW","NOVO-B.CO","HEIA.AS","PHIA.AS"]
EU_STOCKS = IBEX + CAC + DAX_S + FTSE_S + OTHER_EU

# US large caps
US_LARGE = [
    "AAPL","MSFT","NVDA","AMZN","GOOGL","META","TSLA","AMD","INTC","CRM","ORCL",
    "JPM","GS","BAC","V","MA","JNJ","UNH","PFE","XOM","CVX",
    "WMT","HD","MCD","KO","PEP","BA","CAT","NFLX","DIS",
]

# US mid caps y especulativas con potencial
US_MID_SPEC = [
    # Infraestructura IA — los proximos Marvell
    "MRVL","SMCI","ANET","PSTG","CRDO","CIEN","FORM","COHR","VIAV","LITE",
    "PENG","VRT","DELL","HPE","WDC","STX","NTAP",
    # IA software e infraestructura
    "PLTR","NET","SNOW","DDOG","MDB","CRWD","ZS","OKTA","PATH","AI",
    "GTLB","CFLT","MNDY","BILL","HUBS","SMAR","COUP",
    # Semiconductores ciclo
    "MCHP","ON","SWKS","QRVO","WOLF","MPWR","ALGM","ACLS","ONTO",
    # Fintech especulativo
    "COIN","HOOD","AFRM","UPST","SOFI","NU","PYPL","SQ",
    # EV y movilidad
    "RIVN","LCID","NIO","XPEV","LI",
    # Espacio y defensa
    "RKLB","ASTS","LUNR","ACHR","JOBY",
    # Biotech especulativo
    "MRNA","BNTX","CRSP","BEAM","NTLA","RXRX","EXAS","PCVX",
    # Nuclear y energia limpia
    "CCJ","NXE","SMR","OKLO","LEU","UUUU","DNN",
    # Otros especulativos con momentum
    "RBLX","HIMS","DUOL","APP","CELH","CVNA","SHOP","SPOT",
]

US_STOCKS = US_LARGE + US_MID_SPEC

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

# Universo ampliado para /infravaloradas
INFRA_NOMBRES = {
    # Infraestructura IA
    "MRVL":"Marvell Technology","SMCI":"Super Micro Computer","ANET":"Arista Networks",
    "PSTG":"Pure Storage","CRDO":"Credo Technology","CIEN":"Ciena",
    "FORM":"FormFactor","COHR":"Coherent","VRT":"Vertiv","DELL":"Dell",
    "HPE":"HP Enterprise","NTAP":"NetApp","WDC":"Western Digital",
    # IA software
    "PLTR":"Palantir","NET":"Cloudflare","SNOW":"Snowflake","DDOG":"Datadog",
    "MDB":"MongoDB","CRWD":"CrowdStrike","ZS":"Zscaler","OKTA":"Okta",
    "GTLB":"GitLab","CFLT":"Confluent","MNDY":"Monday.com",
    # Uranium / Nuclear
    "CCJ":"Cameco","NXE":"NexGen Energy","URA":"Uranium ETF",
    "DNN":"Denison Mines","UUUU":"Energy Fuels","SMR":"NuScale","OKLO":"Oklo",
    # Espacio
    "RKLB":"Rocket Lab","ASTS":"AST SpaceMobile","LUNR":"Intuitive Machines",
    # Biotech infravalorado
    "MRNA":"Moderna","BNTX":"BioNTech","CRSP":"CRISPR Therapeutics",
    "BEAM":"Beam Therapeutics","NTLA":"Intellia","RXRX":"Recursion",
    # Fintech
    "AFRM":"Affirm","UPST":"Upstart","SOFI":"SoFi","NU":"Nu Holdings","PYPL":"PayPal",
    # Semiconductores ciclo bajo
    "INTC":"Intel","MCHP":"Microchip Tech","ON":"ON Semiconductor","WOLF":"Wolfspeed",
    # Value en correccion
    "BABA":"Alibaba","JD":"JD.com","PDD":"PDD Holdings",
    "RBLX":"Roblox","SNAP":"Snap","PINS":"Pinterest",
    # Energia limpia
    "ENPH":"Enphase","SEDG":"SolarEdge","FSLR":"First Solar","RUN":"Sunrun",
    # EV
    "RIVN":"Rivian","LCID":"Lucid","NIO":"NIO","XPEV":"XPeng",
    # Otros
    "APP":"AppLovin","CELH":"Celsius","CVNA":"Carvana","HIMS":"Hims",
}

DEFI_CRYPTO = [
    "AAVE-USD","UNI-USD","MKR-USD","COMP-USD",
    "ARB-USD","OP-USD","MATIC-USD","LDO-USD",
]

INFRA_UNIVERSE = (
    list(INFRA_NOMBRES.keys()) +
    US_STOCKS +
    EU_STOCKS[:20]
)

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
    "https://feeds.bloomberg.com/markets/news.rss",
    "https://www.cnbc.com/id/100003114/device/rss/rss.html",
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

            # VWAP (precio ponderado por volumen - ultimos 20 dias)
            typical_price = (h + lo + c) / 3
            vwap = (typical_price * vol).tail(20).sum() / vol.tail(20).sum() if vol.tail(20).sum() > 0 else price
            sobre_vwap = price > vwap

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
                "vwap": round(vwap, 2),
                "sobre_vwap": sobre_vwap,
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
        if not isinstance(orders, list):
            raise ValueError("respuesta no es lista")
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

    # Inicializar fase por defecto
    score = 0
    fase = 8  # Ansiedad por defecto

    # ── LOGICA DIRECTA - distancia desde maximo es el factor principal ────────
    # Mapeo directo: donde estás en el ciclo depende de cuanto has caido desde el techo

    if dist_desde_maximo is not None:
        # Punto de partida basado en distancia desde maximo
        if dist_desde_maximo > -5:
            fase_base = 7   # Complacencia (cerca del techo, leve caida)
        elif dist_desde_maximo > -12:
            fase_base = 8   # Ansiedad (correccion del 5-12%)
        elif dist_desde_maximo > -25:
            fase_base = 9   # Negacion (caida 12-25%, la gente aguanta)
        elif dist_desde_maximo > -40:
            fase_base = 10  # Panico (caida 25-40%)
        elif dist_desde_maximo > -55:
            fase_base = 11  # Capitulacion (caida 40-55%)
        else:
            fase_base = 0   # Depresion (caida >55%)

        # Ajuste: si ha rebotado mucho desde minimos, puede estar saliendo del suelo
        if recuperacion_desde_minimo is not None and recuperacion_desde_minimo > 20:
            if fase_base in [10, 11, 0]:   # panico/capitulacion/depresion
                if recuperacion_desde_minimo > 40:
                    fase_base = 2   # Esperanza (rebote fuerte desde suelo)
                else:
                    fase_base = 1   # Incredulidad (rebote pero nadie lo cree)

        # Ajuste Fear&Greed (crypto)
        if fg is not None:
            if fg > 75 and fase_base <= 7:
                fase_base = min(fase_base + 1, 6)   # empuja hacia euforia
            elif fg < 25 and fase_base < 9:
                fase_base = max(fase_base, 9)        # confirma zona de miedo

        # Ajuste VIX (bolsa)
        if vix is not None:
            if vix > 30 and fase_base < 10:
                fase_base = max(fase_base, 10)       # VIX alto = panico
            elif vix < 15 and fase_base == 7:
                fase_base = 7                        # VIX bajo confirma complacencia

        # Ajuste: si tendencia alcista y cerca de maximos, puede ser creencia/emocion
        if dist_desde_maximo > -8 and tendencia_alcista and d_anual is not None:
            if d_anual > 30:
                fase_base = 5   # Emocion (año muy bueno, cerca del techo)
            elif d_anual > 15:
                fase_base = 4   # Creencia (año bueno, tendencia confirmada)

        fase = fase_base

    else:
        # Sin datos anuales: usar indicadores tecnicos clasicos
        score = 0
        if rsi > 70:   score += 3
        elif rsi > 60: score += 2
        elif rsi > 50: score += 1
        elif rsi < 35: score -= 3
        elif rsi < 45: score -= 1
        if tendencia_alcista:  score += 2
        elif not sobre_ema50:  score -= 2
        if fg:
            if fg > 70:   score += 2
            elif fg < 30: score -= 2
        if vix:
            if vix > 30: score -= 3
            elif vix < 15: score += 1
        score = max(-8, min(8, score))
        if score >= 6:   fase = 6
        elif score >= 4: fase = 5
        elif score >= 2: fase = 4
        elif score >= 0: fase = 3
        elif score >= -2: fase = 8
        elif score >= -4: fase = 9
        elif score >= -6: fase = 10
        else:             fase = 11

    return {
        "fase_num": fase,
        "nombre": FASES[fase][1],
        "descripcion": FASES[fase][2],
        "color": FASES[fase][3],
        "emocion": FASES[fase][4],
        "score": score,
    }


def fetch_ema200_distance(ticker):
    """Distancia porcentual del precio actual a la EMA200 diaria."""
    try:
        hist = yf.Ticker(ticker).history(period="1y")
        if hist.empty or len(hist) < 50:
            return None
        c = hist["Close"]
        ema200 = c.ewm(span=200, adjust=False).mean().iloc[-1]
        price = c.iloc[-1]
        dist_pct = round((price - ema200) / ema200 * 100, 1)
        return {"ema200": round(ema200, 2), "dist_pct": dist_pct}
    except:
        return None


def calcular_indice_valor(ticker):
    """
    Indice 0-100 estilo FREDI: 100=barato (zona acumulacion), 0=caro (zona venta).
    Detecta automaticamente el tipo de activo (crypto/accion/indice) y usa
    6 componentes especificos por tipo, cada uno puntuado 0-10.
    """
    es_crypto = ticker in COINGECKO_IDS or "-USD" in ticker
    es_indice = ticker.startswith("^")

    d = fetch_quote(ticker, "3mo")
    if not d:
        return None

    ema200_data = fetch_ema200_distance(ticker)
    componentes = {}

    # RSI 14d diario (comun a todos)
    rsi = d["rsi"]
    if rsi < 25:   pts_rsi = 10
    elif rsi < 35: pts_rsi = 8
    elif rsi < 45: pts_rsi = 6
    elif rsi < 55: pts_rsi = 5
    elif rsi < 65: pts_rsi = 3
    elif rsi < 75: pts_rsi = 2
    else:          pts_rsi = 1
    componentes["RSI 14d"] = {"valor": rsi, "puntos": pts_rsi}

    # EMA200 distancia (comun a todos) — lejos por debajo = barato
    if ema200_data:
        dist = ema200_data["dist_pct"]
        if dist < -30:   pts_ema = 10
        elif dist < -20: pts_ema = 9
        elif dist < -10: pts_ema = 7
        elif dist < 0:   pts_ema = 5
        elif dist < 10:  pts_ema = 3
        elif dist < 20:  pts_ema = 2
        else:            pts_ema = 1
        componentes["EMA200"] = {"valor": f"{dist:+.1f}%", "puntos": pts_ema}
    else:
        pts_ema = 5
        componentes["EMA200"] = {"valor": "N/D", "puntos": 5}

    # Volumen — alto volumen en caida fuerte = posible capitulacion
    vol_rel = d["vol_rel"]
    d1 = d["d1"]
    if vol_rel > 1.5 and d1 < -2:
        pts_vol = 9
    elif vol_rel > 1.2 and d1 < 0:
        pts_vol = 7
    elif vol_rel < 0.7:
        pts_vol = 5
    else:
        pts_vol = 4
    componentes["Volumen"] = {"valor": f"{vol_rel}x", "puntos": pts_vol}

    if es_crypto:
        # Fear & Greed
        fg = get_fear_greed()
        if fg:
            fgv = fg["valor"]
            if fgv < 20:   pts_fg = 10
            elif fgv < 30: pts_fg = 8
            elif fgv < 45: pts_fg = 6
            elif fgv < 55: pts_fg = 5
            elif fgv < 70: pts_fg = 3
            elif fgv < 80: pts_fg = 2
            else:          pts_fg = 1
            componentes["Fear&Greed"] = {"valor": fgv, "puntos": pts_fg}
        else:
            pts_fg = 5
            componentes["Fear&Greed"] = {"valor": "N/D", "puntos": 5}

        # Funding rate
        symbol_map = {"BTC-USD":"BTCUSDT","ETH-USD":"ETHUSDT","SOL-USD":"SOLUSDT","BNB-USD":"BNBUSDT"}
        binance_sym = symbol_map.get(ticker, "BTCUSDT")
        deriv = get_binance_derivatives(binance_sym)
        funding = deriv.get("funding", {}).get("valor") if deriv else None
        if funding is not None:
            if funding < -0.02:   pts_fund = 10
            elif funding < 0:     pts_fund = 7
            elif funding < 0.01:  pts_fund = 5
            elif funding < 0.03:  pts_fund = 3
            elif funding < 0.05:  pts_fund = 2
            else:                 pts_fund = 1
            componentes["Funding Rate"] = {"valor": f"{funding:+.4f}%", "puntos": pts_fund}
        else:
            pts_fund = 5
            componentes["Funding Rate"] = {"valor": "N/D", "puntos": 5}

        # DXY — dolar fuerte suele coincidir con suelos de crypto
        dxy_d = fetch_quote("DX-Y.NYB", "1mo")
        if dxy_d:
            dxy_val = dxy_d["price"]
            if dxy_val > 105:   pts_dxy = 8
            elif dxy_val > 102: pts_dxy = 6
            elif dxy_val > 99:  pts_dxy = 5
            elif dxy_val > 96:  pts_dxy = 3
            else:               pts_dxy = 2
            componentes["DXY"] = {"valor": dxy_val, "puntos": pts_dxy}
        else:
            pts_dxy = 5
            componentes["DXY"] = {"valor": "N/D", "puntos": 5}

        total_pts = pts_rsi + pts_ema + pts_vol + pts_fg + pts_fund + pts_dxy

    else:
        # ACCION o INDICE: VIX, RSI semanal, distancia maximo 52 semanas
        vix_d = fetch_quote("^VIX", "1mo")
        if vix_d:
            vix_val = vix_d["price"]
            if vix_val > 35:   pts_vix = 10
            elif vix_val > 28: pts_vix = 8
            elif vix_val > 22: pts_vix = 6
            elif vix_val > 18: pts_vix = 4
            elif vix_val > 14: pts_vix = 3
            else:              pts_vix = 2
            componentes["VIX"] = {"valor": vix_val, "puntos": pts_vix}
        else:
            pts_vix = 5
            componentes["VIX"] = {"valor": "N/D", "puntos": 5}

        rsi_w = fetch_weekly_rsi(ticker)
        if rsi_w is not None:
            if rsi_w < 30:   pts_rsiw = 10
            elif rsi_w < 40: pts_rsiw = 8
            elif rsi_w < 50: pts_rsiw = 6
            elif rsi_w < 60: pts_rsiw = 4
            elif rsi_w < 70: pts_rsiw = 2
            else:            pts_rsiw = 1
            componentes["RSI Semanal"] = {"valor": rsi_w, "puntos": pts_rsiw}
        else:
            pts_rsiw = 5
            componentes["RSI Semanal"] = {"valor": "N/D", "puntos": 5}

        dist_52 = round((d["price"] - d["hi52"]) / d["hi52"] * 100, 1)
        if dist_52 < -40:   pts_52 = 10
        elif dist_52 < -25: pts_52 = 8
        elif dist_52 < -15: pts_52 = 6
        elif dist_52 < -5:  pts_52 = 4
        elif dist_52 < -2:  pts_52 = 3
        else:               pts_52 = 1
        componentes["Dist. Max 52s"] = {"valor": f"{dist_52:+.1f}%", "puntos": pts_52}

        total_pts = pts_rsi + pts_ema + pts_vol + pts_vix + pts_rsiw + pts_52

    score_final = round(total_pts / 60 * 100)

    if score_final >= 80:
        zona = "BARATO — ACUMULACION FUERTE"
    elif score_final >= 65:
        zona = "BARATO — BUENA ZONA DE COMPRA"
    elif score_final >= 45:
        zona = "NEUTRAL"
    elif score_final >= 30:
        zona = "CARO — PRECAUCION"
    else:
        zona = "MUY CARO — ZONA DE VENTA"

    return {
        "ticker": ticker,
        "nombre": nombre(ticker),
        "price": d["price"],
        "score": score_final,
        "zona": zona,
        "componentes": componentes,
        "tipo": "crypto" if es_crypto else ("indice" if es_indice else "accion"),
    }


def generate_valor_gauge(resultado):
    """Genera gauge visual con velocimetro + barras de componentes estilo FREDI."""
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    import numpy as np

    componentes = resultado.get("componentes", {})
    n_comp = len(componentes)

    # Layout: velocimetro arriba, barras abajo
    fig = plt.figure(figsize=(10, 7 + n_comp * 0.5))
    fig.patch.set_facecolor('#0d1117')

    # Velocimetro (parte superior)
    ax = fig.add_axes([0.05, 0.45, 0.90, 0.50], projection='polar')
    ax.set_facecolor('#0d1117')

    n_seg = 100
    theta = np.linspace(np.pi, 0, n_seg + 1)
    for i in range(n_seg):
        if i < 30:    color = '#FF3333'
        elif i < 45:  color = '#FF7700'
        elif i < 65:  color = '#FFCC00'
        elif i < 80:  color = '#99DD00'
        else:         color = '#00CC44'
        ax.barh(1, theta[i] - theta[i+1], left=theta[i+1], height=0.4, color=color, edgecolor='none')

    score = resultado['score']
    angle = np.pi - (score / 100 * np.pi)
    ax.plot([angle, angle], [0, 1.08], color='white', linewidth=5, zorder=5)
    ax.plot(angle, 0, 'o', color='white', markersize=20, zorder=6)
    ax.plot(angle, 0, 'o', color='#0d1117', markersize=10, zorder=7)

    ax.set_ylim(0, 1.35)
    ax.set_theta_zero_location('E')
    ax.set_theta_direction(1)
    ax.set_thetamin(0)
    ax.set_thetamax(180)
    ax.set_xticks([np.pi, 3*np.pi/4, np.pi/2, np.pi/4, 0])
    ax.set_xticklabels(['0\nCARO', '25', '50\nNEUTRAL', '75', '100\nBARATÉ'],
                        color='white', fontsize=9, fontweight='bold')
    ax.set_yticks([])
    ax.spines['polar'].set_visible(False)
    ax.grid(False)

    # Score central
    fig.text(0.5, 0.47, f"{score}/100", ha='center', va='center',
             fontsize=32, color='white', fontweight='bold')

    # Zona
    zona_color = '#FF3333' if score < 30 else '#FF7700' if score < 45 else '#FFCC00' if score < 65 else '#99DD00' if score < 80 else '#00CC44'
    fig.text(0.5, 0.42, resultado['zona'], ha='center', va='center',
             fontsize=11, color=zona_color, fontweight='bold')

    # Titulo
    fig.text(0.5, 0.97, f"{resultado['nombre']} ({resultado['ticker']})  —  {resultado['price']}",
             ha='center', va='top', fontsize=13, color='white', fontweight='bold')

    # Barras de componentes (parte inferior)
    ax2 = fig.add_axes([0.05, 0.02, 0.90, 0.38])
    ax2.set_facecolor('#0d1117')
    ax2.set_xlim(0, 10)
    ax2.set_ylim(-0.5, n_comp - 0.5)
    ax2.axis('off')

    for idx, (nombre_c, datos) in enumerate(reversed(list(componentes.items()))):
        pts = datos['puntos']
        val = datos['valor']
        y = idx

        # Fondo barra
        ax2.barh(y, 10, height=0.55, left=0, color='#1a1a2e', zorder=1)

        # Barra coloreada
        bar_color = '#FF3333' if pts <= 3 else '#FF7700' if pts <= 5 else '#FFCC00' if pts <= 7 else '#00CC44'
        ax2.barh(y, pts, height=0.55, left=0, color=bar_color, zorder=2)

        # Nombre componente
        ax2.text(-0.1, y, nombre_c, va='center', ha='right',
                color='#AAAAAA', fontsize=9, fontweight='bold')

        # Valor
        ax2.text(pts + 0.1, y, str(val), va='center', ha='left',
                color='white', fontsize=8)

        # Puntuacion
        ax2.text(10.1, y, f"{pts}/10", va='center', ha='left',
                color=bar_color, fontsize=9, fontweight='bold')

    ax2.set_xlim(-3, 11)
    fig.text(0.5, 0.40, 'COMPONENTES', ha='center',
             fontsize=9, color='#666666', fontweight='bold')

    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=120, facecolor='#0d1117', bbox_inches='tight')
    plt.close()
    buf.seek(0)
    return buf


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


def fetch_intraday(ticker):
    """Datos intradía 5min para señales rápidas."""
    try:
        hist = yf.Ticker(ticker).history(period="5d", interval="15m")
        if hist.empty or len(hist) < 20:
            return None
        c, h, lo, vol = hist["Close"], hist["High"], hist["Low"], hist["Volume"]
        price = c.iloc[-1]
        d1 = (price - c.iloc[-2]) / c.iloc[-2] * 100 if len(c) > 1 else 0
        rsi = calc_rsi(c).iloc[-1] if len(c) >= 14 else 50
        macd_l, macd_s = calc_macd(c)
        macd_cross_up = (len(macd_l) >= 2 and
                         macd_l.iloc[-1] > macd_s.iloc[-1] and
                         macd_l.iloc[-2] <= macd_s.iloc[-2])
        avg_vol = vol.tail(20).mean() if len(vol) >= 20 else vol.mean()
        vol_rel = vol.iloc[-1] / avg_vol if avg_vol > 0 else 1.0
        typical_price = (h + lo + c) / 3
        vwap = (typical_price * vol).tail(20).sum() / vol.tail(20).sum() if vol.tail(20).sum() > 0 else price
        ema9 = c.ewm(span=9, adjust=False).mean().iloc[-1]
        ema21 = c.ewm(span=21, adjust=False).mean().iloc[-1]
        high_low = h - lo
        high_close = (h - c.shift()).abs()
        low_close = (lo - c.shift()).abs()
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        atr = tr.ewm(span=14, adjust=False).mean().iloc[-1]
        s1 = round(2 * (h.iloc[-1] + lo.iloc[-1] + c.iloc[-1]) / 3 - h.iloc[-1], 2)
        r1 = round(2 * (h.iloc[-1] + lo.iloc[-1] + c.iloc[-1]) / 3 - lo.iloc[-1], 2)
        return {
            "ticker": ticker,
            "nombre": nombre(ticker),
            "price": round(price, 2),
            "d1": round(d1, 2),
            "rsi": round(rsi, 1),
            "macd_cross_up": macd_cross_up,
            "vol_rel": round(vol_rel, 2),
            "vwap": round(vwap, 2),
            "sobre_vwap": price > vwap,
            "ema9": round(ema9, 2),
            "ema21": round(ema21, 2),
            "tendencia_alcista": ema9 > ema21,
            "atr": round(atr, 2),
            "s1": s1, "r1": r1,
        }
    except Exception as e:
        log.warning(f"fetch_intraday {ticker}: {e}")
        return None


def get_intraday_signals(stocks, n=4):
    """Señales intradía basadas en 15min."""
    candidatos = []
    for t in stocks:
        d = fetch_intraday(t)
        if not d:
            continue
        if d["vol_rel"] < 1.2:
            continue
        if d["rsi"] > 70 or d["rsi"] < 25:
            continue
        score = 0
        motivos = []
        # VWAP — clave para intradía
        if d["sobre_vwap"]:
            score += 4
            motivos.append(f"Precio sobre VWAP {d['vwap']} — momentum institucional")
        # RSI
        if 40 <= d["rsi"] <= 60:
            score += 3
            motivos.append(f"RSI {d['rsi']} zona momentum")
        elif d["rsi"] < 40:
            score += 2
            motivos.append(f"RSI {d['rsi']} rebote potencial")
        # MACD
        if d["macd_cross_up"]:
            score += 4
            motivos.append("MACD cruce alcista 15min")
        # Volumen
        if d["vol_rel"] >= 2.0:
            score += 3
            motivos.append(f"Volumen {d['vol_rel']}x — aceleracion")
        elif d["vol_rel"] >= 1.5:
            score += 2
            motivos.append(f"Volumen {d['vol_rel']}x elevado")
        elif d["vol_rel"] >= 1.2:
            score += 1
        # EMA9 > EMA21
        if d["tendencia_alcista"]:
            score += 2
            motivos.append("EMA9 > EMA21 tendencia alcista")
        # Momentum del dia
        if d["d1"] >= 1.5:
            score += 2
            motivos.append(f"Hoy +{d['d1']}% momentum")
        elif d["d1"] >= 0.5:
            score += 1
        if score < 10:
            continue
        entry = d["price"]
        atr = d["atr"]
        stop = round(entry - atr * 1.0, 2)
        risk = entry - stop
        if risk <= 0:
            continue
        tp1 = round(entry + risk * 1.0, 2)
        tp2 = round(entry + risk * 2.0, 2)
        rr = round((tp1 - entry) / risk, 2)
        candidatos.append({
            **d, "score": score, "motivos": motivos,
            "direction": "COMPRAR", "modo": "intraday",
            "entry": entry, "stop": stop, "tp1": tp1, "tp2": tp2, "rr": rr,
        })
    candidatos.sort(key=lambda x: x["score"], reverse=True)
    return candidatos[:n]


def fetch_fundamentales(ticker):
    """Obtiene datos fundamentales via yfinance."""
    try:
        tk = yf.Ticker(ticker)
        info = tk.info
        if not info:
            return None

        # Precio actual en tiempo real via fast_info
        try:
            fi = tk.fast_info
            price = fi.last_price or fi.regular_market_price
        except:
            price = info.get("currentPrice") or info.get("regularMarketPrice", 0)

        # Valoracion
        pe      = info.get("trailingPE")
        pe_fwd  = info.get("forwardPE")
        peg     = info.get("pegRatio")
        pb      = info.get("priceToBook")
        ps      = info.get("priceToSalesTrailing12Months")
        ev_ebitda = info.get("enterpriseToEbitda")

        # Salud financiera
        deuda_total  = info.get("totalDebt", 0)
        caja         = info.get("totalCash", 0)
        ebitda       = info.get("ebitda", 0)
        current_ratio = info.get("currentRatio")
        deuda_neta   = (deuda_total - caja) if deuda_total and caja else None
        deuda_ebitda = round(deuda_neta / ebitda, 2) if (deuda_neta and ebitda and ebitda > 0) else None

        # Rentabilidad
        margen_bruto   = info.get("grossMargins")
        margen_neto    = info.get("profitMargins")
        margen_op      = info.get("operatingMargins")
        roe            = info.get("returnOnEquity")
        roa            = info.get("returnOnAssets")

        # Crecimiento
        rev_growth     = info.get("revenueGrowth")
        earn_growth    = info.get("earningsGrowth")
        rev_ttm        = info.get("totalRevenue", 0)
        fcf            = info.get("freeCashflow", 0)

        # Market cap y sector
        mktcap   = info.get("marketCap", 0)
        sector   = info.get("sector", "N/D")
        industry = info.get("industry", "N/D")
        nombre_e = info.get("longName") or info.get("shortName", ticker)

        # Dividendo — normalizar a decimal (0.045 = 4.5%)
        div_yield_raw = info.get("dividendYield")
        if div_yield_raw and div_yield_raw > 1:
            div_yield = div_yield_raw / 100  # ya venía en porcentaje
        else:
            div_yield = div_yield_raw

        # Insider ownership
        insider_pct = info.get("heldPercentInsiders")

        # Historico financiero (ultimos 4 años)
        try:
            financials = tk.financials
            rev_hist = {}
            if not financials.empty and "Total Revenue" in financials.index:
                for col in financials.columns[:4]:
                    year = col.year
                    rev_hist[year] = financials.loc["Total Revenue", col]
        except:
            rev_hist = {}

        return {
            "ticker": ticker, "nombre": nombre_e, "price": price,
            "sector": sector, "industry": industry,
            "pe": pe, "pe_fwd": pe_fwd, "peg": peg, "pb": pb, "ps": ps,
            "ev_ebitda": ev_ebitda,
            "deuda_neta": deuda_neta, "deuda_ebitda": deuda_ebitda,
            "caja": caja, "current_ratio": current_ratio,
            "margen_bruto": margen_bruto, "margen_neto": margen_neto,
            "margen_op": margen_op, "roe": roe, "roa": roa,
            "rev_growth": rev_growth, "earn_growth": earn_growth,
            "rev_ttm": rev_ttm, "fcf": fcf, "mktcap": mktcap,
            "div_yield": div_yield, "insider_pct": insider_pct,
            "rev_hist": rev_hist,
        }
    except Exception as e:
        log.warning(f"fetch_fundamentales {ticker}: {e}")
        return None


def detectar_tipo_empresa(f):
    """Detecta el tipo de empresa para ajustar la valoracion."""
    sector = (f.get("sector") or "").lower()
    industry = (f.get("industry") or "").lower()
    rev_growth = f.get("rev_growth") or 0
    fcf = f.get("fcf") or 0
    div_yield = f.get("div_yield") or 0
    margen_neto = f.get("margen_neto") or 0

    if any(s in sector for s in ["utility", "utilities"]):
        return "utility"
    if any(s in sector for s in ["basic material", "mining", "oil", "energy"]):
        return "materials"
    if any(s in industry for s in ["biotech", "pharmaceutical", "drug"]):
        return "biotech"
    if rev_growth > 0.30 and fcf < 0:
        return "growth"
    if div_yield > 0.03:
        return "income"
    if any(s in sector for s in ["technology", "tech"]):
        return "tech"
    return "value"


def estimar_precio_por_tipo(f, tipo, price):
    """Estimacion de precio segun tipo de empresa."""
    rev_growth = f.get("rev_growth") or 0.05
    rev_ttm = f.get("rev_ttm") or 0
    mktcap = f.get("mktcap") or 0
    fcf = f.get("fcf") or 0
    div_yield = f.get("div_yield") or 0
    pe_fwd = f.get("pe_fwd") or f.get("pe") or 20
    earn_growth = f.get("earn_growth") or rev_growth

    precio_justo = None
    est_1y = None
    est_3y = None
    metodo = ""

    if tipo == "growth" and rev_ttm > 0 and mktcap > 0:
        # Valoracion por multiplo de ventas (P/S)
        ps_actual = mktcap / rev_ttm
        ps_objetivo = min(ps_actual * 0.8, 15)  # descuento al actual
        rev_1y = rev_ttm * (1 + rev_growth)
        rev_3y = rev_ttm * (1 + rev_growth) ** 3
        precio_justo = round(price * (ps_objetivo / ps_actual), 2)
        est_1y = round(price * (1 + rev_growth * 0.7), 2)
        est_3y = round(price * (1 + rev_growth * 0.5) ** 3, 2)
        metodo = f"P/S x{ps_objetivo:.1f} (empresa crecimiento, FCF negativo)"

    elif tipo == "utility" and div_yield > 0:
        # Valoracion por yield objetivo
        yield_objetivo = 0.045  # 4.5% yield objetivo para utilities
        div_anual = price * div_yield  # dividendo anual en USD
        precio_justo = round(div_anual / yield_objetivo, 2)
        crecimiento_div = 0.03
        est_1y = round(precio_justo * (1 + crecimiento_div), 2)
        est_3y = round(precio_justo * (1 + crecimiento_div) ** 3, 2)
        metodo = f"Yield objetivo 4.5% (utility) | Div anual: {div_anual:.2f} USD"

    elif tipo == "materials" and rev_ttm > 0 and mktcap > 0:
        # Valoracion por EV/Ingresos para materials
        ev_rev = mktcap / rev_ttm
        ev_rev_objetivo = max(ev_rev * 0.9, 2)
        precio_justo = round(price * (ev_rev_objetivo / ev_rev), 2)
        est_1y = round(price * (1 + max(rev_growth, 0.10)), 2)
        est_3y = round(price * (1 + max(rev_growth * 0.6, 0.08)) ** 3, 2)
        metodo = f"EV/Ingresos ajustado ciclo commodities"

    elif tipo == "biotech":
        # Biotech: muy especulativo, usamos crecimiento de ingresos con descuento
        est_1y = round(price * (1 + max(rev_growth * 0.5, 0.05)), 2)
        est_3y = round(price * (1 + max(rev_growth * 0.4, 0.08)) ** 3, 2)
        precio_justo = round(price * 0.85, 2)
        metodo = "Especulativo (biotech sin beneficios)"

    elif fcf > 0 and mktcap > 0:
        # DCF clasico para empresas con FCF positivo
        tasa_desc = 0.10
        tasa_crec_fcf = min(max(earn_growth, 0.03), 0.25)
        fcf_yield = fcf / mktcap
        precio_justo = round(price * (fcf_yield + tasa_crec_fcf) / tasa_desc, 2)
        est_1y = round(price * (1 + max(earn_growth, 0.05)), 2)
        est_3y = round(price * (1 + max(earn_growth * 0.7, 0.05)) ** 3, 2)
        metodo = "DCF (Free Cash Flow)"

    elif pe_fwd and pe_fwd > 0:
        # Valoracion por PER forward
        pe_objetivo = min(pe_fwd * 0.9, 25)
        precio_justo = round(price * (pe_objetivo / pe_fwd), 2)
        est_1y = round(price * (1 + max(earn_growth, 0.05)), 2)
        est_3y = round(price * (1 + max(earn_growth * 0.7, 0.05)) ** 3, 2)
        metodo = f"PER forward {pe_fwd:.1f}x"

    return precio_justo, est_1y, est_3y, metodo


def buscar_noticias_ma(ticker, nombre_empresa):
    """Busca noticias de M&A para el ticker."""
    try:
        terminos = f"{nombre_empresa} acquisition merger buyout takeover 2026"
        url = f"https://news.google.com/rss/search?q={terminos}&hl=en&gl=US&ceid=US:en"
        feed = feedparser.parse(url)
        noticias_ma = []
        palabras_clave = ["acqui", "merger", "buyout", "takeover", "deal", "bid", "purchase"]
        for entry in feed.entries[:10]:
            titulo = entry.get("title", "").lower()
            if any(p in titulo for p in palabras_clave):
                noticias_ma.append(entry.get("title", ""))
        return noticias_ma[:3]
    except:
        return []


def calcular_fundamental(ticker):
    """
    Puntuacion fundamental 0-100 con ajuste por tipo de empresa.
    5 categorias x 20 puntos = 100 total.
    """
    f = fetch_fundamentales(ticker)
    if not f:
        return None

    tipo = detectar_tipo_empresa(f)
    categorias = {}

    # 1. VALORACION (0-20) — ajustada por tipo
    pts_val = 10
    val_notas = [f"Tipo empresa: {tipo.upper()}"]

    if tipo == "growth":
        # Growth: P/S y crecimiento son mas importantes que P/E
        ps = f.get("ps") or 0
        if ps > 0:
            if ps < 5:    pts_val += 5; val_notas.append(f"P/S {ps:.1f}x barato para growth")
            elif ps < 15: pts_val += 2; val_notas.append(f"P/S {ps:.1f}x razonable")
            elif ps < 30: pts_val -= 1; val_notas.append(f"P/S {ps:.1f}x elevado")
            else:         pts_val -= 3; val_notas.append(f"P/S {ps:.1f}x muy caro")
        if f["rev_growth"] and f["rev_growth"] > 0.5:
            pts_val += 3; val_notas.append(f"Crecimiento {f['rev_growth']*100:.0f}% justifica premium")

    elif tipo == "utility":
        # Utility: dividendo y estabilidad son lo importante
        if f["div_yield"]:
            dy = f["div_yield"] * 100
            if dy > 5:    pts_val += 5; val_notas.append(f"Dividendo {dy:.1f}% excelente para utility")
            elif dy > 3:  pts_val += 3; val_notas.append(f"Dividendo {dy:.1f}% bueno")
            elif dy > 1:  pts_val += 1
        # Deuda alta es NORMAL en utilities — no penalizar
        val_notas.append("Deuda alta es estructural en utilities (no penaliza)")

    elif tipo == "materials":
        # Materials: EV/EBITDA y ciclo
        if f["ev_ebitda"]:
            if f["ev_ebitda"] < 8:   pts_val += 5; val_notas.append(f"EV/EBITDA {f['ev_ebitda']:.1f}x barato")
            elif f["ev_ebitda"] < 15: pts_val += 2; val_notas.append(f"EV/EBITDA {f['ev_ebitda']:.1f}x ok")
            else:                     pts_val -= 1; val_notas.append(f"EV/EBITDA {f['ev_ebitda']:.1f}x caro")
        val_notas.append("FCF negativo en expansion es normal en mining")

    elif tipo == "biotech":
        # Biotech: pipeline y caja
        caja_b = round(f["caja"] / 1e9, 1) if f["caja"] else 0
        if caja_b > 0.5: pts_val += 4; val_notas.append(f"Caja {caja_b}B USD — runway suficiente")
        val_notas.append("Valoracion especulativa — depende del pipeline")

    else:
        # Valoracion clasica P/E, PEG, EV/EBITDA
        if f["pe"]:
            if f["pe"] < 15:   pts_val += 4; val_notas.append(f"P/E {f['pe']:.1f} barato")
            elif f["pe"] < 25: pts_val += 2; val_notas.append(f"P/E {f['pe']:.1f} razonable")
            elif f["pe"] < 40: pts_val -= 1; val_notas.append(f"P/E {f['pe']:.1f} elevado")
            else:              pts_val -= 3; val_notas.append(f"P/E {f['pe']:.1f} muy caro")
        if f["peg"]:
            if f["peg"] < 1:   pts_val += 3; val_notas.append(f"PEG {f['peg']:.2f} infravalorado")
            elif f["peg"] < 2: pts_val += 1; val_notas.append(f"PEG {f['peg']:.2f} ok")
            else:              pts_val -= 2; val_notas.append(f"PEG {f['peg']:.2f} caro vs crecimiento")

    pts_val = max(0, min(20, pts_val))
    pe_txt = f"{f['pe']:.1f}" if f['pe'] else 'N/D'
    peg_txt = f"{f['peg']:.2f}" if f['peg'] else 'N/D'
    ev_txt = f"{f['ev_ebitda']:.1f}" if f['ev_ebitda'] else 'N/D'
    categorias["Valoracion"] = {
        "puntos": pts_val, "max": 20,
        "notas": val_notas,
        "valores": f"P/E:{pe_txt} PEG:{peg_txt} EV/EBITDA:{ev_txt}"
    }

    # 2. SALUD FINANCIERA (0-20) — ajustada por sector
    pts_sal = 10
    sal_notas = []

    if tipo == "utility":
        # Utilities: deuda alta es estructural, mirar cobertura de intereses
        if f["current_ratio"]:
            if f["current_ratio"] > 1: pts_sal += 3; sal_notas.append(f"Current ratio {f['current_ratio']:.1f} ok")
        caja_b = round(f["caja"] / 1e9, 1) if f["caja"] else 0
        sal_notas.append(f"Caja: {caja_b}B — utility con deuda estructural normal")
        pts_sal += 3  # bonus por ser modelo de negocio regulado y predecible
    else:
        if f["deuda_ebitda"] is not None:
            if f["deuda_ebitda"] < 0:   pts_sal += 5; sal_notas.append("Caja neta positiva")
            elif f["deuda_ebitda"] < 1: pts_sal += 4; sal_notas.append(f"Deuda/EBITDA {f['deuda_ebitda']}x muy baja")
            elif f["deuda_ebitda"] < 2: pts_sal += 2; sal_notas.append(f"Deuda/EBITDA {f['deuda_ebitda']}x saludable")
            elif f["deuda_ebitda"] < 4: pts_sal -= 1; sal_notas.append(f"Deuda/EBITDA {f['deuda_ebitda']}x moderada")
            elif tipo not in ["materials", "growth"]:
                pts_sal -= 3; sal_notas.append(f"Deuda/EBITDA {f['deuda_ebitda']}x alta")
        if f["current_ratio"]:
            if f["current_ratio"] > 2:  pts_sal += 3; sal_notas.append(f"Current ratio {f['current_ratio']:.1f} excelente")
            elif f["current_ratio"] > 1: pts_sal += 1
            else:                        pts_sal -= 2; sal_notas.append("Liquidez ajustada")

    caja_b = round(f["caja"] / 1e9, 1) if f["caja"] else 0
    if caja_b not in [str(x) for x in sal_notas]:
        sal_notas.append(f"Caja: {caja_b}B USD")
    pts_sal = max(0, min(20, pts_sal))
    cr_txt = f"{f['current_ratio']:.1f}" if f['current_ratio'] else 'N/D'
    categorias["Salud Financiera"] = {
        "puntos": pts_sal, "max": 20,
        "notas": sal_notas,
        "valores": f"Deuda/EBITDA:{f['deuda_ebitda']}x Current:{cr_txt}"
    }

    # 3. RENTABILIDAD (0-20) — ajustada
    pts_rent = 10
    rent_notas = []

    if tipo in ["growth", "biotech", "materials"] and (f["fcf"] or 0) < 0:
        # FCF negativo en estas empresas es normal si crecen fuerte
        pts_rent += 2
        rent_notas.append("FCF negativo aceptable en fase expansion")
    elif f["fcf"] and f["fcf"] > 0:
        fcf_b = round(f["fcf"] / 1e9, 1)
        pts_rent += 3; rent_notas.append(f"FCF positivo {fcf_b}B USD")
    elif f["fcf"] and f["fcf"] < 0 and tipo not in ["growth", "biotech", "materials"]:
        pts_rent -= 2; rent_notas.append("FCF negativo — preocupante")

    if f["margen_neto"]:
        mn = f["margen_neto"] * 100
        if mn > 25:   pts_rent += 4; rent_notas.append(f"Margen neto {mn:.1f}% excelente")
        elif mn > 15: pts_rent += 3; rent_notas.append(f"Margen neto {mn:.1f}% bueno")
        elif mn > 5:  pts_rent += 1; rent_notas.append(f"Margen neto {mn:.1f}% ok")
        elif mn > 0:  pass
        elif tipo not in ["growth", "biotech"]:
            pts_rent -= 2; rent_notas.append(f"Margen neto negativo {mn:.1f}%")

    if f["roe"]:
        roe = f["roe"] * 100
        if roe > 20:  pts_rent += 3; rent_notas.append(f"ROE {roe:.1f}% excelente")
        elif roe > 10: pts_rent += 1

    pts_rent = max(0, min(20, pts_rent))
    mn_txt = f"{f['margen_neto']*100:.1f}" if f['margen_neto'] else 'N/D'
    roe_txt = f"{f['roe']*100:.1f}" if f['roe'] else 'N/D'
    categorias["Rentabilidad"] = {
        "puntos": pts_rent, "max": 20,
        "notas": rent_notas,
        "valores": f"Margen neto:{mn_txt}% ROE:{roe_txt}%"
    }

    # 4. CRECIMIENTO (0-20)
    pts_crec = 10
    crec_notas = []
    if f["rev_growth"]:
        rg = f["rev_growth"] * 100
        if rg > 50:   pts_crec += 6; crec_notas.append(f"Ingresos +{rg:.0f}% YoY excepcional")
        elif rg > 30: pts_crec += 4; crec_notas.append(f"Ingresos +{rg:.0f}% YoY muy bueno")
        elif rg > 15: pts_crec += 2; crec_notas.append(f"Ingresos +{rg:.0f}% YoY bueno")
        elif rg > 5:  pts_crec += 1; crec_notas.append(f"Ingresos +{rg:.0f}% YoY moderado")
        elif rg > 0:  pass
        elif tipo == "utility": pass  # utilities crecen poco — normal
        else:         pts_crec -= 3; crec_notas.append(f"Ingresos {rg:.0f}% cayendo")
    if f["earn_growth"]:
        eg = f["earn_growth"] * 100
        if eg > 50:   pts_crec += 4; crec_notas.append(f"Beneficios +{eg:.0f}% YoY")
        elif eg > 20: pts_crec += 2
        elif eg > 5:  pts_crec += 1
        elif tipo not in ["growth", "biotech", "materials"]:
            pts_crec -= 1
    if f["rev_hist"] and len(f["rev_hist"]) >= 3:
        revs = sorted(f["rev_hist"].items())
        if revs[-1][1] > revs[0][1]:
            crec_notas.append("Crecimiento consistente multi-año")
            pts_crec += 2
    pts_crec = max(0, min(20, pts_crec))
    rg_txt = f"{f['rev_growth']*100:.0f}" if f['rev_growth'] else 'N/D'
    eg_txt = f"{f['earn_growth']*100:.0f}" if f['earn_growth'] else 'N/D'
    categorias["Crecimiento"] = {
        "puntos": pts_crec, "max": 20,
        "notas": crec_notas,
        "valores": f"Rev growth:{rg_txt}% Earn growth:{eg_txt}%"
    }

    # 5. POTENCIAL LARGO PLAZO (0-20)
    pts_lp = 10
    lp_notas = []

    # Factor M&A — buscar noticias
    noticias_ma = buscar_noticias_ma(ticker, f["nombre"])
    if noticias_ma:
        pts_lp += 4
        lp_notas.append(f"M&A potencial: {noticias_ma[0][:60]}")

    if f["insider_pct"]:
        ip = f["insider_pct"] * 100
        if ip > 10:  pts_lp += 3; lp_notas.append(f"Insiders {ip:.1f}% — directivos comprometidos")
        elif ip > 5: pts_lp += 1

    if f["margen_bruto"]:
        mb = f["margen_bruto"] * 100
        if mb > 60:  pts_lp += 4; lp_notas.append(f"Margen bruto {mb:.0f}% — moat fuerte")
        elif mb > 40: pts_lp += 2; lp_notas.append(f"Margen bruto {mb:.0f}%")
        elif mb > 20: pts_lp += 1

    if tipo == "utility":
        pts_lp += 2; lp_notas.append("Monopolio regulado — ingresos predecibles")
    elif tipo == "materials" and "rare" in (f.get("industry") or "").lower():
        pts_lp += 3; lp_notas.append("Materiales criticos — valor estrategico geopolitico")

    if f["div_yield"]:
        dy = f["div_yield"] * 100
        if dy > 3: pts_lp += 2; lp_notas.append(f"Dividendo {dy:.1f}%")

    pts_lp = max(0, min(20, pts_lp))
    mb_txt = f"{f['margen_bruto']*100:.0f}" if f['margen_bruto'] else 'N/D'
    ins_txt = f"{f['insider_pct']*100:.1f}" if f['insider_pct'] else 'N/D'
    categorias["Potencial LP"] = {
        "puntos": pts_lp, "max": 20,
        "notas": lp_notas,
        "valores": f"Margen bruto:{mb_txt}% Insider:{ins_txt}%"
    }

    total = sum(c["puntos"] for c in categorias.values())

    # Estimacion precio ajustada por tipo
    precio_justo, est_1y, est_3y, metodo_est = estimar_precio_por_tipo(f, tipo, f["price"])

    if total >= 80:    zona = "INVERSION EXCELENTE"
    elif total >= 65:  zona = "BUENA INVERSION"
    elif total >= 50:  zona = "INVERSION ACEPTABLE"
    elif total >= 35:  zona = "PRECAUCION"
    else:              zona = "EVITAR"

    return {
        "ticker": ticker, "nombre": f["nombre"], "price": f["price"],
        "sector": f["sector"], "mktcap": f["mktcap"],
        "tipo": tipo, "score": total, "zona": zona,
        "categorias": categorias, "fundamentales": f,
        "precio_justo": precio_justo, "est_1y": est_1y, "est_3y": est_3y,
        "metodo_est": metodo_est, "noticias_ma": noticias_ma,
    }


def generate_fundamental_chart(resultado):
    """Genera imagen de analisis fundamental con barras por categoria."""
    import matplotlib.pyplot as plt
    import numpy as np

    cats = resultado["categorias"]
    n = len(cats)

    fig = plt.figure(figsize=(10, 8 + n * 0.6))
    fig.patch.set_facecolor('#0d1117')

    # Velocimetro superior
    ax = fig.add_axes([0.05, 0.55, 0.90, 0.40], projection='polar')
    ax.set_facecolor('#0d1117')
    theta = np.linspace(np.pi, 0, 101)
    for i in range(100):
        if i < 35:    c = '#FF3333'
        elif i < 50:  c = '#FF7700'
        elif i < 65:  c = '#FFCC00'
        elif i < 80:  c = '#99DD00'
        else:         c = '#00CC44'
        ax.barh(1, theta[i] - theta[i+1], left=theta[i+1], height=0.4, color=c, edgecolor='none')

    score = resultado["score"]
    angle = np.pi - (score / 100 * np.pi)
    ax.plot([angle, angle], [0, 1.08], color='white', linewidth=5, zorder=5)
    ax.plot(angle, 0, 'o', color='white', markersize=20, zorder=6)
    ax.plot(angle, 0, 'o', color='#0d1117', markersize=10, zorder=7)
    ax.set_ylim(0, 1.35)
    ax.set_theta_zero_location('E')
    ax.set_theta_direction(1)
    ax.set_thetamin(0)
    ax.set_thetamax(180)
    ax.set_xticks([np.pi, 3*np.pi/4, np.pi/2, np.pi/4, 0])
    ax.set_xticklabels(['0\nEVITAR', '25', '50', '75', '100\nEXCELENTE'],
                        color='white', fontsize=9, fontweight='bold')
    ax.set_yticks([])
    ax.spines['polar'].set_visible(False)
    ax.grid(False)

    zona_color = '#FF3333' if score < 35 else '#FF7700' if score < 50 else '#FFCC00' if score < 65 else '#99DD00' if score < 80 else '#00CC44'
    fig.text(0.5, 0.57, f"{score}/100", ha='center', fontsize=30, color='white', fontweight='bold')
    fig.text(0.5, 0.52, resultado['zona'], ha='center', fontsize=11, color=zona_color, fontweight='bold')

    mktcap_b = round(resultado['mktcap'] / 1e9, 1) if resultado.get('mktcap') else 'N/D'
    fig.text(0.5, 0.97, f"{resultado['nombre']} ({resultado['ticker']})  |  {resultado['price']} USD  |  Cap: {mktcap_b}B",
             ha='center', fontsize=12, color='white', fontweight='bold')
    fig.text(0.5, 0.93, resultado['sector'], ha='center', fontsize=9, color='#888888')

    # Barras categorias
    ax2 = fig.add_axes([0.15, 0.20, 0.70, 0.30])
    ax2.set_facecolor('#0d1117')
    ax2.set_xlim(0, 20)
    ax2.set_ylim(-0.5, n - 0.5)
    ax2.axis('off')

    for idx, (cat, datos) in enumerate(reversed(list(cats.items()))):
        pts = datos['puntos']
        y = idx
        ax2.barh(y, 20, height=0.6, color='#1a1a2e', zorder=1)
        bar_c = '#FF3333' if pts < 7 else '#FF7700' if pts < 10 else '#FFCC00' if pts < 14 else '#00CC44'
        ax2.barh(y, pts, height=0.6, color=bar_c, zorder=2)
        ax2.text(-0.3, y, cat, va='center', ha='right', color='#CCCCCC', fontsize=9, fontweight='bold')
        ax2.text(pts + 0.3, y, datos['valores'][:30], va='center', ha='left', color='#AAAAAA', fontsize=7)
        ax2.text(20.5, y, f"{pts}/20", va='center', ha='left', color=bar_c, fontsize=9, fontweight='bold')
    ax2.set_xlim(-6, 22)

    # Estimaciones precio
    if resultado.get('est_1y'):
        pct_1y = round((resultado['est_1y'] - resultado['price']) / resultado['price'] * 100, 1)
        pct_3y = round((resultado['est_3y'] - resultado['price']) / resultado['price'] * 100, 1)
        fig.text(0.5, 0.18, 'ESTIMACION DE PRECIO', ha='center', fontsize=9, color='#666666', fontweight='bold')
        fig.text(0.25, 0.13, f"Precio justo\n{resultado['precio_justo']} USD", ha='center', fontsize=9, color='#AAAAAA')
        fig.text(0.50, 0.13, f"1 año\n{resultado['est_1y']} USD ({pct_1y:+.0f}%)", ha='center', fontsize=9,
                color='#00CC44' if pct_1y > 0 else '#FF3333')
        fig.text(0.75, 0.13, f"3 años\n{resultado['est_3y']} USD ({pct_3y:+.0f}%)", ha='center', fontsize=9,
                color='#00CC44' if pct_3y > 0 else '#FF3333')

    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=120, facecolor='#0d1117', bbox_inches='tight')
    plt.close()
    buf.seek(0)
    return buf


def generate_chart(ticker, entry, tp1, tp2, stop):
    try:
        hist = yf.Ticker(ticker).history(period="2mo", interval="1d")
        if hist.empty or len(hist) < 5:
            return None
        hist.index = pd.to_datetime(hist.index)
        if hist.index.tz is not None:
            hist.index = hist.index.tz_localize(None)

        c, h, lo, vol = hist["Close"], hist["High"], hist["Low"], hist["Volume"]

        # VWAP
        typical = (h + lo + c) / 3
        vwap_vals = (typical * vol).cumsum() / vol.cumsum()

        # Bollinger Bands (20, 2) — solo si hay suficientes datos
        # EMA20 y EMA50
        ema20_line = c.ewm(span=20, adjust=False).mean()
        ema50_line = c.ewm(span=50, adjust=False).mean()

        ap = [
            mpf.make_addplot([entry]*len(hist), color='cyan',   linestyle='dashed', width=1.5),
            mpf.make_addplot([tp1]*len(hist),   color='lime',   linestyle='dashed', width=1.5),
            mpf.make_addplot([tp2]*len(hist),   color='green',  linestyle='dashed', width=1.5),
            mpf.make_addplot([stop]*len(hist),  color='red',    linestyle='dashed', width=1.5),
            mpf.make_addplot(vwap_vals,          color='yellow', width=1.5),
            mpf.make_addplot(ema20_line,         color='#00BFFF', width=1.2),
            mpf.make_addplot(ema50_line,         color='#FF8C00', width=1.2),
        ]

        if len(c) >= 20:
            bb_mid = c.rolling(20).mean()
            bb_std = c.rolling(20).std()
            bb_up  = bb_mid + 2 * bb_std
            bb_dn  = bb_mid - 2 * bb_std
            ap.append(mpf.make_addplot(bb_up, color='#666666', linestyle='dotted', width=0.8))
            ap.append(mpf.make_addplot(bb_dn, color='#666666', linestyle='dotted', width=0.8))

        s = mpf.make_mpf_style(base_mpf_style='nightclouds', gridstyle='')
        buf = io.BytesIO()
        titulo = (f'{nombre(ticker)} | E:{entry} TP1:{tp1} SL:{stop} | '
                  f'VWAP:{round(vwap_vals.iloc[-1],2)} '
                  f'EMA20:{round(ema20_line.iloc[-1],2)} EMA50:{round(ema50_line.iloc[-1],2)}')
        fig, axes = mpf.plot(
            hist, type='candle', style=s,
            figsize=(10, 6), title=f'\n{titulo}',
            addplot=ap, volume=True,
            returnfig=True
        )
        fig.savefig(buf, format='png', dpi=110,
                   bbox_inches='tight', facecolor='#0d1117')
        import matplotlib.pyplot as plt
        plt.close(fig)
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


def fetch_weekly_trend(ticker):
    """Tendencia semanal: EMA20 semanal vs EMA50 semanal y pendiente."""
    try:
        hist = yf.Ticker(ticker).history(period="2y", interval="1wk")
        if hist.empty or len(hist) < 20:
            return None
        c = hist["Close"]
        ema20w = c.ewm(span=20, adjust=False).mean()
        ema50w = c.ewm(span=50, adjust=False).mean()
        rsi_w = calc_rsi(c).iloc[-1]
        tendencia_alcista_w = ema20w.iloc[-1] > ema50w.iloc[-1]
        # Pendiente EMA20 semanal (subiendo o bajando)
        ema20w_subiendo = ema20w.iloc[-1] > ema20w.iloc[-4] if len(ema20w) >= 4 else False
        # Caida semanal fuerte: precio cayo mas del 20% desde maximo 52s
        hi52w = c.tail(52).max()
        dist_max_w = round((c.iloc[-1] - hi52w) / hi52w * 100, 1)
        return {
            "tendencia_alcista_w": tendencia_alcista_w,
            "ema20w_subiendo": ema20w_subiendo,
            "rsi_w": round(rsi_w, 1),
            "dist_max_w": dist_max_w,
            "ema20w": round(ema20w.iloc[-1], 2),
            "ema50w": round(ema50w.iloc[-1], 2),
        }
    except:
        return None


def scan_infravaloradas(stocks, max_results=6):
    """
    Busca activos infravalorados con potencial de rebote.
    Solo acciones — sin crypto.
    Detecta acumulacion silenciosa: volumen creciendo sin subida de precio.
    """
    candidatos = []
    nombres_ext = {**NOMBRES, **INFRA_NOMBRES}

    for t in stocks:
        # Filtrar crypto
        if "-USD" in t or "-EUR" in t:
            continue

        try:
            d = fetch_quote(t, "1y")
            if not d:
                continue

            # Caida desde maximo anual
            dist_max = round((d["price"] - d["hi52"]) / d["hi52"] * 100, 1)
            recup_min = round((d["price"] - d["lo52"]) / d["lo52"] * 100, 1)

            # Filtros principales
            if dist_max > -20:
                continue
            if d["rsi"] > 55:
                continue
            if d["rsi"] < 20:
                continue

            score = 0
            motivos = []

            # Detectar acumulacion silenciosa (volumen creciendo 3 semanas sin subida fuerte)
            acumulacion = False
            try:
                hist_w = yf.Ticker(t).history(period="3mo", interval="1wk")
                if len(hist_w) >= 3:
                    vols = hist_w["Volume"].tail(3).values
                    precios = hist_w["Close"].tail(3).values
                    vol_creciendo = vols[-1] > vols[-2] > vols[-3] * 0.8
                    precio_lateral = abs(precios[-1] - precios[-3]) / precios[-3] < 0.05
                    if vol_creciendo and precio_lateral:
                        acumulacion = True
                        score += 4
                        motivos.append("ACUMULACION SILENCIOSA: volumen creciendo sin subida de precio")
            except:
                pass

            # 1. Magnitud de la caida (cuanto mas caida con RSI bajo, mejor)
            if dist_max < -50:
                score += 4
                motivos.append(f"Caida brutal {dist_max}% desde maximos")
            elif dist_max < -35:
                score += 3
                motivos.append(f"Caida fuerte {dist_max}% desde maximos")
            elif dist_max < -25:
                score += 2
                motivos.append(f"Correccion significativa {dist_max}% desde maximos")

            # 2. RSI en zona ideal de rebote
            if 25 <= d["rsi"] <= 35:
                score += 4
                motivos.append(f"RSI {d['rsi']} zona de sobreventa extrema")
            elif 35 < d["rsi"] <= 45:
                score += 3
                motivos.append(f"RSI {d['rsi']} zona de rebote ideal")
            elif 45 < d["rsi"] <= 55:
                score += 1
                motivos.append(f"RSI {d['rsi']} recuperandose")

            # 3. Recuperacion desde minimos (ya reboto algo = señal positiva)
            if recup_min > 20:
                score += 3
                motivos.append(f"Rebote {recup_min:.0f}% desde minimos — suelo formado")
            elif recup_min > 10:
                score += 2
                motivos.append(f"Primer rebote {recup_min:.0f}% desde minimos")
            elif recup_min > 5:
                score += 1
                motivos.append(f"Inicio rebote {recup_min:.0f}% desde minimos")

            # 4. Semana positiva (momentum inicial)
            if d["d5"] > 3:
                score += 3
                motivos.append(f"Semana +{d['d5']}% momentum naciente")
            elif d["d5"] > 1:
                score += 2
                motivos.append(f"Semana +{d['d5']}% rebote iniciado")
            elif d["d5"] > 0:
                score += 1
                motivos.append(f"Semana positiva +{d['d5']}%")

            # 5. Volumen creciente (acumulacion silenciosa)
            if d["vol_rel"] >= 1.5:
                score += 3
                motivos.append(f"Volumen {d['vol_rel']}x — acumulacion detectada")
            elif d["vol_rel"] >= 1.1:
                score += 1
                motivos.append(f"Volumen {d['vol_rel']}x ligeramente elevado")

            # 6. Cerca de soporte
            for nivel in [d["s1"], d["s2"]]:
                if nivel > 0 and abs(d["price"] - nivel) / nivel * 100 <= 2.0:
                    score += 2
                    motivos.append(f"En zona de soporte {nivel}")
                    break

            # 7. MACD cruce alcista — señal de cambio de tendencia
            if d["macd_cross_up"]:
                score += 3
                motivos.append("MACD cruce alcista — cambio tendencia")

            # Umbral minimo
            if score < 6:
                continue

            nom = nombres_ext.get(t, t)
            candidatos.append({
                **d,
                "nombre": nom,
                "score_intra": score,
                "motivos_intra": motivos,
                "dist_max": dist_max,
                "recup_min": recup_min,
            })

        except Exception as e:
            log.warning(f"Infravaloradas {t}: {e}")

    candidatos.sort(key=lambda x: x["score_intra"], reverse=True)
    return candidatos[:max_results]


def get_noticias_ticker(ticker, nombre_empresa):
    """Busca noticias recientes de un ticker especifico via RSS."""
    try:
        url = f"https://news.google.com/rss/search?q={nombre_empresa}+stock+2026&hl=en&gl=US&ceid=US:en"
        feed = feedparser.parse(url)
        titulares = []
        for entry in feed.entries[:3]:
            title = entry.get("title","").strip()
            if title:
                titulares.append(title)
        return titulares
    except:
        return []


def fetch_weekly_rsi(ticker):
    """RSI semanal para confirmacion 2 timeframes."""
    try:
        hist = yf.Ticker(ticker).history(period="1y", interval="1wk")
        if hist.empty or len(hist) < 14:
            return None
        return round(calc_rsi(hist["Close"]).iloc[-1], 1)
    except:
        return None


def get_top_signals(stocks, n=4, modo="swing"):
    """
    modo='swing': señales swing 1-4 semanas, score minimo 12
    modo='intraday': señales intradía, score minimo 10, criterios distintos
    """
    mercado_ok = mercado_en_tendencia_alcista()
    candidatos = []
    for t in stocks:
        d = fetch_quote(t, "3mo")
        if not d:
            continue

        # Filtros obligatorios
        if d["rsi"] > 68:
            continue
        if d["vol_rel"] < 0.7:
            continue
        if not mercado_ok and d["rsi"] > 45:
            continue

        # Filtro tendencia semanal — no entrar contra tendencia bajista fuerte
        trend_w = fetch_weekly_trend(t)
        if trend_w:
            if trend_w["dist_max_w"] < -25 and not trend_w["tendencia_alcista_w"] and not trend_w["ema20w_subiendo"]:
                continue
            if trend_w["dist_max_w"] < -40 and trend_w["rsi_w"] > 40:
                continue

        score = 0
        motivos = []

        # 1. RSI diario (max 5pts)
        if d["rsi"] < 25:
            score += 5
            motivos.append(f"RSI {d['rsi']} sobreventa extrema")
        elif d["rsi"] < 35:
            score += 4
            motivos.append(f"RSI {d['rsi']} sobreventa")
        elif d["rsi"] < 45:
            score += 3
            motivos.append(f"RSI {d['rsi']} zona ideal entrada")
        elif d["rsi"] < 55:
            score += 2
            motivos.append(f"RSI {d['rsi']} saludable")
        elif d["rsi"] < 68:
            score += 1
            motivos.append(f"RSI {d['rsi']} neutral")

        # 2. RSI semanal confirmacion (max 3pts)
        rsi_w = fetch_weekly_rsi(t)
        if rsi_w is not None:
            if rsi_w < 40:
                score += 3
                motivos.append(f"RSI semanal {rsi_w} confirma ambos TF")
            elif rsi_w < 50:
                score += 2
                motivos.append(f"RSI semanal {rsi_w} confirma")
            elif rsi_w < 60:
                score += 1

        # 3. MACD cruce alcista (4pts)
        if d["macd_cross_up"]:
            score += 4
            motivos.append("MACD cruce alcista confirmado")

        # 4. VWAP (3pts) — precio sobre VWAP = fuerza real
        if d.get("sobre_vwap"):
            score += 3
            motivos.append(f"Precio sobre VWAP ({d.get('vwap',0)}) — fuerza institucional")
        else:
            # Bajo VWAP pero muy cerca = posible rebote
            if d.get("vwap", 0) > 0:
                dist_vwap = (d["price"] - d["vwap"]) / d["vwap"] * 100
                if dist_vwap > -2:
                    score += 1
                    motivos.append(f"Cerca de VWAP ({d.get('vwap',0)}) rebote potencial")

        # 5. Volumen (max 4pts)
        if d["vol_rel"] >= 2.5:
            score += 4
            motivos.append(f"Volumen {d['vol_rel']}x excepcional")
        elif d["vol_rel"] >= 1.8:
            score += 3
            motivos.append(f"Volumen {d['vol_rel']}x muy elevado")
        elif d["vol_rel"] >= 1.3:
            score += 2
            motivos.append(f"Volumen {d['vol_rel']}x elevado")
        elif d["vol_rel"] >= 1.0:
            score += 1
            motivos.append(f"Volumen {d['vol_rel']}x normal")

        # 6. Tendencia EMA (max 4pts)
        if d["tendencia_alcista"] and d["ema20_subiendo"]:
            score += 4
            motivos.append("EMA20 > EMA50 y subiendo")
        elif d["tendencia_alcista"]:
            score += 2
            motivos.append("EMA20 > EMA50")
        elif d["sobre_ema20"]:
            score += 1
            motivos.append("Precio sobre EMA20")

        # 7. Breakout (max 3pts)
        if d["cerca_breakout"]:
            score += 3
            motivos.append(f"Breakout inminente {d['max20']} ({d['dist_breakout']}%)")
        elif d["dist_breakout"] <= 3.0:
            score += 1

        # 8. Fuerza relativa (2pts)
        if d["fuerza_relativa"]:
            score += 2
            motivos.append(f"Fuerza relativa +{d['d20']}% mensual")

        # 9. Momentum (max 2pts)
        if d["d1"] >= 2.0:
            score += 1
        if d["d5"] >= 4.0:
            score += 1

        # 10. Soporte cercano (2pts)
        for nivel in [d["s1"], d["s2"]]:
            if nivel > 0 and abs(d["price"] - nivel) / nivel * 100 <= 1.5:
                score += 2
                motivos.append(f"En zona soporte {nivel}")
                break

        # Umbral minimo segun modo
        umbral = 12 if modo == "swing" else 10
        if score < umbral:
            continue

        entry = d["price"]
        atr = d.get("atr", entry * 0.02)
        stop_atr = round(entry - atr * 1.5, 2)
        stop_sr  = round(d["s1"] * 0.985, 2)
        stop = max(stop_atr, stop_sr) if stop_sr > 0 else stop_atr
        if stop <= 0 or entry - stop > entry * 0.08:
            stop = round(entry * 0.97, 2)

        risk = entry - stop
        if risk <= 0:
            continue

        # Modo swing: TP mas alejados
        # Modo intraday: TP mas cercanos
        mult_tp1 = 1.5 if modo == "swing" else 1.0
        mult_tp2 = 3.0 if modo == "swing" else 2.0
        tp1 = round(entry + risk * mult_tp1, 2)
        tp2 = round(entry + risk * mult_tp2, 2)
        rr  = round((tp1 - entry) / risk, 2)

        if not mercado_ok:
            motivos.insert(0, "AVISO: mercado bajista, operar con cautela")

        candidatos.append({
            **d, "score": score, "motivos": motivos,
            "direction": "COMPRAR", "modo": modo,
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
            # Fix NaN: si no hay precio anterior valido, d1=0
            if len(c) > 1 and c.iloc[-2] > 0:
                d1 = (c.iloc[-1] - c.iloc[-2]) / c.iloc[-2] * 100
            else:
                d1 = 0.0
            # Descartar si precio es NaN o 0
            if c.iloc[-1] != c.iloc[-1] or c.iloc[-1] == 0:
                continue
            if vol_rel >= 2.5:
                anomalias.append({
                    "ticker": t, "nombre": nombre(t),
                    "vol_rel": round(vol_rel, 1),
                    "d1": round(d1, 2) if d1 == d1 else 0.0,
                    "price": round(c.iloc[-1], 2),
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
            resp = ai_client.chat.completions.create(
                model="llama-3.1-70b-versatile",
                messages=[
                    {"role": "system", "content": SYSTEM},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=1024,
                temperature=0.7,
            )
            texto = resp.choices[0].message.content
            return texto[:max_chars] if len(texto) > max_chars else texto
        except Exception as e:
            espera = 10 * (attempt + 1)
            if attempt < 2:
                log.warning(f"Groq rate limit, esperando {espera}s: {e}")
                time.sleep(espera)
            else:
                log.error(f"Groq API: {e}")
                return "IA ocupada. Intenta en unos segundos."


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
    rsi_txt = (f"{s['rsi']} (sobreventa)" if s['rsi'] < 35
               else f"{s['rsi']} (zona ideal)" if s['rsi'] < 45
               else f"{s['rsi']} (saludable)" if s['rsi'] < 55
               else f"{s['rsi']}")
    tendencia  = "ALCISTA" if s.get("tendencia_alcista") else "LATERAL"
    ema_txt    = f"EMA20:{s.get('ema20','-')} EMA50:{s.get('ema50','-')}"
    breakout   = (f"SI a {s.get('max20',0)} ({s.get('dist_breakout',99)}%)"
                  if s.get("cerca_breakout") else "NO")
    fuerza_txt = f"SI (+{s['d20']}% mes)" if s.get("fuerza_relativa") else "NO"
    rsi_w_txt  = f"{s['rsi_semanal']}" if s.get("rsi_semanal") else "N/D"
    vwap_txt   = f"{s.get('vwap','-')} ({'SOBRE' if s.get('sobre_vwap') else 'BAJO'})"
    modo_txt   = "INTRADÍA" if s.get("modo") == "intraday" else "SWING"
    motivos    = "\n  ".join(s.get("motivos", []))
    text = (f"SENAL {modo_txt}: {s['nombre']} ({s['ticker']})\n"
            f"Accion:   {s['direction']}\n"
            f"Entrada:  {s['entry']}\n"
            f"TP1:      {s['tp1']} ({pct_tp1:+.1f}%)\n"
            f"TP2:      {s['tp2']} ({pct_tp2:+.1f}%)\n"
            f"Stop:     {s['stop']} ({pct_sl:+.1f}%) [ATR:{s.get('atr','-')}]\n"
            f"R/R:      {s['rr']}x\n"
            f"Score:    {s['score']}/28\n\n"
            f"CRITERIOS:\n"
            f"RSI diario:    {rsi_txt}\n"
            f"RSI semanal:   {rsi_w_txt}\n"
            f"VWAP:          {vwap_txt}\n"
            f"Volumen:       {s['vol_rel']}x media\n"
            f"Tendencia EMA: {tendencia} ({ema_txt})\n"
            f"Breakout:      {breakout}\n"
            f"Fuerza relat.: {fuerza_txt}\n"
            f"MACD cruce:    {'SI' if s['macd_cross_up'] else 'NO'}\n\n"
            f"Por que entra:\n  {motivos}")
    chart = generate_chart(s['ticker'], s['entry'], s['tp1'], s['tp2'], s['stop'])
    if chart:
        try:
            caption = (f"{s['nombre']} | {modo_txt} | {s['direction']}\n"
                       f"Entrada:{s['entry']} TP1:{s['tp1']} TP2:{s['tp2']} Stop:{s['stop']}\n"
                       f"R/R:{s['rr']}x | RSI:{s['rsi']} | VWAP:{vwap_txt} | Score:{s['score']}/28")
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
    kb.row(InlineKeyboardButton("Infravaloradas", callback_data="infravaloradas"),
           InlineKeyboardButton("Ayuda", callback_data="ayuda"))
    kb.row(InlineKeyboardButton("Intraday", callback_data="intraday"),
           InlineKeyboardButton("Seguimiento", callback_data="seguimiento"))
    kb.row(InlineKeyboardButton("Indice Valor BTC", callback_data="valor_btc"),
           InlineKeyboardButton("Fundamental", callback_data="fundamental_info"))
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

    # Inyectar precios reales de acciones clave
    precios_txt = ""
    for t in ["AZN.L","AMZN","NVDA","META","GOOGL","JPM","PFE","JNJ","MSFT","AAPL"]:
        d = fetch_quote(t, "1mo")
        if d:
            precios_txt += f"{d['nombre']} ({t}): {d['price']} USD\n"

    prompt = (f"Noticias de alto impacto hoy {datetime.now().strftime('%d/%m/%Y')}:\n{bloque}\n\n"
              f"Precios actuales de referencia (USA SOLO ESTOS, no inventes otros):\n{precios_txt}\n"
              "1. Noticia de mayor impacto en bolsa hoy\n"
              "2. OPA o fusion relevante si la hay\n"
              "3. Earnings sorpresa si los hay\n"
              "4. Acciones afectadas con precio actual y como operar")
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
            rows.append(
                f"{d['nombre']} ({t}): precio={d['price']}, RSI={d['rsi']}, "
                f"MACD={'SI' if d['macd_cross_up'] else 'NO'}, vol={d['vol_rel']}x, "
                f"hoy={d['d1']:+.2f}%, semana={d['d5']:+.2f}%, "
                f"EMA20={d['ema20']} ({'sobre' if d['sobre_ema20'] else 'bajo'}), "
                f"S1={d['s1']}, R1={d['r1']}"
            )
    prompt = (
        f"Datos de mercado actuales {datetime.now().strftime('%d/%m/%Y')}:\n"
        + "\n".join(rows) +
        "\n\nUSA SOLO los precios indicados arriba. No uses precios de otros años ni inventes valores.\n\n"
        "1. 2-3 mejores setups con entrada, stop y objetivo usando los precios actuales\n"
        "2. Acciones a evitar y por que\n"
        "3. Trade concreto con precio de entrada exacto del listado\n"
        "4. Riesgo general del mercado ahora mismo 1-10"
    )
    texto = ask_ai(prompt)
    safe_send(msg.chat.id, f"Oportunidades {datetime.now().strftime('%d/%m %H:%M')}\n\n{texto}", message_id=m.message_id)


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

    # Para crypto usar precio en tiempo real de Binance
    precio_real = d["price"]
    if ticker in COINGECKO_IDS:
        rt = get_realtime_price(ticker)
        if rt:
            precio_real = rt["price"]
            d["d1"] = rt["d1"]

    fecha_hoy = datetime.now().strftime('%d/%m/%Y %H:%M')
    vwap_txt = f"VWAP: {d.get('vwap','-')} ({'SOBRE' if d.get('sobre_vwap') else 'BAJO'})"

    prompt = (f"Analisis de {d['nombre']} ({ticker}) — datos en tiempo real {fecha_hoy}:\n"
              f"Precio ACTUAL: {precio_real} | Hoy: {d['d1']:+.2f}% | Semana: {d['d5']:+.2f}% | Mes: {d['d20']:+.2f}%\n"
              f"RSI: {d['rsi']} | MACD cruce: {d['macd_cross_up']} | Volumen: {d['vol_rel']}x\n"
              f"EMA20: {d['ema20']} ({'SOBRE' if d['sobre_ema20'] else 'BAJO'}) | EMA50: {d['ema50']}\n"
              f"{vwap_txt}\n"
              f"R2: {d['r2']} | R1: {d['r1']} | Pivot: {d['pivot']} | S1: {d['s1']} | S2: {d['s2']}\n"
              f"Max 52s: {d['hi52']} | Min 52s: {d['lo52']} | ATR: {d.get('atr','-')}\n\n"
              f"IMPORTANTE: El precio actual es {precio_real}. USA SOLO este precio, no uses precios de otros periodos.\n\n"
              "1. Posicion tecnica actual\n"
              "2. Niveles clave a vigilar\n"
              "3. Escenario alcista vs bajista\n"
              "4. Sesgo operativo\n"
              "5. Entrada concreta, stop y objetivo con precios exactos basados en el precio actual")

    texto = ask_ai(prompt, max_chars=3500)
    header = (f"{d['nombre']} ({ticker}) — {fecha_hoy}\n"
              f"Precio: {precio_real} | Hoy {d['d1']:+.2f}% | Semana {d['d5']:+.2f}% | Mes {d['d20']:+.2f}%\n"
              f"RSI {d['rsi']} | Vol {d['vol_rel']}x | MACD: {'SI' if d['macd_cross_up'] else 'NO'}\n"
              f"EMA20: {'SOBRE' if d['sobre_ema20'] else 'BAJO'} ({d['ema20']}) | EMA50: {d['ema50']}\n"
              f"{vwap_txt}\n"
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
        if btc:
            datos_ia += f"\nBTC precio actual: {btc['price']:,.0f} USD (maximo 52s: {btc['hi52']:,.0f}, minimo 52s: {btc['lo52']:,.0f})"
        if spx:
            datos_ia += f"\nSP500 precio actual: {spx['price']:,.0f} (maximo 52s: {spx['hi52']:,.0f})"
        if dax:
            datos_ia += f"\nDAX precio actual: {dax['price']:,.0f} (maximo 52s: {dax['hi52']:,.0f})"
        prompt = (f"Ciclo de mercado actual:\n{datos_ia}\n\n"
                  "IMPORTANTE: usa SOLO los precios actuales indicados arriba. No uses precios de otros años.\n\n"
                  "1. En que fase real estamos en cada mercado y por que\n"
                  "2. Que suele pasar a continuacion segun el ciclo\n"
                  "3. Que deberia hacer un inversor en esta fase con precios ACTUALES\n"
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


@bot.message_handler(commands=["intraday"])
def cmd_intraday(msg):
    if not allowed(msg): return
    m = bot.send_message(msg.chat.id, "Buscando señales intradía (15min)... RSI+MACD+VWAP+Volumen")
    stocks = US_LARGE + IBEX[:10] + ["BTC-USD","ETH-USD","SOL-USD","BNB-USD"]
    signals = get_intraday_signals(stocks, n=3)
    if not signals:
        safe_send(msg.chat.id,
            "Sin señales intradía claras ahora mismo.\n"
            "Busca momentum fuerte + VWAP + volumen elevado.",
            message_id=m.message_id)
        return
    bot.delete_message(msg.chat.id, m.message_id)
    safe_send(msg.chat.id,
        f"SEÑALES INTRADÍA {datetime.now().strftime('%d/%m %H:%M')}\n"
        f"Timeframe: 15min | TP ajustados para el día\n"
        f"{len(signals)} oportunidades:")
    for s in signals:
        send_signal(msg.chat.id, s)
        time.sleep(1)


@bot.message_handler(commands=["infravaloradas"])
def cmd_infravaloradas(msg):
    if not allowed(msg): return
    m = bot.send_message(msg.chat.id,
        "Buscando activos infravalorados con potencial...\n"
        "Escaneando 80+ activos (puede tardar 30-40s)")

    candidatos = scan_infravaloradas(INFRA_UNIVERSE, max_results=5)

    if not candidatos:
        safe_send(msg.chat.id,
            "No se encontraron activos infravalorados claros ahora mismo.\n"
            "El mercado puede estar en tendencia alcista general sin grandes correcciones.",
            message_id=m.message_id)
        return

    # Construir snapshot
    lines = [f"ACTIVOS INFRAVALORADOS {datetime.now().strftime('%d/%m %H:%M')}\n"]
    datos_ia = []

    for i, c in enumerate(candidatos, 1):
        motivos_txt = " | ".join(c["motivos_intra"][:3])
        lines.append(
            f"{i}. {c['nombre']} ({c['ticker']})\n"
            f"   Precio: {c['price']} | RSI: {c['rsi']}\n"
            f"   Desde maximo: {c['dist_max']}% | Rebote minimo: +{c['recup_min']:.0f}%\n"
            f"   Semana: {c['d5']:+.1f}% | Vol: {c['vol_rel']}x\n"
            f"   Score: {c['score_intra']}/18\n"
            f"   {motivos_txt}\n"
        )
        datos_ia.append(
            f"{c['nombre']} ({c['ticker']}): precio {c['price']}, "
            f"caida {c['dist_max']}% desde maximos, RSI {c['rsi']}, "
            f"semana {c['d5']:+.1f}%, rebote desde minimos {c['recup_min']:.0f}%"
        )

    # Buscar noticias del top 2
    noticias_txt = ""
    for c in candidatos[:2]:
        noticias = get_noticias_ticker(c["ticker"], c["nombre"])
        if noticias:
            noticias_txt += f"\n{c['nombre']}:\n"
            noticias_txt += "\n".join(f"  - {n}" for n in noticias)

    snap = "\n".join(lines)

    # Prompt IA con contexto completo
    prompt = (
        f"Activos infravalorados detectados hoy {datetime.now().strftime('%d/%m/%Y')}:\n\n"
        + "\n".join(datos_ia) +
        (f"\n\nNoticias recientes:\n{noticias_txt}" if noticias_txt else "") +
        "\n\nUSA SOLO los precios indicados. No uses precios de otros años.\n\n"
        "Para cada activo analiza:\n"
        "1. Por que cayo tanto y si el problema esta resuelto o en vias\n"
        "2. Si los fundamentales del negocio siguen intactos\n"
        "3. Cual tiene mas potencial de rebote y por que\n"
        "4. Entrada concreta, stop y objetivo con precios actuales\n"
        "5. Riesgo real: trampa de valor o oportunidad genuina"
    )

    texto = ask_ai(prompt, max_chars=4000)

    safe_send(msg.chat.id, snap, message_id=m.message_id)
    time.sleep(1)
    safe_send(msg.chat.id, f"ANALISIS IA\n\n{texto}")


def cmd_valor_btc_directo(msg):
    """Llamado desde el boton del menu — calcula valor de BTC directamente."""
    if not allowed(msg): return
    m = bot.send_message(msg.chat.id, "Calculando indice barato/caro de Bitcoin...")
    resultado = calcular_indice_valor("BTC-USD")
    if not resultado:
        safe_send(msg.chat.id, "Error obteniendo datos de BTC.", message_id=m.message_id)
        return
    lines = [f"{resultado['nombre']} ({resultado['ticker']}) — {resultado['price']}",
             f"{resultado['score']}/100 — {resultado['zona']}", "",
             "COMPONENTES:"]
    for nombre_c, datos in resultado["componentes"].items():
        lines.append(f"{nombre_c}: {datos['valor']} -> {datos['puntos']}/10")
    texto_resumen = "\n".join(lines)
    try:
        gauge = generate_valor_gauge(resultado)
        bot.delete_message(msg.chat.id, m.message_id)
        bot.send_photo(msg.chat.id, gauge, caption=texto_resumen)
    except Exception as e:
        log.warning(f"Gauge error: {e}")
        safe_send(msg.chat.id, texto_resumen, message_id=m.message_id)


@bot.message_handler(commands=["fundamental"])
def cmd_fundamental(msg):
    if not allowed(msg): return
    parts = msg.text.split()
    if len(parts) < 2:
        safe_send(msg.chat.id,
            "Uso: /fundamental TICKER\n"
            "Analisis fundamental completo con puntuacion 0-100\n\n"
            "Ejemplos:\n"
            "/fundamental NVDA\n"
            "/fundamental AAPL\n"
            "/fundamental IONQ")
        return
    ticker = parts[1].upper()
    m = bot.send_message(msg.chat.id, f"Analizando fundamentales de {ticker}... (10-15s)")

    resultado = calcular_fundamental(ticker)
    if not resultado:
        safe_send(msg.chat.id, f"Sin datos para {ticker}.", message_id=m.message_id)
        return

    # Precio en tiempo real via fetch_quote
    d = fetch_quote(ticker, "1mo")
    precio_actual = d["price"] if d else resultado["price"]
    precio_hoy = d["d1"] if d else 0

    cats = resultado["categorias"]

    # Texto resumen corto para caption
    caption = (f"{resultado['nombre']} ({ticker})\n"
               f"Sector: {resultado['sector']}\n"
               f"Precio: {precio_actual} USD ({precio_hoy:+.2f}% hoy)\n\n"
               f"PUNTUACION: {resultado['score']}/100 — {resultado['zona']}\n\n"
               f"Valoracion:       {cats['Valoracion']['puntos']}/20\n"
               f"Salud Financiera: {cats['Salud Financiera']['puntos']}/20\n"
               f"Rentabilidad:     {cats['Rentabilidad']['puntos']}/20\n"
               f"Crecimiento:      {cats['Crecimiento']['puntos']}/20\n"
               f"Potencial LP:     {cats['Potencial LP']['puntos']}/20")

    # Texto detallado separado
    detalles = [f"DETALLE {resultado['nombre']} ({ticker})\n"]
    for cat, datos in cats.items():
        detalles.append(f"{cat}: {datos['puntos']}/20")
        for nota in datos['notas'][:2]:
            detalles.append(f"  • {nota}")

    # Estimaciones precio — siempre visibles
    if resultado.get('est_1y'):
        pct_1y = round((resultado['est_1y'] - precio_actual) / precio_actual * 100, 1)
        pct_3y = round((resultado['est_3y'] - precio_actual) / precio_actual * 100, 1)
        pj_pct = round((resultado['precio_justo'] - precio_actual) / precio_actual * 100, 1)
        detalles += [
            "",
            "ESTIMACIONES DE PRECIO:",
            f"Precio actual:   {precio_actual} USD",
            f"Precio justo:    {resultado['precio_justo']} USD ({pj_pct:+.0f}%)",
            f"Estimacion 1 año: {resultado['est_1y']} USD ({pct_1y:+.0f}%)",
            f"Estimacion 3 años: {resultado['est_3y']} USD ({pct_3y:+.0f}%)",
        ]
    else:
        detalles.append("\nEstimacion precio: datos insuficientes para calcular DCF")

    texto_detalle = "\n".join(detalles)

    # Análisis IA con precio actual
    ma_txt = f"Noticias M&A: {resultado['noticias_ma'][0][:80]}" if resultado.get('noticias_ma') else "Sin noticias M&A detectadas"
    prompt = (f"Analisis fundamental de {resultado['nombre']} ({ticker}):\n"
              f"Tipo empresa: {resultado.get('tipo','').upper()}\n"
              f"Precio actual HOY: {precio_actual} USD ({precio_hoy:+.2f}% hoy)\n"
              f"Score: {resultado['score']}/100 — {resultado['zona']}\n"
              f"Valoracion: {cats['Valoracion']['puntos']}/20\n"
              f"Salud financiera: {cats['Salud Financiera']['puntos']}/20\n"
              f"Rentabilidad: {cats['Rentabilidad']['puntos']}/20 — {cats['Rentabilidad']['valores']}\n"
              f"Crecimiento: {cats['Crecimiento']['puntos']}/20 — {cats['Crecimiento']['valores']}\n"
              f"Potencial LP: {cats['Potencial LP']['puntos']}/20\n"
              f"{ma_txt}\n\n"
              "USA el precio actual indicado arriba. No uses precios de otros años.\n"
              "Ten en cuenta el tipo de empresa para el analisis — una utility con deuda alta es NORMAL.\n"
              "1. Es buena inversion a largo plazo segun su tipo de negocio?\n"
              "2. Principal riesgo especifico de este sector\n"
              "3. Ventaja competitiva (moat) especifica\n"
              "4. Veredicto: COMPRAR / MANTENER / EVITAR con precio objetivo a 1 año")
    texto_ia = ask_ai(prompt, max_chars=2000)

    try:
        chart = generate_fundamental_chart(resultado)
        bot.delete_message(msg.chat.id, m.message_id)
        bot.send_photo(msg.chat.id, chart, caption=caption[:1020])
        time.sleep(0.5)
        safe_send(msg.chat.id, texto_detalle)
        time.sleep(0.5)
        safe_send(msg.chat.id, f"ANALISIS IA\n\n{texto_ia}")
    except Exception as e:
        log.warning(f"Fundamental chart error: {e}")
        safe_send(msg.chat.id, caption + "\n\n" + texto_detalle + f"\n\nANALISIS IA\n{texto_ia}", message_id=m.message_id)


@bot.message_handler(commands=["valor"])
def cmd_valor(msg):
    if not allowed(msg): return
    parts = msg.text.split()
    if len(parts) < 2:
        safe_send(msg.chat.id,
            "Uso: /valor TICKER\n"
            "Indice 0-100 de barato/caro estilo FREDI\n\n"
            "Ejemplos:\n"
            "/valor BTC-USD\n"
            "/valor NVDA\n"
            "/valor ^GSPC")
        return
    ticker = parts[1].upper()
    m = bot.send_message(msg.chat.id, f"Calculando indice barato/caro de {nombre(ticker)}...")

    resultado = calcular_indice_valor(ticker)
    if not resultado:
        safe_send(msg.chat.id, f"Sin datos para {ticker}.", message_id=m.message_id)
        return

    lines = [f"{resultado['nombre']} ({ticker}) — {resultado['price']}",
             f"{resultado['score']}/100 — {resultado['zona']}", "",
             "COMPONENTES:"]
    for nombre_c, datos in resultado["componentes"].items():
        lines.append(f"{nombre_c}: {datos['valor']} -> {datos['puntos']}/10")
    texto_resumen = "\n".join(lines)

    try:
        gauge = generate_valor_gauge(resultado)
        bot.delete_message(msg.chat.id, m.message_id)
        bot.send_photo(msg.chat.id, gauge, caption=texto_resumen)
    except Exception as e:
        log.warning(f"Gauge error: {e}")
        safe_send(msg.chat.id, texto_resumen, message_id=m.message_id)


@bot.message_handler(commands=["valores"])
def cmd_valores(msg):
    if not allowed(msg): return
    m = bot.send_message(msg.chat.id, "Calculando indices de valor para activos clave... (20-30s)")
    tickers = ["BTC-USD", "ETH-USD", "^GSPC", "^GDAXI", "^IBEX"]
    lines = [f"INDICES DE VALOR {datetime.now().strftime('%d/%m %H:%M')}\n"]
    for t in tickers:
        r = calcular_indice_valor(t)
        if r:
            barra = "#" * (r["score"] // 10) + "-" * (10 - r["score"] // 10)
            lines.append(f"{r['nombre']}: {r['score']}/100 [{barra}]")
            lines.append(f"  {r['zona']}")
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
        "/senales_eu - Senales Europa (RSI+MACD+VWAP+2TF)\n"
        "/senales_us - Senales acciones EEUU\n"
        "/intraday - Senales intradia 15min\n"
        "/etfs - ETFs con señales\n\n"
        "VALOR Y CICLO:\n"
        "/valor TICKER - Indice 0-100 barato/caro (estilo FREDI)\n"
        "/valores - Vision rapida BTC/ETH/SP500/DAX/IBEX\n"
        "/ciclo - Grafico ciclo de mercado BTC/SP500/DAX\n"
        "/infravaloradas - Acciones castigadas con potencial\n\n"
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
        "ciclo": cmd_ciclo, "infravaloradas": cmd_infravaloradas,
        "intraday": cmd_intraday, "seguimiento": cmd_seguimiento,
        "valor": cmd_valor, "valores": cmd_valores,
        "fundamental": cmd_fundamental,
        "fundamental_info": lambda m: safe_send(m.chat.id, "Uso: /fundamental TICKER\nEj: /fundamental NVDA"),
        "riesgo_info": lambda m: safe_send(m.chat.id, "Uso: /riesgo CAPITAL RIESGO% TICKER ENTRADA STOP\nEj: /riesgo 10000 2 NVDA 890 865"),
    }
    if call.data == "valor_btc":
        cmd_valor_btc_directo(call.message)
        return
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
    # Indices con precios reales
    indices_txt = ""
    for t, nom in list(INDICES.items())[3:6]:
        d = fetch_quote(t, "1mo")
        if d:
            indices_txt += f"{nom}: {d['price']:,.0f} (RSI {d['rsi']}, semana {d['d5']:+.1f}%)\n"
    prompt = (f"Briefing {datetime.now().strftime('%A %d/%m/%Y')}:\n"
              f"Indices actuales:\n{indices_txt}"
              f"Noticias:\n{bloque}\nEventos hoy:\n{cal_txt}\n\n"
              "USA SOLO los precios indicados arriba. No uses precios de otros años.\n"
              "1. Resumen del dia\n2. Niveles clave DAX e IBEX hoy\n3. Sectores a vigilar")
    texto = ask_ai(prompt)
    safe_send(ALLOWED_USER_ID, f"Briefing Manana\n\n{texto}")


def job_senales_us():
    """15:00 lunes-viernes — Senales EEUU solo acciones."""
    if not es_dia_laborable():
        return
    safe_send(ALLOWED_USER_ID, f"PREMERCADO EEUU {datetime.now().strftime('%d/%m %H:%M')}")
    signals = get_top_signals(US_STOCKS, n=3)
    if signals:
        for s in signals:
            send_signal(ALLOWED_USER_ID, s)
            time.sleep(2)
    else:
        safe_send(ALLOWED_USER_ID, "Sin senales validas en acciones EEUU para esta sesion.")


def job_close_eu():
    """17:35 lunes-viernes — Cierre Europa."""
    if not es_dia_laborable():
        return
    lines, data_ai = [], []
    for t, nom in list(INDICES.items())[3:]:
        d = fetch_quote(t, "5d")
        if d:
            lines.append(f"{arrow(d['d1'])} {nom}: {d['price']:,.0f}")
            data_ai.append(f"{nom}: precio actual {d['price']:,.0f}, variacion hoy {d['d1']:+.2f}%, semana {d['d5']:+.2f}%, RSI {d['rsi']}")
    if not lines:
        return
    snap = "\n".join(lines)
    prompt = (f"Cierre Europa hoy {datetime.now().strftime('%d/%m/%Y')}:\n"
              + "\n".join(data_ai) +
              "\n\nUSA SOLO estos precios actuales. No uses precios de otros años.\n"
              "1. Resumen de la sesion europea de hoy\n"
              "2. Que esperar de EEUU esta tarde\n"
              "3. Niveles clave manana en DAX e IBEX")
    texto = ask_ai(prompt)
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
            data_ai.append(f"{nom}: precio actual {d['price']:,.0f}, variacion hoy {d['d1']:+.2f}%, semana {d['d5']:+.2f}%, RSI {d['rsi']}")
    if not lines:
        return
    snap = "\n".join(lines)
    prompt = (f"Cierre EEUU hoy {datetime.now().strftime('%d/%m/%Y')}:\n"
              + "\n".join(data_ai) +
              "\n\nUSA SOLO estos precios actuales. No uses precios de otros años.\n"
              "1. Resumen de la sesion americana de hoy\n"
              "2. Sectores que lideraron y cuales quedaron atras\n"
              "3. Perspectiva para manana y niveles clave SP500 y Nasdaq")
    texto = ask_ai(prompt)
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
    fecha = datetime.now().strftime('%d/%m/%Y %H:%M')
    rows = []
    datos_ai = []
    for a in top:
        d1 = a['d1'] if not (a['d1'] != a['d1']) else 0.0  # fix NaN
        rows.append(f"{a['nombre']} ({a['ticker']}): vol {a['vol_rel']}x | hoy {d1:+.2f}% | precio {a['price']}")
        datos_ai.append(f"{a['nombre']} ({a['ticker']}): precio actual {a['price']} USD, volumen {a['vol_rel']}x la media, variacion hoy {d1:+.2f}%")
    bloque = "\n".join(rows)
    prompt = (f"Anomalias de volumen detectadas hoy {fecha}:\n"
              + "\n".join(datos_ai) +
              "\n\nUSA SOLO los precios indicados. No uses precios de otros años.\n"
              "1. Hay noticia detras de cada anomalia?\n"
              "2. Cual es la mas interesante y por que\n"
              "3. Como operar con precio exacto, stop y objetivo")
    texto = ask_ai(prompt)
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
    fecha = datetime.now().strftime('%d/%m/%Y %H:%M')
    prompt = (f"Alertas S/R detectadas {fecha}:\n{bloque}\n\n"
              "Los precios mostrados son los precios actuales reales.\n"
              "1. Las 2 mas interesantes para operar\n"
              "2. Rebote o ruptura y como confirmarlo\n"
              "3. Entrada, stop y objetivo con precios exactos del listado")
    texto = ask_ai(prompt)
    safe_send(ALLOWED_USER_ID, f"Alerta S/R {datetime.now().strftime('%H:%M')}\n\n{bloque}\n\n{texto}")


def job_explosion_scanner():
    """Cada 3h dias laborables — Explosiones."""
    if not es_dia_laborable():
        return
    candidates = scan_explosions(US_STOCKS + EU_STOCKS)
    if not candidates:
        return
    fecha = datetime.now().strftime('%d/%m/%Y %H:%M')
    rows = [f"{c['nombre']} ({c['ticker']}): precio {c['price']} | semana {c['d5']:+.1f}% | vol {c['vol_rel']}x | R1:{c['r1']}" for c in candidates[:3]]
    bloque = "\n".join(rows)
    prompt = (f"Posibles explosiones de precio detectadas {fecha}:\n{bloque}\n\n"
              "USA SOLO los precios indicados arriba.\n"
              "1. Por que podria seguir subiendo cada una\n"
              "2. Nivel clave a superar con precio exacto\n"
              "3. Entrada, stop y objetivo usando el precio actual")
    texto = ask_ai(prompt)
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
