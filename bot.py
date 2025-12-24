import os
import re
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

def extract_fact_from_html(html: str) -> dict:
    # 1) Витягуємо "DisconSchedule.fact = { ... }" (останній script або будь-який у HTML)
    m = re.search(r"DisconSchedule\.fact\s*=\s*(\{.*?\})\s*</script>", html, flags=re.S)
    if not m:
        raise RuntimeError("Cannot find DisconSchedule.fact in page HTML")

    obj_text = m.group(1)

    # 2) Прибираємо JS-коментарі/зайві коми на всяк випадок (у твоєму прикладі є коми після блоків)
    #    Дозволимо trailing commas: видалимо коми перед } або ]
    obj_text = re.sub(r",(\s*[}\]])", r"\1", obj_text)

    # 3) Парсимо як JSON
    return json.loads(obj_text)

def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 720})
        page.goto(WEATHER_URL, wait_until="networkidle", timeout=90_000)

        html = page.content()
        browser.close()

    fact = extract_fact_from_html(html)

    # Збережемо красивим JSON для історії/статистики
    out_path = "dtek_fact.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(fact, f, ensure_ascii=False, indent=2)

    caption = f"DTEK fact update: {fact.get('update', 'unknown')}"
    send_document(out_path, caption=caption)

if __name__ == "__main__":
    main()
