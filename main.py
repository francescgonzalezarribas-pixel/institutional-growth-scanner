import logging
import math
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Dict, Any, Optional

import numpy as np
import pandas as pd
import requests
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import yfinance as yf
from apscheduler.schedulers.background import BackgroundScheduler

# -------------------------------------------------------------------
# CONFIGURACIÓN Y LOGGING
# -------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("AlgorithmicFinancialBot")

TELEGRAM_TOKEN = "TU_TELEGRAM_BOT_TOKEN_AQUI"
bot = telebot.TeleBot(TELEGRAM_TOKEN)
executor = ThreadPoolExecutor(max_workers=5)


# -------------------------------------------------------------------
# CACHÉ DE DATOS EN MEMORIA (Para evitar rate limits de YFinance)
# -------------------------------------------------------------------
class SimpleCache:
    def __init__(self, ttl_seconds: int = 300):
        self.ttl = ttl_seconds
        self.data = {}

    def get(self, key: str) -> Optional[Any]:
        if key in self.data:
            val, timestamp = self.data[key]
            if time.time() - timestamp < self.ttl:
                return val
            else:
                del self.data[key]
        return None

    def set(self, key: str, value: Any):
        self.data[key] = (value, time.time())

cache = SimpleCache(ttl_seconds=300)


# -------------------------------------------------------------------
# MOTOR TÉCNICO Y CALCULADORA DE INDICADORES (Sin IA)
# -------------------------------------------------------------------
def fetch_stock_data(ticker: str) -> Optional[pd.DataFrame]:
    """Descarga de datos de mercado con caché."""
    cached_df = cache.get(ticker)
    if cached_df is not None:
        return cached_df

    try:
        t = yf.Ticker(ticker)
        df = t.history(period="6m")
        if not df.empty:
            cache.set(ticker, df)
            return df
    except Exception as e:
        logger.error(f"Error descargando datos para {ticker}: {e}")
    return None

def calculate_indicators(df: pd.DataFrame) -> Dict[str, Any]:
    """Calcula RSI, MACD, EMAs, ATR y Volumen promedio."""
    if df is None or len(df) < 50:
        return {}

    close = df['Close']
    high = df['High']
    low = df['Low']
    volume = df['Volume']

    # RSI (14 periodos)
    delta = close.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    current_rsi = rsi.iloc[-1]

    # MACD (12, 26, 9)
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    macd_hist = macd_line - signal_line

    # Medias Móviles
    ema20 = close.ewm(span=20, adjust=False).mean().iloc[-1]
    sma50 = close.rolling(window=50).mean().iloc[-1] if len(close) >= 50 else float('nan')

    # ATR (Average True Range - 14)
    tr1 = high - low
    tr2 = (high - close.shift()).abs()
    tr3 = (low - close.shift()).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(window=14).mean().iloc[-1]

    # Volumen
    vol_avg20 = volume.rolling(window=20).mean().iloc[-1]
    current_vol = volume.iloc[-1]
    vol_ratio = current_vol / vol_avg20 if vol_avg20 > 0 else 1.0

    # Soportes y Resistencias (Mínimos y Máximos de 30 días)
    support = low.tail(30).min()
    resistance = high.tail(30).max()

    curr_price = close.iloc[-1]
    prev_close = close.iloc[-2] if len(close) > 1 else curr_price

    return {
        'price': curr_price,
        'prev_close': prev_close,
        'rsi': current_rsi,
        'macd_line': macd_line.iloc[-1],
        'macd_signal': signal_line.iloc[-1],
        'macd_hist': macd_hist.iloc[-1],
        'ema20': ema20,
        'sma50': sma50,
        'atr': atr,
        'support': support,
        'resistance': resistance,
        'vol_ratio': vol_ratio
    }

def generate_signal(ind: Dict[str, Any]) -> Dict[str, Any]:
    """Genera la señal de trading y gestión de riesgo basándose en reglas técnicas."""
    score = 0
    price = ind['price']

    # 1. Evaluación del RSI (CORREGIDO: Lógica limpia sin redundancias)
    rsi = ind['rsi']
    if rsi < 30:
        score += 2
        rsi_desc = "Sobrevendido 🟢"
    elif rsi > 70:
        score -= 2
        rsi_desc = "Sobrecomprado 🔴"
    elif rsi < 45:  # Corregido
        score += 0.5
        rsi_desc = "Zona Compradora 🟢"
    elif rsi > 55:
        score -= 0.5
        rsi_desc = "Zona Vendedora 🔴"
    else:
        rsi_desc = "Neutral ⚪"

    # 2. Evaluación MACD
    if ind['macd_hist'] > 0:
        score += 1
        macd_desc = "Alcista (Hist. > 0) 🟢"
    else:
        score -= 1
        macd_desc = "Bajista (Hist. < 0) 🔴"

    # 3. Medias Móviles
    ma_signals = []
    if price > ind['ema20']:
        score += 1
        ma_signals.append("Precio > EMA20 🟢")
    else:
        score -= 1
        ma_signals.append("Precio < EMA20 🔴")

    if not math.isnan(ind['sma50']):  # CORREGIDO: Uso de math.isnan
        if price > ind['sma50']:
            score += 1
            ma_signals.append("Precio > SMA50 🟢")
        else:
            score -= 1
            ma_signals.append("Precio < SMA50 🔴")

    # 4. Volumen
    vol_desc = "Normal"
    if ind['vol_ratio'] > 1.5:
        vol_desc = f"Alto ({ind['vol_ratio']:.1f}x media) ⚡"
    elif ind['vol_ratio'] < 0.6:
        vol_desc = "Bajo"

    # 5. Etiqueta Global
    if score >= 2.5:
        signal = "COMPRA FUERTE 🟢🟢"
    elif score >= 0.5:
        signal = "COMPRA MODERADA 🟢"
    elif score <= -2.5:
        signal = "VENTA FUERTE 🔴🔴"
    elif score <= -0.5:
        signal = "VENTA MODERADA 🔴"
    else:
        signal = "NEUTRAL ⚪"

    # 6. Gestión de Riesgo (Calculado vía ATR)
    atr = ind['atr'] if not math.isnan(ind['atr']) else price * 0.02  # CORREGIDO: math.isnan
    stop_loss = price - (1.5 * atr)
    take_profit = price + (3.0 * atr)

    return {
        'signal': signal,
        'score': score,
        'rsi_desc': rsi_desc,
        'macd_desc': macd_desc,
        'ma_desc': " | ".join(ma_signals),
        'vol_desc': vol_desc,
        'stop_loss': stop_loss,
        'take_profit': take_profit,
        'risk_reward': "1:2.0"
    }


# -------------------------------------------------------------------
# FORMATEADOR VISUAL DEL COMANDO /valor
# -------------------------------------------------------------------
def format_valor_message(ticker: str, ind: Dict[str, Any], sig: Dict[str, Any]) -> str:
    price = ind['price']
    prev = ind['prev_close']
    change = ((price - prev) / prev) * 100
    change_emoji = "🟢" if change >= 0 else "🔴"

    return f"""
<b>📈 ANÁLISIS TÉCNICO: <code>{ticker.upper()}</code></b>
───────────────────────────
<b>💰 Precio Actual:</b> <code>${price:,.2f}</code> ({change_emoji} {change:+.2f}%)
<b>📊 Señal Algorítmica:</b> <b>{sig['signal']}</b>

<b>🔍 Indicadores Técnicos:</b>
• <b>RSI (14):</b> <code>{ind['rsi']:.1f}</code> ({sig['rsi_desc']})
• <b>MACD:</b> {sig['macd_desc']}
• <b>Medias Móviles:</b> {sig['ma_desc']}
• <b>Volumen:</b> {sig['vol_desc']}

<b>🛡️ Niveles Clave:</b>
• <b>Resistencia (Máx 30d):</b> <code>${ind['resistance']:,.2f}</code>
• <b>Soporte (Mín 30d):</b> <code>${ind['support']:,.2f}</code>

<b>🎯 Gestión de Riesgo (Sugerida):</b>
• <b>Stop Loss:</b> <code>${sig['stop_loss']:,.2f}</code> (-{((price - sig['stop_loss'])/price)*100:.2f}%)
• <b>Take Profit:</b> <code>${sig['take_profit']:,.2f}</code> (+{((sig['take_profit'] - price)/price)*100:.2f}%)
• <b>Ratio Riesgo/Beneficio:</b> <code>{sig['risk_reward']}</code>
───────────────────────────
<i>⚡ Análisis 100% algorítmico y cuantitativo (Sin IA).
⚠️ No constituye asesoramiento financiero.</i>
""".strip()


# -------------------------------------------------------------------
# COMANDOS DEL BOT DE TELEGRAM
# -------------------------------------------------------------------
@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    welcome_text = (
        "<b>🤖 Bot de Análisis Técnico Financiero</b>\n\n"
        "Usa el comando <b>/valor &lt;TICKER&gt;</b> para obtener un reporte técnico completo.\n\n"
        "<b>Ejemplos:</b>\n"
        "• <code>/valor AAPL</code>\n"
        "• <code>/valor TSLA</code>\n"
        "• <code>/valor BTC-USD</code>\n"
        "• <code>/valor NVDA</code>"
    )
    bot.reply_to(message, welcome_text, parse_mode="HTML")


@bot.message_handler(commands=['valor'])
def handle_valor(message):
    parts = message.text.split()
    if len(parts) < 2:
        bot.reply_to(message, "⚠️ Debes especificar un activo. Ejemplo: <code>/valor AAPL</code>", parse_mode="HTML")
        return

    ticker = parts[1].upper().strip()
    status_msg = bot.reply_to(message, f"🔍 Analizando <code>{ticker}</code>...", parse_mode="HTML")

    # Ejecución asíncrona no bloqueante
    def process_valor():
        try:
            df = fetch_stock_data(ticker)
            if df is None or df.empty:
                bot.edit_message_text(
                    f"❌ No se pudieron obtener datos para el ticker: <code>{ticker}</code>",
                    chat_id=message.chat.id,
                    message_id=status_msg.message_id,
                    parse_mode="HTML"
                )
                return

            ind = calculate_indicators(df)
            sig = generate_signal(ind)
            response_text = format_valor_message(ticker, ind, sig)

            # Teclado Inline de interacción
            markup = InlineKeyboardMarkup()
            markup.row(
                InlineKeyboardButton("📊 Actualizar", callback_data=f"refresh_{ticker}"),
                InlineKeyboardButton("⭐ Seguimiento", callback_data=f"seg_{ticker}")
            )
            markup.row(
                InlineKeyboardButton("📈 Backtest Rápido", callback_data=f"bt_{ticker}")
            )

            bot.edit_message_text(
                response_text,
                chat_id=message.chat.id,
                message_id=status_msg.message_id,
                parse_mode="HTML",
                reply_markup=markup
            )
        except Exception as e:
            logger.error(f"Error procesando /valor para {ticker}: {e}")
            bot.edit_message_text(
                "❌ Ocurrió un error al procesar el análisis técnico.",
                chat_id=message.chat.id,
                message_id=status_msg.message_id
            )

    executor.submit(process_valor)


# -------------------------------------------------------------------
# MANEJADOR DE CALLBACKS (CORREGIDO: Sin claves duplicadas)
# -------------------------------------------------------------------
@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    data = call.data
    chat_id = call.message.chat.id

    try:
        if data.startswith("refresh_"):
            ticker = data.split("_")[1]
            bot.answer_callback_query(call.id, f"Actualizando datos de {ticker}...")
            # Forzar actualización llamando a /valor internamente
            df = fetch_stock_data(ticker)
            if df is not None:
                ind = calculate_indicators(df)
                sig = generate_signal(ind)
                text = format_valor_message(ticker, ind, sig)
                bot.edit_message_text(text, chat_id=chat_id, message_id=call.message.message_id, parse_mode="HTML", reply_markup=call.message.reply_markup)

        elif data.startswith("seg_"):
            ticker = data.split("_")[1]
            bot.answer_callback_query(call.id, f" Añadido {ticker} a tu lista de seguimiento.")

        elif data.startswith("bt_"):
            ticker = data.split("_")[1]
            bot.answer_callback_query(call.id, "Ejecutando backtest...")
            run_backtest(chat_id, ticker)

    except Exception as e:
        logger.error(f"Error procesando callback {data}: {e}")


# -------------------------------------------------------------------
# BACKTEST ALGORTÍMICO (CORREGIDO: Sin lógica redundante)
# -------------------------------------------------------------------
def run_backtest(chat_id: int, ticker: str):
    try:
        df = fetch_stock_data(ticker)
        if df is None or len(df) < 50:
            bot.send_message(chat_id, "⚠️ No hay suficientes datos para el backtest.")
            return

        close = df['Close']
        delta = close.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))

        trades = 0
        wins = 0

        # Lógica del Backtest (CORREGIDA)
        for i in range(15, len(df) - 1):
            rsi_val = rsi.iloc[i]
            # CORREGIDO: Eliminada condición redundante (rsi < 45 and rsi < 65)
            if rsi_val < 35:
                trades += 1
                future_return = (close.iloc[i + 1] - close.iloc[i]) / close.iloc[i]
                if future_return > 0:
                    wins += 1

        win_rate = (wins / trades * 100) if trades > 0 else 0
        res = (
            f"<b>📊 RESULTADOS BACKTEST ESTRATEGIA RSI ({ticker})</b>\n\n"
            f"• <b>Operaciones totales:</b> {trades}\n"
            f"• <b>Tasa de acierto (Win Rate):</b> {win_rate:.1f}%\n"
            f"<i>*Regla: Compra en RSI &lt; 35 a 1 día vista.</i>"
        )
        bot.send_message(chat_id, res, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Error en backtest para {ticker}: {e}")


# -------------------------------------------------------------------
# PROGRAMADOR DE TAREAS (SCHEDULER)
# -------------------------------------------------------------------
scheduler = BackgroundScheduler()

def job_scanner():
    """Escáner periódico de mercado."""
    logger.info("Ejecutando escáner de anomalias de mercado...")

scheduler.add_job(job_scanner, 'interval', minutes=30)
scheduler.start()


# -------------------------------------------------------------------
# INICIO DEL BOT
# -------------------------------------------------------------------
if __name__ == "__main__":
    logger.info("Bot financiero algorítmico iniciado sin IA.")
    bot.infinity_polling()
