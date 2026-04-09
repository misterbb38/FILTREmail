

# """
# Классификация писем Yandex через DeepSeek AI.
# Только письма за сегодня. Только заявки клиентов → папка Заявки.
# Остальные письма не трогаем.

# v5: Мульти-ящик — обработка нескольких почтовых ящиков по очереди.
#     MongoDB для хранения обработанных писем, dotenv, Render-ready.

# Запуск: python email_classifier.py
# Остановка: Ctrl+C
# """

# import imaplib
# import email
# from email.header import decode_header
# import json
# import os
# import logging
# import base64
# import re
# import signal
# import time
# from datetime import datetime, date
# from dotenv import load_dotenv
# from openai import OpenAI
# from pymongo import MongoClient

# # ============================================================
# # ЗАГРУЗКА .env
# # ============================================================
# load_dotenv()

# # ============================================================
# # НАСТРОЙКИ ИЗ ПЕРЕМЕННЫХ ОКРУЖЕНИЯ
# # ============================================================

# IMAP_SERVER = "imap.yandex.ru"
# IMAP_PORT = 993

# DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
# DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")

# MONGO_URI = os.getenv("MONGO_URI")
# MONGO_DB = os.getenv("MONGO_DB", "email_classifier")

# TARGET_FOLDER = "Заявки"
# SOURCE_FOLDER = "INBOX"
# MAX_EMAILS_PER_RUN = 50

# DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"
# CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "120"))
# TEST_MODE = os.getenv("TEST_MODE", "false").lower() == "true"
# TEST_EMAIL = os.getenv("TEST_EMAIL", "")

# # ============================================================
# # КОНФИГУРАЦИЯ ПОЧТОВЫХ ЯЩИКОВ
# # ============================================================

# def load_mailboxes() -> list[dict]:
#     """
#     Загружает список почтовых ящиков из переменных окружения.

#     Формат:
#       MAILBOX_1_LOGIN=info@strbr.ru
#       MAILBOX_1_PASSWORD=xxxx
#       MAILBOX_2_LOGIN=info@twowin.ru
#       MAILBOX_2_PASSWORD=xxxx
#       ...

#     Возвращает список словарей: [{"login": "...", "password": "..."}, ...]
#     """
#     mailboxes = []
#     i = 1
#     while True:
#         login = os.getenv(f"MAILBOX_{i}_LOGIN")
#         password = os.getenv(f"MAILBOX_{i}_PASSWORD")
#         if not login:
#             break
#         if not password:
#             log.warning(f"⚠ MAILBOX_{i}_LOGIN={login} задан, но MAILBOX_{i}_PASSWORD отсутствует — пропускаем")
#             i += 1
#             continue
#         mailboxes.append({"login": login, "password": password})
#         i += 1

#     # Обратная совместимость: если MAILBOX_* не заданы, берём старые переменные
#     if not mailboxes:
#         legacy_login = os.getenv("YANDEX_LOGIN")
#         legacy_password = os.getenv("YANDEX_PASSWORD")
#         if legacy_login and legacy_password:
#             mailboxes.append({"login": legacy_login, "password": legacy_password})

#     return mailboxes


# # ============================================================
# # ПРОВЕРКА ОБЯЗАТЕЛЬНЫХ ПЕРЕМЕННЫХ
# # ============================================================

# _missing = []
# if not DEEPSEEK_API_KEY:
#     _missing.append("DEEPSEEK_API_KEY")
# if not MONGO_URI:
#     _missing.append("MONGO_URI")
# if TEST_MODE and not TEST_EMAIL:
#     _missing.append("TEST_EMAIL (обязателен при TEST_MODE=true)")
# if _missing:
#     raise SystemExit(f"❌ Отсутствуют переменные окружения: {', '.join(_missing)}\n"
#                      f"   Создайте файл .env или задайте их в Environment на Render.")

# # ============================================================
# # ЛОГИРОВАНИЕ
# # ============================================================

# logging.basicConfig(
#     level=logging.INFO,
#     format="%(asctime)s [%(levelname)s] %(message)s",
#     handlers=[
#         logging.StreamHandler()
#     ]
# )
# log = logging.getLogger(__name__)

# # ============================================================
# # ПРОВЕРКА ЯЩИКОВ (после инициализации логгера)
# # ============================================================

# MAILBOXES = load_mailboxes()
# if not MAILBOXES:
#     raise SystemExit(
#         "❌ Не найдены почтовые ящики.\n"
#         "   Задайте MAILBOX_1_LOGIN / MAILBOX_1_PASSWORD (и далее 2, 3...)\n"
#         "   или YANDEX_LOGIN / YANDEX_PASSWORD для одного ящика."
#     )

# # ============================================================
# # MONGODB
# # ============================================================

# mongo_client = MongoClient(MONGO_URI)
# db = mongo_client[MONGO_DB]
# processed_col = db["processed_emails"]

# # Индекс для быстрого поиска + TTL автоочистка через 30 дней
# processed_col.create_index("uid", unique=True)
# processed_col.create_index("processed_at", expireAfterSeconds=30 * 24 * 3600)


# def make_uid_key(mailbox_login: str, uid_str: str) -> str:
#     """Уникальный ключ = ящик + UID (чтобы UID из разных ящиков не пересекались)."""
#     return f"{mailbox_login}:{uid_str}"


# def is_processed(uid_key: str) -> bool:
#     return processed_col.find_one({"uid": uid_key}) is not None


# def mark_processed(uid_key: str, mailbox: str, sender: str, subject: str,
#                    is_order: bool, confidence: float, reason: str):
#     processed_col.update_one(
#         {"uid": uid_key},
#         {"$set": {
#             "uid": uid_key,
#             "mailbox": mailbox,
#             "sender": sender,
#             "subject": subject,
#             "is_order": is_order,
#             "confidence": confidence,
#             "reason": reason,
#             "processed_at": datetime.utcnow(),
#         }},
#         upsert=True
#     )


# # ============================================================
# # DEEPSEEK КЛИЕНТ
# # ============================================================

# client = OpenAI(
#     api_key=DEEPSEEK_API_KEY,
#     base_url=DEEPSEEK_BASE_URL,
# )


# # ============================================================
# # IMAP UTF-7 ENCODING
# # ============================================================

# def encode_imap_utf7(text: str) -> str:
#     result = []
#     non_ascii_buffer = ""
#     for char in text:
#         if 0x20 <= ord(char) <= 0x7e:
#             if non_ascii_buffer:
#                 utf16 = non_ascii_buffer.encode("utf-16-be")
#                 b64 = base64.b64encode(utf16).decode("ascii").rstrip("=")
#                 b64 = b64.replace("/", ",")
#                 result.append("&" + b64 + "-")
#                 non_ascii_buffer = ""
#             if char == "&":
#                 result.append("&-")
#             else:
#                 result.append(char)
#         else:
#             non_ascii_buffer += char
#     if non_ascii_buffer:
#         utf16 = non_ascii_buffer.encode("utf-16-be")
#         b64 = base64.b64encode(utf16).decode("ascii").rstrip("=")
#         b64 = b64.replace("/", ",")
#         result.append("&" + b64 + "-")
#     return "".join(result)


# # ============================================================
# # ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# # ============================================================

# def decode_mime_header(header_value: str) -> str:
#     if not header_value:
#         return ""
#     parts = decode_header(header_value)
#     decoded = []
#     for part, charset in parts:
#         if isinstance(part, bytes):
#             decoded.append(part.decode(charset or "utf-8", errors="replace"))
#         else:
#             decoded.append(part)
#     return " ".join(decoded)


# def extract_email_address(from_header: str) -> str:
#     """Извлекает чистый email из заголовка From."""
#     match = re.search(r'[\w.+-]+@[\w.-]+\.\w+', from_header)
#     return match.group(0).lower() if match else from_header.lower()


# def extract_text_from_email(msg) -> str:
#     text_parts = []
#     if msg.is_multipart():
#         for part in msg.walk():
#             content_type = part.get_content_type()
#             if content_type == "text/plain":
#                 payload = part.get_payload(decode=True)
#                 if payload:
#                     charset = part.get_content_charset() or "utf-8"
#                     text_parts.append(payload.decode(charset, errors="replace"))
#             elif content_type == "text/html" and not text_parts:
#                 payload = part.get_payload(decode=True)
#                 if payload:
#                     charset = part.get_content_charset() or "utf-8"
#                     text_parts.append(payload.decode(charset, errors="replace"))
#     else:
#         payload = msg.get_payload(decode=True)
#         if payload:
#             charset = msg.get_content_charset() or "utf-8"
#             text_parts.append(payload.decode(charset, errors="replace"))
#     return "\n".join(text_parts)


# def get_attachments_info(msg) -> list[str]:
#     attachments = []
#     if msg.is_multipart():
#         for part in msg.walk():
#             filename = part.get_filename()
#             if filename:
#                 attachments.append(decode_mime_header(filename))
#     return attachments


# def fetch_email_by_uid(mail: imaplib.IMAP4_SSL, uid: bytes):
#     status, data = mail.uid("FETCH", uid, "(BODY.PEEK[])")
#     if status != "OK":
#         return None
#     for item in data:
#         if isinstance(item, tuple) and len(item) == 2 and isinstance(item[1], bytes):
#             try:
#                 return email.message_from_bytes(item[1])
#             except Exception as e:
#                 log.warning(f"  Ошибка парсинга: {e}")
#                 return None
#     return None


# def is_test_email(sender: str, msg) -> bool:
#     sender_addr = extract_email_address(sender)
#     if TEST_EMAIL.lower() in sender_addr:
#         return True

#     reply_to = msg.get("Reply-To", "")
#     if TEST_EMAIL.lower() in reply_to.lower():
#         return True

#     body = extract_text_from_email(msg)
#     if TEST_EMAIL.lower() in body.lower():
#         return True

#     return False


# # ============================================================
# # КЛАССИФИКАЦИЯ: заявка клиента или нет?
# # ============================================================

# CLASSIFICATION_SYSTEM_PROMPT = """Ты — эксперт-классификатор входящей почты компании TWOWIN (СтройБразерс).

# TWOWIN — дистрибьютор строительных материалов в Екатеринбурге. Компания ПОКУПАЕТ товар у ПОСТАВЩИКОВ и ПРОДАЁТ его КЛИЕНТАМ (строительные компании, подрядчики, магазины, частные лица).

# Твоя задача: определить, является ли письмо ЗАЯВКОЙ КЛИЕНТА (т.е. входящим запросом на покупку от клиента TWOWIN).

# ВАЖНО: Анализируй СМЫСЛ письма целиком, а не отдельные слова. Одно и то же слово "заказ" может быть в заявке клиента и в уведомлении от поставщика — ты должен понять КОНТЕКСТ.

# Отвечай СТРОГО в формате JSON (без markdown, без комментариев):
# {"is_order": true/false, "confidence": 0.0-1.0, "reason": "краткое пояснение на русском"}"""

# CLASSIFICATION_USER_PROMPT = """Проанализируй это письмо.

# КОНТЕКСТ: {mailbox_email} — это почта TWOWIN. Письмо пришло НА этот адрес. Определи: это заявка клиента или нет?

# ═══════════════════════════════════════
# ДАННЫЕ ПИСЬМА:
# ═══════════════════════════════════════
# От: {sender}
# Тема: {subject}
# Вложения: {attachments}

# Текст письма:
# ---
# {body}
# ---

# ═══════════════════════════════════════
# ПРАВИЛА КЛАССИФИКАЦИИ:
# ═══════════════════════════════════════

# ✅ ЭТО ЗАЯВКА КЛИЕНТА (is_order: true), если:

# 1. ПРЯМОЙ ЗАКАЗ: Клиент (не поставщик!) хочет КУПИТЬ у TWOWIN товар/материалы.
#    Признаки: "нужен", "требуется", "закажем", "хотим заказать", "прошу отгрузить",
#    "выставьте счёт", "подготовьте КП", список товаров с количеством.

# 2. ЗАПРОС ЦЕНЫ/НАЛИЧИЯ: Клиент спрашивает цену, наличие, сроки доставки товара,
#    просит прайс или коммерческое предложение на конкретные позиции.

# 3. СПЕЦИФИКАЦИЯ: Во вложении файл-заявка (Excel, PDF со списком товаров),
#    сводный заказ покупателя, спецификация от клиента.

# 4. ОТВЕТ КЛИЕНТА В ЦЕПОЧКЕ ЗАКАЗА: Если тема письма содержит Re:/RE:/FW: и в теме
#    есть слова "счёт", "заказ", "заявка", "КП", "спецификация", "прайс", "отгрузка"
#    — то ЛЮБОЙ ответ от клиента является заявкой. Даже короткий:
#    "Ок", "Хорошо", "Да", "Согласен", "Принято", "Берём", "Давайте", "Смотри",
#    "Подтверждаю", "+", "Ждём", "Оплатили", или просто одно слово.
#    ЛОГИКА: клиент продолжает диалог по заказу → это часть заказа → в CRM.
#    Единственное исключение: если отправитель — ПОСТАВЩИК (не клиент).

# 5. ПОДБОР АНАЛОГА: Клиент просит подобрать аналог, замену, альтернативу товара.

# ❌ ЭТО НЕ ЗАЯВКА (is_order: false), если:

# 1. ПОСТАВЩИК пишет нам (прайс от поставщика, уведомление об отгрузке в наш адрес,
#    изменение цен поставщика). Признак: мы — ПОКУПАТЕЛЬ в этом письме.

# 2. РЕКЛАМА / СПАМ / РАССЫЛКА: маркетинговые материалы, подписки, новости отрасли.

# 3. СИСТЕМНЫЕ УВЕДОМЛЕНИЯ: Битрикс24, 1С, CRM, мониторинг, автоматика.

# 4. БУХГАЛТЕРИЯ без заказа: акт сверки, запрос закрывающих документов, сверка оплат,
#    запрос счёт-фактуры по уже закрытой сделке.

# 5. РЕКЛАМАЦИЯ / ВОЗВРАТ: жалоба на качество, запрос возврата, претензия.

# 6. ЛОГИСТИКА без нового заказа: трекинг, уведомление о доставке, вопрос "где груз?",
#    вопросы по машине/газели, время разгрузки, пропуск, данные водителя.

# 7. ОБЩАЯ ПЕРЕПИСКА: вопросы не связанные с покупкой товара, приветствия,
#    организационные вопросы, договоры без конкретного заказа.

# 8. ВНУТРЕННЯЯ ПОЧТА: письма от сотрудников TWOWIN/СтройБразерс друг другу.

# ═══════════════════════════════════════
# КАК ОТЛИЧИТЬ КЛИЕНТА ОТ ПОСТАВЩИКА:
# ═══════════════════════════════════════
# - КЛИЕНТ хочет КУПИТЬ У НАС → заявка
# - ПОСТАВЩИК предлагает НАМ КУПИТЬ → НЕ заявка
# - Если в письме "ваш заказ №..." и контекст показывает, что это ПОСТАВЩИК
#   сообщает нам о нашем заказе у него → НЕ заявка
# - Если "заказ" в контексте "я хочу заказать у вас" → заявка

# КРИТИЧЕСКИ ВАЖНО — ШАБЛОН ОТВЕТА ПОСТАВЩИКА:
# Если в цепочке (цитата внизу письма) видно сообщение ОТ сотрудника TWOWIN/СтройБразерс
# (подпись @strbr.ru, "С уважением, Резида/Марина/Илья", домен strbr.ru),
# значит TWOWIN ПЕРВЫЙ написал этому человеку с запросом.
# Ответ на такое письмо — это ОТВЕТ ПОСТАВЩИКА, а НЕ заявка клиента.
# Признаки:
#   - Тема содержит "запрос" + имя сотрудника TWOWIN ("запрос Резида", "запрос Марина")
#   - Тема содержит "Заявка" + имя сотрудника TWOWIN ("Заявка Марина")
#   - В цитате есть подпись с @strbr.ru или ООО «СтройБразерс»
#   - Отправитель шлёт счёт НА нас (мы покупатель)
# Все эти письма — НЕ заявка (is_order: false).

# ПОДСКАЗКА: Если не уверен — ставь confidence ниже 0.5 и is_order: false.
# Лучше пропустить, чем отправить не ту заявку в CRM."""


# def is_client_order(subject: str, sender: str, body: str, attachments: list[str],
#                     mailbox_email: str) -> dict:
#     body_truncated = body[:3000] if body else "(пусто)"
#     attachments_str = ", ".join(attachments) if attachments else "нет"

#     user_prompt = CLASSIFICATION_USER_PROMPT.format(
#         mailbox_email=mailbox_email,
#         sender=sender,
#         subject=subject,
#         attachments=attachments_str,
#         body=body_truncated,
#     )

#     try:
#         response = client.chat.completions.create(
#             model="deepseek-chat",
#             messages=[
#                 {"role": "system", "content": CLASSIFICATION_SYSTEM_PROMPT},
#                 {"role": "user", "content": user_prompt}
#             ],
#             temperature=0.05,
#             max_tokens=200,
#         )

#         text = response.choices[0].message.content.strip()
#         text = text.replace("```json", "").replace("```", "").strip()
#         return json.loads(text)

#     except json.JSONDecodeError as e:
#         log.error(f"Ошибка парсинга JSON от DeepSeek: {e} | Ответ: {text[:200]}")
#         return {"is_order": False, "confidence": 0, "reason": "Ошибка парсинга JSON"}
#     except Exception as e:
#         log.error(f"Ошибка DeepSeek API: {e}")
#         return {"is_order": False, "confidence": 0, "reason": f"Ошибка AI: {e}"}


# # ============================================================
# # ПЕРЕМЕЩЕНИЕ
# # ============================================================

# def move_email(mail: imaplib.IMAP4_SSL, uid: bytes, target_folder: str) -> bool:
#     encoded_folder = encode_imap_utf7(target_folder)
#     result = mail.uid("COPY", uid, encoded_folder)
#     if result[0] == "OK":
#         mail.uid("STORE", uid, "+FLAGS", "(\\Deleted)")
#         log.info(f"  ✓ Перемещено в '{target_folder}'")
#         return True
#     else:
#         log.error(f"  ✗ Ошибка копирования: {result}")
#         return False


# # ============================================================
# # ОБРАБОТКА ОДНОГО ЯЩИКА
# # ============================================================

# def process_mailbox(mailbox_login: str, mailbox_password: str) -> dict:
#     """
#     Обрабатывает один почтовый ящик. Возвращает статистику.
#     """
#     stats = {"processed": 0, "moved": 0, "errors": 0}

#     today_imap = date.today().strftime("%d-%b-%Y")

#     log.info(f"\n{'─'*60}")
#     log.info(f"📬 Ящик: {mailbox_login}")
#     log.info(f"{'─'*60}")

#     try:
#         mail = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT)
#         mail.login(mailbox_login, mailbox_password)
#         log.info(f"  ✓ Подключено к {mailbox_login}")
#     except Exception as e:
#         log.error(f"  ✗ Ошибка подключения к {mailbox_login}: {e}")
#         stats["errors"] = 1
#         return stats

#     try:
#         mail.select(SOURCE_FOLDER)

#         status, message_ids = mail.uid("SEARCH", None, f"(UNSEEN SINCE {today_imap})")

#         if status != "OK":
#             log.error(f"  Ошибка поиска писем в {mailbox_login}")
#             return stats

#         uids = message_ids[0].split()
#         if not uids:
#             log.info(f"  Нет новых писем за сегодня")
#             return stats

#         log.info(f"  Найдено {len(uids)} непрочитанных писем за сегодня")

#         limit = len(uids) if TEST_MODE else MAX_EMAILS_PER_RUN
#         skipped_test = 0

#         emails_to_process = []
#         for uid in uids[:limit]:
#             uid_str = uid.decode()
#             uid_key = make_uid_key(mailbox_login, uid_str)

#             # Проверяем в MongoDB — уже обработано?
#             if is_processed(uid_key):
#                 continue

#             msg = fetch_email_by_uid(mail, uid)
#             if msg is None:
#                 log.warning(f"  Не удалось загрузить UID={uid_str}")
#                 continue

#             sender = decode_mime_header(msg.get("From", ""))

#             if TEST_MODE and not is_test_email(sender, msg):
#                 skipped_test += 1
#                 continue

#             emails_to_process.append((uid, uid_str, uid_key, msg))

#         if TEST_MODE and skipped_test > 0:
#             log.info(f"  Пропущено (не от {TEST_EMAIL}): {skipped_test}")

#         if not emails_to_process:
#             log.info(f"  Нет новых писем для анализа")
#             return stats

#         log.info(f"  Новых для анализа: {len(emails_to_process)}")

#         moved_count = 0

#         for uid, uid_str, uid_key, msg in emails_to_process:
#             subject = decode_mime_header(msg.get("Subject", ""))
#             sender = decode_mime_header(msg.get("From", ""))
#             body = extract_text_from_email(msg)
#             attachments = get_attachments_info(msg)

#             log.info(f"\n  {'·'*46}")
#             log.info(f"  UID={uid_str} ({mailbox_login})")
#             log.info(f"    От: {sender}")
#             log.info(f"    Тема: {subject}")
#             if attachments:
#                 log.info(f"    Вложения: {', '.join(attachments)}")

#             result = is_client_order(subject, sender, body, attachments, mailbox_login)
#             is_order = result.get("is_order", False)
#             confidence = result.get("confidence", 0)
#             reason = result.get("reason", "")

#             if is_order and confidence >= 0.7:
#                 log.info(f"    ✅ ЗАЯВКА ({confidence:.0%}) — {reason}")
#                 if not DRY_RUN:
#                     if move_email(mail, uid, TARGET_FOLDER):
#                         moved_count += 1
#                 else:
#                     log.info(f"    [DRY_RUN] Было бы перемещено в '{TARGET_FOLDER}'")
#             else:
#                 log.info(f"    ⬜ Не заявка ({confidence:.0%}) — {reason}")

#             # Сохраняем в MongoDB
#             mark_processed(uid_key, mailbox_login, sender, subject,
#                            is_order, confidence, reason)
#             stats["processed"] += 1

#         if moved_count > 0:
#             mail.expunge()

#         stats["moved"] = moved_count

#     finally:
#         try:
#             mail.logout()
#         except Exception:
#             pass
#         log.info(f"  Отключено от {mailbox_login}")

#     return stats


# # ============================================================
# # ОСНОВНОЙ ЦИКЛ
# # ============================================================

# def run():
#     total_processed = 0
#     total_moved = 0
#     total_errors = 0

#     today_str = datetime.now().strftime('%Y-%m-%d %H:%M')

#     log.info(f"\n{'='*60}")
#     log.info(f"Запуск ({today_str})")
#     log.info(f"Ящиков для проверки: {len(MAILBOXES)}")
#     for mb in MAILBOXES:
#         log.info(f"  • {mb['login']}")
#     if TEST_MODE:
#         log.info(f"⚠ РЕЖИМ ТЕСТИРОВАНИЯ — только письма от/к {TEST_EMAIL}")
#     if DRY_RUN:
#         log.info("⚠ РЕЖИМ DRY_RUN — без перемещений")
#     log.info(f"{'='*60}")

#     for mb in MAILBOXES:
#         try:
#             stats = process_mailbox(mb["login"], mb["password"])
#             total_processed += stats["processed"]
#             total_moved += stats["moved"]
#             total_errors += stats["errors"]
#         except Exception as e:
#             log.error(f"Ошибка при обработке {mb['login']}: {e}", exc_info=True)
#             total_errors += 1

#     log.info(f"\n{'='*60}")
#     log.info(f"Итого по всем ящикам:")
#     log.info(f"  Обработано: {total_processed}")
#     log.info(f"  Заявок перемещено: {total_moved}")
#     if total_errors > 0:
#         log.info(f"  Ошибки подключения: {total_errors}")
#     log.info(f"{'='*60}\n")


# # ============================================================
# # ЗАПУСК
# # ============================================================

# if __name__ == "__main__":
#     stop_flag = False

#     def handle_signal(sig, frame):
#         global stop_flag
#         log.info("\n⛔ Получен сигнал остановки. Завершаем после текущего цикла...")
#         stop_flag = True

#     signal.signal(signal.SIGINT, handle_signal)
#     signal.signal(signal.SIGTERM, handle_signal)

#     log.info(f"🚀 Классификатор запущен (каждые {CHECK_INTERVAL} сек.)")
#     log.info(f"   Ящиков: {len(MAILBOXES)} ({', '.join(mb['login'] for mb in MAILBOXES)})")
#     log.info(f"   Остановка: Ctrl+C\n")

#     while not stop_flag:
#         try:
#             run()
#         except Exception as e:
#             log.error(f"Непредвиденная ошибка: {e}", exc_info=True)

#         if stop_flag:
#             break

#         log.info(f"💤 Следующая проверка через {CHECK_INTERVAL} сек...")
#         for _ in range(CHECK_INTERVAL):
#             if stop_flag:
#                 break
#             time.sleep(1)

#     log.info("✅ Классификатор остановлен.")


"""
Classificateur d'emails Yandex via DeepSeek AI.
Seulement les emails du jour. Seulement les commandes clients → dossier Заявки.

v6: Réécriture complète — robustesse maximale pour Render Background Worker.
    - Reconnexion MongoDB à chaque cycle (évite les timeouts)
    - Reconnexion IMAP avec retry exponentiel
    - Gestion complète des exceptions à tous les niveaux
    - Heartbeat log pour garder le process visible
    - Pas de variables globales MongoDB (connexion fraîche)
    - Timeout IMAP explicite
    - Logging enrichi pour diagnostics

Lancement : python email_classifier.py
Arrêt      : Ctrl+C ou signal SIGTERM
"""

import base64
import email
import imaplib
import json
import logging
import os
import re
import signal
import socket
import time
from datetime import date, datetime
from email.header import decode_header

from dotenv import load_dotenv
from openai import OpenAI
from pymongo import MongoClient, errors as mongo_errors

# ─────────────────────────────────────────────
# CHARGEMENT .env
# ─────────────────────────────────────────────
load_dotenv()

# ─────────────────────────────────────────────
# PARAMÈTRES
# ─────────────────────────────────────────────
IMAP_SERVER          = "imap.yandex.ru"
IMAP_PORT            = 993
IMAP_TIMEOUT         = 30          # secondes — timeout socket IMAP
IMAP_MAX_RETRIES     = 3           # tentatives de connexion IMAP
IMAP_RETRY_DELAY     = 10          # secondes entre les tentatives

DEEPSEEK_API_KEY     = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL    = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")

MONGO_URI            = os.getenv("MONGO_URI")
MONGO_DB             = os.getenv("MONGO_DB", "email_classifier")
MONGO_TIMEOUT_MS     = 10_000      # 10 secondes timeout MongoDB

TARGET_FOLDER        = "Заявки"
SOURCE_FOLDER        = "INBOX"
MAX_EMAILS_PER_RUN   = 50

DRY_RUN              = os.getenv("DRY_RUN", "false").lower() == "true"
CHECK_INTERVAL       = int(os.getenv("CHECK_INTERVAL", "120"))
TEST_MODE            = os.getenv("TEST_MODE", "false").lower() == "true"
TEST_EMAIL           = os.getenv("TEST_EMAIL", "")

# ─────────────────────────────────────────────
# LOGGING (avant toute validation)
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# VALIDATION DES VARIABLES OBLIGATOIRES
# ─────────────────────────────────────────────
_missing = []
if not DEEPSEEK_API_KEY:
    _missing.append("DEEPSEEK_API_KEY")
if not MONGO_URI:
    _missing.append("MONGO_URI")
if TEST_MODE and not TEST_EMAIL:
    _missing.append("TEST_EMAIL (obligatoire si TEST_MODE=true)")
if _missing:
    raise SystemExit(
        f"❌ Variables d'environnement manquantes : {', '.join(_missing)}\n"
        f"   Créez un fichier .env ou configurez-les dans Render > Environment."
    )

# ─────────────────────────────────────────────
# CHARGEMENT DES BOÎTES MAIL
# ─────────────────────────────────────────────
def load_mailboxes() -> list[dict]:
    """
    Charge les boîtes mail depuis les variables d'environnement.
    Format : MAILBOX_1_LOGIN / MAILBOX_1_PASSWORD, MAILBOX_2_LOGIN / ...
    Compatibilité ascendante : YANDEX_LOGIN / YANDEX_PASSWORD.
    """
    mailboxes = []
    i = 1
    while True:
        login    = os.getenv(f"MAILBOX_{i}_LOGIN")
        password = os.getenv(f"MAILBOX_{i}_PASSWORD")
        if not login:
            break
        if not password:
            log.warning(f"⚠ MAILBOX_{i}_LOGIN={login} défini sans MAILBOX_{i}_PASSWORD — ignoré")
            i += 1
            continue
        mailboxes.append({"login": login, "password": password})
        i += 1

    if not mailboxes:
        legacy_login    = os.getenv("YANDEX_LOGIN")
        legacy_password = os.getenv("YANDEX_PASSWORD")
        if legacy_login and legacy_password:
            mailboxes.append({"login": legacy_login, "password": legacy_password})

    return mailboxes


MAILBOXES = load_mailboxes()
if not MAILBOXES:
    raise SystemExit(
        "❌ Aucune boîte mail trouvée.\n"
        "   Définissez MAILBOX_1_LOGIN / MAILBOX_1_PASSWORD (et 2, 3…)\n"
        "   ou YANDEX_LOGIN / YANDEX_PASSWORD."
    )

# ─────────────────────────────────────────────
# CLIENT DEEPSEEK
# ─────────────────────────────────────────────
ai_client = OpenAI(
    api_key=DEEPSEEK_API_KEY,
    base_url=DEEPSEEK_BASE_URL,
)

# ─────────────────────────────────────────────
# MONGODB — connexion fraîche à chaque appel
# ─────────────────────────────────────────────
def get_processed_collection():
    """
    Retourne la collection MongoDB avec une connexion fraîche.
    Timeout court pour ne pas bloquer le cycle en cas de problème réseau.
    """
    mongo_client = MongoClient(
        MONGO_URI,
        serverSelectionTimeoutMS=MONGO_TIMEOUT_MS,
        connectTimeoutMS=MONGO_TIMEOUT_MS,
        socketTimeoutMS=MONGO_TIMEOUT_MS,
    )
    db  = mongo_client[MONGO_DB]
    col = db["processed_emails"]

    # Index créés uniquement s'ils n'existent pas déjà (opération idempotente)
    try:
        col.create_index("uid", unique=True)
        col.create_index("processed_at", expireAfterSeconds=30 * 24 * 3600)
    except Exception:
        pass  # déjà existants — pas grave

    return col


def make_uid_key(mailbox_login: str, uid_str: str) -> str:
    """Clé unique = boîte + UID (évite les collisions entre boîtes)."""
    return f"{mailbox_login}:{uid_str}"


def is_processed(col, uid_key: str) -> bool:
    try:
        return col.find_one({"uid": uid_key}) is not None
    except Exception as e:
        log.warning(f"  MongoDB is_processed error: {e}")
        return False  # en cas d'erreur, on retraite (idempotent côté IMAP)


def mark_processed(col, uid_key: str, mailbox: str, sender: str, subject: str,
                   is_order: bool, confidence: float, reason: str):
    try:
        col.update_one(
            {"uid": uid_key},
            {"$set": {
                "uid":          uid_key,
                "mailbox":      mailbox,
                "sender":       sender,
                "subject":      subject,
                "is_order":     is_order,
                "confidence":   confidence,
                "reason":       reason,
                "processed_at": datetime.utcnow(),
            }},
            upsert=True,
        )
    except Exception as e:
        log.warning(f"  MongoDB mark_processed error: {e}")


# ─────────────────────────────────────────────
# UTILITAIRES IMAP UTF-7
# ─────────────────────────────────────────────
def encode_imap_utf7(text: str) -> str:
    result            = []
    non_ascii_buffer  = ""

    for char in text:
        if 0x20 <= ord(char) <= 0x7E:
            if non_ascii_buffer:
                utf16 = non_ascii_buffer.encode("utf-16-be")
                b64   = base64.b64encode(utf16).decode("ascii").rstrip("=").replace("/", ",")
                result.append("&" + b64 + "-")
                non_ascii_buffer = ""
            result.append("&-" if char == "&" else char)
        else:
            non_ascii_buffer += char

    if non_ascii_buffer:
        utf16 = non_ascii_buffer.encode("utf-16-be")
        b64   = base64.b64encode(utf16).decode("ascii").rstrip("=").replace("/", ",")
        result.append("&" + b64 + "-")

    return "".join(result)


# ─────────────────────────────────────────────
# UTILITAIRES EMAIL
# ─────────────────────────────────────────────
def decode_mime_header(value: str) -> str:
    if not value:
        return ""
    parts   = decode_header(value)
    decoded = []
    for part, charset in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(part)
    return " ".join(decoded)


def extract_email_address(from_header: str) -> str:
    match = re.search(r"[\w.+-]+@[\w.-]+\.\w+", from_header)
    return match.group(0).lower() if match else from_header.lower()


def extract_text_from_email(msg) -> str:
    text_parts = []
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    text_parts.append(payload.decode(charset, errors="replace"))
            elif ct == "text/html" and not text_parts:
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    text_parts.append(payload.decode(charset, errors="replace"))
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            text_parts.append(payload.decode(charset, errors="replace"))
    return "\n".join(text_parts)


def get_attachments_info(msg) -> list[str]:
    attachments = []
    if msg.is_multipart():
        for part in msg.walk():
            filename = part.get_filename()
            if filename:
                attachments.append(decode_mime_header(filename))
    return attachments


def is_test_email_sender(sender: str, msg) -> bool:
    if TEST_EMAIL.lower() in extract_email_address(sender):
        return True
    if TEST_EMAIL.lower() in msg.get("Reply-To", "").lower():
        return True
    if TEST_EMAIL.lower() in extract_text_from_email(msg).lower():
        return True
    return False


# ─────────────────────────────────────────────
# CONNEXION IMAP AVEC RETRY
# ─────────────────────────────────────────────
def connect_imap(login: str, password: str) -> imaplib.IMAP4_SSL | None:
    """
    Tente de se connecter à IMAP avec retry exponentiel.
    Retourne l'objet mail ou None si échec définitif.
    """
    socket.setdefaulttimeout(IMAP_TIMEOUT)

    for attempt in range(1, IMAP_MAX_RETRIES + 1):
        try:
            mail = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT)
            mail.login(login, password)
            log.info(f"  ✓ Connecté à {login}")
            return mail
        except imaplib.IMAP4.error as e:
            log.error(f"  ✗ IMAP auth error ({login}): {e}")
            return None  # erreur auth — inutile de réessayer
        except (OSError, socket.timeout) as e:
            wait = IMAP_RETRY_DELAY * attempt
            log.warning(f"  ⚠ IMAP connexion échouée (tentative {attempt}/{IMAP_MAX_RETRIES}): {e}")
            if attempt < IMAP_MAX_RETRIES:
                log.info(f"  ⏳ Nouvelle tentative dans {wait}s…")
                time.sleep(wait)
            else:
                log.error(f"  ✗ Impossible de se connecter à {login} après {IMAP_MAX_RETRIES} tentatives")
                return None
        except Exception as e:
            log.error(f"  ✗ Erreur inattendue IMAP ({login}): {e}")
            return None

    return None


def fetch_email_by_uid(mail: imaplib.IMAP4_SSL, uid: bytes):
    try:
        status, data = mail.uid("FETCH", uid, "(BODY.PEEK[])")
        if status != "OK":
            return None
        for item in data:
            if isinstance(item, tuple) and len(item) == 2 and isinstance(item[1], bytes):
                return email.message_from_bytes(item[1])
    except Exception as e:
        log.warning(f"  Erreur fetch UID={uid}: {e}")
    return None


def move_email(mail: imaplib.IMAP4_SSL, uid: bytes, target_folder: str) -> bool:
    try:
        encoded = encode_imap_utf7(target_folder)
        result  = mail.uid("COPY", uid, encoded)
        if result[0] == "OK":
            mail.uid("STORE", uid, "+FLAGS", "(\\Deleted)")
            log.info(f"  ✓ Déplacé vers '{target_folder}'")
            return True
        else:
            log.error(f"  ✗ Erreur COPY: {result}")
            return False
    except Exception as e:
        log.error(f"  ✗ Erreur déplacement: {e}")
        return False


# ─────────────────────────────────────────────
# CLASSIFICATION DEEPSEEK
# ─────────────────────────────────────────────
CLASSIFICATION_SYSTEM_PROMPT = """Ты — эксперт-классификатор входящей почты компании TWOWIN (СтройБразерс).

TWOWIN — дистрибьютор строительных материалов в Екатеринбурге. Компания ПОКУПАЕТ товар у ПОСТАВЩИКОВ и ПРОДАЁТ его КЛИЕНТАМ (строительные компании, подрядчики, магазины, частные лица).

Твоя задача: определить, является ли письмо ЗАЯВКОЙ КЛИЕНТА (т.е. входящим запросом на покупку от клиента TWOWIN).

ВАЖНО: Анализируй СМЫСЛ письма целиком, а не отдельные слова. Одно и то же слово "заказ" может быть в заявке клиента и в уведомлении от поставщика — ты должен понять КОНТЕКСТ.

Отвечай СТРОГО в формате JSON (без markdown, без комментариев):
{"is_order": true/false, "confidence": 0.0-1.0, "reason": "краткое пояснение на русском"}"""

CLASSIFICATION_USER_PROMPT = """Проанализируй это письмо.

КОНТЕКСТ: {mailbox_email} — это почта TWOWIN. Письмо пришло НА этот адрес. Определи: это заявка клиента или нет?

═══════════════════════════════════════
ДАННЫЕ ПИСЬМА:
═══════════════════════════════════════
От: {sender}
Тема: {subject}
Вложения: {attachments}

Текст письма:
---
{body}
---

═══════════════════════════════════════
ПРАВИЛА КЛАССИФИКАЦИИ:
═══════════════════════════════════════

✅ ЭТО ЗАЯВКА КЛИЕНТА (is_order: true), если:

1. ПРЯМОЙ ЗАКАЗ: Клиент (не поставщик!) хочет КУПИТЬ у TWOWIN товар/материалы.
   Признаки: "нужен", "требуется", "закажем", "хотим заказать", "прошу отгрузить",
   "выставьте счёт", "подготовьте КП", список товаров с количеством.

2. ЗАПРОС ЦЕНЫ/НАЛИЧИЯ: Клиент спрашивает цену, наличие, сроки доставки товара,
   просит прайс или коммерческое предложение на конкретные позиции.

3. СПЕЦИФИКАЦИЯ: Во вложении файл-заявка (Excel, PDF со списком товаров),
   сводный заказ покупателя, спецификация от клиента.

4. ОТВЕТ КЛИЕНТА EN CHAÎNE SUR UN COMMANDE: Si la thème contient Re:/RE:/FW: et
   des mots "счёт", "заказ", "заявка", "КП", "спецификация", "прайс", "отгрузка"
   — tout réponse du client est une commande. Même court : "Ок", "Да", "+", etc.

5. ПОДБОР АНАЛОГА: Клиент просит подобрать аналог, замену, альтернативу товара.

❌ ЭТО НЕ ЗАЯВКА (is_order: false), если:

1. ПОСТАВЩИК пишет нам (прайс от поставщика, уведомление об отгрузке в наш адрес).
2. РЕКЛАМА / СПАМ / РАССЫЛКА.
3. СИСТЕМНЫЕ УВЕДОМЛЕНИЯ: Битрикс24, 1С, CRM, мониторинг.
4. БУХГАЛТЕРИЯ без заказа: акт сверки, запрос закрывающих документов.
5. РЕКЛАМАЦИЯ / ВОЗВРАТ.
6. ЛОГИСТИКА без нового заказа: трекинг, вопрос "где груз?".
7. ОБЩАЯ ПЕРЕПИСКА: вопросы не связанные с покупкой товара.
8. ВНУТРЕННЯЯ ПОЧТА: письма от сотрудников TWOWIN/СтройБразерс.

КАК ОТЛИЧИТЬ КЛИЕНТА ОТ ПОСТАВЩИКА:
- КЛИЕНТ хочет КУПИТЬ У НАС → заявка
- ПОСТАВЩИК предлагает НАМ КУПИТЬ → НЕ заявка
- Тема "запрос Резида/Марина" + подпись @strbr.ru dans la citation → НЕ заявка
- Тема "Заявка Марина" + ответ поставщика → НЕ заявка

ПОДСКАЗКА: Если не уверен — ставь confidence ниже 0.5 и is_order: false."""


def classify_email(subject: str, sender: str, body: str,
                   attachments: list[str], mailbox_email: str) -> dict:
    body_truncated  = body[:3000] if body else "(пусто)"
    attachments_str = ", ".join(attachments) if attachments else "нет"

    user_prompt = CLASSIFICATION_USER_PROMPT.format(
        mailbox_email=mailbox_email,
        sender=sender,
        subject=subject,
        attachments=attachments_str,
        body=body_truncated,
    )

    try:
        response = ai_client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": CLASSIFICATION_SYSTEM_PROMPT},
                {"role": "user",   "content": user_prompt},
            ],
            temperature=0.05,
            max_tokens=200,
        )
        text = response.choices[0].message.content.strip()
        text = text.replace("```json", "").replace("```", "").strip()
        return json.loads(text)

    except json.JSONDecodeError as e:
        log.error(f"  Erreur JSON DeepSeek: {e}")
        return {"is_order": False, "confidence": 0, "reason": "Erreur parsing JSON"}
    except Exception as e:
        log.error(f"  Erreur API DeepSeek: {e}")
        return {"is_order": False, "confidence": 0, "reason": f"Erreur AI: {e}"}


# ─────────────────────────────────────────────
# TRAITEMENT D'UNE BOÎTE MAIL
# ─────────────────────────────────────────────
def process_mailbox(mailbox_login: str, mailbox_password: str) -> dict:
    stats = {"processed": 0, "moved": 0, "errors": 0}

    log.info(f"\n{'─' * 60}")
    log.info(f"📬 Boîte : {mailbox_login}")
    log.info(f"{'─' * 60}")

    # ── Connexion IMAP ──
    mail = connect_imap(mailbox_login, mailbox_password)
    if mail is None:
        stats["errors"] = 1
        return stats

    # ── Connexion MongoDB fraîche ──
    try:
        col = get_processed_collection()
    except Exception as e:
        log.error(f"  ✗ MongoDB connexion échouée: {e}")
        try:
            mail.logout()
        except Exception:
            pass
        stats["errors"] = 1
        return stats

    try:
        # ── Sélection dossier source ──
        status, _ = mail.select(SOURCE_FOLDER)
        if status != "OK":
            log.error(f"  ✗ Impossible de sélectionner {SOURCE_FOLDER}")
            stats["errors"] = 1
            return stats

        # ── Recherche emails du jour non lus ──
        today_imap = date.today().strftime("%d-%b-%Y")
        status, message_ids = mail.uid("SEARCH", None, f"(UNSEEN SINCE {today_imap})")

        if status != "OK":
            log.error(f"  ✗ Erreur recherche IMAP")
            stats["errors"] = 1
            return stats

        uids = message_ids[0].split() if message_ids[0] else []
        if not uids:
            log.info("  ✓ Aucun nouvel email aujourd'hui")
            return stats

        log.info(f"  Trouvés : {len(uids)} emails non lus aujourd'hui")

        # ── Filtrage et chargement ──
        limit          = len(uids) if TEST_MODE else MAX_EMAILS_PER_RUN
        emails_to_process = []
        skipped_test   = 0

        for uid in uids[:limit]:
            uid_str = uid.decode()
            uid_key = make_uid_key(mailbox_login, uid_str)

            if is_processed(col, uid_key):
                continue

            msg = fetch_email_by_uid(mail, uid)
            if msg is None:
                log.warning(f"  ⚠ Impossible de charger UID={uid_str}")
                continue

            sender = decode_mime_header(msg.get("From", ""))

            if TEST_MODE and not is_test_email_sender(sender, msg):
                skipped_test += 1
                continue

            emails_to_process.append((uid, uid_str, uid_key, msg))

        if TEST_MODE and skipped_test > 0:
            log.info(f"  Ignorés (pas de {TEST_EMAIL}) : {skipped_test}")

        if not emails_to_process:
            log.info("  ✓ Rien de nouveau à analyser")
            return stats

        log.info(f"  À analyser : {len(emails_to_process)}")

        # ── Traitement ──
        moved_count = 0

        for uid, uid_str, uid_key, msg in emails_to_process:
            subject     = decode_mime_header(msg.get("Subject", ""))
            sender      = decode_mime_header(msg.get("From", ""))
            body        = extract_text_from_email(msg)
            attachments = get_attachments_info(msg)

            log.info(f"\n  {'·' * 46}")
            log.info(f"  UID={uid_str} ({mailbox_login})")
            log.info(f"    De      : {sender}")
            log.info(f"    Objet   : {subject}")
            if attachments:
                log.info(f"    Pièces  : {', '.join(attachments)}")

            result     = classify_email(subject, sender, body, attachments, mailbox_login)
            is_order   = result.get("is_order", False)
            confidence = result.get("confidence", 0)
            reason     = result.get("reason", "")

            if is_order and confidence >= 0.7:
                log.info(f"    ✅ COMMANDE ({confidence:.0%}) — {reason}")
                if not DRY_RUN:
                    if move_email(mail, uid, TARGET_FOLDER):
                        moved_count += 1
                else:
                    log.info(f"    [DRY_RUN] Aurait été déplacé vers '{TARGET_FOLDER}'")
            else:
                log.info(f"    ⬜ Pas une commande ({confidence:.0%}) — {reason}")

            mark_processed(col, uid_key, mailbox_login, sender, subject,
                           is_order, confidence, reason)
            stats["processed"] += 1

        # ── Suppression définitive des emails déplacés ──
        if moved_count > 0:
            try:
                mail.expunge()
            except Exception as e:
                log.warning(f"  ⚠ Expunge error: {e}")

        stats["moved"] = moved_count

    except (imaplib.IMAP4.abort, imaplib.IMAP4.error, OSError, socket.timeout) as e:
        log.error(f"  ✗ Erreur IMAP durant traitement: {e}")
        stats["errors"] = 1

    except Exception as e:
        log.error(f"  ✗ Erreur inattendue ({mailbox_login}): {e}", exc_info=True)
        stats["errors"] = 1

    finally:
        try:
            mail.logout()
        except Exception:
            pass
        log.info(f"  Déconnecté de {mailbox_login}")

    return stats


# ─────────────────────────────────────────────
# CYCLE PRINCIPAL
# ─────────────────────────────────────────────
def run_cycle():
    """Exécute un cycle complet sur toutes les boîtes mail."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    log.info(f"\n{'=' * 60}")
    log.info(f"🔄 Cycle démarré — {now}")
    log.info(f"   Boîtes : {len(MAILBOXES)} ({', '.join(mb['login'] for mb in MAILBOXES)})")
    if TEST_MODE:
        log.info(f"   ⚠ MODE TEST — seulement les emails de/vers {TEST_EMAIL}")
    if DRY_RUN:
        log.info("   ⚠ MODE DRY_RUN — aucun déplacement effectué")
    log.info(f"{'=' * 60}")

    total_processed = 0
    total_moved     = 0
    total_errors    = 0

    for mb in MAILBOXES:
        try:
            stats = process_mailbox(mb["login"], mb["password"])
        except Exception as e:
            log.error(f"Erreur critique ({mb['login']}): {e}", exc_info=True)
            stats = {"processed": 0, "moved": 0, "errors": 1}

        total_processed += stats["processed"]
        total_moved     += stats["moved"]
        total_errors    += stats["errors"]

    log.info(f"\n{'=' * 60}")
    log.info(f"📊 Résultat du cycle :")
    log.info(f"   Analysés  : {total_processed}")
    log.info(f"   Déplacés  : {total_moved}")
    if total_errors:
        log.info(f"   Erreurs   : {total_errors}")
    log.info(f"{'=' * 60}\n")


# ─────────────────────────────────────────────
# POINT D'ENTRÉE
# ─────────────────────────────────────────────
if __name__ == "__main__":
    stop_flag = False

    def handle_signal(sig, frame):
        global stop_flag
        log.info("\n⛔ Signal d'arrêt reçu — fin après le cycle en cours…")
        stop_flag = True

    signal.signal(signal.SIGINT,  handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    log.info(f"🚀 Classificateur démarré")
    log.info(f"   Intervalle : {CHECK_INTERVAL}s")
    log.info(f"   Boîtes     : {len(MAILBOXES)} ({', '.join(mb['login'] for mb in MAILBOXES)})")
    log.info(f"   Arrêt      : Ctrl+C\n")

    cycle_count = 0

    while not stop_flag:
        cycle_count += 1
        log.info(f"💓 Heartbeat — cycle #{cycle_count} — worker actif")

        try:
            run_cycle()
        except Exception as e:
            # Sécurité absolue : même si run_cycle() explose, on continue
            log.error(f"🚨 Erreur non rattrapée dans run_cycle(): {e}", exc_info=True)

        if stop_flag:
            break

        log.info(f"💤 Prochain cycle dans {CHECK_INTERVAL}s…")

        # Attente découpée en secondes pour réagir rapidement au signal SIGTERM
        for _ in range(CHECK_INTERVAL):
            if stop_flag:
                break
            time.sleep(1)

    log.info("✅ Classificateur arrêté proprement.")