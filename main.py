"""
Main entrypoint for the Mail Triage & Aggregation Daemon.
"""

import sys
import os
import json
import time
import asyncio
import signal
import logging
import sqlite3
from typing import List

from storage.db import init_db, get_latest_otp, get_recent_messages
from core.searcher import search_emails, check_spam_folders
from notify.telegram import TelegramNotifier
from core.worker import ImapAccountWorker
from core.api import MailRestApi

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] (%(name)s) %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("mail-triage")

START_TIME = time.time()

def get_status_report(accounts_list: List[dict], db_path: str) -> str:
    uptime_sec = int(time.time() - START_TIME)
    hours, rem = divmod(uptime_sec, 3600)
    minutes, seconds = divmod(rem, 60)
    uptime_str = f"{hours}ч {minutes}м {seconds}с"

    total_processed = 0
    otp_count = 0
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("SELECT count(*), count(CASE WHEN category='OTP' THEN 1 END) FROM processed_messages")
        row = cur.fetchone()
        if row:
            total_processed, otp_count = row[0], row[1]
        conn.close()
    except Exception:
        pass

    account_lines = []
    for acc in accounts_list:
        status_icon = "[OK]" if acc.get("enabled") else "[OFF]"
        account_lines.append(f"• {status_icon} <code>{acc['email']}</code>")

    accounts_str = "\n".join(account_lines) if account_lines else "<i>Нет подключенных ящиков</i>"

    return (
        f"<b>Аптайм:</b> {uptime_str}\n"
        f"<b>Обработано всего:</b> {total_processed}\n"
        f"<b>Извлечено кодов:</b> {otp_count}\n\n"
        f"<b>Мониторинг ящиков:</b>\n{accounts_str}"
    )

async def search_all_inboxes(accounts_list: List[dict], query: str) -> List[dict]:
    results = []
    for acc in accounts_list:
        try:
            res = await search_emails(acc, query=query, limit=3)
            results.extend(res)
        except Exception as exc:
            logger.warning("Search failed for %s: %s", acc.get("email"), exc)
    return results

async def check_all_spam(accounts_list: List[dict]) -> List[dict]:
    results = []
    for acc in accounts_list:
        try:
            res = await check_spam_folders(acc, limit=3)
            results.extend(res)
        except Exception as exc:
            logger.warning("Spam folder check failed for %s: %s", acc.get("email"), exc)
    return results

async def main():
    config_path = os.environ.get(
        "MAIL_TRIAGE_CONFIG",
        os.path.expanduser("/home/pi/mail-triage-daemon/config/accounts.json")
    )

    if not os.path.exists(config_path):
        local_candidate = os.path.join(os.path.dirname(__file__), "config", "accounts.json")
        if os.path.exists(local_candidate):
            config_path = local_candidate
        else:
            logger.error(
                "Configuration file not found at %s. Please copy accounts.example.json to accounts.json.",
                config_path
            )
            sys.exit(1)

    with open(config_path, "r", encoding="utf-8") as f:
        config_data = json.load(f)

    db_path = config_data.get("db_path", os.path.expanduser("/home/pi/mail-triage-daemon/storage/state.db"))
    init_db(db_path)

    tg_conf = config_data.get("telegram", {})
    bot_token = tg_conf.get("bot_token")
    allowed_user_id = tg_conf.get("allowed_user_id") or tg_conf.get("chat_id") or os.environ.get("TELEGRAM_ALLOWED_USER_ID")

    if not bot_token:
        logger.error("Missing telegram.bot_token in configuration.")
        sys.exit(1)
    if not allowed_user_id:
        logger.error("Missing telegram.allowed_user_id in configuration.")
        sys.exit(1)

    notifier = TelegramNotifier(bot_token=bot_token, allowed_user_id=int(allowed_user_id))

    # Filter enabled accounts
    all_accounts = config_data.get("accounts", [])
    active_accounts = [acc for acc in all_accounts if acc.get("enabled", False)]

    workers: List[ImapAccountWorker] = []
    tasks: List[asyncio.Task] = []

    # Launch Telegram command listener for /status, /otp, /spam, /recent, /search
    status_cb = lambda: get_status_report(all_accounts, db_path)
    otp_cb = lambda service=None: get_latest_otp(service=service, db_path=db_path)
    recent_cb = lambda limit: get_recent_messages(limit=limit, db_path=db_path)
    search_cb = lambda query: search_all_inboxes(active_accounts, query)
    spam_cb = lambda: check_all_spam(active_accounts)

    tasks.append(asyncio.create_task(notifier.start_command_listener(
        status_callback=status_cb,
        otp_callback=otp_cb,
        recent_callback=recent_cb,
        search_callback=search_cb,
        spam_callback=spam_cb
    )))

    for acc in active_accounts:
        worker = ImapAccountWorker(account_config=acc, notifier=notifier, db_path=db_path)
        workers.append(worker)
        tasks.append(asyncio.create_task(worker.run()))

    # Launch embedded REST API for local mesh & Antigravity MCP integration (port 8766)
    api_port = int(config_data.get("api_port", 8766))
    rest_api = MailRestApi(accounts=all_accounts, db_path=db_path, start_time=START_TIME, port=api_port)
    api_runner = await rest_api.start()

    logger.info("Mail Triage Daemon started with %d active mailbox workers.", len(workers))

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _signal_handler():
        logger.info("Received termination signal. Stopping daemon...")
        notifier.stop()
        for w in workers:
            w.stop()
        for t in tasks:
            t.cancel()
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            pass

    await stop_event.wait()
    await api_runner.cleanup()
    logger.info("Daemon gracefully shut down.")

if __name__ == "__main__":
    asyncio.run(main())
