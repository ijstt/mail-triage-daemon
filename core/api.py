"""
Embedded HTTP REST API for mail triage daemon.
Enables local network observability and Antigravity MCP integration.
"""

import time
import logging
from aiohttp import web
from typing import Dict, Any, List

from storage.db import get_latest_otp, get_recent_messages
from core.searcher import search_emails
from core.sender import send_email

logger = logging.getLogger(__name__)

class MailRestApi:
    def __init__(self, accounts: List[Dict[str, Any]], db_path: str, start_time: float, port: int = 8766):
        self.accounts = accounts
        self.db_path = db_path
        self.start_time = start_time
        self.port = port
        self.app = web.Application()
        self._setup_routes()

    def _setup_routes(self):
        self.app.router.add_get("/health", self.handle_health)
        self.app.router.add_get("/api/status", self.handle_status)
        self.app.router.add_get("/api/otp/latest", self.handle_latest_otp)
        self.app.router.add_get("/api/mail/recent", self.handle_recent)
        self.app.router.add_post("/api/mail/search", self.handle_search)
        self.app.router.add_post("/api/mail/send", self.handle_send)

    async def handle_health(self, request: web.Request) -> web.Response:
        """Healthcheck probe for supervisor and monitoring systems."""
        uptime = int(time.time() - self.start_time)
        return web.json_response({
            "status": "healthy",
            "service": "mail-triage-daemon",
            "uptime_seconds": uptime,
            "monitored_accounts": len(self.accounts)
        })

    async def handle_status(self, request: web.Request) -> web.Response:
        """Detailed status endpoint."""
        uptime = int(time.time() - self.start_time)
        acc_summary = []
        for acc in self.accounts:
            acc_summary.append({
                "id": acc.get("id"),
                "email": acc.get("email"),
                "host": acc.get("host"),
                "enabled": acc.get("enabled", False)
            })

        return web.json_response({
            "status": "online",
            "uptime_seconds": uptime,
            "accounts": acc_summary
        })

    async def handle_latest_otp(self, request: web.Request) -> web.Response:
        """Fetch latest verification/2FA code."""
        service = request.query.get("service")
        max_age_str = request.query.get("max_age", "900")
        try:
            max_age = int(max_age_str)
        except ValueError:
            max_age = 900

        otp = get_latest_otp(service=service, max_age_seconds=max_age, db_path=self.db_path)
        if not otp:
            return web.json_response({"error": "No recent OTP found", "found": False}, status=404)

        return web.json_response({"found": True, "otp": otp})

    async def handle_recent(self, request: web.Request) -> web.Response:
        """Fetch recent processed messages log."""
        category = request.query.get("category")
        try:
            limit = min(int(request.query.get("limit", 10)), 50)
        except ValueError:
            limit = 10

        messages = get_recent_messages(limit=limit, category=category, db_path=self.db_path)
        return web.json_response({"messages": messages, "count": len(messages)})

    async def handle_search(self, request: web.Request) -> web.Response:
        """Search messages across inboxes."""
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON body"}, status=400)

        query = body.get("query", "")
        sender = body.get("sender")
        account_id = body.get("account_id")
        limit = body.get("limit", 5)

        target_accounts = []
        if account_id:
            target_accounts = [acc for acc in self.accounts if acc.get("id") == account_id and acc.get("enabled")]
        else:
            target_accounts = [acc for acc in self.accounts if acc.get("enabled")]

        if not target_accounts:
            return web.json_response({"error": "No enabled accounts matching criteria"}, status=404)

        all_results = []
        for acc in target_accounts:
            res = await search_emails(acc, query=query, sender=sender, limit=limit)
            all_results.extend(res)

        return web.json_response({"total": len(all_results), "results": all_results})

    async def handle_send(self, request: web.Request) -> web.Response:
        """Send an email via authenticated SMTP."""
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON body"}, status=400)

        account_id = body.get("account_id")
        to = body.get("to")
        subject = body.get("subject")
        text_body = body.get("body")
        html_body = body.get("html")

        if not (to and subject and text_body):
            return web.json_response({"error": "Missing required fields: to, subject, body"}, status=400)

        # Select account
        acc = None
        if account_id:
            acc = next((a for a in self.accounts if a.get("id") == account_id and a.get("enabled")), None)
        else:
            # Default to the first enabled account
            acc = next((a for a in self.accounts if a.get("enabled")), None)

        if not acc:
            return web.json_response({"error": "No enabled sending account available"}, status=400)

        res = await send_email(acc, to=to, subject=subject, body=text_body, html_body=html_body)
        if res.get("success"):
            return web.json_response(res, status=200)
        else:
            return web.json_response(res, status=500)

    async def start(self) -> web.AppRunner:
        runner = web.AppRunner(self.app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", self.port)
        await site.start()
        logger.info("Mail REST API listening on 0.0.0.0:%d", self.port)
        return runner
