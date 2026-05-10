import time
import requests

from app.config import (
    TWELVEDATA_API_KEY
)

from app.news.news_engine import (
    get_company_news
)

from app.scoring.scoring_engine import (
    calculate_score
)

from app.scanner.universe import (
    get_market_universe
)

from app.utils.logger import log


BASE_URL = (
    "https://api.twelvedata.com"
)


def get_stock_data(symbol):

    try:

        url = (
            f"{BASE_URL}/time_series"
        )

        params = {
            "symbol": symbol,
            "interval": "1day",
            "outputsize": 30,
            "apikey": TWELVEDATA_API_KEY
        }

        response = requests.get(
            url,
            params=params,
            timeout=15
        )

        data = response.json()

        values = data.get("values")

        if not values:
            return None

        closes = []
        highs = []
        volumes = []

        for candle in values:

            closes.append(
                float(candle["close"])
            )

            highs.append(
                float(candle["high"])
            )

            volumes.append(
                float(candle["volume"])
            )

        closes.reverse()
        highs.reverse()
        volumes.reverse()

        if len(closes) < 10:
            return None

        current_price = closes[-1]

        avg_volume = (
            sum(volumes[-10:]) / 10
        )

        current_volume = volumes[-1]

        if avg_volume <= 0:
            return None

        relative_volume = round(
            current_volume / avg_volume,
            2
        )

        resistance = max(highs[-20:])

        breakout = (
            current_price >= resistance * 0.98
        )

        return {
            "price": current_price,
            "relative_volume": relative_volume,
            "breakout": breakout
        }

    except Exception as e:

        log.error(
            f"{symbol} data error {e}"
        )

        return None


def scan_market():

    signals = []

    universe = get_market_universe()

    log.info(
        f"Universo cargado: "
        f"{len(universe)} acciones"
    )

    for symbol in universe:

        try:

            log.info(
                f"Analizando {symbol}"
            )

            stock_data = get_stock_data(
                symbol
            )

            if not stock_data:
                continue

            price = stock_data["price"]

            if (
                price <= 1
                or price >= 1000
            ):
                continue

            relative_volume = (
                stock_data[
                    "relative_volume"
                ]
            )

            breakout = (
                stock_data["breakout"]
            )

            news = get_company_news(
                symbol
            )

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

            score = calculate_score(
                data
            )

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
                    f"SETUP DETECTADO: "
                    f"{symbol}"
                )

            time.sleep(5)

        except Exception as e:

            log.error(
                f"{symbol} {e}"
            )

            time.sleep(10)

    return signals