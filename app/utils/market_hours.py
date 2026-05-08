from datetime import datetime
import pytz


def market_is_open():

    ny = pytz.timezone(
        "America/New_York"
    )

    now = datetime.now(ny)

    # sábado o domingo
    if now.weekday() >= 5:
        return False

    hour = now.hour
    minute = now.minute

    current = hour + minute / 60

    # 9:30 -> 16:00 NY
    return 9.5 <= current <= 16