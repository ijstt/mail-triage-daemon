"""
Asynchronous IMAP search module to query emails by text, sender, or date.
"""

import imaplib
import asyncio
import logging
from typing import Dict, Any, List, Optional
from core.parser import parse_email_bytes

logger = logging.getLogger(__name__)

def _search_sync(
    account_config: Dict[str, Any],
    query: str,
    sender: Optional[str] = None,
    limit: int = 5
) -> List[Dict[str, Any]]:
    host = account_config.get("host", "imap.gmail.com")
    port = account_config.get("port", 993)
    ssl = account_config.get("ssl", True)
    email_user = account_config["email"]
    password = account_config["password"]

    client = None
    results = []
    try:
        if ssl:
            client = imaplib.IMAP4_SSL(host, port, timeout=20)
        else:
            client = imaplib.IMAP4(host, port, timeout=20)

        client.login(email_user, password)
        client.select("INBOX", readonly=True)

        is_ascii = all(ord(c) < 128 for c in (query or ""))
        if is_ascii:
            search_criteria = []
            if sender:
                search_criteria.append(f'FROM "{sender}"')
            if query:
                search_criteria.append(f'(OR (SUBJECT "{query}") (BODY "{query}"))')

            search_str = " ".join(search_criteria) if search_criteria else "ALL"
            status, data = client.search(None, search_str)
        else:
            # RFC 3501 UTF-8 literal search for non-ASCII character sets
            client.literal = query.strip().encode("utf-8")
            status, data = client.search("utf-8", "SUBJECT")

        if status != "OK" or not data or not data[0]:
            return []

        msg_ids = data[0].split()
        # Take the most recent 'limit' messages
        recent_ids = msg_ids[-limit:]
        recent_ids.reverse()

        for msg_id in recent_ids:
            fetch_st, fetch_data = client.fetch(msg_id, "(RFC822)")
            if fetch_st != "OK" or not fetch_data:
                continue

            raw_bytes = None
            for part in fetch_data:
                if isinstance(part, tuple) and len(part) >= 2:
                    raw_bytes = part[1]
                    break

            if not raw_bytes:
                continue

            parsed = parse_email_bytes(raw_bytes)
            results.append({
                "account_id": account_config["id"],
                "account_email": email_user,
                "message_id": parsed["message_id"],
                "subject": parsed["subject"],
                "from": parsed["from"],
                "to": parsed["to"],
                "date": parsed["date"],
                "snippet": parsed["body_text"][:300].strip()
            })

    except Exception as exc:
        logger.error("IMAP search failed for %s: %s", email_user, exc)
    finally:
        if client:
            try:
                client.logout()
            except Exception:
                pass

    return results

async def search_emails(
    account_config: Dict[str, Any],
    query: str,
    sender: Optional[str] = None,
    limit: int = 5
) -> List[Dict[str, Any]]:
    return await asyncio.to_thread(
        _search_sync,
        account_config=account_config,
        query=query,
        sender=sender,
        limit=limit
    )
