"""
High-performance deterministic OTP and verification code extractor.
Zero LLM latency (< 1ms execution time).
"""

import re
from typing import Optional, Dict, Any
from email.utils import parseaddr

OTP_SUBJECT_KEYWORDS = [
    "код", "code", "verification", "verify", "подтвержд",
    "одноразов", "парол", "password reset", "сброс",
    "авторизац", "security", "безопасн", "2fa", "otp", "pin"
]

OTP_BODY_PATTERNS = [
    # Explicit phrases with flexible descriptor (e.g. "код подтверждения учетной записи: 123456")
    r"(?:код|code|пароль|password)(?:[^\d\n\r]{1,50})?[:\s-]\s*([0-9]{4,8})\b",
    r"(?:use\s+code|enter\s+code|code\s+is):?\s*([0-9]{4,8})\b",
    r"\bcode\s*[:\-]?\s*([0-9]{4,8})\b",
    r"\bкод\s*[:\-]?\s*([0-9]{4,8})\b",
    # Specific services (e.g. Steam Guard: 5 alphanumeric characters)
    r"\b(?:steam\s+guard|steam\s+code):?\s*([A-Z0-9]{5})\b",
    # Standalone 6-digit number in verified OTP context
    r"\b([0-9]{6})\b",
    # Standalone 4-8 digit number
    r"\b([0-9]{4,8})\b",
]

def extract_service_name(from_header: str) -> str:
    """Extract clean service name from From header."""
    name, addr = parseaddr(from_header)
    if name:
        clean_name = name.strip("\"' ")
        if clean_name:
            return clean_name
    if "@" in addr:
        domain = addr.split("@")[-1].lower()
        parts = domain.split(".")
        if len(parts) >= 2:
            return parts[-2].capitalize()
    return from_header or "Unknown Service"

def detect_otp(subject: str, body_text: str, from_header: str) -> Optional[Dict[str, Any]]:
    """
    Evaluate if an email is an OTP/verification code.
    Returns structured dict if matched, None otherwise.
    """
    subject_lower = (subject or "").lower()
    body_clean = (body_text or "").replace("\\n", "\n").replace("\\r", "\r")
    body_lower = body_clean.lower()

    # Fast subject check
    is_subject_otp = any(kw in subject_lower for kw in OTP_SUBJECT_KEYWORDS)

    # Check for keywords in first 1500 chars of body
    body_snippet = body_lower[:1500]
    is_body_otp = any(kw in body_snippet for kw in OTP_SUBJECT_KEYWORDS)

    if not (is_subject_otp or is_body_otp):
        return None

    # Search for code patterns
    code = None
    for pattern in OTP_BODY_PATTERNS:
        match = re.search(pattern, body_clean, re.IGNORECASE | re.MULTILINE)
        if match:
            candidate = match.group(1).strip()
            # Exclude current and near years as false positives
            if candidate in ("2023", "2024", "2025", "2026", "2027"):
                continue
            code = candidate
            break

    # If code not found in body, check subject
    if not code:
        match = re.search(r"\b([0-9]{4,8})\b", subject or "")
        if match:
            cand = match.group(1).strip()
            if cand not in ("2023", "2024", "2025", "2026", "2027"):
                code = cand

    if not code:
        return None

    service = extract_service_name(from_header)
    return {
        "code": code,
        "service": service,
        "subject": subject or "(No Subject)",
        "from": from_header
    }
