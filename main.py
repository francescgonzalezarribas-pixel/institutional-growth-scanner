import os
import time
import json
import logging
import threading
import urllib.request
import urllib.parse
import ccxt
import finnhub
import yfinance as yf
import requests
import feedparser
import pytz
from datetime import datetime, timezone, timedelta

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)

TZ = pytz.timezone("Europe/Madrid")

# ============================================
# CONFIG
# ============================================

TELEGRAM_TOKEN   = os.environ.get('TELEGRAM_TOKEN', '')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '')
FINNHUB_API_KEY  = os.environ.get('FINNHUB_API_KEY', '')

CAPITAL      = 20
LEVERAGE     = 3
SL_PCT       = 0.015
TP1_PCT      = 0.020
TP2_PCT      = 0.040
TP3_PCT      = 0.060
TRAIL_PCT    = 0.015
STATE_FILE   = 'igs_state.json'

finnhub_client = finnhub.Client(api_key=FINNHUB_API_KEY) if FINNHUB_API_KEY else None

exchange = ccxt.bitget({
    'apiKey':   os.environ.get('BITGET_API_KEY', ''),
    'secret':   os.environ.get('BITGET_API_SECRET', ''),
    'password': os.environ.get('BITGET_API_PASSPHRASE', ''),
    'options':  {'defaultType': 'swap'},
})

# ============================================
# TELEGRAM
# ============================================

def tg(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url  = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = urllib.parse.urlencode({
            'chat_id':    TELEGRAM_CHAT_ID,
            'text':       msg,
            'parse_mode': 'Markdown'
        }).encode()
        urllib.request.urlopen(url, data=data, timeout=10)
    except Exception as e:
        log.error(f"Telegram: {e}")

# ============================================
# SÍMBOLOS
# ============================================

USA_STOCKS = [
    "NVDA", "AMD", "META", "TSLA", "GOOGL", "MSFT",
    "INTC", "AMZN", "AAPL", "COIN", "PLTR", "BABA",
    "MSTR", "MU", "ORCL", "ARM", "TSM", "CRWV",
    "OKLO", "GME", "HOOD", "APP", "RKLB", "IONQ",
    "SOUN", "SNDK", "NBIS", "NFLX", "AVGO", "MRVL",
    "AMAT", "KLAC", "WMT", "COST", "LLY", "XOM",
    "RDDT", "FUTU", "JD", "GE", "UNH", "COP",
]

EUROPE_STOCKS = {
    "BMW.DE": "BMW", "SAP.DE": "SAP", "SIE.DE": "Siemens",
    "ADS.DE": "Adidas", "ALV.DE": "Allianz", "DBK.DE": "Deutsche Bank",
    "VOW3.DE": "Volkswagen", "BAYN.DE": "Bayer", "BAS.DE": "BASF",
    "AIR.PA": "Airbus", "MC.PA": "LVMH", "BNP.PA": "BNP Paribas",
    "TTE.PA": "TotalEnergies", "OR.PA": "L'Oréal", "RMS.PA": "Hermès",
    "TEF.MC": "Telefónica", "SAN.MC": "Santander", "BBVA.MC": "BBVA",
    "ITX.MC": "Inditex", "IBE.MC": "Iberdrola", "REP.MC": "Repsol",
}

ASIA_STOCKS = {
    "7203.T": "Toyota", "6758.T": "Sony", "9984.T": "SoftBank",
    "6861.T": "Keyence", "8306.T": "Mitsubishi UFJ",
    "0700.HK": "Tencent", "9988.HK": "Alibaba HK",
    "005930.KS": "Samsung", "000660.KS": "SK Hynix",
}

USA_SYMBOL_MAP = {s: f"{s}/USDT:USDT" for s in [
    "NVDA", "AMD", "META", "TSLA", "GOOGL", "MSFT",
    "INTC", "AMZN", "AAPL", "COIN", "PLTR", "BABA",
    "MSTR", "MU", "ORCL", "ARM", "SOUN", "SNDK",
    "NBIS", "NFLX", "AVGO", "MRVL", "GME", "HOOD",
]}

RSS_FEEDS = {
    "CNBC":        "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    "Reuters":     "https://feeds.reuters.com/reuters/businessNews",
    "MarketWatch": "https://feeds.content.dowjones.io/public/rss/mw_realtimeheadlines",
    "Yahoo":       "https://finance.yahoo.com/rss/topstories",
}

HOT_KEYWORDS = [
    "earnings", "beat", "miss", "merger", "acquisition",
    "FDA", "approval", "ipo", "split", "buyback",
    "guidance", "revenue", "upgrade", "downgrade",
    "AI", "chip", "semiconductor", "bankruptcy",
    "record", "surge", "crash", "layoffs",
]

# ============================================
# ESTADO
# ============================================

state = {
    "position":       None,   # trade activo
    "signals_sent":   {},     # symbol -> timestamp última señal
    "daily_signals":  [],     # señales del día
    "premarket_top":  [],     # top movers premercado USA
    "last_reset":     "",     # fecha último reset
}

def load_state():
    global state
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                saved = json.load(f)
                state.update(saved)
        except:
            pass

def save_state():
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        log.error(f"save_state: {e}")

def reset_daily():
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    if state["last_reset"] != today:
        state["signals_sent"]  = {}
        state["daily_signals"] = []
        state["premarket_top"] = []
        state["last_reset"]    = today
        save_state()
        log.info("🔄 Reset diario")

def can_send_signal(symbol, cooldown_h=4):
    """Evita señales repetidas — cooldown de 4h por símbolo."""
    last = state["signals_sent"].get(symbol, 0)
    return (time.time() - last) >= cooldown_h * 3600

def mark_signal_sent(symbol):
    state["signals_sent"][symbol] = time.time()
    save_state()

# ============================================
# NOTICIAS
# ============================================

def fetch_rss():
    news = []
    for source, url in RSS_FEEDS.items():
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:5]:
                title = entry.get("title", "")
                if title:
                    news.append({
                        "source": source,
                        "title":  title,
                        "link":   entry.get("link", ""),
                    })
        except Exception as e:
            log.error(f"RSS {source}: {e}")
        time.sleep(0.3)
    return news

def fetch_stock_news(symbol):
    try:
        ticker = yf.Ticker(symbol)
        news   = ticker.news or []
        return [{
            "source": n.get("publisher", "Yahoo"),
            "title":  n.get("title", ""),
            "link":   n.get("link", ""),
            "symbol": symbol,
        } for n in news[:2]]
    except:
        return []

def fetch_ipo_news():
    try:
        start = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        end   = datetime.now().strftime("%Y-%m-%d")
        url   = f"https://efts.sec.gov/LATEST/search-index?forms=S-1&dateRange=custom&startdt={start}&enddt={end}"
        resp  = requests.get(url, timeout=10, headers={"User-Agent": "igs-bot contact@example.com"})
        data  = resp.json()
        ipos  = []
        for hit in data.get("hits", {}).get("hits", [])[:5]:
            src = hit.get("_source", {})
            ipos.append({
                "company": src.get("entity_name", ""),
                "link":    f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={src.get('entity_id', '')}",
            })
        return ipos
    except Exception as e:
        log.error(f"IPO SEC: {e}")
        return []

def get_hot_keyword(title):
    title_lower = title.lower()
    for kw in HOT_KEYWORDS:
        if kw.lower() in title_lower:
            return kw
    return None

# ============================================
# PRECIOS
# ============================================

MIN_INTERVAL      = 1.2
last_request_time = 0

def rate_limit_guard():
    global last_request_time
    elapsed = time.time() - last_request_time
    if elapsed < MIN_INTERVAL:
        time.sleep(MIN_INTERVAL - elapsed)
    last_request_time = time.time()

def fetch_yfinance_data(symbols_dict):
    """Obtiene precios via yfinance para Europa/Asia."""
    data = []
    for symbol, name in symbols_dict.items():
        try:
            ticker  = yf.Ticker(symbol)
            hist_1d = ticker.history(period="1d", interval="1m")
            hist_5d = ticker.history(period="5d", interval="1d")
            if hist_1d.empty or hist_5d.empty or len(hist_5d) < 2:
                continue
            price     = float(hist_1d["Close"].iloc[-1])
            open_     = float(hist_1d["Open"].iloc[0])
            prev      = float(hist_5d["Close"].iloc[-2])
            reference = open_ if open_ > 0 else prev
            if price == 0 or reference == 0:
                continue
            change = round((price - reference) / reference * 100, 2)
            data.append({
                "symbol":    symbol,
                "name":      name,
                "price":     price,
                "open":      open_,
                "prev":      prev,
                "change":    change,
                "reference": reference,
            })
        except Exception as e:
            log.error(f"yfinance {symbol}: {e}")
    return data

def fetch_finnhub_data(symbols):
    """Obtiene precios USA via Finnhub."""
    data = []
    for symbol in symbols:
        try:
            rate_limit_guard()
            if not finnhub_client:
                break
            quote = finnhub_client.quote(symbol)
            if not quote or quote.get("c", 0) == 0:
                continue
            price  = quote["c"]
            open_  = quote["o"]
            prev   = quote["pc"]
            ref    = open_ if open_ > 0 else prev
            change = round((price - prev) / prev * 100, 2) if prev else 0
            data.append({
                "symbol":    symbol,
                "name":      symbol,
                "price":     price,
                "open":      open_,
                "prev":      prev,
                "change":    change,
                "volume":    quote.get("v", 0),
                "reference": ref,
            })
        except Exception as e:
            log.error(f"finnhub {symbol}: {e}")
    return data

# ============================================
# ANÁLISIS Y SCORING
# ============================================

def analyze_signal(item, news_list):
    """Genera análisis completo con nota de probabilidad."""
    symbol = item["symbol"]
    name   = item.get("name", symbol)
    price  = item["price"]
    change = item["change"]

    # Score base por cambio %
    abs_change = abs(change)
    if abs_change >= 5:
        price_score = 5
    elif abs_change >= 3:
        price_score = 4
    elif abs_change >= 2:
        price_score = 3
    elif abs_change >= 1:
        price_score = 2
    else:
        price_score = 1

    # Score noticias
    news_score   = 0
    related_news = []
    for n in news_list:
        title = n.get("title", "")
        if symbol in title.upper() or name.upper() in title.upper():
            kw = get_hot_keyword(title)
            if kw:
                news_score = max(news_score, 3)
                related_news.append((n, kw))
            else:
                news_score = max(news_score, 1)
                related_news.append((n, None))

    # Score volumen
    volume       = item.get("volume", 0)
    volume_score = 0
    if volume > 0:
        if volume > 50_000_000:
            volume_score = 3
        elif volume > 20_000_000:
            volume_score = 2
        elif volume > 5_000_000:
            volume_score = 1

    # Total score y probabilidad
    total = price_score * 0.5 + news_score * 0.3 + volume_score * 0.2
    if total >= 4:
        prob = 85
        grade = "🔥🔥 MUY ALTA"
    elif total >= 3:
        prob = 70
        grade = "🔥 ALTA"
    elif total >= 2:
        prob = 55
        grade = "📈 MEDIA"
    else:
        prob = 35
        grade = "📊 BAJA"

    # Niveles de precio
    direction = "alcista" if change > 0 else "bajista"
    if direction == "alcista":
        sl  = round(price * (1 - SL_PCT), 2)
        tp1 = round(price * (1 + TP1_PCT), 2)
        tp2 = round(price * (1 + TP2_PCT), 2)
        tp3 = round(price * (1 + TP3_PCT), 2)
    else:
        sl  = round(price * (1 + SL_PCT), 2)
        tp1 = round(price * (1 - TP1_PCT), 2)
        tp2 = round(price * (1 - TP2_PCT), 2)
        tp3 = round(price * (1 - TP3_PCT), 2)

    return {
        "symbol":       symbol,
        "name":         name,
        "price":        price,
        "change":       change,
        "direction":    direction,
        "prob":         prob,
        "grade":        grade,
        "total_score":  round(total, 2),
        "sl":           sl,
        "tp1":          tp1,
        "tp2":          tp2,
        "tp3":          tp3,
        "related_news": related_news[:2],
    }

# ============================================
# MENSAJES TELEGRAM
# ============================================

def send_signal(analysis, market):
    """Señal completa con análisis."""
    s         = analysis
    dir_emoji = "🚀" if s["direction"] == "alcista" else "📉"
    mkt_emoji = {"usa": "🇺🇸", "europe": "🇪🇺", "asia": "🌏"}.get(market, "📈")

    msg = (
        f"⚡ *SEÑAL — {s['name']} ({s['symbol']})*\n"
        f"{mkt_emoji} Mercado: *{market.upper()}*\n\n"
        f"{dir_emoji} Dirección: *{s['direction'].upper()}*\n"
        f"📊 Probabilidad: *{s['grade']}* ({s['prob']}%)\n"
        f"📈 Cambio: *{s['change']}%*\n\n"
        f"💰 *Precio entrada:* {s['price']}\n"
        f"🛑 *Stop Loss:* {s['sl']}\n"
        f"🎯 *TP1:* {s['tp1']} (+{TP1_PCT*100}%)\n"
        f"🎯 *TP2:* {s['tp2']} (+{TP2_PCT*100}%)\n"
        f"🎯 *TP3:* {s['tp3']} (+{TP3_PCT*100}%)\n"
    )

    if s["related_news"]:
        msg += "\n📰 *Noticias relacionadas:*\n"
        for n, kw in s["related_news"]:
            msg += f"• [{n['source']}] {n['title'][:70]}...\n"
            if kw:
                msg += f"  🔑 Keyword: *{kw.upper()}*\n"

    tg(msg)

def send_premarket_report(top_movers, market, news):
    """Reporte premercado."""
    emoji = {"usa": "🇺🇸", "europe": "🇪🇺", "asia": "🌏"}.get(market, "📈")
    now   = datetime.now(TZ).strftime("%H:%M")
    msg   = f"{emoji} *PREMERCADO {market.upper()} — {now}*\n\n"

    alcistas = [m for m in top_movers if m["change"] > 0][:5]
    bajistas = [m for m in top_movers if m["change"] < 0][:3]

    if alcistas:
        msg += "🚀 *Subidas:*\n"
        for m in alcistas:
            msg += f"  • *{m['name']}* +{m['change']}% @ {m['price']}\n"
        msg += "\n"

    if bajistas:
        msg += "📉 *Bajadas:*\n"
        for m in bajistas:
            msg += f"  • *{m['name']}* {m['change']}% @ {m['price']}\n"
        msg += "\n"

    # IPOs si es USA
    if market == "usa":
        ipos = fetch_ipo_news()
        if ipos:
            msg += "🆕 *IPOs recientes:*\n"
            for ipo in ipos[:3]:
                msg += f"  • {ipo['company']}\n"
            msg += "\n"

    # Noticias calientes
    hot_news = [n for n in news if get_hot_keyword(n.get("title", ""))]
    if hot_news:
        msg += "📰 *Noticias calientes:*\n"
        for n in hot_news[:3]:
            kw = get_hot_keyword(n["title"])
            msg += f"  • [{n['source']}] {n['title'][:60]}...\n"
            if kw:
                msg += f"    🔑 {kw.upper()}\n"

    tg(msg)

def send_market_open(market, top_movers):
    """Mensaje apertura de mercado."""
    emoji = {"usa": "🇺🇸", "europe": "🇪🇺", "asia": "🌏"}.get(market, "📈")
    now   = datetime.now(TZ).strftime("%H:%M")
    msg   = f"{emoji} *APERTURA {market.upper()} — {now}*\n\n"
    msg  += "🔥 *Más calientes:*\n"
    for m in top_movers[:5]:
        e = "🚀" if m["change"] > 0 else "📉"
        msg += f"{e} *{m['name']}* {m['change']}% @ {m['price']}\n"
    tg(msg)

def send_evening_summary():
    """Resumen del día."""
    now  = datetime.now(TZ).strftime("%d/%m/%Y")
    msg  = f"🌙 *RESUMEN — {now}*\n\n"
    msg += f"📊 Señales enviadas: *{len(state['daily_signals'])}*\n\n"
    if state["daily_signals"]:
        msg += "*Top señales del día:*\n"
        top = sorted(state["daily_signals"], key=lambda x: abs(x["change"]), reverse=True)[:5]
        for s in top:
            e = "🚀" if s["change"] > 0 else "📉"
            msg += f"{e} *{s['name']}* {s['change']}%\n"
    tg(msg)

# ============================================
# AUTO TRADE — USA más caliente
# ============================================

def open_trade(signal):
    """Abre trade en Bitget con el más caliente del premercado."""
    symbol  = signal["symbol"]
    pair    = USA_SYMBOL_MAP.get(symbol)
    if not pair:
        log.warning(f"Sin par Bitget para {symbol}")
        return

    price = signal["price"]
    size  = round((CAPITAL * LEVERAGE) / price, 4)
    if size < 0.01:
        size = 0.01

    try:
        exchange.set_leverage(LEVERAGE, pair, params={
            'marginMode':  'isolated',
            'productType': 'USDT-FUTURES',
        })
        time.sleep(0.5)
        exchange.create_order(pair, 'market', 'buy', size, None, {
            'marginMode': 'isolated',
            'leverage':   str(LEVERAGE),
            'reduceOnly': False,
        })
        state["position"] = {
            "pair":        pair,
            "symbol":      symbol,
            "side":        "long",
            "entry":       price,
            "size":        size,
            "sl":          signal["sl"],
            "tp1":         signal["tp1"],
            "tp2":         signal["tp2"],
            "tp3":         signal["tp3"],
            "entry_time":  time.time(),
            "trail_best":  price,
            "trail_sl":    round(price * (1 - TRAIL_PCT), 4),
        }
        save_state()
        tg(
            f"🟢 *AUTO TRADE LONG* — {pair}\n"
            f"💰 {CAPITAL} USDT x{LEVERAGE} | size={size}\n"
            f"🎯 Entrada: {round(price,2)}\n"
            f"🛑 SL: {signal['sl']}\n"
            f"🎯 TP1: {signal['tp1']} | TP2: {signal['tp2']} | TP3: {signal['tp3']}"
        )
        log.info(f"🚀 AUTO TRADE LONG {pair} @ {price}")
    except Exception as e:
        log.error(f"open_trade {pair}: {e}")
        tg(f"❌ Error abriendo trade {pair}: {e}")

def close_trade(reason, price=None):
    """Cierra trade activo."""
    pos = state.get("position")
    if not pos:
        return
    try:
        exchange.create_order(pos["pair"], 'market', 'sell', pos["size"], None, {
            'marginMode': 'isolated',
            'leverage':   str(LEVERAGE),
            'reduceOnly': True,
        })
        p_str = str(round(price, 2)) if price else "N/A"
        tg(f"🏁 *CLOSE LONG* — {pos['pair']}\n📋 {reason}\n💰 Precio: {p_str}")
        log.info(f"🏁 CLOSE {pos['pair']} — {reason}")
    except Exception as e:
        if "22002" in str(e):
            log.warning(f"⚠️ {pos['pair']} ya cerrada")
        else:
            log.error(f"close_trade: {e}")
            tg(f"❌ Error cerrando: {e}")
    finally:
        state["position"] = None
        save_state()

def manage_trade():
    """Gestiona SL/TP/trailing del trade activo."""
    pos = state.get("position")
    if not pos:
        return
    try:
        ticker = exchange.fetch_ticker(pos["pair"])
        price  = float(ticker['last'])
        entry  = pos["entry"]
        pnl    = (price - entry) / entry

        log.info(f"[{pos['pair']}] LONG P={round(price,2)} PnL={round(pnl*100,2)}%")

        # SL
        if price <= pos["sl"]:
            close_trade(f"SL {round(pnl*100,2)}%", price)
            return

        # TP3
        if price >= pos["tp3"]:
            close_trade(f"TP3 +{round(pnl*100,2)}%", price)
            return

        # TP2
        if price >= pos["tp2"] and not pos.get("tp2_hit"):
            pos["tp2_hit"] = True
            tg(f"🎯 *TP2 alcanzado* — {pos['pair']} @ {round(price,2)} (+{round(pnl*100,2)}%)")

        # TP1
        if price >= pos["tp1"] and not pos.get("tp1_hit"):
            pos["tp1_hit"] = True
            tg(f"🎯 *TP1 alcanzado* — {pos['pair']} @ {round(price,2)} (+{round(pnl*100,2)}%)")

        # Trailing
        if price > pos["trail_best"]:
            pos["trail_best"] = price
            pos["trail_sl"]   = round(price * (1 - TRAIL_PCT), 4)
        if price <= pos["trail_sl"] and pos.get("tp1_hit"):
            close_trade(f"Trailing {round(pnl*100,2)}%", price)
            return

        save_state()

    except Exception as e:
        log.error(f"manage_trade: {e}")

# ============================================
# HORARIOS
# ============================================

def now_madrid():
    return datetime.now(TZ)

def t(hhmm):
    return datetime.strptime(hhmm, "%H:%M").time()

def is_between(h1, h2):
    return h1 <= now_madrid().time() <= h2

# ============================================
# LOOP PRINCIPAL
# ============================================

def main_loop():
    load_state()
    log.info("🚀 Institutional Growth Scanner iniciado")
    tg("🚀 *Institutional Growth Scanner*\nMonitorizando USA, Europa, Asia + Noticias")

    flags = {
        "asia_premarket": False,
        "asia_open":      False,
        "europe_premarket": False,
        "europe_open":    False,
        "usa_premarket":  False,
        "usa_open":       False,
        "trade_opened":   False,
        "evening":        False,
    }

    cached_news      = []
    last_news_fetch  = 0
    last_usa_fetch   = 0
    last_eu_fetch    = 0
    last_asia_fetch  = 0

    USA_INTERVAL  = 300
    EU_INTERVAL   = 300
    ASIA_INTERVAL = 300
    NEWS_INTERVAL = 900

    while True:
        try:
            now     = now_madrid().time()
            weekday = now_madrid().weekday()
            ts      = time.time()
            es_finde = weekday >= 5

            # Reset diario
            reset_daily()

            # Reset flags diarios
            if now < t("01:00"):
                for k in flags:
                    flags[k] = False

            # ── NOTICIAS cada 15 min ──────────────────
            if ts - last_news_fetch >= NEWS_INTERVAL:
                last_news_fetch = ts
                def _update_news():
                    global cached_news
                    try:
                        news = fetch_rss()
                        for sym in ["NVDA", "TSLA", "META", "AAPL", "MSFT"]:
                            news += fetch_stock_news(sym)
                            time.sleep(0.3)
                        cached_news = news
                        log.info(f"📰 {len(news)} noticias")
                    except Exception as e:
                        log.error(f"news update: {e}")
                threading.Thread(target=_update_news, daemon=True).start()

            if not es_finde:

                # ── PREMERCADO ASIA 00:30 ─────────────
                if is_between(t("00:30"), t("01:00")) and not flags["asia_premarket"]:
                    flags["asia_premarket"] = True
                    log.info("🌏 Premercado Asia")
                    data = fetch_yfinance_data(ASIA_STOCKS)
                    if data:
                        top = sorted(data, key=lambda x: abs(x["change"]), reverse=True)
                        send_premarket_report(top, "asia", cached_news)

                # ── APERTURA ASIA 01:00 ───────────────
                if is_between(t("01:00"), t("01:10")) and not flags["asia_open"]:
                    flags["asia_open"] = True
                    log.info("🌏 Apertura Asia")
                    data = fetch_yfinance_data(ASIA_STOCKS)
                    if data:
                        top = sorted(data, key=lambda x: abs(x["change"]), reverse=True)
                        send_market_open("asia", top)

                # ── PREMERCADO EUROPA 08:00 ───────────
                if is_between(t("08:00"), t("09:00")) and not flags["europe_premarket"]:
                    flags["europe_premarket"] = True
                    log.info("🇪🇺 Premercado Europa")
                    data = fetch_yfinance_data(EUROPE_STOCKS)
                    if data:
                        top = sorted(data, key=lambda x: abs(x["change"]), reverse=True)
                        send_premarket_report(top, "europe", cached_news)

                # ── APERTURA EUROPA 09:00 ─────────────
                if is_between(t("09:00"), t("09:10")) and not flags["europe_open"]:
                    flags["europe_open"] = True
                    log.info("🇪🇺 Apertura Europa")
                    data = fetch_yfinance_data(EUROPE_STOCKS)
                    if data:
                        top = sorted(data, key=lambda x: abs(x["change"]), reverse=True)
                        send_market_open("europe", top)

                # ── PREMERCADO USA 14:00 ──────────────
                if is_between(t("14:00"), t("15:30")) and not flags["usa_premarket"]:
                    flags["usa_premarket"] = True
                    log.info("🇺🇸 Premercado USA")
                    data = fetch_finnhub_data(USA_STOCKS)
                    if data:
                        top = sorted(data, key=lambda x: abs(x["change"]), reverse=True)
                        state["premarket_top"] = top[:10]
                        save_state()
                        send_premarket_report(top, "usa", cached_news)

                # ── APERTURA USA 15:30 + AUTO TRADE ───
                if is_between(t("15:30"), t("15:40")) and not flags["usa_open"]:
                    flags["usa_open"]   = True
                    flags["trade_opened"] = False
                    log.info("🇺🇸 Apertura USA")
                    data = fetch_finnhub_data(USA_STOCKS)
                    if data:
                        top = sorted(data, key=lambda x: abs(x["change"]), reverse=True)
                        send_market_open("usa", top)
                        # Auto trade con el más caliente alcista
                        if not state.get("position"):
                            alcistas = [m for m in top if m["change"] > 0]
                            if alcistas:
                                best     = alcistas[0]
                                analysis = analyze_signal(best, cached_news)
                                if analysis["prob"] >= 55:
                                    open_trade(analysis)
                                    flags["trade_opened"] = True

                # ── CIERRE AUTOMÁTICO 21:15 ───────────
                if is_between(t("21:15"), t("21:20")) and state.get("position"):
                    log.info("⏰ Cierre automático 21:15")
                    pos   = state["position"]
                    ticker = exchange.fetch_ticker(pos["pair"])
                    price  = float(ticker['last'])
                    close_trade("Cierre automático 21:15", price)

                # ── RESUMEN 22:00 ─────────────────────
                if is_between(t("22:00"), t("22:10")) and not flags["evening"]:
                    flags["evening"] = True
                    send_evening_summary()

                # ── GESTIÓN TRADE ACTIVO ──────────────
                if state.get("position"):
                    manage_trade()

                # ── SEÑALES USA 15:30-21:15 ───────────
                if is_between(t("15:30"), t("21:15")) and \
                   (ts - last_usa_fetch >= USA_INTERVAL):
                    last_usa_fetch = ts
                    data = fetch_finnhub_data(USA_STOCKS)
                    for item in data:
                        if abs(item["change"]) >= 2.0 and can_send_signal(item["symbol"]):
                            analysis = analyze_signal(item, cached_news)
                            if analysis["prob"] >= 55:
                                send_signal(analysis, "usa")
                                mark_signal_sent(item["symbol"])
                                state["daily_signals"].append(analysis)
                                save_state()

                # ── SEÑALES EUROPA 09:00-17:30 ────────
                if is_between(t("09:00"), t("17:30")) and \
                   (ts - last_eu_fetch >= EU_INTERVAL):
                    last_eu_fetch = ts
                    data = fetch_yfinance_data(EUROPE_STOCKS)
                    for item in data:
                        if abs(item["change"]) >= 2.0 and can_send_signal(item["symbol"]):
                            analysis = analyze_signal(item, cached_news)
                            if analysis["prob"] >= 55:
                                send_signal(analysis, "europe")
                                mark_signal_sent(item["symbol"])
                                state["daily_signals"].append(analysis)
                                save_state()

                # ── SEÑALES ASIA 01:00-07:00 ──────────
                if is_between(t("01:00"), t("07:00")) and \
                   (ts - last_asia_fetch >= ASIA_INTERVAL):
                    last_asia_fetch = ts
                    data = fetch_yfinance_data(ASIA_STOCKS)
                    for item in data:
                        if abs(item["change"]) >= 2.0 and can_send_signal(item["symbol"]):
                            analysis = analyze_signal(item, cached_news)
                            if analysis["prob"] >= 55:
                                send_signal(analysis, "asia")
                                mark_signal_sent(item["symbol"])
                                state["daily_signals"].append(analysis)
                                save_state()

            time.sleep(30)

        except KeyboardInterrupt:
            log.info("Bot detenido")
            break
        except Exception as e:
            log.error(f"Error loop: {e}")
            time.sleep(30)

if __name__ == '__main__':
    main_loop()