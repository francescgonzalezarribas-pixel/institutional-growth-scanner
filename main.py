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
MISTRAL_API_KEY = os.environ.get("MISTRAL_API_KEY", "").strip()
ALLOWED_USER_ID = int(os.environ.get("ALLOWED_USER_ID", 0))

bot = telebot.TeleBot(TELEGRAM_TOKEN, parse_mode="Markdown")


def is_authorized(user_id: int) -> bool:
    if ALLOWED_USER_ID == 0:
        return True
    return user_id == ALLOWED_USER_ID


# --- MISTRAL (OPCIONAL) ---
def ask_mistral(prompt: str, system_prompt: str = "Eres un analista financiero experto.") -> str:
    if not MISTRAL_API_KEY:
        return ""

    url = "https://api.mistral.ai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {MISTRAL_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "mistral-small-latest",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 600,
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=20)
        data = response.json()
        if response.status_code == 200 and "choices" in data:
            return data["choices"][0]["message"]["content"]
        return ""
    except Exception as e:
        print(f"Mistral error: {e}")
        return ""


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
        return 20.0


def score_rsi(rsi: float) -> int:
    if rsi < 30: return 10
    if rsi < 40: return 8
    if rsi < 50: return 6
    if rsi < 60: return 5
    if rsi < 70: return 3
    if rsi < 80: return 2
    return 1


def score_ema_pct(pct: float) -> int:
    if pct < -20: return 10
    if pct < -10: return 8
    if pct < -5:  return 7
    if pct < 0:   return 6
    if pct < 5:   return 5
    if pct < 10:  return 4
    if pct < 20:  return 3
    return 2


def score_volume(rel_vol: float) -> int:
    if rel_vol > 2.0: return 8
    if rel_vol > 1.5: return 7
    if rel_vol > 1.2: return 6
    if rel_vol > 0.8: return 5
    if rel_vol >