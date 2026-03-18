


# """
# Автоматическая классификация писем Yandex через DeepSeek AI.
# Читает входящие письма → классифицирует через AI → перемещает в нужную папку.

# Запуск: python email_classifier.py
# Можно поставить на cron каждые 5-10 минут.
# """

# import imaplib
# import email
# from email.header import decode_header
# import json
# import os
# import logging
# import base64
# from datetime import datetime
# from openai import OpenAI

# # ============================================================
# # НАСТРОЙКИ
# # ============================================================

# YANDEX_LOGIN = "info@strbr.ru"
# YANDEX_PASSWORD = "vgbzugscpajzpxel"
# IMAP_SERVER = "imap.yandex.ru"
# IMAP_PORT = 993

# DEEPSEEK_API_KEY = "sk-abc35f130986414fbe9d10fae4bcd789"
# DEEPSEEK_BASE_URL = "https://api.deepseek.com"

# FOLDERS = {
#     "Заявки": "Заявки/заказы от клиентов: запросы на товар, просьбы выставить счёт, "
#               "спецификации, запросы КП, прайс-листы, уточнения по заказам, "
#               "запросы на доставку товаров",
#     "Поставщики": "Письма от поставщиков: прайс-листы, уведомления об отгрузке, "
#                   "изменения цен, наличие товара, коммерческие предложения от поставщиков",
#     "Оплата": "Вопросы оплаты: счета, акты сверки, платёжные поручения, "
#               "напоминания о задолженности, реквизиты",
#     "Рекламации": "Рекламации и претензии: жалобы на качество, возвраты, брак, "
#                   "несоответствие товара, повреждения при доставке",
#     "Реклама": "Рекламные рассылки, спам, маркетинговые предложения, "
#                "вебинары, промо-акции от сторонних компаний",
#     "INBOX": "Всё остальное, что не подходит ни под одну категорию выше. "
#              "Личные письма, общие вопросы, информационные письма",
# }

# SOURCE_FOLDER = "INBOX"
# MAX_EMAILS_PER_RUN = 20
# PROCESSED_FILE = "processed_emails.json"
# DRY_RUN = False

# # ============================================================
# # ЛОГИРОВАНИЕ
# # ============================================================

# logging.basicConfig(
#     level=logging.INFO,
#     format="%(asctime)s [%(levelname)s] %(message)s",
#     handlers=[
#         logging.FileHandler("email_classifier.log", encoding="utf-8"),
#         logging.StreamHandler()
#     ]
# )
# log = logging.getLogger(__name__)

# # ============================================================
# # DEEPSEEK КЛИЕНТ
# # ============================================================

# client = OpenAI(
#     api_key=DEEPSEEK_API_KEY,
#     base_url=DEEPSEEK_BASE_URL,
# )


# # ============================================================
# # IMAP UTF-7 ENCODING (для кириллических папок)
# # ============================================================

# def encode_imap_utf7(text: str) -> str:
#     """
#     Кодирует строку в IMAP Modified UTF-7 (RFC 3501).
#     'Заявки' → '&-BCIEMAQ8BDwEOgQ4-'
#     """
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

# def load_processed_ids() -> set:
#     if os.path.exists(PROCESSED_FILE):
#         with open(PROCESSED_FILE, "r") as f:
#             return set(json.load(f))
#     return set()


# def save_processed_ids(ids: set):
#     with open(PROCESSED_FILE, "w") as f:
#         json.dump(list(ids), f)


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
#     """
#     Загрузить письмо по UID с валидацией ответа.
#     Возвращает email.message или None если ошибка.
#     """
#     status, data = mail.uid("FETCH", uid, "(BODY.PEEK[])")
#     if status != "OK":
#         return None

#     # Ищем в ответе элемент с bytes (тело письма)
#     # data может содержать tuple(header, body) и/или b')'
#     raw_email = None
#     for item in data:
#         if isinstance(item, tuple) and len(item) == 2 and isinstance(item[1], bytes):
#             raw_email = item[1]
#             break

#     if raw_email is None:
#         return None

#     try:
#         return email.message_from_bytes(raw_email)
#     except Exception as e:
#         log.warning(f"  Ошибка парсинга письма: {e}")
#         return None


# # ============================================================
# # КЛАССИФИКАЦИЯ ЧЕРЕЗ DEEPSEEK
# # ============================================================

# def classify_email(subject: str, sender: str, body: str, attachments: list[str]) -> dict:
#     folder_descriptions = "\n".join(
#         f'- "{name}": {desc}' for name, desc in FOLDERS.items()
#     )

#     body_truncated = body[:2000] if body else "(пусто)"
#     attachments_str = ", ".join(attachments) if attachments else "нет"

#     prompt = f"""Ты — помощник для классификации входящих писем компании TWOWIN 
# (дистрибьютор строительных материалов, Екатеринбург).

# Определи, в какую папку переместить письмо. Доступные папки:
# {folder_descriptions}

# Данные письма:
# - От: {sender}
# - Тема: {subject}
# - Вложения: {attachments_str}
# - Текст письма:
# {body_truncated}

# Ответь СТРОГО в формате JSON (без markdown, без пояснений):
# {{"folder": "имя_папки", "confidence": 0.85, "reason": "краткая причина"}}

# Правила:
# 1. Если уверенность ниже 0.6, отправляй в INBOX
# 2. Рекламные рассылки → Реклама
# 3. Если клиент просит товар/цену/счёт → Заявки
# 4. Если поставщик присылает прайс или информацию → Поставщики
# """

#     try:
#         response = client.chat.completions.create(
#             model="deepseek-chat",
#             messages=[
#                 {"role": "system", "content": "Ты классификатор писем. Отвечай только JSON."},
#                 {"role": "user", "content": prompt}
#             ],
#             temperature=0.1,
#             max_tokens=200,
#         )

#         text = response.choices[0].message.content.strip()
#         text = text.replace("```json", "").replace("```", "").strip()
#         result = json.loads(text)

#         if result.get("folder") not in FOLDERS:
#             log.warning(f"AI вернул неизвестную папку: {result.get('folder')}, fallback → INBOX")
#             result["folder"] = "INBOX"

#         if result.get("confidence", 0) < 0.6:
#             log.info(f"Низкая уверенность ({result.get('confidence')}), fallback → INBOX")
#             result["folder"] = "INBOX"

#         return result

#     except Exception as e:
#         log.error(f"Ошибка DeepSeek API: {e}")
#         return {"folder": "INBOX", "confidence": 0, "reason": f"Ошибка AI: {e}"}


# # ============================================================
# # ПЕРЕМЕЩЕНИЕ ПИСЬМА (без expunge — делаем expunge один раз в конце)
# # ============================================================

# def move_email(mail: imaplib.IMAP4_SSL, uid: bytes, target_folder: str) -> bool:
#     """Копировать письмо в целевую папку и пометить на удаление. Возвращает True/False."""
#     encoded_folder = encode_imap_utf7(target_folder)

#     result = mail.uid("COPY", uid, encoded_folder)
#     if result[0] == "OK":
#         mail.uid("STORE", uid, "+FLAGS", "(\\Deleted)")
#         log.info(f"  ✓ Перемещено в '{target_folder}'")
#         return True
#     else:
#         log.error(f"  ✗ Ошибка копирования: {result}")
#         return False


# def list_server_folders(mail: imaplib.IMAP4_SSL):
#     result, folders = mail.list()
#     if result == "OK":
#         log.info("=== Папки на сервере ===")
#         for f in folders:
#             log.info(f"  {f.decode()}")
#         log.info("========================")


# # ============================================================
# # ОСНОВНОЙ ЦИКЛ
# # ============================================================

# def run():
#     processed_ids = load_processed_ids()
#     new_processed = set()
#     moved_count = 0

#     log.info(f"{'='*50}")
#     log.info(f"Запуск классификации ({datetime.now().strftime('%Y-%m-%d %H:%M')})")
#     if DRY_RUN:
#         log.info("⚠ РЕЖИМ DRY_RUN — письма НЕ будут перемещаться")
#     log.info(f"{'='*50}")

#     try:
#         mail = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT)
#         mail.login(YANDEX_LOGIN, YANDEX_PASSWORD)
#         log.info("✓ Подключено к Yandex Mail")
#     except Exception as e:
#         log.error(f"✗ Ошибка подключения: {e}")
#         return

#     try:
#         # Раскомментировать для отладки — покажет реальные имена папок на сервере
#         # list_server_folders(mail)

#         mail.select(SOURCE_FOLDER)

#         # Сначала собираем ВСЕ UIDs
#         status, message_ids = mail.uid("SEARCH", None, "UNSEEN")
#         if status != "OK":
#             log.error("Ошибка поиска писем")
#             return

#         uids = message_ids[0].split()
#         if not uids:
#             log.info("Нет новых писем для обработки")
#             return

#         log.info(f"Найдено {len(uids)} непрочитанных писем")

#         # Сначала загружаем все письма в память, потом обрабатываем
#         emails_to_process = []
#         for uid in uids[:MAX_EMAILS_PER_RUN]:
#             uid_str = uid.decode()
#             if uid_str in processed_ids:
#                 continue

#             msg = fetch_email_by_uid(mail, uid)
#             if msg is None:
#                 log.warning(f"Не удалось загрузить письмо UID={uid_str}, пропускаем")
#                 continue

#             emails_to_process.append((uid, uid_str, msg))

#         log.info(f"Загружено {len(emails_to_process)} писем для классификации")

#         # Теперь классифицируем и перемещаем
#         for uid, uid_str, msg in emails_to_process:
#             subject = decode_mime_header(msg.get("Subject", ""))
#             sender = decode_mime_header(msg.get("From", ""))
#             body = extract_text_from_email(msg)
#             attachments = get_attachments_info(msg)

#             log.info(f"\n--- Письмо UID={uid_str} ---")
#             log.info(f"  От: {sender}")
#             log.info(f"  Тема: {subject}")
#             if attachments:
#                 log.info(f"  Вложения: {', '.join(attachments)}")

#             result = classify_email(subject, sender, body, attachments)
#             target = result["folder"]
#             confidence = result.get("confidence", 0)
#             reason = result.get("reason", "")

#             log.info(f"  → AI решение: '{target}' "
#                      f"(уверенность: {confidence:.0%}, причина: {reason})")

#             if target != SOURCE_FOLDER and not DRY_RUN:
#                 if move_email(mail, uid, target):
#                     moved_count += 1
#             elif target == SOURCE_FOLDER:
#                 log.info(f"  → Остаётся во Входящих")
#             elif DRY_RUN:
#                 log.info(f"  → [DRY_RUN] Было бы перемещено в '{target}'")

#             new_processed.add(uid_str)

#         # Expunge один раз в конце — удаляем все помеченные \Deleted
#         if moved_count > 0:
#             mail.expunge()
#             log.info(f"\n✓ Expunge выполнен ({moved_count} писем удалено из Входящих)")

#     finally:
#         mail.logout()
#         log.info("Отключено от сервера")

#     all_processed = processed_ids | new_processed
#     if len(all_processed) > 5000:
#         all_processed = set(list(all_processed)[-5000:])
#     save_processed_ids(all_processed)

#     log.info(f"\nОбработано писем: {len(new_processed)}")
#     log.info(f"{'='*50}\n")


# if __name__ == "__main__":
#     run()


"""
Классификация писем Yandex через DeepSeek AI.
Только письма за сегодня. Только заявки клиентов → папка Заявки.
Остальные письма не трогаем.

Запуск: python email_classifier.py
"""

import imaplib
import email
from email.header import decode_header
import json
import os
import logging
import base64
from datetime import datetime, date
from openai import OpenAI

# ============================================================
# НАСТРОЙКИ
# ============================================================

YANDEX_LOGIN = "info@strbr.ru"
YANDEX_PASSWORD = "vgbzugscpajzpxel"
IMAP_SERVER = "imap.yandex.ru"
IMAP_PORT = 993

DEEPSEEK_API_KEY = "sk-abc35f130986414fbe9d10fae4bcd789"
DEEPSEEK_BASE_URL = "https://api.deepseek.com"

TARGET_FOLDER = "Заявки"
SOURCE_FOLDER = "INBOX"
MAX_EMAILS_PER_RUN = 50
PROCESSED_FILE = "processed_emails.json"
DRY_RUN = False

# ============================================================
# ЛОГИРОВАНИЕ
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("email_classifier.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

# ============================================================
# DEEPSEEK КЛИЕНТ
# ============================================================

client = OpenAI(
    api_key=DEEPSEEK_API_KEY,
    base_url=DEEPSEEK_BASE_URL,
)


# ============================================================
# IMAP UTF-7 ENCODING
# ============================================================

def encode_imap_utf7(text: str) -> str:
    result = []
    non_ascii_buffer = ""
    for char in text:
        if 0x20 <= ord(char) <= 0x7e:
            if non_ascii_buffer:
                utf16 = non_ascii_buffer.encode("utf-16-be")
                b64 = base64.b64encode(utf16).decode("ascii").rstrip("=")
                b64 = b64.replace("/", ",")
                result.append("&" + b64 + "-")
                non_ascii_buffer = ""
            if char == "&":
                result.append("&-")
            else:
                result.append(char)
        else:
            non_ascii_buffer += char
    if non_ascii_buffer:
        utf16 = non_ascii_buffer.encode("utf-16-be")
        b64 = base64.b64encode(utf16).decode("ascii").rstrip("=")
        b64 = b64.replace("/", ",")
        result.append("&" + b64 + "-")
    return "".join(result)


# ============================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================

def load_processed_ids() -> set:
    if os.path.exists(PROCESSED_FILE):
        with open(PROCESSED_FILE, "r") as f:
            return set(json.load(f))
    return set()


def save_processed_ids(ids: set):
    with open(PROCESSED_FILE, "w") as f:
        json.dump(list(ids), f)


def decode_mime_header(header_value: str) -> str:
    if not header_value:
        return ""
    parts = decode_header(header_value)
    decoded = []
    for part, charset in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(part)
    return " ".join(decoded)


def extract_text_from_email(msg) -> str:
    text_parts = []
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            if content_type == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    text_parts.append(payload.decode(charset, errors="replace"))
            elif content_type == "text/html" and not text_parts:
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


def fetch_email_by_uid(mail: imaplib.IMAP4_SSL, uid: bytes):
    status, data = mail.uid("FETCH", uid, "(BODY.PEEK[])")
    if status != "OK":
        return None
    for item in data:
        if isinstance(item, tuple) and len(item) == 2 and isinstance(item[1], bytes):
            try:
                return email.message_from_bytes(item[1])
            except Exception as e:
                log.warning(f"  Ошибка парсинга: {e}")
                return None
    return None


# ============================================================
# КЛАССИФИКАЦИЯ: заявка клиента или нет?
# ============================================================

def is_client_order(subject: str, sender: str, body: str, attachments: list[str]) -> dict:
    """
    Определяет, является ли письмо заявкой/заказом от клиента.
    Возвращает {"is_order": true/false, "confidence": 0.0-1.0, "reason": "..."}
    """
    body_truncated = body[:2000] if body else "(пусто)"
    attachments_str = ", ".join(attachments) if attachments else "нет"

    prompt = f"""Ты — помощник компании TWOWIN (дистрибьютор строительных материалов, Екатеринбург).

Определи, является ли это письмо ЗАЯВКОЙ или ЗАКАЗОМ от клиента.

Это ЗАЯВКА если:
- Клиент запрашивает товар, материалы, продукцию
- Клиент просит выставить счёт
- Клиент отправляет спецификацию или список товаров
- Клиент запрашивает КП (коммерческое предложение) или прайс
- Клиент уточняет наличие/цену/сроки доставки товара
- Во вложении есть заказ, спецификация, сводный заказ покупателя
- Клиент просит подобрать товар или аналог

Это НЕ заявка если:
- Рекламная рассылка, спам, маркетинг
- Письмо от поставщика (прайс поставщика, уведомление об отгрузке ОТ поставщика)
- Внутренняя переписка, оповещения систем
- Вопросы по оплате, акты сверки, бухгалтерия
- Рекламации, жалобы, возвраты
- Общие вопросы, не связанные с заказом товара
- Уведомления о доставке, трекинг, логистика без нового заказа

Данные письма:
- От: {sender}
- Тема: {subject}
- Вложения: {attachments_str}
- Текст:
{body_truncated}

Ответь СТРОГО в формате JSON (без markdown):
{{"is_order": true, "confidence": 0.9, "reason": "краткая причина"}}
"""

    try:
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": "Ты определяешь, является ли письмо заказом клиента. Отвечай только JSON."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.1,
            max_tokens=150,
        )

        text = response.choices[0].message.content.strip()
        text = text.replace("```json", "").replace("```", "").strip()
        return json.loads(text)

    except Exception as e:
        log.error(f"Ошибка DeepSeek API: {e}")
        return {"is_order": False, "confidence": 0, "reason": f"Ошибка AI: {e}"}


# ============================================================
# ПЕРЕМЕЩЕНИЕ
# ============================================================

def move_email(mail: imaplib.IMAP4_SSL, uid: bytes, target_folder: str) -> bool:
    encoded_folder = encode_imap_utf7(target_folder)
    result = mail.uid("COPY", uid, encoded_folder)
    if result[0] == "OK":
        mail.uid("STORE", uid, "+FLAGS", "(\\Deleted)")
        log.info(f"  ✓ Перемещено в '{target_folder}'")
        return True
    else:
        log.error(f"  ✗ Ошибка копирования: {result}")
        return False


# ============================================================
# ОСНОВНОЙ ЦИКЛ
# ============================================================

def run():
    processed_ids = load_processed_ids()
    new_processed = set()
    moved_count = 0

    # Дата для IMAP SINCE (формат: 12-Mar-2026)
    today_imap = date.today().strftime("%d-%b-%Y")

    log.info(f"{'='*50}")
    log.info(f"Запуск ({datetime.now().strftime('%Y-%m-%d %H:%M')})")
    log.info(f"Ищем письма за сегодня ({today_imap})")
    if DRY_RUN:
        log.info("⚠ РЕЖИМ DRY_RUN")
    log.info(f"{'='*50}")

    try:
        mail = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT)
        mail.login(YANDEX_LOGIN, YANDEX_PASSWORD)
        log.info("✓ Подключено к Yandex Mail")
    except Exception as e:
        log.error(f"✗ Ошибка подключения: {e}")
        return

    try:
        mail.select(SOURCE_FOLDER)

        # Только непрочитанные письма за сегодня
        status, message_ids = mail.uid("SEARCH", None, f"(UNSEEN SINCE {today_imap})")
        if status != "OK":
            log.error("Ошибка поиска писем")
            return

        uids = message_ids[0].split()
        if not uids:
            log.info("Нет писем за сегодня")
            return

        log.info(f"Найдено {len(uids)} писем за сегодня")

        # Загружаем все письма в память
        emails_to_process = []
        for uid in uids[:MAX_EMAILS_PER_RUN]:
            uid_str = uid.decode()
            if uid_str in processed_ids:
                continue
            msg = fetch_email_by_uid(mail, uid)
            if msg is None:
                log.warning(f"Не удалось загрузить UID={uid_str}")
                continue
            emails_to_process.append((uid, uid_str, msg))

        if not emails_to_process:
            log.info("Все письма за сегодня уже обработаны")
            return

        log.info(f"Новых для анализа: {len(emails_to_process)}")

        # Классифицируем
        for uid, uid_str, msg in emails_to_process:
            subject = decode_mime_header(msg.get("Subject", ""))
            sender = decode_mime_header(msg.get("From", ""))
            body = extract_text_from_email(msg)
            attachments = get_attachments_info(msg)

            log.info(f"\n--- UID={uid_str} ---")
            log.info(f"  От: {sender}")
            log.info(f"  Тема: {subject}")
            if attachments:
                log.info(f"  Вложения: {', '.join(attachments)}")

            result = is_client_order(subject, sender, body, attachments)
            is_order = result.get("is_order", False)
            confidence = result.get("confidence", 0)
            reason = result.get("reason", "")

            if is_order and confidence >= 0.7:
                log.info(f"  → ЗАЯВКА ({confidence:.0%}) — {reason}")
                if not DRY_RUN:
                    if move_email(mail, uid, TARGET_FOLDER):
                        moved_count += 1
                else:
                    log.info(f"  → [DRY_RUN] Было бы перемещено")
            else:
                log.info(f"  → Не заявка ({confidence:.0%}) — {reason}")

            new_processed.add(uid_str)

        # Expunge один раз в конце
        if moved_count > 0:
            mail.expunge()
            log.info(f"\n✓ Перемещено заявок: {moved_count}")

    finally:
        mail.logout()
        log.info("Отключено от сервера")

    all_processed = processed_ids | new_processed
    if len(all_processed) > 5000:
        all_processed = set(list(all_processed)[-5000:])
    save_processed_ids(all_processed)

    log.info(f"\nОбработано: {len(new_processed)}, заявок: {moved_count}")
    log.info(f"{'='*50}\n")


if __name__ == "__main__":
    run()