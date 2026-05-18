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

TG_TOKEN   = os.environ.get('TELEGRAM_TOKEN', '')
TG_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '')

def tg(msg):
    if not TG_TOKEN or not TG_CHAT_ID: return
    try:
        url  = "https://api.telegram.org/bot" + TG_TOKEN + "/sendMessage"
        data = urllib.parse.urlencode({'chat_id': TG_CHAT_ID, 'text': msg}).encode()
        urllib.request.urlopen(url, data=data, timeout=10)
    except Exception as e: log.error("Telegram error: " + str(e))

exchange = ccxt.bitget({
    'apiKey':   os.environ['BITGET_API_KEY'],
    'secret':   os.environ['BITGET_API_SECRET'],
    'password': os.environ['BITGET_API_PASSPHRASE'],
    'options':  {'defaultType': 'swap'},
    'timeout':  10000,
})

def utcnow(): return datetime.now(timezone.utc)

def calc_rsi(closes, period=14):
    if len(closes) < period + 1: return 50.0
    diffs  = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains  = [max(d, 0) for d in diffs]; losses = [max(-d, 0) for d in diffs]
    ag = sum(gains[-period:]) / period; al = sum(losses[-period:]) / period
    if al == 0: return 100.0
    return 100 - (100 / (1 + ag / al))

def fetch_candles(pair, limit=60, timeframe=None):
    tf = timeframe if timeframe else SCAN_TF
    try:
        time.sleep(API_PAUSE)
        return exchange.fetch_ohlcv(pair, tf, limit=limit)
    except Exception as e: log.error(pair + " fetch_ohlcv: " + str(e)); return []

def fetch_price(pair):
    try: return float(exchange.fetch_ticker(pair)['last'])
    except Exception as e: log.error(pair + " fetch_ticker: " + str(e)); return None

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
        log.info("Actualizando candidatos...")
        tickers  = exchange.fetch_tickers()
        excluded = {'BTC', 'ETH', 'SOL', 'BNB', 'XRP', 'ADA', 'DOGE', 'XAU', 'XAG', 'BZ', 'CL'}
        cands    = []
        for symbol, ticker in tickers.items():
            if not symbol.endswith('/USDT:USDT'): continue
            base = symbol.split('/')[0]
            if base in excluded: continue
            vol = float(ticker.get('quoteVolume', 0) or 0)
            if SCAN_VOL_MIN <= vol <= SCAN_VOL_MAX:
                cands.append(symbol)
        scan_state['candidates'] = cands
        scan_state['last_scan']  = time.time()
        log.info("Candidatos: " + str(len(cands)))
        tg("🔍 Candidatos actualizados: " + str(len(cands)) + " pares")
    except Exception as e: log.error("refresh_candidates: " + str(e))

def check_breakout(pair, idx, total):
    try:
        if idx % 10 == 0: log.info("Scan " + str(idx) + "/" + str(total) + " - " + pair)
        candles = fetch_candles(pair, limit=60)
        if not candles or len(candles) < 20: return False
        closed  = candles[:-1]
        current = candles[-1]
        closes  = [c[4] for c in closed]
        volumes = [c[5] for c in closed]

        vm20 = sum(volumes[-20:]) / 20
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

        log.info("🎯 BREAKOUT: " + pair + " vol=" + str(round(cv/vm20, 1)) + "x rsi=" + str(round(rsi, 1)) + " move=" + str(round(cp*100, 2)) + "%")
        return True
    except Exception as e:
        log.error("check_breakout " + pair + ": " + str(e)); return False

def scan_open(pair, price):
    size = round((SCAN_CAPITAL * SCAN_LEVERAGE) / price, 4)
    if size <= 0: return None
    try:
        params = {'marginMode': 'isolated', 'leverage': str(SCAN_LEVERAGE), 'reduceOnly': False}
        exchange.create_order(pair, 'market', 'buy', size, None, params)
        log.info("🚀 SCAN OPEN " + pair + " @ " + str(round(price, 6)))
        tg("🚀 BREAKOUT " + pair + "\n" + str(SCAN_CAPITAL) + " USDT x" + str(SCAN_LEVERAGE) + "\nPrecio: " + str(round(price, 6)) + "\nSL: " + str(round(price*(1-SCAN_SL_PCT), 6)))
        return size
    except Exception as e: log.error("scan_open " + pair + ": " + str(e)); return None

def scan_close(pair, pos, reason, price=None):
    try:
        params = {'marginMode': 'isolated', 'leverage': str(SCAN_LEVERAGE), 'reduceOnly': True}
        exchange.create_order(pair, 'market', 'sell', pos['size'], None, params)
        p_str = str(round(price, 6)) if price else "N/A"
        log.info("SCAN CLOSE " + pair + " - " + reason)
        tg("🏁 SCAN CLOSE " + pair + "\n" + reason + "\nPrecio: " + p_str)
    except Exception as e: log.error("scan_close " + pair + ": " + str(e))

def manage_positions(scan_state):
    positions = scan_state.get('positions', {})
    for pair in list(positions.keys()):
        try:
            price = fetch_price(pair)
            if not price: continue
            pos = positions[pair]
            pnl = (price - pos['entry_price']) / pos['entry_price']
            log.info("[SCAN " + pair + "] pnl=" + str(round(pnl*100, 3)) + "% BE=" + str(pos['be_activated']))
            if pnl <= -SCAN_SL_PCT:
                scan_close(pair, pos, "SL " + str(round(pnl*100, 2)) + "%", price)
                del positions[pair]; save_scan_state(scan_state); continue
            if not pos['be_activated'] and pnl >= SCAN_BE_TRIGGER:
                pos['be_activated'] = True; pos['trail_best'] = price
                tg("📍 SCAN BE " + pair)
            if pos['be_activated']:
                if price > pos['trail_best']: pos['trail_best'] = price
                if price <= pos['trail_best'] * (1 - SCAN_TRAIL_PCT):
                    scan_close(pair, pos, "trailing pnl=" + str(round(pnl*100, 2)) + "%", price)
                    del positions[pair]; save_scan_state(scan_state)
        except Exception as e: log.error("manage " + pair + ": " + str(e))

def run():
    scan_state = load_scan_state()
    if not scan_state.get('candidates'):
        refresh_candidates(scan_state)
        save_scan_state(scan_state)
    tg("🤖 Scanner Cripto arrancado\n" + str(SCAN_CAPITAL) + " USDT x" + str(SCAN_LEVERAGE) + " | Max " + str(SCAN_MAX_POS) + " posiciones\nActivo 24/7")
    last_scan_ts = 0
    last_hb      = -1

    while True:
        try:
            now     = utcnow()
            hb_slot = now.hour // HEARTBEAT_H
            if hb_slot != last_hb:
                last_hb = hb_slot
                pos_n   = len(scan_state.get('positions', {}))
                msg     = "💓 Scanner activo\n" + now.strftime('%H:%M UTC') + "\nPosiciones: " + str(pos_n) + "/" + str(SCAN_MAX_POS) + "\n"
                for pair, pos in scan_state.get('positions', {}).items():
                    price = fetch_price(pair)
                    if price:
                        pnl = (price - pos['entry_price']) / pos['entry_price']
                        msg += pair.split('/')[0] + ": " + str(round(pnl*100, 2)) + "%\n"
                tg(msg)

            if time.time() - last_scan_ts >= SCAN_INTERVAL:
                last_scan_ts = time.time()
                if time.time() - scan_state.get('last_scan', 0) > 1800:
                    refresh_candidates(scan_state)
                    save_scan_state(scan_state)
                manage_positions(scan_state)
                positions  = scan_state.get('positions', {})
                candidates = scan_state.get('candidates', [])
                log.info("=== Scanner | pos=" + str(len(positions)) + "/" + str(SCAN_MAX_POS) + " cands=" + str(len(candidates)) + " ===")
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
                                positions[pair] = {'entry_price': price, 'size': size, 'be_activated': False, 'trail_best': price, 'entry_time': time.time()}
                                scan_state['positions'] = positions
                                save_scan_state(scan_state)
                            time.sleep(2)
            time.sleep(60)

        except KeyboardInterrupt: log.info("Bot detenido manualmente"); break
        except Exception as e: log.error("Error: " + str(e)); time.sleep(30)

if __name__ == '__main__': run()