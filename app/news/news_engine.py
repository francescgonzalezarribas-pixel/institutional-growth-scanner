import finnhub

from app.config import FINNHUB_API_KEY

client = finnhub.Client(api_key=FINNHUB_API_KEY)


IMPORTANT_KEYWORDS = [
    "ai",
    "artificial intelligence",
    "earnings",
    "guidance",
    "contract",
    "partnership",
    "semiconductor",
    "satellite",
    "robotics",
    "acquisition",
    "defense",
    "growth",
    "cloud"
]


def get_company_news(symbol: str):

    try:

        news = client.company_news(
            symbol,
            _from="2026-05-01",
            to="2026-05-08"
        )

        filtered = []

        for item in news[:10]:

            headline = item.get("headline", "").lower()

            if any(k in headline for k in IMPORTANT_KEYWORDS):
                filtered.append(item)

        return filtered

    except Exception:
        return []