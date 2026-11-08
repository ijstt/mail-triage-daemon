"""
Unit tests for deterministic OTP extraction and email classification rules.
"""

from core.otp import detect_otp
from core.filter import is_marketing_or_bulk
from core.triage import classify_important_email

def test_otp_detection_russian():
    subject = "Код подтверждения для входа"
    body = "Здравствуйте! Ваш код подтверждения: 582910. Никому не сообщайте его."
    from_header = "СберБанк <auth@sber.ru>"

    result = detect_otp(subject, body, from_header)
    assert result is not None
    assert result["code"] == "582910"
    assert "СберБанк" in result["service"]

def test_otp_detection_english_google():
    subject = "Your Google verification code"
    body = "Use 930184 to verify your Google Account."
    from_header = "Google <no-reply@accounts.google.com>"

    result = detect_otp(subject, body, from_header)
    assert result is not None
    assert result["code"] == "930184"
    assert "Google" in result["service"]

def test_otp_false_positive_exclusion():
    # Regular email talking about conference 2026 without OTP keywords
    subject = "Отчет по исследовательской работе"
    body = "В 2026 году запланировано проведение конференции."
    from_header = "Иван Иванов <ivan@sfedu.ru>"

    result = detect_otp(subject, body, from_header)
    assert result is None

def test_marketing_filter():
    parsed = {
        "list_unsubscribe": "<mailto:unsub@promo.market.ru>",
        "precedence": "bulk",
        "subject": "Скидки до 70% на черную пятницу!",
        "from": "Маркет <promo@market.ru>"
    }
    assert is_marketing_or_bulk(parsed) is True

def test_career_classification():
    parsed = {
        "subject": "Приглашение на техническое собеседование: Senior ML Engineer",
        "from": "HR Команда <hr@tech-corp.com>",
        "body_text": "Добрый день! Ознакомились с вашим профилем, приглашаем на онлайн-интервью."
    }
    match = classify_important_email(parsed)
    assert match is not None
    assert match["category"] == "CAREER"

def test_academic_classification():
    parsed = {
        "subject": "Ведомость по научно-исследовательской практике",
        "from": "Учебная часть <office@university.edu>",
        "body_text": "Уважаемые студенты, необходимо сдать ведомости до пятницы."
    }
    match = classify_important_email(parsed)
    assert match is not None
    assert match["category"] == "ACADEMIC"

def test_otp_flexible_descriptors():
    subject = "Одноразовый код подтверждения для входа"
    body = "Здравствуйте!\n\nВаш код подтверждения учетной записи: 849201\nДействителен 10 минут."
    from_header = "Auth Service <auth@cloud.io>"

    match = detect_otp(subject, body, from_header)
    assert match is not None
    assert match["code"] == "849201"
