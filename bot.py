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
        r = requests.post(url, data={"chat_id": CHAT_ID, "caption": caption}, files={"photo": f})
    r.raise_for_status()

def main():
    screenshot_path = "forecast.png"

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 720})
        page.goto(WEATHER_URL, wait_until="networkidle", timeout=90_000)

        # Якщо графік є конкретним DOM-елементом (canvas/svg/div), краще скрінити його.
        if CHART_SELECTOR:
            el = page.locator(CHART_SELECTOR)
            el.wait_for(state="visible", timeout=30_000)
            el.screenshot(path=screenshot_path)
        else:
            # Фолбек: скрін усієї сторінки
            page.screenshot(path=screenshot_path, full_page=True)

        browser.close()

    send_photo(screenshot_path, caption="Daily forecast (auto)")

if __name__ == "__main__":
    main()
