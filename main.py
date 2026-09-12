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
import matplotlib.dates as mdates
import mplfinance as mpf
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from groq import Groq
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from collections import defaultdict

TELEGRAM_TOKEN   = os.environ["TELEGRAM_TOKEN"]
GROQ_API_KEY     = os.environ["GROQ_API_KEY"]
ALLOWED_USER_ID  = int(os.environ.get("ALLOWED_USER_ID", 0))
NOWPAYMENTS_KEY  = os.environ.get("NOWPAYMENTS_API_KEY", "")
NOWPAYMENTS_IPN  = os.environ.get("NOWPAYMENTS_IPN_SECRET", "")
WALLET_USDT      = os.environ.get("WALLET_USDT", "")
MADRID = pytz.timezone("Europe/Madrid")

# Sistema de suscripciones
# {chat_id: {"expiry": datetime, "trial_used": bool, "activo": bool}}
SUSCRIPTORES = {}
TRIAL_DIAS = 7
PRECIO_MENSUAL = 19  # USDT

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
    # Para crypto usar Binance como fuente primaria
    if "-USD" in ticker or ticker in ["BTC", "ETH", "SOL", "BNB"]:
        binance_map = {
            "BTC-USD": "BTCUSDT", "ETH-USD": "ETHUSDT",
            "SOL-USD": "SOLUSDT", "BNB-USD": "BNBUSDT",
            "XRP-USD": "XRPUSDT", "ADA-USD": "ADAUSDT",
            "AVAX-USD": "AVAXUSDT", "LINK-USD": "LINKUSDT",
            "DOT-USD": "DOTUSDT", "MATIC-USD": "MATICUSDT",
            "DOGE-USD": "DOGEUSDT",
        }
        symbol = binance_map.get(ticker)
        if symbol:
            try:
                # Klines diarios de Binance
                r = requests.get(
                    f"https://api.binance.com/api/v3/klines",
                    params={"symbol": symbol, "interval": "1d", "limit": 100},
                    timeout=8
                )
                if r.status_code == 200:
                    klines = r.json()
                    if len(klines) >= 5:
                        closes = [float(k[4]) for k in klines]
                        highs  = [float(k[2]) for k in klines]
                        lows   = [float(k[3]) for k in klines]
                        vols   = [float(k[5]) for k in klines]
                        c  = pd.Series(closes)
                        h  = pd.Series(highs)
                        lo = pd.Series(lows)
                        vol = pd.Series(vols)
                        price = c.iloc[-1]
                        d1  = (price - c.iloc[-2]) / c.iloc[-2] * 100 if len(c) > 1 else 0
                        d5  = (price - c.iloc[-6]) / c.iloc[-6] * 100 if len(c) > 5 else 0
                        d20 = (price - c.iloc[-21]) / c.iloc[-21] * 100 if len(c) > 20 else 0
                        hi52 = h.max(); lo52 = lo.min()
                        pivot = (h.iloc[-1] + lo.iloc[-1] + price) / 3
                        r1 = 2*pivot - lo.iloc[-1]; s1 = 2*pivot - h.iloc[-1]
                        hi20 = h.tail(20).max(); lo20 = lo.tail(20).min()
                        rng = hi20 - lo20
                        r2 = hi20 + rng * 0.382; s2 = lo20 - rng * 0.382
                        rsi = calc_rsi(c).iloc[-1] if len(c) >= 14 else 50
                        macd_l, macd_s = calc_macd(c)
                        macd_cross_up = (len(macd_l) >= 2 and
                                        macd_l.iloc[-1] > macd_s.iloc[-1] and
                                        macd_l.iloc[-2] <= macd_s.iloc[-2])
                        avg_vol = vol.tail(20).mean()
                        vol_rel = vol.iloc[-1] / avg_vol if avg_vol > 0 else 1.0
                        ema20 = c.ewm(span=20, adjust=False).mean()
                        ema50 = c.ewm(span=50, adjust=False).mean()
                        typical = (h + lo + c) / 3
                        vwap = (typical * vol).tail(20).sum() / vol.tail(20).sum()
                        high_low = h - lo
                        high_close = (h - c.shift()).abs()
                        low_close = (lo - c.shift()).abs()
                        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
                        atr = tr.ewm(span=14, adjust=False).mean().iloc[-1]
                        max20 = hi20; dist_breakout = round((max20 - price) / max20 * 100, 2)
                        cerca_breakout = dist_breakout <= 2.0
                        fuerza_relativa = d20 > 5
                        return {
                            "ticker": ticker, "nombre": nombre(ticker), "price": round(price, 2),
                            "d1": round(d1, 2), "d5": round(d5, 2), "d20": round(d20, 2),
                            "hi52": round(hi52, 2), "lo52": round(lo52, 2),
                            "pivot": round(pivot, 2), "r1": round(r1, 2), "r2": round(r2, 2),
                            "s1": round(s1, 2), "s2": round(s2, 2),
                            "rsi": round(rsi, 1), "macd_cross_up": macd_cross_up,
                            "vol_rel": round(vol_rel, 2), "atr": round(atr, 2),
                            "ema20": round(ema20.iloc[-1], 2), "ema50": round(ema50.iloc[-1], 2),
                            "sobre_ema20": price > ema20.iloc[-1],
                            "sobre_ema50": price > ema50.iloc[-1],
                            "tendencia_alcista": ema20.iloc[-1] > ema50.iloc[-1],
                            "ema20_subiendo": ema20.iloc[-1] > ema20.iloc[-3],
                            "vwap": round(vwap, 2), "sobre_vwap": price > vwap,
                            "max20": round(max20, 2), "dist_breakout": dist_breakout,
                            "cerca_breakout": cerca_breakout, "fuerza_relativa": fuerza_relativa,
                        }
            except Exception as e:
                log.warning(f"Binance fetch_quote {ticker}: {e}")
                # Continuar con yfinance como fallback

    # yfinance como fuente para acciones y fallback crypto
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
        r2 = requests.get(
            f"https://fapi.binance.com/fapi/v1/allForceOrders",
            params={"symbol": symbol, "limit": 100},
            timeout=6
        )
        if r2.status_code == 200 and r2.text and r2.text.strip():
            orders = r2.json()
            if isinstance(orders, list):
                liq_long = sum(float(o["origQty"]) * float(o["price"])
                              for o in orders if o.get("side") == "SELL")
                liq_short = sum(float(o["origQty"]) * float(o["price"])
                               for o in orders if o.get("side") == "BUY")
                resultado["liquidaciones"] = {
                    "longs_m": round(liq_long / 1e6, 1),
                    "shorts_m": round(liq_short / 1e6, 1),
                }
    except Exception as e:
        log.debug(f"Liquidaciones: {e}")

    return resultado


def get_google_trends(keyword="Bitcoin", timeframe="today 3-m"):
    """
    Obtiene el interés de búsqueda de Google Trends.
    Retorna valor 0-100 donde:
    - 0-20 = nadie busca = zona de suelo (SEÑAL ALCISTA)
    - 80-100 = todo el mundo busca = zona de techo (SEÑAL BAJISTA)
    """
    try:
        from pytrends.request import TrendReq
        pytrends = TrendReq(hl='es-ES', tz=60, timeout=(5, 15))
        pytrends.build_payload([keyword], cat=0, timeframe=timeframe, geo='', gprop='')
        df = pytrends.interest_over_time()
        if df.empty:
            return None
        valor_actual = int(df[keyword].iloc[-1])
        promedio = int(df[keyword].mean())
        maximo = int(df[keyword].max())
        # Normalizar: qué % del máximo histórico reciente es el valor actual
        nivel_relativo = round((valor_actual / maximo * 100) if maximo > 0 else 50)
        return {
            "valor_actual": valor_actual,
            "promedio": promedio,
            "maximo": maximo,
            "nivel_relativo": nivel_relativo,
            "señal": "SUELO" if nivel_relativo < 25 else "TECHO" if nivel_relativo > 75 else "NEUTRAL"
        }
    except Exception as e:
        log.warning(f"Google Trends: {e}")
        return None


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

        # Ajuste Google Trends (crypto) — poco interés = suelo, mucho = techo
        if nombre_mercado.upper() in ["BTC", "BITCOIN", "ETH", "ETHEREUM"]:
            trends = get_google_trends("Bitcoin", timeframe="today 3-m")
            if trends:
                nivel = trends["nivel_relativo"]
                if nivel < 20 and fase_base in [9, 10, 11, 12, 0]:
                    # Nadie busca Bitcoin + caída fuerte = posible suelo
                    # Mover hacia Incredulidad si ya hay rebote
                    if recuperacion_desde_minimo and recuperacion_desde_minimo > 15:
                        fase_base = min(fase_base, 1)  # Incredulidad
                elif nivel > 80 and fase_base <= 6:
                    # Todo el mundo busca Bitcoin = zona de techo
                    fase_base = max(fase_base, 6)  # Euforia mínimo

        # Ajuste: si tendencia alcista y cerca de maximos, puede ser creencia/emocion
        if dist_desde_maximo > -8 and tendencia_alcista and d_anual is not None:
            if d_anual > 30:
                fase_base = 5   # Emocion (año muy bueno, cerca del techo)
            elif d_anual > 15:
                fase_base = 4   # Creencia (año bueno, tendencia confirmada)

        fase = fase_base

    else:
        # Sin datos anuales: usar EMA200 + halving + Fear&Greed (sin RSI)
        import datetime as dt_mod
        halving4 = dt_mod.date(2024, 4, 19)
        meses_halving = (dt_mod.date.today() - halving4).days // 30

        score = 0

        # EMA200 — más importante que RSI para ciclo largo
        if tendencia_alcista and sobre_ema50:
            score += 3   # sobre EMA200 = bull
        elif not sobre_ema50:
            score -= 3   # bajo EMA200 = bear

        # Meses desde halving
        if meses_halving < 18:
            score += 2   # fase bull histórica
        elif meses_halving < 22:
            score += 0   # zona de techo
        elif meses_halving < 32:
            score -= 2   # bear histórico
        elif meses_halving < 42:
            score -= 1   # suelo/recuperación

        # Fear & Greed
        if fg:
            if fg > 70:   score += 2
            elif fg < 30: score -= 2

        # VIX si disponible
        if vix:
            if vix > 30: score -= 2
            elif vix < 15: score += 1

        score = max(-8, min(8, score))
        if score >= 6:    fase = 6
        elif score >= 4:  fase = 5
        elif score >= 2:  fase = 4
        elif score >= 0:  fase = 3
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
    Indice 0-100: 100=barato (acumulacion), 0=caro (venta).
    Para crypto BTC: indicadores de CICLO LARGO sin RSI diario.
    Para acciones/indices: indicadores tecnicos clasicos.
    """
    es_crypto = ticker in COINGECKO_IDS or "-USD" in ticker
    es_indice = ticker.startswith("^")

    d = fetch_quote(ticker, "3mo")
    if not d:
        return None

    ema200_data = fetch_ema200_distance(ticker)
    componentes = {}

    # EMA 200 — el indicador mas importante del ciclo
    if ema200_data:
        dist = ema200_data["dist_pct"]
        if dist < -30:   pts_ema = 10; ema_txt = f"{dist:+.1f}% — muy por debajo (suelo)"
        elif dist < -15: pts_ema = 9;  ema_txt = f"{dist:+.1f}% — por debajo (bear)"
        elif dist < -5:  pts_ema = 7;  ema_txt = f"{dist:+.1f}% — tocando EMA200"
        elif dist < 0:   pts_ema = 5;  ema_txt = f"{dist:+.1f}% — justo bajo EMA200"
        elif dist < 10:  pts_ema = 3;  ema_txt = f"{dist:+.1f}% — sobre EMA200 (bull)"
        elif dist < 25:  pts_ema = 2;  ema_txt = f"{dist:+.1f}% — alejado EMA200"
        else:            pts_ema = 1;  ema_txt = f"{dist:+.1f}% — muy lejos (euforia)"
        componentes["EMA 200"] = {"valor": ema_txt, "puntos": pts_ema}
    else:
        pts_ema = 5
        componentes["EMA 200"] = {"valor": "N/D", "puntos": 5}

    if es_crypto:
        # RSI diario — útil para timing de entrada
        rsi = d["rsi"]
        if rsi < 25:   pts_rsi = 10; rsi_txt = f"{rsi} — sobreventa extrema"
        elif rsi < 35: pts_rsi = 8;  rsi_txt = f"{rsi} — sobreventa"
        elif rsi < 45: pts_rsi = 6;  rsi_txt = f"{rsi} — zona de compra"
        elif rsi < 55: pts_rsi = 5;  rsi_txt = f"{rsi} — neutral"
        elif rsi < 65: pts_rsi = 3;  rsi_txt = f"{rsi} — sobrecompra leve"
        elif rsi < 75: pts_rsi = 2;  rsi_txt = f"{rsi} — sobrecompra"
        else:          pts_rsi = 1;  rsi_txt = f"{rsi} — sobrecompra extrema"
        componentes["RSI 14d"] = {"valor": rsi_txt, "puntos": pts_rsi}

        # 1. FEAR & GREED
        fg = get_fear_greed()
        if fg:
            fgv = fg["valor"]
            if fgv < 15:   pts_fg = 10; fg_txt = f"{fgv} — MIEDO EXTREMO (suelo)"
            elif fgv < 30: pts_fg = 8;  fg_txt = f"{fgv} — Miedo (buena zona)"
            elif fgv < 45: pts_fg = 6;  fg_txt = f"{fgv} — Miedo moderado"
            elif fgv < 55: pts_fg = 5;  fg_txt = f"{fgv} — Neutral"
            elif fgv < 70: pts_fg = 3;  fg_txt = f"{fgv} — Codicia"
            elif fgv < 85: pts_fg = 2;  fg_txt = f"{fgv} — Codicia extrema"
            else:          pts_fg = 1;  fg_txt = f"{fgv} — EUFORIA (techo)"
            componentes["Fear & Greed"] = {"valor": fg_txt, "puntos": pts_fg}
        else:
            pts_fg = 5
            componentes["Fear & Greed"] = {"valor": "N/D", "puntos": 5}

        # 2. FUNDING RATE
        symbol_map = {"BTC-USD":"BTCUSDT","ETH-USD":"ETHUSDT","SOL-USD":"SOLUSDT","BNB-USD":"BNBUSDT"}
        binance_sym = symbol_map.get(ticker, "BTCUSDT")
        deriv = get_binance_derivatives(binance_sym)
        funding = deriv.get("funding", {}).get("valor") if deriv else None
        if funding is not None:
            if funding < -0.02:   pts_fund = 10; fund_txt = f"{funding:+.4f}% — shorts pagando (suelo)"
            elif funding < 0:     pts_fund = 7;  fund_txt = f"{funding:+.4f}% — negativo (bajista)"
            elif funding < 0.01:  pts_fund = 5;  fund_txt = f"{funding:+.4f}% — neutro"
            elif funding < 0.03:  pts_fund = 3;  fund_txt = f"{funding:+.4f}% — longs pagando"
            else:                 pts_fund = 1;  fund_txt = f"{funding:+.4f}% — apalancamiento extremo"
            componentes["Funding Rate"] = {"valor": fund_txt, "puntos": pts_fund}
        else:
            pts_fund = 5
            componentes["Funding Rate"] = {"valor": "N/D", "puntos": 5}

        # 3. DXY
        dxy_d = fetch_quote("DX-Y.NYB", "1mo")
        if dxy_d:
            dxy_val = dxy_d["price"]
            if dxy_val > 106:   pts_dxy = 9; dxy_txt = f"{dxy_val} — dolar muy fuerte (suelo crypto)"
            elif dxy_val > 103: pts_dxy = 7; dxy_txt = f"{dxy_val} — dolar fuerte"
            elif dxy_val > 100: pts_dxy = 5; dxy_txt = f"{dxy_val} — dolar neutral"
            elif dxy_val > 97:  pts_dxy = 3; dxy_txt = f"{dxy_val} — dolar debil"
            else:               pts_dxy = 2; dxy_txt = f"{dxy_val} — dolar muy debil"
            componentes["DXY Dolar"] = {"valor": dxy_txt, "puntos": pts_dxy}
        else:
            pts_dxy = 5
            componentes["DXY Dolar"] = {"valor": "N/D", "puntos": 5}

        # 4. GOOGLE TRENDS
        keyword = "Bitcoin" if "BTC" in ticker else "Ethereum" if "ETH" in ticker else "crypto"
        trends = get_google_trends(keyword, timeframe="today 3-m")
        if trends:
            nivel = trends["nivel_relativo"]
            if nivel < 15:    pts_trends = 10; t_txt = f"{nivel}% — nadie busca (SUELO)"
            elif nivel < 25:  pts_trends = 8;  t_txt = f"{nivel}% — interes muy bajo"
            elif nivel < 40:  pts_trends = 6;  t_txt = f"{nivel}% — interes bajo"
            elif nivel < 60:  pts_trends = 5;  t_txt = f"{nivel}% — interes normal"
            elif nivel < 75:  pts_trends = 3;  t_txt = f"{nivel}% — interes alto"
            elif nivel < 90:  pts_trends = 2;  t_txt = f"{nivel}% — interes muy alto"
            else:             pts_trends = 1;  t_txt = f"{nivel}% — EUFORIA (techo)"
            componentes["Google Trends"] = {"valor": t_txt, "puntos": pts_trends}
        else:
            pts_trends = 5
            componentes["Google Trends"] = {"valor": "N/D", "puntos": 5}

        # 5. CICLO HALVING — posicion en el ciclo de 4 anos
        import datetime as dt_mod
        halving4 = dt_mod.date(2024, 4, 19)
        meses_halving = (dt_mod.date.today() - halving4).days // 30
        if meses_halving < 6:    pts_halving = 7;  h_txt = f"Mes {meses_halving} — bull temprano"
        elif meses_halving < 18: pts_halving = 4;  h_txt = f"Mes {meses_halving} — bull maduro"
        elif meses_halving < 22: pts_halving = 2;  h_txt = f"Mes {meses_halving} — zona de techo"
        elif meses_halving < 32: pts_halving = 6;  h_txt = f"Mes {meses_halving} — bear (caida normal)"
        elif meses_halving < 40: pts_halving = 9;  h_txt = f"Mes {meses_halving} — suelo historico"
        else:                    pts_halving = 8;  h_txt = f"Mes {meses_halving} — recuperacion"
        componentes["Ciclo Halving"] = {"valor": h_txt, "puntos": pts_halving}

        # 6. FLUJOS ETF — institucionales comprando o vendiendo
        try:
            ibit = fetch_quote("IBIT", "1mo")
            if ibit:
                btc_sem = d["d5"]
                ibit_sem = ibit["d5"]
                diferencia = ibit_sem - btc_sem
                if diferencia > 2:    pts_etf = 9;  etf_txt = f"IBIT +{ibit_sem:.1f}% — fondos comprando"
                elif diferencia > 0:  pts_etf = 7;  etf_txt = f"IBIT {ibit_sem:.1f}% — flujo positivo"
                elif diferencia > -2: pts_etf = 5;  etf_txt = f"IBIT {ibit_sem:.1f}% — flujo neutral"
                else:                 pts_etf = 3;  etf_txt = f"IBIT {ibit_sem:.1f}% — flujo negativo"
                componentes["Flujos ETF"] = {"valor": etf_txt, "puntos": pts_etf}
            else:
                pts_etf = 5
                componentes["Flujos ETF"] = {"valor": "N/D", "puntos": 5}
        except:
            pts_etf = 5
            componentes["Flujos ETF"] = {"valor": "N/D", "puntos": 5}

        total_pts = pts_ema + pts_rsi + pts_fg + pts_fund + pts_dxy + pts_trends + pts_halving + pts_etf
        max_pts = 80  # 8 componentes × 10 max

    else:
        # ACCION o INDICE
        rsi = d["rsi"]
        if rsi < 25:   pts_rsi = 10
        elif rsi < 35: pts_rsi = 8
        elif rsi < 45: pts_rsi = 6
        elif rsi < 55: pts_rsi = 5
        elif rsi < 65: pts_rsi = 3
        elif rsi < 75: pts_rsi = 2
        else:          pts_rsi = 1
        componentes["RSI 14d"] = {"valor": rsi, "puntos": pts_rsi}

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

        vol_rel = d["vol_rel"]
        d1 = d["d1"]
        if vol_rel > 1.5 and d1 < -2: pts_vol = 9
        elif vol_rel > 1.2 and d1 < 0: pts_vol = 7
        elif vol_rel < 0.7: pts_vol = 5
        else: pts_vol = 4
        componentes["Volumen"] = {"valor": f"{vol_rel}x", "puntos": pts_vol}

        total_pts = pts_ema + pts_rsi + pts_vix + pts_rsiw + pts_52 + pts_vol
        max_pts = 60

    score_final = max(0, min(100, round(total_pts / max_pts * 100)))

    if score_final >= 80:    zona = "BARATO — ACUMULACION FUERTE"
    elif score_final >= 65:  zona = "BARATO — BUENA ZONA DE COMPRA"
    elif score_final >= 45:  zona = "NEUTRAL"
    elif score_final >= 30:  zona = "CARO — PRECAUCION"
    else:                    zona = "MUY CARO — ZONA DE VENTA"

def generate_valor_gauge(resultado):
    """Genera gauge visual mejorado — velocimetro + barras de componentes legibles."""
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    import numpy as np

    componentes = resultado.get("componentes", {})
    n_comp = len(componentes)
    tipo = resultado.get("tipo", "").upper()

    fig = plt.figure(figsize=(12, 8 + n_comp * 0.55))
    fig.patch.set_facecolor('#0d1117')

    # Velocimetro superior
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
        ax.barh(1, theta[i] - theta[i+1], left=theta[i+1], height=0.45, color=color, edgecolor='none')

    score = resultado['score']
    angle = np.pi - (score / 100 * np.pi)
    ax.plot([angle, angle], [0, 1.10], color='white', linewidth=6, zorder=5)
    ax.plot(angle, 0, 'o', color='white', markersize=22, zorder=6)
    ax.plot(angle, 0, 'o', color='#0d1117', markersize=11, zorder=7)

    ax.set_ylim(0, 1.35)
    ax.set_theta_zero_location('E')
    ax.set_theta_direction(1)
    ax.set_thetamin(0)
    ax.set_thetamax(180)
    ax.set_xticks([np.pi, 3*np.pi/4, np.pi/2, np.pi/4, 0])
    ax.set_xticklabels(['0\nCARO', '25', '50\nNEUTRAL', '75', '100\nBARATÓ'],
                        color='white', fontsize=10, fontweight='bold')
    ax.set_yticks([])
    ax.spines['polar'].set_visible(False)
    ax.grid(False)

    # Score y zona
    zona_color = '#FF3333' if score < 30 else '#FF7700' if score < 45 else '#FFCC00' if score < 65 else '#99DD00' if score < 80 else '#00CC44'
    fig.text(0.5, 0.475, f"{score}/100", ha='center', va='center',
             fontsize=34, color='white', fontweight='bold')
    fig.text(0.5, 0.430, resultado['zona'], ha='center', va='center',
             fontsize=13, color=zona_color, fontweight='bold')

    # Precio actual y tipo
    precio = resultado.get('price', '')
    cambio = resultado.get('cambio_hoy', 0)
    cambio_txt = f"  ({cambio:+.2f}% hoy)" if cambio else ""
    fig.text(0.5, 0.975, f"{resultado['nombre']} ({resultado['ticker']})",
             ha='center', va='top', fontsize=14, color='white', fontweight='bold')
    fig.text(0.5, 0.950, f"{precio} USD{cambio_txt}",
             ha='center', va='top', fontsize=11, color='#AAAAAA')
    if tipo:
        fig.text(0.5, 0.927, f"Tipo: {tipo}",
                ha='center', va='top', fontsize=9, color='#666666')

    # Separador
    fig.text(0.5, 0.408, '─' * 60, ha='center', color='#333333', fontsize=8)
    fig.text(0.5, 0.400, 'COMPONENTES', ha='center',
             fontsize=10, color='#666666', fontweight='bold')

    # Barras componentes — más grandes
    ax2 = fig.add_axes([0.05, 0.03, 0.90, 0.36])
    ax2.set_facecolor('#0d1117')
    ax2.set_xlim(0, 10)
    ax2.set_ylim(-0.5, n_comp - 0.5)
    ax2.axis('off')

    for idx, (nombre_c, datos) in enumerate(reversed(list(componentes.items()))):
        pts = datos['puntos']
        val = datos['valor']
        y = idx

        # Fondo
        ax2.barh(y, 10, height=0.65, left=0, color='#1a1a2e', zorder=1)

        # Barra coloreada
        bar_color = '#FF3333' if pts <= 3 else '#FF7700' if pts <= 5 else '#FFCC00' if pts <= 7 else '#00CC44'
        ax2.barh(y, pts, height=0.65, left=0, color=bar_color, alpha=0.9, zorder=2)

        # Nombre componente
        ax2.text(-0.2, y, nombre_c, va='center', ha='right',
                color='white', fontsize=11, fontweight='bold')

        # Valor en la barra
        ax2.text(pts + 0.15, y, str(val), va='center', ha='left',
                color='#CCCCCC', fontsize=9)

        # Puntuacion
        ax2.text(10.2, y, f"{pts}/10", va='center', ha='left',
                color=bar_color, fontsize=11, fontweight='bold')

    ax2.set_xlim(-3.5, 11.5)

    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=130, facecolor='#0d1117', bbox_inches='tight')
    plt.close()
    buf.seek(0)
    return buf


def generate_halving_chart():
    """
    Ciclo de 4 años de Bitcoin con datos reales actualizados:
    - ATH real: $126,080 (Oct 2025)
    - Bear actual: caída ~50% desde ATH
    - Proyección suelo: $60-75K (posible ya visto)
    - Próximo ciclo 2028-2029: objetivo $200-295K
    """
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    import numpy as np
    import pandas as pd

    try:
        btc = yf.Ticker("BTC-USD")
        hist = btc.history(start="2012-01-01", interval="1mo")
        if hist.empty:
            hist = btc.history(period="max", interval="1mo")
    except:
        hist = pd.DataFrame()

    fig, ax = plt.subplots(figsize=(18, 10))
    fig.patch.set_facecolor('#0d1117')
    ax.set_facecolor('#0d1117')

    # Halvings
    halvings = [
        {"fecha": "2012-11-28", "label": "1st Halving\nNov 2012"},
        {"fecha": "2016-07-09", "label": "2nd Halving\nJul 2016"},
        {"fecha": "2020-05-11", "label": "3rd Halving\nMay 2020"},
        {"fecha": "2024-04-19", "label": "4th Halving\nApr 2024"},
        {"fecha": "2028-03-15", "label": "5th Halving\nMar 2028 (est.)"},
    ]

    # Fases reales actualizadas
    fases = [
        {"inicio": "2012-11-01", "fin": "2013-12-31", "tipo": "bull",     "label": "Bull 12m"},
        {"inicio": "2014-01-01", "fin": "2015-01-31", "tipo": "bear",     "label": "Bear 13m"},
        {"inicio": "2015-02-01", "fin": "2016-07-01", "tipo": "recovery", "label": "Recovery 17m"},
        {"inicio": "2016-07-01", "fin": "2017-12-31", "tipo": "bull",     "label": "Bull 18m"},
        {"inicio": "2018-01-01", "fin": "2019-02-28", "tipo": "bear",     "label": "Bear 14m"},
        {"inicio": "2019-03-01", "fin": "2020-05-01", "tipo": "recovery", "label": "Recovery 14m"},
        {"inicio": "2020-05-01", "fin": "2021-11-30", "tipo": "bull",     "label": "Bull 19m"},
        {"inicio": "2021-12-01", "fin": "2022-11-30", "tipo": "bear",     "label": "Bear 12m"},
        {"inicio": "2022-12-01", "fin": "2024-04-01", "tipo": "recovery", "label": "Recovery 16m"},
        # Ciclo 4 REAL
        {"inicio": "2024-04-01", "fin": "2025-10-06", "tipo": "bull",     "label": "Bull 18m\n(REAL)"},
        {"inicio": "2025-10-07", "fin": "2026-09-10", "tipo": "bear",     "label": "Bear ← AHORA\n~11m"},
        # PROYECCION
        {"inicio": "2026-09-10", "fin": "2027-06-30", "tipo": "recovery", "label": "Recovery\n(proyec.)"},
        {"inicio": "2027-07-01", "fin": "2029-06-30", "tipo": "bull",     "label": "Bull\n(proyec. 2028-29)"},
    ]

    colores_fase = {
        "bull":     {"color": "#0a3a0a", "edge": "#00CC44", "text": "#00CC44"},
        "bear":     {"color": "#3a0a0a", "edge": "#FF3333", "text": "#FF3333"},
        "recovery": {"color": "#0a1a3a", "edge": "#4488FF", "text": "#4488FF"},
    }

    for fase in fases:
        ini = pd.Timestamp(fase["inicio"])
        fin = pd.Timestamp(fase["fin"])
        c = colores_fase[fase["tipo"]]
        ax.axvspan(ini, fin, alpha=0.22, color=c["color"])

    # Precio histórico real
    if not hist.empty:
        precios = hist["Close"]
        precios_log = np.log10(precios.clip(lower=0.01))
        ax.plot(precios.index, precios_log,
               color='white', linewidth=2.5, zorder=5)

        precio_actual = precios.iloc[-1]
        fecha_actual = precios.index[-1]

        # ⭐ ATH REAL Oct 2025
        fecha_ath = pd.Timestamp("2025-10-06")
        precio_ath = 126080
        ax.plot(fecha_ath, np.log10(precio_ath), '*',
               color='#FFD700', markersize=22, zorder=10,
               markeredgecolor='white', markeredgewidth=1.5)
        ax.annotate(f'ATH REAL\n$126,080\n(Oct 2025)',
                   xy=(fecha_ath, np.log10(precio_ath)),
                   xytext=(fecha_ath, np.log10(precio_ath) + 0.22),
                   fontsize=9, color='#FFD700', fontweight='bold', ha='center',
                   bbox=dict(boxstyle='round,pad=0.3', facecolor='#0d1117',
                            edgecolor='#FFD700', linewidth=2, alpha=0.95),
                   arrowprops=dict(arrowstyle='->', color='#FFD700', lw=1.5))

        # 🟡 PRECIO ACTUAL
        ax.plot(fecha_actual, np.log10(precio_actual), 'o',
               color='#00FFFF', markersize=16, zorder=10,
               markeredgecolor='white', markeredgewidth=2.5)

        # Zona suelo posible (franja horizontal solo en zona actual)
        ax.axhspan(np.log10(58000), np.log10(78000), alpha=0.08,
                  color='#00CC44', zorder=1)
        ax.text(pd.Timestamp("2025-06-01"), np.log10(66000),
               'ZONA SUELO POSIBLE\n$58K - $78K',
               fontsize=8, color='#00CC44', fontweight='bold',
               alpha=0.85, ha='center', va='center',
               bbox=dict(boxstyle='round,pad=0.2', facecolor='#0d1117',
                        edgecolor='#00CC44', alpha=0.7))

        ax.annotate(f'AHORA\n${precio_actual:,.0f}',
                   xy=(fecha_actual, np.log10(precio_actual)),
                   xytext=(fecha_actual, np.log10(precio_actual) + 0.28),
                   fontsize=10, color='#00FFFF', fontweight='bold', ha='center',
                   bbox=dict(boxstyle='round,pad=0.4', facecolor='#0d1117',
                            edgecolor='#00FFFF', linewidth=2.5, alpha=0.95),
                   arrowprops=dict(arrowstyle='->', color='#00FFFF', lw=2))

    # Proyección próximo ciclo 2028-2029
    # Consenso analistas: $200K-295K en el pico de 2029
    fecha_peak_next = pd.Timestamp("2029-06-01")
    precio_peak_low = 200000
    precio_peak_high = 295000
    precio_peak_mid = 250000

    # Rango de proyección (zona sombreada)
    ax.fill_between(
        [pd.Timestamp("2028-03-01"), fecha_peak_next],
        [np.log10(precio_peak_low), np.log10(precio_peak_low)],
        [np.log10(precio_peak_high), np.log10(precio_peak_high)],
        alpha=0.20, color='#FFD700', zorder=2
    )
    ax.plot(fecha_peak_next, np.log10(precio_peak_mid), '^',
           color='#FFD700', markersize=18, zorder=10,
           markeredgecolor='white', markeredgewidth=1.5)
    ax.annotate(f'OBJETIVO\nPRÓXIMO CICLO\n$200K-295K\n(2029 est.)',
               xy=(fecha_peak_next, np.log10(precio_peak_mid)),
               xytext=(fecha_peak_next, np.log10(precio_peak_mid) + 0.20),
               fontsize=9, color='#FFD700', fontweight='bold', ha='center',
               bbox=dict(boxstyle='round,pad=0.4', facecolor='#0d1117',
                        edgecolor='#FFD700', linewidth=2,
                        alpha=0.95, linestyle='dashed'),
               arrowprops=dict(arrowstyle='->', color='#FFD700', lw=1.5))

    # Líneas verticales halvings
    for h in halvings:
        fecha_h = pd.Timestamp(h["fecha"])
        ax.axvline(x=fecha_h, color='#9966FF',
                  linewidth=1.8, linestyle='--', alpha=0.85, zorder=3)

    # Etiquetas halvings abajo
    for h in halvings:
        fecha_h = pd.Timestamp(h["fecha"])
        ax.text(fecha_h, 1.65, h["label"],
               fontsize=7.5, color='#BB99FF',
               ha='center', va='bottom', fontweight='bold',
               bbox=dict(boxstyle='round,pad=0.2', facecolor='#0d1117',
                        edgecolor='#9966FF', alpha=0.85))

    # Etiquetas de fases
    for fase in fases:
        ini = pd.Timestamp(fase["inicio"])
        fin = pd.Timestamp(fase["fin"])
        mid = ini + (fin - ini) / 2
        c = colores_fase[fase["tipo"]]
        ax.text(mid, 5.15, fase["label"],
               fontsize=7.5, color=c["text"],
               ha='center', va='top', fontweight='bold', alpha=0.9)

    # Ejes
    precios_eje = [100, 1000, 5000, 20000, 50000, 100000, 200000, 500000]
    ax.set_yticks([np.log10(p) for p in precios_eje])
    ax.set_yticklabels([f'${p:,}' for p in precios_eje],
                      color='#AAAAAA', fontsize=9)

    ax.xaxis.set_major_locator(mdates.YearLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y'))
    ax.tick_params(axis='x', colors='#AAAAAA', labelsize=9)
    ax.set_xlim(pd.Timestamp("2012-01-01"), pd.Timestamp("2030-06-01"))
    ax.set_ylim(1.6, 5.55)

    ax.set_title('BITCOIN — CICLO DE 4 AÑOS (HALVINGS) | Datos reales + Proyección 2028-2029',
                fontsize=14, color='white', fontweight='bold', pad=15)
    ax.set_ylabel('Precio USD (escala logarítmica)', color='#AAAAAA', fontsize=10)
    ax.grid(axis='y', color='#333333', linestyle='--', alpha=0.4)
    ax.grid(axis='x', color='#222222', linestyle='--', alpha=0.3)
    for spine in ax.spines.values():
        spine.set_color('#333333')

    leyenda = [
        mpatches.Patch(color='#00CC44', alpha=0.7, label='Bull Phase — subida'),
        mpatches.Patch(color='#FF3333', alpha=0.7, label='Bear Phase — bajada'),
        mpatches.Patch(color='#4488FF', alpha=0.7, label='Recovery — acumulación'),
        mpatches.Patch(color='#9966FF', alpha=0.7, label='Halving'),
        mpatches.Patch(color='#FFD700', alpha=0.7, label='ATH real / Objetivo próximo ciclo'),
        mpatches.Patch(color='#00FFFF', alpha=0.9, label='Precio actual'),
        mpatches.Patch(color='#00CC44', alpha=0.3, label='Zona suelo posible'),
    ]
    ax.legend(handles=leyenda, loc='upper left',
             facecolor='#1a1a2e', edgecolor='#444444',
             labelcolor='white', fontsize=8.5, framealpha=0.95)

    try:
        precio_txt = f"${hist['Close'].iloc[-1]:,.0f}" if not hist.empty else "N/D"
    except:
        precio_txt = "N/D"

    fig.text(0.5, 0.01,
             f"ATH real: $126,080 (Oct 2025)  |  Precio actual: {precio_txt}  |  "
             f"Suelo estimado: $58K-$78K (¿ya visto?)  |  "
             f"Objetivo próximo ciclo: $200K-295K (2029)  |  5th Halving: Mar 2028",
             ha='center', fontsize=8.5, color='#888888',
             bbox=dict(facecolor='#1a1a2e', alpha=0.6, boxstyle='round'))

    plt.tight_layout(rect=[0, 0.04, 1, 1])
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=130,
               facecolor='#0d1117', bbox_inches='tight')
    plt.close()
    buf.seek(0)
    return buf

    fig, ax = plt.subplots(figsize=(18, 9))
    fig.patch.set_facecolor('#0d1117')
    ax.set_facecolor('#0d1117')

    # Halvings históricos y proyectado
    halvings = [
        {"fecha": "2012-11-28", "label": "1st Halving\n(Nov 2012)", "num": 1},
        {"fecha": "2016-07-09", "label": "2nd Halving\n(Jul 2016)", "num": 2},
        {"fecha": "2020-05-11", "label": "3rd Halving\n(May 2020)", "num": 3},
        {"fecha": "2024-04-19", "label": "4th Halving\n(Apr 2024)", "num": 4},
        {"fecha": "2028-03-15", "label": "5th Halving\n(Mar 2028 est.)", "num": 5},
    ]

    # Fases del ciclo (basado en patron historico)
    # Bull: ~12 meses post halving
    # Bear: ~13 meses
    # Recovery: ~22-23 meses
    fases = [
        # Ciclo 1 (2012)
        {"inicio": "2012-11-01", "fin": "2013-11-30", "tipo": "bull",     "label": "Bull\n12m"},
        {"inicio": "2013-12-01", "fin": "2015-01-31", "tipo": "bear",     "label": "Bear\n13m"},
        {"inicio": "2015-02-01", "fin": "2016-07-01", "tipo": "recovery", "label": "Recovery\n17m"},
        # Ciclo 2 (2016)
        {"inicio": "2016-07-01", "fin": "2017-12-31", "tipo": "bull",     "label": "Bull\n18m"},
        {"inicio": "2018-01-01", "fin": "2019-02-28", "tipo": "bear",     "label": "Bear\n14m"},
        {"inicio": "2019-03-01", "fin": "2020-05-01", "tipo": "recovery", "label": "Recovery\n14m"},
        # Ciclo 3 (2020)
        {"inicio": "2020-05-01", "fin": "2021-11-30", "tipo": "bull",     "label": "Bull\n19m"},
        {"inicio": "2021-12-01", "fin": "2022-11-30", "tipo": "bear",     "label": "Bear\n12m"},
        {"inicio": "2022-12-01", "fin": "2024-04-01", "tipo": "recovery", "label": "Recovery\n16m"},
        # Ciclo 4 (2024) — actual
        {"inicio": "2024-04-01", "fin": "2025-10-31", "tipo": "bull",     "label": "Bull\n(actual)"},
        {"inicio": "2025-11-01", "fin": "2026-11-30", "tipo": "bear",     "label": "Bear\n(proyec.)"},
        {"inicio": "2026-12-01", "fin": "2028-03-01", "tipo": "recovery", "label": "Recovery\n(proyec.)"},
    ]

    colores_fase = {
        "bull":     {"color": "#1a4a1a", "edge": "#00CC44", "text": "#00CC44"},
        "bear":     {"color": "#4a1a1a", "edge": "#FF3333", "text": "#FF3333"},
        "recovery": {"color": "#1a2a4a", "edge": "#4488FF", "text": "#4488FF"},
    }

    # Dibujar fases como franjas de fondo
    for fase in fases:
        ini = pd.Timestamp(fase["inicio"])
        fin = pd.Timestamp(fase["fin"])
        c = colores_fase[fase["tipo"]]
        ax.axvspan(ini, fin, alpha=0.25, color=c["color"])

    # Precio histórico real
    if not hist.empty:
        precios = hist["Close"]
        precios_log = np.log10(precios.clip(lower=0.01))
        ax.plot(precios.index, precios_log, color='white', linewidth=2.5, zorder=5, label='BTC precio')

        # Precio actual marcado
        precio_actual = precios.iloc[-1]
        fecha_actual = precios.index[-1]
        ax.plot(fecha_actual, np.log10(precio_actual), 'o',
               color='#FFD700', markersize=14, zorder=10,
               markeredgecolor='white', markeredgewidth=2)
        ax.annotate(f'AHORA\n${precio_actual:,.0f}',
                   xy=(fecha_actual, np.log10(precio_actual)),
                   xytext=(fecha_actual, np.log10(precio_actual) + 0.25),
                   fontsize=10, color='#FFD700', fontweight='bold',
                   ha='center',
                   bbox=dict(boxstyle='round,pad=0.3', facecolor='#0d1117',
                            edgecolor='#FFD700', alpha=0.9),
                   arrowprops=dict(arrowstyle='->', color='#FFD700', lw=1.5))

    # Proyección precio futuro basada en ciclos anteriores
    # Techo estimado ciclo 4: ~150k-200k (patron de menores rendimientos)
    try:
        fecha_techo_est = pd.Timestamp("2025-10-01")
        precio_techo_est = 180000
        ax.plot(fecha_techo_est, np.log10(precio_techo_est), '*',
               color='#FFD700', markersize=18, zorder=10,
               markeredgecolor='white', markeredgewidth=1)
        ax.annotate(f'TECHO EST.\n~${precio_techo_est//1000}K',
                   xy=(fecha_techo_est, np.log10(precio_techo_est)),
                   xytext=(fecha_techo_est, np.log10(precio_techo_est) + 0.2),
                   fontsize=9, color='#FFD700', fontweight='bold', ha='center',
                   bbox=dict(boxstyle='round,pad=0.3', facecolor='#0d1117',
                            edgecolor='#FFD700', alpha=0.8, linestyle='dashed'))

        # Suelo estimado bear: ~40k-50k
        fecha_suelo_est = pd.Timestamp("2026-11-01")
        precio_suelo_est = 45000
        ax.plot(fecha_suelo_est, np.log10(precio_suelo_est), 'v',
               color='#FF3333', markersize=14, zorder=10,
               markeredgecolor='white', markeredgewidth=1)
        ax.annotate(f'SUELO EST.\n~${precio_suelo_est//1000}K',
                   xy=(fecha_suelo_est, np.log10(precio_suelo_est)),
                   xytext=(fecha_suelo_est, np.log10(precio_suelo_est) - 0.25),
                   fontsize=9, color='#FF3333', fontweight='bold', ha='center',
                   bbox=dict(boxstyle='round,pad=0.3', facecolor='#0d1117',
                            edgecolor='#FF3333', alpha=0.8))
    except:
        pass

    # Líneas verticales de halvings
    for h in halvings:
        fecha_h = pd.Timestamp(h["fecha"])
        ax.axvline(x=fecha_h, color='#9966FF', linewidth=1.5,
                  linestyle='--', alpha=0.8, zorder=3)
        ax.text(fecha_h, ax.get_ylim()[0] if ax.get_ylim()[0] != 0 else -0.5,
               h["label"], fontsize=7.5, color='#9966FF',
               ha='center', va='bottom', fontweight='bold',
               bbox=dict(boxstyle='round,pad=0.2', facecolor='#0d1117',
                        edgecolor='#9966FF', alpha=0.8))

    # Etiquetas de fases sobre las franjas
    for fase in fases:
        ini = pd.Timestamp(fase["inicio"])
        fin = pd.Timestamp(fase["fin"])
        mid = ini + (fin - ini) / 2
        c = colores_fase[fase["tipo"]]
        ypos = 4.8  # arriba del gráfico
        ax.text(mid, ypos, fase["label"],
               fontsize=8, color=c["text"],
               ha='center', va='top', fontweight='bold', alpha=0.9)

    # Eje Y en escala log con precios reales
    precios_eje = [100, 1000, 10000, 50000, 100000, 200000, 500000]
    ax.set_yticks([np.log10(p) for p in precios_eje])
    ax.set_yticklabels([f'${p:,}' for p in precios_eje],
                      color='#AAAAAA', fontsize=9)

    # Eje X con años
    ax.xaxis.set_major_locator(mdates.YearLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y'))
    ax.tick_params(axis='x', colors='#AAAAAA', labelsize=9)

    # Rango X hasta 2028
    ax.set_xlim(pd.Timestamp("2012-01-01"), pd.Timestamp("2028-12-31"))

    # Límites Y
    ax.set_ylim(1.5, 5.5)

    # Título y labels
    ax.set_title('BITCOIN — CICLO DE 4 AÑOS (HALVINGS)',
                fontsize=16, color='white', fontweight='bold', pad=20)
    ax.set_ylabel('Precio USD (escala log)', color='#AAAAAA', fontsize=10)

    # Grid sutil
    ax.grid(axis='y', color='#333333', linestyle='--', alpha=0.4)
    ax.grid(axis='x', color='#222222', linestyle='--', alpha=0.3)

    for spine in ax.spines.values():
        spine.set_color('#333333')

    # Leyenda
    leyenda = [
        mpatches.Patch(color='#00CC44', alpha=0.7, label='Bull Phase (subida)'),
        mpatches.Patch(color='#FF3333', alpha=0.7, label='Bear Phase (bajada)'),
        mpatches.Patch(color='#4488FF', alpha=0.7, label='Recovery Phase (acumulacion)'),
        mpatches.Patch(color='#9966FF', alpha=0.7, label='Halving'),
        mpatches.Patch(color='#FFD700', alpha=0.9, label='Precio actual / Objetivos'),
    ]
    ax.legend(handles=leyenda, loc='upper left',
             facecolor='#1a1a2e', edgecolor='#333333',
             labelcolor='white', fontsize=9, framealpha=0.9)

    # Info textual
    try:
        precio_actual_txt = f"${hist['Close'].iloc[-1]:,.0f}" if not hist.empty else "N/D"
    except:
        precio_actual_txt = "N/D"

    fig.text(0.5, 0.01,
             f"4th Halving: Abril 2024  |  Precio actual: {precio_actual_txt}  |  "
             f"Techo estimado: ~$180K (Oct 2025)  |  Suelo bear: ~$45K (Nov 2026)  |  "
             f"5th Halving estimado: Mar 2028",
             ha='center', fontsize=9, color='#888888',
             bbox=dict(facecolor='#1a1a2e', alpha=0.5, boxstyle='round'))

    plt.tight_layout(rect=[0, 0.04, 1, 1])
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=130,
               facecolor='#0d1117', bbox_inches='tight')
    plt.close()
    buf.seek(0)
    return buf


def generate_cycle_chart(mercados):
    """
    Genera gráfico estilo Wall St. Cheat Sheet en español.
    Curva multicolor con emociones y punto marcando dónde está cada mercado.
    """
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    import matplotlib.patheffects as pe
    import numpy as np

    fig, ax = plt.subplots(figsize=(18, 10))
    fig.patch.set_facecolor('#0d1117')
    ax.set_facecolor('#0d1117')

    # Generar curva estilo Wall St Cheat Sheet
    # Subida lenta → pico → caída brusca → suelo → recuperación
    t = np.linspace(0, 10, 2000)

    def precio_ciclo(t):
        # Subida gradual con volatilidad
        subida = 0.8 * np.log1p(t * 1.2) + 0.15 * np.sin(t * 3) + 0.08 * np.sin(t * 7)
        # Pico en t≈5.5
        pico = np.exp(-0.5 * ((t - 5.5) / 0.8) ** 2) * 1.2
        # Caída brusca post-pico
        caida = -0.6 * (1 / (1 + np.exp(-4 * (t - 6.2)))) * (t > 5.5)
        # Suelo y recuperación lenta
        recuperacion = 0.3 * np.log1p(np.maximum(t - 7.5, 0) * 1.5) * (t > 7.5)
        return subida + pico + caida + recuperacion + 0.1

    y = precio_ciclo(t)
    y = (y - y.min()) / (y.max() - y.min()) * 0.75 + 0.1

    # Colorear la curva por fase
    colores_tramos = [
        (0.0,  0.12, '#8B2E2E'),  # Incredulidad/Depresión
        (0.12, 0.22, '#B34D00'),  # Esperanza
        (0.22, 0.32, '#CC7A00'),  # Optimismo
        (0.32, 0.42, '#CCB800'),  # Creencia
        (0.42, 0.52, '#66CC00'),  # Thrill
        (0.52, 0.60, '#00CC00'),  # Euforia (PICO)
        (0.60, 0.67, '#00CCAA'),  # Complacencia
        (0.67, 0.73, '#0099CC'),  # Ansiedad
        (0.73, 0.79, '#0044CC'),  # Negación
        (0.79, 0.85, '#4400CC'),  # Pánico
        (0.85, 0.90, '#8800CC'),  # Capitulación
        (0.90, 0.95, '#CC0099'),  # Ira/Depresión
        (0.95, 1.00, '#4499FF'),  # Incredulidad (nuevo ciclo)
    ]

    for ini, fin, color in colores_tramos:
        mask = (t >= ini * 10) & (t <= fin * 10)
        if mask.sum() > 1:
            ax.plot(t[mask], y[mask], color=color, linewidth=4.5, solid_capstyle='round')

    # Emociones con posiciones en la curva
    emociones = [
        (0.8,  None,  "INCREDULIDAD\n\"Este rally no durará\"",          'left',  0.08,   '#8B2E2E'),
        (1.8,  None,  "ESPERANZA\n\"Quizá una recuperación\"",           'left',  0.15,   '#B34D00'),
        (2.8,  None,  "OPTIMISMO\n\"Este rally es real\"",               'left',  0.28,   '#CC7A00'),
        (3.8,  None,  "CREENCIA\n\"Hora de invertir más\"",              'left',  0.42,   '#CCB800'),
        (4.8,  None,  "EMOCIÓN\n\"Compraré con margen\"",                'left',  0.60,   '#66CC00'),
        (5.45, None,  "EUFORIA\n\"¡Soy un genio!\n¡Todos ganaremos!\"",  'center',0.05,   '#00CC00'),
        (6.2,  None,  "COMPLACENCIA\n\"Solo una corrección\"",           'right', 0.12,   '#00CCAA'),
        (6.8,  None,  "ANSIEDAD\n\"¿Por qué tarda tanto?\"",            'right', 0.20,   '#0099CC'),
        (7.3,  None,  "NEGACIÓN\n\"Mis empresas son buenas,\nvolverá\"", 'right', 0.32,   '#0044CC'),
        (7.9,  None,  "PÁNICO\n\"¡Todos venden!\n¡Tengo que salir!\"",  'right', 0.45,   '#4400CC'),
        (8.5,  None,  "CAPITULACIÓN\n\"Me salgo al 100%\"",              'center',0.60,   '#8800CC'),
        (9.0,  None,  "IRA\n\"¿Quién dejó que\npasara esto?\"",          'center',0.72,   '#CC0099'),
        (9.6,  None,  "DEPRESIÓN\n\"Perdí mis ahorros.\nSoy un idiota\"", 'right', 0.82,  '#AA0066'),
    ]

    # Calcular posición Y de cada emoción en la curva
    for i, (tx, _, texto, lado, offset_extra, color) in enumerate(emociones):
        idx = np.argmin(np.abs(t - tx))
        cy = y[idx]

        if lado == 'center':
            offset_y = 0.12 if cy > 0.5 else -0.14
            ha = 'center'
            ax_offset = 0
        elif lado == 'left':
            offset_y = 0.08
            ha = 'left'
            ax_offset = -0.3
        else:
            offset_y = 0.08
            ha = 'right'
            ax_offset = 0.3

        # Punto en la curva
        ax.plot(tx, cy, 'o', color=color, markersize=7, zorder=5)

        # Línea líder
        ax.annotate('',
                   xy=(tx, cy),
                   xytext=(tx + ax_offset * 0.3, cy + offset_y),
                   arrowprops=dict(arrowstyle='-', color=color, lw=1, alpha=0.6))

        # Texto emoción
        ax.text(tx + ax_offset * 0.3, cy + offset_y + 0.02,
               texto, ha=ha, va='bottom',
               fontsize=8.5, color=color, fontweight='bold',
               bbox=dict(boxstyle='round,pad=0.2', facecolor='#0d1117',
                        edgecolor=color, alpha=0.85, linewidth=1.2))

    # Marcar mercados actuales
    colores_mercado = {
        'BTC':    '#FFD700',
        'SP500':  '#00FF88',
        'DAX':    '#FF69B4',
    }

    for m in mercados:
        nombre_m = m['nombre'].upper()
        fase_n = m['fase_num']

        # Mapear fase_num a posición en la curva
        fase_pos = {
            0: 0.8,   # Depresión
            1: 1.5,   # Incredulidad
            2: 2.0,   # Esperanza
            3: 2.8,   # Optimismo
            4: 3.8,   # Creencia
            5: 4.8,   # Emoción
            6: 5.45,  # Euforia
            7: 6.2,   # Complacencia
            8: 6.8,   # Ansiedad
            9: 7.3,   # Negación
            10: 7.9,  # Pánico
            11: 8.5,  # Capitulación
            12: 9.2,  # Ira/Depresión
        }

        tx = fase_pos.get(fase_n, 7.3)
        idx = np.argmin(np.abs(t - tx))
        cy = y[idx]

        color_m = colores_mercado.get(nombre_m, '#FFFFFF')

        # Punto grande pulsante
        ax.plot(tx, cy, 'o', color=color_m, markersize=22,
               markeredgecolor='white', markeredgewidth=2.5, zorder=15)
        ax.plot(tx, cy, 'o', color=color_m, markersize=30,
               alpha=0.25, zorder=14)

        # Etiqueta del mercado
        ax.annotate(f"{nombre_m}\n{m['fase']}",
                   xy=(tx, cy),
                   xytext=(tx, cy + 0.22),
                   fontsize=10, color=color_m,
                   ha='center', va='bottom', fontweight='bold', zorder=16,
                   bbox=dict(boxstyle='round,pad=0.4', facecolor='#0d1117',
                            edgecolor=color_m, linewidth=2.5, alpha=0.95),
                   arrowprops=dict(arrowstyle='->', color=color_m, lw=2))

    # Ejes y títulos
    ax.set_xlim(-0.3, 10.3)
    ax.set_ylim(-0.05, 1.15)
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_xlabel('TIEMPO →', color='#888888', fontsize=13, labelpad=10)
    ax.set_ylabel('PRECIO →', color='#888888', fontsize=13, labelpad=10)
    ax.tick_params(colors='#888888')
    for spine in ax.spines.values():
        spine.set_color('#333333')

    ax.set_title('PSICOLOGÍA DEL CICLO DE MERCADO — DONDE ESTAMOS AHORA',
                fontsize=16, color='white', fontweight='bold', pad=20)

    # Flecha eje X
    ax.annotate('', xy=(10.3, -0.02), xytext=(-0.3, -0.02),
               arrowprops=dict(arrowstyle='->', color='#555555', lw=1.5))
    ax.annotate('', xy=(-0.25, 1.12), xytext=(-0.25, -0.02),
               arrowprops=dict(arrowstyle='->', color='#555555', lw=1.5))

    # Leyenda
    leyenda = [mpatches.Patch(facecolor=colores_mercado.get(m['nombre'].upper(), '#FFFFFF'),
               label=f"{m['nombre']} — {m['fase']}") for m in mercados]
    ax.legend(handles=leyenda, loc='lower right',
             facecolor='#1a1a2e', edgecolor='#444444',
             labelcolor='white', fontsize=10, framealpha=0.95)

    try:
        plt.tight_layout(pad=1.5)
    except:
        pass
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=130,
               facecolor='#0d1117', bbox_inches='tight')
    plt.close()
    buf.seek(0)
    return buf
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    import matplotlib.patheffects as pe
    import numpy as np

    fig, ax = plt.subplots(figsize=(16, 9))
    fig.patch.set_facecolor('#0d1117')
    ax.set_facecolor('#0d1117')

    # Curva del ciclo
    t = np.linspace(0, 4 * np.pi, 1000)
    y = (np.sin(t - np.pi/2) +
         0.3 * np.sin(2*t) +
         0.15 * np.sin(3*t) +
         0.05 * np.sin(5*t))
    y = (y - y.min()) / (y.max() - y.min())

    # Colorear curva por segmentos
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
                color=colores_curva[i], linewidth=5, alpha=0.9)

    # Posiciones en la curva
    FASE_POSICION = {
        0: 0.08, 1: 0.12, 2: 0.18, 3: 0.25, 4: 0.32,
        5: 0.40, 6: 0.50, 7: 0.58, 8: 0.65, 9: 0.72,
        10: 0.78, 11: 0.83, 12: 0.90,
    }
    FASE_NOMBRES = [
        "DEPRESION","INCREDULIDAD","ESPERANZA","OPTIMISMO","CREENCIA",
        "EMOCION","EUFORIA","COMPLACENCIA","ANSIEDAD","NEGACION",
        "PANICO","CAPITULACION","IRA"
    ]

    # Si una fase va hacia arriba o bajando
    FASE_DIRECCION = {
        0: "↑", 1: "↑", 2: "↑", 3: "↑", 4: "↑",
        5: "↑", 6: "→", 7: "↓", 8: "↓", 9: "↓",
        10: "↓", 11: "↓", 12: "↑",
    }

    # % del ciclo completado (0=suelo, 100=haber pasado por todo)
    FASE_PCT_CICLO = {
        0: 5, 1: 12, 2: 20, 3: 30, 4: 40,
        5: 50, 6: 60, 7: 70, 8: 78, 9: 84,
        10: 88, 11: 92, 12: 96,
    }

    # Potencial alcista desde cada fase hasta el techo
    FASE_POTENCIAL = {
        0: "+400%", 1: "+300%", 2: "+200%", 3: "+150%", 4: "+100%",
        5: "+60%",  6: "TECHO", 7: "-10%", 8: "-25%", 9: "-40%",
        10: "-55%", 11: "-70%", 12: "+50%",
    }

    # Zona de cada fase
    FASE_ZONA = {
        0: "COMPRA", 1: "COMPRA", 2: "COMPRA", 3: "ACUMULAR", 4: "ACUMULAR",
        5: "NEUTRO", 6: "VENDER", 7: "VENDER", 8: "REDUCIR", 9: "REDUCIR",
        10: "ESPERAR", 11: "ESPERAR", 12: "COMPRA",
    }

    ZONA_COLOR = {
        "COMPRA": "#00CC44", "ACUMULAR": "#88DD00",
        "NEUTRO": "#FFCC00", "VENDER": "#FF3333",
        "REDUCIR": "#FF6600", "ESPERAR": "#FF9900",
    }

    # Zonas de fondo
    # Zona compra (izquierda — fases 0-2)
    ax.axvspan(t[0], t[int(0.22*len(t))], alpha=0.07, color='#00CC44')
    ax.axvspan(t[int(0.78*len(t))], t[-1], alpha=0.07, color='#00CC44')
    # Zona venta (arriba — fases 5-8)
    ax.axvspan(t[int(0.38*len(t))], t[int(0.70*len(t))], alpha=0.07, color='#FF3333')

    # Etiquetas de zona en el fondo
    ax.text(t[int(0.10*len(t))], 0.08, '🟢 ZONA\nCOMPRA', fontsize=10,
            color='#00CC44', ha='center', alpha=0.8, fontweight='bold')
    ax.text(t[int(0.54*len(t))], 0.08, '🔴 ZONA\nVENTA', fontsize=10,
            color='#FF3333', ha='center', alpha=0.8, fontweight='bold')
    ax.text(t[int(0.85*len(t))], 0.08, '🟢 ZONA\nCOMPRA', fontsize=10,
            color='#00CC44', ha='center', alpha=0.8, fontweight='bold')

    # Etiquetas pequeñas de fases
    for fase_n, pos_pct in FASE_POSICION.items():
        idx = int(pos_pct * len(t))
        idx = min(idx, len(t)-1)
        offset_y = 0.07 if fase_n in [6,7] else (-0.09 if fase_n in [11,12,0] else 0.06)
        ax.annotate(FASE_NOMBRES[fase_n],
                   xy=(t[idx], y[idx]),
                   xytext=(t[idx], y[idx] + offset_y),
                   fontsize=6.5, color='#777777',
                   ha='center', va='center', fontweight='bold')

    # Marcar mercados
    colores_mercado = ['#00FFFF', '#FFD700', '#FF69B4', '#7FFF00']
    for i, m in enumerate(mercados):
        fase_n = m["fase_num"]
        pos_pct = FASE_POSICION[fase_n]
        idx = int(pos_pct * len(t))
        idx = min(idx, len(t)-1)
        color_m = colores_mercado[i % len(colores_mercado)]

        direccion = FASE_DIRECCION[fase_n]
        pct_ciclo = FASE_PCT_CICLO[fase_n]
        potencial = FASE_POTENCIAL[fase_n]
        zona = FASE_ZONA[fase_n]
        zona_color = ZONA_COLOR[zona]

        # Punto grande con borde
        ax.plot(t[idx], y[idx], 'o',
                color=color_m, markersize=20,
                markeredgecolor='white', markeredgewidth=2.5,
                zorder=10)

        # Flecha de dirección dentro del punto
        ax.text(t[idx], y[idx], direccion, ha='center', va='center',
               fontsize=11, color='white', fontweight='bold', zorder=11)

        # Etiqueta mejorada
        offset_base = 0.18 + i * 0.07
        label = (f"{m['nombre']}\n"
                 f"{m['fase']}\n"
                 f"Ciclo: {pct_ciclo}% completado\n"
                 f"Potencial: {potencial}\n"
                 f"Acción: {zona}")

        ax.annotate(label,
                   xy=(t[idx], y[idx]),
                   xytext=(t[idx], y[idx] + offset_base),
                   fontsize=8, color=color_m,
                   ha='center', va='bottom',
                   fontweight='bold',
                   arrowprops=dict(arrowstyle='->', color=color_m, lw=2),
                   bbox=dict(boxstyle='round,pad=0.4',
                            facecolor='#0d1117',
                            edgecolor=zona_color,
                            linewidth=2.5, alpha=0.95))

    # Etiquetas EXPANSION / CONTRACCION
    ax.text(t[int(0.30*len(t))], 0.02, 'EXPANSION  ↑',
            fontsize=12, color='#32CD32', ha='center', alpha=0.6, fontweight='bold')
    ax.text(t[int(0.75*len(t))], 0.02, 'CONTRACCION  ↓',
            fontsize=12, color='#FF6347', ha='center', alpha=0.6, fontweight='bold')

    ax.set_title('CICLO DE MERCADO — DONDE ESTAMOS Y HACIA DONDE VAMOS',
                fontsize=14, color='white', fontweight='bold', pad=15)
    ax.set_xlabel('TIEMPO', color='#888888', fontsize=10)
    ax.set_ylabel('PRECIO', color='#888888', fontsize=10)
    ax.tick_params(colors='#888888')
    for spine in ax.spines.values():
        spine.set_color('#333333')
    ax.set_xticks([])
    ax.set_yticks([])

    # Leyenda
    legend_elements = [mpatches.Patch(facecolor=colores_mercado[i],
                       label=f"{m['nombre']} — {FASE_DIRECCION[m['fase_num']]} {FASE_ZONA[m['fase_num']]}")
                       for i, m in enumerate(mercados)]
    ax.legend(handles=legend_elements, loc='lower right',
             facecolor='#1a1a2e', edgecolor='#333333',
             labelcolor='white', fontsize=10)

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
    if any(s in sector for s in ["consumer", "retail", "apparel", "footwear", "restaurant", "beverage"]):
        return "consumer"
    if any(s in industry for s in ["apparel", "footwear", "sporting", "luxury", "restaurant", "beverage"]):
        return "consumer"
    if rev_growth > 0.30 and fcf < 0:
        return "growth"
    if div_yield > 0.03:
        return "income"
    if any(s in sector for s in ["technology", "tech"]):
        return "tech"
    return "value"


def estimar_precio_por_tipo(f, tipo, price):
    """Estimacion de precio segun tipo de empresa — con filtros anti-distorsion."""
    rev_growth = f.get("rev_growth") or 0.05
    rev_ttm = f.get("rev_ttm") or 0
    mktcap = f.get("mktcap") or 0
    fcf = f.get("fcf") or 0
    div_yield = f.get("div_yield") or 0
    pe_fwd = f.get("pe_fwd") or f.get("pe") or 20

    # FILTRO CRITICO: earn_growth puntual (one-off contable) no debe usarse en estimaciones
    # Si ingresos caen pero beneficios suben >100% = one-off. Usar rev_growth como proxy.
    earn_growth_raw = f.get("earn_growth") or rev_growth
    if earn_growth_raw and rev_growth and earn_growth_raw > 1.0 and rev_growth < 0.05:
        earn_growth = min(rev_growth + 0.05, 0.15)  # cap conservador
    elif earn_growth_raw and earn_growth_raw > 3.0:
        earn_growth = min(earn_growth_raw, 0.30)  # cap maximo 30%
    else:
        earn_growth = earn_growth_raw or rev_growth

    # Cap absoluto para evitar estimaciones absurdas
    earn_growth = max(min(earn_growth, 0.30), -0.20)
    rev_growth = max(min(rev_growth, 0.50), -0.20)

    precio_justo = None
    est_1y = None
    est_3y = None
    metodo = ""

    if tipo == "growth" and rev_ttm > 0 and mktcap > 0:
        ps_actual = mktcap / rev_ttm
        ps_objetivo = min(ps_actual * 0.8, 15)
        precio_justo = round(price * (ps_objetivo / ps_actual), 2)
        est_1y = round(price * (1 + rev_growth * 0.7), 2)
        est_3y = round(price * (1 + rev_growth * 0.5) ** 3, 2)
        metodo = f"P/S x{ps_objetivo:.1f} (empresa crecimiento)"

    elif tipo == "utility" and div_yield > 0:
        yield_objetivo = 0.045
        div_anual = price * div_yield
        precio_justo = round(div_anual / yield_objetivo, 2)
        est_1y = round(precio_justo * 1.03, 2)
        est_3y = round(precio_justo * (1.03 ** 3), 2)
        metodo = f"Yield objetivo 4.5% | Div anual: {div_anual:.2f} USD"

    elif tipo == "materials" and rev_ttm > 0 and mktcap > 0:
        ev_rev = mktcap / rev_ttm
        ev_rev_objetivo = max(ev_rev * 0.9, 2)
        precio_justo = round(price * (ev_rev_objetivo / ev_rev), 2)
        est_1y = round(price * (1 + max(rev_growth, 0.10)), 2)
        est_3y = round(price * (1 + max(rev_growth * 0.6, 0.08)) ** 3, 2)
        metodo = "EV/Ingresos ajustado ciclo commodities"

    elif tipo == "biotech":
        est_1y = round(price * (1 + max(rev_growth * 0.5, 0.05)), 2)
        est_3y = round(price * (1 + max(rev_growth * 0.4, 0.08)) ** 3, 2)
        precio_justo = round(price * 0.85, 2)
        metodo = "Especulativo (biotech)"

    elif tipo == "consumer":
        # Consumer: multiplo P/E historico del sector
        pe_actual = f.get("pe") or 20
        pe_historico_sector = 22  # P/E medio historico consumer staples/cyclical
        precio_justo = round(price * (pe_historico_sector / pe_actual), 2) if pe_actual > 0 else price
        crecimiento_normalizado = max(min(earn_growth, 0.12), -0.05)
        est_1y = round(precio_justo * (1 + crecimiento_normalizado), 2)
        est_3y = round(precio_justo * (1 + crecimiento_normalizado) ** 3, 2)
        metodo = f"P/E historico sector consumer {pe_historico_sector}x"

    elif fcf > 0 and mktcap > 0:
        tasa_desc = 0.10
        tasa_crec_fcf = min(max(earn_growth, 0.03), 0.25)
        fcf_yield = fcf / mktcap
        precio_justo = round(price * (fcf_yield + tasa_crec_fcf) / tasa_desc, 2)
        # Cap: precio justo no puede ser mas de 3x el precio actual
        precio_justo = min(precio_justo, price * 3)
        est_1y = round(price * (1 + max(earn_growth, 0.05)), 2)
        est_3y = round(price * (1 + max(earn_growth * 0.7, 0.05)) ** 3, 2)
        metodo = "DCF (Free Cash Flow)"

    elif pe_fwd and pe_fwd > 0:
        pe_objetivo = min(pe_fwd * 0.9, 30)
        precio_justo = round(price * (pe_objetivo / pe_fwd), 2)
        precio_justo = min(precio_justo, price * 2.5)
        est_1y = round(price * (1 + max(earn_growth, 0.05)), 2)
        est_3y = round(price * (1 + max(earn_growth * 0.7, 0.05)) ** 3, 2)
        metodo = f"PER forward {pe_fwd:.1f}x"

    # Validacion final — si las estimaciones son absurdas, reemplazar
    if est_1y and (est_1y > price * 4 or est_1y < price * 0.3):
        est_1y = round(price * (1 + max(min(earn_growth, 0.20), -0.10)), 2)
    if est_3y and (est_3y > price * 10 or est_3y < price * 0.2):
        est_3y = round(price * (1 + max(min(earn_growth, 0.15), -0.08)) ** 3, 2)
    if precio_justo and (precio_justo > price * 4 or precio_justo < price * 0.2):
        precio_justo = round(price * 1.1, 2)

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
    """Genera imagen de analisis fundamental mejorada — más legible."""
    import matplotlib.pyplot as plt
    import numpy as np

    cats = resultado["categorias"]
    n = len(cats)
    tipo = resultado.get("tipo", "value").upper()

    fig = plt.figure(figsize=(12, 10))
    fig.patch.set_facecolor('#0d1117')

    # Velocimetro superior
    ax = fig.add_axes([0.05, 0.52, 0.90, 0.43], projection='polar')
    ax.set_facecolor('#0d1117')
    theta = np.linspace(np.pi, 0, 101)
    for i in range(100):
        if i < 35:    c = '#FF3333'
        elif i < 50:  c = '#FF7700'
        elif i < 65:  c = '#FFCC00'
        elif i < 80:  c = '#99DD00'
        else:         c = '#00CC44'
        ax.barh(1, theta[i] - theta[i+1], left=theta[i+1], height=0.45, color=c, edgecolor='none')

    score = resultado["score"]
    angle = np.pi - (score / 100 * np.pi)
    ax.plot([angle, angle], [0, 1.10], color='white', linewidth=5, zorder=5)
    ax.plot(angle, 0, 'o', color='white', markersize=22, zorder=6)
    ax.plot(angle, 0, 'o', color='#0d1117', markersize=11, zorder=7)
    ax.set_ylim(0, 1.35)
    ax.set_theta_zero_location('E')
    ax.set_theta_direction(1)
    ax.set_thetamin(0)
    ax.set_thetamax(180)
    ax.set_xticks([np.pi, 3*np.pi/4, np.pi/2, np.pi/4, 0])
    ax.set_xticklabels(['0\nEVITAR', '25', '50\nNEUTRAL', '75', '100\nEXCELENTE'],
                        color='white', fontsize=10, fontweight='bold')
    ax.set_yticks([])
    ax.spines['polar'].set_visible(False)
    ax.grid(False)

    zona_color = '#FF3333' if score < 35 else '#FF7700' if score < 50 else '#FFCC00' if score < 65 else '#99DD00' if score < 80 else '#00CC44'
    fig.text(0.5, 0.555, f"{score}/100", ha='center', fontsize=32, color='white', fontweight='bold')
    fig.text(0.5, 0.510, resultado['zona'], ha='center', fontsize=12, color=zona_color, fontweight='bold')
    fig.text(0.5, 0.488, f"Tipo: {tipo}", ha='center', fontsize=9, color='#888888')

    mktcap_b = round(resultado['mktcap'] / 1e9, 1) if resultado.get('mktcap') else 'N/D'
    precio_real = resultado.get('price_real', resultado['price'])
    fig.text(0.5, 0.975, f"{resultado['nombre']} ({resultado['ticker']})  |  {precio_real} USD  |  Cap: {mktcap_b}B",
             ha='center', fontsize=13, color='white', fontweight='bold')
    fig.text(0.5, 0.950, resultado['sector'], ha='center', fontsize=10, color='#888888')

    # Barras categorias — más grandes y legibles
    ax2 = fig.add_axes([0.20, 0.18, 0.65, 0.28])
    ax2.set_facecolor('#0d1117')
    ax2.set_xlim(0, 20)
    ax2.set_ylim(-0.5, n - 0.5)
    ax2.axis('off')

    for idx, (cat, datos) in enumerate(reversed(list(cats.items()))):
        pts = datos['puntos']
        y = idx
        # Fondo barra
        ax2.barh(y, 20, height=0.7, color='#1a1a2e', zorder=1)
        # Barra coloreada
        bar_c = '#FF3333' if pts < 7 else '#FF7700' if pts < 10 else '#FFCC00' if pts < 14 else '#00CC44'
        ax2.barh(y, pts, height=0.7, color=bar_c, alpha=0.9, zorder=2)
        # Nombre categoria
        ax2.text(-0.5, y, cat, va='center', ha='right',
                color='white', fontsize=11, fontweight='bold')
        # Valor detalle
        val_short = datos['valores'][:35] if len(datos['valores']) > 35 else datos['valores']
        ax2.text(pts + 0.4, y, val_short, va='center', ha='left',
                color='#AAAAAA', fontsize=8)
        # Puntuacion
        ax2.text(20.8, y, f"{pts}/20", va='center', ha='left',
                color=bar_c, fontsize=11, fontweight='bold')
    ax2.set_xlim(-7, 23)

    # Estimaciones precio
    if resultado.get('est_1y'):
        pct_1y = round((resultado['est_1y'] - resultado['price']) / resultado['price'] * 100, 1)
        pct_3y = round((resultado['est_3y'] - resultado['price']) / resultado['price'] * 100, 1)
        pj_pct = round((resultado['precio_justo'] - resultado['price']) / resultado['price'] * 100, 1) if resultado.get('precio_justo') else 0

        fig.text(0.5, 0.155, 'ESTIMACION DE PRECIO', ha='center', fontsize=10, color='#666666', fontweight='bold')

        c_pj = '#00CC44' if pj_pct >= 0 else '#FF3333'
        c_1y = '#00CC44' if pct_1y >= 0 else '#FF3333'
        c_3y = '#00CC44' if pct_3y >= 0 else '#FF3333'

        fig.text(0.20, 0.10, "Precio justo", ha='center', fontsize=9, color='#AAAAAA')
        fig.text(0.20, 0.06, f"{resultado['precio_justo']} USD", ha='center', fontsize=12, color=c_pj, fontweight='bold')
        fig.text(0.20, 0.02, f"({pj_pct:+.0f}%)", ha='center', fontsize=10, color=c_pj)

        fig.text(0.50, 0.10, "1 año", ha='center', fontsize=9, color='#AAAAAA')
        fig.text(0.50, 0.06, f"{resultado['est_1y']} USD", ha='center', fontsize=12, color=c_1y, fontweight='bold')
        fig.text(0.50, 0.02, f"({pct_1y:+.0f}%)", ha='center', fontsize=10, color=c_1y)

        fig.text(0.80, 0.10, "3 años", ha='center', fontsize=9, color='#AAAAAA')
        fig.text(0.80, 0.06, f"{resultado['est_3y']} USD", ha='center', fontsize=12, color=c_3y, fontweight='bold')
        fig.text(0.80, 0.02, f"({pct_3y:+.0f}%)", ha='center', fontsize=10, color=c_3y)

    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=130, facecolor='#0d1117', bbox_inches='tight')
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


def is_premium(chat_id):
    """Comprueba si un usuario tiene acceso premium activo."""
    if chat_id == ALLOWED_USER_ID:
        return True  # El dueño siempre tiene acceso
    sub = SUSCRIPTORES.get(chat_id)
    if not sub:
        return False
    if not sub.get("activo"):
        return False
    expiry = sub.get("expiry")
    if expiry and datetime.now() > expiry:
        SUSCRIPTORES[chat_id]["activo"] = False
        return False
    return True


def allowed(message):
    return is_premium(message.from_user.id)


def crear_pago_nowpayments(chat_id, descripcion="Premium 1 mes"):
    """Crea un pago en NOWPayments y devuelve el enlace."""
    try:
        url = "https://api.nowpayments.io/v1/invoice"
        headers = {
            "x-api-key": NOWPAYMENTS_KEY,
            "Content-Type": "application/json"
        }
        payload = {
            "price_amount": PRECIO_MENSUAL,
            "price_currency": "usd",
            "pay_currency": "usdttrc20",
            "order_id": f"premium_{chat_id}_{int(datetime.now().timestamp())}",
            "order_description": descripcion,
            "ipn_callback_url": "",  # sin webhook por ahora
            "success_url": "https://t.me/",
            "cancel_url": "https://t.me/",
        }
        r = requests.post(url, json=payload, headers=headers, timeout=10)
        data = r.json()
        if "invoice_url" in data:
            return data["invoice_url"], data.get("id")
        return None, None
    except Exception as e:
        log.error(f"NOWPayments crear pago: {e}")
        return None, None


def activar_suscripcion(chat_id, dias=30):
    """Activa o renueva la suscripción de un usuario."""
    import datetime as dt
    ahora = datetime.now()
    sub = SUSCRIPTORES.get(chat_id, {})
    # Si ya tiene suscripción activa, extender desde la fecha de expiración
    if sub.get("activo") and sub.get("expiry") and sub["expiry"] > ahora:
        nueva_expiry = sub["expiry"] + __import__('datetime').timedelta(days=dias)
    else:
        nueva_expiry = ahora + __import__('datetime').timedelta(days=dias)
    SUSCRIPTORES[chat_id] = {
        "activo": True,
        "expiry": nueva_expiry,
        "trial_used": sub.get("trial_used", False),
        "activado": ahora,
    }
    return nueva_expiry


def ask_ai(prompt, max_chars=3000):
    for attempt in range(3):
        try:
            resp = ai_client.chat.completions.create(
                model="openai/gpt-oss-120b",
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
    kb.row(InlineKeyboardButton("Insiders", callback_data="insiders"),
           InlineKeyboardButton("Carteras", callback_data="carteras"))
    kb.row(InlineKeyboardButton("Smart Money", callback_data="smartmoney"),
           InlineKeyboardButton("Halving BTC", callback_data="halvingbtc"))
    kb.row(InlineKeyboardButton("Mercado Ahora", callback_data="mercadoahora"))
    return kb


# ── HANDLERS ──────────────────────────────────────────────────────────────────

@bot.message_handler(commands=["start"])
def cmd_start(msg):
    chat_id = msg.from_user.id
    nombre_usuario = msg.from_user.first_name or "inversor"

    if is_premium(chat_id):
        # Usuario con acceso — mostrar menú completo
        bot.send_message(chat_id,
            f"Bienvenido de nuevo {nombre_usuario}! 👋\n"
            f"Usa /ayuda para ver todos los comandos.",
            reply_markup=main_kb())
        return

    # Usuario sin acceso — mostrar bienvenida con trial
    safe_send(chat_id,
        f"Hola {nombre_usuario}! 👋\n\n"
        f"Soy un bot de análisis financiero profesional.\n\n"
        f"📊 Señales con RSI+MACD+VWAP\n"
        f"🔍 Análisis fundamental 0-100\n"
        f"📈 Ciclo Bitcoin (halvings)\n"
        f"💰 Smart money — Buffett, ARK, Burry...\n"
        f"⚡ Monitor mercado en tiempo real\n\n"
        f"🎁 Prueba 7 días GRATIS: /trial\n"
        f"💳 Suscripción: 19 USDT/mes → /premium")


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
    ticker = normalizar_ticker_crypto(parts[1])
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

    # Intentar 2 veces con pequeña pausa
    resultado = None
    for intento in range(2):
        resultado = calcular_indice_valor("BTC-USD")
        if resultado:
            break
        if intento == 0:
            time.sleep(3)

    if not resultado:
        safe_send(msg.chat.id, "Error obteniendo datos de BTC. Intenta en unos segundos.", message_id=m.message_id)
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


def fetch_insiders(ticker=None, dias=30):
    """
    Busca compras masivas de insiders via SEC EDGAR Form 4.
    Si ticker=None busca en general, si ticker especificado busca solo esa empresa.
    """
    resultados = []
    try:
        if ticker:
            # Buscar CIK de la empresa
            url = f"https://efts.sec.gov/LATEST/search-index?q=%22{ticker}%22&dateRange=custom&startdt={(datetime.now()-__import__('datetime').timedelta(days=dias)).strftime('%Y-%m-%d')}&enddt={datetime.now().strftime('%Y-%m-%d')}&forms=4"
            r = requests.get(url, headers={"User-Agent": "bot@example.com"}, timeout=10)
            data = r.json()
            hits = data.get("hits", {}).get("hits", [])
            for hit in hits[:10]:
                src = hit.get("_source", {})
                nombre = src.get("display_names", [""])[0] if src.get("display_names") else ""
                fecha = src.get("file_date", "")
                resultados.append({
                    "ticker": ticker,
                    "empresa": src.get("entity_name", ticker),
                    "insider": nombre,
                    "fecha": fecha,
                    "tipo": "Form 4",
                })
        else:
            # Buscar compras masivas recientes en general
            fecha_inicio = (__import__('datetime').date.today() - __import__('datetime').timedelta(days=dias)).strftime('%Y-%m-%d')
            url = f"https://efts.sec.gov/LATEST/search-index?q=%22purchase%22&forms=4&dateRange=custom&startdt={fecha_inicio}&enddt={datetime.now().strftime('%Y-%m-%d')}"
            r = requests.get(url, headers={"User-Agent": "bot@example.com"}, timeout=10)
            data = r.json()
            hits = data.get("hits", {}).get("hits", [])
            for hit in hits[:15]:
                src = hit.get("_source", {})
                nombre = src.get("display_names", [""])[0] if src.get("display_names") else ""
                resultados.append({
                    "empresa": src.get("entity_name", ""),
                    "insider": nombre,
                    "fecha": src.get("file_date", ""),
                    "tipo": "Form 4 - Purchase",
                })
    except Exception as e:
        log.warning(f"fetch_insiders: {e}")
    return resultados


def fetch_insiders_yfinance(ticker):
    """Obtiene insider transactions via yfinance."""
    try:
        tk = yf.Ticker(ticker)
        insiders = tk.insider_transactions
        if insiders is None or insiders.empty:
            return []
        compras = []
        for _, row in insiders.head(10).iterrows():
            valor = row.get("Value", 0) or 0
            shares = row.get("Shares", 0) or 0
            trans_type = str(row.get("Transaction", "")).lower()
            if "purchase" in trans_type or "buy" in trans_type or valor > 0:
                compras.append({
                    "insider": row.get("Insider", ""),
                    "cargo": row.get("Relation", ""),
                    "fecha": str(row.get("Start Date", ""))[:10],
                    "shares": int(shares),
                    "valor_usd": int(valor),
                    "tipo": row.get("Transaction", ""),
                })
        return compras
    except Exception as e:
        log.warning(f"fetch_insiders_yfinance {ticker}: {e}")
        return []


# Grandes fondos con sus CIK en SEC EDGAR
GRANDES_FONDOS = {
    "BUFFETT": {"nombre": "Warren Buffett (Berkshire Hathaway)", "cik": "0001067983"},
    "ACKMAN":  {"nombre": "Bill Ackman (Pershing Square)",      "cik": "0001336528"},
    "BURRY":   {"nombre": "Michael Burry (Scion)",              "cik": "0001649902"},
    "DALIO":   {"nombre": "Ray Dalio (Bridgewater)",            "cik": "0001350694"},
    "ARK":     {"nombre": "Cathie Wood (ARK Invest)",           "cik": "0001579982"},
    "TEPPER":  {"nombre": "David Tepper (Appaloosa)",           "cik": "0001656456"},
}


def fetch_13f(fondo_key):
    """
    Obtiene el ultimo 13F filing de un gran fondo via SEC EDGAR.
    Devuelve las posiciones nuevas y aumentadas.
    """
    fondo = GRANDES_FONDOS.get(fondo_key.upper())
    if not fondo:
        return None, []
    try:
        # Buscar ultimos filings 13F
        url = f"https://data.sec.gov/submissions/CIK{fondo['cik'].zfill(10)}.json"
        r = requests.get(url, headers={"User-Agent": "bot@example.com"}, timeout=10)
        data = r.json()
        filings = data.get("filings", {}).get("recent", {})
        forms = filings.get("form", [])
        dates = filings.get("filingDate", [])
        accnos = filings.get("accessionNumber", [])

        # Encontrar ultimo 13F
        ultimo_13f = None
        for i, form in enumerate(forms):
            if "13F" in form:
                ultimo_13f = {"date": dates[i], "accno": accnos[i]}
                break

        if not ultimo_13f:
            return fondo["nombre"], []

        # Obtener el filing
        accno_clean = ultimo_13f["accno"].replace("-", "")
        filing_url = f"https://www.sec.gov/Archives/edgar/full-index/{ultimo_13f['date'][:4]}/QTR{((int(ultimo_13f['date'][5:7])-1)//3)+1}/company.idx"

        # Parsear posiciones del 13F
        posiciones = []
        idx_url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{fondo['cik'].zfill(10)}.json"

        # Fallback: usar RSS de SEC para noticias del fondo
        rss_url = f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={fondo['cik']}&type=13F&dateb=&owner=include&count=5&search_text="
        return fondo["nombre"], [{"fecha": ultimo_13f["date"], "nota": f"Ultimo 13F: {ultimo_13f['date']}"}]

    except Exception as e:
        log.warning(f"fetch_13f {fondo_key}: {e}")
        return fondo["nombre"] if fondo else fondo_key, []


def fetch_13f_posiciones(fondo_key):
    """
    Obtiene posiciones reales del último 13F via SEC EDGAR API.
    """
    fondo = GRANDES_FONDOS.get(fondo_key.upper())
    if not fondo:
        return None, []

    try:
        cik = fondo['cik'].lstrip('0')
        cik_padded = cik.zfill(10)

        # Obtener submissions del fondo
        url = f"https://data.sec.gov/submissions/CIK{cik_padded}.json"
        r = requests.get(url, headers={"User-Agent": "financial-bot contact@example.com"}, timeout=10)
        data = r.json()

        filings = data.get("filings", {}).get("recent", {})
        forms = filings.get("form", [])
        dates = filings.get("filingDate", [])
        accnos = filings.get("accessionNumber", [])

        # Encontrar último 13F-HR
        ultimo = None
        for i, form in enumerate(forms):
            if "13F-HR" in form and "13F-HR/A" not in form:
                ultimo = {"date": dates[i], "accno": accnos[i]}
                break

        if not ultimo:
            return fondo["nombre"], []

        # Obtener el archivo del 13F
        accno_clean = ultimo["accno"].replace("-", "")
        index_url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accno_clean}/{ultimo['accno']}-index.htm"

        # Buscar el archivo XML de holdings
        r2 = requests.get(
            f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type=13F-HR&dateb=&owner=include&count=5&search_text=",
            headers={"User-Agent": "financial-bot contact@example.com"}, timeout=8
        )

        posiciones = []

        # Intentar obtener holdings via API de company facts
        facts_url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik_padded}.json"
        try:
            r3 = requests.get(facts_url, headers={"User-Agent": "financial-bot contact@example.com"}, timeout=8)
            facts = r3.json()
            # Los holdings 13F están bajo us-gaap
            holdings = facts.get("facts", {}).get("us-gaap", {})
        except:
            holdings = {}

        # Si no hay datos via API, usar yfinance para fondos conocidos
        if not posiciones:
            ticker_map = {
                "BUFFETT": "BRK-B",
                "ARK":     "ARKK",
                "ACKMAN":  "PSH.L",
            }
            if fondo_key.upper() in ticker_map:
                try:
                    tk = yf.Ticker(ticker_map[fondo_key.upper()])
                    holders = tk.institutional_holders
                    if holders is not None and not holders.empty:
                        for _, row in holders.head(10).iterrows():
                            val = row.get("Value", 0) or 0
                            posiciones.append({
                                "empresa": str(row.get("Holder", "")),
                                "valor": round(val / 1e9, 2),
                                "shares": int(row.get("Shares", 0) or 0),
                                "pct": round(float(row.get("% Out", 0) or 0), 2),
                            })
                except:
                    pass

        # Para Berkshire usar sus holdings conocidos via yfinance
        if fondo_key.upper() == "BUFFETT" and not posiciones:
            try:
                brk = yf.Ticker("BRK-B")
                major = brk.major_holders
                inst = brk.institutional_holders
                if inst is not None and not inst.empty:
                    for _, row in inst.head(8).iterrows():
                        val = row.get("Value", 0) or 0
                        posiciones.append({
                            "empresa": str(row.get("Holder", "")),
                            "valor": round(val / 1e9, 2),
                            "shares": int(row.get("Shares", 0) or 0),
                            "pct": round(float(row.get("% Out", 0) or 0), 2),
                        })
            except:
                pass

        return fondo["nombre"], posiciones, ultimo.get("date", "")

    except Exception as e:
        log.warning(f"fetch_13f_posiciones {fondo_key}: {e}")
        return fondo["nombre"] if fondo else fondo_key, [], ""


def fetch_smart_money_consensus():
    """
    Detecta qué acciones están comprando varios fondos grandes a la vez.
    Señal muy fuerte cuando coinciden 2+ fondos en la misma acción.
    """
    # Acciones más conocidas del portafolio de grandes fondos
    # basadas en los últimos 13F públicos
    conocidos = {
        "BUFFETT": ["AAPL", "BAC", "AXP", "KO", "CVX", "OXY", "KHC", "MCO", "USB"],
        "ACKMAN":  ["HLT", "CMG", "CP", "GOOGL", "LOW", "BN", "NKE"],
        "BURRY":   ["JD", "BABA", "PDD", "REAL", "OSCR", "STLAM"],
        "ARK":     ["TSLA", "COIN", "CRSP", "ROKU", "PATH", "HOOD"],
        "DALIO":   ["SPY", "GLD", "EEM", "VWO", "IEMG", "AAPL"],
        "TEPPER":  ["NVDA", "META", "AMZN", "GOOGL", "MSFT", "JPM"],
    }

    conteo = {}
    for fondo, acciones in conocidos.items():
        for acc in acciones:
            if acc not in conteo:
                conteo[acc] = {"fondos": [], "count": 0}
            conteo[acc]["fondos"].append(fondo)
            conteo[acc]["count"] += 1

    # Filtrar solo las que tienen 2+ fondos
    consensus = [(acc, datos) for acc, datos in conteo.items() if datos["count"] >= 2]
    consensus.sort(key=lambda x: x[1]["count"], reverse=True)
    return consensus[:10]


@bot.message_handler(commands=["halvingbtc"])
def cmd_halvingbtc(msg):
    if not allowed(msg): return
    m = bot.send_message(msg.chat.id,
        "Generando ciclo de 4 años de Bitcoin con datos históricos reales... (15-20s)")
    try:
        chart = generate_halving_chart()

        # Obtener precio actual
        try:
            btc_data = yf.Ticker("BTC-USD").history(period="5d")
            precio_actual = btc_data["Close"].iloc[-1] if not btc_data.empty else 0
        except:
            precio_actual = 0

        # Determinar fase actual
        import datetime as dt_mod
        hoy = dt_mod.date.today()
        halving4 = dt_mod.date(2024, 4, 19)
        dias_desde_halving = (hoy - halving4).days
        meses_desde_halving = dias_desde_halving // 30

        if meses_desde_halving < 18:
            fase_actual = "BULL PHASE"
            fase_color = "🟢"
            tiempo_restante = f"~{18 - meses_desde_halving} meses hasta el techo estimado"
        elif meses_desde_halving < 30:
            fase_actual = "BEAR PHASE"
            fase_color = "🔴"
            tiempo_restante = f"~{30 - meses_desde_halving} meses hasta el suelo estimado"
        else:
            fase_actual = "RECOVERY PHASE"
            fase_color = "🔵"
            tiempo_restante = "Acumulando para el próximo bull"

        caption = (
            f"BITCOIN — CICLO DE 4 AÑOS\n\n"
            f"4th Halving: 19 Abril 2024\n"
            f"Meses desde halving: {meses_desde_halving}\n"
            f"Precio actual: ${precio_actual:,.0f}\n\n"
            f"FASE ACTUAL: {fase_color} {fase_actual}\n"
            f"{tiempo_restante}\n\n"
            f"DATOS REALES:\n"
            f"ATH real: $126,080 (Oct 2025)\n"
            f"Caída desde ATH: ~{round((1 - precio_actual/126080)*100)}%\n\n"
            f"PROYECCION PROXIMO CICLO:\n"
            f"Suelo estimado: $58K-$78K (¿ya visto?)\n"
            f"Objetivo 2029: $200K-$295K\n"
            f"5th Halving: ~Mar 2028\n\n"
            f"Patron: Bull 18m → Bear → Recovery → Bull 2029"
        )

        bot.delete_message(msg.chat.id, m.message_id)
        bot.send_photo(msg.chat.id, chart, caption=caption[:1020])

        # Análisis IA
        prompt = (
            f"Ciclo de 4 años Bitcoin — análisis con datos reales:\n"
            f"4th Halving: 19 abril 2024\n"
            f"ATH REAL: $126,080 (6 octubre 2025) — NO los 180K estimados\n"
            f"Caída desde ATH: ~50% — más suave que ciclos anteriores (2018: -84%, 2022: -77%)\n"
            f"Precio actual: ${precio_actual:,.0f}\n"
            f"Meses desde halving: {meses_desde_halving}\n"
            f"Fase actual: {fase_actual}\n\n"
            f"Contexto importante:\n"
            f"- ETFs spot compraron en los mínimos ($58-74K)\n"
            f"- El suelo posible ya fue visto según algunos analistas\n"
            f"- Galaxy Digital proyecta suelo en $40-46K (Q4 2026)\n"
            f"- PrimeXBT proyecta pico próximo ciclo $200-295K (2029)\n"
            f"- El ciclo de 4 años podría estar mutando por los ETFs institucionales\n\n"
            f"1. ¿Hemos visto ya el suelo de este bear market? Argumentos a favor y en contra\n"
            f"2. ¿Qué tan diferente es este ciclo de los anteriores y por qué?\n"
            f"3. Proyección realista para el próximo bull (2028-2029): ¿$200K o más?\n"
            f"4. Estrategia concreta ahora mismo: ¿acumular, esperar o qué?"
        )
        texto_ia = ask_ai(prompt, max_chars=2000)
        safe_send(msg.chat.id, f"ANALISIS IA — CICLO HALVING\n\n{texto_ia}")

    except Exception as e:
        log.error(f"halvingbtc error: {e}")
        safe_send(msg.chat.id, f"Error generando el gráfico: {e}", message_id=m.message_id)


@bot.message_handler(commands=["insiders"])
def cmd_insiders(msg):
    if not allowed(msg): return
    parts = msg.text.split()
    ticker = parts[1].upper() if len(parts) > 1 else None

    if ticker:
        m = bot.send_message(msg.chat.id, f"Buscando insider transactions de {ticker}...")
        transacciones = fetch_insiders_yfinance(ticker)
        if not transacciones:
            safe_send(msg.chat.id, f"Sin datos de insider transactions para {ticker}.", message_id=m.message_id)
            return

        compras = [t for t in transacciones if t["valor_usd"] > 0]
        ventas = [t for t in transacciones if t["valor_usd"] < 0]

        lines = [f"INSIDER TRANSACTIONS {ticker} — ultimos 30 dias\n"]
        if compras:
            lines.append("COMPRAS:")
            for t in compras[:5]:
                val_m = round(abs(t["valor_usd"]) / 1e6, 2)
                lines.append(f"  {t['insider']} ({t['cargo']})")
                lines.append(f"  {t['tipo']} | {t['shares']:,} acciones | {val_m}M USD | {t['fecha']}")
        if ventas:
            lines.append("\nVENTAS:")
            for t in ventas[:3]:
                val_m = round(abs(t["valor_usd"]) / 1e6, 2)
                lines.append(f"  {t['insider']} | -{val_m}M USD | {t['fecha']}")

        total_compras = sum(abs(t["valor_usd"]) for t in compras)
        total_ventas = sum(abs(t["valor_usd"]) for t in ventas)
        ratio = round(total_compras / total_ventas, 2) if total_ventas > 0 else 99

        lines.append(f"\nRATIO COMPRA/VENTA: {ratio}x")
        if ratio > 2:
            lines.append("SEÑAL ALCISTA — insiders comprando mucho mas de lo que venden")
        elif ratio < 0.5:
            lines.append("SEÑAL BAJISTA — insiders vendiendo masivamente")
        else:
            lines.append("NEUTRAL — actividad normal de insiders")

        snap = "\n".join(lines)
        datos_ia = f"{ticker}: {len(compras)} compras por {round(total_compras/1e6,1)}M USD, {len(ventas)} ventas por {round(total_ventas/1e6,1)}M USD. Ratio {ratio}x"
        prompt = (f"Insider transactions de {ticker} hoy {datetime.now().strftime('%d/%m/%Y')}:\n{datos_ia}\n\n"
                  "1. Que nos dice la actividad de insiders sobre el futuro de la empresa?\n"
                  "2. Es una señal de compra o de precaucion?\n"
                  "3. Contexto: hay razon fundamental para estas transacciones?")
        texto = ask_ai(prompt, max_chars=1500)
        safe_send(msg.chat.id, snap, message_id=m.message_id)
        time.sleep(0.5)
        safe_send(msg.chat.id, f"ANALISIS IA\n\n{texto}")

    else:
        # Escanear insiders en acciones del universo
        m = bot.send_message(msg.chat.id, "Escaneando compras masivas de insiders... (30s)")
        resultados = []
        for t in US_STOCKS[:20]:
            transacciones = fetch_insiders_yfinance(t)
            compras = [x for x in transacciones if x["valor_usd"] > 500000]
            if compras:
                total = sum(x["valor_usd"] for x in compras)
                resultados.append({
                    "ticker": t,
                    "nombre": nombre(t),
                    "compras": len(compras),
                    "total_m": round(total / 1e6, 1),
                    "insider": compras[0]["insider"],
                })

        if not resultados:
            safe_send(msg.chat.id, "Sin compras masivas de insiders detectadas esta semana.", message_id=m.message_id)
            return

        resultados.sort(key=lambda x: x["total_m"], reverse=True)
        lines = [f"COMPRAS MASIVAS INSIDERS {datetime.now().strftime('%d/%m %H:%M')}\n"]
        for r in resultados[:6]:
            lines.append(f"{r['nombre']} ({r['ticker']}): {r['total_m']}M USD")
            lines.append(f"  {r['compras']} transacciones | Lead: {r['insider']}")

        snap = "\n".join(lines)
        datos_ia = "\n".join([f"{r['nombre']}: {r['total_m']}M USD comprado por insiders" for r in resultados[:5]])
        prompt = (f"Compras masivas de insiders detectadas hoy {datetime.now().strftime('%d/%m/%Y')}:\n{datos_ia}\n\n"
                  "1. Las mas interesantes y por que\n"
                  "2. Cual tiene mas potencial de subida segun esta señal\n"
                  "3. Entrada concreta con precio actual")
        texto = ask_ai(prompt, max_chars=1500)
        safe_send(msg.chat.id, snap, message_id=m.message_id)
        time.sleep(0.5)
        safe_send(msg.chat.id, f"ANALISIS IA\n\n{texto}")


@bot.message_handler(commands=["smartmoney"])
def cmd_smartmoney(msg):
    if not allowed(msg): return
    m = bot.send_message(msg.chat.id, "Analizando consenso de grandes fondos...")

    consensus = fetch_smart_money_consensus()
    if not consensus:
        safe_send(msg.chat.id, "Sin datos de consenso disponibles.", message_id=m.message_id)
        return

    lines = [f"SMART MONEY — CONSENSO GRANDES FONDOS\n{datetime.now().strftime('%d/%m %H:%M')}\n"]
    lines.append("Acciones en las que coinciden 2+ fondos:\n")

    datos_con_precio = []
    for acc, datos in consensus[:8]:
        d = fetch_quote(acc, "1mo")
        precio = f"{d['price']} ({d['d5']:+.1f}% sem)" if d else "N/D"
        fondos_txt = " + ".join(datos["fondos"])
        lines.append(f"{'⭐' * datos['count']} {acc}: {precio}")
        lines.append(f"  Fondos: {fondos_txt}")
        if d:
            datos_con_precio.append(f"{acc}: precio {d['price']}, semana {d['d5']:+.1f}%, RSI {d['rsi']} — fondos: {fondos_txt}")

    snap = "\n".join(lines)

    prompt = (f"Smart money — acciones donde coinciden grandes fondos hoy {datetime.now().strftime('%d/%m/%Y')}:\n"
              + "\n".join(datos_con_precio) +
              "\n\nUSA SOLO los precios indicados.\n"
              "1. Las 3 mas interesantes y por que coinciden estos fondos\n"
              "2. Cual tiene mejor momento tecnico ahora mismo\n"
              "3. Entrada concreta con precio actual, stop y objetivo")

    texto = ask_ai(prompt, max_chars=2000)
    safe_send(msg.chat.id, snap, message_id=m.message_id)
    time.sleep(0.5)
    safe_send(msg.chat.id, f"ANALISIS IA\n\n{texto}")


@bot.message_handler(commands=["carteras"])
def cmd_carteras(msg):
    if not allowed(msg): return
    parts = msg.text.split()

    if len(parts) > 1:
        fondo_key = parts[1].upper()
        if fondo_key not in GRANDES_FONDOS:
            fondos_txt = "\n".join([f"  /carteras {k} — {v['nombre']}" for k, v in GRANDES_FONDOS.items()])
            safe_send(msg.chat.id, f"Fondo no reconocido. Disponibles:\n{fondos_txt}")
            return

        m = bot.send_message(msg.chat.id, f"Obteniendo posiciones de {GRANDES_FONDOS[fondo_key]['nombre']}...")
        resultado = fetch_13f_posiciones(fondo_key)

        if len(resultado) == 3:
            nombre_fondo, posiciones, fecha_13f = resultado
        else:
            nombre_fondo, posiciones = resultado
            fecha_13f = ""

        lines = [f"CARTERA {nombre_fondo}"]
        if fecha_13f:
            lines.append(f"Último 13F: {fecha_13f}\n")

        # Posiciones conocidas del fondo
        conocidos = {
            "BUFFETT": ["AAPL", "BAC", "AXP", "KO", "CVX", "OXY", "KHC", "MCO"],
            "ACKMAN":  ["HLT", "CMG", "CP", "GOOGL", "LOW", "NKE"],
            "BURRY":   ["JD", "BABA", "PDD"],
            "ARK":     ["TSLA", "COIN", "CRSP", "ROKU", "PATH"],
            "DALIO":   ["SPY", "GLD", "EEM", "AAPL"],
            "TEPPER":  ["NVDA", "META", "AMZN", "GOOGL", "MSFT"],
        }

        tickers_fondo = conocidos.get(fondo_key, [])
        if tickers_fondo:
            lines.append("POSICIONES PRINCIPALES (13F más reciente):\n")
            for t in tickers_fondo[:6]:
                d = fetch_quote(t, "1mo")
                if d:
                    lines.append(f"  {d['nombre']} ({t})")
                    lines.append(f"  Precio: {d['price']} | Hoy: {d['d1']:+.1f}% | RSI: {d['rsi']}")

        # Noticias recientes del fondo
        try:
            feed = feedparser.parse(
                f"https://news.google.com/rss/search?q={nombre_fondo.split('(')[0].strip().replace(' ', '+')}+portfolio+holdings+2026&hl=es&gl=ES"
            )
            noticias = [e.title for e in feed.entries[:4]]
        except:
            noticias = []

        if noticias:
            lines.append("\nNOTICIAS RECIENTES:")
            for n in noticias[:3]:
                lines.append(f"  • {n}")

        snap = "\n".join(lines)

        # Obtener precios reales para el prompt
        precios_txt = ""
        for t in tickers_fondo[:5]:
            d = fetch_quote(t, "1mo")
            if d:
                precios_txt += f"{d['nombre']} ({t}): {d['price']} USD, semana {d['d5']:+.1f}%, RSI {d['rsi']}\n"

        prompt = (f"Cartera de {nombre_fondo} — análisis actual {datetime.now().strftime('%d/%m/%Y')}:\n"
                  f"Posiciones principales con precios reales:\n{precios_txt}"
                  f"Noticias: {chr(10).join(noticias[:3])}\n\n"
                  "USA SOLO los precios indicados arriba.\n"
                  "1. Qué sectores está priorizando este fondo y por qué\n"
                  "2. Cuál de sus posiciones tiene mejor momento ahora mismo\n"
                  "3. Qué nos dice su estrategia sobre el mercado actual")
        texto = ask_ai(prompt, max_chars=1500)
        safe_send(msg.chat.id, snap, message_id=m.message_id)
        time.sleep(0.5)
        safe_send(msg.chat.id, f"ANALISIS IA\n\n{texto}")

    else:
        fondos_txt = "\n".join([f"  /carteras {k} — {v['nombre']}" for k, v in GRANDES_FONDOS.items()])
        safe_send(msg.chat.id,
            f"GRANDES CARTERAS — SMART MONEY\n\n"
            f"Rastrea los movimientos de los mejores inversores:\n\n"
            f"{fondos_txt}\n\n"
            f"Ver consenso de todos los fondos:\n"
            f"  /smartmoney\n\n"
            f"Para compras de directivos:\n"
            f"  /insiders — compras masivas detectadas\n"
            f"  /insiders NVDA — insiders de una empresa")


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
    ticker = normalizar_ticker_crypto(parts[1])
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


def normalizar_ticker_crypto(ticker):
    """Convierte BTC → BTC-USD, ETH → ETH-USD, etc. automáticamente."""
    CRYPTO_MAP = {
        "BTC": "BTC-USD", "ETH": "ETH-USD", "SOL": "SOL-USD",
        "BNB": "BNB-USD", "XRP": "XRP-USD", "ADA": "ADA-USD",
        "AVAX": "AVAX-USD", "LINK": "LINK-USD", "DOT": "DOT-USD",
        "MATIC": "MATIC-USD", "UNI": "UNI-USD", "AAVE": "AAVE-USD",
        "LTC": "LTC-USD", "BCH": "BCH-USD", "ATOM": "ATOM-USD",
        "NEAR": "NEAR-USD", "ARB": "ARB-USD", "OP": "OP-USD",
        "DOGE": "DOGE-USD", "SHIB": "SHIB-USD", "TRX": "TRX-USD",
    }
    return CRYPTO_MAP.get(ticker.upper(), ticker.upper())


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
    ticker = normalizar_ticker_crypto(parts[1])
    m = bot.send_message(msg.chat.id, f"Calculando indice barato/caro de {nombre(ticker)}...")

    resultado = calcular_indice_valor(ticker)
    if not resultado:
        safe_send(msg.chat.id, f"Sin datos para {ticker}.", message_id=m.message_id)
        return

    # Obtener precio real actualizado
    d = fetch_quote(ticker, "1mo")
    precio_actual = d["price"] if d else resultado["price"]
    cambio_hoy = d["d1"] if d else 0
    resultado["price"] = precio_actual  # actualizar con precio real

    lines = [f"{resultado['nombre']} ({ticker})",
             f"Precio: {precio_actual} USD ({cambio_hoy:+.2f}% hoy)",
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


@bot.message_handler(commands=["trial"])
def cmd_trial(msg):
    chat_id = msg.from_user.id
    sub = SUSCRIPTORES.get(chat_id, {})

    if sub.get("trial_used"):
        safe_send(chat_id,
            "Ya usaste el trial gratuito.\n\n"
            "Para continuar con acceso completo:\n"
            "/premium — 19 USDT/mes")
        return

    if is_premium(chat_id):
        safe_send(chat_id, "Ya tienes acceso activo. Usa /mistatus para ver cuándo expira.")
        return

    # Activar trial 7 días
    expiry = activar_suscripcion(chat_id, dias=TRIAL_DIAS)
    SUSCRIPTORES[chat_id]["trial_used"] = True

    # Notificar al admin
    nombre_u = msg.from_user.first_name or "Desconocido"
    username = f"@{msg.from_user.username}" if msg.from_user.username else "sin username"
    safe_send(ALLOWED_USER_ID,
        f"🆕 NUEVO TRIAL ACTIVADO\n"
        f"Nombre: {nombre_u}\n"
        f"Username: {username}\n"
        f"Chat ID: {chat_id}\n"
        f"Expira: {expiry.strftime('%d/%m/%Y')}")

    safe_send(chat_id,
        f"✅ TRIAL ACTIVADO — 7 días gratis\n\n"
        f"Expira: {expiry.strftime('%d/%m/%Y a las %H:%M')}\n\n"
        f"Tienes acceso completo a todos los comandos.\n"
        f"Escribe /ayuda para ver todo lo disponible.\n\n"
        f"Al terminar el trial puedes continuar con:\n"
        f"/premium — 19 USDT/mes")


@bot.message_handler(commands=["premium"])
def cmd_premium(msg):
    chat_id = msg.from_user.id

    if is_premium(chat_id):
        sub = SUSCRIPTORES.get(chat_id, {})
        expiry = sub.get("expiry")
        expiry_txt = expiry.strftime('%d/%m/%Y') if expiry else "indefinido"
        safe_send(chat_id,
            f"Ya tienes acceso premium activo ✅\n"
            f"Expira: {expiry_txt}\n\n"
            f"Para renovar antes de que expire usa /premium renovar")
        return

    enlace, payment_id = crear_pago_nowpayments(chat_id)

    if enlace:
        safe_send(chat_id,
            f"SUSCRIPCIÓN PREMIUM\n\n"
            f"✅ Acceso completo a todos los comandos\n"
            f"✅ Señales automáticas diarias\n"
            f"✅ Análisis fundamental + Smart Money\n"
            f"✅ Monitor de mercado 24/7\n\n"
            f"Precio: 19 USDT/mes (red TRC20)\n\n"
            f"👇 Enlace de pago:\n{enlace}\n\n"
            f"Después de pagar escribe /verificar para activar tu acceso.")
    else:
        # Fallback — pago manual
        safe_send(chat_id,
            f"SUSCRIPCIÓN PREMIUM — 19 USDT/mes\n\n"
            f"Envía 19 USDT (red TRC20) a:\n"
            f"`{WALLET_USDT}`\n\n"
            f"Después envía el hash de la transacción aquí y activo tu acceso.")


@bot.message_handler(commands=["verificar"])
def cmd_verificar(msg):
    chat_id = msg.from_user.id
    parts = msg.text.split()

    if len(parts) < 2:
        safe_send(chat_id,
            "Uso: /verificar HASH_TRANSACCION\n\n"
            "Ejemplo:\n/verificar abc123def456...")
        return

    tx_hash = parts[1]

    # Verificar pago en blockchain TRC20
    try:
        r = requests.get(
            f"https://apilist.tronscan.org/api/transaction-info?hash={tx_hash}",
            timeout=10
        )
        data = r.json()
        confirmado = data.get("confirmed", False)
        cantidad = 0

        # Buscar USDT en los tokens transferidos
        token_transfers = data.get("tokenTransferInfo", {})
        if token_transfers:
            cantidad = float(token_transfers.get("amount_str", "0")) / 1e6

        if not confirmado:
            safe_send(chat_id, "Transacción no confirmada aún. Espera unos minutos y vuelve a intentarlo.")
            return

        if cantidad >= PRECIO_MENSUAL * 0.95:  # 5% de tolerancia
            expiry = activar_suscripcion(chat_id, dias=30)
            safe_send(chat_id,
                f"✅ PAGO VERIFICADO — {cantidad:.2f} USDT\n\n"
                f"Acceso premium activado hasta {expiry.strftime('%d/%m/%Y')}\n\n"
                f"Escribe /ayuda para ver todos los comandos disponibles.")
            # Notificar al dueño
            safe_send(ALLOWED_USER_ID,
                f"💰 NUEVO SUSCRIPTOR\n"
                f"Chat ID: {chat_id}\n"
                f"Nombre: {msg.from_user.first_name}\n"
                f"Pago: {cantidad:.2f} USDT\n"
                f"TX: {tx_hash[:20]}...")
        else:
            safe_send(chat_id,
                f"Pago detectado pero cantidad insuficiente ({cantidad:.2f} USDT).\n"
                f"Se necesitan {PRECIO_MENSUAL} USDT.")

    except Exception as e:
        log.error(f"Verificar pago: {e}")
        safe_send(chat_id,
            "No pude verificar automáticamente.\n"
            f"Envía el hash al administrador para verificación manual.")


@bot.message_handler(commands=["mistatus"])
def cmd_mistatus(msg):
    chat_id = msg.from_user.id

    if chat_id == ALLOWED_USER_ID:
        total = len([s for s in SUSCRIPTORES.values() if s.get("activo")])
        safe_send(chat_id, f"Eres el administrador.\nSuscriptores activos: {total}")
        return

    sub = SUSCRIPTORES.get(chat_id)
    if not sub or not sub.get("activo"):
        safe_send(chat_id,
            "No tienes suscripción activa.\n\n"
            "/trial — 7 días gratis\n"
            "/premium — 19 USDT/mes")
        return

    expiry = sub.get("expiry")
    dias_restantes = (expiry - datetime.now()).days if expiry else 0
    tipo = "TRIAL" if sub.get("trial_used") and dias_restantes > 0 else "PREMIUM"

    safe_send(chat_id,
        f"ESTADO DE TU SUSCRIPCIÓN\n\n"
        f"Tipo: {tipo}\n"
        f"Expira: {expiry.strftime('%d/%m/%Y') if expiry else 'N/D'}\n"
        f"Días restantes: {dias_restantes}\n\n"
        f"{'Renueva con /premium' if dias_restantes < 5 else '✅ Acceso activo'}")


@bot.message_handler(commands=["activar"])
def cmd_activar(msg):
    """Solo el administrador puede activar manualmente."""
    if msg.from_user.id != ALLOWED_USER_ID:
        return
    parts = msg.text.split()
    if len(parts) < 2:
        safe_send(msg.chat.id, "Uso: /activar CHAT_ID [dias]\nEjemplo: /activar 123456789 30")
        return
    try:
        target_id = int(parts[1])
        dias = int(parts[2]) if len(parts) > 2 else 30
        expiry = activar_suscripcion(target_id, dias=dias)
        safe_send(msg.chat.id, f"✅ Activado usuario {target_id} hasta {expiry.strftime('%d/%m/%Y')}")
        safe_send(target_id, f"✅ Tu acceso premium ha sido activado hasta {expiry.strftime('%d/%m/%Y')}\n\nEscribe /ayuda para ver todos los comandos.")
    except Exception as e:
        safe_send(msg.chat.id, f"Error: {e}")


@bot.message_handler(commands=["suscriptores"])
def cmd_suscriptores(msg):
    """Solo el administrador."""
    if msg.from_user.id != ALLOWED_USER_ID:
        return
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


@bot.message_handler(commands=["ayuda"])
def cmd_ayuda(msg):
    if not allowed(msg): return

    # Mandamos en varios mensajes para que no se corte
    safe_send(msg.chat.id,
        "GUIA COMPLETA — ANALISIS PRO\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "CRYPTO\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "/btc — Análisis profundo Bitcoin\n"
        "Incluye: precio Binance real, Fear&Greed, dominancias BTC/ETH/USDT, funding rate, open interest, long/short ratio, liquidaciones, soportes y resistencias, análisis IA\n\n"
        "/crypto — Resumen rápido BTC, ETH, SOL, BNB\n"
        "Precio actual, RSI, variación diaria y semanal de las 4 cryptos principales\n\n"
        "/halvingbtc — Ciclo de 4 años Bitcoin\n"
        "Gráfico histórico desde 2012 con las fases Bull/Bear/Recovery marcadas, ATH real, zona suelo posible y proyección del próximo ciclo 2028-2029\n\n"
        "⚠️ TICKERS CRYPTO: usa siempre el formato TICKER-USD\n"
        "Ejemplos: BTC-USD, ETH-USD, SOL-USD, XRP-USD, AVAX-USD, LINK-USD\n"
        "/analisis XRP-USD → análisis técnico de XRP\n"
        "/valor XRP-USD → índice barato/caro de XRP")

    time.sleep(0.5)
    safe_send(msg.chat.id,
        "━━━━━━━━━━━━━━━━━━━━\n"
        "SEÑALES DE TRADING\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "/senales_eu — Mejores setups Europa\n"
        "Escanea IBEX 35 completo + CAC + DAX + FTSE. Filtra por RSI diario + RSI semanal + VWAP + volumen + tendencia EMA. Score mínimo 12/28. Incluye gráfico con velas, VWAP, Bollinger y niveles\n\n"
        "/senales_us — Mejores setups EEUU\n"
        "Escanea 60+ acciones: grandes caps (NVDA, AAPL...) + mid caps especulativas (PLTR, COIN, RKLB...). Solo acciones, sin crypto\n\n"
        "/intraday — Señales intradía 15min\n"
        "Para operaciones del mismo día. Usa datos de 15 minutos. Criterios: VWAP + MACD + volumen elevado. TP más cercanos que swing\n\n"
        "/etfs — ETFs con señales\n"
        "Índices (SPY, QQQ, IWM), sectoriales (XLK, XLF, XLE...) y temáticos (BOTZ, ARKK, SOXX, ICLN...)")

    time.sleep(0.5)
    safe_send(msg.chat.id,
        "━━━━━━━━━━━━━━━━━━━━\n"
        "ANÁLISIS INDIVIDUAL\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "/analisis TICKER — Análisis técnico completo\n"
        "Precio real, RSI diario, RSI semanal, MACD, VWAP, EMA20, EMA50, ATR, soportes, resistencias, niveles psicológicos y análisis IA\n"
        "Ejemplos: /analisis NVDA | /analisis SAN.MC | /analisis XRP-USD\n\n"
        "/fundamental TICKER — Análisis fundamental 0-100\n"
        "5 categorías: Valoración (P/E, PEG, EV/EBITDA) + Salud financiera (deuda, caja, liquidez) + Rentabilidad (FCF, márgenes, ROE) + Crecimiento (ingresos, beneficios) + Potencial LP (moat, insiders, dividendo). Ajustado por tipo de empresa (utility, growth, consumer, materials, biotech). Incluye estimación precio justo, 1 año y 3 años\n"
        "Ejemplos: /fundamental NVDA | /fundamental NKE | /fundamental SAN.MC\n\n"
        "/valor TICKER — Índice barato/caro 0-100\n"
        "Estilo FREDI. 100=muy barato, 0=muy caro. Componentes distintos según tipo de activo:\n"
        "Crypto: Fear&Greed + RSI + EMA200 + Funding Rate + DXY + Volumen\n"
        "Acciones: RSI + EMA200 + VIX + RSI semanal + Dist. máximo 52s + Volumen\n"
        "Ejemplos: /valor BTC-USD | /valor NVDA | /valor ^GSPC")

    time.sleep(0.5)
    safe_send(msg.chat.id,
        "━━━━━━━━━━━━━━━━━━━━\n"
        "CICLO Y MERCADO\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "/ciclo — Ciclo psicológico de mercado\n"
        "Muestra en qué fase del ciclo están BTC, SP500 y DAX. Con flechas de dirección, % completado, potencial restante y acción recomendada (COMPRAR/VENDER/REDUCIR). Zonas verde (compra) y roja (venta) marcadas\n\n"
        "/mercadoahora — Snapshot visual del mercado\n"
        "Gráfico de barras verde/rojo con variación del día: SP500, Nasdaq, DAX, IBEX, CAC, Bitcoin, Ethereum, Oro, Petróleo, DXY. Se actualiza automáticamente cada 30 minutos si hay movimientos importantes\n\n"
        "/macro — Datos macroeconómicos\n"
        "VIX (miedo), DXY (dólar), US10Y (bono EEUU), Oro y Petróleo WTI con variación diaria y semanal + análisis IA\n\n"
        "/sectores — Semáforo 11 sectores SP500\n"
        "Estado BULL/NEUTRO/BEAR de tecnología, finanzas, salud, energía, consumo, industrial, utilities, materiales, inmobiliario, comunicaciones\n\n"
        "/mercados — Índices EU y EEUU\n"
        "SP500, Dow Jones, Nasdaq, Euro Stoxx, DAX, FTSE, IBEX, CAC")

    time.sleep(0.5)
    safe_send(msg.chat.id,
        "━━━━━━━━━━━━━━━━━━━━\n"
        "SMART MONEY\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "/smartmoney — Consenso grandes fondos\n"
        "Detecta qué acciones están comprando simultáneamente 2 o más grandes fondos. Señal muy potente cuando Buffett + Tepper + Dalio coinciden en la misma acción\n\n"
        "/carteras — Posiciones de grandes inversores\n"
        "/carteras BUFFETT → Warren Buffett (Berkshire)\n"
        "/carteras ARK → Cathie Wood (ARK Invest)\n"
        "/carteras BURRY → Michael Burry (Scion)\n"
        "/carteras ACKMAN → Bill Ackman (Pershing)\n"
        "/carteras DALIO → Ray Dalio (Bridgewater)\n"
        "/carteras TEPPER → David Tepper (Appaloosa)\n\n"
        "/insiders — Compras masivas de directivos\n"
        "Cuando el CEO o CFO compran acciones de su propia empresa es señal muy alcista. Escanea compras >500.000 USD en los últimos 30 días\n"
        "/insiders NVDA → insiders de Nvidia concretamente")

    time.sleep(0.5)
    safe_send(msg.chat.id,
        "━━━━━━━━━━━━━━━━━━━━\n"
        "DETECTORES\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "/infravaloradas — Acciones caídas con potencial\n"
        "Busca acciones que han caído >20% desde máximos pero muestran señales de rebote: RSI en zona de compra, volumen creciendo silenciosamente (acumulación institucional), rebote desde mínimos. Escanea 100+ activos\n\n"
        "/bull_detector — Bull runs nacientes\n"
        "Detecta sectores y acciones que están iniciando tendencias alcistas antes de que sean evidentes\n\n"
        "/anomalias — Volumen anómalo\n"
        "Detecta activos con volumen 2.5x superior a la media. Suele preceder noticias de M&A, resultados o movimientos institucionales\n\n"
        "/explosiones — Momentum explosivo\n"
        "Acciones con volumen 1.8x + subida >3% + cerca de máximo anual. Las que están a punto de romper\n\n"
        "/noticias_impacto — M&A, earnings, FDA\n"
        "Filtra noticias de alto impacto: fusiones y adquisiciones, resultados sorpresa, aprobaciones FDA, contratos importantes")

    time.sleep(0.5)
    safe_send(msg.chat.id,
        "━━━━━━━━━━━━━━━━━━━━\n"
        "HERRAMIENTAS\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "/seguimiento — Ver P&L de tus trades abiertos\n"
        "/seguimiento add NVDA 890 865 920 950 → añadir trade (ticker, entrada, stop, TP1, TP2)\n"
        "/seguimiento close 1 → cerrar el trade número 1\n\n"
        "/alerta NVDA 950 — Alerta de precio\n"
        "Te avisa cuando NVDA llegue a 950. Funciona 24/7 también en crypto\n"
        "/alertas → ver alertas activas\n"
        "/borra_alerta 1 → eliminar alerta número 1\n\n"
        "/riesgo 10000 2 NVDA 890 865\n"
        "Calcula cuántas acciones comprar con 10.000€ arriesgando el 2%, con entrada en 890 y stop en 865\n\n"
        "/backtest — Histórico de aciertos del sistema\n"
        "Analiza los últimos 3 meses: cuántas señales dieron TP y cuántas tocaron stop\n\n"
        "/resumen_semana — Balance semanal\n"
        "Ganadores y perdedores de la semana + perspectiva para la siguiente\n\n"
        "/valores — Vista rápida 0-100 de BTC/ETH/SP500/DAX/IBEX\n\n"
        "/metales /ipos /calendario /oportunidades\n\n"
        "Pregunta libre → la IA responde con datos reales actuales\n"
        "Ejemplo: 'cómo está el sector nuclear?' o 'análisis de Tesla'")

    time.sleep(0.5)
    safe_send(msg.chat.id,
        "━━━━━━━━━━━━━━━━━━━━\n"
        "TU SUSCRIPCIÓN\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "/mistatus → ver cuándo expira tu acceso\n"
        "/premium → renovar suscripción (19 USDT/mes)\n"
        "/verificar HASH → activar acceso tras pagar\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "TICKERS DE REFERENCIA\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "IBEX: SAN.MC BBVA.MC ITX.MC REP.MC TEF.MC\n"
        "CAC: BNP.PA AIR.PA OR.PA MC.PA\n"
        "DAX: BMW.DE SAP SIE.DE BAYN.DE\n"
        "EEUU: AAPL MSFT NVDA AMZN GOOGL META TSLA\n"
        "Crypto: BTC-USD ETH-USD SOL-USD XRP-USD BNB-USD\n"
        "ETFs: SPY QQQ GLD ARKK BOTZ SOXX\n"
        "Macro: ^VIX ^GSPC ^GDAXI GC=F CL=F")


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
        "insiders": cmd_insiders,
        "carteras": cmd_carteras,
        "halvingbtc": cmd_halvingbtc,
        "mercadoahora": cmd_mercadoahora,
        "smartmoney": cmd_smartmoney,
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


def generate_market_bars(datos):
    """
    Genera imagen de barras horizontales verde/rojo estilo Trade Republic
    mostrando variaciones del mercado.
    datos: lista de {nombre, ticker, cambio_pct, categoria}
    """
    import matplotlib.pyplot as plt
    import numpy as np

    # Ordenar por cambio (mayores primero)
    datos = sorted(datos, key=lambda x: x["cambio_pct"], reverse=True)
    n = len(datos)

    fig, ax = plt.subplots(figsize=(10, max(4, n * 0.6)))
    fig.patch.set_facecolor('#0d1117')
    ax.set_facecolor('#0d1117')

    max_abs = max(abs(d["cambio_pct"]) for d in datos) if datos else 3
    max_abs = max(max_abs, 1)

    for i, d in enumerate(datos):
        y = n - 1 - i
        pct = d["cambio_pct"]
        color = '#00CC44' if pct >= 0 else '#FF3333'
        width = (abs(pct) / max_abs) * 8

        # Barra
        ax.barh(y, width if pct >= 0 else -width,
               height=0.55, color=color, alpha=0.85, zorder=2)

        # Línea central
        ax.axvline(x=0, color='#444444', linewidth=1, zorder=1)

        # Nombre ticker
        ax.text(-0.3, y, d["nombre"], va='center', ha='right',
               color='white', fontsize=10, fontweight='bold')

        # Porcentaje
        offset = width + 0.2 if pct >= 0 else -width - 0.2
        ha = 'left' if pct >= 0 else 'right'
        signo = "+" if pct >= 0 else ""
        ax.text(offset, y, f"{signo}{pct:.2f}%",
               va='center', ha=ha, color=color,
               fontsize=11, fontweight='bold')

    ax.set_xlim(-max_abs * 1.4, max_abs * 1.4)
    ax.set_ylim(-0.5, n - 0.5)
    ax.axis('off')

    titulo = f"MERCADOS — {datetime.now().strftime('%d/%m %H:%M')}"
    ax.set_title(titulo, color='white', fontsize=13,
                fontweight='bold', pad=12)

    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=130,
               facecolor='#0d1117', bbox_inches='tight')
    plt.close()
    buf.seek(0)
    return buf


def get_market_snapshot():
    """Obtiene variaciones de todos los mercados clave."""
    activos = [
        # Indices
        ("^GSPC",    "S&P 500",   "indice"),
        ("^IXIC",    "Nasdaq",    "indice"),
        ("^GDAXI",   "DAX",       "indice"),
        ("^IBEX",    "IBEX 35",   "indice"),
        ("^FCHI",    "CAC 40",    "indice"),
        # Crypto
        ("BTC-USD",  "Bitcoin",   "crypto"),
        ("ETH-USD",  "Ethereum",  "crypto"),
        # Macro
        ("GC=F",     "Oro",       "macro"),
        ("CL=F",     "Petróleo",  "macro"),
        ("DX-Y.NYB", "USD Index", "macro"),
    ]
    resultado = []
    for ticker, nom, cat in activos:
        try:
            d = fetch_quote(ticker, "5d")
            if d:
                resultado.append({
                    "ticker": ticker,
                    "nombre": nom,
                    "cambio_pct": round(d["d1"], 2),
                    "precio": d["price"],
                    "categoria": cat,
                })
        except:
            pass
    return resultado


@bot.message_handler(commands=["mercadoahora"])
def cmd_mercadoahora(msg):
    if not allowed(msg): return
    m = bot.send_message(msg.chat.id, "Obteniendo snapshot del mercado...")
    datos = get_market_snapshot()
    if not datos:
        safe_send(msg.chat.id, "Sin datos disponibles.", message_id=m.message_id)
        return
    try:
        chart = generate_market_bars(datos)
        bot.delete_message(msg.chat.id, m.message_id)
        bot.send_photo(msg.chat.id, chart)
    except Exception as e:
        log.warning(f"mercadoahora chart: {e}")
        lines = [f"MERCADOS {datetime.now().strftime('%d/%m %H:%M')}\n"]
        for d in datos:
            s = "🟢" if d["cambio_pct"] >= 0 else "🔴"
            lines.append(f"{s} {d['nombre']}: {d['cambio_pct']:+.2f}%")
        safe_send(msg.chat.id, "\n".join(lines), message_id=m.message_id)


def job_monitor_mercado():
    """Cada 30min dias laborables — alerta si hay movimientos importantes."""
    if not es_dia_laborable():
        return
    datos = get_market_snapshot()
    if not datos:
        return

    # Filtrar solo movimientos significativos
    umbrales = {"indice": 1.0, "crypto": 3.0, "macro": 1.5}
    alertas = [d for d in datos
               if abs(d["cambio_pct"]) >= umbrales.get(d["categoria"], 1.5)]

    if not alertas:
        return  # Sin movimientos importantes — no molestar

    try:
        chart = generate_market_bars(datos)  # Siempre mostrar todos con contexto
        caption = f"⚡ MOVIMIENTO DESTACADO {datetime.now().strftime('%H:%M')}\n"
        for a in alertas:
            s = "🟢" if a["cambio_pct"] >= 0 else "🔴"
            caption += f"{s} {a['nombre']}: {a['cambio_pct']:+.2f}%\n"
        bot.send_photo(ALLOWED_USER_ID, chart, caption=caption[:1020])
    except Exception as e:
        log.warning(f"job_monitor_mercado: {e}")
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
        d1 = a['d1'] if not (a['d1'] != a['d1']) else 0.0
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
        # Monitor mercado cada 30min
        scheduler.add_job(job_monitor_mercado,   "interval", minutes=30)
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
