"""
Financial Telegram Bot - Version Completa
RSI + MACD + Graficos + Alertas + Backtest + Macro
ETFs + Anomalias + Sectores + Bull Detector + Noticias Impacto
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

# Nombres completos
NOMBRES = {
    "SAN.MC":"Banco Santander","BBVA.MC":"BBVA","ITX.MC":"Inditex",
    "REP.MC":"Repsol","TEF.MC":"Telefonica","IBE.MC":"Iberdrola",
    "ELE.MC":"Endesa","AMS.MC":"Amadeus IT",
    "OR.PA":"L'Oreal","BNP.PA":"BNP Paribas","AIR.PA":"Airbus",
    "MC.PA":"LVMH","TTE.PA":"TotalEnergies","LVMH.PA":"LVMH","BN.PA":"Danone",
    "BMW.DE":"BMW","BAS.DE":"BASF","DTE.DE":"Deutsche Telekom",
    "VOW3.DE":"Volkswagen","SIE.DE":"Siemens","SAP":"SAP",
    "HSBA.L":"HSBC","BP.L":"BP","SHEL.L":"Shell","AZN.L":"AstraZeneca","RIO.L":"Rio Tinto",
    "ASML":"ASML Holding","NESN.SW":"Nestle","NOVO-B.CO":"Novo Nordisk",
    "AAPL":"Apple","MSFT":"Microsoft","NVDA":"Nvidia","AMZN":"Amazon",
    "GOOGL":"Alphabet","META":"Meta","TSLA":"Tesla","AMD":"AMD","INTC":"Intel",
    "CRM":"Salesforce","ORCL":"Oracle",
    "JPM":"JPMorgan","GS":"Goldman Sachs","BAC":"Bank of America","V":"Visa","MA":"Mastercard",
    "JNJ":"Johnson & Johnson","UNH":"UnitedHealth","PFE":"Pfizer",
    "XOM":"ExxonMobil","CVX":"Chevron",
    "WMT":"Walmart","HD":"Home Depot","MCD":"McDonalds","KO":"Coca-Cola","PEP":"PepsiCo",
    "BA":"Boeing","CAT":"Caterpillar","NFLX":"Netflix","DIS":"Disney",
    "BTC-USD":"Bitcoin","ETH-USD":"Ethereum","SOL-USD":"Solana","BNB-USD":"BNB",
    # ETFs indices
    "SPY":"S&P 500 ETF","QQQ":"Nasdaq 100 ETF","IWM":"Russell 2000 ETF","DIA":"Dow Jones ETF",
    "VTI":"Total Market ETF","VEA":"Europa ETF","EWG":"DAX ETF","EWQ":"CAC 40 ETF","EZU":"Eurozona ETF",
    # ETFs sectoriales
    "XLK":"Tech ETF","XLF":"Finanzas ETF","XLE":"Energia ETF","XLV":"Salud ETF",
    "XLI":"Industrial ETF","XLY":"Consumo ETF","XLP":"Consumo Basico ETF",
    "XLU":"Utilities ETF","XLB":"Materiales ETF","XLRE":"Inmobiliario ETF","XLC":"Comunicaciones ETF",
    # ETFs tematicos
    "GLD":"Oro ETF","SLV":"Plata ETF","USO":"Petroleo ETF","PDBC":"Commodities ETF",
    "ARKK":"ARK Innovation ETF","BOTZ":"Robotica IA ETF","ICLN":"Energia Limpia ETF",
    "HACK":"Ciberseguridad ETF","SOXX":"Semiconductores ETF","CIBR":"Ciberseguridad ETF",
    "MCHI":"China ETF","EEM":"Emergentes ETF",
    # Rumores/especiales
    "LUNR":"Intuitive Machines","RKLB":"Rocket Lab","ASTS":"AST SpaceMobile",
    "ACHR":"Archer Aviation","JOBY":"Joby Aviation",
}

def nombre(ticker):
    return NOMBRES.get(ticker, ticker)

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

# ETFs organizados por categoria
ETFS_INDICES = ["SPY","QQQ","IWM","DIA","VTI","VEA","EWG","EZU"]
ETFS_SECTORIALES = ["XLK","XLF","XLE","XLV","XLI","XLY","XLP","XLU","XLB","XLRE","XLC"]
ETFS_TEMATICOS = ["GLD","SLV","USO","ARKK","BOTZ","ICLN","HACK","SOXX","EEM","MCHI"]
ETFS_ESPECIALES = ["LUNR","RKLB","ASTS","ACHR","JOBY"]  # proximas a SpaceX/NASA

# Sectores SP500 para analisis de rotacion
SECTORES_SP500 = {
    "Tecnologia": ["AAPL","MSFT","NVDA","AMD","INTC","CRM","ORCL","XLK"],
    "Finanzas": ["JPM","GS","BAC","V","MA","XLF"],
    "Salud": ["JNJ","UNH","PFE","XLV"],
    "Energia": ["XOM","CVX","XLE"],
    "Consumo Discrecional": ["TSLA","AMZN","HD","MCD","NFLX","XLY"],
    "Consumo Basico": ["WMT","KO","PEP","XLP"],
    "Industrial": ["BA","CAT","XLI"],
    "Comunicaciones": ["GOOGL","META","DIS","XLC"],
    "Materiales": ["XLB"],
    "Utilities": ["XLU"],
    "Inmobiliario": ["XLRE"],
}

RSS_FEEDS = [
    "https://feeds.marketwatch.com/marketwatch/topstories/",
    "https://news.google.com/rss/search?q=stock+market+europe+usa&hl=en&gl=US&ceid=US:en",
    "https://feeds.reuters.com/reuters/businessNews",
]

RSS_IMPACTO = [
    "https://news.google.com/rss/search?q=merger+acquisition+takeover+2026&hl=en&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=earnings+surprise+beat+2026&hl=en&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=FDA+approval+drug+2026&hl=en&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=SpaceX+NASA+contract+IPO+2026&hl=en&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=short+squeeze+unusual+volume+2026&hl=en&gl=US&ceid=US:en",
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
        d20 = (price - c.iloc[-21]) / c.iloc[-21] * 100 if len(c) > 20 else 0
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
        # Tendencia: precio sobre EMA20
        ema20 = c.ewm(span=20, adjust=False).mean().iloc[-1]
        sobre_ema20 = price > ema20
        return {
            "ticker": ticker,
            "nombre": nombre(ticker),
            "price": round(price, 4) if price < 10 else round(price, 2),
            "d1": round(d1, 2), "d5": round(d5, 2), "d20": round(d20, 2),
            "pivot": round(pivot, 2),
            "r1": round(r1, 2), "r2": round(r2, 2),
            "s1": round(s1, 2), "s2": round(s2, 2),
            "hi52": round(h.tail(252).max(), 2),
            "lo52": round(lo.tail(252).min(), 2),
            "vol_rel": round(vol_rel, 2),
            "rsi": round(rsi, 1),
            "macd_cross_up": macd_cross_up,
            "sobre_ema20": sobre_ema20,
            "ema20": round(ema20, 2),
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
        titulo = f'\n{nombre(ticker)} ({ticker})  Entrada:{entry}  TP1:{tp1}  TP2:{tp2}  Stop:{stop}'
        mpf.plot(hist, type='candle', style=s, figsize=(10, 5),
                title=titulo, addplot=ap,
                savefig=dict(fname=buf, dpi=100, bbox_inches='tight'))
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
        if d["rsi"] > 65:
            continue
        if d["vol_rel"] < 0.8:
            continue
        score = 0
        if d["rsi"] < 30:       score += 5
        elif d["rsi"] < 40:     score += 3
        elif d["rsi"] < 50:     score += 1
        if d["macd_cross_up"]:  score += 4
        if d["vol_rel"] >= 2.0: score += 4
        elif d["vol_rel"] >= 1.5: score += 2
        elif d["vol_rel"] >= 1.0: score += 1
        if d["d1"] >= 1.5:      score += 2
        if d["d5"] >= 3.0:      score += 2
        for nivel in [d["s1"], d["s2"]]:
            if nivel > 0 and abs(d["price"] - nivel) / nivel * 100 <= 1.5:
                score += 3
        if d["hi52"] > 0 and (d["hi52"] - d["price"]) / d["hi52"] * 100 <= 3.0:
            score += 2
        if score < 6:
            continue
        entry = d["price"]
        stop = round(d["s1"] * 0.985, 2)
        risk = entry - stop
        if risk <= 0 or risk > entry * 0.08:
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


def scan_anomalias(stocks):
    """Detecta acciones con volumen anomalo 3x+ sin noticia clara — posible rumor o insider."""
    anomalias = []
    for t in stocks:
        try:
            hist = yf.Ticker(t).history(period="1mo")
            if hist.empty or len(hist) < 10:
                continue
            vol = hist["Volume"]
            c = hist["Close"]
            avg_vol = vol.tail(20).mean()
            vol_hoy = vol.iloc[-1]
            vol_rel = vol_hoy / avg_vol if avg_vol > 0 else 1.0
            d1 = (c.iloc[-1] - c.iloc[-2]) / c.iloc[-2] * 100
            if vol_rel >= 2.5:
                anomalias.append({
                    "ticker": t,
                    "nombre": nombre(t),
                    "vol_rel": round(vol_rel, 1),
                    "d1": round(d1, 2),
                    "price": round(c.iloc[-1], 2),
                })
        except:
            pass
    anomalias.sort(key=lambda x: x["vol_rel"], reverse=True)
    return anomalias[:8]


def analisis_sectores():
    """Semaforo de sectores: bull, neutro o bear. Detecta rotacion."""
    resultados = []
    for sector, tickers in SECTORES_SP500.items():
        rsis, d5s, vol_rels, sobre_ema = [], [], [], []
        for t in tickers:
            d = fetch_quote(t, "3mo")
            if d:
                rsis.append(d["rsi"])
                d5s.append(d["d5"])
                vol_rels.append(d["vol_rel"])
                sobre_ema.append(1 if d["sobre_ema20"] else 0)
        if not rsis:
            continue
        rsi_med = round(np.mean(rsis), 1)
        d5_med = round(np.mean(d5s), 2)
        vol_med = round(np.mean(vol_rels), 2)
        pct_sobre_ema = round(np.mean(sobre_ema) * 100, 0)
        # Semaforo
        if rsi_med > 55 and d5_med > 1.0 and pct_sobre_ema >= 60:
            estado = "BULL"
            emoji = "verde"
        elif rsi_med < 40 or d5_med < -2.0:
            estado = "BEAR"
            emoji = "rojo"
        else:
            estado = "NEUTRO"
            emoji = "amarillo"
        resultados.append({
            "sector": sector, "estado": estado, "emoji": emoji,
            "rsi": rsi_med, "d5": d5_med, "vol": vol_med,
            "sobre_ema": pct_sobre_ema,
        })
    resultados.sort(key=lambda x: (x["estado"] == "BULL", x["d5"]), reverse=True)
    return resultados


def bull_detector():
    """Detecta posibles bull runs nacientes en sectores, crypto y ETFs."""
    senales = []

    # Sectores
    sectores = analisis_sectores()
    bulls = [s for s in sectores if s["estado"] == "BULL"]
    for b in bulls:
        senales.append({
            "tipo": "SECTOR",
            "nombre": b["sector"],
            "fuerza": b["d5"],
            "rsi": b["rsi"],
            "detalle": f"RSI medio {b['rsi']} | semana {b['d5']:+.1f}% | {b['sobre_ema']}% sobre EMA20",
        })

    # ETFs tematicos con momentum
    for t in ETFS_TEMATICOS + ETFS_INDICES[:4]:
        d = fetch_quote(t, "3mo")
        if not d:
            continue
        if d["d20"] > 5.0 and d["rsi"] > 50 and d["rsi"] < 72 and d["vol_rel"] >= 1.0:
            senales.append({
                "tipo": "ETF",
                "nombre": d["nombre"],
                "ticker": t,
                "fuerza": d["d20"],
                "rsi": d["rsi"],
                "detalle": f"RSI {d['rsi']} | mes {d['d20']:+.1f}% | vol {d['vol_rel']}x",
            })

    # Crypto dominancia y momentum
    for t in CRYPTO:
        d = fetch_quote(t, "3mo")
        if not d:
            continue
        if d["d20"] > 10.0 and d["rsi"] > 50 and d["rsi"] < 75:
            senales.append({
                "tipo": "CRYPTO",
                "nombre": d["nombre"],
                "ticker": t,
                "fuerza": d["d20"],
                "rsi": d["rsi"],
                "detalle": f"RSI {d['rsi']} | mes {d['d20']:+.1f}% | vol {d['vol_rel']}x",
            })

    senales.sort(key=lambda x: x["fuerza"], reverse=True)
    return senales[:8]


def get_noticias_impacto():
    """Noticias de alto impacto: M&A, earnings sorpresa, FDA, rumores."""
    todas = []
    for url in RSS_IMPACTO:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:4]:
                title = entry.get("title", "").strip()
                link = entry.get("link", "")
                published = entry.get("published", "")[:16] if entry.get("published") else ""
                if title and title not in [t["title"] for t in todas]:
                    todas.append({"title": title, "link": link, "published": published})
        except:
            pass
    return todas[:15]


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
                if (rsi.iloc[i] < 45 and rsi.iloc[i] < 65 and
                        macd_l.iloc[i] > macd_s.iloc[i] and
                        macd_l.iloc[i-1] <= macd_s.iloc[i-1]):
                    entry = c.iloc[i]
                    tp = entry * 1.03
                    sl = entry * 0.98
                    total += 1
                    for j in range(i+1, min(i+8, len(c))):
                        if c.iloc[j] >= tp:
                            wins += 1; break
                        elif c.iloc[j] <= sl:
                            losses += 1; break
            if total > 0:
                results.append({
                    "ticker": t, "nombre": nombre(t),
                    "total": total, "wins": wins, "losses": losses,
                    "winrate": round(wins/total*100, 1),
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
    rsi_txt = f"{s['rsi']} (sobreventa - buena entrada)" if s['rsi'] < 40 else f"{s['rsi']}"
    text = (f"SENAL: {s['nombre']} ({s['ticker']})\n"
            f"Accion: {s['direction']}\n"
            f"Entrada:  {s['entry']}\n"
            f"TP1:      {s['tp1']} ({pct_tp1:+.1f}%)\n"
            f"TP2:      {s['tp2']} ({pct_tp2:+.1f}%)\n"
            f"Stop:     {s['stop']} ({pct_sl:+.1f}%)\n"
            f"R/R:      {s['rr']}x\n"
            f"RSI:      {rsi_txt}\n"
            f"Volumen:  {s['vol_rel']}x media\n"
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
                    eventos.append({"date": fecha, "country": "US",
                                    "title": f"Earnings {nombre(t)}", "impact": "High"})
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
        for nivel, nom in [(d["s1"],"S1"),(d["s2"],"S2"),(d["r1"],"R1"),(d["r2"],"R2")]:
            dist = abs(d["price"] - nivel) / nivel * 100
            if dist <= 1.5:
                tipo = "SOPORTE" if "S" in nom else "RESIST"
                alerts.append(f"{tipo} {d['nombre']} ({t}) cerca {nom} {nivel} precio {d['price']} ({dist:.1f}%)")
    return alerts


def scan_explosions(stocks):
    out = []
    for t in stocks:
        d = fetch_quote(t, "3mo")
        if not d:
            continue
        dist_hi = (d["hi52"] - d["price"]) / d["hi52"] * 100
        if d["vol_rel"] >= 1.8 and d["d5"] >= 3.0 and dist_hi <= 8.0:
            out.append({"ticker": t, "nombre": d["nombre"], "d5": d["d5"],
                        "vol_rel": d["vol_rel"], "price": d["price"], "r1": d["r1"]})
    out.sort(key=lambda x: x["vol_rel"]*x["d5"], reverse=True)
    return out[:5]


def arrow(v):
    return f"{'(+)' if v >= 0 else '(-)'} {v:+.2f}%"


def semaforo(estado):
    if estado == "BULL":   return "[BULL]"
    if estado == "BEAR":   return "[BEAR]"
    return "[=]"


def main_kb():
    kb = InlineKeyboardMarkup()
    kb.row(InlineKeyboardButton("Noticias", callback_data="noticias"),
           InlineKeyboardButton("Mercados", callback_data="mercados"))
    kb.row(InlineKeyboardButton("Senales EU", callback_data="senales_eu"),
           InlineKeyboardButton("Senales EEUU", callback_data="senales_us"))
    kb.row(InlineKeyboardButton("ETFs", callback_data="etfs"),
           InlineKeyboardButton("Sectores", callback_data="sectores"))
    kb.row(InlineKeyboardButton("Bull Detector", callback_data="bull_detector"),
           InlineKeyboardButton("Anomalias", callback_data="anomalias"))
    kb.row(InlineKeyboardButton("Noticias Impacto", callback_data="noticias_impacto"),
           InlineKeyboardButton("Macro", callback_data="macro"))
    kb.row(InlineKeyboardButton("Oportunidades", callback_data="oportunidades"),
           InlineKeyboardButton("Explosiones", callback_data="explosiones"))
    kb.row(InlineKeyboardButton("Calendario", callback_data="calendario"),
           InlineKeyboardButton("Crypto", callback_data="crypto"))
    kb.row(InlineKeyboardButton("S/R Scanner", callback_data="sr_scan"),
           InlineKeyboardButton("Metales", callback_data="metales"))
    kb.row(InlineKeyboardButton("IPOs", callback_data="ipos"),
           InlineKeyboardButton("Backtest", callback_data="backtest"))
    kb.row(InlineKeyboardButton("Alertas", callback_data="alertas"),
           InlineKeyboardButton("Riesgo", callback_data="riesgo_info"))
    return kb


# ── HANDLERS ─────────────────────────────────────────────────────────────────

@bot.message_handler(commands=["start"])
def cmd_start(msg):
    if not allowed(msg): return
    bot.send_message(msg.chat.id,
        "Financial Bot - Analisis EU y EEUU\n\n"
        "NUEVOS COMANDOS:\n"
        "/etfs - ETFs indices, sectoriales y tematicos\n"
        "/anomalias - Volumen anomalo 2.5x+ posible rumor\n"
        "/sectores - Semaforo 11 sectores SP500\n"
        "/bull_detector - Bull runs nacientes\n"
        "/noticias_impacto - M&A, earnings sorpresa, FDA\n\n"
        "COMANDOS BASE:\n"
        "/senales_eu /senales_us - Senales con grafico\n"
        "/macro /backtest /calendario\n"
        "/alerta TICKER PRECIO\n"
        "/riesgo CAPITAL % TICKER ENTRADA STOP\n"
        "/analisis TICKER /crypto /metales /ipos\n"
        "/oportunidades /explosiones /sr\n"
        "Pregunta libre - IA responde",
        reply_markup=main_kb()
    )


@bot.message_handler(commands=["etfs"])
def cmd_etfs(msg):
    if not allowed(msg): return
    m = bot.send_message(msg.chat.id, "Escaneando ETFs con RSI + MACD...")
    todos = ETFS_INDICES + ETFS_TEMATICOS[:6]
    signals = get_top_signals(todos, n=3)
    lines_idx, lines_tem = [], []
    for t in ETFS_INDICES:
        d = fetch_quote(t, "1mo")
        if d:
            lines_idx.append(f"{arrow(d['d1'])} {d['nombre']}: {d['price']} | semana {arrow(d['d5'])} | RSI {d['rsi']}")
    for t in ETFS_TEMATICOS:
        d = fetch_quote(t, "1mo")
        if d:
            lines_tem.append(f"{arrow(d['d1'])} {d['nombre']}: {d['price']} | semana {arrow(d['d5'])} | RSI {d['rsi']}")
    snap = "INDICES\n" + "\n".join(lines_idx) + "\n\nTEMATICOS\n" + "\n".join(lines_tem)
    safe_send(msg.chat.id, f"ETFs {datetime.now().strftime('%d/%m %H:%M')}\n\n{snap}", message_id=m.message_id)
    if signals:
        time.sleep(1)
        safe_send(msg.chat.id, f"MEJORES SETUPS ETF:")
        for s in signals:
            send_signal(msg.chat.id, s)
            time.sleep(1)
    else:
        prompt = ("ETFs:\n" + "\n".join(lines_idx[:5]) + "\n\n"
                  "1. Cual tiene mejor momentum ahora\n"
                  "2. Cual evitar\n3. Mejor ETF para los proximos 30 dias")
        texto = ask_ai(prompt)
        safe_send(msg.chat.id, texto)


@bot.message_handler(commands=["anomalias"])
def cmd_anomalias(msg):
    if not allowed(msg): return
    m = bot.send_message(msg.chat.id, "Buscando volumen anomalo 2.5x+... (posibles rumores o insider)")
    todos = US_STOCKS + EU_STOCKS[:10] + ETFS_ESPECIALES
    anomalias = scan_anomalias(todos)
    if not anomalias:
        safe_send(msg.chat.id, "Sin anomalias de volumen detectadas ahora mismo.", message_id=m.message_id)
        return
    rows = [f"{a['nombre']} ({a['ticker']}): vol {a['vol_rel']}x | hoy {a['d1']:+.2f}% | precio {a['price']}"
            for a in anomalias]
    bloque = "\n".join(rows)
    prompt = (f"Acciones con volumen anomalo hoy:\n{bloque}\n\n"
              "1. Cual es mas sospechosa de tener noticia detras\n"
              "2. Podria ser rumor de M&A, earnings, FDA o insider?\n"
              "3. Como operar este tipo de movimiento\n"
              "4. Cual tiene mejor potencial y cual es trampa")
    texto = ask_ai(prompt)
    safe_send(msg.chat.id, f"ANOMALIAS DE VOLUMEN {datetime.now().strftime('%d/%m %H:%M')}\n\n{bloque}\n\n{texto}", message_id=m.message_id)


@bot.message_handler(commands=["sectores"])
def cmd_sectores(msg):
    if not allowed(msg): return
    m = bot.send_message(msg.chat.id, "Analizando 11 sectores SP500... (puede tardar 20s)")
    sectores = analisis_sectores()
    if not sectores:
        safe_send(msg.chat.id, "Sin datos de sectores.", message_id=m.message_id)
        return
    bulls = [s for s in sectores if s["estado"] == "BULL"]
    bears = [s for s in sectores if s["estado"] == "BEAR"]
    neutros = [s for s in sectores if s["estado"] == "NEUTRO"]
    lines = []
    for s in sectores:
        lines.append(f"{semaforo(s['estado'])} {s['sector']}: RSI {s['rsi']} | semana {s['d5']:+.1f}% | {s['sobre_ema']}% sobre EMA20")
    snap = "\n".join(lines)
    prompt = (f"Semaforo sectores SP500:\n{snap}\n\n"
              f"1. Cual es el sector mas fuerte ahora y por que\n"
              f"2. Hay rotacion sectorial? De donde a donde va el dinero\n"
              f"3. Que sectores evitar\n"
              f"4. Mejor ETF sectorial para comprar ahora")
    texto = ask_ai(prompt)
    resumen = f"Bulls: {len(bulls)} | Neutros: {len(neutros)} | Bears: {len(bears)}"
    safe_send(msg.chat.id, f"SECTORES SP500 {datetime.now().strftime('%d/%m %H:%M')}\n{resumen}\n\n{snap}\n\n{texto}", message_id=m.message_id)


@bot.message_handler(commands=["bull_detector"])
def cmd_bull_detector(msg):
    if not allowed(msg): return
    m = bot.send_message(msg.chat.id, "Detectando bull runs nacientes... (sectores, ETFs, crypto)")
    senales = bull_detector()
    if not senales:
        safe_send(msg.chat.id, "No se detectan bull runs claros ahora mismo. Mercado en consolidacion.", message_id=m.message_id)
        return
    lines = []
    for s in senales:
        ticker_txt = f" ({s['ticker']})" if "ticker" in s else ""
        lines.append(f"[{s['tipo']}] {s['nombre']}{ticker_txt}: {s['detalle']}")
    bloque = "\n".join(lines)
    prompt = (f"Posibles bull runs detectados:\n{bloque}\n\n"
              "1. Cual tiene mas probabilidad de ser bull run real\n"
              "2. Es bull market general o solo sectorial\n"
              "3. Como posicionarse: ETF concreto, entrada, stop\n"
              "4. Que podria truncarlo")
    texto = ask_ai(prompt)
    safe_send(msg.chat.id, f"BULL DETECTOR {datetime.now().strftime('%d/%m %H:%M')}\n\n{bloque}\n\n{texto}", message_id=m.message_id)


@bot.message_handler(commands=["noticias_impacto"])
def cmd_noticias_impacto(msg):
    if not allowed(msg): return
    m = bot.send_message(msg.chat.id, "Buscando noticias de alto impacto: M&A, earnings, FDA, rumores...")
    noticias = get_noticias_impacto()
    if not noticias:
        safe_send(msg.chat.id, "Sin noticias de alto impacto detectadas ahora mismo.", message_id=m.message_id)
        return
    lines = [f"- {n['title']}" for n in noticias[:12]]
    bloque = "\n".join(lines)
    prompt = (f"Noticias de alto impacto:\n{bloque}\n\n"
              "1. Cual tiene mayor impacto en bolsa hoy\n"
              "2. Hay alguna OPA, fusion o adquisicion relevante\n"
              "3. Earnings sorpresa o decepcion importante\n"
              "4. Acciones o sectores directamente afectados y como operar")
    texto = ask_ai(prompt)
    safe_send(msg.chat.id, f"NOTICIAS IMPACTO {datetime.now().strftime('%d/%m %H:%M')}\n\n{bloque}\n\n{texto}", message_id=m.message_id)


@bot.message_handler(commands=["noticias"])
def cmd_noticias(msg):
    if not allowed(msg): return
    m = bot.send_message(msg.chat.id, "Leyendo feeds RSS...")
    titulares = get_news(10)
    if titulares:
        bloque = "\n".join(f"- {t}" for t in titulares)
        prompt = (f"Noticias:\n{bloque}\n\n1. 3 titulares clave\n"
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
    for t, nom in INDICES.items():
        d = fetch_quote(t, "1mo")
        if d:
            lines.append(f"{arrow(d['d1'])} {nom}: {d['price']:,.0f} | semana {arrow(d['d5'])}")
            data_ai.append(f"{nom}: {d['price']:,.0f} ({d['d1']:+.2f}% hoy, {d['d5']:+.2f}% semana)")
    snap = "\n".join(lines) or "Sin datos"
    analisis = ask_ai("Snapshot:\n" + "\n".join(data_ai) + "\n\nLectura global, divergencias EU/EEUU, que vigilar.")
    safe_send(msg.chat.id, f"Mercados {datetime.now().strftime('%d/%m %H:%M')}\n\n{snap}\n\n{analisis}", message_id=m.message_id)


@bot.message_handler(commands=["senales_eu"])
def cmd_senales_eu(msg):
    if not allowed(msg): return
    m = bot.send_message(msg.chat.id, "Escaneando Europa con RSI + MACD + filtros...")
    signals = get_top_signals(EU_STOCKS, n=3)
    if not signals:
        safe_send(msg.chat.id, "Sin senales validas en Europa ahora mismo.", message_id=m.message_id)
        return
    bot.delete_message(msg.chat.id, m.message_id)
    safe_send(msg.chat.id, f"SENALES EUROPA {datetime.now().strftime('%d/%m %H:%M')}\n{len(signals)} oportunidades:")
    for s in signals:
        send_signal(msg.chat.id, s)
        time.sleep(1)


@bot.message_handler(commands=["senales_us"])
def cmd_senales_us(msg):
    if not allowed(msg): return
    m = bot.send_message(msg.chat.id, "Escaneando EEUU con RSI + MACD + filtros...")
    signals = get_top_signals(US_STOCKS + list(CRYPTO), n=3)
    if not signals:
        safe_send(msg.chat.id, "Sin senales validas en EEUU ahora mismo.", message_id=m.message_id)
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
    for t, nom in MACRO_TICKERS.items():
        d = fetch_quote(t, "1mo")
        if d:
            lines.append(f"{arrow(d['d1'])} {nom}: {d['price']:,.2f} | semana {arrow(d['d5'])}")
            data_ai.append(f"{nom}: {d['price']:,.2f} ({d['d1']:+.2f}%)")
    snap = "\n".join(lines) or "Sin datos"
    texto = ask_ai("Macro:\n" + "\n".join(data_ai) + "\n\n1. Risk-on o risk-off\n2. Que dice el VIX\n3. Impacto DXY\n4. Que hacer")
    safe_send(msg.chat.id, f"MACRO {datetime.now().strftime('%d/%m %H:%M')}\n\n{snap}\n\n{texto}", message_id=m.message_id)


@bot.message_handler(commands=["backtest"])
def cmd_backtest(msg):
    if not allowed(msg): return
    m = bot.send_message(msg.chat.id, "Ejecutando backtest 3 meses... (30s)")
    stocks = US_STOCKS[:8] + [s for s in EU_STOCKS if ".MC" in s or ".PA" in s][:5]
    results = run_backtest(stocks)
    if not results:
        safe_send(msg.chat.id, "Sin datos.", message_id=m.message_id)
        return
    lines = []
    total_wins = total_ops = 0
    for r in results:
        total_wins += r["wins"]
        total_ops += r["total"]
        bar = "W"*r["wins"] + "L"*r["losses"]
        lines.append(f"{r['nombre']} ({r['ticker']}): {r['winrate']}% - {r['wins']}W/{r['losses']}L  {bar}")
    global_wr = round(total_wins/total_ops*100, 1) if total_ops > 0 else 0
    safe_send(msg.chat.id,
        f"BACKTEST 3 MESES\nCriterio: RSI<45 + cruce MACD / TP +3% SL -2%\n"
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
    ALERTS[msg.chat.id].append({"ticker": ticker, "nombre": d["nombre"], "price": precio, "direction": direction, "triggered": False})
    activas = len([a for a in ALERTS[msg.chat.id] if not a["triggered"]])
    safe_send(msg.chat.id, f"Alerta creada\n{d['nombre']} ({ticker}) ahora: {d['price']}\nTe aviso cuando {direction} {precio}\nAlertas activas: {activas}")


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
        lines.append(f"{i}. {a['nombre']} ({a['ticker']}) cuando {a['direction']} {a['price']} (ahora: {actual})")
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
        nom = nombre(ticker)
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
            f"GESTION DE RIESGO\n{nom} ({ticker})\n\n"
            f"Capital:          {capital:,.0f}\n"
            f"Riesgo maximo:    {riesgo}% = {riesgo_e:,.0f}\n"
            f"Entrada:          {entrada}\n"
            f"Stop loss:        {stop} (-{riesgo_a:.2f} por accion)\n\n"
            f"ACCIONES A COMPRAR: {acciones}\n"
            f"Valor de posicion:  {valor_pos:,.0f}\n\n"
            f"TP1 (R/R 1.5x): {tp1}\n"
            f"TP2 (R/R 3.0x): {tp2}")
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
            rows.append(f"{d['nombre']} ({t}): RSI={d['rsi']}, MACD={'SI' if d['macd_cross_up'] else 'NO'}, vol={d['vol_rel']}x, 5d={d['d5']}%")
    prompt = ("Datos EU+EEUU:\n" + "\n".join(rows) + "\n\n"
              "1. 2-3 mejores setups\n2. Acciones a evitar\n3. Trade concreto: entrada/objetivo/stop\n4. Riesgo 1-10")
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
    rows = [f"{c['nombre']} ({c['ticker']}): {c['price']} | semana {c['d5']:+.1f}% | vol {c['vol_rel']}x" for c in candidates]
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
        lines = [f"{e.get('date','')[:10]} [{e.get('country','').upper()}] {e.get('title','')}" for e in eventos_sorted]
        bloque = "\n".join(lines)
        texto = ask_ai(f"Eventos:\n{bloque}\n\n1. 3 mas importantes\n2. Que esperar\n3. Sectores afectados")
        safe_send(msg.chat.id, f"Calendario\n\n{bloque}\n\n{texto}", message_id=m.message_id)
    else:
        texto = ask_ai("Eventos clave esta semana: Fed, BCE, inflacion, empleo, earnings.")
        safe_send(msg.chat.id, f"Calendario\n\n{texto}", message_id=m.message_id)


@bot.message_handler(commands=["sr"])
def cmd_sr(msg):
    if not allowed(msg): return
    m = bot.send_message(msg.chat.id, "Escaneando S/R...")
    alerts = scan_sr_alerts(US_STOCKS[:10] + EU_STOCKS[:10])
    if alerts:
        bloque = "\n".join(alerts)
        texto = ask_ai(f"S/R:\n{bloque}\n\n1. Rebote o ruptura\n2. Confirmacion\n3. Operativa")
        safe_send(msg.chat.id, f"S/R Scanner\n\n{bloque}\n\n{texto}", message_id=m.message_id)
    else:
        safe_send(msg.chat.id, "Sin acciones en zona critica ahora mismo.", message_id=m.message_id)


@bot.message_handler(commands=["metales"])
def cmd_metales(msg):
    if not allowed(msg): return
    m = bot.send_message(msg.chat.id, "Analizando metales...")
    lines, data_ai = [], []
    for t, nom in METALES.items():
        d = fetch_quote(t, "3mo")
        if d:
            lines.append(f"{arrow(d['d1'])} {nom}: {d['price']:,.2f} | semana {arrow(d['d5'])} | RSI {d['rsi']}")
            data_ai.append(f"{nom}: precio={d['price']}, RSI={d['rsi']}, 1d={d['d1']}%, S1={d['s1']}, R1={d['r1']}")
    snap = "\n".join(lines) or "Sin datos"
    texto = ask_ai(f"Metales:\n" + "\n".join(data_ai) + "\n\n1. Tendencia\n2. COMPRAR/VENDER/ESPERAR\n3. Entrada stop objetivo\n4. Catalizador macro")
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
    texto = ask_ai(f"IPOs:\n{snap}\n\nNoticias:\n{noticias_txt}\n\n1. SI/NO/ESPERAR\n2. Riesgo\n3. Mejor potencial\n4. Debut o esperar")
    safe_send(msg.chat.id, f"IPOs {datetime.now().strftime('%d/%m %H:%M')}\n\n{snap}\n\n{texto}", message_id=m.message_id)


@bot.message_handler(commands=["analisis"])
def cmd_analisis(msg):
    if not allowed(msg): return
    parts = msg.text.split()
    if len(parts) < 2:
        safe_send(msg.chat.id, "Uso: /analisis TICKER\nEj: /analisis AAPL o /analisis SPY o /analisis SAN.MC")
        return
    ticker = parts[1].upper()
    m = bot.send_message(msg.chat.id, f"Analizando {nombre(ticker)}...")
    d = fetch_quote(ticker, "6mo")
    if not d:
        safe_send(msg.chat.id, f"Sin datos para {ticker}.", message_id=m.message_id)
        return
    prompt = (f"Empresa/ETF: {d['nombre']} ({ticker})\n"
              f"Precio: {d['price']} | 1d: {d['d1']}% | 5d: {d['d5']}% | 20d: {d['d20']}%\n"
              f"RSI: {d['rsi']} | MACD cruce alcista: {d['macd_cross_up']} | Vol: {d['vol_rel']}x\n"
              f"Sobre EMA20: {d['sobre_ema20']} (EMA20: {d['ema20']})\n"
              f"R2: {d['r2']} R1: {d['r1']} | Pivot: {d['pivot']} | S1: {d['s1']} S2: {d['s2']}\n"
              f"Max52: {d['hi52']} | Min52: {d['lo52']}\n\n"
              "1. Posicion tecnica completa\n2. Niveles clave\n"
              "3. Escenario alcista vs bajista con precios\n"
              "4. Sesgo operativo\n5. Entrada, stop y objetivo si hay setup")
    texto = ask_ai(prompt)
    header = (f"{d['nombre']} ({ticker})\n"
              f"Precio {d['price']} | Hoy {d['d1']:+.2f}% | Semana {d['d5']:+.2f}% | Mes {d['d20']:+.2f}%\n"
              f"RSI {d['rsi']} | Vol {d['vol_rel']}x | MACD: {'SI' if d['macd_cross_up'] else 'NO'} | EMA20: {'SOBRE' if d['sobre_ema20'] else 'BAJO'}\n"
              f"R2 {d['r2']} | R1 {d['r1']} | Pivot {d['pivot']} | S1 {d['s1']} | S2 {d['s2']}\n"
              f"Rango 52s: {d['lo52']} - {d['hi52']}\n\n")
    safe_send(msg.chat.id, header + texto, message_id=m.message_id)


@bot.message_handler(commands=["crypto"])
def cmd_crypto(msg):
    if not allowed(msg): return
    m = bot.send_message(msg.chat.id, "Cargando crypto...")
    lines, data_ai = [], []
    for t in CRYPTO:
        d = fetch_quote(t, "1mo")
        if d:
            lines.append(f"{arrow(d['d1'])} {d['nombre']}: {d['price']:,.0f} | semana {arrow(d['d5'])} | RSI {d['rsi']}")
            data_ai.append(f"{d['nombre']}: {d['price']:,.0f} ({d['d1']:+.2f}%) RSI={d['rsi']} S1={d['s1']} R1={d['r1']}")
    texto = ask_ai("Crypto:\n" + "\n".join(data_ai) + "\n\n1. Lectura tecnica global\n2. Mejor setup\n3. Niveles criticos Bitcoin\n4. Hay altseason?")
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
        "noticias": cmd_noticias, "mercados": cmd_mercados,
        "senales_eu": cmd_senales_eu, "senales_us": cmd_senales_us,
        "etfs": cmd_etfs, "sectores": cmd_sectores,
        "bull_detector": cmd_bull_detector, "anomalias": cmd_anomalias,
        "noticias_impacto": cmd_noticias_impacto,
        "oportunidades": cmd_oportunidades, "explosiones": cmd_explosiones,
        "calendario": cmd_calendario, "sr_scan": cmd_sr,
        "crypto": cmd_crypto, "macro": cmd_macro,
        "metales": cmd_metales, "ipos": cmd_ipos,
        "backtest": cmd_backtest, "alertas": cmd_alertas,
        "riesgo_info": lambda m: safe_send(m.chat.id, "Uso: /riesgo CAPITAL RIESGO% TICKER ENTRADA STOP\nEj: /riesgo 10000 2 AAPL 890 865"),
    }
    fn = handlers.get(call.data)
    if fn:
        fn(call.message)


# ── JOBS AUTOMATICOS ──────────────────────────────────────────────────────────

def job_senales_eu():
    safe_send(ALLOWED_USER_ID, f"BUENOS DIAS - Senales Europa {datetime.now().strftime('%d/%m')}")
    signals = get_top_signals(EU_STOCKS, n=3)
    if signals:
        for s in signals:
            send_signal(ALLOWED_USER_ID, s)
            time.sleep(2)
    else:
        safe_send(ALLOWED_USER_ID, "Sin senales validas en Europa esta manana.")
    titulares = get_news(6)
    bloque = "\n".join(f"- {t}" for t in titulares) if titulares else "Sin noticias"
    eventos = get_economic_calendar()
    cal_hoy = [e for e in eventos if datetime.now().strftime("%Y-%m-%d") in e.get("date","")]
    cal_txt = "\n".join(f"- {e.get('title','')}" for e in cal_hoy) or "Sin eventos clave"
    texto = ask_ai(f"Briefing {datetime.now().strftime('%A %d/%m')}:\nNoticias:\n{bloque}\nEventos:\n{cal_txt}\n\nResumen y niveles clave DAX e IBEX.")
    safe_send(ALLOWED_USER_ID, f"Briefing Manana\n\n{texto}")


def job_senales_us():
    safe_send(ALLOWED_USER_ID, f"PREMERCADO EEUU {datetime.now().strftime('%d/%m %H:%M')}")
    signals = get_top_signals(US_STOCKS + list(CRYPTO), n=3)
    if signals:
        for s in signals:
            send_signal(ALLOWED_USER_ID, s)
            time.sleep(2)
    else:
        safe_send(ALLOWED_USER_ID, "Sin senales validas en EEUU para esta sesion.")


def job_close_eu():
    lines, data_ai = [], []
    for t, nom in list(INDICES.items())[3:]:
        d = fetch_quote(t, "5d")
        if d:
            lines.append(f"{arrow(d['d1'])} {nom}: {d['price']:,.0f}")
            data_ai.append(f"{nom}: {d['d1']:+.2f}%")
    snap = "\n".join(lines)
    texto = ask_ai("Cierre EU:\n" + "\n".join(data_ai) + "\n\nResumen y que esperar de EEUU.")
    safe_send(ALLOWED_USER_ID, f"Cierre Europa {datetime.now().strftime('%d/%m %H:%M')}\n\n{snap}\n\n{texto}")


def job_close_us():
    lines, data_ai = [], []
    for t, nom in list(INDICES.items())[:3]:
        d = fetch_quote(t, "5d")
        if d:
            lines.append(f"{arrow(d['d1'])} {nom}: {d['price']:,.0f}")
            data_ai.append(f"{nom}: {d['d1']:+.2f}%")
    snap = "\n".join(lines)
    texto = ask_ai("Cierre EEUU:\n" + "\n".join(data_ai) + "\n\nResumen y perspectiva manana.")
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
                    safe_send(chat_id, f"ALERTA ACTIVADA\n{alert['nombre']} ({alert['ticker']}) ha {alert['direction']} {alert['price']}\nPrecio actual: {d['price']}")
            except Exception as e:
                log.warning(f"Alert check: {e}")


def job_bull_detector():
    """Cada 4h — detecta bull runs y los envia si hay algo nuevo."""
    senales = bull_detector()
    if not senales:
        return
    lines = []
    for s in senales[:4]:
        ticker_txt = f" ({s['ticker']})" if "ticker" in s else ""
        lines.append(f"[{s['tipo']}] {s['nombre']}{ticker_txt}: {s['detalle']}")
    bloque = "\n".join(lines)
    texto = ask_ai(f"Bull runs detectados:\n{bloque}\n\nEs momento de entrar? Como posicionarse? ETF o accion concreta.")
    safe_send(ALLOWED_USER_ID, f"BULL DETECTOR {datetime.now().strftime('%H:%M')}\n\n{bloque}\n\n{texto}")


def job_anomalias_scanner():
    """Cada 2h — anomalias de volumen."""
    todos = US_STOCKS + EU_STOCKS[:10] + ETFS_ESPECIALES
    anomalias = scan_anomalias(todos)
    if not anomalias:
        return
    top = anomalias[:4]
    rows = [f"{a['nombre']} ({a['ticker']}): vol {a['vol_rel']}x | hoy {a['d1']:+.2f}%" for a in top]
    bloque = "\n".join(rows)
    texto = ask_ai(f"Anomalias volumen:\n{bloque}\n\nHay noticia detras? Como operar.")
    safe_send(ALLOWED_USER_ID, f"ANOMALIA VOLUMEN {datetime.now().strftime('%H:%M')}\n\n{bloque}\n\n{texto}")


def job_noticias_impacto():
    """Cada 3h — noticias de alto impacto."""
    noticias = get_noticias_impacto()
    if not noticias:
        return
    lines = [f"- {n['title']}" for n in noticias[:6]]
    bloque = "\n".join(lines)
    prompt = (f"Noticias impacto:\n{bloque}\n\nHay algo importante que afecte a bolsa ahora mismo?\nSi SI: que y como operar.\nSi NO: responde solo SIN NOVEDAD.")
    texto = ask_ai(prompt)
    if "SIN NOVEDAD" in texto.upper():
        return
    safe_send(ALLOWED_USER_ID, f"NOTICIA IMPACTO {datetime.now().strftime('%H:%M')}\n\n{texto}")


def job_sr_scanner():
    alerts = scan_sr_alerts(US_STOCKS[:8] + EU_STOCKS[:8])
    if not alerts:
        return
    bloque = "\n".join(alerts[:6])
    texto = ask_ai(f"S/R:\n{bloque}\n\nTop 2 mas interesantes y operativa.")
    safe_send(ALLOWED_USER_ID, f"Alerta S/R {datetime.now().strftime('%H:%M')}\n\n{bloque}\n\n{texto}")


def job_explosion_scanner():
    candidates = scan_explosions(US_STOCKS + EU_STOCKS)
    if not candidates:
        return
    rows = [f"{c['nombre']} ({c['ticker']}): semana {c['d5']:+.1f}% | vol {c['vol_rel']}x" for c in candidates[:3]]
    bloque = "\n".join(rows)
    texto = ask_ai(f"Explosiones:\n{bloque}\n\nAnalisis y niveles.")
    safe_send(ALLOWED_USER_ID, f"Explosiones {datetime.now().strftime('%H:%M')}\n\n{bloque}\n\n{texto}")


def job_metales_scanner():
    data_ai = []
    for t, nom in METALES.items():
        d = fetch_quote(t, "3mo")
        if d:
            data_ai.append(f"{nom}: precio={d['price']}, RSI={d['rsi']}, 1d={d['d1']}%, S1={d['s1']}, R1={d['r1']}")
    if not data_ai:
        return
    texto = ask_ai("Metales:\n" + "\n".join(data_ai) + "\n\nHay senal clara? Si SI: cual, entrada, stop, objetivo. Si NO: responde solo SIN SENAL.")
    if "SIN SENAL" in texto.upper():
        return
    safe_send(ALLOWED_USER_ID, f"Alerta Metales {datetime.now().strftime('%H:%M')}\n\n{texto}")


if __name__ == "__main__":
    if ALLOWED_USER_ID:
        scheduler = BackgroundScheduler(timezone=MADRID)
        scheduler.add_job(job_senales_eu,        "cron",     hour=9,  minute=0)
        scheduler.add_job(job_senales_us,        "cron",     hour=15, minute=0)
        scheduler.add_job(job_close_eu,          "cron",     hour=17, minute=35)
        scheduler.add_job(job_close_us,          "cron",     hour=22, minute=5)
        scheduler.add_job(job_check_alerts,      "interval", minutes=5)
        scheduler.add_job(job_anomalias_scanner, "interval", hours=2)
        scheduler.add_job(job_noticias_impacto,  "interval", hours=3)
        scheduler.add_job(job_bull_detector,     "interval", hours=4)
        scheduler.add_job(job_explosion_scanner, "interval", hours=3)
        scheduler.add_job(job_metales_scanner,   "interval", hours=4)
        scheduler.add_job(job_sr_scanner,        "interval", hours=2)
        scheduler.start()
        log.info("Jobs automaticos activados")
    log.info("Financial Bot arrancado - Version Completa")
    bot.infinity_polling(timeout=60, long_polling_timeout=60)
