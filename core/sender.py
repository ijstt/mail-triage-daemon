"""
Asynchronous SMTP email sender module supporting SSL and STARTTLS.
"""

import smtplib
import asyncio
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formatdate, make_msgid
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

DEFAULT_SMTP_HOSTS = {
    "imap.gmail.com": ("smtp.gmail.com", 465, True),
    "imap.yandex.ru": ("smtp.yandex.ru", 465, True),
    "mail.sfedu.ru": ("mail.sfedu.ru", 465, True)
}

def _send_sync(
    smtp_host: str,
    smtp_port: int,
    use_ssl: bool,
    sender_email: str,
    password: str,
    to_email: str,
    subject: str,
    body: str,
    html_body: Optional[str] = None
) -> Dict[str, Any]:
    msg = MIMEMultipart("alternative")
    msg["From"] = sender_email
    msg["To"] = to_email
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid()

    # Plain text alternative
    msg.attach(MIMEText(body, "plain", "utf-8"))
    if html_body:
        msg.attach(MIMEText(html_body, "html", "utf-8"))

    server = None
    try:
        if use_ssl:
            server = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=20)
        else:
            server = smtplib.SMTP(smtp_host, smtp_port, timeout=20)
            server.starttls()

        server.login(sender_email, password)
        server.send_message(msg)
        return {
            "success": True,
            "message_id": msg["Message-ID"],
            "recipient": to_email
        }
    except Exception as exc:
        logger.error("Failed to send email to %s: %s", to_email, exc)
        return {
            "success": False,
            "error": str(exc),
            "recipient": to_email
        }
    finally:
        if server:
            try:
                server.quit()
            except Exception:
                pass

async def send_email(
    account_config: Dict[str, Any],
    to: str,
    subject: str,
    body: str,
    html_body: Optional[str] = None
) -> Dict[str, Any]:
    """Asynchronous wrapper for sending emails via account SMTP settings."""
    sender_email = account_config["email"]
    password = account_config["password"]
    imap_host = account_config.get("host", "imap.gmail.com")

    # Resolve SMTP host and port
    if "smtp_host" in account_config:
        smtp_host = account_config["smtp_host"]
        smtp_port = account_config.get("smtp_port", 465)
        use_ssl = account_config.get("smtp_ssl", True)
    elif imap_host in DEFAULT_SMTP_HOSTS:
        smtp_host, smtp_port, use_ssl = DEFAULT_SMTP_HOSTS[imap_host]
    else:
        # Generic fallback: replace imap. with smtp.
        smtp_host = imap_host.replace("imap.", "smtp.")
        smtp_port = 465
        use_ssl = True

    return await asyncio.to_thread(
        _send_sync,
        smtp_host=smtp_host,
        smtp_port=smtp_port,
        use_ssl=use_ssl,
        sender_email=sender_email,
        password=password,
        to_email=to,
        subject=subject,
        body=body,
        html_body=html_body
    )
