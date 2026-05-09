import time
import yfinance as yf

from app.news.news_engine import get_company_news
from app.scoring.scoring_engine import calculate_score

from app.scanner.universe import (
    get_market_universe
)

from app.utils.logger import log


def scan_market():

    signals = []

    universe = get_market_universe()

    log.info(
        f"Universo cargado: {len(universe)} acciones"
    )

    for symbol in universe:

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

            if avg_volume <= 0:
                continue

            relative_volume = round(
                current_volume / avg_volume,
                2
            )

            price = ticker.fast_info.get(
                "lastPrice"
            )

            if not price:
                continue

            if price <= 1 or price >= 1000:
                continue

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

            headline = (
                news[0]["headline"]
                if has_news
                else "Momentum detectado."
            )

            institutional_volume = (
                relative_volume >= 3
            )

            sector_hot = True

            ipo = symbol in [
                "ARM",
                "RKLB",
                "ASTS",
                "IONQ",
                "TEM",
                "RDDT",
                "CART"
            ]

            data = {
                "symbol": symbol,
                "price": float(price),
                "relative_volume": relative_volume,
                "breakout": breakout,
                "ipo": ipo,
                "sector_hot": sector_hot,
                "institutional_volume": institutional_volume,
                "has_news": has_news,
                "headline": headline,
                "sector": "Growth"
            }

            score = calculate_score(data)

            data["score"] = score

            log.info(
                f"{symbol} | "
                f"PRICE={price:.2f} | "
                f"RVOL={relative_volume} | "
                f"SCORE={score}"
            )

            if score >= 85:

                signals.append(data)

                log.info(
                    f"SETUP DETECTADO: {symbol}"
                )

            time.sleep(10)

        except Exception as e:

            log.error(f"{symbol} {e}")

            time.sleep(30)

    return signals