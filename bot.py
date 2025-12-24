import os
import re
import json
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
from playwright.sync_api import sync_playwright

# ================== ENV ==================
WEATHER_URL = os.environ.get("WEATHER_URL", "").strip()
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

if not WEATHER_URL or not BOT_TOKEN or not CHAT_ID:
    raise RuntimeError("Missing env vars (WEATHER_URL / TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID)")

KYIV_TZ = ZoneInfo("Europe/Kyiv")
GROUP = "GPV2.2"

# ================== TELEGRAM ==================
def send_message(text: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    r = requests.post(
        url,
        data={
            "chat_id": CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=60,
    )
    if not r.ok:
        print("Telegram status:", r.status_code)
        print("Telegram response:", r.text)
        r.raise_for_status()

# ================== PARSE JS ==================
def extract_fact_from_script(script_text: str) -> dict:
    """
    Очікуємо:
      DisconSchedule.fact = { ... }
    """
    m = re.search(
        r"DisconSchedule\.fact\s*=\s*(\{.*\})\s*;?\s*$",
        script_text.strip(),
        flags=re.S,
    )
    if not m:
        raise RuntimeError("Script does not contain DisconSchedule.fact")

    obj_text = m.group(1)

    # прибираємо trailing commas
    obj_text = re.sub(r",(\s*[}\]])", r"\1", obj_text)

    return json.loads(obj_text)

# ================== FORMAT ==================
def format_schedule_halfhour(day_gpv: dict) -> str:
    """
    Інтерпретація:
    - yes/no -> повна година
    - first/second -> півгодинний перехід
      (перша половина — попередній стан, друга — наступний)
    """

    def prev_yesno(h):
        for hh in range(h - 1, 0, -1):
            v = day_gpv.get(str(hh))
            if v in ("yes", "no"):
                return v
        return "no"

    def next_yesno(h):
        for hh in range(h + 1, 25):
            v = day_gpv.get(str(hh))
            if v in ("yes", "no"):
                return v
        return prev_yesno(h)

    # 48 слотів по 30 хв
    slots = [None] * 48

    for h in range(1, 25):
        v = day_gpv.get(str(h), "no")
        i = (h - 1) * 2

        if v in ("yes", "no"):
            slots[i] = v
            slots[i + 1] = v
        elif v in ("first", "second"):
            slots[i] = prev_yesno(h)
            slots[i + 1] = next_yesno(h)
        else:
            slots[i] = "no"
            slots[i + 1] = "no"

    def t(i):
        m = i * 30
        return f"{m // 60:02d}:{m % 60:02d}"

    def icon(v):
        return "✅" if v == "yes" else "❌"

    out = []
    start = 0
    cur = slots[0]

    for i in range(1, 48):
        if slots[i] != cur:
            out.append((start, i, cur))
            start = i
            cur = slots[i]

    out.append((start, 48, cur))

    lines = []
    for a, b, v in out:
        lines.append(f"{t(a)}–{t(b)} — {icon(v)} {v}")

    return "\n".join(lines)

# ================== MAIN ==================
def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 720})

        page.goto(WEATHER_URL, wait_until="networkidle", timeout=90_000)

        # твій перевірений варіант
        page.wait_for_timeout(15000)

        script_text = page.evaluate("""
        () => {
            const s = Array.from(document.scripts)
              .find(x => (x.textContent || '').includes('DisconSchedule.fact'));
            return s ? s.textContent : null;
        }
        """)

        browser.close()

    if not script_text:
        raise RuntimeError("Cannot find DisconSchedule.fact script")

    fact = extract_fact_from_script(script_text)

    today = fact.get("today")
    update = fact.get("update", "unknown")
    data = fact.get("data", {})

    if not today or str(today) not in data:
        raise RuntimeError("No data for today")

    day_obj = data[str(today)]

    if GROUP not in day_obj:
        raise RuntimeError(f"{GROUP} not found. Available: {', '.join(day_obj.keys())}")

    gpv = day_obj[GROUP]

    date_str = datetime.fromtimestamp(int(today), KYIV_TZ).strftime("%d.%m.%Y")
    schedule = format_schedule_halfhour(gpv)

    msg = (
        f"<b>Графік на {date_str}</b>\n"
        f"Останнє оновлення: {update}\n"
        f"Група: {GROUP}\n\n"
        f"{schedule}"
    )

    send_message(msg)

if __name__ == "__main__":
    main()
