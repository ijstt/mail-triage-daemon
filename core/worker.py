"""
Asynchronous IMAP worker supporting IDLE push notifications and robust reconnects.
"""

import asyncio
import logging
from typing import Dict, Any, Optional
import aioimaplib

from core.parser import parse_email_bytes
from core.otp import detect_otp
from core.filter import is_marketing_or_bulk
from core.triage import classify_important_email
from storage.db import is_processed, mark_processed, record_otp
from notify.telegram import TelegramNotifier

logger = logging.getLogger(__name__)

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

                await client.wait_hello_from_server()
                login_res = await client.login(self.email, self.password)
                if login_res.result != "OK":
                    logger.error("Authentication failed for %s: %s", self.email, login_res)
                    await asyncio.sleep(60)
                    continue

                select_res = await client.select("INBOX")
                if select_res.result != "OK":
                    logger.error("Failed to select INBOX for %s: %s", self.email, select_res)
                    await asyncio.sleep(30)
                    continue

                logger.info("Connected & authenticated to %s", self.email)
                retry_delay = 5  # Reset retry delay on successful connection

                # Initial check for unread messages upon connecting
                await self._check_unseen(client)

                # Enter IDLE loop with fallback polling
                while self._running:
                    try:
                        idle_task = await client.idle_start(timeout=120)
                        try:
                            await client.wait_server_push(timeout=20)
                        except (asyncio.TimeoutError, TimeoutError):
                            pass
                        finally:
                            client.idle_done()
                            try:
                                await asyncio.wait_for(idle_task, timeout=10)
                            except Exception:
                                pass

                        # Process any new arrivals
                        await self._check_unseen(client)
                    except asyncio.CancelledError:
                        raise
                    except Exception as loop_err:
                        logger.warning("IDLE loop tick error for %s: %s", self.email, loop_err)
                        await asyncio.sleep(2)

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
                        await client.logout()
                    except Exception:
                        pass

    async def _check_unseen(self, client: aioimaplib.IMAP4_SSL):
        """Fetch and evaluate recent unseen messages."""
        try:
            from datetime import datetime, timezone, timedelta
            # Only inspect unread emails from the last 2 days to ignore ancient backlogs
            since_date = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%d-%b-%Y")
            res, data = await client.search(f'(UNSEEN SINCE "{since_date}")')
            if res != "OK" or not data or not data[0]:
                return

            msg_ids = data[0].split()
            if not msg_ids:
                return

            total_unseen = len(msg_ids)
            logger.info("Found %d recent unread message(s) in %s (since %s)", total_unseen, self.email, since_date)

            for msg_id in msg_ids:
                fetch_res, fetch_data = await client.fetch(msg_id.decode("ascii"), "(RFC822)")
                if fetch_res != "OK" or not fetch_data:
                    continue

                raw_email = None
                for part in fetch_data:
                    if isinstance(part, tuple) and len(part) >= 2 and isinstance(part[1], (bytes, bytearray)):
                        raw_email = bytes(part[1])
                        break
                    elif isinstance(part, (bytes, bytearray)) and len(part) > 50:
                        raw_email = bytes(part)
                        break

                if not raw_email:
                    continue

                await self._process_message(raw_email)

        except Exception as exc:
            logger.error("Error during _check_unseen for %s: %s", self.email, exc)

    async def _process_message(self, raw_bytes: bytes):
        """Process a single email through the triage pipeline."""
        try:
            parsed = parse_email_bytes(raw_bytes)
            message_id = parsed["message_id"] or f"{self.email}_{parsed['subject']}_{parsed['date']}"

            # Idempotency check
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

            # Tier 1: Discard-Path for bulk / marketing
            if is_marketing_or_bulk(parsed):
                logger.debug("Discarded bulk/marketing mail in %s: %s", self.email, parsed["subject"])
                mark_processed(self.account_id, message_id, "MARKETING", parsed["subject"], self.db_path)
                return

            # Tier 2: Semantic triage (Career, Academic, Security, Finance)
            triage_match = classify_important_email(parsed)
            if triage_match:
                logger.info("Important email detected in %s: %s", self.email, triage_match["tag"])
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
