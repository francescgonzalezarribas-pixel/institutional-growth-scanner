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

PAIRS = {
    'BTC/USDT:USDT': {'capital': 75, 'leverage': 3, 'label': 'BTC/USDT'},
}

TIMEFRAME     = '1m'
SL_PCT        = 0.003      # 0.3% — más ajustado que en 1H
BE_TRIGGER    = 0.002      # 0.2% para activar trailing
TRAIL_PCT     = 0.002      # trailing base
EMA_FAST      = 9
EMA_SLOW      = 21
RSI_PERIOD    = 14
POLL          = 30         # segundos entre ciclos
EMA_MIN_GAP   = 0.0005     # menos exigente que 1H
BE_TIME_H     = 0.5        # 30 min sin movimiento → BE automático

STATE_FILE = 'btc_1m_bot_state.json'

TG_TOKEN   = os.environ.get('TELEGRAM_TOKEN', '')
TG_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '')

def tg(msg):
    if not TG_TOKEN or not TG_CHAT_ID: return
    try:
        url  = "https://api.telegram.org/bot" + TG_TOKEN + "/sendMessage"
        data = urllib.parse.urlencode({'chat_id': TG_CHAT_ID, 'text': msg}).encode()
        urllib.request.urlopen(url, data=data, timeout=10)
    except Exception as e:
        log.error("Telegram: " + str(e))

exchange = ccxt.bitget({
    'apiKey':   os.environ['BITGET_API_KEY'],
    'secret':   os.environ['BITGET_API_SECRET'],
    'password': os.environ['BITGET_PASSPHRASE'],
    'options':  {'defaultType': 'swap'}
})

def default_pair_state():
    return {
        'side': None, 'entry_price': 0.0, 'size': 0.0,
        'be_activated': False, 'trail_best': 0.0,
        'last_signal_ts': 0, 'entry_time': 0,
    }

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                saved = json.load(f)
            state = {}
            for pair in PAIRS:
                state[pair] = default_pair_state()
                if pair in saved:
                    state[pair].update(saved[pair])
            state['last_heartbeat_h'] = saved.get('last_heartbeat_h', -1)
            return state
        except Exception as e:
            log.warning("State error: " + str(e))
    state = {pair: default_pair_state() for pair in PAIRS}
    state['last_heartbeat_h'] = -1
    return state

def save_state(state):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=2)

def reset_position(ps):
    ps.update({
        'side': None, 'entry_price': 0.0, 'size': 0.0,
        'be_activated': False, 'trail_best': 0.0, 'entry_time': 0,
    })

def utcnow():
    return datetime.now(timezone.utc)

def calc_ema(values, period):
    k = 2 / (period + 1); ema = [values[0]]
    for v in values[1:]: ema.append(v * k + ema[-1] * (1 - k))
    return ema

def calc_rsi(closes, period=14):
    if len(closes) < period + 1: return 50.0
    diffs  = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains  = [max(d, 0) for d in diffs]
    losses = [max(-d, 0) for d in diffs]
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period
    if al == 0: return 100.0
    return 100 - (100 / (1 + ag / al))

def calc_vwap(candles):
    cum_tv, cum_v = 0.0, 0.0
    for c in candles:
        typ = (c[2] + c[3] + c[4]) / 3
        cum_tv += typ * c[5]; cum_v += c[5]
    return (cum_tv / cum_v) if cum_v > 0 else None

def get_signals(closed_candles):
    if len(closed_candles) < EMA_SLOW + 5: return None
    closes = [c[4] for c in closed_candles]
    ema_f  = calc_ema(closes, EMA_FAST)
    ema_s  = calc_ema(closes, EMA_SLOW)
    rsi    = calc_rsi(closes[-30:], RSI_PERIOD)
    price  = closes[-1]
    ef_now, ef_prev = ema_f[-1], ema_f[-2]
    es_now, es_prev = ema_s[-1], ema_s[-2]
    ema_gap = abs(ef_now - es_now) / es_now
    vwap    = calc_vwap(closed_candles[-60:])  # última hora (60 velas de 1m)
    return {
        'price': price, 'rsi': rsi, 'ema_gap': ema_gap,
        'cross_long':  ef_prev <= es_prev and ef_now > es_now,
        'cross_short': ef_prev >= es_prev and ef_now < es_now,
        'aligned_long':  ef_now > es_now,
        'aligned_short': ef_now < es_now,
        'price_ok_long':  price >= ef_now,
        'price_ok_short': price <= ef_now,
        'above_vwap': price > vwap if vwap else True,
        'below_vwap': price < vwap if vwap else True,
        'last_ts': closed_candles[-1][0],
    }

def get_trail_pct(pnl):
    if pnl >= 0.015:  return 0.001
    elif pnl >= 0.006: return 0.0015
    else:              return 0.002

def fetch_candles(pair, limit=150):
    try: return exchange.fetch_ohlcv(pair, TIMEFRAME, limit=limit)
    except Exception as e: log.error(pair + " fetch_ohlcv: " + str(e)); return []

def fetch_price(pair):
    try: return float(exchange.fetch_ticker(pair)['last'])
    except Exception as e: log.error(pair + " fetch_ticker: " + str(e)); return None

def fetch_position(pair):
    try:
        positions = exchange.fetch_positions([pair])
        for p in positions:
            if p['symbol'] == pair and float(p.get('contracts', 0) or 0) > 0:
                return p
        return None
    except Exception as e:
        log.error(pair + " fetch_positions: " + str(e)); return None

def open_position(pair, direction, price):
    cfg  = PAIRS[pair]
    side = 'buy' if direction == 'long' else 'sell'
    size = round((cfg['capital'] * cfg['leverage']) / price, 6)
    try:
        params = {'marginMode': 'isolated', 'leverage': str(cfg['leverage']), 'reduceOnly': False}
        exchange.create_order(pair, 'market', side, size, None, params)
        emoji = "🟢" if direction == 'long' else "🔴"
        sl    = round(price * (1 - SL_PCT) if direction == 'long' else price * (1 + SL_PCT), 2)
        log.info(cfg['label'] + " OPEN " + direction.upper() + " size=" + str(size) + " @ " + str(round(price, 2)))
        tg(
            emoji + " OPEN " + direction.upper() + " " + cfg['label'] + "\n"
            "Precio: " + str(round(price, 2)) + "\n"
            "Capital: " + str(cfg['capital']) + " USDT x" + str(cfg['leverage']) + "\n"
            "SL: " + str(sl) + "\n"
            "⚡ Entrada por CRUCE EMA 9/21 · 1m"
        )
        return size
    except Exception as e:
        log.error(cfg['label'] + " open_position: " + str(e)); return None

def close_position(pair, ps, reason, price=None):
    side = 'sell' if ps['side'] == 'long' else 'buy'
    try:
        params = {'marginMode': 'isolated', 'leverage': str(PAIRS[pair]['leverage']), 'reduceOnly': True}
        exchange.create_order(pair, 'market', side, ps['size'], None, params)
        emoji = "🏁" if "trail" in reason else "🛑"
        p_str = str(round(price, 2)) if price else "N/A"
        log.info(PAIRS[pair]['label'] + " CLOSE " + ps['side'].upper() + " - " + reason)
        tg(emoji + " CLOSE " + ps['side'].upper() + " " + PAIRS[pair]['label'] + "\n" + reason + "\nPrecio: " + p_str)
    except Exception as e:
        if "22002" in str(e):
            log.warning(pair + " ya cerrada")
        else:
            log.error(PAIRS[pair]['label'] + " close_position: " + str(e))
    finally:
        reset_position(ps)

def manage_position(pair, ps, price, sig):
    entry = ps['entry_price']
    side  = ps['side']
    pnl   = ((price - entry) / entry) if side == 'long' else ((entry - price) / entry)
    trail_pct = get_trail_pct(pnl)

    log.info("[" + PAIRS[pair]['label'] + "] " + side.upper() +
             " entrada=" + str(round(entry, 2)) +
             " precio=" + str(round(price, 2)) +
             " PnL=" + str(round(pnl * 100, 3)) + "%" +
             " BE=" + str(ps['be_activated']))

    # Cruce contrario — cerrar
    if sig:
        contrario = (side == 'long' and sig['cross_short']) or \
                    (side == 'short' and sig['cross_long'])
        if contrario and sig['last_ts'] > ps['last_signal_ts']:
            log.info("[" + PAIRS[pair]['label'] + "] CRUCE CONTRARIO — cerrando")
            close_position(pair, ps, "Cruce contrario pnl=" + str(round(pnl * 100, 3)) + "%", price)
            ps['last_signal_ts'] = sig['last_ts']
            return

    # SL
    if pnl <= -SL_PCT:
        close_position(pair, ps, "SL " + str(round(pnl * 100, 3)) + "%", price)
        return

    # BE por tiempo (30 min en 1m)
    if not ps['be_activated'] and ps['entry_time'] > 0:
        hours_in = (time.time() - ps['entry_time']) / 3600
        if hours_in >= BE_TIME_H:
            ps['be_activated'] = True
            ps['trail_best']   = price
            log.info("[" + PAIRS[pair]['label'] + "] BE automático " + str(round(hours_in * 60, 0)) + "min")
            tg("⏱ BE automático " + PAIRS[pair]['label'] + "\nTrailing desde " + str(round(price, 2)))

    # BE por PnL
    if not ps['be_activated'] and pnl >= BE_TRIGGER:
        ps['be_activated'] = True
        ps['trail_best']   = price
        log.info("[" + PAIRS[pair]['label'] + "] BE activado")
        tg("📍 BE activado " + PAIRS[pair]['label'] + "\nTrailing desde " + str(round(price, 2)))

    # Trailing dinámico
    if ps['be_activated']:
        if side == 'long':
            if price > ps['trail_best']:
                ps['trail_best'] = price
            stop = ps['trail_best'] * (1 - trail_pct)
            if price <= stop:
                close_position(pair, ps, "trailing pnl=" + str(round(pnl * 100, 3)) + "%", price)
        elif side == 'short':
            if price < ps['trail_best']:
                ps['trail_best'] = price
            stop = ps['trail_best'] * (1 + trail_pct)
            if price >= stop:
                close_position(pair, ps, "trailing pnl=" + str(round(pnl * 100, 3)) + "%", price)

def sync_on_start(state):
    log.info("=== Syncing positions on start ===")
    for pair in PAIRS:
        ps  = state[pair]
        pos = fetch_position(pair)
        if pos:
            ps['side']        = pos['side']
            ps['entry_price'] = float(pos['entryPrice'])
            ps['size']        = float(pos['contracts'])
            log.info("  " + PAIRS[pair]['label'] + ": " + ps['side'] + " @ " + str(ps['entry_price']))
        else:
            reset_position(ps)
            log.info("  " + PAIRS[pair]['label'] + ": sin posicion")
    tg("🤖 BTC 1m Bot arrancado\nBTC: 75 USDT x3\nTF: 1m | EMA 9/21 + RSI + VWAP\nSL 0.3% | Trail dinámico")
    save_state(state)

def run():
    state = load_state()
    sync_on_start(state)
    cycle = 0

    while True:
        try:
            cycle += 1
            now     = utcnow()
            hb_slot = now.hour // 4

            # Heartbeat cada 4h
            if hb_slot != state.get('last_heartbeat_h', -1):
                state['last_heartbeat_h'] = hb_slot
                msg = "💓 BTC 1m Bot activo\n" + now.strftime('%H:%M UTC') + "\n"
                for pair in PAIRS:
                    ps = state[pair]
                    msg += PAIRS[pair]['label'] + ": " + \
                           (ps['side'].upper() + " @ " + str(round(ps['entry_price'], 2)) if ps['side'] else "Sin posicion") + "\n"
                tg(msg)
                save_state(state)

            for pair in PAIRS:
                ps = state[pair]

                price = fetch_price(pair)
                if not price:
                    continue

                candles = fetch_candles(pair, limit=150)
                if not candles:
                    continue

                # Velas cerradas para señales
                closed = candles[:-1]
                sig    = get_signals(closed)
                if not sig:
                    continue

                last_ts = sig['last_ts']

                # Gestionar posición abierta
                if ps['side']:
                    manage_position(pair, ps, price, sig)
                    save_state(state)
                    continue

                log.info(
                    "[ciclo " + str(cycle) + "] " + PAIRS[pair]['label'] +
                    " precio=" + str(round(price, 2)) +
                    " EMA9" + (">" if sig['aligned_long'] else "<") + "EMA21" +
                    " RSI=" + str(round(sig['rsi'], 1)) +
                    " gap=" + str(round(sig['ema_gap'] * 100, 3)) + "%" +
                    " crossL=" + str(sig['cross_long']) +
                    " crossS=" + str(sig['cross_short'])
                )

                # ── SOLO CRUCES REALES ─────────────────
                if last_ts > ps['last_signal_ts']:
                    ps['last_signal_ts'] = last_ts

                    if sig['cross_long'] and \
                       sig['ema_gap'] >= EMA_MIN_GAP and \
                       sig['price_ok_long'] and \
                       sig['above_vwap'] and \
                       sig['rsi'] <= 75:
                        log.info("[" + PAIRS[pair]['label'] + "] *** CRUCE LONG RSI=" + str(round(sig['rsi'], 1)))
                        size = open_position(pair, 'long', price)
                        if size:
                            ps.update({
                                'side': 'long', 'entry_price': price,
                                'size': size, 'entry_time': time.time(),
                            })

                    elif sig['cross_short'] and \
                         sig['ema_gap'] >= EMA_MIN_GAP and \
                         sig['price_ok_short'] and \
                         sig['below_vwap'] and \
                         sig['rsi'] >= 25:
                        log.info("[" + PAIRS[pair]['label'] + "] *** CRUCE SHORT RSI=" + str(round(sig['rsi'], 1)))
                        size = open_position(pair, 'short', price)
                        if size:
                            ps.update({
                                'side': 'short', 'entry_price': price,
                                'size': size, 'entry_time': time.time(),
                            })

                    save_state(state)

            time.sleep(POLL)

        except KeyboardInterrupt:
            log.info("Bot detenido")
            break
        except Exception as e:
            log.error("Error loop: " + str(e))
            time.sleep(30)

if __name__ == '__main__':
    run()
