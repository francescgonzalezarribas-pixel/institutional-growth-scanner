import os
import io
import math
import requests
import feedparser
import yfinance as yf
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Wedge, Circle

# --- CONFIGURACIÓN ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
ALLOWED_USER_ID = int(os.environ.get("ALLOWED_USER_ID", 0))

bot = telebot.TeleBot(TELEGRAM_TOKEN, parse_mode="Markdown")


def is_authorized(user_id: int) -> bool:
    if ALLOWED_USER_ID == 0:
        return True
    return user_id == ALLOWED_USER_ID


# --- INDICADORES ---
def compute_rsi(series: pd.Series, period: int = 14) -> float:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return float(rsi.iloc[-1]) if not pd.isna(rsi.iloc[-1]) else 50.0


def get_fear_greed() -> float:
    try:
        r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=8)
        data = r.json()
        return float(data["data"][0]["value"])
    except Exception:
        return 50.0


def get_vix() -> float:
    try:
        vix = yf.Ticker("^VIX")
        hist = vix.history(period="5d")
        return float(hist["Close"].iloc[-1])
    except Exception:
        return 18.0


def score_rsi(rsi: float) -> int:
    """Más bajo = más barato"""
    if rsi <= 30: return 10
    if rsi <= 40: return 8
    if rsi <= 50: return 6
    if rsi <= 60: return 4
    if rsi <= 70: return 2
    return 1


def score_ema_pct(pct: float) -> int:
    """% sobre EMA200: negativo = más barato"""
    if pct <= -25: return 10
    if pct <= -15: return 9
    if pct <= -8:  return 8
    if pct <= -3:  return 7
    if pct <= 3:   return 5
    if pct <= 10:  return 3
    if pct <= 20:  return 2
    return 1


def score_volume(rel_vol: float) -> int:
    if rel_vol >= 2.0: return 8
    if rel_vol >= 1.5: return 7
    if rel_vol >= 1.1: return 6
    if rel_vol >= 0.8: return 5
    if rel_vol >= 0.5: return 4
    return 3


def score_dist_52w(pct: float) -> int:
    """Distancia al máximo 52 semanas (negativo = más barato)"""
    if pct <= -45: return 10
    if pct <= -30: return 9
    if pct <= -20: return 8
    if pct <= -12: return 7
    if pct <= -5:  return 6
    if pct <= 0:   return 5
    if pct <= 8:   return 3
    return 1


def score_vix(vix: float) -> int:
    if vix >= 30: return 9
    if vix >= 25: return 7
    if vix >= 20: return 5
    if vix >= 16: return 4
    return 3


def score_fear_greed(fg: float) -> int:
    if fg <= 25: return 10
    if fg <= 40: return 8
    if fg <= 55: return 5
    if fg <= 70: return 2
    return 1


def get_label(score: int) -> str:
    if score >= 72: return "BARATO — OPORTUNIDAD"
    if score >= 58: return "NEUTRAL — ACUMULACIÓN"
    if score >= 42: return "NEUTRAL"
    if score >= 28: return "CARO — PRECAUCIÓN"
    return "MUY CARO — ALTO RIESGO"


def calculate_value_index(ticker: str) -> dict:
    ticker = ticker.upper().strip()
    is_crypto = any(x in ticker for x in ["BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "DOGE"]) or "-USD" in ticker

    mapping = {
        "BTC": "BTC-USD",
        "ETH": "ETH-USD",
        "SOL": "SOL-USD",
        "BNB": "BNB-USD",
    }
    ticker = mapping.get(ticker, ticker)

    stock = yf.Ticker(ticker)
    hist = stock.history(period="1y")

    if hist.empty or len(hist) < 60:
        raise ValueError(f"No hay datos suficientes para {ticker}")

    close = hist["Close"]