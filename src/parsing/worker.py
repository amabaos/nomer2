import asyncio
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

from loguru import logger
from pyrogram import Client

from src.gateways.telegram_client import make_client
from src.leads.formatter import build_lead_message
from src.leads.notifier import send_lead_html
from src.parsing.filters import is_text_message
from src.core.config import settings

from src.db.database import SessionLocal
from src.db.models import ProcessedMessage, UserChatHit, Blacklist

# НОВОЕ: группы из runtime
from src.groups.runtime import build_chat_to_groups_map, build_group_keywords_map

POLL_INTERVAL = 3


class ParserWorker:
    def __init__(self, account, channels: list[int]):
        self.account = account
        self.channels = channels
        self.last_ids: dict[int, int] = {cid: 0 for cid in channels}

        # Кэши групп (перезагружаются периодически)
        self._chat_to_groups = {}
        self._group_to_keywords = {}
        self._groups_cache_ts = 0.0

    def _refresh_groups_cache(self) -> None:
        """
        Подхватывает изменения в groups.json.
        Чтобы не читать файл на каждое сообщение — обновляем раз в ~15 сек.
        """
        import time
        now = time.time()
        if now - self._groups_cache_ts < 15:
            return
        self._chat_to_groups = build_chat_to_groups_map()
        self._group_to_keywords = build_group_keywords_map()
        self._groups_cache_ts = now

    async def run(self):
        async with make_client(self.account.session_path, self.account.proxy) as app:
            me = await app.get_me()
            logger.info(f"[{self.account.stage}] Запущен под {me.username or me.id}")

            # Встаём на конец истории
            for chat_id in self.channels:
                try:
                    async for msg in app.get_chat_history(chat_id, limit=1):
                        self.last_ids[chat_id] = msg.id
                        break
                except Exception as e:
                    logger.error(f"[{self.account.stage}] init chat {chat_id}: {e}")

            logger.info(f"[{self.account.stage}] Мониторинг запущен")

            while True:
                self._refresh_groups_cache()

                for chat_id in self.channels:
                    try:
                        await self._poll_chat(app, chat_id)
                    except Exception as e:
                        logger.error(f"[{self.account.stage}] чат {chat_id}: {e}")

                await asyncio.sleep(POLL_INTERVAL)

    async def _poll_chat(self, app: Client, chat_id: int):
        last_id = self.last_ids.get(chat_id, 0)
        new_msgs = []

        async for msg in app.get_chat_history(chat_id, limit=25):
            if msg.id <= last_id:
                break
            new_msgs.append(msg)

        if not new_msgs:
            return

        new_msgs.reverse()

        for msg in new_msgs:
            try:
                await self._handle_message(msg)
            except Exception as e:
                logger.error(f"[{self.account.stage}] msg_id={msg.id}: {e}")
            finally:
                # ✅ сдвигаем last_id всегда
                if msg.id > self.last_ids.get(chat_id, 0):
                    self.last_ids[chat_id] = msg.id

    def _match_groups_for_message(self, chat_id: int, text_lower: str) -> Tuple[List[str], Optional[str]]:
        """
        Возвращает:
        - matched_groups: список групп, где совпали keywords
        - matched_keyword: первая совпавшая фраза (для вывода)
        """
        groups = self._chat_to_groups.get(int(chat_id), [])
        if not groups:
            return [], None

        matched = []
        first_kw = None

        for gname in groups:
            kws = self._group_to_keywords.get(gname, [])
            if not kws:
                continue
            for kw in kws:
                if kw and kw in text_lower:
                    matched.append(gname)
                    if first_kw is None:
                        first_kw = kw
                    break

        return matched, first_kw

    async def _handle_message(self, msg) -> bool:
        text = msg.text or ""
        lower = text.lower()

        if not is_text_message(msg):
            return False

        user = msg.from_user
        if not user:
            return False

        user_id = user.id
        chat_id = msg.chat.id

        # 0) Группы/ключи: определяем, подходит ли сообщение хоть под одну группу
        matched_groups, matched_keyword = self._match_groups_for_message(chat_id, lower)
        if not matched_groups:
            return False

        # Для основного поля "Группа" берём первую (а остальные показываем как предупреждение)
        primary_group = matched_groups[0]

        db = SessionLocal()
        try:
            # 1) Blacklist
            if db.query(Blacklist).filter_by(user_id=user_id).first():
                logger.info(f"[{self.account.stage}] user {user_id} в blacklist — пропуск")
                return False

            # 2) Дедуп по сообщению (межаккаунтный)
            exists = db.query(ProcessedMessage).filter_by(
                chat_id=chat_id,
                message_id=msg.id
            ).first()
            if exists:
                return False

            # 3) TTL (можно отключить через ENV)
            ttl_hours = int(getattr(settings, "lead_ttl_hours", 24))
            hit = db.query(UserChatHit).filter_by(
                chat_id=chat_id,
                user_id=user_id
            ).first()

            if ttl_hours > 0:
                now = datetime.utcnow()
                cutoff = now - timedelta(hours=ttl_hours)
                if hit and hit.last_hit_at and hit.last_hit_at > cutoff:
                    logger.info(f"[{self.account.stage}] TTL активен — пропуск")
                    return False

            # 4) Формируем лид
            payload = build_lead_message(
                chat_title=msg.chat.title or "Без названия",
                chat_username=getattr(msg.chat, "username", None),
                chat_id=chat_id,
                author_username=getattr(user, "username", None),
                stage=primary_group,  # ✅ теперь stage = имя группы
                message_text=text,
                message_id=msg.id,
                matched_groups=matched_groups,
                matched_keyword=matched_keyword,
                parser_account=f"#{self.account.id} ({self.account.phone})",
            )

            # 5) Приводим кнопки к формату "строки"
            if payload.get("buttons"):
                payload["buttons"] = [[b] for b in payload["buttons"]]
            else:
                payload["buttons"] = []

            # 6) Добавляем кнопку ЧС отдельной строкой
            payload["buttons"].append([
                {"text": "🚫 В ЧС", "callback_data": f"bl:on:{user_id}"}
            ])

            # 7) Отправляем
            sent_ok = await send_lead_html(
                chat_id=settings.service_chat_id,
                text_html=payload["text"],
                buttons=payload["buttons"],
            )
            if not sent_ok:
                logger.error(f"[{self.account.stage}] send failed chat={chat_id} msg={msg.id}")
                return False

            # 8) Записываем в БД
            db.add(ProcessedMessage(chat_id=chat_id, message_id=msg.id))

            now2 = datetime.utcnow()
            if hit:
                hit.last_hit_at = now2
            else:
                db.add(UserChatHit(chat_id=chat_id, user_id=user_id, last_hit_at=now2))

            db.commit()

            logger.info(f"[{self.account.stage}] ЛИД ОТПРАВЛЕН | group={primary_group}")
            return True

        except Exception:
            db.rollback()
            raise
        finally:
            db.close()