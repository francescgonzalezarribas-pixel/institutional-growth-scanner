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
    # Japón / Corea / Taiwán
    # Ajustado Railway UTC
    # =========================

    asia_day = weekday in [0, 1, 2, 3, 4]

    # domingo noche España
    if weekday == 6 and current >= 23:
        asia_day = True

    asia_open = (
        asia_day
        and (
            current >= 23
            or current <= 6
        )
    )

    # =========================
    # Europa
    # Ajustado Railway UTC
    # =========================

    europe_open = (
        weekday < 5
        and 7 <= current <= 15.5
    )

    # =========================
    # USA
    # Ajustado Railway UTC
    # =========================

    usa_open = (
        weekday < 5
        and 13.5 <= current <= 20
    )

    return (
        asia_open
        or europe_open
        or usa_open
    )