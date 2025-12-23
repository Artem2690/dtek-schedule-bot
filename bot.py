import os
import re
import json
import hashlib
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

URL = "https://www.dtek-krem.com.ua/ua/shutdowns"
STATE_FILE = "state.json"

QUEUE_ID = os.getenv("QUEUE_ID", "GPV2.2")
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

TZ = ZoneInfo("Europe/Kyiv")


def fetch_html() -> str:
    r = requests.get(
        URL,
        timeout=30,
        headers={"User-Agent": "Mozilla/5.0 (compatible; dtek-schedule-bot/1.0)"},
        allow_redirects=True,
    )
    print("HTTP:", r.status_code)
    print("Final URL:", r.url)
    print("Content-Type:", r.headers.get("content-type"))
    text = r.text
    print("HTML head (first 4000 chars):\n", text[:4000])
    return text


def extract_fact(html: str) -> dict:
    # Витягнути DisconSchedule.fact = {...}
    m = re.search(r"DisconSchedule\.fact\s*=\s*(\{.*?\})\s*DisconSchedule\.", html, flags=re.S)
    if not m:
        m = re.search(r"DisconSchedule\.fact\s*=\s*(\{.*?\})\s*</script>", html, flags=re.S)
    if not m:
        raise RuntimeError("Не знайшов DisconSchedule.fact у HTML")
    return json.loads(m.group(1))


def compute_state(fact: dict, queue_id: str) -> dict:
    today_ts = str(fact["today"])
    day_obj = fact["data"].get(today_ts, {}).get(queue_id)
    if not day_obj:
        raise RuntimeError(f"Немає даних для queue={queue_id} today={today_ts}")

    slots = [day_obj[str(i)] for i in range(1, 25)]

    # Хеш саме сьогоднішніх слотів (якщо зміняться — зміниться хеш)
    h = hashlib.sha256(("|".join(slots)).encode("utf-8")).hexdigest()

    return {
        "queue_id": queue_id,
        "today_ts": today_ts,
        "update": fact.get("update"),
        "slots": slots,
        "hash": h,
    }


def load_prev_state() -> dict | None:
    if not os.path.exists(STATE_FILE):
        return None
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def send_telegram(text: str) -> None:
    if not BOT_TOKEN or not CHAT_ID:
        raise RuntimeError("TELEGRAM_BOT_TOKEN або TELEGRAM_CHAT_ID не задані в env/secrets")

    resp = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        timeout=30,
        data={"chat_id": CHAT_ID, "text": text}
    )
    resp.raise_for_status()


def format_message(state: dict, title: str) -> str:
    # Перетворюємо стани в короткі позначки
    legend = {
        "yes": "✅ є",
        "no": "⛔ нема",
        "maybe": "⚠ можливо",
        "first": "⛔(1/2)",
        "second": "⛔(2/2)",
        "mfirst": "⚠(1/2)",
        "msecond": "⚠(2/2)",
    }

    slots = [legend.get(x, x) for x in state["slots"]]

    # Групуємо в інтервали однакових статусів, щоб читалось нормально
    groups = []
    start = 1
    cur = slots[0]
    for i in range(2, 25):
        if slots[i - 1] != cur:
            groups.append((start, i - 1, cur))
            start = i
            cur = slots[i - 1]
    groups.append((start, 24, cur))

    now = datetime.now(TZ).strftime("%Y-%m-%d %H:%M")
    update = state.get("update") or "невідомо"

    lines = [
        f"{title}",
        f"Черга: {state['queue_id']}",
        f"Оновлення на сайті: {update}",
        f"Локальний час: {now}",
        "",
        "Інтервали:",
    ]
    for a, b, status in groups:
        if a == b:
            lines.append(f"{a:02d}:00–{a:02d}:59 — {status}")
        else:
            lines.append(f"{a:02d}:00–{b:02d}:59 — {status}")

    return "\n".join(lines)


def main():
    now = datetime.now(TZ)
    today_date = now.strftime("%Y-%m-%d")

    html = fetch_html()
    fact = extract_fact(html)
    cur = compute_state(fact, QUEUE_ID)

    prev = load_prev_state()
    prev_hash = prev.get("hash") if prev else None
    prev_morning_date = prev.get("morning_sent_date") if prev else None

    changed = (prev_hash is not None and prev_hash != cur["hash"])

    # Ранкове повідомлення: 07:00–07:09 раз на день
    is_morning_window = (now.hour == 7 and 0 <= now.minute <= 9)
    should_send_morning = is_morning_window and (prev_morning_date != today_date)

    if changed:
        send_telegram(format_message(cur, "🔄 Графік змінився"))
    elif should_send_morning:
        send_telegram(format_message(cur, "☀️ Графік на сьогодні"))

    # Зберігаємо state + дату ранкового надсилання
    cur["morning_sent_date"] = today_date if should_send_morning else prev_morning_date
    save_state(cur)


if __name__ == "__main__":
    main()
