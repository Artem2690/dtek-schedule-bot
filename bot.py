import os
import requests
from playwright.sync_api import sync_playwright

WEATHER_URL = os.environ["WEATHER_URL"]            # сторінка з графіком
CHART_SELECTOR = os.environ.get("CHART_SELECTOR", "")# CSS-селектор графіка (опційно)
BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

def send_photo(path: str, caption: str = ""):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    with open(path, "rb") as f:
        r = requests.post(
            url,
            data={"chat_id": CHAT_ID, "caption": caption},
            files={"photo": f},
            timeout=60,
        )

    if not r.ok:
        # Telegram повертає JSON з описом помилки
        print("Telegram status:", r.status_code)
        print("Telegram response:", r.text)
        r.raise_for_status()

def main():
    screenshot_path = "forecast.png"

   with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 720})
        page.goto(WEATHER_URL, wait_until="domcontentloaded", timeout=90_000)

        script_text = page.evaluate("""
        () => {
          const scripts = Array.from(document.querySelectorAll("body ul"));
          if (!scripts.length) return null;
          return scripts[scripts.length - 1].textContent;
        }
        """)

        browser.close()

    if not script_text or not script_text.strip():
        raise RuntimeError("Last <script> not found or empty")

    out_path = "dtek_raw_script.txt"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(script_text.strip())

    send_document(out_path, caption="DTEK raw schedule script")

if __name__ == "__main__":
    main()
