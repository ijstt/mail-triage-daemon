"""
High-performance deterministic OTP and verification code extractor.
Zero LLM latency (< 1ms execution time).
"""

import re
from typing import Optional, Dict, Any
from email.utils import parseaddr

OTP_SUBJECT_KEYWORDS = [
    "код", "code", "verification", "verify", "подтвержд",
    "одноразов", "password reset", "сброс", "2fa", "otp", "pin",
    "steam account", "steam guard", "login code", "код доступа",
    "код входа", "sudo authentication", "authentication code"
]

OTP_BODY_KEYWORDS = [
    "код подтверждения", "verification code", "authentication code",
    "security code", "login code", "steam guard", "одноразовый пароль",
    "one-time password", "use code", "enter code", "ваш код", "your code",
    "код доступа", "код входа", "sudo code", "2fa"
]

OTP_ANCHORED_PATTERNS = [
    # Dedicated Steam Guard / alphanumeric login code pattern (e.g. Login Code\nTCB87)
    r"(?:\b(?:login\s+code|steam\s+guard(?:\s+code)?|steam\s+code|security\s+code|код\s+доступа|код\s+входа):?\s*(?:\r?\n)?\s*([A-Z0-9]{5})\b)",
    # Explicit phrases with flexible descriptor and optional newline (e.g. "код подтверждения:\n 123456")
    r"(?:код|code|пароль|password)(?:[^\d\n\r]{1,50})?[:\s-]\s*(?:\r?\n)?\s*([0-9]{4,8})\b",
    r"(?:use\s+code|enter\s+code|code\s+is|your\s+code\s+is|ваш\s+код|is\s+your\s+code):?\s*(?:\r?\n)?\s*([0-9]{4,8})\b",
    r"(?:authentication\s+code|verification\s+code|security\s+code|sudo\s+code|confirmation\s+code):?\s*(?:\r?\n)?\s*([0-9]{4,8})\b",
    r"\bcode\s*[:\-]?\s*(?:\r?\n)?\s*([0-9]{4,8})\b",
    r"\bкод\s*[:\-]?\s*(?:\r?\n)?\s*([0-9]{4,8})\b",
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
    Strictly avoids false positives from newsletter/footer text (e.g. US zip codes).
    """
    subject_lower = (subject or "").lower()
    body_clean = (body_text or "").replace("\\n", "\n").replace("\\r", "\r")
    body_lower = body_clean.lower()

    # Fast subject check
    is_subject_otp = any(kw in subject_lower for kw in OTP_SUBJECT_KEYWORDS)

    # Check for specific OTP keywords in first 1500 chars of body
    body_snippet = body_lower[:1500]
    is_body_otp = any(kw in body_snippet for kw in OTP_BODY_KEYWORDS)

    if not (is_subject_otp or is_body_otp):
        return None

    code = None

    # 1. Search for anchored patterns (highest confidence)
    for pattern in OTP_ANCHORED_PATTERNS:
        match = re.search(pattern, body_clean, re.IGNORECASE | re.MULTILINE)
        if match:
            candidate = match.group(1).strip()
            # Exclude current and near years as false positives
            if candidate not in ("2023", "2024", "2025", "2026", "2027"):
                code = candidate
                break

    # 2. Standalone number search: ONLY permitted when subject explicitly indicates OTP
    # (Prevents matching US zip codes like Menlo Park 94025 in marketing footers)
    if not code and is_subject_otp:
        # Check body for standalone 4-8 digit number
        match = re.search(r"\b([0-9]{4,8})\b", body_clean)
        if match:
            cand = match.group(1).strip()
            if cand not in ("2023", "2024", "2025", "2026", "2027"):
                code = cand

        # Check subject itself for code
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
