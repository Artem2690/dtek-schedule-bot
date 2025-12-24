import os
import re
import json
import time
import random
import hashlib
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
from playwright.sync_api import sync_playwright

# ========== CONFIG ==========
KYIV_TZ = ZoneInfo("Europe/Kyiv")
GROUP = "GPV2.2"
STATE_FILE = "state.json"

WEATHER_URL = os.environ.get("WEATHER_URL", "").strip()
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

if not WEATHER_URL or not BOT_TOKEN or not CHAT_ID:
    raise RuntimeError("Missing env vars (WEATHER_URL / TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID)")

# Random delay: 0..8 minutes (to make checks not exactly every 30 minutes)
RANDOM_DELAY_SECONDS = int(os.environ.get("RANDOM_DELAY_SECONDS", "480"))  # 8 min default

# ========== TELEGRAM ==========
def tg_send_message(text: str) -> dict:
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
    return r.json()

# ========== FACT PARSING ==========
def extract_fact_from_script(script_text: str) -> dict:
    m = re.search(
        r"DisconSchedule\.fact\s*=\s*(\{.*\})\s*;?\s*$",
        script_text.strip(),
        flags=re.S,
    )
    if not m:
        raise RuntimeError("Script does not contain DisconSchedule.fact")

    obj_text = m.group(1)
    obj_text = re.sub(r",(\s*[}\]])", r"\1", obj_text)  # remove trailing commas
    return json.loads(obj_text)

# ========== SCHEDULE FORMATTING (30-min precision) ==========
def format_schedule_halfhour(day_gpv: dict) -> str:
    """
    day_gpv: {"1":"yes"/"no"/"first"/"second", ... "24":...}
    Interpret hour h as interval (h-1):00 -> h:00.
    first/second are treated as half-hour transition at (h-1):30:
      first half = prev yes/no, second half = next yes/no
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

    slots = [None] * 48  # 48 half-hours

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

def build_message(fact: dict) -> tuple[str, str]:
    """
    Returns (header_text, schedule_text)
    """
    today = fact.get("today")
    update = fact.get("update", "unknown")
    data = fact.get("data", {})

    if not today:
        raise RuntimeError("fact.today missing")

    day_key = str(today)
    if day_key not in data:
        raise RuntimeError(f"No data for today={day_key}")

    day_obj = data[day_key]
    if GROUP not in day_obj:
        raise RuntimeError(f"{GROUP} not found. Available: {', '.join(day_obj.keys())}")

    gpv = day_obj[GROUP]
    date_str = datetime.fromtimestamp(int(today), KYIV_TZ).strftime("%d.%m.%Y")

    header = (
        f"<b>Графік на {date_str}</b>\n"
        f"Останнє оновлення: {update}\n"
        f"Група: {GROUP}"
    )
    schedule = format_schedule_halfhour(gpv)
    return header, schedule

def schedule_hash(header: str, schedule: str) -> str:
    # Hash only the schedule (and group/day header), not Telegram formatting noise
    payload = header + "\n\n" + schedule
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()

# ========== STATE ==========
def load_state() -> dict:
    if not os.path.exists(STATE_FILE):
        return {
            "last_hash": "",
            "last_sent_date": "",  # YYYY-MM-DD Kyiv
        }
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_state(state: dict):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

# ========== FETCH FACT ==========
def fetch_fact() -> dict:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 720})

        page.goto(WEATHER_URL, wait_until="networkidle", timeout=90_000)

        # Your known stable wait (site renders late)
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
    return extract_fact_from_script(script_text)

# ========== MAIN ==========
def main():
    # randomize interval: 30..38 minutes total (cron every 30 min + random sleep up to 8 min)
    if RANDOM_DELAY_SECONDS > 0:
        delay = random.randint(0, RANDOM_DELAY_SECONDS)
        time.sleep(delay)

    now_kyiv = datetime.now(KYIV_TZ)
    today_str = now_kyiv.strftime("%Y-%m-%d")

    state = load_state()

    fact = fetch_fact()
    header, schedule = build_message(fact)
    h = schedule_hash(header, schedule)

    # Rule A: send at/after 08:00 Kyiv once per day (first run after 08:00)
    # We treat "daily send window" as 08:00–23:59.
    should_send_daily = (now_kyiv.hour >= 8) and (state.get("last_sent_date") != today_str)

    # Rule B: send on changes anytime (after we have ever sent something)
    changed = (state.get("last_hash") != "") and (h != state.get("last_hash"))

    if should_send_daily:
        msg = header + "\n\n" + schedule
        tg_send_message(msg)
        state["last_hash"] = h
        state["last_sent_date"] = today_str
        save_state(state)
        print("Sent daily schedule.")
        return

    if changed:
        msg = "<b>Графік змінився</b>\n\n" + header + "\n\n" + schedule
        tg_send_message(msg)
        state["last_hash"] = h
        save_state(state)
        print("Sent updated schedule.")
        return

    print("No changes; nothing sent.")

if __name__ == "__main__":
    main()
