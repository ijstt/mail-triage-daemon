"""
Model Context Protocol (MCP) Server for Antigravity.
Connects Antigravity agent directly to Mail Triage Daemon on Raspberry Pi.
Uses httpx for HTTP calls.
"""

import os
import json
import httpx
from fastmcp import FastMCP

mcp = FastMCP("MailTriage")

DAEMON_URL = os.environ.get("MAIL_DAEMON_URL", "http://127.0.0.1:8766")

@mcp.tool()
def mail_get_latest_otp(service_name: str = "") -> str:
    """
    Retrieve the most recent OTP or 2FA verification code received in the last 15 minutes.
    Use this when completing registrations or logins that send a confirmation code via email.
    """
    try:
        params = {}
        if service_name:
            params["service"] = service_name
        with httpx.Client(timeout=5.0, trust_env=False) as client:
            r = client.get(f"{DAEMON_URL}/api/otp/latest", params=params)
            if r.status_code == 200:
                data = r.json()
                otp = data.get("otp", {})
                return (
                    f"Service: {otp.get('service', 'Unknown')}\n"
                    f"Code: {otp.get('code')}\n"
                    f"Account: {otp.get('account_id')}\n"
                    f"Received: {otp.get('age_seconds')} seconds ago\n"
                    f"Subject: {otp.get('subject')}"
                )
            return "No recent OTP codes found."
    except Exception as exc:
        return f"Error communicating with mail daemon at {DAEMON_URL}: {exc}"

@mcp.tool()
def mail_search_messages(query: str, account_id: str = "", limit: int = 5) -> str:
    """
    Search incoming messages across connected inboxes by sender, subject, or query string.
    """
    try:
        payload = {"query": query, "limit": limit}
        if account_id:
            payload["account_id"] = account_id
        with httpx.Client(timeout=20.0, trust_env=False) as client:
            r = client.post(f"{DAEMON_URL}/api/mail/search", json=payload)
            if r.status_code == 200:
                data = r.json()
                results = data.get("results", [])
                if not results:
                    return "No matching emails found."
                lines = []
                for item in results:
                    lines.append(
                        f"[{item.get('account_email')}] From: {item.get('from')}\n"
                        f"Subject: {item.get('subject')}\n"
                        f"Date: {item.get('date')}\n"
                        f"Snippet: {item.get('snippet')}\n"
                        "---"
                    )
                return "\n".join(lines)
            return f"Search failed (HTTP {r.status_code}): {r.text}"
    except Exception as exc:
        return f"Error communicating with mail daemon at {DAEMON_URL}: {exc}"

@mcp.tool()
def mail_send_message(to: str, subject: str, body: str, account_id: str = "") -> str:
    """
    Send an email via authenticated SMTP server from a configured mailbox.
    """
    try:
        payload = {"to": to, "subject": subject, "body": body}
        if account_id:
            payload["account_id"] = account_id
        with httpx.Client(timeout=20.0, trust_env=False) as client:
            r = client.post(f"{DAEMON_URL}/api/mail/send", json=payload)
            if r.status_code == 200:
                data = r.json()
                return f"Email successfully sent. Message-ID: {data.get('message_id')}"
            return f"Failed to send email (HTTP {r.status_code}): {r.text}"
    except Exception as exc:
        return f"Error communicating with mail daemon at {DAEMON_URL}: {exc}"

@mcp.tool()
def mail_get_system_status() -> str:
    """
    Return health, uptime, and active connection status of all monitored mailboxes.
    """
    try:
        with httpx.Client(timeout=5.0, trust_env=False) as client:
            r = client.get(f"{DAEMON_URL}/api/status")
            if r.status_code == 200:
                return json.dumps(r.json(), indent=2, ensure_ascii=False)
    except Exception as exc:
        return f"Mail daemon unreachable at {DAEMON_URL}: {exc}"

@mcp.tool()
def mail_list_recent(limit: int = 10, category: str = "") -> str:
    """
    List recently received and triaged messages (OTP, CAREER, ACADEMIC, SECURITY, GENERAL).
    """
    try:
        params = {"limit": limit}
        if category:
            params["category"] = category
        with httpx.Client(timeout=5.0, trust_env=False) as client:
            r = client.get(f"{DAEMON_URL}/api/mail/recent", params=params)
            if r.status_code == 200:
                data = r.json()
                messages = data.get("messages", [])
                if not messages:
                    return "No recent messages in log."
                lines = []
                for item in messages:
                    age = item.get("age_seconds", 0)
                    m, s = divmod(age, 60)
                    age_str = f"{m}m ago" if m else f"{s}s ago"
                    lines.append(
                        f"[{item.get('category')}] {item.get('subject')}\n"
                        f"Account: {item.get('account_id')} | Received: {age_str}\n"
                        f"Message-ID: {item.get('message_id')}\n"
                        "---"
                    )
                return "\n".join(lines)
            return f"Failed to retrieve recent messages (HTTP {r.status_code}): {r.text}"
    except Exception as exc:
        return f"Error communicating with mail daemon at {DAEMON_URL}: {exc}"

if __name__ == "__main__":
    mcp.run()
