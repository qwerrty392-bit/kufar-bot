# -*- coding: utf-8 -*-
import os
import re
import time
import json
import logging
import threading
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler
import telebot

# Настройка логирования
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# --- КОНФИГУРАЦИЯ ---
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8753909204:AAFO5nIkWctl0pT07vyBczV8l4HyNE1Cst0")

# Размеры шин для отслеживания
TARGET_SIZES = [
    {"w": "205", "p": "55", "d": "16"},
    {"w": "195", "p": "65", "d": "15"},
    {"w": "205", "p": "65", "d": "16"},
    {"w": "185", "p": "65", "d": "15"}
]

CHECK_INTERVAL = 60  # Проверка каждую минуту
USERS_FILE = "subscribers.json"
SEEN_ADS_FILE = "seen_ads.json"

bot = telebot.TeleBot(BOT_TOKEN)

# --- 1. ВЕБ-СЕРВЕР ДЛЯ RENDER (HEALTH CHECK) ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Kufar Bot is Running!")

    def log_message(self, format, *args):
        return

def start_health_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    logging.info(f"Health check HTTP server started on port {port}")
    server.serve_forever()

# --- 2. РАБОТА С ФАЙЛАМИ ДАННЫХ ---
def load_data(filename):
    if os.path.exists(filename):
        try:
            with open(filename, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception as e:
            logging.error(f"Error loading {filename}: {e}")
    return set()

def save_data(filename, data_set):
    try:
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(list(data_set), f, ensure_ascii=False)
    except Exception as e:
        logging.error(f"Error saving {filename}: {e}")

subscribers = load_data(USERS_FILE)
seen_ads = load_data(SEEN_ADS_FILE)

# --- 3. ОБРАБОТЧИК /START ---
@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    try:
        chat_id = message.chat.id
        logging.info(f"User {chat_id} triggered /start")
        
        if chat_id not in subscribers:
            subscribers.add(chat_id)
            save_data(USERS_FILE, subscribers)
            logging.info(f"Saved subscriber {chat_id}")
            
        sizes_str = ", ".join([f"{s['w']}/{s['p']} R{s['d']}" for s in TARGET_SIZES])
        welcome_text = (
            "👋 <b>Привет! Я твой бот-охотник за шинами!</b>\n\n"
            "🎯 <b>Что я отслеживаю на Куфаре:</b>\n"
            f"• Размеры: <code>{sizes_str}</code>\n"
            "• Город: <b>Минск</b> (только частники)\n"
            "• Сезон: <b>Зима</b> (только Б/У)\n"
            "• Бюджет: <b>до 200 BYN</b>\n\n"
            "Как только появится горячий вариант — я сразу пришлю его сюда с фото и ценой! ⚡"
        )
        bot.send_message(chat_id, welcome_text, parse_mode="HTML")
    except Exception as e:
        logging.error(f"Error in /start handler: {e}")

# --- 4. ПРОВЕРКА РАЗМЕРОВ ШИНЫ ---
def matches_target_size(ad):
    title = ad.get("subject", "").lower()
    body = ad.get("body", "").lower()
    full_text = f"{title} {body}"
    
    params = {}
    for p in ad.get("ad_parameters", []):
        p_name = p.get("p", "")
        p_val = str(p.get("v", "")).lower()
        p_label = str(p.get("vl", "")).lower()
        
        if "width" in p_name or "ширина" in str(p.get("pl", "")).lower():
            params["w"] = p_val or p_label
        elif "profile" in p_name or "профиль" in str(p.get("pl", "")).lower():
            params["p"] = p_val or p_label
        elif "rim" in p_name or "диаметр" in str(p.get("pl", "")).lower():
            params["d"] = p_val.replace("r", "").replace("р", "") or p_label.replace("r", "").replace("р", "")

    for target in TARGET_SIZES:
        tw, tp, td = target["w"], target["p"], target["d"]
        
        # 1. По параметрам Куфара
        if params.get("w") == tw and params.get("p") == tp and params.get("d") == td:
            return f"{tw}/{tp} R{td}"
            
        # 2. По тексту (205/55 R16, 205/55r16, 205 55 16, 205/55/16)
        pattern = rf"\b{tw}[/ -.\\]+{tp}\b.*?\b(r|р)?{td}\b"
        if re.search(pattern, full_text):
            return f"{tw}/{tp} R{td}"
            
    return None

# --- 5. СКАНИРОВАНИЕ КУФАРА ---
def fetch_kufar_ads():
    url = "https://cre-api.kufar.by/v1/search/renditions/ad-list"
    params = {
        "cat": "2010",       # Автомобильные шины
        "size": 30,
        "sort": "lst.d",     # Сначала самые новые
        "cnd": "2",          # Только Б/У
        "cmp": "0",          # Только частные лица
        "rgn": "7",          # Только Минск
        "prc": "r:0,20000",  # До 200 BYN
        "ar_season": "2"     # Только зимние
    }
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    try:
        res = requests.get(url, params=params, headers=headers, timeout=10)
        if res.status_code == 200:
            return res.json().get("ads", [])
    except Exception as e:
        logging.error(f"Kufar request error: {e}")
    return []

def check_kufar_loop():
    logging.info("Kufar scanner loop started...")
    while True:
        try:
            ads = fetch_kufar_ads()
            for ad in reversed(ads):
                ad_id = str(ad.get("ad_id"))
                if not ad_id or ad_id in seen_ads:
                    continue

                matched_size = matches_target_size(ad)
                if matched_size:
                    title = ad.get("subject", "Без названия")
                    price_raw = ad.get("price_byn")
                    if price_raw and str(price_raw).isdigit():
                        val = int(price_raw) / 100
                        price_str = f"{int(val)} BYN" if val.is_integer() else f"{val:.2f} BYN"
                    else:
                        price_str = "Договорная"

                    link = ad.get("ad_link", "https://www.kufar.by")
                    images = ad.get("images", [])
                    photo_url = f"https://yams.kufar.by/v1/transform/v1/m/id/{images[0]['path']}?rule=gallery" if images else None

                    msg_text = (
                        f"❄️ <b>Найдена зимняя шина {matched_size}!</b>\n\n"
                        f"📌 <b>{title}</b>\n"
                        f"💰 <b>Цена:</b> {price_str}\n\n"
                        f"🔗 <a href='{link}'>Открыть на Kufar</a>"
                    )

                    # Рассылка всем подписчикам
                    for chat_id in list(subscribers):
                        try:
                            if photo_url:
                                bot.send_photo(chat_id, photo_url, caption=msg_text, parse_mode="HTML")
                            else:
                                bot.send_message(chat_id, msg_text, parse_mode="HTML")
                        except Exception as e:
                            logging.error(f"Send error to {chat_id}: {e}")

                    seen_ads.add(ad_id)
                    save_data(SEEN_ADS_FILE, seen_ads)

        except Exception as e:
            logging.error(f"Error in scan loop: {e}")

        time.sleep(CHECK_INTERVAL)

# --- 6. ЗАПУСК ВСЕХ СЛУЖБ ---
if __name__ == "__main__":
    # 1. Запуск веб-сервера для Render
    threading.Thread(target=start_health_server, daemon=True).start()
    
    # 2. Запуск фонового сканирования Куфара
    threading.Thread(target=check_kufar_loop, daemon=True).start()
    
    # 3. Безопасный запуск поллинга Телеграм с авто-переподключением
    while True:
        try:
            try:
                bot.remove_webhook()
            except Exception:
                pass
            logging.info("Starting Telegram bot polling...")
            bot.infinity_polling(timeout=20, long_polling_timeout=10, skip_pending=True)
        except Exception as err:
            logging.error(f"Polling crashed: {err}. Restarting in 5s...")
            time.sleep(5)

