import os
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
BOT_TOKEN = "8753909204:AAHH9FoRc3HF7e-R96OPqwpMIB8e2Hl7_M4"

SEARCH_QUERIES = [
    "205/55 R16",
    "195/65 R15",
    "205/65 R16",
    "185/65 R15"
]

CHECK_INTERVAL = 60  # Проверка каждые 60 секунд
USERS_FILE = "subscribers.json"
SEEN_ADS_FILE = "seen_ads.json"

bot = telebot.TeleBot(BOT_TOKEN)

# --- 1. ВЕБ-СЕРВЕР ДЛЯ RENDER ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Telegram Bot is active!")

    def log_message(self, format, *args):
        return

def start_health_server():
    port = int(os.environ.get("PORT", 10000))
    try:
        server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
        logging.info(f"Health check HTTP server started on port {port}")
        server.serve_forever()
    except Exception as e:
        logging.error(f"HTTP Server Error: {e}")

# --- 2. ХРАНИЛИЩЕ ДАННЫХ ---
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

# --- 3. ПАРСИНГ KUFAR ---
def fetch_kufar_ads(query):
    url = "https://cre-api.kufar.by/ads-search/v1/engine/v1/search/rendered-paginated"
    params = {
        "cat": "2010",      # Категория: Шины
        "query": query,     # Поисковый запрос
        "lang": "ru",
        "size": "30",
        "cmp": "0",         # Только частные лица
        "rgn": "7",         # Минск
        "sort": "lst.d"     # Сначала новые
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

        for ad in ads:
            ad_id = str(ad.get("ad_id"))
            
            # 1. Проверка: Только частные лица (не компании)
            if ad.get("company_ad", False):
                continue

            title = ad.get("subject", "Шины")
            
            # Собираем текстовые параметры
            params_list = ad.get("ad_parameters", [])
            all_text = title.lower()
            for p in params_list:
                all_text += " " + str(p.get("pl", "")).lower()
                all_text += " " + str(p.get("vl", "")).lower()

            # 2. Фильтр: Б/У (Исключаем только новое)
            if "новое" in all_text or "нов." in all_text:
                continue

            # 3. Фильтр: Зимние шины (Исключаем строго летние)
            if "летн" in all_text and "зим" not in all_text:
                continue

            # Цена
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

# --- 4. ФОНОВЫЙ СКАНИРОВАНИЕ ---
def check_kufar_loop():
    logging.info("Сканер Куфара запущен...")
    is_first_run = len(seen_ads) == 0

    while True:
        try:
            for query in SEARCH_QUERIES:
                ads = fetch_kufar_ads(query)
                for ad in ads:
                    ad_id = ad["id"]
                    if ad_id not in seen_ads:
                        seen_ads.add(ad_id)
                        save_data(SEEN_ADS_FILE, list(seen_ads))
                        
                        # Не отправляем уведомления при самом первом запуске базы
                        if not is_first_run and subscribers:
                            message_text = (
                                f"❄️ **Новое объявление в Минске [{ad['query']}]**\n\n"
                                f"📌 **{ad['title']}**\n"
                                f"💰 **Цена:** {ad['price']}\n"
                                f"📍 **Город:** Минск\n"
                                f"👤 **Продавец:** Частное лицо (б/у)\n\n"
                                f"🔗 [Открыть на Kufar]({ad['link']})"
                            )
                            
                            for user_id in list(subscribers):
                                try:
                                    bot.send_message(user_id, message_text, parse_mode="Markdown")
                                except Exception as err:
                                    logging.error(f"Не удалось отправить {user_id}: {err}")
                
                time.sleep(2)
            
            is_first_run = False

        except Exception as e:
            logging.error(f"Ошибка сканера: {e}")
            
        time.sleep(CHECK_INTERVAL)

# --- 5. КОМАНДЫ БОТА ---
@bot.message_handler(commands=['start'])
def send_welcome(message):
    user_id = message.chat.id
    subscribers.add(user_id)
    save_data(USERS_FILE, list(subscribers))
    
    queries_str = "\n".join([f"• `{q}`" for q in SEARCH_QUERIES])
    text = (
        f"👋 Здравствуйте, {message.from_user.first_name}!\n\n"
        f"Вы успешно **подписались** на уведомления о б/у зимних шинах в **г. Минске**.\n\n"
        f"🔍 **Отслеживаемые размеры:**\n{queries_str}\n\n"
        f"Бот проверяет Kufar каждые 60 секунд!"
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

# --- 6. ЗАПУСК ---
if __name__ == "__main__":
    threading.Thread(target=start_health_server, daemon=True).start()
    import asyncio
from telegram import Bot
import time

async def clear_webhook():
    bot = Bot(token="8753909204:AAHH9FoRc3HF7e-R96OPqwpMIB8e2Hl7_M4Н")  # Вставьте ваш токен
    await bot.delete_webhook(drop_pending_updates=True)
    print("Webhook удалён, конфликт устранён")

if __name__ == '__main__':
    # Сброс вебхука перед запуском
    asyncio.run(clear_webhook())

    # Запуск в бесконечном цикле с автоперезапуском
    while True:
        try:
            threading.Thread(target=check_kufar_loop, daemon=True).start()
            logging.info("Бот запущен!")
            bot.infinity_polling(skip_pending=True, drop_pending_updates=True)
        except Exception as e:
            logging.error(f"Ошибка: {e}. Перезапуск через 10 секунд...")
            time.sleep(10)

