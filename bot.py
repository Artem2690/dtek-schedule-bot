from __future__ import annotations

import os
import json
import hashlib
import requests
from datetime import datetime, timedelta, date
from html import escape
from zoneinfo import ZoneInfo
from playwright.sync_api import sync_playwright

# ========== CONFIG ==========
KYIV_TZ = ZoneInfo("Europe/Kyiv")
GROUP = "GPV2.2"
STATE_FILE = "state.json"

WEATHER_URL = os.environ.get("WEATHER_URL", "").strip()
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
FIREBASE_URL = os.environ.get("FIREBASE_URL", "").strip()

# ========== TELEGRAM ==========
class TelegramError(RuntimeError):
    def __init__(self, code: int, description: str):
        super().__init__(f"Telegram {code}: {description}")
        self.code = code
        self.description = description


def tg_call(method: str, payload: dict) -> dict:
    response = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/{method}",
        json={"chat_id": CHAT_ID, **payload}, timeout=60,
    )
    body = response.json()
    if not body.get("ok"):
        raise TelegramError(body.get("error_code", response.status_code),
                            body.get("description", "Unknown error"))
    response.raise_for_status()
    return body


def tg_send_message(text: str, *, disable_notification: bool = False,
                    reply_to: int | None = None) -> dict:
    payload = {"text": text, "parse_mode": "HTML",
               "link_preview_options": {"is_disabled": True},
               "disable_notification": disable_notification}
    if reply_to is not None:
        payload["reply_parameters"] = {
            "message_id": reply_to, "allow_sending_without_reply": True,
        }
    return tg_call("sendMessage", payload)


def tg_edit_message(message_id: int, text: str) -> None:
    try:
        tg_call("editMessageText", {"message_id": message_id, "text": text,
                "parse_mode": "HTML", "link_preview_options": {"is_disabled": True}})
    except TelegramError as exc:
        if exc.code == 400 and "message is not modified" in exc.description.lower():
            return
        raise


# ========== SCHEDULE ==========
def format_schedule_halfhour(day_gpv: dict) -> tuple[str, list]:
    mapping = {"yes": ("yes", "yes"), "no": ("no", "no"),
               "first": ("no", "yes"), "second": ("yes", "no")}
    if set(day_gpv) != {str(h) for h in range(1, 25)}:
        raise RuntimeError("Schedule must contain exactly 24 hours")
    slots = []
    for h in range(1, 25):
        value = day_gpv[str(h)]
        if value not in mapping:
            raise RuntimeError(f"Unknown schedule value at hour {h}: {value!r}")
        slots.extend(mapping[value])

    def clock(i):
        return f"{i // 2:02d}:{(i % 2) * 30:02d}"

    result = []
    start = 0
    for i in range(1, 49):
        if i == 48 or slots[i] != slots[start]:
            result.append({"time": f"{clock(start)}–{clock(i)}", "value": slots[start]})
            start = i
    labels = {"yes": "✅ Світло є", "no": "❌ Світла немає"}
    text = "\n".join(f"{item['time']} — {labels[item['value']]}" for item in result)
    return text, result


def collect_days(fact: dict, today: date) -> dict:
    # Use actual Kyiv calendar dates; fact.today may still point to yesterday.
    raw_data = fact.get("data")
    if not isinstance(raw_data, dict):
        raise RuntimeError("fact.data missing or invalid")
    by_date = {}
    for timestamp, groups in raw_data.items():
        key = datetime.fromtimestamp(int(timestamp), KYIV_TZ).date().isoformat()
        if key in by_date:
            raise RuntimeError(f"Duplicate schedule date: {key}")
        by_date[key] = groups
    days = {}
    for target in (today, today + timedelta(days=1)):
        key = target.isoformat()
        groups = by_date.get(key, {})
        hours = groups.get(GROUP)
        if hours is None:
            days[key] = None
        else:
            _, days[key] = format_schedule_halfhour(hours)
    return days


def schedule_hash(day: list | None) -> str:
    canonical = json.dumps(day, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_message(days: dict, today: date, update: str) -> str:
    sections = [f"#розклад\n<b>Графіки відключень · {escape(GROUP)}</b>"]
    for offset, label in ((0, "Сьогодні"), (1, "Завтра")):
        target = today + timedelta(days=offset)
        day = days[target.isoformat()]
        text = "Графік ще не опубліковано." if day is None else "\n".join(
            f"{item['time']} — " + ("✅ Світло є" if item['value'] == "yes" else "❌ Світла немає")
            for item in day
        )
        sections.append(f"<b>{label}, {target:%d.%m.%Y}</b>\n{text}")
    sections.append(f"Оновлення джерела: {escape(str(update))}")
    return "\n\n".join(sections)


# ========== STATE ==========
def load_state() -> dict:
    if not os.path.exists(STATE_FILE):
        return {}
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        state = json.load(f)
    if not isinstance(state, dict):
        raise RuntimeError("Invalid state.json")
    return state  # Old fields remain compatible; no manual reset needed.


def save_state(state: dict):
    temp = STATE_FILE + ".tmp"
    with open(temp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, STATE_FILE)


# ========== FETCH FACT ==========
def fetch_fact() -> dict:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 720})
            page.goto(WEATHER_URL, wait_until="domcontentloaded", timeout=90_000)
            page.wait_for_function("""() => typeof DisconSchedule !== 'undefined'
                && DisconSchedule.fact && DisconSchedule.fact.data""", timeout=60_000)
            return page.evaluate("() => DisconSchedule.fact")
        finally:
            browser.close()


# ========== FIREBASE ==========
def send_to_firebase(data: list):
    if not FIREBASE_URL:
        return
    try:
        payload = {
            "mode": "edit",
            "list": data
        }
        print(f"Sending data to Firebase...")
        response = requests.put(FIREBASE_URL, json=payload, timeout=10)
        response.raise_for_status()
        print("Firebase updated successfully ✅")
    except Exception as e:
        print(f"Firebase Error ❌: {e}")

# ========== DAILY MESSAGE ==========
def flush_notifications(state: dict) -> None:
    while state.get("pending_notifications"):
        text = state["pending_notifications"][0]
        tg_send_message(text, reply_to=state["daily_message_id"])
        state["pending_notifications"].pop(0)
        save_state(state)


def sync_daily_message(fact: dict, state: dict, now_kyiv: datetime) -> None:
    today = now_kyiv.date()
    if now_kyiv.hour == 0 and now_kyiv.minute < 1:
        return  # Do not create the new day's message before 00:01.
    days = collect_days(fact, today)
    hashes = {key: schedule_hash(day) for key, day in days.items()}
    message = build_message(days, today, fact.get("update", "невідомо"))
    same_day = (state.get("daily_message_date") == today.isoformat()
                and state.get("daily_message_chat_id") == CHAT_ID
                and state.get("daily_message_group") == GROUP
                and bool(state.get("daily_message_id")))
    if not same_day:
        sent = tg_send_message(message)
        state.update(daily_message_date=today.isoformat(),
                     daily_message_chat_id=CHAT_ID, daily_message_group=GROUP,
                     daily_message_id=sent["result"]["message_id"],
                     day_hashes=hashes, pending_notifications=[])
        save_state(state)
    else:
        flush_notifications(state)
        changed = [key for key in days if hashes[key] != state.get("day_hashes", {}).get(key)]
        if changed:
            try:
                tg_edit_message(state["daily_message_id"], message)
            except TelegramError as exc:
                description = exc.description.lower()
                if exc.code == 400 and ("message to edit not found" in description
                                        or "message can't be edited" in description):
                    sent = tg_send_message(message)
                    state["daily_message_id"] = sent["result"]["message_id"]
                else:
                    raise
            labels = ["сьогодні" if key == today.isoformat() else "завтра" for key in changed]
            text = "⚡ Оновлено графік на " + " і ".join(labels) + ". Основне повідомлення актуалізовано."
            state["day_hashes"] = hashes
            state["pending_notifications"] = [text]
            save_state(state)  # Persist edited state before attempting notification.
            flush_notifications(state)
    # Firebase keeps its original today-only payload for existing consumers.
    if days[today.isoformat()] is not None:
        send_to_firebase(days[today.isoformat()])


def main():
    if not WEATHER_URL or not BOT_TOKEN or not CHAT_ID:
        raise RuntimeError("Missing env vars (WEATHER_URL / TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID)")
    state = load_state()
    fact = fetch_fact()
    now_kyiv = datetime.now(KYIV_TZ)  # Fetch may cross midnight.
    sync_daily_message(fact, state, now_kyiv)


if __name__ == "__main__":
    main()
