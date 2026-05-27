import os
import pytz

# =========================================================
# TOKENS
# =========================================================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY")

ALLOWED_USER_ID = int(os.getenv("ALLOWED_USER_ID"))

# =========================================================
# TIMEZONE
# =========================================================

MADRID = pytz.timezone("Europe/Madrid")

# =========================================================
# USA STOCKS
# =========================================================

US_STOCKS = [
    "NVDA",
    "AAPL",
    "MSFT",
    "AMZN",
    "META",
    "GOOGL",
    "TSLA",
    "PLTR",
    "RKLB",
    "ASTS",
    "SMCI",
    "AMD",
    "AVGO",
    "MU",
    "CRWD",
    "PANW",
    "SNOW",
    "NET",
    "ARM",
    "ORCL",
    "INTC",
    "IONQ",
    "RGTI",
    "QBTS",
]

# =========================================================
# EUROPE STOCKS
# =========================================================

EU_STOCKS = [
    "SAN.MC",
    "BBVA.MC",
    "IBE.MC",
    "ITX.MC",
    "MC.PA",
    "SIE.DE",
    "ADS.DE",
    "AIR.PA",
    "ASML.AS",
]

# =========================================================
# ETFs
# =========================================================

ETFS = [
    "SPY",
    "QQQ",
    "DIA",
    "IWM",
    "SMH",
    "SOXX",
    "ARKK",
    "XLE",
    "XLK",
    "XLF",
    "XLV",
    "ITA",
    "UFO",
]

# =========================================================
# CRYPTO
# =========================================================

CRYPTO = [
    "BTC-USD",
    "ETH-USD",
    "SOL-USD",
]

# =========================================================
# INDICES
# =========================================================

INDICES = {
    "^GSPC": "S&P500",
    "^IXIC": "NASDAQ",
    "^DJI": "DOW JONES",
    "^GDAXI": "DAX",
    "^IBEX": "IBEX35",
    "^FCHI": "CAC40",
}

# =========================================================
# MACRO
# =========================================================

MACRO_TICKERS = {
    "^VIX": "VIX",
    "DX-Y.NYB": "DXY",
    "GC=F": "ORO",
    "CL=F": "PETROLEO",
    "^TNX": "US10Y",
}

# =========================================================
# RSS FEEDS
# =========================================================

RSS_FEEDS = [
    "https://feeds.finance.yahoo.com/rss/2.0/headline",
    "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    "https://feeds.reuters.com/reuters/businessNews",
    "https://feeds.marketwatch.com/marketwatch/topstories/",
    "https://www.investing.com/rss/news.rss",
]

# =========================================================
# SETTINGS
# =========================================================

CACHE_MINUTES = 3

INTRADAY_INTERVAL = "5m"

DEFAULT_PERIOD = "1mo"

MAX_NEWS = 20