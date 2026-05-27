import logging

from config import *
from core.market_data import *

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)

log = logging.getLogger(__name__)

def test_market_data():

    print("\n==============================")
    print("TEST MARKET DATA")
    print("==============================\n")

    data = fetch_quote("NVDA")

    if not data:
        print("ERROR obteniendo datos")
        return

    for k, v in data.items():
        print(f"{k}: {v}")

    print("\n==============================\n")

if __name__ == "__main__":

    log.info("Financial Bot iniciado")

    test_market_data()