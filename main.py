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
BOT_TOKEN = "8753909204:AAGNuySGiuvSO3IUH0NkapggdkoK-Pkj5D8"

# Нужные размеры шин (ширина, профиль, диаметр)
TARGET_SIZES = [
    {"w": "205", "p": "55", "d": "16"},
    {"w": "195", "p": "65", "d": "15"},
    {"w": "205", "p": "65", "d": "16"},
    {"w": "185", "p": "65", "d": "15"}
]

CHECK_INTERVAL = 60  # Проверка каждые 60 секунд
USERS_FILE = "subscribers.json"
SEEN_ADS_FILE = "seen_ads.json"

bot = telebot.TeleBot(BOT_TOKEN)

# --- 1. ЗАПУСК HTTP-СЕРВЕРА ДЛЯ RENDER ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Telegram Bot is active!")

    def log_message(self, format, *args):
        return

def start_health_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    logging.info(f"HTTP-сервер запущен на порту {port}")
    server.serve_forever()

# --- 2. РАБОТА С ФАЙЛАМИ ДАННЫХ ---
def load_data(filename):
    if os.path.exists(filename):
        try:
            with open(filename, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception as e:
            logging.error(f"Ошибка загрузки {filename}: {e}")
    return set()

def save_data(filename, data_set):
    try:
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(list(data_set), f, ensure_ascii=False)
    except Exception as e:
        logging.error(f"Ошибка сохранения {filename}: {e}")

subscribers = load_data(USERS_FILE)
seen_ads = load_data(SEEN_ADS_FILE)

# --- 3. ТЕЛЕГРАМ БОТ (ПОДПИСКА) ---
@bot.message_handler(commands=['start'])
def start_cmd(message):
    chat_id = message.chat.id
    if chat_id not in subscribers:
        subscribers.add(chat_id)
        save_data(USERS_FILE, subscribers)
        logging.info(f"Новый подписчик: {chat_id}")
    
    sizes_str = ", ".join([f"{s['w']}/{s['p']} R{s['d']}" for s in TARGET_SIZES])
    text = (
        "👋 <b>Привет! Поиск шин запущен!</b>\n\n"
        f"🔍 <b>Отслеживаемые размеры:</b> {sizes_str}\n"
        "📍 <b>Фильтры:</b> г. Минск, Б/У, Частные лица, Зима, До 200 BYN.\n\n"
        "Как только появится новое объявление — я сразу пришлю его сюда!"
    )
    bot.send_message(chat_id, text, parse_mode="HTML")

# --- 4. УМНАЯ ПРОВЕРКА РАЗМЕРА ШИНЫ ---
def matches_target_size(ad):
    title = ad.get("subject", "").lower()
    body = ad.get("body", "").lower()
    full_text = f"{title} {body}"
    
    # Извлекаем параметры из Куфара (галочек)
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
        
        # 1. Проверка по официальным параметрам Куфара
        if params.get("w") == tw and params.get("p") == tp and params.get("d") == td:
            return f"{tw}/{tp} R{td}"
            
        # 2. Проверка по разным вариантам написания в тексте (205/65r16, 205 65 16, 205/65/16)
        pattern = rf"\b{tw}[/ -.\\]+{tp}\b.*?\b(r|р)?{td}\b"
        if re.search(pattern, full_text):
            return f"{tw}/{tp} R{td}"
            
    return None

# --- 5. ПАРСИНГ КУФАРА ---
def fetch_kufar_ads():
    url = "https://cre-api.kufar.by/v1/search/renditions/ad-list"
    params = {
        "cat": "2010",       # Автомобильные шины
        "size": 30,
        "sort": "lst.d",     # Сначала новые
        "cnd": "2",          # Только Б/У
        "cmp": "0",          # Только частники
        "rgn": "7",          # Только Минск
        "prc": "r:0,20000",  # До 200 BYN (20000 копеек)
        "ar_season": "2"     # Только Зимние
    }
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    try:
        res = requests.get(url, params=params, headers=headers, timeout=10)
        if res.status_code == 200:
            return res.json().get("ads", [])
    except Exception as e:
        logging.error(f"Ошибка запроса к Куфару: {e}")
    return []

def check_kufar_loop():
    logging.info("Сканнер Куфара запущен...")
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
                        f"🔔 <b>Найдена шина {matched_size}!</b>\n\n"
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
                            logging.error(f"Ошибка отправки пользователю {chat_id}: {e}")

                    seen_ads.add(ad_id)
                    save_data(SEEN_ADS_FILE, seen_ads)

        except Exception as e:
            logging.error(f"Ошибка в цикле сканирования: {e}")

        time.sleep(CHECK_INTERVAL)

# --- 6. ЗАПУСК ВСЕХ ПОТОКОВ ---
if __name__ == "__main__":
    # Запуск веб-сервера для Render
    threading.Thread(target=start_health_server, daemon=True).start()
    
    # Запуск сканирования Куфара
    threading.Thread(target=check_kufar_loop, daemon=True).start()
    
    # Запуск обработки сообщений Телеграм (кнопка /start)
    logging.info("Телеграм-бот слушает команды...")
    bot.infinity_polling()
