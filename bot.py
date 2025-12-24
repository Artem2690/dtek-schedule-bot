import os
import re
import json
import requests
from playwright.sync_api import sync_playwright
from datetime import datetime

WEATHER_URL = os.environ.get("WEATHER_URL", "").strip()
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

if not WEATHER_URL or not BOT_TOKEN or not CHAT_ID:
    raise RuntimeError("Missing env vars (WEATHER_URL / TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID)")

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

def extract_fact_from_script(script_text: str) -> dict:
    """
    Очікуємо формат на кшталт:
      DisconSchedule.fact = { ... }
    """
    # Витягнути об'єкт {...} після "DisconSchedule.fact ="
    m = re.search(r"DisconSchedule\.fact\s*=\s*(\{.*\})\s*;?\s*$", script_text.strip(), flags=re.S)
    if not m:
        raise RuntimeError("Found script, but it does not match 'DisconSchedule.fact = { ... }'")

    obj_text = m.group(1)

    # Прибираємо trailing commas перед } або ]
    obj_text = re.sub(r",(\s*[}\]])", r"\1", obj_text)

    return json.loads(obj_text)

def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 720})

        page.goto(WEATHER_URL, wait_until="networkidle", timeout=90_000)

        # 1) Правильне очікування: чекаємо, поки у document.scripts з'явиться текст з DisconSchedule.fact
        # Якщо твій сайт "повільний", збільш timeout до 120_000
        try:
            page.wait_for_function(
                "() => Array.from(document.scripts).some(s => (s.textContent || '').includes('DisconSchedule.fact'))",
                timeout=60_000
            )
        except Exception:
            # 2) Fallback на твій робочий "sleep", якщо сайт інколи довго віддає потрібний script
            page.wait_for_timeout(15000)

        # Забираємо саме той <script>, де є DisconSchedule.fact
        script_text = page.evaluate("""
        () => {
            const scripts = Array.from(document.scripts);
            const s = scripts.find(x => (x.textContent || '').includes('DisconSchedule.fact'));
            return s ? s.textContent : null;
        }
        """)

        browser.close()

    if not script_text or not script_text.strip():
        raise RuntimeError("Cannot find script containing 'DisconSchedule.fact' (maybe URL is not the expected page)")

    fact = extract_fact_from_script(script_text)

    ts = datetime.utcnow().strftime("%Y-%m-%d_%H-%M")
    out_path = f"dtek_fact_{ts}.json"

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(fact, f, ensure_ascii=False, indent=2)

    caption = f"DTEK fact update: {fact.get('update', 'unknown')}"
    send_document(out_path, caption=caption)

if __name__ == "__main__":
    main()
