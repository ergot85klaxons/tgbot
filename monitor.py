#!/usr/bin/env python3
"""
Telegram-бот: мониторинг постов нескольких X (Twitter) аккаунтов.
Управление списком прямо из бота: /add /remove /list /help.
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
STATE_DIR     = pathlib.Path(os.getenv("STATE_DIR", "state"))
ACCOUNTS_FILE = STATE_DIR / "accounts.json"
LASTSEEN_FILE = STATE_DIR / "last_seen.json"
OFFSET_FILE   = STATE_DIR / "tg_offset.json"

TWITTERAPI_KEY = os.environ["TWITTERAPI_KEY"]
TG_TOKEN   = os.environ["TELEGRAM_BOT_TOKEN"]
TG_CHAT_ID = str(os.environ["TELEGRAM_CHAT_ID"])

INCLUDE_REPLIES  = os.getenv("INCLUDE_REPLIES",  "false").lower() == "true"
INCLUDE_RETWEETS = os.getenv("INCLUDE_RETWEETS", "false").lower() == "true"
DEFAULT_ACCOUNTS = [a.strip().lstrip("@") for a in
                    os.getenv("DEFAULT_ACCOUNTS", "blknoiz06").split(",") if a.strip()]
POLL_INTERVAL    = int(os.getenv("POLL_INTERVAL", "120"))

HELP = (
    "Команды:\n"
    "/add &lt;username&gt; — следить за аккаунтом\n"
    "/remove &lt;username&gt; — перестать следить\n"
    "/list — текущий список\n"
    "/help — справка"
)


def log(*a):
    print(time.strftime("[%Y-%m-%d %H:%M:%S]"), *a, flush=True)


def read_json(p, default):
    try:
        return json.loads(p.read_text())
    except Exception:
        return default


def write_json(p, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False))


def load_accounts():
    data = read_json(ACCOUNTS_FILE, None)
    if not data:
        write_json(ACCOUNTS_FILE, {"accounts": DEFAULT_ACCOUNTS})
        return list(DEFAULT_ACCOUNTS)
    return list(data.get("accounts", []))


def save_accounts(accts):
    write_json(ACCOUNTS_FILE, {"accounts": accts})


def tg(method, **params):
    r = requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/{method}",
                      json=params, timeout=30)
    r.raise_for_status()
    return r.json()


def send(text):
    tg("sendMessage", chat_id=TG_CHAT_ID, text=text,
       parse_mode="HTML", disable_web_page_preview=False)


def extract_tweets(data):
    if isinstance(data.get("tweets"), list):
        return data["tweets"]
    d = data.get("data")
    if isinstance(d, dict) and isinstance(d.get("tweets"), list):
        return d["tweets"]
    if isinstance(d, list):
        return d
    return []


def fetch_tweets(username):
    r = requests.get(API_URL, headers={"X-API-Key": TWITTERAPI_KEY},
                     params={"userName": username}, timeout=30)
    r.raise_for_status()
    return extract_tweets(r.json())


def is_retweet(t):
    return t.get("text", "").startswith("RT @")


def keep(t):
    if t.get("isReply") and not INCLUDE_REPLIES:
        return False
    if is_retweet(t) and not INCLUDE_RETWEETS:
        return False
    return True


def process_commands():
    """Читает входящие сообщения и правит список аккаунтов."""
    offset = read_json(OFFSET_FILE, {}).get("offset")
    params = {"timeout": 0, "allowed_updates": ["message"]}
    if offset is not None:
        params["offset"] = offset
    updates = tg("getUpdates", **params).get("result", [])
    if not updates:
        return
    accts = load_accounts()
    last_id = offset
    for u in updates:
        last_id = u["update_id"] + 1
        msg = u.get("message") or {}
        if str((msg.get("chat") or {}).get("id")) != TG_CHAT_ID:
            continue  # команды только от владельца
        text = (msg.get("text") or "").strip()
        if not text.startswith("/"):
            continue
        parts = text.split()
        cmd = parts[0].lower().split("@")[0]
        arg = parts[1].lstrip("@").strip() if len(parts) > 1 else ""
        if cmd in ("/start", "/help"):
            send(HELP)
        elif cmd == "/add" and arg:
            if arg.lower() in [a.lower() for a in accts]:
                send(f"@{arg} уже в списке.")
            else:
                accts.append(arg)
                send(f"✅ Добавлен @{arg}. Слежу за {len(accts)} аккаунт(ами).")
        elif cmd == "/remove" and arg:
            new = [a for a in accts if a.lower() != arg.lower()]
            if len(new) == len(accts):
                send(f"@{arg} не найден.")
            else:
                accts = new
                send(f"🗑 Удалён @{arg}. Осталось {len(accts)}.")
        elif cmd == "/list":
            body = "\n".join("@" + a for a in accts) if accts else "(пусто)"
            send("Слежу за:\n" + body)
        else:
            send("Не понял команду.\n\n" + HELP)
    save_accounts(accts)
    if last_id is not None:
        write_json(OFFSET_FILE, {"offset": last_id})


def check_account(username, seen):
    tweets = fetch_tweets(username)
    log(f"@{username}: API вернул {len(tweets)} твит(ов)")
    if not tweets:
        return
    tweets.sort(key=lambda t: int(t["id"]))
    newest = int(tweets[-1]["id"])
    prev = seen.get(username)
    if prev is None:
        seen[username] = str(newest)
        log(f"@{username}: инициализация, last_id={newest}")
        return
    prev = int(prev)
    cursor = prev
    for t in [x for x in tweets if int(x["id"]) > prev]:
        if keep(t):
            try:
                txt = html.escape(t.get("text", ""))
                send(f"\U0001F426 <b>@{username}</b>\n\n{txt}\n\n{t.get('url','')}")
                log(f"@{username}: отправлен {t['id']}")
                time.sleep(1)
            except Exception as e:
                log(f"@{username}: ошибка Telegram:", e)
                break
        cursor = int(t["id"])
    if cursor != prev:
        seen[username] = str(cursor)


def check_once():
    try:
        process_commands()
    except Exception as e:
        log("Ошибка обработки команд:", e)
    seen = read_json(LASTSEEN_FILE, {})
    if not isinstance(seen, dict):
        seen = {}
    for username in load_accounts():
        try:
            check_account(username, seen)
        except Exception as e:
            log(f"@{username}: ошибка:", e)
    write_json(LASTSEEN_FILE, seen)


def main():
    if "--loop" in sys.argv:
        log(f"Старт цикла, интервал {POLL_INTERVAL}s")
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
