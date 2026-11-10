"""
Asynchronous Telegram Bot API notifier with strict single-user isolation.
Ensures private alerts and interactive bot controls are accessible only to the configured owner.
"""

import html
import asyncio
import logging
import aiohttp
from typing import Optional, Dict, Any, Callable, List

logger = logging.getLogger(__name__)

class TelegramNotifier:
    def __init__(self, bot_token: str, allowed_user_id: int):
        self.bot_token = bot_token
        self.allowed_user_id = int(allowed_user_id)
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}"
        self.send_url = f"{self.base_url}/sendMessage"
        self._running = True

    async def setup_bot_commands(self):
        """Register the bot's interactive menu in Telegram client."""
        commands = [
            {"command": "otp", "description": "Показать последний код верификации (2FA)"},
            {"command": "status", "description": "Статус демона и активных ящиков"},
            {"command": "recent", "description": "Последние обработанные письма"},
            {"command": "search", "description": "Поиск по письмам: /search <запрос>"},
            {"command": "help", "description": "Справка по командам"},
            {"command": "ping", "description": "Проверка доступности бота"}
        ]
        try:
            async with aiohttp.ClientSession() as session:
                url = f"{self.base_url}/setMyCommands"
                async with session.post(url, json={"commands": commands}, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        logger.info("Telegram bot commands registered successfully.")
        except Exception as exc:
            logger.warning("Failed to register Telegram bot commands: %s", exc)

    async def send_message(
        self,
        text: str,
        topic_id: Optional[int] = None,
        disable_notification: bool = False
    ) -> bool:
        """
        Send formatted HTML message to Telegram.
        Strictly enforces that target chat is allowed_user_id.
        """
        payload = {
            "chat_id": self.allowed_user_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            "disable_notification": disable_notification
        }
        if topic_id:
            payload["message_thread_id"] = topic_id

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(self.send_url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        return True
                    err_text = await resp.text()
                    logger.error("Failed to send Telegram message: status=%s response=%s", resp.status, err_text)
                    return False
        except Exception as exc:
            logger.error("Network error sending Telegram notification: %s", exc)
            return False

    async def notify_otp(
        self,
        otp_data: Dict[str, Any],
        account_email: str,
        topic_id: Optional[int] = None
    ) -> bool:
        """
        Fast-path delivery of 2FA/verification codes.
        Formatted with <code> for instant 1-tap copy in Telegram.
        """
        code = html.escape(str(otp_data.get("code", "")))
        service = html.escape(str(otp_data.get("service", "Unknown")))
        subject = html.escape(str(otp_data.get("subject", "")))
        email_clean = html.escape(account_email)

        message = (
            f"<b>Код верификации:</b> <code>{code}</code>\n"
            f"<b>Сервис:</b> {service}\n"
            f"<b>Ящик:</b> {email_clean}\n"
            f"<b>Тема:</b> {subject}"
        )
        return await self.send_message(message, topic_id=topic_id, disable_notification=False)

    async def notify_important(
        self,
        triage_data: Dict[str, str],
        parsed: Dict[str, Any],
        account_email: str,
        topic_id: Optional[int] = None
    ) -> bool:
        """Send structured alert for Career, Academic, or Security emails."""
        tag = html.escape(triage_data.get("tag", "Важное письмо"))
        headline = html.escape(triage_data.get("headline", ""))
        subject = html.escape(str(parsed.get("subject", "(Без темы)")))
        sender = html.escape(str(parsed.get("from", "Неизвестный")))
        email_clean = html.escape(account_email)
        snippet = html.escape(str(parsed.get("body_text", "")[:400]).strip())

        cred_lines = []
        if triage_data.get("username"):
            u_clean = html.escape(str(triage_data["username"]))
            cred_lines.append(f"<b>Логин:</b> <code>{u_clean}</code>")
        if triage_data.get("password"):
            p_clean = html.escape(str(triage_data["password"]))
            cred_lines.append(f"<b>Пароль:</b> <code>{p_clean}</code>")
        if triage_data.get("url"):
            url_clean = html.escape(str(triage_data["url"]))
            cred_lines.append(f"<b>Панель управления:</b> {url_clean}")

        cred_block = ("\n" + "\n".join(cred_lines) + "\n") if cred_lines else ""

        message = (
            f"<b>{tag}</b>\n"
            f"<b>{headline}</b>\n\n"
            f"<b>От:</b> {sender}\n"
            f"<b>Ящик:</b> {email_clean}\n"
            f"<b>Тема:</b> {subject}\n"
            f"{cred_block}\n"
            f"<i>{snippet}...</i>"
        )
        return await self.send_message(message, topic_id=topic_id, disable_notification=False)

    async def start_command_listener(
        self,
        status_callback: Optional[Callable[[], str]] = None,
        otp_callback: Optional[Callable[[], Optional[Dict[str, Any]]]] = None,
        recent_callback: Optional[Callable[[int], List[Dict[str, Any]]]] = None,
        search_callback: Optional[Callable[[str], Any]] = None
    ):
        """
        Background listener for incoming Telegram commands.
        Strictly ignores/drops any messages from users other than allowed_user_id.
        """
        await self.setup_bot_commands()

        offset = 0
        get_updates_url = f"{self.base_url}/getUpdates"

        while self._running:
            try:
                async with aiohttp.ClientSession() as session:
                    params = {"offset": offset, "timeout": 25}
                    async with session.get(get_updates_url, params=params, timeout=aiohttp.ClientTimeout(total=35)) as resp:
                        if resp.status != 200:
                            await asyncio.sleep(5)
                            continue

                        data = await resp.json()
                        if not data.get("ok"):
                            await asyncio.sleep(5)
                            continue

                        for update in data.get("result", []):
                            offset = max(offset, update["update_id"] + 1)
                            msg = update.get("message")
                            if not msg:
                                continue

                            sender_id = msg.get("from", {}).get("id")

                            # Security gate: Drop all external users without answering
                            if sender_id != self.allowed_user_id:
                                logger.warning("Unauthorized access attempt from user_id=%s. Ignored.", sender_id)
                                continue

                            # Handle commands from authorized owner
                            text = (msg.get("text") or "").strip()

                            if text.startswith("/status"):
                                status_text = status_callback() if status_callback else "Демон активен."
                                await self.send_message(f"<b>Статус Juhan Mail:</b>\n{status_text}")

                            elif text.startswith("/otp"):
                                if otp_callback:
                                    otp = otp_callback()
                                    if otp:
                                        age = otp.get("age_seconds", 0)
                                        m, s = divmod(age, 60)
                                        age_str = f"{m}м {s}с" if m else f"{s}с"
                                        msg_out = (
                                            f"<b>Последний проверочный код:</b>\n"
                                            f"<code>{html.escape(str(otp['code']))}</code>\n\n"
                                            f"<b>Сервис:</b> {html.escape(str(otp.get('service', 'Unknown')))}\n"
                                            f"<b>Ящик:</b> {html.escape(str(otp.get('account_id', '')))}\n"
                                            f"<b>Тема:</b> {html.escape(str(otp.get('subject', '')))}\n"
                                            f"<b>Получен:</b> {age_str} назад"
                                        )
                                    else:
                                        msg_out = "<i>Свежих кодов подтверждения за последние 15 минут не обнаружено.</i>"
                                    await self.send_message(msg_out)
                                else:
                                    await self.send_message("Модуль OTP недоступен.")

                            elif text.startswith("/recent"):
                                if recent_callback:
                                    items = recent_callback(5)
                                    if not items:
                                        await self.send_message("<i>Журнал обработанных писем пуст.</i>")
                                    else:
                                        lines = ["<b>Последние обработанные письма:</b>\n"]
                                        for i, it in enumerate(items, 1):
                                            cat = html.escape(str(it.get("category", "GENERAL")))
                                            subj = html.escape(str(it.get("subject", "")))
                                            acc = html.escape(str(it.get("account_id", "")))
                                            age = it.get("age_seconds", 0)
                                            m, s = divmod(age, 60)
                                            age_str = f"{m}м назад" if m else f"{s}с назад"
                                            lines.append(f"{i}. <b>[{cat}]</b> {subj}\n   <i>{acc} • {age_str}</i>")
                                        await self.send_message("\n".join(lines))
                                else:
                                    await self.send_message("Журнал недоступен.")

                            elif text.startswith("/search"):
                                parts = text.split(maxsplit=1)
                                if len(parts) < 2 or not parts[1].strip():
                                    await self.send_message(
                                        "<b>Использование:</b> <code>/search &lt;запрос&gt;</code>\n"
                                        "<b>Пример:</b> <code>/search Google</code>"
                                    )
                                else:
                                    query_str = parts[1].strip()
                                    await self.send_message(f"Ищу письма по запросу «{html.escape(query_str)}»...")
                                    if search_callback:
                                        try:
                                            results = await search_callback(query_str)
                                            if not results:
                                                await self.send_message("Писем по данному запросу не найдено.")
                                            else:
                                                lines = [f"<b>Результаты поиска ({len(results)}):</b>\n"]
                                                for idx, item in enumerate(results[:5], 1):
                                                    s_subj = html.escape(str(item.get("subject", "")))
                                                    s_from = html.escape(str(item.get("from", "")))
                                                    s_acc = html.escape(str(item.get("account_email", "")))
                                                    s_snip = html.escape(str(item.get("snippet", ""))[:150])
                                                    lines.append(
                                                        f"{idx}. <b>{s_subj}</b>\n"
                                                        f"   <b>От:</b> {s_from}\n"
                                                        f"   <b>Ящик:</b> {s_acc}\n"
                                                        f"   <i>{s_snip}...</i>\n"
                                                    )
                                                await self.send_message("\n".join(lines))
                                        except Exception as s_err:
                                            await self.send_message(f"Ошибка при поиске: {html.escape(str(s_err))}")
                                    else:
                                        await self.send_message("Поиск по ящикам не настроен.")

                            elif text.startswith("/ping"):
                                await self.send_message("pong")

                            elif text.startswith("/start"):
                                await self.send_message(
                                    "<b>Juhan Mail Triage Agent</b> активен.\n"
                                    "Доступ закреплен за вашим аккаунтом.\n"
                                    "Используйте /help для просмотра списка команд."
                                )

                            elif text.startswith("/help"):
                                help_text = (
                                    "<b>Справка по командам Juhan Mail:</b>\n\n"
                                    "• /otp — Показать последний проверочный код (2FA/OTP)\n"
                                    "• /status — Uptime, счетчики обработанных писем и статус соединений\n"
                                    "• /recent — Показать последние обработанные письма\n"
                                    "• /search &lt;запрос&gt; — Быстрый поиск писем по теме и тексту\n"
                                    "• /ping — Проверка отклика бота\n"
                                    "• /help — Данная справка\n\n"
                                    "<i>Все коды подтверждения и важные письма приходят автоматически.</i>"
                                )
                                await self.send_message(help_text)

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.debug("Updates polling error: %s", exc)
                await asyncio.sleep(5)

    def stop(self):
        self._running = False
