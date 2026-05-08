from app.scanner.ipo_scanner import (
    get_recent_ipos
)


BASE_STOCKS = [

    # IA
    "NVDA",
    "PLTR",
    "SMCI",
    "ARM",
    "SOUN",
    "BBAI",

    # Space / Defense
    "RKLB",
    "ASTS",
    "LUNR",

    # Quantum / Robotics
    "IONQ",
    "SYM",

    # Cybersecurity
    "CRWD",
    "PANW",

    # Growth
    "HIMS",
    "TEM",
    "CAVA",

    # Semiconductors
    "AMD",
    "TSM",
    "MU",

    # IPO / Newer
    "RDDT",
    "CART"
]


def get_market_universe():

    ipo_stocks = get_recent_ipos()

    combined = (
        BASE_STOCKS
        + ipo_stocks
    )

    return list(set(combined))