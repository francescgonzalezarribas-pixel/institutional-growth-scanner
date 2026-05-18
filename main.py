import ccxt
import time
import os
import json
import logging
import urllib.request
import urllib.parse
import pytz
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)

TZ = pytz.timezone("Europe/Madrid")

# ============================================
# TELEGRAM
# ============================================

TG_TOKEN   = os.environ.get('TELEGRAM_TOKEN', '')
TG_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '')

def tg(msg):
    if not TG_TOKEN or not TG_CHAT_ID: return
    try:
        url  = "https://api.telegram.org/bot" + TG_TOKEN + "/sendMessage"
        data = urllib.parse.urlencode({'chat_id': TG_CHAT_ID, 'text': msg}).encode()
        urllib.request.urlopen(url, data=data, timeout=10)
    except Exception as e: log.error("Telegram: " + str(e))

# ============================================
# EXCHANGE
# ============================================

exchange = ccxt.bitget({
    'apiKey':   os.environ['BITGET_API_KEY'],
    'secret':   os.environ['BITGET_API_SECRET'],
    'password': os.environ['BITGET_API_PASSPHRASE'],
    'options':  {'defaultType': 'swap'},
    'timeout':  10000,
})

def utcnow(): return datetime.now(timezone.utc)
def now_madrid(): return datetime.now(TZ)

# ============================================
# CONFIG CRYPTO SCANNER
# ============================================

SCAN_TF         = '15m'
SCAN_CAPITAL    = 10
SCAN_LEVERAGE   = 3
SCAN_MAX_POS    = 2
SCAN_SL_PCT     = 0.015
SCAN_TRAIL_PCT  = 0.01
SCAN_BE_TRIGGER = 0.005
SCAN_VOL_MIN    = 1_000_000
SCAN_VOL_MAX    = 100_000_000
SCAN_VOL_SPIKE  = 4.0
SCAN_PRICE_MOVE = 0.01
SCAN_RSI_MIN    = 60
SCAN_INTERVAL   = 300
API_PAUSE       = 0.3
HEARTBEAT_H     = 4
SCAN_STATE_FILE = 'scanner_state.json'

# ============================================
# CONFIG STOCKS
# ============================================

STOCK_CAPITAL    = 20
STOCK_LEVERAGE   = 3
STOCK_MAX_POS    = 3
STOCK_SL_PCT     = 0.015
STOCK_BE_TRIGGER = 0.003
STOCK_BE_TIME_H  = 3
STOCK_EMA_FAST   = 9
STOCK_EMA_SLOW   = 21
STOCK_RSI_PERIOD = 14
STOCK_EMA_MIN_GAP = 0.001
STOCK_TIMEFRAME_H = '1h'
STOCK_TIMEFRAME_M = '15m'
STOCK_TOP_VOLUME  = 20
STOCK_POLL        = 60
STOCK_STATE_FILE  = 'stock_state.json'
STOCK_OPEN_H      = "16:00"
STOCK_CLOSE_H     = "21:15"

MANUAL_PAIRS = [
    "NVDA/USDT:USDT", "AMD/USDT:USDT",  "META/USDT:USDT",
    "TSLA/USDT:USDT", "GOOGL/USDT:USDT","MSFT/USDT:USDT",
    "INTC/USDT:USDT", "AMZN/USDT:USDT", "AAPL/USDT:USDT",
    "COIN/USDT:USDT", "PLTR/USDT:USDT", "BABA/USDT:USDT",
    "MSTR/USDT:USDT", "GME/USDT:USDT",  "HOOD/USDT:USDT",
    "SOUN/USDT:USDT", "CRWV/USDT:USDT", "RKLB/USDT:USDT",
    "IONQ/USDT:USDT", "OKLO/USDT:USDT", "CRCL/USDT:USDT",
    "NBIS/USDT:USDT", "SNDK/USDT:USDT", "ARM/USDT:USDT",
    "MU/USDT:USDT",   "ORCL/USDT:USDT", "NFLX/USDT:USDT",
    "AVGO/USDT:USDT", "MRVL/USDT:USDT", "RDDT/USDT:USDT",
]

# ============================================
# HORARIO STOCKS
# ============================================

def is_trading_hours():
    now = now_madrid().time()
    t1  = datetime.strptime(STOCK_OPEN_H,  "%H:%M").time()
    t2  = datetime.strptime(STOCK_CLOSE_H, "%H:%M").time()
    return t1 <= now <= t2

def is_weekday():
    return now_madrid().weekday() < 5

def is_close_time():
    now = now_madrid().time()
    t1  = datetime.strptime("21:15", "%H:%M").time()
    t2  = datetime.strptime("21:20", "%H:%M").time()
    return t1 <= now <= t2

# ============================================
# INDICADORES COMPARTIDOS
# ============================================

def calc_rsi(closes, period=14):
    if len(closes) < period + 1: return 50.0
    diffs  = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains  = [max(d, 0) for d in diffs]
    losses = [max(-d, 0) for d in diffs]
    ag     = sum(gains[-period:]) / period
    al     = sum(losses[-period:]) / period
    if al == 0: return 100.0
    return 100 - (100 / (1 + ag / al))

def calc_ema(values, period):
    k   = 2 / (period + 1)
    ema = [values[0]]
    for v in values[1:]:
        ema.append(v * k + ema[-1] * (1 - k))
    return ema

def calc_vwap(candles):
    cum_tv, cum_v = 0.0, 0.0
    for c in candles:
        typ     = (c[2] + c[3] + c[4]) / 3
        cum_tv += typ * c[5]
        cum_v  += c[5]
    return (cum_tv / cum_v) if cum_v > 0 else None

def get_stock_signals(candles):
    if len(candles) < STOCK_EMA_SLOW + 5: return None
    closes = [c[4] for c in candles]
    ema_f  = calc_ema(closes, STOCK_EMA_FAST)
    ema_s  = calc_ema(closes, STOCK_EMA_SLOW)
    rsi    = calc_rsi(closes[-30:], STOCK_RSI_PERIOD)
    price  = closes[-1]
    ef_now, ef_prev = ema_f[-1], ema_f[-2]
    es_now, es_prev = ema_s[-1], ema_s[-2]
    ema_gap = abs(ef_now - es_now) / es_now
    vwap    = calc_vwap(candles[-24:])
    return {
        "price":          price,
        "rsi":            rsi,
        "ema_gap":        ema_gap,
        "cross_long":     ef_prev <= es_prev and ef_now > es_now,
        "cross_short":    ef_prev >= es_prev and ef_now < es_now,
        "aligned_long":   ef_now > es_now,
        "aligned_short":  ef_now < es_now,
        "price_ok_long":  price >= ef_now,
        "price_ok_short": price <= ef_now,
        "above_vwap":     price > vwap if vwap else True,
        "below_vwap":     price < vwap if vwap else True,
        "last_ts":        candles[-1][0],
    }

def confirms_15m(pair, direction):
    try:
        candles = exchange.fetch_ohlcv(pair, STOCK_TIMEFRAME_M, limit=50)
        if not candles: return True
        sig = get_stock_signals(candles)
        if not sig: return True
        if direction == 'long':
            return sig['aligned_long'] and sig['price_ok_long']
        else:
            return sig['aligned_short'] and sig['price_ok_short']
    except Exception as e:
        log.error(f"confirms_15m {pair}: {e}")
        return True

def get_trail_pct_stock(pnl):
    if pnl >= 0.03:   return 0.005
    elif pnl >= 0.01: return 0.010
    else:             return 0.015

# ============================================
# ESTADO STOCKS
# ============================================

def default_stock_state():
    return {
        'side': None, 'entry_price': 0.0, 'size': 0.0,
        'be_activated': False, 'trail_best': 0.0,
        'trail_sl': 0.0, 'last_sig_ts': 0, 'entry_time': 0,
    }

def load_stock_state(pairs):
    if os.path.exists(STOCK_STATE_FILE):
        try:
            with open(STOCK_STATE_FILE) as f:
                saved = json.load(f)
            state = {}
            for pair in pairs:
                state[pair] = default_stock_state()
                if pair in saved:
                    state[pair].update(saved[pair])
            return state
        except Exception as e:
            log.warning(f"Stock state load: {e}")
    return {pair: default_stock_state() for pair in pairs}

def save_stock_state(state):
    try:
        with open(STOCK_STATE_FILE, 'w') as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        log.error(f"save_stock_state: {e}")

def reset_stock_position(ps):
    ps.update({
        'side': None, 'entry_price': 0.0, 'size': 0.0,
        'be_activated': False, 'trail_best': 0.0,
        'trail_sl': 0.0, 'entry_time': 0,
    })

# ============================================
# TRADING STOCKS
# ============================================

def force_leverage_stock(pair):
    try:
        exchange.set_leverage(STOCK_LEVERAGE, pair, params={
            'marginMode': 'isolated', 'productType': 'USDT-FUTURES',
        })
    except Exception as e:
        log.warning(f"set_leverage {pair}: {e}")

def fetch_price(pair):
    try:
        return float(exchange.fetch_ticker(pair)['last'])
    except Exception as e:
        log.error(f"fetch_price {pair}: {e}")
        return None

def open_stock_position(pair, direction, price):
    size      = round((STOCK_CAPITAL * STOCK_LEVERAGE) / price, 4)
    if size < 0.01: size = 0.01
    ccxt_side = 'buy' if direction == 'long' else 'sell'
    try:
        force_leverage_stock(pair)
        time.sleep(0.5)
        exchange.create_order(pair, 'market', ccxt_side, size, None, {
            'marginMode': 'isolated', 'leverage': str(STOCK_LEVERAGE), 'reduceOnly': False,
        })
        sl    = round(price * (1 - STOCK_SL_PCT) if direction == 'long' else price * (1 + STOCK_SL_PCT), 4)
        emoji = "🟢" if direction == 'long' else "🔴"
        tg(
            f"{emoji} *STOCK {direction.upper()}* — {pair}\n"
            f"💰 {STOCK_CAPITAL} USDT x{STOCK_LEVERAGE} | size={size}\n"
            f"🎯 Entrada: {round(price,2)} | SL: {sl}"
        )
        log.info(f"🚀 STOCK OPEN {direction.upper()} {pair} @ {round(price,4)}")
        return size
    except Exception as e:
        log.error(f"open_stock {pair}: {e}")
        tg(f"❌ Error stock {pair}: {e}")
        return None

def close_stock_position(pair, ps, reason, price=None):
    ccxt_side = 'sell' if ps['side'] == 'long' else 'buy'
    try:
        exchange.create_order(pair, 'market', ccxt_side, ps['size'], None, {
            'marginMode': 'isolated', 'leverage': str(STOCK_LEVERAGE), 'reduceOnly': True,
        })
        p_str = str(round(price, 2)) if price else "N/A"
        emoji = "🏁" if "trail" in reason.lower() else "🛑"
        tg(f"{emoji} *STOCK CLOSE {ps['side'].upper()}* — {pair}\n📋 {reason}\n💰 {p_str}")
        log.info(f"{emoji} STOCK CLOSE {pair} — {reason}")
    except Exception as e:
        if "22002" in str(e):
            log.warning(f"⚠️ {pair} ya cerrada")
        else:
            log.error(f"close_stock {pair}: {e}")
    finally:
        reset_stock_position(ps)

def manage_stock_position(pair, ps, price, sig):
    entry     = ps['entry_price']
    side      = ps['side']
    pnl       = ((price - entry) / entry) if side == 'long' else ((entry - price) / entry)
    trail_pct = get_trail_pct_stock(pnl)

    log.info(f"[STOCK {pair}] {side.upper()} P={round(price,2)} PnL={round(pnl*100,3)}% BE={ps['be_activated']}")

    # Cruce contrario
    if sig:
        contrario = (side == 'long' and sig['cross_short']) or \
                    (side == 'short' and sig['cross_long'])
        if contrario and sig['last_ts'] > ps['last_sig_ts']:
            close_stock_position(pair, ps, f"Cruce contrario {round(pnl*100,2)}%", price)
            ps['last_sig_ts'] = sig['last_ts']
            return

    # SL
    if pnl <= -STOCK_SL_PCT:
        close_stock_position(pair, ps, f"SL {round(pnl*100,2)}%", price)
        return

    # BE por tiempo
    if not ps['be_activated'] and ps['entry_time'] > 0:
        hours_in = (time.time() - ps['entry_time']) / 3600
        if hours_in >= STOCK_BE_TIME_H:
            ps['be_activated'] = True
            ps['trail_best']   = price
            ps['trail_sl']     = round(
                price * (1 - trail_pct) if side == 'long' else price * (1 + trail_pct), 4
            )
            tg(f"⏱️ *STOCK BE auto* — {pair}")

    # BE por PnL
    if not ps['be_activated'] and pnl >= STOCK_BE_TRIGGER:
        ps['be_activated'] = True
        ps['trail_best']   = price
        ps['trail_sl']     = round(
            price * (1 - trail_pct) if side == 'long' else price * (1 + trail_pct), 4
        )
        tg(f"📍 *STOCK BE activado* — {pair}")

    # Trailing dinámico
    if ps['be_activated']:
        if side == 'long':
            if price > ps['trail_best']:
                ps['trail_best'] = price
                ps['trail_sl']   = round(price * (1 - trail_pct), 4)
            if price <= ps['trail_sl']:
                close_stock_position(pair, ps, f"Trailing {round(pnl*100,2)}%", price)
        else:
            if price < ps['trail_best']:
                ps['trail_best'] = price
                ps['trail_sl']   = round(price * (1 + trail_pct), 4)
            if price >= ps['trail_sl']:
                close_stock_position(pair, ps, f"Trailing {round(pnl*100,2)}%", price)

def sync_stock_on_start(state):
    log.info("=== Sync stock positions ===")
    for pair in list(state.keys()):
        ps = state[pair]
        try:
            positions = exchange.fetch_positions([pair])
            found     = False
            for p in positions:
                if p['symbol'] == pair and float(p.get('contracts', 0) or 0) > 0:
                    ps['side']        = p['side']
                    ps['entry_price'] = float(p['entryPrice'])
                    ps['size']        = float(p['contracts'])
                    log.info(f"  {pair}: {ps['side']} @ {ps['entry_price']}")
                    found = True
            if not found:
                reset_stock_position(ps)
        except:
            reset_stock_position(ps)

def get_top_stocks():
    try:
        volumes = []
        for symbol in MANUAL_PAIRS:
            try:
                ticker = exchange.fetch_ticker(symbol)
                vol    = float(ticker.get('quoteVolume', 0) or 0)
                volumes.append((symbol, vol))
            except:
                pass
            time.sleep(0.2)
        volumes.sort(key=lambda x: x[1], reverse=True)
        top = [v[0] for v in volumes[:STOCK_TOP_VOLUME]]
        log.info(f"✅ Top stocks: {[p.replace('/USDT:USDT','') for p in top]}")
        return top if top else MANUAL_PAIRS[:STOCK_TOP_VOLUME]
    except Exception as e:
        log.error(f"get_top_stocks: {e}")
        return MANUAL_PAIRS[:STOCK_TOP_VOLUME]

# ============================================
# CRYPTO SCANNER FUNCTIONS
# ============================================

def fetch_candles(pair, limit=60, timeframe=None):
    tf = timeframe if timeframe else SCAN_TF
    try:
        time.sleep(API_PAUSE)
        return exchange.fetch_ohlcv(pair, tf, limit=limit)
    except Exception as e:
        log.error(pair + " fetch_ohlcv: " + str(e)); return []

def load_scan_state():
    if os.path.exists(SCAN_STATE_FILE):
        try:
            with open(SCAN_STATE_FILE) as f: return json.load(f)
        except: pass
    return {'positions': {}, 'candidates': [], 'last_scan': 0}

def save_scan_state(state):
    with open(SCAN_STATE_FILE, 'w') as f: json.dump(state, f, indent=2)

def refresh_candidates(scan_state):
    try:
        log.info("Actualizando candidatos crypto...")
        tickers  = exchange.fetch_tickers()
        excluded = {'BTC', 'ETH', 'SOL', 'BNB', 'XRP', 'ADA', 'DOGE', 'XAU', 'XAG', 'BZ', 'CL'}
        cands    = []
        for symbol, ticker in tickers.items():
            if not symbol.endswith('/USDT:USDT'): continue
            base = symbol.split('/')[0]
            if base in excluded: continue
            # Excluir también acciones del stock bot
            if symbol in MANUAL_PAIRS: continue
            vol = float(ticker.get('quoteVolume', 0) or 0)
            if SCAN_VOL_MIN <= vol <= SCAN_VOL_MAX:
                cands.append(symbol)
        scan_state['candidates'] = cands
        scan_state['last_scan']  = time.time()
        log.info("Candidatos crypto: " + str(len(cands)))
        tg("🔍 Candidatos crypto: " + str(len(cands)))
    except Exception as e:
        log.error("refresh_candidates: " + str(e))

def check_breakout(pair, idx, total):
    try:
        if idx % 10 == 0:
            log.info("Scan " + str(idx) + "/" + str(total) + " - " + pair)
        candles = fetch_candles(pair, limit=60)
        if not candles or len(candles) < 20: return False
        closed  = candles[:-1]
        current = candles[-1]
        closes  = [c[4] for c in closed]
        volumes = [c[5] for c in closed]
        vm20    = sum(volumes[-20:]) / 20
        if vm20 == 0: return False
        cv = current[5]
        if cv < vm20 * SCAN_VOL_SPIKE: return False
        cp = (current[4] - current[1]) / current[1] if current[1] > 0 else 0
        if cp < SCAN_PRICE_MOVE: return False
        rsi = calc_rsi(closes[-20:])
        if rsi < SCAN_RSI_MIN: return False
        last3 = closed[-3:]
        if not all(c[4] > c[1] for c in last3): return False
        if len(closes) >= 4 and closes[-1] <= closes[-4]: return False
        log.info("🎯 BREAKOUT: " + pair + " vol=" + str(round(cv/vm20,1)) + "x rsi=" + str(round(rsi,1)) + " move=" + str(round(cp*100,2)) + "%")
        return True
    except Exception as e:
        log.error("check_breakout " + pair + ": " + str(e)); return False

def scan_open(pair, price):
    size = round((SCAN_CAPITAL * SCAN_LEVERAGE) / price, 4)
    if size <= 0: return None
    try:
        exchange.set_leverage(SCAN_LEVERAGE, pair, params={
            'marginMode': 'isolated', 'productType': 'USDT-FUTURES',
        })
        time.sleep(0.5)
        params = {'marginMode': 'isolated', 'leverage': str(SCAN_LEVERAGE), 'reduceOnly': False}
        exchange.create_order(pair, 'market', 'buy', size, None, params)
        log.info("🚀 SCAN OPEN " + pair + " @ " + str(round(price, 6)))
        tg("🚀 *CRYPTO BREAKOUT* — " + pair + "\n" +
           str(SCAN_CAPITAL) + " USDT x" + str(SCAN_LEVERAGE) + "\n" +
           "Precio: " + str(round(price, 6)) + "\n" +
           "SL: " + str(round(price*(1-SCAN_SL_PCT), 6)))
        return size
    except Exception as e:
        log.error("scan_open " + pair + ": " + str(e)); return None

def scan_close(pair, pos, reason, price=None):
    try:
        params = {'marginMode': 'isolated', 'leverage': str(SCAN_LEVERAGE), 'reduceOnly': True}
        exchange.create_order(pair, 'market', 'sell', pos['size'], None, params)
        p_str = str(round(price, 6)) if price else "N/A"
        log.info("SCAN CLOSE " + pair + " - " + reason)
        tg("🏁 *CRYPTO CLOSE* — " + pair + "\n" + reason + "\nPrecio: " + p_str)
    except Exception as e:
        log.error("scan_close " + pair + ": " + str(e))

def manage_scan_positions(scan_state):
    positions = scan_state.get('positions', {})
    for pair in list(positions.keys()):
        try:
            price = fetch_price(pair)
            if not price: continue
            pos = positions[pair]
            pnl = (price - pos['entry_price']) / pos['entry_price']
            log.info("[SCAN " + pair + "] pnl=" + str(round(pnl*100,3)) + "% BE=" + str(pos['be_activated']))
            if pnl <= -SCAN_SL_PCT:
                scan_close(pair, pos, "SL " + str(round(pnl*100,2)) + "%", price)
                del positions[pair]; save_scan_state(scan_state); continue
            if not pos['be_activated'] and pnl >= SCAN_BE_TRIGGER:
                pos['be_activated'] = True; pos['trail_best'] = price
                tg("📍 CRYPTO BE — " + pair)
            if pos['be_activated']:
                if price > pos['trail_best']: pos['trail_best'] = price
                if price <= pos['trail_best'] * (1 - SCAN_TRAIL_PCT):
                    scan_close(pair, pos, "trailing pnl=" + str(round(pnl*100,2)) + "%", price)
                    del positions[pair]; save_scan_state(scan_state)
        except Exception as e:
            log.error("manage_scan " + pair + ": " + str(e))

# ============================================
# LOOP PRINCIPAL
# ============================================

def run():
    # ── Init Stocks ───────────────────────────
    log.info("📊 Cargando top stocks...")
    stock_pairs   = get_top_stocks()
    stock_state   = load_stock_state(stock_pairs)
    sync_stock_on_start(stock_state)
    save_stock_state(stock_state)

    # ── Init Scanner ──────────────────────────
    scan_state = load_scan_state()
    if not scan_state.get('candidates'):
        refresh_candidates(scan_state)
        save_scan_state(scan_state)

    tg(
        "🤖 *Bot arrancado*\n"
        f"📈 Stocks: {STOCK_CAPITAL} USDT x{STOCK_LEVERAGE} | 16:00-21:15\n"
        f"🪙 Crypto: {SCAN_CAPITAL} USDT x{SCAN_LEVERAGE} | 24h\n"
        f"📊 {len(stock_pairs)} acciones | Max {STOCK_MAX_POS} stocks / {SCAN_MAX_POS} crypto"
    )

    last_scan_ts    = 0
    last_hb         = -1
    last_vol_update = time.time()
    crossed_pairs   = {}
    auto_closed     = False

    while True:
        try:
            now     = utcnow()
            hb_slot = now.hour // HEARTBEAT_H

            # ── Heartbeat cada 4h ─────────────────
            if hb_slot != last_hb:
                last_hb   = hb_slot
                stock_act = sum(1 for p in stock_pairs if stock_state[p]['side'])
                scan_act  = len(scan_state.get('positions', {}))
                msg = (
                    f"💓 Bot activo — {now.strftime('%H:%M UTC')}\n"
                    f"📈 Stocks: {stock_act}/{STOCK_MAX_POS}\n"
                    f"🪙 Crypto: {scan_act}/{SCAN_MAX_POS}\n"
                )
                tg(msg)

            # ── Reset auto_closed cada día ────────
            if now_madrid().hour == 0 and auto_closed:
                auto_closed = False

            # ── Actualizar ranking stocks cada 4h ─
            if time.time() - last_vol_update >= 4 * 3600:
                log.info("🔄 Actualizando ranking stocks...")
                new_pairs = get_top_stocks()
                for p in new_pairs:
                    if p not in stock_state:
                        stock_state[p] = default_stock_state()
                stock_pairs     = new_pairs
                last_vol_update = time.time()

            if is_weekday():

                # ── CIERRE AUTOMÁTICO 21:15 ───────
                if is_close_time() and not auto_closed:
                    auto_closed = True
                    log.info("⏰ Cierre automático 21:15 stocks")
                    for pair in stock_pairs:
                        ps = stock_state[pair]
                        if ps['side']:
                            price = fetch_price(pair)
                            if price:
                                close_stock_position(pair, ps, "Cierre automático 21:15", price)
                    save_stock_state(stock_state)

                # ── GESTIÓN STOCKS ACTIVOS ────────
                for pair in stock_pairs:
                    ps = stock_state[pair]
                    if not ps['side']:
                        continue
                    price = fetch_price(pair)
                    if not price:
                        continue
                    try:
                        candles = exchange.fetch_ohlcv(pair, STOCK_TIMEFRAME_H, limit=150)
                        if not candles: continue
                        sig = get_stock_signals(candles)
                        if not sig: continue
                    except:
                        continue
                    manage_stock_position(pair, ps, price, sig)
                    save_stock_state(stock_state)

                # ── ENTRADAS STOCKS 16:00-21:15 ───
                if is_trading_hours():
                    active = sum(1 for p in stock_pairs if stock_state[p]['side'])
                    for pair in stock_pairs:
                        ps = stock_state[pair]
                        if ps['side'] or active >= STOCK_MAX_POS:
                            continue
                        price = fetch_price(pair)
                        if not price:
                            continue
                        try:
                            candles = exchange.fetch_ohlcv(pair, STOCK_TIMEFRAME_H, limit=150)
                            if not candles or len(candles) < STOCK_EMA_SLOW + 5: continue
                            sig_live = get_stock_signals(candles)
                            if not sig_live: continue
                        except Exception as e:
                            log.error(f"ohlcv {pair}: {e}"); continue

                        now_ts     = candles[-1][0]
                        last_cross = crossed_pairs.get(pair, 0)
                        if now_ts <= last_cross:
                            continue

                        log.info(
                            f"[STOCK {pair}] P={round(price,2)} "
                            f"{'▲' if sig_live['aligned_long'] else '▼'} "
                            f"RSI={round(sig_live['rsi'],1)} "
                            f"gap={round(sig_live['ema_gap']*100,3)}%"
                        )

                        # LONG
                        if sig_live['ema_gap'] >= STOCK_EMA_MIN_GAP and \
                           sig_live['price_ok_long'] and \
                           sig_live['above_vwap'] and \
                           35 <= sig_live['rsi'] <= 65 and \
                           (sig_live['cross_long'] or sig_live['aligned_long']) and \
                           confirms_15m(pair, 'long'):
                            size = open_stock_position(pair, 'long', price)
                            if size:
                                ps.update({
                                    'side': 'long', 'entry_price': price,
                                    'size': size, 'entry_time': time.time(),
                                    'last_sig_ts': now_ts,
                                })
                                crossed_pairs[pair] = now_ts
                                active += 1
                            save_stock_state(stock_state)

                        # SHORT
                        elif sig_live['ema_gap'] >= STOCK_EMA_MIN_GAP and \
                             sig_live['price_ok_short'] and \
                             sig_live['below_vwap'] and \
                             35 <= sig_live['rsi'] <= 65 and \
                             (sig_live['cross_short'] or sig_live['aligned_short']) and \
                             confirms_15m(pair, 'short'):
                            size = open_stock_position(pair, 'short', price)
                            if size:
                                ps.update({
                                    'side': 'short', 'entry_price': price,
                                    'size': size, 'entry_time': time.time(),
                                    'last_sig_ts': now_ts,
                                })
                                crossed_pairs[pair] = now_ts
                                active += 1
                            save_stock_state(stock_state)

            # ── CRYPTO SCANNER 24H ────────────────
            if time.time() - last_scan_ts >= SCAN_INTERVAL:
                last_scan_ts = time.time()

                if time.time() - scan_state.get('last_scan', 0) > 1800:
                    refresh_candidates(scan_state)
                    save_scan_state(scan_state)

                manage_scan_positions(scan_state)

                positions  = scan_state.get('positions', {})
                candidates = scan_state.get('candidates', [])
                log.info(f"=== Crypto scan | pos={len(positions)}/{SCAN_MAX_POS} cands={len(candidates)} ===")

                if len(positions) < SCAN_MAX_POS and candidates:
                    total = len(candidates)
                    for idx, pair in enumerate(candidates):
                        if pair in positions: continue
                        if len(positions) >= SCAN_MAX_POS: break
                        if check_breakout(pair, idx, total):
                            price = fetch_price(pair)
                            if not price: continue
                            size = scan_open(pair, price)
                            if size:
                                positions[pair] = {
                                    'entry_price': price, 'size': size,
                                    'be_activated': False, 'trail_best': price,
                                    'entry_time': time.time()
                                }
                                scan_state['positions'] = positions
                                save_scan_state(scan_state)
                            time.sleep(2)

            time.sleep(STOCK_POLL)

        except KeyboardInterrupt:
            log.info("Bot detenido")
            break
        except Exception as e:
            log.error(f"Error loop: {e}")
            time.sleep(30)

if __name__ == '__main__':
    run()