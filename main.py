"""
Financial Telegram Bot - Version Mejorada
RSI + MACD + Graficos + Alertas + Backtest + Macro
"""

import os, io, logging, time, feedparser
import yfinance as yf
import requests, pytz
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import mplfinance as mpf
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from mistralai.client import MistralClient as Mistral
from mistralai.models.chat_completion import ChatMessage
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from collections import defaultdict

TELEGRAM_TOKEN  = os.environ["TELEGRAM_TOKEN"]
MISTRAL_API_KEY = os.environ["MISTRAL_API_KEY"]
ALLOWED_USER_ID = int(os.environ.get("ALLOWED_USER_ID", 0))
MADRID = pytz.timezone("Europe/Madrid")

SYSTEM = """Eres un analista financiero senior. Reglas:
- Responde SIEMPRE en espanol
- Sin markdown especial, texto plano
- Maximo 4 parrafos o listas cortas
- Da conclusiones concretas y accionables
- Precios exactos en tus recomendaciones"""

ai_client = Mistral(api_key=MISTRAL_API_KEY)
bot = telebot.TeleBot(TELEGRAM_TOKEN)
ALERTS = defaultdict(list)

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

IBEX = ["SAN.MC","BBVA.MC","ITX.MC","REP.MC","TEF.MC","IBE.MC","ELE.MC","AMS.MC"]
CAC = ["OR.PA","BNP.PA","AIR.PA","MC.PA","TTE.PA","LVMH.PA","BN.PA"]
DAX_S = ["BMW.DE","BAS.DE","DTE.DE","VOW3.DE","SIE.DE","SAP"]
FTSE_S = ["HSBA.L","BP.L","SHEL.L","AZN.L","RIO.L"]
OTHER_EU = ["ASML","NESN.SW","NOVO-B.CO"]
EU_STOCKS = IBEX + CAC + DAX_S + FTSE_S + OTHER_EU

US_STOCKS = [
    "AAPL","MSFT","NVDA","AMZN","GOOGL","META","TSLA","AMD","INTC","CRM","ORCL",
    "JPM","GS","BAC","V","MA","JNJ","UNH","PFE","XOM","CVX",
    "WMT","HD","MCD","KO","PEP","BA","CAT","NFLX","DIS",
]

CRYPTO = ["BTC-USD","ETH-USD","SOL-USD","BNB-USD"]
METALES = {"GC=F":"Oro","SI=F":"Plata","PL=F":"Platino","HG=F":"Cobre","PA=F":"Paladio"}

INDICES = {
    "^GSPC":"S&P 500","^DJI":"Dow Jones","^IXIC":"Nasdaq",
    "^STOXX50E":"Euro Stoxx 50","^GDAXI":"DAX","^FTSE":"FTSE 100",
    "^IBEX":"IBEX 35","^FCHI":"CAC 40",
}

MACRO_TICKERS = {
    "^VIX":"VIX Miedo","DX-Y.NYB":"DXY Dolar","^TNX":"US10Y Bono",
    "GC=F":"Oro","CL=F":"Petroleo WTI",
}

RSS_FEEDS = [
    "https://feeds.marketwatch.com/marketwatch/topstories/",
    "https://news.google.com/rss/search?q=stock+market+europe+usa&hl=en&gl=US&ceid=US:en",
    "https://feeds.reuters.com/reuters/businessNews",
]

IPOS_WATCH = [
    {"nombre":"SpaceX","ticker":None,"sector":"Aeroespacial","valor":"$1.5T","estado":"Proxima","bolsa":"NYSE"},
    {"nombre":"OpenAI","ticker":None,"sector":"IA","valor":"$1T","estado":"Proxima","bolsa":"NASDAQ"},
    {"nombre":"Kraken","ticker":None,"sector":"Crypto","valor":"$20B","estado":"Proxima","bolsa":"NASDAQ"},
    {"nombre":"Revolut","ticker":None,"sector":"Fintech","valor":"$75B","estado":"Proxima","bolsa":"NASDAQ"},
    {"nombre":"Canva","ticker":None,"sector":"SaaS","valor":"$42B","estado":"Proxima","bolsa":"NYSE"},
    {"nombre":"Cerebras","ticker":"CBRS","sector":"Chips IA","valor":"$48B","estado":"Reciente","bolsa":"NASDAQ"},
    {"nombre":"Lincoln Intl","ticker":"LCLN","sector":"Banca","valor":"$1.94B","estado":"Esta semana","bolsa":"NYSE"},
]


def calc_rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_g = gain.ewm(com=period-1, min_periods=period).mean()
    avg_l = loss.ewm(com=period-1, min_periods=period).mean()
    rs = avg_g / avg_l.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def calc_macd(series, fast=12, slow=26, signal=9):
    ema_f = series.ewm(span=fast, adjust=False).mean()
    ema_s = series.ewm(span=slow, adjust=False).mean()
    macd = ema_f - ema_s
    sig = macd.ewm(span=signal, adjust=False).mean()
    return macd, sig


def fetch_quote(ticker, period="3mo"):
    try:
        hist = yf.Ticker(ticker).history(period=period)
        if hist.empty or len(hist) < 15:
            return None
        c, h, lo = hist["Close"], hist["High"], hist["Low"]
        vol = hist["Volume"]
        price = c.iloc[-1]
        d1 = (price - c.iloc[-2]) / c.iloc[-2] * 100
        d5 = (price - c.iloc[-6]) / c.iloc[-6] * 100 if len(c) > 5 else 0
        pivot = (h.iloc[-1] + lo.iloc[-1] + c.iloc[-1]) / 3
        r1 = 2*pivot - lo.iloc[-1]
        s1 = 2*pivot - h.iloc[-1]
        hi20, lo20 = h.tail(20).max(), lo.tail(20).min()
        rng = hi20 - lo20
        r2 = hi20 + rng * 0.382
        s2 = lo20 - rng * 0.382
        rsi = calc_rsi(c).iloc[-1]
        macd_l, macd_s = calc_macd(c)
        macd_cross_up = (macd_l.iloc[-1] > macd_s.iloc[-1] and macd_l.iloc[-2] <= macd_s.iloc[-2])
        avg_vol = vol.tail(20).mean()
        vol_rel = vol.iloc[-1] / avg_vol if avg_vol > 0 else 1.0
        return {
            "ticker": ticker, "price": round(price, 2),
            "d1": round(d1, 2), "d5": round(d5, 2),
            "pivot": round(pivot, 2),
            "r1": round(r1, 2), "r2": round(r2, 2),
            "s1": round(s1, 2), "s2": round(s2, 2),
            "hi52": round(h.tail(252).max(), 2),
            "lo52": round(lo.tail(252).min(), 2),
            "vol_rel": round(vol_rel, 2),
            "rsi": round(rsi, 1),
            "macd_cross_up": macd_cross_up,
        }
    except Exception as e:
        log.warning(f"fetch_quote {ticker}: {e}")
        return None


def generate_chart(ticker, entry, tp1, tp2, stop):
    try:
        hist = yf.Ticker(ticker).history(period="1mo", interval="1d")
        if hist.empty or len(hist) < 5:
            return None
        hist.index = pd.to_datetime(hist.index)
        if hist.index.tz is not None:
            hist.index = hist.index.tz_localize(None)
        ap = [
            mpf.make_addplot([entry]*len(hist), color='cyan', linestyle='dashed', width=1.5),
            mpf.make_addplot([tp1]*len(hist), color='lime', linestyle='dashed', width=1.5),
            mpf.make_addplot([tp2]*len(hist), color='green', linestyle='dashed', width=1.5),
            mpf.make_addplot([stop]*len(hist), color='red', linestyle='dashed', width=1.5),
        ]
        s = mpf.make_mpf_style(base_mpf_style='nightclouds', gridstyle='')
        buf = io.BytesIO()
        mpf.plot(hist, type='candle', style=s, figsize=(10, 5),
                title=f'\n{ticker}  Entrada:{entry}  TP1:{tp1}  TP2:{tp2}  Stop:{stop}',
                addplot=ap, savefig=dict(fname=buf, dpi=100, bbox_inches='tight'))
        buf.seek(0)
        return buf
    except Exception as e:
        log.warning(f"Chart error {ticker}: {e}")
        return None


def get_top_signals(stocks, n=4):
    candidatos = []
    for t in stocks:
        d = fetch_quote(t, "3mo")
        if not d:
            continue
        score = 0
        if d["rsi"] < 30:
            score += 4
        elif d["rsi"] < 40:
            score += 2
        if d["macd_cross_up"]:
            score += 4
        if d["vol_rel"] >= 2.0:
            score += 3
        elif d["vol_rel"] >= 1.5:
            score += 1
        if d["d1"] >= 1.5:
            score += 2
        if d["d5"] >= 3.0:
            score += 2
        for nivel in [d["s1"], d["s2"]]:
            if abs(d["price"] - nivel) / nivel * 100 <= 1.5:
                score += 3
        if (d["hi52"] - d["price"]) / d["hi52"] * 100 <= 3.0:
            score += 2
        if score < 4:
            continue
        entry = d["price"]
        stop = round(d["s1"] * 0.985, 2)
        risk = entry - stop
        if risk <= 0:
            stop = round(entry * 0.97, 2)
            risk = entry - stop
        tp1 = round(entry + risk * 1.5, 2)
        tp2 = round(entry + risk * 3.0, 2)
        rr = round((tp1 - entry) / risk, 2) if risk > 0 else 0
        candidatos.append({
            **d, "score": score, "direction": "COMPRAR",
            "entry": entry, "stop": stop, "tp1": tp1, "tp2": tp2, "rr": rr,
        })
    candidatos.sort(key=lambda x: x["score"], reverse=True)
    return candidatos[:n]


def run_backtest(stocks):
    results = []
    for t in stocks[:12]:
        try:
            hist = yf.Ticker(t).history(period="3mo")
            if hist.empty or len(hist) < 30:
                continue
            c = hist["Close"]
            rsi = calc_rsi(c)
            macd_l, macd_s = calc_macd(c)
            wins = losses = total = 0
            for i in range(20, len(c)-6):
                if (rsi.iloc[i] < 40 and
                        macd_l.iloc[i] > macd_s.iloc[i] and
                        macd_l.iloc[i-1] <= macd_s.iloc[i-1]):
                    entry = c.iloc[i]
                    tp = entry * 1.03
                    sl = entry * 0.98
                    total += 1
                    for j in range(i+1, min(i+8, len(c))):
                        if c.iloc[j] >= tp:
                            wins += 1
                            break
                        elif c.iloc[j] <= sl:
                            losses += 1
                            break
            if total > 0:
                results.append({
                    "ticker": t, "total": total, "wins": wins,
                    "losses": losses, "winrate": round(wins/total*100, 1),
                })
        except Exception as e:
            log.warning(f"Backtest {t}: {e}")
    results.sort(key=lambda x: x["winrate"], reverse=True)
    return results


def allowed(message):
    if ALLOWED_USER_ID == 0:
        return True
    return message.from_user.id == ALLOWED_USER_ID


def ask_ai(prompt, max_chars=3000):
    for attempt in range(3):
        try:
            resp = ai_client.chat(
                model="mistral-small-latest",
                messages=[
                    ChatMessage(role="system", content=SYSTEM),
                    ChatMessage(role="user", content=prompt)
                ],
            )
            texto = resp.choices[0].message.content
            return texto[:max_chars] if len(texto) > max_chars else texto
        except Exception as e:
            if attempt < 2:
                time.sleep(3)
            else:
                log.error(f"Mistral: {e}")
                return "Error IA. Intenta en unos segundos."


def safe_send(chat_id, text, message_id=None):
    if len(text) > 4000:
        text = text[:4000] + "\n...truncado"
    try:
        if message_id:
            bot.edit_message_text(text, chat_id, message_id)
        else:
            bot.send_message(chat_id, text)
    except Exception as e:
        log.error(f"Send error: {e}")


def send_signal(chat_id, s):
    pct_tp1 = (s['tp1']/s['entry']-1)*100
    pct_tp2 = (s['tp2']/s['entry']-1)*100
    pct_sl = (s['stop']/s['entry']-1)*100
    text = (f"SENAL: {s['ticker']} - {s['direction']}\n"
            f"Entrada:  {s['entry']}\n"
            f"TP1:      {s['tp1']} ({pct_tp1:+.1f}%)\n"
            f"TP2:      {s['tp2']} ({pct_tp2:+.1f}%)\n"
            f"Stop:     {s['stop']} ({pct_sl:+.1f}%)\n"
            f"R/R:      {s['rr']}x\n"
            f"RSI:      {s['rsi']} | Vol: {s['vol_rel']}x\n"
            f"Score:    {s['score']}/17")
    chart = generate_chart(s['ticker'], s['entry'], s['tp1'], s['tp2'], s['stop'])
    if chart:
        try:
            bot.send_photo(chat_id, chart, caption=text)
            return
        except Exception as e:
            log.warning(f"Photo error: {e}")
    safe_send(chat_id, text)


def get_news(max_items=10):
    titulares = []
    for url in RSS_FEEDS:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:3]:
                title = entry.get("title", "").strip()
                if title and title not in titulares:
                    titulares.append(title)
            if len(titulares) >= max_items:
                break
        except:
            pass
    return titulares[:max_items]


def get_economic_calendar():
    eventos = []
    try:
        r = requests.get("https://nfs.faireconomy.media/ff_calendar_thisweek.json", timeout=8)
        eventos = [e for e in r.json() if e.get("impact") == "High"][:12]
    except:
        pass
    for t in ["NVDA","AAPL","MSFT","AMZN","META","TSLA","JPM"]:
        try:
            cal = yf.Ticker(t).calendar
            if cal is not None and "Earnings Date" in cal:
                fecha = str(cal["Earnings Date"][0])[:10]
                if datetime.now().strftime("%Y-%m") in fecha:
                    eventos.append({"date": fecha, "country": "US", "title": f"Earnings {t}", "impact": "High"})
        except:
            pass
    return eventos


def get_ipo_news():
    try:
        feed = feedparser.parse("https://news.google.com/rss/search?q=IPO+2026+NYSE+NASDAQ&hl=en&gl=US&ceid=US:en")
        return [e.get("title", "").strip() for e in feed.entries[:8] if e.get("title")]
    except:
        return []


def scan_sr_alerts(stocks):
    alerts = []
    for t in stocks:
        d = fetch_quote(t, "3mo")
        if not d:
            continue
        for nivel, nombre in [(d["s1"],"S1"),(d["s2"],"S2"),(d["r1"],"R1"),(d["r2"],"R2")]:
            dist = abs(d["price"] - nivel) / nivel * 100
            if dist <= 1.5:
                tipo = "SOPORTE" if "S" in nombre else "RESIST"
                alerts.append(f"{tipo} {t} cerca {nombre} {nivel} precio {d['price']} ({dist:.1f}%)")
    return alerts


def scan_explosions(stocks):
    out = []
    for t in stocks:
        d = fetch_quote(t, "3mo")
        if not d:
            continue
        dist_hi = (d["hi52"] - d["price"]) / d["hi52"] * 100
        if d["vol_rel"] >= 1.8 and d["d5"] >= 3.0 and dist_hi <= 8.0:
            out.append({"ticker": t, "d5": d["d5"], "vol_rel": d["vol_rel"],
                        "dist_hi52": dist_hi, "price": d["price"], "r1": d["r1"]})
    out.sort(key=lambda x: x["vol_rel"]*x["d5"], reverse=True)
    return out[:5]


def arrow(v):
    return f"{'(+)' if v >= 0 else '(-)'} {v:+.2f}%"


def main_kb():
    kb = InlineKeyboardMarkup()
    kb.row(InlineKeyboardButton("Noticias", callback_data="noticias"),
           InlineKeyboardButton("Mercados", callback_data="mercados"))
    kb.row(InlineKeyboardButton("Senales EU", callback_data="senales_eu"),
           InlineKeyboardButton("Senales EEUU", callback_data="senales_us"))
    kb.row(InlineKeyboardButton("Oportunidades", callback_data="oportunidades"),
           InlineKeyboardButton("Explosiones", callback_data="explosiones"))
    kb.row(InlineKeyboardButton("Calendario", callback_data="calendario"),
           InlineKeyboardButton("Crypto", callback_data="crypto"))
    kb.row(InlineKeyboardButton("S/R Scanner", callback_data="sr_scan"),
           InlineKeyboardButton("Macro", callback_data="macro"))
    kb.row(InlineKeyboardButton("Metales", callback_data="metales"),
           InlineKeyboardButton("IPOs", callback_data="ipos"))
    kb.row(InlineKeyboardButton("Backtest", callback_data="backtest"),
           InlineKeyboardButton("Alertas", callback_data="alertas"))
    return kb


@bot.message_handler(commands=["start"])
def cmd_start(msg):
    if not allowed(msg): return
    bot.send_message(msg.chat.id,
        "Financial Bot - Analisis EU y EEUU\n\n"
        "/senales_eu - Senales Europa con grafico\n"
        "/senales_us - Senales EEUU con grafico\n"
        "/macro - VIX, DXY, tipos interes, petroleo\n"
        "/backtest - Historico aciertos del sistema\n"
        "/alerta AAPL 200 - Alerta cuando llegue al precio\n"
        "/alertas - Ver alertas activas\n"
        "/borra_alerta 1 - Borrar alerta por numero\n"
        "/riesgo 10000 2 AAPL 890 865 - Cuantas acciones comprar\n"
        "/noticias /mercados /oportunidades\n"
        "/explosiones /calendario /sr\n"
        "/metales /ipos /analisis TICKER /crypto\n"
        "Pregunta libre - IA responde",
        reply_markup=main_kb()
    )


@bot.message_handler(commands=["noticias"])
def cmd_noticias(msg):
    if not allowed(msg): return
    m = bot.send_message(msg.chat.id, "Leyendo feeds RSS...")
    titulares = get_news(10)
    if titulares:
        bloque = "\n".join(f"- {t}" for t in titulares)
        prompt = (f"Noticias:\n{bloque}\n\n1. 3 titulares clave y por que\n"
                  "2. Sentimiento mercado\n3. Que esperar EU y EEUU\n4. Sectores a vigilar")
    else:
        prompt = "Estado mercados EU y EEUU hoy, factores clave, sectores con momentum."
    texto = ask_ai(prompt)
    safe_send(msg.chat.id, f"Noticias {datetime.now().strftime('%d/%m %H:%M')}\n\n{texto}", message_id=m.message_id)


@bot.message_handler(commands=["mercados"])
def cmd_mercados(msg):
    if not allowed(msg): return
    m = bot.send_message(msg.chat.id, "Cargando mercados...")
    lines, data_ai = [], []
    for t, nombre in INDICES.items():
        d = fetch_quote(t, "1mo")
        if d:
            lines.append(f"{arrow(d['d1'])} {nombre}: {d['price']:,.0f} | semana {arrow(d['d5'])}")
            data_ai.append(f"{nombre}: {d['price']:,.0f} ({d['d1']:+.2f}% hoy, {d['d5']:+.2f}% semana)")
    snap = "\n".join(lines) or "Sin datos"
    analisis = ask_ai("Snapshot:\n" + "\n".join(data_ai) + "\n\nLectura global, divergencias EU/EEUU, que vigilar.")
    safe_send(msg.chat.id, f"Mercados {datetime.now().strftime('%d/%m %H:%M')}\n\n{snap}\n\n{analisis}", message_id=m.message_id)


@bot.message_handler(commands=["senales_eu"])
def cmd_senales_eu(msg):
    if not allowed(msg): return
    m = bot.send_message(msg.chat.id, "Escaneando Europa con RSI + MACD...")
    signals = get_top_signals(EU_STOCKS, n=3)
    if not signals:
        safe_send(msg.chat.id, "Sin senales claras en Europa ahora mismo.", message_id=m.message_id)
        return
    bot.delete_message(msg.chat.id, m.message_id)
    safe_send(msg.chat.id, f"SENALES EUROPA {datetime.now().strftime('%d/%m %H:%M')}\n{len(signals)} oportunidades:")
    for s in signals:
        send_signal(msg.chat.id, s)
        time.sleep(1)


@bot.message_handler(commands=["senales_us"])
def cmd_senales_us(msg):
    if not allowed(msg): return
    m = bot.send_message(msg.chat.id, "Escaneando EEUU con RSI + MACD...")
    signals = get_top_signals(US_STOCKS + list(CRYPTO), n=3)
    if not signals:
        safe_send(msg.chat.id, "Sin senales claras en EEUU ahora mismo.", message_id=m.message_id)
        return
    bot.delete_message(msg.chat.id, m.message_id)
    safe_send(msg.chat.id, f"SENALES EEUU {datetime.now().strftime('%d/%m %H:%M')}\n{len(signals)} oportunidades:")
    for s in signals:
        send_signal(msg.chat.id, s)
        time.sleep(1)


@bot.message_handler(commands=["macro"])
def cmd_macro(msg):
    if not allowed(msg): return
    m = bot.send_message(msg.chat.id, "Cargando dashboard macro...")
    lines, data_ai = [], []
    for t, nombre in MACRO_TICKERS.items():
        d = fetch_quote(t, "1mo")
        if d:
            lines.append(f"{arrow(d['d1'])} {nombre}: {d['price']:,.2f} | semana {arrow(d['d5'])}")
            data_ai.append(f"{nombre}: {d['price']:,.2f} ({d['d1']:+.2f}% hoy)")
    snap = "\n".join(lines) or "Sin datos"
    prompt = ("Dashboard macro:\n" + "\n".join(data_ai) + "\n\n"
              "1. Risk-on o risk-off\n2. Que dice el VIX\n3. Impacto DXY en bolsas\n4. Que hacer con esta lectura")
    texto = ask_ai(prompt)
    safe_send(msg.chat.id, f"MACRO {datetime.now().strftime('%d/%m %H:%M')}\n\n{snap}\n\n{texto}", message_id=m.message_id)


@bot.message_handler(commands=["backtest"])
def cmd_backtest(msg):
    if not allowed(msg): return
    m = bot.send_message(msg.chat.id, "Ejecutando backtest 3 meses... (puede tardar 30s)")
    stocks = US_STOCKS[:8] + [s for s in EU_STOCKS if ".MC" in s or ".PA" in s][:5]
    results = run_backtest(stocks)
    if not results:
        safe_send(msg.chat.id, "Sin datos suficientes para backtest.", message_id=m.message_id)
        return
    lines = []
    total_wins = total_ops = 0
    for r in results:
        total_wins += r["wins"]
        total_ops += r["total"]
        lines.append(f"{r['ticker']}: {r['winrate']}% ({r['wins']}W/{r['losses']}L de {r['total']})")
    global_wr = round(total_wins/total_ops*100, 1) if total_ops > 0 else 0
    safe_send(msg.chat.id,
        f"BACKTEST 3 MESES\nCriterio: RSI menor 40 + cruce MACD / TP +3% SL -2%\n"
        f"Acierto global: {global_wr}% ({total_wins}W de {total_ops} ops)\n\n" + "\n".join(lines),
        message_id=m.message_id)


@bot.message_handler(commands=["alerta"])
def cmd_alerta(msg):
    if not allowed(msg): return
    parts = msg.text.split()
    if len(parts) < 3:
        safe_send(msg.chat.id, "Uso: /alerta TICKER PRECIO\nEj: /alerta AAPL 200\nEj: /alerta BTC-USD 90000")
        return
    ticker = parts[1].upper()
    try:
        precio = float(parts[2])
    except:
        safe_send(msg.chat.id, "Precio invalido.")
        return
    d = fetch_quote(ticker, "5d")
    if not d:
        safe_send(msg.chat.id, f"Ticker {ticker} no encontrado.")
        return
    direction = "sube a" if precio > d["price"] else "baja a"
    ALERTS[msg.chat.id].append({"ticker": ticker, "price": precio, "direction": direction, "triggered": False})
    activas = len([a for a in ALERTS[msg.chat.id] if not a["triggered"]])
    safe_send(msg.chat.id, f"Alerta creada\n{ticker} ahora: {d['price']}\nTe aviso cuando {direction} {precio}\nAlertas activas: {activas}")


@bot.message_handler(commands=["alertas"])
def cmd_alertas(msg):
    if not allowed(msg): return
    activas = [a for a in ALERTS[msg.chat.id] if not a["triggered"]]
    if not activas:
        safe_send(msg.chat.id, "No tienes alertas activas.\nCrea una con /alerta AAPL 200")
        return
    lines = []
    for i, a in enumerate(activas, 1):
        d = fetch_quote(a["ticker"], "5d")
        actual = d["price"] if d else "?"
        lines.append(f"{i}. {a['ticker']} cuando {a['direction']} {a['price']} (ahora: {actual})")
    safe_send(msg.chat.id, f"Alertas activas ({len(activas)}):\n\n" + "\n".join(lines))


@bot.message_handler(commands=["borra_alerta"])
def cmd_borra_alerta(msg):
    if not allowed(msg): return
    parts = msg.text.split()
    if len(parts) < 2:
        safe_send(msg.chat.id, "Uso: /borra_alerta NUMERO\nEj: /borra_alerta 1")
        return
    try:
        idx = int(parts[1]) - 1
        activas = [a for a in ALERTS[msg.chat.id] if not a["triggered"]]
        if 0 <= idx < len(activas):
            activas[idx]["triggered"] = True
            safe_send(msg.chat.id, f"Alerta {parts[1]} eliminada.")
        else:
            safe_send(msg.chat.id, "Numero invalido.")
    except:
        safe_send(msg.chat.id, "Uso: /borra_alerta NUMERO")


@bot.message_handler(commands=["riesgo"])
def cmd_riesgo(msg):
    if not allowed(msg): return
    parts = msg.text.split()
    if len(parts) < 6:
        safe_send(msg.chat.id, "Uso: /riesgo CAPITAL RIESGO% TICKER ENTRADA STOP\nEj: /riesgo 10000 2 AAPL 890 865")
        return
    try:
        capital = float(parts[1])
        riesgo = float(parts[2])
        ticker = parts[3].upper()
        entrada = float(parts[4])
        stop = float(parts[5])
        riesgo_e = capital * riesgo / 100
        riesgo_a = abs(entrada - stop)
        if riesgo_a == 0:
            safe_send(msg.chat.id, "Entrada y stop no pueden ser iguales.")
            return
        acciones = int(riesgo_e / riesgo_a)
        valor_pos = acciones * entrada
        tp1 = round(entrada + riesgo_a * 1.5, 2)
        tp2 = round(entrada + riesgo_a * 3.0, 2)
        safe_send(msg.chat.id,
            f"GESTION DE RIESGO - {ticker}\n\n"
            f"Capital:       {capital:,.0f}\n"
            f"Riesgo max:    {riesgo}% = {riesgo_e:,.0f}\n"
            f"Entrada:       {entrada}\n"
            f"Stop:          {stop} (-{riesgo_a:.2f})\n\n"
            f"ACCIONES A COMPRAR: {acciones}\n"
            f"Valor posicion: {valor_pos:,.0f}\n\n"
            f"TP1 (1.5x R/R): {tp1}\n"
            f"TP2 (3.0x R/R): {tp2}")
    except Exception as e:
        safe_send(msg.chat.id, f"Error: {e}")


@bot.message_handler(commands=["oportunidades"])
def cmd_oportunidades(msg):
    if not allowed(msg): return
    m = bot.send_message(msg.chat.id, "Escaneando oportunidades EU+EEUU...")
    rows = []
    for t in (US_STOCKS[:7] + EU_STOCKS[:6]):
        d = fetch_quote(t, "3mo")
        if d:
            rows.append(f"{t}: precio={d['price']}, RSI={d['rsi']}, MACD={'SI' if d['macd_cross_up'] else 'NO'}, vol={d['vol_rel']}x, 5d={d['d5']}%")
    prompt = ("Datos tecnicos EU+EEUU:\n" + "\n".join(rows) + "\n\n"
              "1. 2-3 mejores setups con RSI y momentum\n"
              "2. Acciones a evitar\n3. Trade concreto: entrada/objetivo/stop\n4. Riesgo 1-10")
    texto = ask_ai(prompt)
    safe_send(msg.chat.id, f"Oportunidades\n\n{texto}", message_id=m.message_id)


@bot.message_handler(commands=["explosiones"])
def cmd_explosiones(msg):
    if not allowed(msg): return
    m = bot.send_message(msg.chat.id, "Buscando explosiones...")
    candidates = scan_explosions(US_STOCKS + EU_STOCKS)
    if not candidates:
        safe_send(msg.chat.id, "Mercado en calma, sin senales de explosion.", message_id=m.message_id)
        return
    rows = [f"{c['ticker']}: {c['price']} | semana {c['d5']:+.1f}% | vol {c['vol_rel']}x"
            for c in candidates]
    bloque = "\n".join(rows)
    texto = ask_ai(f"Posibles explosiones:\n{bloque}\n\n1. Por que podria subir\n2. Nivel a superar\n3. Riesgo")
    safe_send(msg.chat.id, f"Posibles Explosiones\n\n{bloque}\n\n{texto}", message_id=m.message_id)


@bot.message_handler(commands=["calendario"])
def cmd_calendario(msg):
    if not allowed(msg): return
    m = bot.send_message(msg.chat.id, "Cargando calendario...")
    eventos = get_economic_calendar()
    if eventos:
        eventos_sorted = sorted(eventos, key=lambda x: x.get("date",""))
        lines = [f"{e.get('date','')[:10]} [{e.get('country','').upper()}] {e.get('title','')}"
                 for e in eventos_sorted]
        bloque = "\n".join(lines)
        prompt = (f"Eventos esta semana:\n{bloque}\n\nTraduce en espanol. 1. 3 mas importantes\n2. Que esperar\n3. Sectores afectados")
        texto = ask_ai(prompt)
        safe_send(msg.chat.id, f"Calendario Esta Semana\n\n{bloque}\n\n{texto}", message_id=m.message_id)
    else:
        texto = ask_ai("Calendario eventos economicos clave esta semana: Fed, BCE, inflacion, empleo, earnings.")
        safe_send(msg.chat.id, f"Calendario Economico\n\n{texto}", message_id=m.message_id)


@bot.message_handler(commands=["sr"])
def cmd_sr(msg):
    if not allowed(msg): return
    m = bot.send_message(msg.chat.id, "Escaneando S/R...")
    alerts = scan_sr_alerts(US_STOCKS[:10] + EU_STOCKS[:10])
    if alerts:
        bloque = "\n".join(alerts)
        texto = ask_ai(f"Acciones en zona S/R:\n{bloque}\n\n1. Rebote o ruptura\n2. Que confirmaria\n3. Operativa")
        safe_send(msg.chat.id, f"S/R Scanner\n\n{bloque}\n\n{texto}", message_id=m.message_id)
    else:
        safe_send(msg.chat.id, "S/R Scanner\n\nNinguna accion en zona critica ahora mismo.", message_id=m.message_id)


@bot.message_handler(commands=["metales"])
def cmd_metales(msg):
    if not allowed(msg): return
    m = bot.send_message(msg.chat.id, "Analizando metales...")
    lines, data_ai = [], []
    for t, nombre in METALES.items():
        d = fetch_quote(t, "3mo")
        if d:
            lines.append(f"{arrow(d['d1'])} {nombre}: {d['price']:,.2f} | semana {arrow(d['d5'])} | RSI {d['rsi']}")
            data_ai.append(f"{nombre}: precio={d['price']}, RSI={d['rsi']}, 1d={d['d1']}%, S1={d['s1']}, R1={d['r1']}")
    snap = "\n".join(lines) or "Sin datos"
    prompt = (f"Metales:\n" + "\n".join(data_ai) + "\n\n"
              "1. Tendencia y RSI\n2. Senal: COMPRAR/VENDER/ESPERAR\n3. Entrada, stop, objetivo\n4. Catalizador macro")
    texto = ask_ai(prompt)
    safe_send(msg.chat.id, f"Metales {datetime.now().strftime('%d/%m %H:%M')}\n\n{snap}\n\n{texto}", message_id=m.message_id)


@bot.message_handler(commands=["ipos"])
def cmd_ipos(msg):
    if not allowed(msg): return
    m = bot.send_message(msg.chat.id, "Cargando IPOs...")
    lines = []
    for ipo in IPOS_WATCH:
        if ipo["ticker"]:
            d = fetch_quote(ipo["ticker"], "1mo")
            if d:
                lines.append(f"{ipo['nombre']} ({ipo['ticker']}) {d['price']} | hoy {d['d1']:+.2f}% | RSI {d['rsi']}\n  Val: {ipo['valor']} | {ipo['bolsa']}")
            else:
                lines.append(f"{ipo['nombre']} ({ipo['ticker']}) - {ipo['estado']}\n  Val: {ipo['valor']}")
        else:
            lines.append(f"{ipo['nombre']} - {ipo['estado']}\n  Sector: {ipo['sector']} | Val: {ipo['valor']}")
    snap = "\n\n".join(lines)
    noticias_txt = "\n".join(f"- {n}" for n in get_ipo_news())
    prompt = (f"IPOs:\n{snap}\n\nNoticias:\n{noticias_txt}\n\n"
              "1. Vale la pena entrar? SI/NO/ESPERAR\n2. Riesgo principal\n3. Mejor potencial 6-12 meses\n4. Debut o esperar correccion?")
    texto = ask_ai(prompt)
    safe_send(msg.chat.id, f"IPOs {datetime.now().strftime('%d/%m %H:%M')}\n\n{snap}\n\n{texto}", message_id=m.message_id)


@bot.message_handler(commands=["analisis"])
def cmd_analisis(msg):
    if not allowed(msg): return
    parts = msg.text.split()
    if len(parts) < 2:
        safe_send(msg.chat.id, "Uso: /analisis TICKER\nEj: /analisis AAPL o /analisis SAN.MC")
        return
    ticker = parts[1].upper()
    m = bot.send_message(msg.chat.id, f"Analizando {ticker}...")
    d = fetch_quote(ticker, "6mo")
    if not d:
        safe_send(msg.chat.id, f"Sin datos para {ticker}.", message_id=m.message_id)
        return
    prompt = (f"Accion: {ticker}\nPrecio: {d['price']} | 1d: {d['d1']}% | 5d: {d['d5']}%\n"
              f"RSI: {d['rsi']} | MACD cruce alcista: {d['macd_cross_up']} | Vol: {d['vol_rel']}x\n"
              f"R2: {d['r2']} R1: {d['r1']} | Pivot: {d['pivot']} | S1: {d['s1']} S2: {d['s2']}\n"
              f"Max52: {d['hi52']} | Min52: {d['lo52']}\n\n"
              "1. Posicion tecnica\n2. Niveles clave\n3. Escenario alcista vs bajista\n4. Sesgo operativo\n5. Entrada, stop y objetivo")
    texto = ask_ai(prompt)
    header = (f"{ticker}\n"
              f"Precio {d['price']} | Hoy {d['d1']:+.2f}% | Semana {d['d5']:+.2f}%\n"
              f"RSI {d['rsi']} | Vol {d['vol_rel']}x | MACD: {'SI' if d['macd_cross_up'] else 'NO'}\n"
              f"R2 {d['r2']} | R1 {d['r1']} | Pivot {d['pivot']} | S1 {d['s1']} | S2 {d['s2']}\n"
              f"Rango 52s: {d['lo52']} - {d['hi52']}\n\n")
    safe_send(msg.chat.id, header + texto, message_id=m.message_id)


@bot.message_handler(commands=["crypto"])
def cmd_crypto(msg):
    if not allowed(msg): return
    m = bot.send_message(msg.chat.id, "Cargando crypto...")
    nombres = {"BTC-USD":"Bitcoin","ETH-USD":"Ethereum","SOL-USD":"Solana","BNB-USD":"BNB"}
    lines, data_ai = [], []
    for t in CRYPTO:
        d = fetch_quote(t, "1mo")
        if d:
            n = nombres.get(t, t)
            lines.append(f"{arrow(d['d1'])} {n}: {d['price']:,.0f} | semana {arrow(d['d5'])} | RSI {d['rsi']}")
            data_ai.append(f"{n}: {d['price']:,.0f} ({d['d1']:+.2f}%) RSI={d['rsi']} S1={d['s1']} R1={d['r1']}")
    texto = ask_ai("Crypto:\n" + "\n".join(data_ai) + "\n\n1. Lectura tecnica global\n2. Mejor setup ahora\n3. Niveles criticos BTC")
    safe_send(msg.chat.id, f"Crypto {datetime.now().strftime('%H:%M')}\n\n" + "\n".join(lines) + f"\n\n{texto}", message_id=m.message_id)


@bot.message_handler(func=lambda m: True)
def handle_text(msg):
    if not allowed(msg): return
    resp = ask_ai(f"Pregunta: {msg.text}\nResponde como analista financiero.")
    safe_send(msg.chat.id, resp)


@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    bot.answer_callback_query(call.id)
    call.message.from_user = call.from_user
    handlers = {
        "noticias": cmd_noticias,
        "mercados": cmd_mercados,
        "senales_eu": cmd_senales_eu,
        "senales_us": cmd_senales_us,
        "oportunidades": cmd_oportunidades,
        "explosiones": cmd_explosiones,
        "calendario": cmd_calendario,
        "sr_scan": cmd_sr,
        "crypto": cmd_crypto,
        "macro": cmd_macro,
        "metales": cmd_metales,
        "ipos": cmd_ipos,
        "backtest": cmd_backtest,
        "alertas": cmd_alertas,
    }
    fn = handlers.get(call.data)
    if fn:
        fn(call.message)


def job_senales_eu():
    safe_send(ALLOWED_USER_ID, f"BUENOS DIAS - Senales Europa {datetime.now().strftime('%d/%m')}")
    signals = get_top_signals(EU_STOCKS, n=3)
    if signals:
        for s in signals:
            send_signal(ALLOWED_USER_ID, s)
            time.sleep(2)
    else:
        safe_send(ALLOWED_USER_ID, "Sin senales claras en Europa esta manana.")
    titulares = get_news(6)
    bloque = "\n".join(f"- {t}" for t in titulares) if titulares else "Sin noticias"
    eventos = get_economic_calendar()
    cal_hoy = [e for e in eventos if datetime.now().strftime("%Y-%m-%d") in e.get("date","")]
    cal_txt = "\n".join(f"- {e.get('title','')}" for e in cal_hoy) or "Sin eventos clave hoy"
    texto = ask_ai(f"Briefing {datetime.now().strftime('%A %d/%m')}:\nNoticias:\n{bloque}\nEventos hoy:\n{cal_txt}\n\nResumen del dia, niveles DAX e IBEX.")
    safe_send(ALLOWED_USER_ID, f"Briefing Manana\n\n{texto}")


def job_senales_us():
    safe_send(ALLOWED_USER_ID, f"PREMERCADO EEUU {datetime.now().strftime('%d/%m %H:%M')}")
    signals = get_top_signals(US_STOCKS + list(CRYPTO), n=3)
    if signals:
        for s in signals:
            send_signal(ALLOWED_USER_ID, s)
            time.sleep(2)
    else:
        safe_send(ALLOWED_USER_ID, "Sin senales claras en EEUU para esta sesion.")


def job_close_eu():
    lines, data_ai = [], []
    for t, nombre in list(INDICES.items())[3:]:
        d = fetch_quote(t, "5d")
        if d:
            lines.append(f"{arrow(d['d1'])} {nombre}: {d['price']:,.0f}")
            data_ai.append(f"{nombre}: {d['d1']:+.2f}%")
    snap = "\n".join(lines)
    texto = ask_ai("Cierre EU:\n" + "\n".join(data_ai) + "\n\nResumen sesion europea y que esperar de EEUU.")
    safe_send(ALLOWED_USER_ID, f"Cierre Europa {datetime.now().strftime('%d/%m %H:%M')}\n\n{snap}\n\n{texto}")


def job_close_us():
    lines, data_ai = [], []
    for t, nombre in list(INDICES.items())[:3]:
        d = fetch_quote(t, "5d")
        if d:
            lines.append(f"{arrow(d['d1'])} {nombre}: {d['price']:,.0f}")
            data_ai.append(f"{nombre}: {d['d1']:+.2f}%")
    snap = "\n".join(lines)
    texto = ask_ai("Cierre EEUU:\n" + "\n".join(data_ai) + "\n\nResumen sesion americana y perspectiva manana.")
    safe_send(ALLOWED_USER_ID, f"Cierre EEUU {datetime.now().strftime('%d/%m %H:%M')}\n\n{snap}\n\n{texto}")


def job_check_alerts():
    for chat_id, alert_list in ALERTS.items():
        for alert in alert_list:
            if alert["triggered"]:
                continue
            try:
                d = fetch_quote(alert["ticker"], "1d")
                if not d:
                    continue
                triggered = False
                if "sube" in alert["direction"] and d["price"] >= alert["price"]:
                    triggered = True
                elif "baja" in alert["direction"] and d["price"] <= alert["price"]:
                    triggered = True
                if triggered:
                    alert["triggered"] = True
                    safe_send(chat_id, f"ALERTA ACTIVADA\n{alert['ticker']} ha {alert['direction']} {alert['price']}\nPrecio actual: {d['price']}")
            except Exception as e:
                log.warning(f"Alert check: {e}")


def job_sr_scanner():
    alerts = scan_sr_alerts(US_STOCKS[:8] + EU_STOCKS[:8])
    if not alerts:
        return
    bloque = "\n".join(alerts[:6])
    texto = ask_ai(f"Alertas S/R:\n{bloque}\n\nTop 2 mas interesantes y operativa.")
    safe_send(ALLOWED_USER_ID, f"Alerta S/R {datetime.now().strftime('%H:%M')}\n\n{bloque}\n\n{texto}")


def job_explosion_scanner():
    candidates = scan_explosions(US_STOCKS + EU_STOCKS)
    if not candidates:
        return
    rows = [f"{c['ticker']}: semana {c['d5']:+.1f}% | vol {c['vol_rel']}x | R1 {c['r1']}" for c in candidates[:3]]
    bloque = "\n".join(rows)
    texto = ask_ai(f"Posibles explosiones:\n{bloque}\n\nAnalisis y niveles a vigilar.")
    safe_send(ALLOWED_USER_ID, f"Explosiones {datetime.now().strftime('%H:%M')}\n\n{bloque}\n\n{texto}")


def job_metales_scanner():
    data_ai = []
    for t, nombre in METALES.items():
        d = fetch_quote(t, "3mo")
        if d:
            data_ai.append(f"{nombre}: precio={d['price']}, RSI={d['rsi']}, 1d={d['d1']}%, S1={d['s1']}, R1={d['r1']}")
    if not data_ai:
        return
    prompt = ("Metales:\n" + "\n".join(data_ai) + "\n\nHay senal clara de entrada o salida?\nSi SI: cual, entrada, stop, objetivo.\nSi NO: responde solo SIN SENAL.")
    texto = ask_ai(prompt)
    if "SIN SENAL" in texto.upper():
        return
    safe_send(ALLOWED_USER_ID, f"Alerta Metales {datetime.now().strftime('%H:%M')}\n\n{texto}")


if __name__ == "__main__":
    if ALLOWED_USER_ID:
        scheduler = BackgroundScheduler(timezone=MADRID)
        scheduler.add_job(job_senales_eu, "cron", hour=9, minute=0)
        scheduler.add_job(job_senales_us, "cron", hour=15, minute=0)
        scheduler.add_job(job_close_eu, "cron", hour=17, minute=35)
        scheduler.add_job(job_close_us, "cron", hour=22, minute=5)
        scheduler.add_job(job_check_alerts, "interval", minutes=5)
        scheduler.add_job(job_sr_scanner, "interval", hours=2)
        scheduler.add_job(job_explosion_scanner, "interval", hours=3)
        scheduler.add_job(job_metales_scanner, "interval", hours=4)
        scheduler.start()
        log.info("Jobs automaticos activados")
    log.info("Financial Bot arrancado - Version Mejorada")
    bot.infinity_polling(timeout=60, long_polling_timeout=60)
