import asyncio
import time

from app.scanner.market_scanner import scan_market
from app.telegram.telegram_bot import send_message
from app.telegram.message_builder import build_signal

from app.utils.logger import log
from app.utils.market_hours import (
    market_is_open
)


# =========================
# ANTI DUPLICADOS
# =========================

sent_signals = {}

# 4 horas
SIGNAL_COOLDOWN = 14400


async def process_signals():

    signals = scan_market()

    now = time.time()

    for signal in signals:

        key = (
            f"{signal['symbol']}"
            f"-{signal['headline']}"
        )

        # ya enviada recientemente
        if key in sent_signals:

            last_sent = sent_signals[key]

            if (
                now - last_sent
                < SIGNAL_COOLDOWN
            ):

                continue

        msg = build_signal(signal)

        await send_message(msg)

        sent_signals[key] = now

        log.info(
            f"Señal enviada: "
            f"{signal['symbol']}"
        )


async def main():

    log.info(
        "Institutional Growth Scanner iniciado"
    )

    while True:

        try:

            if market_is_open():

                await process_signals()

                # 20 min
                await asyncio.sleep(1200)

            else:

                log.info(
                    "Mercados cerrados"
                )

                # revisar cada 5 min
                await asyncio.sleep(300)

        except Exception as e:

            log.error(e)

            await asyncio.sleep(60)


if __name__ == "__main__":
    asyncio.run(main())