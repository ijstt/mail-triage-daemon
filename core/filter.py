"""
Heuristic filtering rules for marketing, social media notifications, and spam.
"""

from typing import Dict, Any

MARKETING_DOMAINS = [
    "newsletters.", "promo.", "marketing.", "mail.aliexpress.",
    "wildberries.ru", "ozon.ru", "lamoda.ru", "market.yandex.ru",
    "delivery-club.ru", "eda.yandex.ru", "booking.com"
]

NOTIFICATION_DOMAINS = [
    "notifications.google.com", "notification.yandex.ru",
    "no-reply@medium.com", "digest@", "news@", "marketing@"
]

def is_marketing_or_bulk(parsed: Dict[str, Any]) -> bool:
    """
    Return True if email is an automated marketing newsletter or bulk broadcast.
    """
    # 1. Unsubscribe header is the strongest RFC indicator of commercial newsletters
    if parsed.get("list_unsubscribe"):
        return True

    # 2. Bulk or list precedence
    if parsed.get("precedence") in ("bulk", "list", "junk"):
        return True

    from_header = (parsed.get("from") or "").lower()
    subject = (parsed.get("subject") or "").lower()

    # 3. Domain blacklist for marketing
    if any(domain in from_header for domain in MARKETING_DOMAINS):
        return True

    # 4. Standard promotional patterns in Russian and English
    promo_keywords = [
        "скидк", "распродаж", "скидка до", "промокод", "специальное предложение",
        "купон", "только сегодня", "black friday", "cyber monday", "sale",
        "discount", "promo code", "exclusive offer", "неделя скидок"
    ]
    if any(pk in subject for pk in promo_keywords):
        return True

    return False
