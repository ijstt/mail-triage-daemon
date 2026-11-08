"""
Semantic triage classifier for non-OTP emails.
Identifies Career, Academic, Security, and Important Transactions.
"""

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

FINANCE_KEYWORDS = [
    "оплата", "счет", "квитанция", "чек", "перевод", "пополнение", "списание",
    "payment", "invoice", "receipt", "bank", "банк", "тинькофф", "т-банк", "сбер"
]

def classify_important_email(parsed: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """
    Classify important non-OTP emails.
    Returns category and summary dict if significant, or None if ordinary notice.
    """
    subject = (parsed.get("subject") or "").lower()
    from_header = (parsed.get("from") or "").lower()
    body_snippet = (parsed.get("body_text") or "")[:2000].lower()

    text_to_search = f"{subject} {from_header} {body_snippet}"

    # 1. Career & Job Opportunities
    if any(kw in text_to_search for kw in CAREER_KEYWORDS):
        return {
            "category": "CAREER",
            "tag": "💼 Карьера и Офферы",
            "headline": "Приглашение / Вакансия / Сообщение от HR"
        }

    # 2. Academic / University Notices
    if any(kw in text_to_search for kw in ACADEMIC_KEYWORDS) or "sfedu.ru" in from_header or ".edu" in from_header:
        return {
            "category": "ACADEMIC",
            "tag": "🎓 Университет",
            "headline": "Уведомление от университета / деканата"
        }

    # 3. Critical Security Alerts
    if any(kw in text_to_search for kw in SECURITY_KEYWORDS):
        return {
            "category": "SECURITY",
            "tag": "🚨 Безопасность",
            "headline": "Оповещение о безопасности аккаунта"
        }

    # 4. Financial Transactions
    if any(kw in text_to_search for kw in FINANCE_KEYWORDS):
        return {
            "category": "FINANCE",
            "tag": "💳 Финансы",
            "headline": "Платеж / Чек / Финансовое уведомление"
        }

    return None
