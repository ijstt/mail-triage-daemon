"""
Asynchronous IMAP worker supporting IDLE push notifications, UID-based sync, and robust reconnects.
"""

import time
import asyncio
import logging
from typing import Dict, Any, Optional, List
import aioimaplib

from core.parser import parse_email_bytes
from core.otp import detect_otp
from core.filter import is_marketing_or_bulk
from core.triage import classify_important_email
from storage.db import is_processed, mark_processed, record_otp, get_last_seen_uid, update_last_seen_uid
from notify.telegram import TelegramNotifier

logger = logging.getLogger(__name__)

MAX_SESSION_SECONDS = 1800   # Reconnect cleanly every 30 minutes to prevent stale NAT/proxy connections
WATCHDOG_PING_SECONDS = 300  # Send NOOP keepalive every 5 minutes if idle
IDLE_TICK_SECONDS = 25       # Tick idle loop every 25 seconds

class ImapAccountWorker:
    def __init__(self, account_config: Dict[str, Any], notifier: TelegramNotifier, db_path: Optional[str] = None):
        self.config = account_config
        self.account_id = account_config["id"]
        self.email = account_config["email"]
        self.host = account_config.get("host", "imap.gmail.com")
        self.port = account_config.get("port", 993)
        self.ssl = account_config.get("ssl", True)
        self.password = account_config["password"]
        self.notifier = notifier
        self.db_path = db_path
        self._running = True

    async def run(self):
        """Main lifecycle loop with automatic reconnection."""
        logger.info("Starting worker for %s (%s)", self.account_id, self.email)
        retry_delay = 5

        while self._running:
            client = None
            try:
                if self.ssl:
                    client = aioimaplib.IMAP4_SSL(host=self.host, port=self.port, timeout=30)
                else:
                    client = aioimaplib.IMAP4(host=self.host, port=self.port, timeout=30)

                await asyncio.wait_for(client.wait_hello_from_server(), timeout=15)
                login_res = await asyncio.wait_for(client.login(self.email, self.password), timeout=20)
                if login_res.result != "OK":
                    logger.error("Authentication failed for %s: %s", self.email, login_res)
                    try:
                        err_lines = " ".join(str(l) for l in getattr(login_res, "lines", [])) if hasattr(login_res, "lines") else str(login_res)
                        await self.notifier.notify_auth_failure(
                            account_email=self.email,
                            error_msg=err_lines,
                            topic_id=self.config.get("topic_important")
                        )
                    except Exception as notify_err:
                        logger.error("Failed to deliver auth failure alert: %s", notify_err)
                    await asyncio.sleep(60)
                    continue

                select_res = await asyncio.wait_for(client.select("INBOX"), timeout=15)
                if select_res.result != "OK":
                    logger.error("Failed to select INBOX for %s: %s", self.email, select_res)
                    await asyncio.sleep(30)
                    continue

                logger.info("Connected & authenticated to %s", self.email)
                retry_delay = 5  # Reset retry delay on successful connection

                # Initial check for messages upon connecting
                await self._check_messages(client, initial=True)

                session_start = time.time()
                last_ping = time.time()

                # Enter IDLE loop with fallback polling & keepalive watchdog
                while self._running:
                    # 1. Lifetime check
                    if time.time() - session_start > MAX_SESSION_SECONDS:
                        logger.info("Session lifetime limit reached (30m) for %s. Cycling connection cleanly...", self.email)
                        break

                    # 2. Watchdog keepalive ping
                    if time.time() - last_ping > WATCHDOG_PING_SECONDS:
                        try:
                            noop_res = await asyncio.wait_for(client.noop(), timeout=10)
                            if noop_res.result != "OK":
                                logger.warning("Keepalive NOOP failed for %s: %s. Reconnecting...", self.email, noop_res)
                                break
                            last_ping = time.time()
                        except Exception as noop_err:
                            logger.warning("Keepalive NOOP timed out or failed for %s: %s. Reconnecting...", self.email, noop_err)
                            break

                    # 3. IDLE tick with bounded timeout
                    try:
                        idle_task = await asyncio.wait_for(client.idle_start(timeout=120), timeout=15)
                        try:
                            await client.wait_server_push(timeout=IDLE_TICK_SECONDS)
                        except (asyncio.TimeoutError, TimeoutError):
                            pass
                        finally:
                            client.idle_done()
                            try:
                                await asyncio.wait_for(idle_task, timeout=10)
                            except Exception:
                                pass
                    except asyncio.CancelledError:
                        raise
                    except Exception as idle_err:
                        logger.warning("IDLE error for %s: %s. Forcing fresh reconnect...", self.email, idle_err)
                        break

                    # 4. Process new arrivals (UID-based + UNSEEN)
                    await self._check_messages(client)

            except asyncio.CancelledError:
                logger.info("Worker for %s cancelled.", self.email)
                self._running = False
                break
            except Exception as exc:
                logger.warning("Connection error for %s: %s. Reconnecting in %ds...", self.email, exc, retry_delay)
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 120)
            finally:
                if client:
                    try:
                        await asyncio.wait_for(client.logout(), timeout=5)
                    except Exception:
                        pass

    async def _check_messages(self, client: aioimaplib.IMAP4_SSL, initial: bool = False):
        """Fetch and evaluate new messages using UID tracking and UNSEEN fallback."""
        try:
            last_uid = get_last_seen_uid(self.account_id, self.db_path)

            # Discover highest UID in mailbox
            all_res = await asyncio.wait_for(client.uid_search("ALL", charset=None), timeout=15)
            if all_res.result != "OK" or not all_res.lines or not all_res.lines[0]:
                return

            all_uids = [int(u) for u in all_res.lines[0].split() if u.isdigit()]
            if not all_uids:
                return

            highest_uid = max(all_uids)

            # Cold start: if no prior UID recorded, start from max(0, highest_uid - 15) to cover immediate arrivals
            if last_uid == 0:
                last_uid = max(0, highest_uid - 15)
                update_last_seen_uid(self.account_id, last_uid, self.db_path)
                logger.info("Initialized UID tracker for %s at UID %d (highest: %d)", self.account_id, last_uid, highest_uid)

            # 1. Query messages with UID > last_uid
            uids_to_check: List[int] = []
            if highest_uid > last_uid:
                range_res = await asyncio.wait_for(client.uid_search("UID", f"{last_uid + 1}:*", charset=None), timeout=15)
                if range_res.result == "OK" and range_res.lines and range_res.lines[0]:
                    range_uids = [int(u) for u in range_res.lines[0].split() if u.isdigit()]
                    for u in range_uids:
                        if u > last_uid and u not in uids_to_check:
                            uids_to_check.append(u)

            # 2. Fallback: also query UNSEEN from the last 2 days (captures moved or backfilled unread messages)
            from datetime import datetime, timezone, timedelta
            since_date = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%d-%b-%Y")
            unseen_res = await asyncio.wait_for(client.uid_search(f'(UNSEEN SINCE "{since_date}")', charset=None), timeout=15)
            if unseen_res.result == "OK" and unseen_res.lines and unseen_res.lines[0]:
                unseen_uids = [int(u) for u in unseen_res.lines[0].split() if u.isdigit()]
                for u in unseen_uids:
                    if u not in uids_to_check:
                        uids_to_check.append(u)

            if not uids_to_check:
                return

            uids_to_check.sort()
            logger.info("Found %d candidate message(s) to process in %s (UIDs: %s)", len(uids_to_check), self.email, uids_to_check[:10])

            for uid in uids_to_check:
                try:
                    fetch_res = await asyncio.wait_for(client.uid("FETCH", str(uid), "(RFC822)"), timeout=20)
                    if fetch_res.result != "OK" or not fetch_res.lines:
                        continue

                    raw_email = None
                    for part in fetch_res.lines:
                        if isinstance(part, tuple) and len(part) >= 2 and isinstance(part[1], (bytes, bytearray)):
                            raw_email = bytes(part[1])
                            break
                        elif isinstance(part, (bytes, bytearray)) and len(part) > 50:
                            raw_email = bytes(part)
                            break

                    if raw_email:
                        await self._process_message(raw_email)

                    # Advance UID pointer
                    if uid > last_uid:
                        last_uid = uid
                        update_last_seen_uid(self.account_id, last_uid, self.db_path)

                except Exception as msg_err:
                    logger.error("Failed to fetch/process message UID %d in %s: %s", uid, self.email, msg_err)

            # Final update of tracked UID
            if highest_uid > last_uid:
                update_last_seen_uid(self.account_id, highest_uid, self.db_path)

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Error during message check for %s: %s", self.email, exc)
            raise  # Re-raise so worker's inner loop can break and cleanly reconnect

    async def _process_message(self, raw_bytes: bytes):
        """Process a single email through the triage pipeline."""
        try:
            parsed = parse_email_bytes(raw_bytes)
            message_id = parsed["message_id"] or f"{self.email}_{parsed['subject']}_{parsed['date']}"

            # Idempotency check: never process the same message_id twice
            if is_processed(self.account_id, message_id, self.db_path):
                return

            # Tier 0: Fast-Path OTP check (< 1ms)
            otp_match = detect_otp(parsed["subject"], parsed["body_text"], parsed["from"])
            if otp_match:
                logger.info("OTP detected in %s: %s from %s", self.email, otp_match["code"], otp_match["service"])
                await self.notifier.notify_otp(
                    otp_data=otp_match,
                    account_email=self.email,
                    topic_id=self.config.get("topic_otp")
                )
                record_otp(self.account_id, otp_match["service"], otp_match["code"], parsed["subject"], self.db_path)
                mark_processed(self.account_id, message_id, "OTP", parsed["subject"], self.db_path)
                return

            # Tier 1: Critical Credentials & Security check (BEFORE marketing filter)
            triage_match = classify_important_email(parsed)
            if triage_match and triage_match.get("category") in ("CREDENTIALS", "SECURITY"):
                logger.info("High-priority %s detected in %s: %s", triage_match["category"], self.email, triage_match["tag"])
                await self.notifier.notify_important(
                    triage_data=triage_match,
                    parsed=parsed,
                    account_email=self.email,
                    topic_id=self.config.get("topic_important")
                )
                mark_processed(self.account_id, message_id, triage_match["category"], parsed["subject"], self.db_path)
                return

            # Tier 2: Discard-Path for bulk / marketing
            if is_marketing_or_bulk(parsed):
                logger.debug("Discarded bulk/marketing mail in %s: %s", self.email, parsed["subject"])
                mark_processed(self.account_id, message_id, "MARKETING", parsed["subject"], self.db_path)
                return

            # Tier 3: Semantic triage (Career, Academic, Finance)
            if triage_match:
                logger.info("Important email detected in %s: %s (%s)", self.email, triage_match["category"], triage_match["tag"])
                await self.notifier.notify_important(
                    triage_data=triage_match,
                    parsed=parsed,
                    account_email=self.email,
                    topic_id=self.config.get("topic_important")
                )
                mark_processed(self.account_id, message_id, triage_match["category"], parsed["subject"], self.db_path)
                return

            # Fallback: normal personal/unclassified email (record without notification)
            mark_processed(self.account_id, message_id, "GENERAL", parsed["subject"], self.db_path)

        except Exception as exc:
            logger.error("Failed to process message in %s: %s", self.email, exc)

    def stop(self):
        self._running = False
