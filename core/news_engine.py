import feedparser
import re
from datetime import datetime

# ─────────────────────────────────────────────────────────────
# FUENTES RSS GRATUITAS PREMIUM
# ─────────────────────────────────────────────────────────────

RSS_FEEDS = [
    # Yahoo Finance
    "https://finance.yahoo.com/rss/topstories",

    # Reuters Markets
    "https://feeds.reuters.com/reuters/businessNews",

    # CNBC
    "https://www.cnbc.com/id/100003114/device/rss/rss.html",

    # MarketWatch
    "http://feeds.marketwatch.com/marketwatch/topstories/",

    # Investing
    "https://www.investing.com/rss/news.rss",

    # Nasdaq
    "https://www.nasdaq.com/feed/rssoutbound?category=Markets",

    # Benzinga
    "https://www.benzinga.com/feed",

    # TheStreet
    "https://www.thestreet.com/.rss/full/",

    # CoinDesk
    "https://www.coindesk.com/arc/outboundfeeds/rss/",

    # Seeking Alpha
    "https://seekingalpha.com/feed.xml",
]

# ─────────────────────────────────────────────────────────────
# FILTROS
# ─────────────────────────────────────────────────────────────

GOOD_KEYWORDS = [
    "stock",
    "stocks",
    "market",
    "markets",
    "nasdaq",
    "dow",
    "sp500",
    "s&p",
    "fed",
    "federal reserve",
    "interest rates",
    "inflation",
    "earnings",
    "guidance",
    "ipo",
    "merger",
    "acquisition",
    "buyback",
    "semiconductor",
    "ai",
    "artificial intelligence",
    "nvidia",
    "microsoft",
    "apple",
    "amazon",
    "google",
    "meta",
    "tesla",
    "bitcoin",
    "crypto",
    "ethereum",
    "etf",
    "oil",
    "gold",
    "bank",
    "biotech",
    "fda",
    "bull",
    "bear",
    "rally",
    "crash",
    "surge",
]

BAD_KEYWORDS = [
    "social security",
    "retirement",
    "mortgage",
    "credit card",
    "travel",
    "recipe",
    "celebrity",
    "shopping",
    "insurance",
    "personal finance",
    "dating",
    "lifestyle",
]

# ─────────────────────────────────────────────────────────────
# DETECTAR TICKERS
# ─────────────────────────────────────────────────────────────

def detect_tickers(text):
    tickers = re.findall(r"\b[A-Z]{2,5}\b", text)
    blacklist = {
        "USA", "EU", "ETF", "FED", "CEO",
        "USD", "GDP", "AI", "IPO", "SEC",
        "FDA", "BTC"
    }
    return [t for t in tickers if t not in blacklist]


# ─────────────────────────────────────────────────────────────
# IMPACTO
# ─────────────────────────────────────────────────────────────

def detect_impact(title):
    t = title.lower()

    bullish = [
        "beats",
        "surges",
        "jumps",
        "buyback",
        "acquisition",
        "approval",
        "bull",
        "record revenue",
        "strong guidance",
    ]

    bearish = [
        "misses",
        "falls",
        "crash",
        "lawsuit",
        "downgrade",
        "bear",
        "investigation",
        "weak guidance",
    ]

    for k in bullish:
        if k in t:
            return "BULLISH"

    for k in bearish:
        if k in t:
            return "BEARISH"

    return "NEUTRAL"


# ─────────────────────────────────────────────────────────────
# FILTRADO PRINCIPAL
# ─────────────────────────────────────────────────────────────

def valid_news(title):
    t = title.lower()

    if any(bad in t for bad in BAD_KEYWORDS):
        return False

    if any(good in t for good in GOOD_KEYWORDS):
        return True

    return False


# ─────────────────────────────────────────────────────────────
# MOTOR PRINCIPAL
# ─────────────────────────────────────────────────────────────

def get_market_news(limit=25):
    news = []
    seen = set()

    for url in RSS_FEEDS:
        try:
            feed = feedparser.parse(url)

            for entry in feed.entries[:20]:

                title = entry.title.strip()

                if title in seen:
                    continue

                if not valid_news(title):
                    continue

                seen.add(title)

                summary = getattr(entry, "summary", "")
                link = getattr(entry, "link", "")

                tickers = detect_tickers(title)

                impact = detect_impact(title)

                news.append({
                    "title": title,
                    "summary": summary[:300],
                    "link": link,
                    "tickers": tickers,
                    "impact": impact,
                    "source": feed.feed.get("title", "Unknown"),
                    "time": datetime.now().strftime("%H:%M"),
                })

        except Exception as e:
            print(f"[NEWS ERROR] {url} -> {e}")

    # Ordenar
    priority = {
        "BULLISH": 0,
        "BEARISH": 1,
        "NEUTRAL": 2,
    }

    news.sort(key=lambda x: priority.get(x["impact"], 99))

    return news[:limit]


# ─────────────────────────────────────────────────────────────
# TEST
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":

    noticias = get_market_news()

    for n in noticias[:10]:

        print("\n━━━━━━━━━━━━━━━━━━━━━━")
        print(f"[{n['impact']}] {n['title']}")
        print(f"Fuente: {n['source']}")
        print(f"Tickers: {n['tickers']}")