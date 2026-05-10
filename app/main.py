import asyncio

from app.scanner.market_scanner import scan_market
from app.telegram.telegram_bot import send_message
from app.telegram.message_builder import build_signal

from app.utils.logger import log
from app.utils.market_hours import (
    market_is_open
)


sent = set()


async def process_signals():

    signals = scan_market()

    for signal in signals:

        key = (
            f"{signal['symbol']}"
            f"-{signal['headline']}"
        )

        if key in sent:
            continue

        msg = build_signal(signal)

        await send_message(msg)

        sent.add(key)

        log.info(
            f"Señal enviada: {signal['symbol']}"
        )


async def main():

    log.info(
        "Institutional Growth Scanner iniciado"
    )

    while True:

        try:

            if market_is_open():

                log.info(
                    "Mercado abierto → escaneando"
                )

                await process_signals()

                await asyncio.sleep(900)

            else:

                log.info(
                    "Mercado cerrado"
                )

                await asyncio.sleep(21600)

        except Exception as e:

            log.error(e)

            await asyncio.sleep(300)


if __name__ == "__main__":
    asyncio.run(main())