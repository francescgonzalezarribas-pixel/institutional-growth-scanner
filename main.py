import ccxt
import time
import os
import json
import logging
import urllib.request
import urllib.parse
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)

# ============================================
# CONFIG
# ============================================

CAPITAL        = 20
LEVERAGE       = 3
MAX_POSITIONS  = 3
TIMEFRAME_H    = '1h'
TIMEFRAME_M    = '15m'
SL_PCT         = 0.015
BE_TRIGGER     = 0.003
EMA_FAST       = 9
EMA_SLOW       = 21
RSI_PERIOD     = 14
EMA_MIN_GAP    = 0.001
BE_TIME_H      = 3
POLL           = 60
TOP_VOLUME     = 20
STATE_FILE     = 'stock_bot_state.json'

TG_TOKEN   = os.environ.get('TELEGRAM_TOKEN', '')
TG_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '')

def tg(msg):
    if not TG_TOKEN or not TG_CHAT_ID:
        return
    try:
        url  = "https://api.telegram.org/bot" + TG_TOKEN + "/sendMessage"
        data = urllib.parse.urlencode({
            'chat_id':    TG_CHAT_ID,
            'text':       msg,
            'parse_mode': 'Markdown'
        }).encode()
        urllib.request.urlopen(url, data=data, timeout=10)
    except Exception as e:
        log.error(f"Telegram: {e}")

exchange = ccxt.bitget({
    'apiKey':   os.environ['BITGET_API_KEY'],
    'secret':   os.environ['BITGET_API_SECRET'],
    'password': os.environ['BITGET_API_PASSPHRASE'],
    'options':  {'defaultType': 'swap'},
})

# ============================================
# ESTADO
# ============================================

def default_state():
    return {
        'side':         None,
        'entry_price':  0.0,
        'size':         0.0,
        'be_activated': False,
        'trail_best':   0.0,
        'trail_sl':     0.0,
        'last_sig_ts':  0,
        'entry_time':   0,
    }

def load_state(pairs):
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                saved = json.load(f)
            state = {}
            for pair in pairs:
                state[pair] = default_state()
                if pair in saved:
                    state[pair].update(saved[pair])
            return state
        except Exception as e:
            log.warning(f"State load: {e}")
    return {pair: default_state() for pair in pairs}

def save_state(state):
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        log.error(f"save_state: {e}")

def reset_position(ps):
    ps.update({
        'side': None, 'entry_price': 0.0, 'size': 0.0,
        'be_activated': False, 'trail_best': 0.0,
        'trail_sl': 0.0, 'entry_time': 0,
    })

# ============================================
# MERCADO — solo L-V
# ============================================

def is_market_open():
    return datetime.now(timezone.utc).weekday() < 5

# ============================================
# TOP ACCIONES POR VOLUMEN
# ============================================

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
        top = [v[0] for v in volumes[:TOP_VOLUME]]
        log.info(f"✅ Top {len(top)}: {[p.replace('/USDT:USDT','') for p in top]}")
        return top if top else MANUAL_PAIRS[:TOP_VOLUME]
    except Exception as e:
        log.error(f"get_top_stocks: {e}")
        return MANUAL_PAIRS[:TOP_VOLUME]

# ============================================
# INDICADORES
# ============================================

def calc_ema(values, period):
    k   = 2 / (period + 1)
    ema = [values[0]]
    for v in values[1:]:
        ema.append(v * k + ema[-1] * (1 - k))
    return ema

def calc_rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50.0
    diffs  = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains  = [max(d, 0) for d in diffs]
    losses = [max(-d, 0) for d in diffs]
    ag     = sum(gains[-period:]) / period
    al     = sum(losses[-period:]) / period
    if al == 0:
        return 100.0
    return 100 - (100 / (1 + ag / al))

def calc_vwap(candles):
    cum_tv, cum_v = 0.0, 0.0
    for c in candles:
        typ     = (c[2] + c[3] + c[4]) / 3
        cum_tv += typ * c[5]
        cum_v  += c[5]
    return (cum_tv / cum_v) if cum_v > 0 else None

def get_signals(candles):
    if len(candles) < EMA_SLOW + 5:
        return None
    closes = [c[4] for c in candles]
    ema_f  = calc_ema(closes, EMA_FAST)
    ema_s  = calc_ema(closes, EMA_SLOW)
    rsi    = calc_rsi(closes[-30:], RSI_PERIOD)
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
        candles = exchange.fetch_ohlcv(pair, TIMEFRAME_M, limit=50)
        if not candles:
            return True
        sig = get_signals(candles)
        if not sig:
            return True
        if direction == 'long':
            return sig['aligned_long'] and sig['price_ok_long']
        else:
            return sig['aligned_short'] and sig['price_ok_short']
    except Exception as e:
        log.error(f"confirms_15m {pair}: {e}")
        return True

# ============================================
# TRAILING DINÁMICO
# ============================================

def get_trail_pct(pnl):
    if pnl >= 0.03:   return 0.005
    elif pnl >= 0.01: return 0.010
    else:             return 0.015

# ============================================
# TRADING
# ============================================

def force_leverage(pair):
    try:
        exchange.set_leverage(LEVERAGE, pair, params={
            'marginMode':  'isolated',
            'productType': 'USDT-FUTURES',
        })
        log.info(f"⚙️ x{LEVERAGE} isolated — {pair}")
    except Exception as e:
        log.warning(f"set_leverage {pair}: {e}")

def fetch_price(pair):
    try:
        return float(exchange.fetch_ticker(pair)['last'])
    except Exception as e:
        log.error(f"fetch_price {pair}: {e}")
        return None

def fetch_position(pair):
    try:
        positions = exchange.fetch_positions([pair])
        for p in positions:
            if p['symbol'] == pair and float(p.get('contracts', 0) or 0) > 0:
                return p
        return None
    except Exception as e:
        log.error(f"fetch_position {pair}: {e}")
        return None

def open_position(pair, direction, price):
    size      = round((CAPITAL * LEVERAGE) / price, 4)
    if size < 0.01:
        size = 0.01
    ccxt_side = 'buy' if direction == 'long' else 'sell'
    try:
        force_leverage(pair)
        time.sleep(0.5)
        exchange.create_order(pair, 'market', ccxt_side, size, None, {
            'marginMode': 'isolated',
            'leverage':   str(LEVERAGE),
            'reduceOnly': False,
        })
        sl    = round(price * (1 - SL_PCT) if direction == 'long' else price * (1 + SL_PCT), 4)
        emoji = "🟢" if direction == 'long' else "🔴"
        tg(
            f"{emoji} *OPEN {direction.upper()}* — {pair}\n"
            f"💰 {CAPITAL} USDT x{LEVERAGE} | size={size}\n"
            f"🎯 Entrada: {round(price,2)} | SL: {sl}"
        )
        log.info(f"🚀 OPEN {direction.upper()} {pair} @ {round(price,4)}")
        return size
    except Exception as e:
        log.error(f"open_position {pair}: {e}")
        tg(f"❌ Error abriendo {pair}: {e}")
        return None

def close_position(pair, ps, reason, price=None):
    ccxt_side = 'sell' if ps['side'] == 'long' else 'buy'
    try:
        exchange.create_order(pair, 'market', ccxt_side, ps['size'], None, {
            'marginMode': 'isolated',
            'leverage':   str(LEVERAGE),
            'reduceOnly': True,
        })
        p_str = str(round(price, 2)) if price else "N/A"
        emoji = "🏁" if "trail" in reason.lower() else "🛑"
        tg(f"{emoji} *CLOSE {ps['side'].upper()}* — {pair}\n📋 {reason}\n💰 {p_str}")
        log.info(f"{emoji} CLOSE {pair} — {reason}")
    except Exception as e:
        if "22002" in str(e):
            log.warning(f"⚠️ {pair} ya cerrada")
        else:
            log.error(f"close_position {pair}: {e}")
    finally:
        reset_position(ps)

# ============================================
# GESTIÓN POSICIÓN
# ============================================

def manage_position(pair, ps, price, sig):
    entry     = ps['entry_price']
    side      = ps['side']
    pnl       = ((price - entry) / entry) if side == 'long' else ((entry - price) / entry)
    trail_pct = get_trail_pct(pnl)

    log.info(f"[{pair}] {side.upper()} P={round(price,2)} PnL={round(pnl*100,3)}% BE={ps['be_activated']}")

    # Cruce contrario
    if sig:
        contrario = (side == 'long' and sig['cross_short']) or \
                    (side == 'short' and sig['cross_long'])
        if contrario and sig['last_ts'] > ps['last_sig_ts']:
            close_position(pair, ps, f"Cruce contrario {round(pnl*100,2)}%", price)
            ps['last_sig_ts'] = sig['last_ts']
            return

    # SL
    if pnl <= -SL_PCT:
        close_position(pair, ps, f"SL {round(pnl*100,2)}%", price)
        return

    # BE por tiempo
    if not ps['be_activated'] and ps['entry_time'] > 0:
        hours_in = (time.time() - ps['entry_time']) / 3600
        if hours_in >= BE_TIME_H:
            ps['be_activated'] = True
            ps['trail_best']   = price
            ps['trail_sl']     = round(
                price * (1 - trail_pct) if side == 'long' else price * (1 + trail_pct), 4
            )
            tg(f"⏱️ *BE automático* — {pair} | trailing desde {round(price,2)}")

    # BE por PnL
    if not ps['be_activated'] and pnl >= BE_TRIGGER:
        ps['be_activated'] = True
        ps['trail_best']   = price
        ps['trail_sl']     = round(
            price * (1 - trail_pct) if side == 'long' else price * (1 + trail_pct), 4
        )
        tg(f"📍 *BE activado* — {pair} | trailing desde {round(price,2)}")

    # Trailing dinámico
    if ps['be_activated']:
        if side == 'long':
            if price > ps['trail_best']:
                ps['trail_best'] = price
                ps['trail_sl']   = round(price * (1 - trail_pct), 4)
            if price <= ps['trail_sl']:
                close_position(pair, ps, f"Trailing {round(pnl*100,2)}%", price)
        else:
            if price < ps['trail_best']:
                ps['trail_best'] = price
                ps['trail_sl']   = round(price * (1 + trail_pct), 4)
            if price >= ps['trail_sl']:
                close_position(pair, ps, f"Trailing {round(pnl*100,2)}%", price)

# ============================================
# SYNC AL ARRANCAR
# ============================================

def sync_on_start(state):
    log.info("=== Sync positions ===")
    for pair in list(state.keys()):
        ps  = state[pair]
        pos = fetch_position(pair)
        if pos:
            ps['side']        = pos['side']
            ps['entry_price'] = float(pos['entryPrice'])
            ps['size']        = float(pos['contracts'])
            log.info(f"  {pair}: {ps['side']} @ {ps['entry_price']}")
        else:
            reset_position(ps)
            log.info(f"  {pair}: sin posición")
    tg(
        f"🤖 *Stock Bot arrancado*\n"
        f"💰 {CAPITAL} USDT x{LEVERAGE}\n"
        f"📊 Top {TOP_VOLUME} por volumen | 24h L-V\n"
        f"🛑 SL {SL_PCT*100}% | BE {BE_TRIGGER*100}% | Trailing dinámico\n"
        f"⚡ EMA 9/21 + RSI 35-65 + VWAP + 15m"
    )
    save_state(state)

# ============================================
# LOOP PRINCIPAL
# ============================================

def run():
    log.info("🚀 Stock Bot iniciando...")
    pairs = get_top_stocks()
    state = load_state(pairs)
    sync_on_start(state)

    cycle           = 0
    last_vol_update = time.time()
    crossed_pairs   = {}

    while True:
        try:
            cycle += 1

            # Actualizar ranking cada 4h
            if time.time() - last_vol_update >= 4 * 3600:
                log.info("🔄 Actualizando ranking...")
                new_pairs = get_top_stocks()
                for p in new_pairs:
                    if p not in state:
                        state[p] = default_state()
                pairs           = new_pairs
                last_vol_update = time.time()

            # Fin de semana — pausa
            if not is_market_open():
                log.info(f"[ciclo {cycle}] Fin de semana")
                for pair in pairs:
                    ps = state[pair]
                    if ps['side']:
                        price = fetch_price(pair)
                        if price:
                            close_position(pair, ps, "Cierre fin de semana", price)
                save_state(state)
                time.sleep(300)
                continue

            active = sum(1 for p in pairs if state[p]['side'])

            for pair in pairs:
                ps = state[pair]

                price = fetch_price(pair)
                if not price:
                    continue

                try:
                    candles = exchange.fetch_ohlcv(pair, TIMEFRAME_H, limit=150)
                    if not candles or len(candles) < EMA_SLOW + 5:
                        continue
                    sig_live   = get_signals(candles)
                    sig_closed = get_signals(candles[:-1])
                    if not sig_live or not sig_closed:
                        continue
                except Exception as e:
                    log.error(f"fetch_ohlcv {pair}: {e}")
                    continue

                # Gestionar posición abierta
                if ps['side']:
                    manage_position(pair, ps, price, sig_live)
                    save_state(state)
                    continue

                if active >= MAX_POSITIONS:
                    continue

                log.info(
                    f"[{pair}] P={round(price,2)} "
                    f"{'▲' if sig_live['aligned_long'] else '▼'} "
                    f"RSI={round(sig_live['rsi'],1)} "
                    f"gap={round(sig_live['ema_gap']*100,3)}%"
                )

                now_ts     = candles[-1][0]
                last_cross = crossed_pairs.get(pair, 0)
                if now_ts <= last_cross:
                    continue

                # ── LONG ──────────────────────────────
                if sig_live['ema_gap'] >= EMA_MIN_GAP and \
                   sig_live['price_ok_long'] and \
                   sig_live['above_vwap'] and \
                   35 <= sig_live['rsi'] <= 65 and \
                   (sig_live['cross_long'] or sig_live['aligned_long']) and \
                   confirms_15m(pair, 'long'):
                    log.info(f"⚡ LONG {pair} RSI={round(sig_live['rsi'],1)}")
                    size = open_position(pair, 'long', price)
                    if size:
                        ps.update({
                            'side': 'long', 'entry_price': price,
                            'size': size, 'entry_time': time.time(),
                            'last_sig_ts': now_ts,
                        })
                        crossed_pairs[pair] = now_ts
                        active += 1
                    save_state(state)

                # ── SHORT ─────────────────────────────
                elif sig_live['ema_gap'] >= EMA_MIN_GAP and \
                     sig_live['price_ok_short'] and \
                     sig_live['below_vwap'] and \
                     35 <= sig_live['rsi'] <= 65 and \
                     (sig_live['cross_short'] or sig_live['aligned_short']) and \
                     confirms_15m(pair, 'short'):
                    log.info(f"⚡ SHORT {pair} RSI={round(sig_live['rsi'],1)}")
                    size = open_position(pair, 'short', price)
                    if size:
                        ps.update({
                            'side': 'short', 'entry_price': price,
                            'size': size, 'entry_time': time.time(),
                            'last_sig_ts': now_ts,
                        })
                        crossed_pairs[pair] = now_ts
                        active += 1
                    save_state(state)

            time.sleep(POLL)

        except KeyboardInterrupt:
            log.info("Bot detenido")
            break
        except Exception as e:
            log.error(f"Error loop: {e}")
            time.sleep(30)

if __name__ == '__main__':
    run()