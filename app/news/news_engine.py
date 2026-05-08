import finnhub

from app.config import FINNHUB_API_KEY

client = finnhub.Client(api_key=FINNHUB_API_KEY)


IMPORTANT_KEYWORDS = [
    "earnings",
    "guidance",
    "contract",
    "partnership",
    "artificial intelligence",
    "ai",
    "semiconductor",
    "satellite",
    "robotics",
    "cloud",
    "acquisition",
    "defense",
    "military",
    "growth",
    "expansion",
    "approval",
    "deal",
    "infrastructure"
]


BAD_KEYWORDS = [
    "top gainers",
    "top losers",
    "market recap",
    "analyst ratings",
    "price target",
    "stock moved",
    "why shares",
    "why stock",
    "market close",
    "trading session"
]


def get_company_news(symbol: str):

    try:

        news = client.company_news(
            symbol,
            _from="2026-05-01",
            to="2026-05-09"
        )

        filtered = []

        for item in news[:15]:

            headline = item.get(
                "headline",
                ""
            ).lower()

            if any(
                bad in headline
                for bad in BAD_KEYWORDS
            ):
                continue

            if any(
                good in headline
                for good in IMPORTANT_KEYWORDS
            ):

                filtered.append(item)

        return filtered

    except Exception:
        return []