import time
import requests
from datetime import datetime

import pytz

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
    USA_STOCKS,
    EUROPE_STOCKS,
    ASIA_STOCKS
)

from app.utils.logger import log


BASE_URL = (
    "https://api.twelvedata.com"
)


# =========================
# SESIÓN ACTUAL
# =========================

def get_active_market():

    madrid = pytz.timezone(
        "Europe/Madrid"
    )

    now = datetime.now(madrid)

    current = (
        now.hour
        + now.minute / 60
    )

    weekday = now.weekday()

    # Asia
    if (
        weekday < 5
        and (
            current >= 1
            and current <= 8
        )
    ):
        return "ASIA"

    # Europa
    if (
        weekday < 5
        and (
            current >= 9
            and current <= 17.5
        )
    ):
        return "EUROPE"

    # USA
    if (
        weekday < 5
        and (
            current >= 15.5
            and current <= 22
        )
    ):
        return "USA"

    return None


# =========================
# UNIVERSO DINÁMICO
# =========================

def get_active_universe():

    market = get_active_market()

    if market == "ASIA":

        log.info(
            "Sesión activa: ASIA"
        )

        return ASIA_STOCKS, 72

    elif market == "EUROPE":

        log.info(
            "Sesión activa: EUROPA"
        )

        return EUROPE_STOCKS, 75

    elif market == "USA":

        log.info(
            "Sesión activa: USA"
        )

        return USA_STOCKS, 85

    return [], 999


# =========================
# DATOS
# =========================

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


# =========================
# SCANNER
# =========================

def scan_market():

    signals = []

    universe, min_score = (
        get_active_universe()
    )

    if not universe:

        log.info(
            "No hay mercado activo"
        )

        return signals

    log.info(
        f"Universo activo: "
        f"{len(universe)} acciones"
    )

    for symbol in universe:

        try:

            stock_data = get_stock_data(
                symbol
            )

            if not stock_data:
                continue

            relative_volume = (
                stock_data[
                    "relative_volume"
                ]
            )

            news = get_company_news(
                symbol
            )

            has_news = len(news) > 0

            headline = (
                news[0]["headline"]
                if has_news
                else "Momentum detectado"
            )

            data = {
                "symbol": symbol,
                "price": stock_data[
                    "price"
                ],
                "relative_volume": relative_volume,
                "breakout": stock_data[
                    "breakout"
                ],
                "ipo": symbol in [
                    "ARM",
                    "RKLB",
                    "ASTS",
                    "IONQ",
                    "TEM",
                    "RDDT",
                    "CART"
                ],
                "sector_hot": True,
                "institutional_volume": (
                    relative_volume >= 2
                ),
                "has_news": has_news,
                "headline": headline,
                "sector": "Growth"
            }

            score = calculate_score(
                data
            )

            data["score"] = score

            if score >= 70:

                log.info(
                    f"{symbol} | "
                    f"RVOL={relative_volume} | "
                    f"SCORE={score}"
                )

            if score >= min_score:

                signals.append(data)

                log.info(
                    f"SETUP DETECTADO: "
                    f"{symbol}"
                )

            time.sleep(3)

        except Exception as e:

            log.error(
                f"{symbol} {e}"
            )

            time.sleep(5)

    return signals