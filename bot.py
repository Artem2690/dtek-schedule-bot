import os
import json
import requests
from playwright.sync_api import sync_playwright

WEATHER_URL = os.environ.get("WEATHER_URL", "").strip()
BOT_TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID     = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

if not WEATHER_URL or not BOT_TOKEN or not CHAT_ID:
    raise RuntimeError("Missing env vars (WEATHER_URL / TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID)")

def send_document(path: str, caption: str = ""):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument"
    with open(path, "rb") as f:
        r = requests.post(url, data={"chat_id": CHAT_ID, "caption": caption}, files={"document": f}, timeout=60)
    if not r.ok:
        print("Telegram status:", r.status_code)
        print("Telegram response:", r.text)
        r.raise_for_status()

def main():
    out_path = "dtek_fact.json"

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 720})

        page.goto(WEATHER_URL, wait_until="domcontentloaded", timeout=90_000)

        # Чекаємо, поки об'єкт з'явиться у window
        page.wait_for_function(
            "() => window.DisconSchedule && window.DisconSchedule.fact && window.DisconSchedule.fact.data",
            timeout=60_000
        )

        fact = page.evaluate("() => window.DisconSchedule.fact")
        browser.close()

    # Збережемо у файл
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(fact, f, ensure_ascii=False, indent=2)

    caption = f"DTEK fact update: {fact.get('update', 'unknown')}"
    send_document(out_path, caption=caption)

if __name__ == "__main__":
    main()
