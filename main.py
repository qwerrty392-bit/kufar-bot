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
BOT_TOKEN = "8753909204:AAF_6L3vePZsAYVmY0Ie1LTVdik7nq85Wpw"

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
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    logging.info(f"Веб-сервер запущен на порту {port}")
    server.serve_forever()

# --- 2. РАБОТА С ФАЙЛАМИ ХРАНЕНИЯ ---
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

# --- 3. ПАРСИНГ KUFAR (ТОЛЬКО МИНСК) ---
def fetch_kufar_ads(query):
    """Запрос объявлений к официальному API Куфар по Минску"""
    url = "https://cre-api.kufar.by/ads-search/v1/engine/v1/search/rendered-paginated"
    params = {
        "cat": "2010",      # Категория: Шины
        "query": query,     # Поисковый запрос
        "lang": "ru",
        "size": "30",       # Размер выборки
        "cmp": "0",         # Только частные лица (cmp=0)
        "rgn": "7"          # 👈 ТОЛЬКО МИНСК
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    found_ads = []
    
    try:
        response = requests.get(url, params=params, headers=headers, timeout=10)
        if response.status_code != 200:
            logging.warning(f"Ошибка запроса Kufar ({response.status_code}) для '{query}'")
            return found_ads

        data = response.json()
        ads = data.get("ads", [])

        for ad in ads:
            ad_id = str(ad.get("ad_id"))
            
            # 1. Проверка: Только частные лица
            if ad.get("company_ad", False):
                continue

            # Извлечение параметров объявления (Состояние, Сезонность)
            params_list = ad.get("ad_parameters", [])
            param_dict = {}
            for p in params_list:
                pl = p.get("pl", "").lower()
                vl = str(p.get("vl", "")).lower()
                p_id = p.get("p", "")
                param_dict[p_id] = vl
                param_dict[pl] = vl

            # 2. Фильтр: Б/У (Исключаем 'новое')
            condition_val = str(param_dict.get("condition", "")) + str(param_dict.get("состояние", ""))
            if "нов" in condition_val:
                continue

            # 3. Фильтр: Зимние шины
            season_val = str(param_dict.get("tyre_type", "")) + str(param_dict.get("сезонность", "")) + ad.get("subject", "").lower()
            if "зим" not in season_val:
                continue

            # Формирование цены и ссылки
            price_byn = ad.get("price_byn", "0")
            try:
                price = f"{int(price_byn) // 100} BYN"
            except:
                price = "Цена не указана"

            ad_link = ad.get("ad_link", f"https://www.kufar.by/item/{ad_id}")
            title = ad.get("subject", "Шины")

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

# --- 4. ФОНОВЫЙ МОНИТОРИНГ ---
def check_kufar_loop():
    logging.info("Фоновый мониторинг объявлений запущен...")
    while True:
        try:
            if subscribers:  # Проверяем только если есть подписчики
                for query in SEARCH_QUERIES:
                    ads = fetch_kufar_ads(query)
                    for ad in ads:
                        ad_id = ad["id"]
                        if ad_id not in seen_ads:
                            seen_ads.add(ad_id)
                            save_data(SEEN_ADS_FILE, list(seen_ads))
                            
                            # Сообщение для отправки
                            message_text = (
                                f"❄️ **Новое объявление в Минске [{ad['query']}]**\n\n"
                                f"📌 **{ad['title']}**\n"
                                f"💰 **Цена:** {ad['price']}\n"
                                f"📍 **Город:** Минск\n"
                                f"👤 **Продавец:** Частное лицо (б/у)\n\n"
                                f"🔗 [Открыть на Kufar]({ad['link']})"
                            )
                            
                            # Рассылка всем подписчикам
                            for user_id in list(subscribers):
                                try:
                                    bot.send_message(user_id, message_text, parse_mode="Markdown")
                                except Exception as err:
                                    logging.error(f"Не удалось отправить пользователю {user_id}: {err}")
                    
                    time.sleep(2)
        except Exception as e:
            logging.error(f"Ошибка в цикле мониторинга: {e}")
            
        time.sleep(CHECK_INTERVAL)

# --- 5. КОМАНДЫ ТЕЛЕГРАМ БОТА ---
@bot.message_handler(commands=['start'])
def send_welcome(message):
    user_id = message.chat.id
    subscribers.add(user_id)
    save_data(USERS_FILE, list(subscribers))
    
    queries_str = "\n".join([f"• `{q}`" for q in SEARCH_QUERIES])
    text = (
        f"👋 Здравствуйте, {message.from_user.first_name}!\n\n"
        f"Вы успешно **подписались** на уведомления о б/у зимних шинах в **г. Минске** от частных лиц.\n\n"
        f"🔍 **Отслеживаемые размеры:**\n{queries_str}\n\n"
        f"Бот проверяет Kufar каждые 60 секунд и сразу пришлет новые варианты!"
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

# --- 6. ТОЧКА ВХОДА ---
if __name__ == "__main__":
    # Запускаем HTTP веб-сервер для Render в отдельном потоке
    threading.Thread(target=start_health_server, daemon=True).start()
    
    # Запускаем цикл проверки объявлений Kufar в отдельном потоке
    threading.Thread(target=check_kufar_loop, daemon=True).start()
    
    # Запускаем Telegram бота
    logging.info("Бот успешно запущен!")
    bot.infinity_polling(skip_pending=True)
