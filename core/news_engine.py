import feedparser
import logging
from datetime import datetime

from config import RSS_FEEDS

log = logging.getLogger(__name__)

# =========================================================
# KEYWORDS IMPORTANTES
# =========================================================

IMPORTANT_KEYWORDS = [
    "earnings",
    "guidance",
    "upgrade",
    "downgrade",
    "fda",
    "acquisition",
    "merger",
    "ai",
    "artificial intelligence",
    "defense",
    "military",
    "space",
    "nuclear",
    "quantum",
    "contract",
    "breakthrough",
    "bankruptcy",
    "insider",
]

# =========================================================
# RSS NEWS
# =========================================================

def get_news(limit=30):

    news = []

    seen = set()

    for feed_url in RSS_FEEDS:

        try:

            feed = feedparser.parse(feed_url)

            for entry in feed.entries:

                title = entry.get("title", "").strip()

                if not title:
                    continue

                clean = title.lower()

                if clean in seen:
                    continue

                seen.add(clean)

                news.append({
                    "title": title,
                    "source": feed.feed.get("title", "RSS"),
                    "link": entry.get("link", ""),
                    "published": entry.get("published", ""),
                    "important": is_important(title),
                    "score": news_score(title),
                })

        except Exception as e:

            log.warning(f"RSS error {feed_url}: {e}")

    # ordenar por score
    news.sort(
        key=lambda x: x["score"],
        reverse=True
    )

    return news[:limit]


# =========================================================
# IMPORTANCE
# =========================================================

def is_important(title):

    t = title.lower()

    for kw in IMPORTANT_KEYWORDS:

        if kw in t:
            return True

    return False


# =========================================================
# NEWS SCORE
# =========================================================

def news_score(title):

    t = title.lower()

    score = 0

    for kw in IMPORTANT_KEYWORDS:

        if kw in t:
            score += 2

    # IA y space pesan más
    if "ai" in t:
        score += 3

    if "space" in t:
        score += 3

    if "nuclear" in t:
        score += 2

    if "quantum" in t:
        score += 2

    return score


# =========================================================
# HOT NEWS
# =========================================================

def get_hot_news(limit=10):

    news = get_news(100)

    hot = [
        n for n in news
        if n["important"]
    ]

    hot.sort(
        key=lambda x: x["score"],
        reverse=True
    )

    return hot[:limit]


# =========================================================
# TRENDING THEMES
# =========================================================

def trending_themes():

    news = get_news(100)

    themes = {
        "AI": 0,
        "SPACE": 0,
        "NUCLEAR": 0,
        "DEFENSE": 0,
        "QUANTUM": 0,
    }

    for n in news:

        t = n["title"].lower()

        if "ai" in t or "artificial intelligence" in t:
            themes["AI"] += 1

        if "space" in t:
            themes["SPACE"] += 1

        if "nuclear" in t:
            themes["NUCLEAR"] += 1

        if "defense" in t or "military" in t:
            themes["DEFENSE"] += 1

        if "quantum" in t:
            themes["QUANTUM"] += 1

    return themes


# =========================================================
# TEST
# =========================================================

if __name__ == "__main__":

    print("\n======================")
    print("HOT NEWS")
    print("======================\n")

    news = get_hot_news()

    for n in news:

        print(f"[{n['score']}] {n['title']}")
        print(f"Source: {n['source']}")
        print()