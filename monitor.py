#!/usr/bin/env python3
"""
Telegram-уведомления о новых постах X (Twitter) аккаунта.
Источник твитов: twitterapi.io (официальный ключ X НЕ нужен).
  python monitor.py          - однократная проверка (GitHub Actions / cron)
  python monitor.py --loop   - бесконечный цикл (VPS / Raspberry Pi / ПК)
"""
import os
import sys
import json
import time
import html
import pathlib
import requests

API_URL = "https://api.twitterapi.io/twitter/user/last_tweets"
STATE_FILE = pathlib.Path(os.getenv("STATE_FILE", "state/last_seen.json"))

TWITTERAPI_KEY = os.environ["TWITTERAPI_KEY"]
TG_TOKEN       = os.environ["TELEGRAM_BOT_TOKEN"]
TG_CHAT_ID     = os.environ["TELEGRAM_CHAT_ID"]
TARGET         = os.getenv("TARGET_USERNAME", "blknoiz06").lstrip("@")

INCLUDE_REPLIES  = os.getenv("INCLUDE_REPLIES",  "false").lower() == "true"
INCLUDE_RETWEETS = os.getenv("INCLUDE_RETWEETS", "false").lower() == "true"
POLL_INTERVAL    = int(os.getenv("POLL_INTERVAL", "120"))


def log(*a):
    print(time.strftime("[%Y-%m-%d %H:%M:%S]"), *a, flush=True)


def extract_tweets(data):
    """last_tweets может вернуть твиты на верхнем уровне ИЛИ внутри data.tweets."""
    if isinstance(data.get("tweets"), list):
        return data["tweets"]
    d = data.get("data")
    if isinstance(d, dict) and isinstance(d.get("tweets"), list):
        return d["tweets"]
    if isinstance(d, list):
        return d
    return []


def fetch_tweets():
    r = requests.get(
        API_URL,
        headers={"X-API-Key": TWITTERAPI_KEY},
        params={"userName": TARGET},
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    tweets = extract_tweets(data)
    log(f"API вернул {len(tweets)} твит(ов)")
    if not tweets:
        log("Сырой ответ API:", json.dumps(data, ensure_ascii=False)[:600])
    return tweets


def is_retweet(t):
    return t.get("text", "").startswith("RT @")


def keep(t):
    if t.get("isReply") and not INCLUDE_REPLIES:
        return False
    if is_retweet(t) and not INCLUDE_RETWEETS:
        return False
    return True


def load_state():
    try:
        return int(json.loads(STATE_FILE.read_text())["last_id"])
    except Exception:
        return None


def save_state(last_id):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps({"last_id": str(last_id)}))
    log(f"Состояние сохранено: last_id={last_id}")


def send_telegram(t):
    text = html.escape(t.get("text", ""))
    url  = t.get("url", "")
    msg = f"\U0001F426 <b>@{TARGET}</b> — новый пост\n\n{text}\n\n{url}"
    resp = requests.post(
        f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
        json={
            "chat_id": TG_CHAT_ID,
            "text": msg,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        },
        timeout=30,
    )
    resp.raise_for_status()


def check_once():
    tweets = fetch_tweets()
    if not tweets:
        return
    tweets.sort(key=lambda t: int(t["id"]))
    newest_id = int(tweets[-1]["id"])
    last_seen = load_state()

    if last_seen is None:
        save_state(newest_id)
        log("Инициализация завершена.")
        return

    new = [t for t in tweets if int(t["id"]) > last_seen]
    if not new:
        log("Новых твитов нет.")
        return

    cursor = last_seen
    for t in new:
        if keep(t):
            try:
                send_telegram(t)
                log(f"Отправлен твит {t['id']}")
                time.sleep(1)
            except Exception as e:
                log("Ошибка отправки в Telegram:", e)
                break
        cursor = int(t["id"])
    if cursor != last_seen:
        save_state(cursor)


def main():
    if "--loop" in sys.argv:
        log(f"Мониторинг @{TARGET}, интервал {POLL_INTERVAL}s")
        while True:
            try:
                check_once()
            except Exception as e:
                log("Ошибка цикла:", e)
            time.sleep(POLL_INTERVAL)
    else:
        check_once()


if __name__ == "__main__":
    main()
