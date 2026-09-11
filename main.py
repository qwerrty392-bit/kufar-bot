import os
import time
import json
import logging
import threading
import requests
from flask import Flask, request, abort
import telebot
from telebot import types

# Настройка логирования
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# ============================================================
# --- ВАШ TELEGRAM ID ---
# ============================================================
MY_TELEGRAM_ID = 545995986  # <-- Ваш ID
# ============================================================

# --- КОНФИГУРАЦИЯ ---
BOT_TOKEN = "8753909204:AAH1Fi8Fj4-cbdxfc34_xyR7nT2J2KUgxJk"
RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL", "https://kufar-bot-vpkb.onrender.com")

SEARCH_QUERIES = [
    "205/55 R16",
    "195/65 R15",
    "205/65 R16",
    "185/65 R15"
]

CHECK_INTERVAL = 300  # 5 минут
USERS_FILE = "subscribers.json"
SEEN_ADS_FILE = "seen_ads.json"
MAX_PRICE_BYN = 200

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

def get_all_subscribers():
    all_subs = {MY_TELEGRAM_ID}
    file_subs = set(load_data(USERS_FILE, []))
    all_subs.update(file_subs)
    return all_subs

# --- 2. ПАРСИНГ KUFAR (С ФИЛЬТРАМИ) ---
def fetch_kufar_ads(query):
    url = "https://cre-api.kufar.by/ads-search/v1/engine/v1/search/rendered-paginated"
    params = {
        "cat": "2010",      # Категория: Шины
        "query": query,     # Поисковый запрос
        "lang": "ru",
        "size": "50",
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
        logging.info(f"По запросу '{query}' найдено {len(ads)} объявлений")

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

            # 2. Фильтр: Только шины (в названии должно быть слово "шина" или "шины")
            if "шин" not in all_text:
                continue

            # 3. Фильтр: Б/У (Исключаем только новое)
            if "новое" in all_text or "нов." in all_text:
                continue

            # 4. Фильтр: Зимние шины (Исключаем строго летние)
            if "летн" in all_text and "зим" not in all_text:
                continue

            # 5. Фильтр: Цена до 200 BYN
            price_byn = ad.get("price_byn", "0")
            try:
                price_int = int(price_byn) // 100
                if price_int > MAX_PRICE_BYN:
                    continue
                price = f"{price_int} BYN"
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

    # Загружаем базу увиденных объявлений
    seen_ads = set(load_data(SEEN_ADS_FILE, []))

    while True:
        subscribers = get_all_subscribers()
        logging.info(f"=== ЦИКЛ: Подписчиков: {len(subscribers)}. Увиденных: {len(seen_ads)} ===")

        try:
            total_sent = 0
            for query in SEARCH_QUERIES:
                ads = fetch_kufar_ads(query)
                for ad in ads:
                    ad_id = ad["id"]
                    if ad_id not in seen_ads:
                        seen_ads.add(ad_id)
                        save_data(SEEN_ADS_FILE, list(seen_ads))
                        
                        logging.info(f"НОВОЕ: {ad['title']} | Цена: {ad['price']}")
                        
                        if subscribers:
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
                                    logging.info(f"-> ✅ УСПЕШНО отправлено {user_id}")
                                    total_sent += 1
                                except Exception as err:
                                    logging.error(f"-> ❌ ОШИБКА отправки {user_id}: {err}")
                
                time.sleep(2)
            
            logging.info(f"=== ЦИКЛ ЗАВЕРШЕН. Отправлено новых: {total_sent} ===")

        except Exception as e:
            logging.error(f"Ошибка сканера: {e}")
            
        time.sleep(CHECK_INTERVAL)

# --- 4. КОМАНДЫ БОТА ---
@bot.message_handler(commands=['start'])
def send_welcome(message):
    user_id = message.chat.id
    subs = set(load_data(USERS_FILE, []))
    subs.add(user_id)
    save_data(USERS_FILE, list(subs))
    logging.info(f"Новый подписчик: {user_id}. Всего в файле: {len(subs)}")
    
    queries_str = "\n".join([f"• `{q}`" for q in SEARCH_QUERIES])
    text = (
        f"👋 Здравствуйте, {message.from_user.first_name}!\n\n"
        f"Вы успешно **подписались** на уведомления о б/у зимних шинах в **г. Минске**.\n\n"
        f"🔍 **Отслеживаемые размеры:**\n{queries_str}\n\n"
        f"💰 **Максимальная цена:** {MAX_PRICE_BYN} BYN\n"
        f"👤 **Только частные лица**\n\n"
        f"Бот проверяет Kufar каждые 5 минут!"
    )
    bot.reply_to(message, text, parse_mode="Markdown")

@bot.message_handler(commands=['stop'])
def stop_subscription(message):
    user_id = message.chat.id
    subs = set(load_data(USERS_FILE, []))
    if user_id in subs:
        subs.remove(user_id)
        save_data(USERS_FILE, list(subs))
        bot.reply_to(message, "❌ Вы отписались от уведомлений.")
    else:
        bot.reply_to(message, "Вы не были подписаны.")

@bot.message_handler(commands=['status'])
def status_info(message):
    subs = get_all_subscribers()
    seen = set(load_data(SEEN_ADS_FILE, []))
    text = (
        f"📊 **Статус бота:**\n"
        f"📍 Регион: г. Минск\n"
        f"👥 Подписчиков: {len(subs)}\n"
        f"📦 Увиденных объявлений: {len(seen)}\n"
        f"💰 Макс. цена: {MAX_PRICE_BYN} BYN\n"
        f"⏱ Проверка каждые: {CHECK_INTERVAL} сек."
    )
    bot.reply_to(message, text, parse_mode="Markdown")

# --- 5. ВЕБХУК ---
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
