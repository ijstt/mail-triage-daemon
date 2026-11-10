"""
MIME parsing and body text normalization utilities.
"""

import email
import email.message
from email.header import decode_header
from typing import Dict, Any, Tuple
from bs4 import BeautifulSoup

def decode_mime_header(header_value: str) -> str:
    """Decode encoded MIME header strings (e.g. =?UTF-8?B?...?=)."""
    if not header_value:
        return ""
    try:
        decoded_parts = decode_header(header_value)
        result = []
        for content, charset in decoded_parts:
            if isinstance(content, bytes):
                try:
                    encoding = charset or "utf-8"
                    result.append(content.decode(encoding, errors="replace"))
                except (LookupError, UnicodeDecodeError):
                    result.append(content.decode("utf-8", errors="replace"))
            else:
                result.append(str(content))
        return "".join(result).strip()
    except Exception:
        return str(header_value)

def extract_body(msg: email.message.Message) -> Tuple[str, str]:
    """
    Extract plain text and HTML bodies from an email message.
    Returns (plain_text, html_text).
    """
    plain_text = ""
    html_text = ""

    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition", ""))

            if "attachment" in content_disposition:
                continue

            try:
                payload = part.get_payload(decode=True)
                if not payload:
                    continue
                charset = part.get_content_charset() or "utf-8"
                try:
                    decoded = payload.decode(charset, errors="replace")
                except (LookupError, UnicodeDecodeError):
                    decoded = payload.decode("utf-8", errors="replace")

                if content_type == "text/plain" and not plain_text:
                    plain_text = decoded
                elif content_type == "text/html" and not html_text:
                    html_text = decoded
            except Exception:
                continue
    else:
        content_type = msg.get_content_type()
        try:
            payload = msg.get_payload(decode=True)
            if payload:
                charset = msg.get_content_charset() or "utf-8"
                try:
                    decoded = payload.decode(charset, errors="replace")
                except (LookupError, UnicodeDecodeError):
                    decoded = payload.decode("utf-8", errors="replace")

                if content_type == "text/plain":
                    plain_text = decoded
                elif content_type == "text/html":
                    html_text = decoded
        except Exception:
            pass

    # If only HTML is available, strip markup to construct plain text
    if not plain_text and html_text:
        try:
            soup = BeautifulSoup(html_text, "html.parser")
            for tag in soup(["script", "style", "meta", "noscript"]):
                tag.decompose()
            plain_text = soup.get_text(separator="\n", strip=True)
        except Exception:
            plain_text = html_text

    return plain_text.strip(), html_text.strip()

def normalize_text(text: str) -> str:
    """Strip zero-width non-printable characters and normalize non-breaking whitespace."""
    if not text:
        return ""
    import re
    cleaned = re.sub(r"[\u200b-\u200d\ufeff\u00ad]", "", text)
    cleaned = cleaned.replace("\xa0", " ")
    return cleaned

def parse_email_bytes(raw_bytes: bytes) -> Dict[str, Any]:
    """Parse raw RFC822 bytes into a structured dictionary."""
    msg = email.message_from_bytes(raw_bytes)

    message_id = msg.get("Message-ID", "").strip()
    subject = normalize_text(decode_mime_header(msg.get("Subject", "")))
    from_header = decode_mime_header(msg.get("From", ""))
    to_header = decode_mime_header(msg.get("To", ""))
    date_header = msg.get("Date", "")

    # Headers signaling automated/bulk delivery
    list_unsubscribe = msg.get("List-Unsubscribe", "")
    precedence = msg.get("Precedence", "").lower()
    auto_submitted = msg.get("Auto-Submitted", "").lower()

    plain_text, html_text = extract_body(msg)

    return {
        "message_id": message_id,
        "subject": subject,
        "from": from_header,
        "to": to_header,
        "date": date_header,
        "list_unsubscribe": list_unsubscribe,
        "precedence": precedence,
        "auto_submitted": auto_submitted,
        "body_text": normalize_text(plain_text),
        "body_html": html_text
    }

