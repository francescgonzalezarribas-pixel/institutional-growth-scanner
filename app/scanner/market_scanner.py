import time
import yfinance as yf

from app.news.news_engine import get_company_news
from app.scoring.scoring_engine import calculate_score

from app.utils.logger import log


app.scanner.universe


def scan_market():

    signals = []

    for symbol in HOT_STOCKS:

        try:

            log.info(f"Analizando {symbol}")

            ticker = yf.Ticker(symbol)

            hist = ticker.history(
                period="1mo",
                interval="1d"
            )

            if hist.empty or len(hist) < 10:
                continue

            avg_volume = (
                hist["Volume"]
                .tail(10)
                .mean()
            )

            current_volume = hist["Volume"].iloc[-1]

            relative_volume = round(
                current_volume / avg_volume,
                2
            )

            price = hist["Close"].iloc[-1]

            resistance = (
                hist["High"]
                .tail(20)
                .max()
            )

            breakout = (
                price >= resistance * 0.98
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
                    else "Momentum detectado."
                ),
                "sector": "Growth"
            }

            score = calculate_score(data)

            data["score"] = score

            log.info(
                f"{symbol} | "
                f"RVOL={relative_volume} | "
                f"SCORE={score}"
            )

            if score >= 80:
                signals.append(data)

            time.sleep(3)

        except Exception as e:

            log.error(f"{symbol} {e}")

            time.sleep(5)

    return signals