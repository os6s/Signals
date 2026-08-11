"""
بوت تحليل فوركس عند الطلب (تيليجرام)
======================================
ترسل له اسم الزوج (مثلاً: ذهب، يورو دولار، EUR/USD) وهو يرد عليك بـ:
- الاتجاه الحالي (صعود/هبوط)
- سعر دخول مقترح
- وقف خسارة (Stop Loss) مقترح
- هدف ربح (Take Profit) مقترح

⚠️ هذا تحليل فني بسيط (تقاطع متوسطات + مدى التذبذب الأخير) وليس توقعاً
مضموناً ولا نسبة نجاح مربوطة. القرار والتنفيذ مسؤوليتك بالكامل.

مصدر البيانات: Twelve Data (https://twelvedata.com)
"""

import os
import time
import threading
from datetime import datetime

import requests
import pandas as pd
from flask import Flask

# ============ الإعدادات (من متغيرات البيئة على Render) ============
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TWELVEDATA_API_KEY = os.environ.get("TWELVEDATA_API_KEY", "")
INTERVAL = os.environ.get("INTERVAL", "15min")
FAST_MA = int(os.environ.get("FAST_MA", "20"))
SLOW_MA = int(os.environ.get("SLOW_MA", "50"))

# خريطة الأسماء العربية/الشائعة لصيغة الرمز اللي يفهمها Twelve Data
SYMBOL_MAP = {
    "ذهب": "XAU/USD", "gold": "XAU/USD", "xauusd": "XAU/USD",
    "فضة": "XAG/USD", "silver": "XAG/USD",
    "يورو دولار": "EUR/USD", "يورو": "EUR/USD", "eurusd": "EUR/USD",
    "باوند دولار": "GBP/USD", "باوند": "GBP/USD", "جنيه استرليني": "GBP/USD", "gbpusd": "GBP/USD",
    "دولار ين": "USD/JPY", "ين ياباني": "USD/JPY", "usdjpy": "USD/JPY",
    "دولار كندي": "USD/CAD", "usdcad": "USD/CAD",
    "دولار فرنك": "USD/CHF", "فرنك سويسري": "USD/CHF", "usdchf": "USD/CHF",
    "استرالي دولار": "AUD/USD", "دولار استرالي": "AUD/USD", "audusd": "AUD/USD",
    "نيوزلندي دولار": "NZD/USD", "nzdusd": "NZD/USD",
    "بيتكوين": "BTC/USD", "bitcoin": "BTC/USD", "btcusd": "BTC/USD",
}

app = Flask(__name__)


@app.route("/")
def home():
    return {"status": "running", "info": "Telegram forex analysis bot is polling for messages."}


def resolve_symbol(text):
    """يحاول يفهم أي رمز يقصده المستخدم من رسالته"""
    t = text.strip().lower()
    if t in SYMBOL_MAP:
        return SYMBOL_MAP[t]
    # لو كتب مباشرة بصيغة EUR/USD أو EURUSD
    cleaned = t.replace(" ", "").replace("-", "/").upper()
    if "/" in cleaned:
        return cleaned
    if len(cleaned) == 6 and cleaned.isalpha():
        return f"{cleaned[:3]}/{cleaned[3:]}"
    return None


def get_data(symbol):
    url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol": symbol,
        "interval": INTERVAL,
        "outputsize": max(SLOW_MA + 20, 100),
        "apikey": TWELVEDATA_API_KEY,
        "order": "ASC",
    }
    r = requests.get(url, params=params, timeout=15)
    data = r.json()
    if "values" not in data:
        return None, data.get("message", "تعذر جلب البيانات لهذا الرمز.")
    df = pd.DataFrame(data["values"])
    df["close"] = df["close"].astype(float)
    df["high"] = df["high"].astype(float)
    df["low"] = df["low"].astype(float)
    df["datetime"] = pd.to_datetime(df["datetime"])
    df = df.sort_values("datetime").reset_index(drop=True)
    return df, None


def analyze(df):
    """يحسب الاتجاه ومستويات دخول/وقف/هدف مقترحة"""
    df["MA_fast"] = df["close"].rolling(window=FAST_MA).mean()
    df["MA_slow"] = df["close"].rolling(window=SLOW_MA).mean()

    curr_fast, curr_slow = df["MA_fast"].iloc[-1], df["MA_slow"].iloc[-1]
    price = df["close"].iloc[-1]

    trend = "صاعد 📈" if curr_fast > curr_slow else "هابط 📉"
    direction = "BUY" if curr_fast > curr_slow else "SELL"

    # مدى التذبذب الأخير (متوسط المدى بين أعلى وأدنى آخر 14 شمعة) لتحديد مسافة الوقف والهدف
    recent = df.tail(14)
    avg_range = (recent["high"] - recent["low"]).mean()

    if direction == "BUY":
        entry = price
        stop_loss = price - (avg_range * 1.5)
        take_profit = price + (avg_range * 2.5)
    else:
        entry = price
        stop_loss = price + (avg_range * 1.5)
        take_profit = price - (avg_range * 2.5)

    return {
        "trend": trend,
        "direction": direction,
        "entry": entry,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
    }


def format_reply(symbol, result):
    emoji = "🟢" if result["direction"] == "BUY" else "🔴"
    return (
        f"{emoji} تحليل {symbol}\n\n"
        f"الاتجاه: {result['trend']}\n"
        f"دخول مقترح: {result['entry']:.5f}\n"
        f"وقف خسارة: {result['stop_loss']:.5f}\n"
        f"هدف ربح: {result['take_profit']:.5f}\n\n"
        f"⚠️ تحليل فني آلي بسيط، وليس توصية مضمونة. القرار لك."
    )


def send_message(chat_id, text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, data={"chat_id": chat_id, "text": text}, timeout=10)
    except Exception as e:
        print("خطأ إرسال رسالة:", e)


def handle_message(chat_id, text):
    symbol = resolve_symbol(text)
    if not symbol:
        send_message(
            chat_id,
            "ما قدرت أتعرف على الرمز 🤔\n"
            "جرب مثلاً: ذهب، يورو دولار، باوند دولار، أو اكتب الرمز مباشرة زي EUR/USD",
        )
        return

    send_message(chat_id, f"⏳ جاري تحليل {symbol} ...")
    df, error = get_data(symbol)
    if df is None or len(df) < SLOW_MA + 1:
        send_message(chat_id, f"❌ تعذر التحليل: {error or 'بيانات غير كافية'}")
        return

    result = analyze(df)
    send_message(chat_id, format_reply(symbol, result))


def polling_loop():
    print("بدء الاستماع لرسائل تيليجرام...")
    offset = None
    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
            params = {"timeout": 30}
            if offset:
                params["offset"] = offset
            r = requests.get(url, params=params, timeout=35)
            data = r.json()

            for update in data.get("result", []):
                offset = update["update_id"] + 1
                message = update.get("message", {})
                text = message.get("text", "")
                chat_id = message.get("chat", {}).get("id")
                if text and chat_id:
                    print(f"[{datetime.now()}] رسالة من {chat_id}: {text}")
                    if text.strip() == "/start":
                        send_message(
                            chat_id,
                            "أهلاً 👋\nابعث لي اسم زوج (مثل: ذهب، يورو دولار، EUR/USD)"
                            " وبعطيك تحليل دخول/وقف/هدف.",
                        )
                    else:
                        handle_message(chat_id, text)
        except Exception as e:
            print("خطأ بحلقة الاستماع:", e)
            time.sleep(5)


threading.Thread(target=polling_loop, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
