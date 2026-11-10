"""
Semantic triage classifier for non-OTP emails.
Identifies Career, Academic, Security, and Important Transactions.
"""

import re
from typing import Dict, Any, Optional

CAREER_KEYWORDS = [
    "резюме", "отклик", "собеседование", "ваканси", "оффер", "интервью",
    "приглашение на работу", "recruiter", "hiring", "headhunter", "hh.ru",
    "habr career", "хабр карьера", "linkedin", "job offer", "technical interview",
    "screen", "кандидат", "рекрутер"
]

ACADEMIC_KEYWORDS = [
    "деканат", "кафедра", "преподаватель", "сессия", "зачет", "экзамен",
    "ведомост", "контрольная", "лабораторн", "курсовая", "диплом", "научный руководитель",
    "юфу", "sfedu", "университет", "студент", "академическ", "лекция", "семинар"
]

SECURITY_KEYWORDS = [
    "вход с нового устройства", "подозрительная попытка", "пароль был изменен",
    "новый вход", "security alert", "suspicious activity", "new sign-in",
    "login from new device", "compromised", "unauthorized"
]

CREDENTIALS_KEYWORDS = [
    "registration", "регистраци", "данные для входа", "пароль для входа",
    "пароль для первого входа", "пароль от", "учетные данные", "access credentials",
    "login details", "password for the first login", "your credentials",
    "успешно зарегистрирован", "данные учетной записи", "control panel", "панель управления",
    "server access", "ssh access", "доступ к серверу"
]

FINANCE_KEYWORDS = [
    "оплата", "счет", "квитанция", "чек", "перевод", "пополнение", "списание",
    "payment", "invoice", "receipt", "bank", "банк", "тинькофф", "т-банк", "сбер"
]

def extract_credentials_details(body_text: str) -> Dict[str, str]:
    """Extract username, password, and control panel URL from email body if present."""
    details = {}
    if not body_text:
        return details

    # Username / Login
    u_match = re.search(r"(?:username|логин|пользователь|login)\s*[:\-]?\s*([^\s\r\n]+)", body_text, re.IGNORECASE)
    if u_match:
        details["username"] = u_match.group(1).strip()

    # Password (including complex generated passwords)
    p_match = re.search(r"(?:password(?:\s+for[^\r\n:]*)?|пароль(?:\s+для[^\r\n:]*)?)\s*[:\-]?\s*([^\s\r\n]+)", body_text, re.IGNORECASE)
    if p_match:
        cand_pwd = p_match.group(1).strip()
        if cand_pwd.lower() not in ("is", "was", "has", "reset", "changed"):
            details["password"] = cand_pwd

    # URL / Control panel link
    url_match = re.search(r"(https?://[^\s\r\n\)\"'>]+)", body_text)
    if url_match:
        details["url"] = url_match.group(1).strip()

    return details

def classify_important_email(parsed: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Classify important non-OTP emails.
    Returns category and summary dict if significant, or None if ordinary notice.
    """
    subject = (parsed.get("subject") or "").lower()
    from_header = (parsed.get("from") or "").lower()
    body_snippet = (parsed.get("body_text") or "")[:2000].lower()

    text_to_search = f"{subject} {from_header} {body_snippet}"

    # 1. Account Registration & Credentials
    if any(kw in text_to_search for kw in CREDENTIALS_KEYWORDS):
        cred_details = extract_credentials_details(parsed.get("body_text") or "")
        res = {
            "category": "CREDENTIALS",
            "tag": "[ДОСТУПЫ] Регистрация и Доступы",
            "headline": "Данные для входа / Регистрация аккаунта"
        }
        res.update(cred_details)
        return res

    # 2. Career & Job Opportunities
    if any(kw in text_to_search for kw in CAREER_KEYWORDS):
        return {
            "category": "CAREER",
            "tag": "[КАРЬЕРА] Карьера и Офферы",
            "headline": "Приглашение / Вакансия / Сообщение от HR"
        }

    # 3. Academic / University Notices
    if any(kw in text_to_search for kw in ACADEMIC_KEYWORDS) or "sfedu.ru" in from_header or ".edu" in from_header:
        return {
            "category": "ACADEMIC",
            "tag": "[УНИВЕРСИТЕТ] Университет",
            "headline": "Уведомление от университета / деканата"
        }

    # 4. Critical Security Alerts
    if any(kw in text_to_search for kw in SECURITY_KEYWORDS):
        return {
            "category": "SECURITY",
            "tag": "[БЕЗОПАСНОСТЬ] Безопасность",
            "headline": "Оповещение о безопасности аккаунта"
        }

    # 5. Financial Transactions
    if any(kw in text_to_search for kw in FINANCE_KEYWORDS):
        return {
            "category": "FINANCE",
            "tag": "[ФИНАНСЫ] Финансы",
            "headline": "Платеж / Чек / Финансовое уведомление"
        }

    return None

