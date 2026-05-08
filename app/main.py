import time

from app.scanner.market_scanner import scan_market
from app.telegram.telegram_bot import send_message
from app.telegram.message_builder import build_signal

from app.utils.logger import log


sent = set()


def main():

    log.info("Institutional Growth Scanner iniciado")

    while True:

        try:

            signals = scan_market()

            for signal in signals:

                key = (
                    f"{signal['symbol']}"
                    f"-{signal['headline']}"
                )

                if key in sent:
                    continue

                msg = build_signal(signal)

                send_message(msg)

                sent.add(key)

                log.info(
                    f"Señal enviada: {signal['symbol']}"
                )

            time.sleep(300)

        except Exception as e:

            log.error(e)

            time.sleep(60)


if __name__ == "__main__":
    main()