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

def test_otp_steam_guard():
    subject = "Your Steam account: Access from new web or mobile device"
    body = (
        "Dear user,\n\n"
        "It looks like you are trying to log in from a new device. Here is the Steam "
        "Guard code you need to access your account:\n\n"
        "Login Code\n"
        "TCB87\n\n"
        "If this wasn't you, please reset your password."
    )
    from_header = '"Steam Support" <noreply@steampowered.com>'

    match = detect_otp(subject, body, from_header)
    assert match is not None
    assert match["code"] == "TCB87"
    assert "Steam" in match["service"]

def test_otp_github_sudo():
    subject = "[GitHub] Sudo email verification code"
    body = (
        "Hey, ijstt!\n\n"
        "Here is your GitHub sudo authentication code: 54109541\n"
        "This code is valid for 15 minutes and can only be used once."
    )
    from_header = "GitHub <noreply@github.com>"

    match = detect_otp(subject, body, from_header)
    assert match is not None
    assert match["code"] == "54109541"
    assert "GitHub" in match["service"]

def test_credentials_vdsina():
    parsed = {
        "subject": "VDSina.com registration",
        "from": '"VDSina.com" <support@vdsina.com>',
        "body_text": (
            "You have successfully registered in the control panel VDSina.com!\n\n"
            "- Your username: vladimir.fedorko225@gmail.com\n\n"
            "- Password for the first login: K6P4#1wV^j-3pz161AZ4\n\n"
            "Control panel: https://cp.vdsina.com/\n\n"
            "Help and support https://vdsina.com/qa/"
        )
    }
    match = classify_important_email(parsed)
    assert match is not None
    assert match["category"] == "CREDENTIALS"
    assert match["username"] == "vladimir.fedorko225@gmail.com"
    assert match["password"] == "K6P4#1wV^j-3pz161AZ4"
    assert match["url"] == "https://cp.vdsina.com/"
    # Verify ZERO emojis in tag
    assert not any(ord(c) > 0x1F000 for c in match["tag"])

