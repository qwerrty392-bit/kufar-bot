import os
import time
import json
import requests
from threading import Thread
from flask import Flask, request

# ================= ТОЛЬКО ТОКЕН БОТА =================
TELEGRAM_BOT_TOKEN = "8970830553:AAHe18Q2bf0cIAkVRw9YFgpzoR_3e_PecwE"

# Список моделей для поиска (добавляй новые через запятую в кавычках)
SEARCH_QUERIES = [
    "205/55 R16",
    "195/65 R15",
    "185/65/ R15",
    "205/65 R16"
]

# Интервал проверки (в секундах)
CHECK_INTERVAL = 60
USERS_FILE = "users.json"
SEEN_ADS_FILE = "seen_ads.json"

# ================= Flask приложение =================
app = Flask(__name__)

@app.route('/')
def home():
    return "Бот активен и рассылает объявления всем пользователям!"

@app.route('/webhook', methods=['POST'])
def webhook():
    """Принимаем сообщения от Telegram"""
    try:
        data = request.get_json()
        msg = data.get("message", {})
        chat = msg.get("chat", {})
        chat_id = chat.get("id")
        text = msg.get("text", "")

        if chat_id and text == "/start":
            users = load_users()
            if chat_id not in users:
                users.add(chat_id)
                save_users(users)
                print(f"➕ Новый подписчик добавлен: {chat_id}")

                welcome_text = (
                    "👋 <b>Привет! Ты успешно подписался на уведомления!</b>\n\n"
                    f"🚗 Я ищу <b>зимние б/у шины в Минске до 200 BYN</b> по размерам:\n"
                    f"• {', '.join(SEARCH_QUERIES)}\n\n"
                    "Как только появится свежее объявление на Куфаре — я сразу пришлю его сюда!"
                )
                requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", data={
                    "chat_id": chat_id,
                    "text": welcome_text,
                    "parse_mode": "HTML"
                })
            else:
                # Если уже подписан, просто подтверждаем
                requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", data={
                    "chat_id": chat_id,
                    "text": "✅ Вы уже подписаны на уведомления!",
                    "parse_mode": "HTML"
                })
    except Exception as e:
        print(f"Ошибка обработки вебхука: {e}")

    return "OK", 200

# ================= Работа с файлами =================
def load_users():
    if os.path.exists(USERS_FILE):
        try:
            with open(USERS_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except:
            return set()
    return set()

def save_users(users):
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(list(users), f)

def load_seen_ids():
    if os.path.exists(SEEN_ADS_FILE):
        try:
            with open(SEEN_ADS_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except:
            return set()
    return set()

def save_seen_ids(seen_ids):
    with open(SEEN_ADS_FILE, "w", encoding="utf-8") as f:
        json.dump(list(seen_ids), f)

# ================= Рассылка сообщений =================
def broadcast_telegram(title, price, link, photo_url=None):
    users = load_users()
    if not users:
        print("⚠️ Новых объявлений найдено, но пока нет подписчиков (никто не нажал /start).")
        return

    message = (
        f"🔔 <b>Новое б/у объявление (Минск, Зима)</b>\n\n"
        f"📌 <b>{title}</b>\n"
        f"💰 <b>Цена:</b> {price}\n\n"
        f"🔗 <a href='{link}'>Открыть на Kufar</a>"
    )

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/"
    for chat_id in users:
        try:
            if photo_url:
                requests.post(url + "sendPhoto", data={
                    "chat_id": chat_id,
                    "photo": photo_url,
                    "caption": message,
                    "parse_mode": "HTML"
                }, timeout=10)
            else:
                requests.post(url + "sendMessage", data={
                    "chat_id": chat_id,
                    "text": message,
                    "parse_mode": "HTML"
                }, timeout=10)
        except Exception as e:
            print(f"Ошибка отправки пользователю {chat_id}: {e}")

# ================= Парсинг объявлений с Kufar =================
def fetch_ads(query):
    url = "https://cre-api.kufar.by/v1/search/renditions/ad-list"
    params = {
        "query": query,
        "size": 10,
        "sort": "lst.d",
        "cnd": "2",          # Только б/у
        "cmp": "0",          # Только частники
        "rgn": "7",          # Только Минск
        "prc": "r:0,20000",  # До 200 BYN
        "ar_season": "2"     # Зимние шины
    }
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        res = requests.get(url, params=params, headers=headers, timeout=10)
        return res.json().get("ads", []) if res.status_code == 200 else []
    except:
        return []

# ================= Главный цикл проверки Куфара =================
def kufar_scanner():
    seen_ids = load_seen_ids()
    print(f"🚀 Сканнер запущен! Ищем размеры: {', '.join(SEARCH_QUERIES)}")

    while True:
        for query in SEARCH_QUERIES:
            ads = fetch_ads(query)
            for ad in reversed(ads):
                ad_id = str(ad.get("ad_id"))
                if ad_id not in seen_ids:
                    title = ad.get("subject", "")

                    # Исключаем лето
                    if "летн" in title.lower() or "лето" in title.lower():
                        seen_ids.add(ad_id)
                        continue

                    price_raw = ad.get("price_byn")
                    price = f"{int(price_raw)/100:.2f} BYN" if price_raw else "Договорная"
                    link = ad.get("ad_link")
                    img = ad.get("images", [])
                    photo = f"https://yams.kufar.by/v1/transform/v1/m/id/{img[0]['path']}?rule=gallery" if img else None

                    # Отправляем ВСЕМ
                    broadcast_telegram(title, price, link, photo)
                    seen_ids.add(ad_id)
                    save_seen_ids(seen_ids)
        time.sleep(CHECK_INTERVAL)

# ================= Запуск =================
if __name__ == "__main__":
    # 1. Запускаем сканнер в отдельном потоке
    Thread(target=kufar_scanner, daemon=True).start()

    # 2. Запускаем Flask (вебхук)
    app.run(host='0.0.0.0', port=8080)
