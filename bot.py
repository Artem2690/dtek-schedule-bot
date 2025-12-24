import os
import requests
from playwright.sync_api import sync_playwright
from datetime import datetime

WEATHER_URL = os.environ.get("WEATHER_URL", "").strip()
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

if not WEATHER_URL or not BOT_TOKEN or not CHAT_ID:
    raise RuntimeError("Missing env vars")

def send_document(path: str, caption: str = ""):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument"
    with open(path, "rb") as f:
        r = requests.post(
            url,
            data={"chat_id": CHAT_ID, "caption": caption},
            files={"document": f},
            timeout=60,
        )
    if not r.ok:
        print("Telegram status:", r.status_code)
        print("Telegram response:", r.text)
        r.raise_for_status()

def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 720})

        page.goto(WEATHER_URL, wait_until="networkidle", timeout=90_000)
        page.wait_for_timeout(15000)

        # беремо останній <ul> у body
        ul_text = page.evaluate("""
        () => {
            const els = Array.from(document.querySelectorAll("body script"));
            if (!els.length) return null;
            return els[els.length - 1].innerText;
        }
        """)

        browser.close()

    if not ul_text or not ul_text.strip():
        raise RuntimeError("Last <ul> not found or empty")

    ts = datetime.utcnow().strftime("%Y-%m-%d_%H-%M")
    out_path = f"page_ul_{ts}.txt"

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(ul_text.strip())

    send_document(out_path, caption="Last <ul> content")

if __name__ == "__main__":
    main()
