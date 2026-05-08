from app.scanner.ipo_scanner import (
    get_recent_ipos
)


BASE_STOCKS = [

    # IA
    "NVDA",
    "PLTR",
    "ARM",

    # Space
    "RKLB",
    "ASTS",

    # Quantum
    "IONQ",

    # Growth
    "HIMS",
    "TEM",

    # Cybersecurity
    "CRWD",

    # Semis
    "AMD",

    # IPO / newer
    "RDDT",
    "CART"
]


def get_market_universe():

    ipo_stocks = (
        get_recent_ipos()[:5]
    )

    combined = (
        BASE_STOCKS
        + ipo_stocks
    )

    return list(set(combined))