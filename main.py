import os
import re
import time
import json
import logging
import threading
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler
import telebot

# ÐÐ°ÑÑ‚Ñ€Ð¾Ð¹ÐºÐ° Ð»Ð¾Ð³Ð¸Ñ€Ð¾Ð²Ð°Ð½Ð¸Ñ
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# --- ÐšÐžÐÐ¤Ð˜Ð“Ð£Ð ÐÐ¦Ð˜Ð¯ ---
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8753909204:AAHIDp2lV4OxPz0hnJcMf2LEWJsNpwq4WVk")

# ÐžÑ‚ÑÐ»ÐµÐ¶Ð¸Ð²Ð°ÐµÐ¼Ñ‹Ðµ Ñ€Ð°Ð·Ð¼ÐµÑ€Ñ‹ ÑˆÐ¸Ð½
TARGET_SIZES = [
    {"w": "205", "p": "55", "d": "16"},
    {"w": "195", "p": "65", "d": "15"},
    {"w": "205", "p": "65", "d": "16"},
    {"w": "185", "p": "65", "d": "15"}
]

CHECK_INTERVAL = 60  # ÐŸÑ€Ð¾Ð²ÐµÑ€ÐºÐ° ÐºÐ°Ð¶Ð´Ñ‹Ðµ 60 ÑÐµÐºÑƒÐ½Ð´
USERS_FILE = "subscribers.json"
SEEN_ADS_FILE = "seen_ads.json"

bot = telebot.TeleBot(BOT_TOKEN)

# --- 1. Ð’Ð•Ð‘-Ð¡Ð•Ð Ð’Ð•Ð  Ð”Ð›Ð¯ RENDER ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Bot is active!")

    def log_message(self, format, *args):
        return

def start_health_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    logging.info(f"HTTP-ÑÐµÑ€Ð²ÐµÑ€ Ð·Ð°Ð¿ÑƒÑ‰ÐµÐ½ Ð½Ð° Ð¿Ð¾Ñ€Ñ‚Ñƒ {port}")
    server.serve_forever()

# --- 2. Ð ÐÐ‘ÐžÐ¢Ð Ð¡ Ð¥Ð ÐÐÐ˜Ð›Ð˜Ð©Ð•Ðœ ---
def load_data(filename):
    if os.path.exists(filename):
        try:
            with open(filename, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception as e:
            logging.error(f"ÐžÑˆÐ¸Ð±ÐºÐ° Ð·Ð°Ð³Ñ€ÑƒÐ·ÐºÐ¸ {filename}: {e}")
    return set()

def save_data(filename, data_set):
    try:
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(list(data_set), f, ensure_ascii=False)
    except Exception as e:
        logging.error(f"ÐžÑˆÐ¸Ð±ÐºÐ° ÑÐ¾Ñ…Ñ€Ð°Ð½ÐµÐ½Ð¸Ñ {filename}: {e}")

subscribers = load_data(USERS_FILE)
seen_ads = load_data(SEEN_ADS_FILE)

# --- 3. ÐžÐ‘Ð ÐÐ‘ÐžÐ¢ÐšÐ ÐšÐžÐœÐÐÐ”Ð« /start ---
@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    try:
        chat_id = message.chat.id
        logging.info(f"ÐÐ°Ð¶Ð°Ñ‚Ð° ÐºÐ½Ð¾Ð¿ÐºÐ° /start Ð¿Ð¾Ð»ÑŒÐ·Ð¾Ð²Ð°Ñ‚ÐµÐ»ÐµÐ¼ {chat_id}")
        
        if chat_id not in subscribers:
            subscribers.add(chat_id)
            save_data(USERS_FILE, subscribers)
            logging.info(f"Ð¡Ð¾Ñ…Ñ€Ð°Ð½ÐµÐ½ Ð½Ð¾Ð²Ñ‹Ð¹ Ð¿Ð¾Ð´Ð¿Ð¸ÑÑ‡Ð¸Ðº: {chat_id}")
        
        sizes_str = ", ".join([f"{s['w']}/{s['p']} R{s['d']}" for s in TARGET_SIZES])
        text = (
            "ðŸ‘‹ <b>ÐŸÑ€Ð¸Ð²ÐµÑ‚! ÐŸÐ¾Ð¸ÑÐº ÑˆÐ¸Ð½ Ð·Ð°Ð¿ÑƒÑ‰ÐµÐ½!</b>\n\n"
            f"ðŸ” <b>ÐžÑ‚ÑÐ»ÐµÐ¶Ð¸Ð²Ð°ÐµÐ¼ Ñ€Ð°Ð·Ð¼ÐµÑ€Ñ‹:</b> {sizes_str}\n"
            "ðŸ“ <b>Ð¤Ð¸Ð»ÑŒÑ‚Ñ€Ñ‹:</b> Ð³. ÐœÐ¸Ð½ÑÐº, Ð‘/Ð£, Ð§Ð°ÑÑ‚Ð½Ñ‹Ðµ Ð»Ð¸Ñ†Ð°, Ð—Ð¸Ð¼Ð°, Ð”Ð¾ 200 BYN.\n\n"
            "ÐšÐ°Ðº Ñ‚Ð¾Ð»ÑŒÐºÐ¾ Ð¿Ð¾ÑÐ²Ð¸Ñ‚ÑÑ Ð½Ð¾Ð²Ð¾Ðµ Ð¾Ð±ÑŠÑÐ²Ð»ÐµÐ½Ð¸Ðµ â€” Ñ ÑÑ€Ð°Ð·Ñƒ Ð¿Ñ€Ð¸ÑˆÐ»ÑŽ ÐµÐ³Ð¾ ÑÑŽÐ´Ð°!"
        )
        bot.send_message(chat_id, text, parse_mode="HTML")
    except Exception as e:
        logging.error(f"ÐžÑˆÐ¸Ð±ÐºÐ° Ð¿Ñ€Ð¸ Ð¾Ð±Ñ€Ð°Ð±Ð¾Ñ‚ÐºÐµ /start: {e}")

# --- 4. ÐŸÐ ÐžÐ’Ð•Ð ÐšÐ Ð¡ÐžÐžÐ¢Ð’Ð•Ð¢Ð¡Ð¢Ð’Ð˜Ð¯ Ð ÐÐ—ÐœÐ•Ð Ð ---
def matches_target_size(ad):
    title = ad.get("subject", "").lower()
    body = ad.get("body", "").lower()
    full_text = f"{title} {body}"
    
    params = {}
    for p in ad.get("ad_parameters", []):
        p_name = p.get("p", "")
        p_val = str(p.get("v", "")).lower()
        p_label = str(p.get("vl", "")).lower()
        
        if "width" in p_name or "ÑˆÐ¸Ñ€Ð¸Ð½Ð°" in str(p.get("pl", "")).lower():
            params["w"] = p_val or p_label
        elif "profile" in p_name or "Ð¿Ñ€Ð¾Ñ„Ð¸Ð»ÑŒ" in str(p.get("pl", "")).lower():
            params["p"] = p_val or p_label
        elif "rim" in p_name or "Ð´Ð¸Ð°Ð¼ÐµÑ‚Ñ€" in str(p.get("pl", "")).lower():
            params["d"] = p_val.replace("r", "").replace("Ñ€", "") or p_label.replace("r", "").replace("Ñ€", "")

    for target in TARGET_SIZES:
        tw, tp, td = target["w"], target["p"], target["d"]
        
        if params.get("w") == tw and params.get("p") == tp and params.get("d") == td:
            return f"{tw}/{tp} R{td}"
            
        pattern = rf"\b{tw}[/ -.\\]+{tp}\b.*?\b(r|Ñ€)?{td}\b"
        if re.search(pattern, full_text):
            return f"{tw}/{tp} R{td}"
            
    return None

# --- 5. ÐŸÐÐ Ð¡Ð˜ÐÐ“ ÐšÐ£Ð¤ÐÐ Ð ---
def fetch_kufar_ads():
    url = "https://cre-api.kufar.by/v1/search/renditions/ad-list"
    params = {
        "cat": "2010",
        "size": 30,
        "sort": "lst.d",
        "cnd": "2",          # Ð‘/Ð£
        "cmp": "0",          # Ð§Ð°ÑÑ‚Ð½Ð¸ÐºÐ¸
        "rgn": "7",          # ÐœÐ¸Ð½ÑÐº
        "prc": "r:0,20000",  # Ð”Ð¾ 200 BYN
        "ar_season": "2"     # Ð—Ð¸Ð¼Ð°
    }
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    try:
        res = requests.get(url, params=params, headers=headers, timeout=10)
        if res.status_code == 200:
            return res.json().get("ads", [])
    except Exception as e:
        logging.error(f"ÐžÑˆÐ¸Ð±ÐºÐ° Ð·Ð°Ð¿Ñ€Ð¾ÑÐ° Ðº ÐšÑƒÑ„Ð°Ñ€Ñƒ: {e}")
    return []

def check_kufar_loop():
    logging.info("Ð¡ÐºÐ°Ð½Ð½ÐµÑ€ ÐšÑƒÑ„Ð°Ñ€Ð° Ð·Ð°Ð¿ÑƒÑ‰ÐµÐ½...")
    while True:
        try:
            ads = fetch_kufar_ads()
            for ad in reversed(ads):
                ad_id = str(ad.get("ad_id"))
                if not ad_id or ad_id in seen_ads:
                    continue

                matched_size = matches_target_size(ad)
                if matched_size:
                    title = ad.get("subject", "Ð‘ÐµÐ· Ð½Ð°Ð·Ð²Ð°Ð½Ð¸Ñ")
                    price_raw = ad.get("price_byn")
                    if price_raw and str(price_raw).isdigit():
                        val = int(price_raw) / 100
                        price_str = f"{int(val)} BYN" if val.is_integer() else f"{val:.2f} BYN"
                    else:
                        price_str = "Ð”Ð¾Ð³Ð¾Ð²Ð¾Ñ€Ð½Ð°Ñ"

                    link = ad.get("ad_link", "https://www.kufar.by")
                    images = ad.get("images", [])
                    photo_url = f"https://yams.kufar.by/v1/transform/v1/m/id/{images[0]['path']}?rule=gallery" if images else None

                    msg_text = (
                        f"ðŸ”” <b>ÐÐ°Ð¹Ð´ÐµÐ½Ð° ÑˆÐ¸Ð½Ð° {matched_size}!</b>\n\n"
                        f"ðŸ“Œ <b>{title}</b>\n"
                        f"ðŸ’° <b>Ð¦ÐµÐ½Ð°:</b> {price_str}\n\n"
                        f"ðŸ”— <a href='{link}'>ÐžÑ‚ÐºÑ€Ñ‹Ñ‚ÑŒ Ð½Ð° Kufar</a>"
                    )

                    for chat_id in list(subscribers):
                        try:
                            if photo_url:
                                bot.send_photo(chat_id, photo_url, caption=msg_text, parse_mode="HTML")
                            else:
                                bot.send_message(chat_id, msg_text, parse_mode="HTML")
                        except Exception as e:
                            logging.error(f"ÐžÑˆÐ¸Ð±ÐºÐ° Ð¾Ñ‚Ð¿Ñ€Ð°Ð²ÐºÐ¸ Ð¿Ð¾Ð»ÑŒÐ·Ð¾Ð²Ð°Ñ‚ÐµÐ»ÑŽ {chat_id}: {e}")

                    seen_ads.add(ad_id)
                    save_data(SEEN_ADS_FILE, seen_ads)

        except Exception as e:
            logging.error(f"ÐžÑˆÐ¸Ð±ÐºÐ° Ð² Ñ†Ð¸ÐºÐ»Ðµ ÑÐºÐ°Ð½Ð¸Ñ€Ð¾Ð²Ð°Ð½Ð¸Ñ: {e}")

        time.sleep(CHECK_INTERVAL)

# --- 6. Ð—ÐÐŸÐ£Ð¡Ðš Ð’Ð¡Ð•Ð¥ ÐŸÐžÐ¢ÐžÐšÐžÐ’ ---
if __name__ == "__main__":
    threading.Thread(target=start_health_server, daemon=True).start()
    threading.Thread(target=check_kufar_loop, daemon=True).start()
    
    try:
        bot.remove_webhook()
    except Exception:
        pass

    logging.info("Ð‘Ð¾Ñ‚ Ð³Ð¾Ñ‚Ð¾Ð² Ð¿Ð¾Ð»ÑƒÑ‡Ð°Ñ‚ÑŒ ÑÐ¾Ð¾Ð±Ñ‰ÐµÐ½Ð¸Ñ...")
    bot.infinity_polling(skip_pending=True)
