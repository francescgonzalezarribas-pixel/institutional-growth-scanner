import os
import pytz

# ── TOKENS ─────────────────────────────────────

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY")

ALLOWED_USER_ID = int(os.getenv("ALLOWED_USER_ID"))

# ── TIMEZONE ───────────────────────────────────

MADRID = pytz.timezone("Europe/Madrid")

# ── USA STOCKS ─────────────────────────────────

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
]

# ── EUROPA ─────────────────────────────────────

EU_STOCKS = [
    "SAN.MC",
    "BBVA.MC",
    "IBE.MC",
    "MC.PA",
    "SIE.DE",
    "ADS.DE",
]

# ── ETFs ───────────────────────────────────────

ETFS = [
    "SPY",
    "QQQ",
    "SMH",
    "ARKK",
    "XLE",
    "XLK",
    "ITA",
]

# ── CRYPTO ─────────────────────────────────────

CRYPTO = [
    "BTC-USD",
    "ETH-USD",
    "SOL-USD",
]

# ── INDICES ────────────────────────────────────

INDICES = {
    "^GSPC": "S&P500",
    "^IXIC": "NASDAQ",
    "^DJI": "DOW JONES",
    "^GDAXI": "DAX",
}

# ── RSS FEEDS ──────────────────────────────────

RSS_FEEDS = [
    "https://feeds.finance.yahoo.com/rss/2.0/headline",
    "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    "https://feeds.reuters.com/reuters/businessNews",
]