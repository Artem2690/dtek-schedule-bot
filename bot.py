import os
import re
import json
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
from playwright.sync_api import sync_playwright

WEATHER_URL = os.environ.get("WEATHER_URL", "").strip()
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

if not WEATHER_URL or not BOT_TOKEN or not CHAT_ID:
    raise RuntimeError("Missing env vars (WEATHER_URL / TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID)")

KYIV_TZ = ZoneInfo("Europe/Kyiv")

def send_message(text: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    r = requests.post(
        url,
        data={
            "chat_id": CHAT_ID,
            "text": text,
            "parse_mode": "HTML",  # зручно для жирного заголовка
            "disable_web_page_preview": True,
        },
        timeout=60,
    )
    if not r.ok:
        print("Telegram status:", r.status_code)
        print("Telegram response:", r.text)
        r.raise_for_status()

def extract_fact_from_script(script_text: str) -> dict:
    m = re.search(r"DisconSchedule\.fact\s*=\s*(\{.*\})\s*;?\s*$", script_text.strip(), flags=re.S)
    if not m:
        raise RuntimeError("Script does not match 'DisconSchedule.fact = { ... }'")

    obj_text = m.group(1)
    obj_text = re.sub(r",(\s*[}\]])", r"\1", obj_text)  # прибираємо trailing commas
    return json.loads(obj_text)

def format_schedule_compact(day_gpv: dict) -> str:
    """
    day_gpv: {"1":"yes", ... "24":"first"}.
    Робимо компактні інтервали: 01–03 yes, 04–06 no, ...
    """
    def label(v: str) -> str:
        # Можеш змінити позначення під себе
        if v == "yes":
            return "✅ yes"
        if v == "no":
            return "❌ no"
        if v == "first":
            return "🟡 first"
        if v == "second":
            return "🟠 second"
        return f"❓ {v}"

    # години як ints 1..24
    items = []
    for h in range(1, 25):
        v = day_gpv.get(str(h))
        if v is None:
            v = "unknown"
        items.append((h, v))

    # згортаємо в діапазони однакових значень
    out = []
    start_h, prev_v = items[0]
    for h, v in items[1:]:
        if v != prev_v:
            out.append((start_h, h - 1, prev_v))
            start_h, prev_v = h, v
    out.append((start_h, 24, prev_v))

    lines = []
    for a, b, v in out:
        if a == b:
            lines.append(f"{a:02d}:00 — {label(v)}")
        else:
            lines.append(f"{a:02d}:00–{b:02d}:00 — {label(v)}")
    return "\n".join(lines)

def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 720})

        page.goto(WEATHER_URL, wait_until="networkidle", timeout=90_000)

        # Якщо у тебе вже було, що треба чекати довше — залиш свій sleep
        page.wait_for_timeout(15000)

        # Беремо script, який містить DisconSchedule.fact
        script_text = page.evaluate("""
        () => {
            const s = Array.from(document.scripts)
              .find(x => (x.textContent || '').includes('DisconSchedule.fact'));
            return s ? s.textContent : null;
        }
        """)

        browser.close()

    if not script_text:
        raise RuntimeError("Cannot find script containing 'DisconSchedule.fact'")

    fact = extract_fact_from_script(script_text)

    today = fact.get("today")
    update = fact.get("update", "unknown")
    data = fact.get("data", {})

    if today is None:
        raise RuntimeError("fact.today is missing")

    day_key = str(today)
    day_obj = data.get(day_key)
    if not day_obj:
        raise RuntimeError(f"No data for today={day_key}")

    group = "GPV2.2"
    gpv = day_obj.get(group)
    if not gpv:
        # Якщо на сторінці інколи немає GPV2.2, дай fallback або виведи, які є
        available = ", ".join(sorted(day_obj.keys()))
        raise RuntimeError(f"{group} not found. Available groups: {available}")

    # Дата з today (unix seconds) у часі Києва
    date_str = datetime.fromtimestamp(int(today), tz=KYIV_TZ).strftime("%d.%m.%Y")

    schedule_text = format_schedule_compact(gpv)

    msg = (
        f"<b>Графік на {date_str}</b>\n"
        f"Останнє оновлення: {update}\n"
        f"Група: {group}\n\n"
        f"{schedule_text}"
    )

    send_message(msg)

if __name__ == "__main__":
    main()
