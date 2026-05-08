import finnhub

from app.config import FINNHUB_API_KEY

client = finnhub.Client(
    api_key=FINNHUB_API_KEY
)


def get_recent_ipos():

    try:

        data = client.ipo_calendar(
            _from="2026-01-01",
            to="2026-12-31"
        )

        ipo_list = data.get(
            "ipoCalendar",
            []
        )

        symbols = []

        for ipo in ipo_list:

            symbol = ipo.get("symbol")

            if not symbol:
                continue

            exchange = ipo.get("exchange")

            if not exchange:
                continue

            exchange = exchange.lower()

            if (
                "nasdaq" in exchange
                or "nyse" in exchange
            ):

                symbols.append(symbol)

        return list(set(symbols))

    except Exception as e:

        print(f"IPO scanner error: {e}")

        return []