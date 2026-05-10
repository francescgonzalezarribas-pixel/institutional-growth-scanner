from datetime import datetime

import pytz


def market_is_open():

    madrid = pytz.timezone(
        "Europe/Madrid"
    )

    now = datetime.now(madrid)

    hour = now.hour
    minute = now.minute

    current = hour + minute / 60

    weekday = now.weekday()

    # =========================
    # Asia
    # Domingo noche incluido
    # =========================

    asia_day = weekday in [0, 1, 2, 3, 4]

    # domingo noche España
    if weekday == 6 and current >= 1:
        asia_day = True

    asia_open = (
        asia_day
        and 1 <= current <= 8
    )

    # =========================
    # Europa
    # =========================

    europe_open = (
        weekday < 5
        and 9 <= current <= 17.5
    )

    # =========================
    # USA
    # =========================

    usa_open = (
        weekday < 5
        and 15.5 <= current <= 22
    )

    return (
        asia_open
        or europe_open
        or usa_open
    )