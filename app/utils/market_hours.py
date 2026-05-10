from datetime import datetime

import pytz


def market_is_open():

    madrid = pytz.timezone(
        "Europe/Madrid"
    )

    now = datetime.now(madrid)

    # sábado o domingo
    if now.weekday() >= 5:
        return False

    hour = now.hour
    minute = now.minute

    current = hour + minute / 60

    # =========================
    # Asia
    # Japón / Corea / Taiwán
    # 01:00 -> 08:00 España
    # =========================

    asia_open = (
        1 <= current <= 8
    )

    # =========================
    # Europa
    # 09:00 -> 17:30 España
    # =========================

    europe_open = (
        9 <= current <= 17.5
    )

    # =========================
    # USA
    # 15:30 -> 22:00 España
    # =========================

    usa_open = (
        15.5 <= current <= 22
    )

    return (
        asia_open
        or europe_open
        or usa_open
    )