import yfinance as yf
import pandas as pd

from app.news.news_engine import get_company_news
from app.scoring.scoring_engine import calculate_score


WATCHLIST = [
    "ASTS",
    "HIMS",
    "PLTR",
    "ARM",
    "TEM",
    "SMCI",
    "SOUN",
    "IONQ",
    "RKLB",
    "CRWD"
]


def scan_market():

    signals = []

    for symbol in WATCHLIST:

        try:

            ticker = yf.Ticker(symbol)

            hist = ticker.history(period="5d")

            if len(hist) < 3:
                continue

            avg_volume = hist["Volume"].mean()

            current_volume = hist["Volume"].iloc[-1]

            relative_volume = round(
                current_volume / avg_volume,
                2
            )

            price = hist["Close"].iloc[-1]

            breakout = (
                price >= hist["High"].max() * 0.98
            )

            news = get_company_news(symbol)

            has_news = len(news) > 0

            data = {
                "symbol": symbol,
                "price": price,
                "relative_volume": relative_volume,
                "breakout": breakout,
                "ipo": True,
                "sector_hot": True,
                "institutional_volume": relative_volume >= 3,
                "has_news": has_news,
                "headline": (
                    news[0]["headline"]
                    if has_news
                    else "Momentum detectado sin noticia relevante."
                ),
                "sector": "Growth"
            }

            score = calculate_score(data)

            data["score"] = score

            if score >= 80:
                signals.append(data)

        except Exception as e:
            print(symbol, e)

    return signals