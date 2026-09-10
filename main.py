import os
import time
import json
import logging
import threading
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler
from flask import Flask, request, abort
import telebot
from telebot import types

# Настройка логирования
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# --- КОНФИГУРАЦИЯ ---
BOT_TOKEN = "8753909204:AAHH9FoRc3HF7e-R96OPqwpMIB8e2Hl7_M4"

# URL вашего сервиса на Render
RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL", "https://kufar-bot-vpkb.onrender.com")

SEARCH_QUERIES = [
    "205/55 R16",
    "195/65 R15",
    "205/65 R16",
    "185/65 R15"
]

CHECK_INTERVAL = 60  # ВРЕМЕННО: проверка каждую 1 минуту (для теста)
USERS_FILE = "subscribers.json"
SEEN_ADS_FILE = "seen_ads.json"

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

# --- 1. ХРАНИЛИЩЕ ДАННЫХ ---
def load_data(filename, default):
    if os.path.exists(filename):
        try:
            with open(filename, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logging.error(f"Ошибка чтения {filename}: {e}")
    return default

def save_data(filename, data):
    try:
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error(f"Ошибка сохранения {filename}: {e}")

subscribers = set(load_data(USERS_FILE, []))
seen_ads = set(load_data(SEEN_ADS_FILE, []))
# save_data(SEEN_ADS_FILE, [])  # <-- ЗАКОММЕНТИРОВАНО, база НЕ очищается

# --- 2. ПАРСИНГ KUFAR ---
def fetch_kufar_ads(query):
    url = "https://cre-api.kufar.by/ads-search/v1/engine/v1/search/rendered-paginated"
    params = {
        "cat": "2010",
        "query": query,
        "lang": "ru",
        "size": "30",
        "cmp": "1",         # ВРЕМЕННО: все продавцы (не только частные)
        "rgn": "7",
        "sort": "lst.d"
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    found_ads = []
    try:
        response = requests.get(url, params=params, headers=headers, timeout=10)
        if response.status_code != 200:
            logging.warning(f"Ошибка Kufar status={response.status_code} query='{query}'")
            return found_ads

        data = response.json()
        ads = data.get("ads", [])
        logging.info(f"По запросу '{query}' найдено {len(ads)} объявлений")

        for ad in ads:
            ad_id = str(ad.get("ad_id"))
            # ВРЕМЕННО: фильтры отключены полностью
            title = ad.get("subject", "Шины")
            price_byn = ad.get("price_byn", "0")
            try:
                price = f"{int(price_byn) // 100} BYN"
            except:
                price = "Цена не указана"

            ad_link = ad.get("ad_link", f"https://www.kufar.by/item/{ad_id}")

            found_ads.append({
                "id": ad_id,
                "title": title,
                "price": price,
                "link": ad_link,
                "query": query
            })
    except Exception as e:
        logging.error(f"Ошибка при парсинге Куфара ({query}): {e}")

    return found_ads

# --- 3. ФОНОВОЕ СКАНИРОВАНИЕ ---
def check_kufar_loop():
    logging.info("Сканер Куфара запущен...")

    while True:
        try:
            for query in SEARCH_QUERIES:
                ads = fetch_kufar_ads(query)
                for ad in ads:
                    ad_id = ad["id"]
                    if ad_id not in seen_ads:
                        seen_ads.add(ad_id)
                        save_data(SEEN_ADS_FILE, list(seen_ads))
                        
                        if subscribers:
                            message_text = (
                                f"🔔 **Новое объявление в Минске [{ad['query']}]**\n\n"
                                f"📌 **{ad['title']}**\n"
                                f"💰 **Цена:** {ad['price']}\n"
                                f"📍 **Город:** Минск\n\n"
                                f"🔗 [Открыть на Kufar]({ad['link']})"
                            )
                            
                            for user_id in list(subscribers):
                                try:
                                    bot.send_message(user_id, message_text, parse_mode="Markdown")
                                    logging.info(f"Уведомление отправлено {user_id}: {ad['title']}")
                                except Exception as err:
                                    logging.error(f"Не удалось отправить {user_id}: {err}")
                
                time.sleep(2)

        except Exception as e:
            logging.error(f"Ошибка сканера: {e}")
            
        time.sleep(CHECK_INTERVAL)

# --- 4. КОМАНДЫ БОТА ---
@bot.message_handler(commands=['start'])
def send_welcome(message):
    user_id = message.chat.id
    subscribers.add(user_id)
    save_data(USERS_FILE, list(subscribers))
    
    queries_str = "\n".join([f"• `{q}`" for q in SEARCH_QUERIES])
    text = (
        f"👋 Здравствуйте, {message.from_user.first_name}!\n\n"
        f"Вы успешно **подписались** на уведомления.\n\n"
        f"🔍 **Отслеживаемые размеры:**\n{queries_str}\n\n"
        f"Бот проверяет Kufar каждую минуту!"
    )
    bot.reply_to(message, text, parse_mode="Markdown")

@bot.message_handler(commands=['stop'])
def stop_subscription(message):
    user_id = message.chat.id
    if user_id in subscribers:
        subscribers.remove(user_id)
        save_data(USERS_FILE, list(subscribers))
        bot.reply_to(message, "❌ Вы отписались от уведомлений.")
    else:
        bot.reply_to(message, "Вы не были подписаны.")

@bot.message_handler(commands=['status'])
def status_info(message):
    text = (
        f"📊 **Статус бота:**\n"
        f"📍 Регион: г. Минск\n"
        f"👥 Подписчиков: {len(subscribers)}\n"
        f"📦 Обработано объявлений: {len(seen_ads)}\n"
        f"⏱ Проверка каждые: {CHECK_INTERVAL} сек."
    )
    bot.reply_to(message, text, parse_mode="Markdown")

# --- 5. ВЕБХУК ДЛЯ TELEGRAM ---
@app.route(f"/{BOT_TOKEN}", methods=['POST'])
def webhook():
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return '', 200
    else:
        abort(403)

@app.route('/')
def index():
    return "Telegram Bot is active!", 200

# --- 6. ЗАПУСК ---
if __name__ == "__main__":
    try:
        bot.remove_webhook()
        time.sleep(1)
        bot.set_webhook(url=f"{RENDER_EXTERNAL_URL}/{BOT_TOKEN}")
        logging.info(f"Вебхук установлен: {RENDER_EXTERNAL_URL}/{BOT_TOKEN}")
    except Exception as e:
        logging.error(f"Ошибка установки вебхука: {e}")

    threading.Thread(target=check_kufar_loop, daemon=True).start()

    port = int(os.environ.get("PORT", 10000))
    logging.info(f"Flask сервер запущен на порту {port}")
    app.run(host="0.0.0.0", port=port)
