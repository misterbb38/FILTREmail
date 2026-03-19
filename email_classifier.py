

# """
# Классификация писем Yandex через DeepSeek AI.
# Только письма за сегодня. Только заявки клиентов → папка Заявки.
# Остальные письма не трогаем.

# v2: Улучшенный промпт, отлов ответов клиентов, TEST_MODE.
#     Автоматическая проверка каждые CHECK_INTERVAL секунд.

# Запуск: python email_classifier.py
# Остановка: Ctrl+C (завершает корректно после текущего цикла)
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

# TARGET_FOLDER = "Заявки"
# SOURCE_FOLDER = "INBOX"
# MAX_EMAILS_PER_RUN = 200
# PROCESSED_FILE = "processed_emails.json"
# DRY_RUN = False

# # Интервал проверки в секундах (120 = 2 минуты)
# CHECK_INTERVAL = 120

# # ============================================================
# # РЕЖИМ ТЕСТИРОВАНИЯ
# # При TEST_MODE = True обрабатываются ТОЛЬКО письма
# # от/кому TEST_EMAIL (и ответы на них).
# # Поставьте False для боевого режима.
# # ============================================================
# TEST_MODE = True
# TEST_EMAIL = "amady305@gmail.com"

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
#     """
#     Проверяет, относится ли письмо к тестовому адресу:
#     - отправлено С тестового адреса
#     - является ответом/пересылкой, содержащей тестовый адрес в цепочке
#     """
#     sender_addr = extract_email_address(sender)
#     if TEST_EMAIL.lower() in sender_addr:
#         return True

#     # Проверяем Reply-To
#     reply_to = msg.get("Reply-To", "")
#     if TEST_EMAIL.lower() in reply_to.lower():
#         return True

#     # Проверяем In-Reply-To / References (цепочка ответов)
#     # Если письмо — ответ на что-то от тестового адреса,
#     # тестовый email может фигурировать в теле (цитата)
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

# КОНТЕКСТ: info@strbr.ru — это почта TWOWIN. Письмо пришло НА этот адрес. Определи: это заявка клиента или нет?

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

# 4. ОТВЕТ КЛИЕНТА В ЦЕПОЧКЕ ЗАКАЗА: Клиент отвечает на ранее выставленный счёт,
#    подтверждает заказ, уточняет позиции в рамках своего заказа, добавляет товары,
#    меняет количество, подтверждает оплату по СВОЕМУ заказу.
#    Ключевой признак: это ОТВЕТ (Re:) на тему, связанную с заказом, и клиент
#    что-то подтверждает, уточняет или дополняет.

# 5. ПОДБОР АНАЛОГА: Клиент просит подобрать аналог, замену, альтернативу товара.

# ❌ ЭТО НЕ ЗАЯВКА (is_order: false), если:

# 1. ПОСТАВЩИК пишет нам (прайс от поставщика, уведомление об отгрузке в наш адрес,
#    изменение цен поставщика). Признак: мы — ПОКУПАТЕЛЬ в этом письме.

# 2. РЕКЛАМА / СПАМ / РАССЫЛКА: маркетинговые материалы, подписки, новости отрасли.

# 3. СИСТЕМНЫЕ УВЕДОМЛЕНИЯ: Битрикс24, 1С, CRM, мониторинг, автоматика.

# 4. БУХГАЛТЕРИЯ без заказа: акт сверки, запрос закрывающих документов, сверка оплат,
#    запрос счёт-фактуры по уже закрытой сделке.

# 5. РЕКЛАМАЦИЯ / ВОЗВРАТ: жалоба на качество, запрос возврата, претензия.

# 6. ЛОГИСТИКА без нового заказа: трекинг, уведомление о доставке, вопрос "где груз?".

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

# ПОДСКАЗКА: Если не уверен — ставь confidence ниже 0.5 и is_order: false.
# Лучше пропустить, чем отправить не ту заявку в CRM."""


# def is_client_order(subject: str, sender: str, body: str, attachments: list[str]) -> dict:
#     """
#     Определяет, является ли письмо заявкой/заказом от клиента.
#     Возвращает {"is_order": true/false, "confidence": 0.0-1.0, "reason": "..."}
#     """
#     body_truncated = body[:3000] if body else "(пусто)"
#     attachments_str = ", ".join(attachments) if attachments else "нет"

#     user_prompt = CLASSIFICATION_USER_PROMPT.format(
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
#         return {"is_order": False, "confidence": 0, "reason": f"Ошибка парсинга JSON"}
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
# # ОСНОВНОЙ ЦИКЛ
# # ============================================================

# def run():
#     processed_ids = load_processed_ids()
#     new_processed = set()
#     moved_count = 0
#     skipped_test = 0

#     # Дата для IMAP SINCE (формат: 12-Mar-2026)
#     today_imap = date.today().strftime("%d-%b-%Y")

#     log.info(f"{'='*60}")
#     log.info(f"Запуск ({datetime.now().strftime('%Y-%m-%d %H:%M')})")
#     log.info(f"Ищем письма за сегодня ({today_imap})")
#     if TEST_MODE:
#         log.info(f"⚠ РЕЖИМ ТЕСТИРОВАНИЯ — только письма от/к {TEST_EMAIL}")
#     if DRY_RUN:
#         log.info("⚠ РЕЖИМ DRY_RUN — без перемещений")
#     log.info(f"{'='*60}")

#     try:
#         mail = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT)
#         mail.login(YANDEX_LOGIN, YANDEX_PASSWORD)
#         log.info("✓ Подключено к Yandex Mail")
#     except Exception as e:
#         log.error(f"✗ Ошибка подключения: {e}")
#         return

#     try:
#         mail.select(SOURCE_FOLDER)

#         # В тест-режиме ищем ВСЕ за сегодня (SEEN и UNSEEN),
#         # чтобы можно было тестировать с уже прочитанными
#         if TEST_MODE:
#             status, message_ids = mail.uid("SEARCH", None, f"(SINCE {today_imap})")
#         else:
#             status, message_ids = mail.uid("SEARCH", None, f"(UNSEEN SINCE {today_imap})")

#         if status != "OK":
#             log.error("Ошибка поиска писем")
#             return

#         uids = message_ids[0].split()
#         if not uids:
#             log.info("Нет писем за сегодня")
#             return

#         log.info(f"Найдено {len(uids)} писем за сегодня")

#         # Загружаем и фильтруем
#         emails_to_process = []
#         for uid in uids[:MAX_EMAILS_PER_RUN]:
#             uid_str = uid.decode()
#             if uid_str in processed_ids:
#                 continue
#             msg = fetch_email_by_uid(mail, uid)
#             if msg is None:
#                 log.warning(f"Не удалось загрузить UID={uid_str}")
#                 continue

#             sender = decode_mime_header(msg.get("From", ""))

#             # TEST_MODE: пропускаем всё кроме тестового адреса
#             if TEST_MODE and not is_test_email(sender, msg):
#                 skipped_test += 1
#                 continue

#             emails_to_process.append((uid, uid_str, msg))

#         if TEST_MODE and skipped_test > 0:
#             log.info(f"Пропущено (не от {TEST_EMAIL}): {skipped_test}")

#         if not emails_to_process:
#             log.info("Нет новых писем для анализа")
#             return

#         log.info(f"Новых для анализа: {len(emails_to_process)}")

#         # Классифицируем
#         for uid, uid_str, msg in emails_to_process:
#             subject = decode_mime_header(msg.get("Subject", ""))
#             sender = decode_mime_header(msg.get("From", ""))
#             body = extract_text_from_email(msg)
#             attachments = get_attachments_info(msg)

#             log.info(f"\n{'─'*50}")
#             log.info(f"UID={uid_str}")
#             log.info(f"  От: {sender}")
#             log.info(f"  Тема: {subject}")
#             if attachments:
#                 log.info(f"  Вложения: {', '.join(attachments)}")

#             result = is_client_order(subject, sender, body, attachments)
#             is_order = result.get("is_order", False)
#             confidence = result.get("confidence", 0)
#             reason = result.get("reason", "")

#             if is_order and confidence >= 0.7:
#                 log.info(f"  ✅ ЗАЯВКА ({confidence:.0%}) — {reason}")
#                 if not DRY_RUN:
#                     if move_email(mail, uid, TARGET_FOLDER):
#                         moved_count += 1
#                 else:
#                     log.info(f"  [DRY_RUN] Было бы перемещено в '{TARGET_FOLDER}'")
#             else:
#                 log.info(f"  ⬜ Не заявка ({confidence:.0%}) — {reason}")

#             new_processed.add(uid_str)

#         # Expunge один раз в конце
#         if moved_count > 0:
#             mail.expunge()
#             log.info(f"\n✓ Перемещено заявок: {moved_count}")

#     finally:
#         mail.logout()
#         log.info("Отключено от сервера")

#     all_processed = processed_ids | new_processed
#     if len(all_processed) > 5000:
#         all_processed = set(list(all_processed)[-5000:])
#     save_processed_ids(all_processed)

#     log.info(f"\nИтого: обработано {len(new_processed)}, заявок перемещено: {moved_count}")
#     log.info(f"{'='*60}\n")


# if __name__ == "__main__":
#     stop_flag = False

#     def handle_signal(sig, frame):
#         global stop_flag
#         log.info("\n⛔ Получен сигнал остановки. Завершаем после текущего цикла...")
#         stop_flag = True

#     signal.signal(signal.SIGINT, handle_signal)
#     signal.signal(signal.SIGTERM, handle_signal)

#     log.info(f"🚀 Классификатор запущен (каждые {CHECK_INTERVAL} сек.)")
#     log.info(f"   Остановка: Ctrl+C\n")

#     while not stop_flag:
#         try:
#             run()
#         except Exception as e:
#             log.error(f"Непредвиденная ошибка: {e}", exc_info=True)

#         if stop_flag:
#             break

#         log.info(f"💤 Следующая проверка через {CHECK_INTERVAL} сек...")
#         # Спим мелкими шагами, чтобы быстро реагировать на Ctrl+C
#         for _ in range(CHECK_INTERVAL):
#             if stop_flag:
#                 break
#             time.sleep(1)

#     log.info("✅ Классификатор остановлен.")


"""
Классификация писем Yandex через DeepSeek AI.
Только письма за сегодня. Только заявки клиентов → папка Заявки.
Остальные письма не трогаем.

v3: dotenv для секретов, готов к деплою на Render.

Запуск: python email_classifier.py
Остановка: Ctrl+C
"""

import imaplib
import email
from email.header import decode_header
import json
import os
import logging
import base64
import re
import signal
import time
from datetime import datetime, date
from dotenv import load_dotenv
from openai import OpenAI

# ============================================================
# ЗАГРУЗКА .env
# ============================================================
load_dotenv()

# ============================================================
# НАСТРОЙКИ ИЗ ПЕРЕМЕННЫХ ОКРУЖЕНИЯ
# ============================================================

YANDEX_LOGIN = os.getenv("YANDEX_LOGIN")
YANDEX_PASSWORD = os.getenv("YANDEX_PASSWORD")
IMAP_SERVER = "imap.yandex.ru"
IMAP_PORT = 993

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")

TARGET_FOLDER = "Заявки"
SOURCE_FOLDER = "INBOX"
MAX_EMAILS_PER_RUN = 50
PROCESSED_FILE = "processed_emails.json"

DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "120"))
TEST_MODE = os.getenv("TEST_MODE", "false").lower() == "true"
TEST_EMAIL = os.getenv("TEST_EMAIL", "")

# ============================================================
# ПРОВЕРКА ОБЯЗАТЕЛЬНЫХ ПЕРЕМЕННЫХ
# ============================================================

_missing = []
if not YANDEX_LOGIN:
    _missing.append("YANDEX_LOGIN")
if not YANDEX_PASSWORD:
    _missing.append("YANDEX_PASSWORD")
if not DEEPSEEK_API_KEY:
    _missing.append("DEEPSEEK_API_KEY")
if TEST_MODE and not TEST_EMAIL:
    _missing.append("TEST_EMAIL (обязателен при TEST_MODE=true)")
if _missing:
    raise SystemExit(f"❌ Отсутствуют переменные окружения: {', '.join(_missing)}\n"
                     f"   Создайте файл .env или задайте их в Environment на Render.")

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
            content = f.read().strip()
            if content:
                return set(json.loads(content))
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


def extract_email_address(from_header: str) -> str:
    """Извлекает чистый email из заголовка From."""
    match = re.search(r'[\w.+-]+@[\w.-]+\.\w+', from_header)
    return match.group(0).lower() if match else from_header.lower()


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


def is_test_email(sender: str, msg) -> bool:
    """
    Проверяет, относится ли письмо к тестовому адресу:
    - отправлено С тестового адреса
    - является ответом/пересылкой, содержащей тестовый адрес в цепочке
    """
    sender_addr = extract_email_address(sender)
    if TEST_EMAIL.lower() in sender_addr:
        return True

    reply_to = msg.get("Reply-To", "")
    if TEST_EMAIL.lower() in reply_to.lower():
        return True

    body = extract_text_from_email(msg)
    if TEST_EMAIL.lower() in body.lower():
        return True

    return False


# ============================================================
# КЛАССИФИКАЦИЯ: заявка клиента или нет?
# ============================================================

CLASSIFICATION_SYSTEM_PROMPT = """Ты — эксперт-классификатор входящей почты компании TWOWIN (СтройБразерс).

TWOWIN — дистрибьютор строительных материалов в Екатеринбурге. Компания ПОКУПАЕТ товар у ПОСТАВЩИКОВ и ПРОДАЁТ его КЛИЕНТАМ (строительные компании, подрядчики, магазины, частные лица).

Твоя задача: определить, является ли письмо ЗАЯВКОЙ КЛИЕНТА (т.е. входящим запросом на покупку от клиента TWOWIN).

ВАЖНО: Анализируй СМЫСЛ письма целиком, а не отдельные слова. Одно и то же слово "заказ" может быть в заявке клиента и в уведомлении от поставщика — ты должен понять КОНТЕКСТ.

Отвечай СТРОГО в формате JSON (без markdown, без комментариев):
{"is_order": true/false, "confidence": 0.0-1.0, "reason": "краткое пояснение на русском"}"""

CLASSIFICATION_USER_PROMPT = """Проанализируй это письмо.

КОНТЕКСТ: info@strbr.ru — это почта TWOWIN. Письмо пришло НА этот адрес. Определи: это заявка клиента или нет?

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

4. ОТВЕТ КЛИЕНТА В ЦЕПОЧКЕ ЗАКАЗА: Если тема письма содержит Re:/RE:/FW: и в теме
   есть слова "счёт", "заказ", "заявка", "КП", "спецификация", "прайс", "отгрузка"
   — то ЛЮБОЙ ответ от клиента является заявкой. Даже короткий:
   "Ок", "Хорошо", "Да", "Согласен", "Принято", "Берём", "Давайте", "Смотри",
   "Подтверждаю", "+", "Ждём", "Оплатили", или просто одно слово.
   ЛОГИКА: клиент продолжает диалог по заказу → это часть заказа → в CRM.
   Единственное исключение: если отправитель — ПОСТАВЩИК (не клиент).

5. ПОДБОР АНАЛОГА: Клиент просит подобрать аналог, замену, альтернативу товара.

❌ ЭТО НЕ ЗАЯВКА (is_order: false), если:

1. ПОСТАВЩИК пишет нам (прайс от поставщика, уведомление об отгрузке в наш адрес,
   изменение цен поставщика). Признак: мы — ПОКУПАТЕЛЬ в этом письме.

2. РЕКЛАМА / СПАМ / РАССЫЛКА: маркетинговые материалы, подписки, новости отрасли.

3. СИСТЕМНЫЕ УВЕДОМЛЕНИЯ: Битрикс24, 1С, CRM, мониторинг, автоматика.

4. БУХГАЛТЕРИЯ без заказа: акт сверки, запрос закрывающих документов, сверка оплат,
   запрос счёт-фактуры по уже закрытой сделке.

5. РЕКЛАМАЦИЯ / ВОЗВРАТ: жалоба на качество, запрос возврата, претензия.

6. ЛОГИСТИКА без нового заказа: трекинг, уведомление о доставке, вопрос "где груз?".

7. ОБЩАЯ ПЕРЕПИСКА: вопросы не связанные с покупкой товара, приветствия,
   организационные вопросы, договоры без конкретного заказа.

8. ВНУТРЕННЯЯ ПОЧТА: письма от сотрудников TWOWIN/СтройБразерс друг другу.

═══════════════════════════════════════
КАК ОТЛИЧИТЬ КЛИЕНТА ОТ ПОСТАВЩИКА:
═══════════════════════════════════════
- КЛИЕНТ хочет КУПИТЬ У НАС → заявка
- ПОСТАВЩИК предлагает НАМ КУПИТЬ → НЕ заявка
- Если в письме "ваш заказ №..." и контекст показывает, что это ПОСТАВЩИК
  сообщает нам о нашем заказе у него → НЕ заявка
- Если "заказ" в контексте "я хочу заказать у вас" → заявка

ПОДСКАЗКА: Если не уверен — ставь confidence ниже 0.5 и is_order: false.
Лучше пропустить, чем отправить не ту заявку в CRM."""


def is_client_order(subject: str, sender: str, body: str, attachments: list[str]) -> dict:
    """
    Определяет, является ли письмо заявкой/заказом от клиента.
    Возвращает {"is_order": true/false, "confidence": 0.0-1.0, "reason": "..."}
    """
    body_truncated = body[:3000] if body else "(пусто)"
    attachments_str = ", ".join(attachments) if attachments else "нет"

    user_prompt = CLASSIFICATION_USER_PROMPT.format(
        sender=sender,
        subject=subject,
        attachments=attachments_str,
        body=body_truncated,
    )

    try:
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": CLASSIFICATION_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.05,
            max_tokens=200,
        )

        text = response.choices[0].message.content.strip()
        text = text.replace("```json", "").replace("```", "").strip()
        return json.loads(text)

    except json.JSONDecodeError as e:
        log.error(f"Ошибка парсинга JSON от DeepSeek: {e} | Ответ: {text[:200]}")
        return {"is_order": False, "confidence": 0, "reason": "Ошибка парсинга JSON"}
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
    skipped_test = 0

    today_imap = date.today().strftime("%d-%b-%Y")

    log.info(f"{'='*60}")
    log.info(f"Запуск ({datetime.now().strftime('%Y-%m-%d %H:%M')})")
    log.info(f"Ищем письма за сегодня ({today_imap})")
    if TEST_MODE:
        log.info(f"⚠ РЕЖИМ ТЕСТИРОВАНИЯ — только письма от/к {TEST_EMAIL}")
    if DRY_RUN:
        log.info("⚠ РЕЖИМ DRY_RUN — без перемещений")
    log.info(f"{'='*60}")

    try:
        mail = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT)
        mail.login(YANDEX_LOGIN, YANDEX_PASSWORD)
        log.info("✓ Подключено к Yandex Mail")
    except Exception as e:
        log.error(f"✗ Ошибка подключения: {e}")
        return

    try:
        mail.select(SOURCE_FOLDER)

        if TEST_MODE:
            status, message_ids = mail.uid("SEARCH", None, f"(SINCE {today_imap})")
        else:
            status, message_ids = mail.uid("SEARCH", None, f"(UNSEEN SINCE {today_imap})")

        if status != "OK":
            log.error("Ошибка поиска писем")
            return

        uids = message_ids[0].split()
        if not uids:
            log.info("Нет писем за сегодня")
            return

        log.info(f"Найдено {len(uids)} писем за сегодня")

        # В TEST_MODE сканируем всё, в проде — лимит
        limit = len(uids) if TEST_MODE else MAX_EMAILS_PER_RUN

        emails_to_process = []
        for uid in uids[:limit]:
            uid_str = uid.decode()
            if uid_str in processed_ids:
                continue
            msg = fetch_email_by_uid(mail, uid)
            if msg is None:
                log.warning(f"Не удалось загрузить UID={uid_str}")
                continue

            sender = decode_mime_header(msg.get("From", ""))

            if TEST_MODE and not is_test_email(sender, msg):
                skipped_test += 1
                continue

            emails_to_process.append((uid, uid_str, msg))

        if TEST_MODE and skipped_test > 0:
            log.info(f"Пропущено (не от {TEST_EMAIL}): {skipped_test}")

        if not emails_to_process:
            log.info("Нет новых писем для анализа")
            return

        log.info(f"Новых для анализа: {len(emails_to_process)}")

        for uid, uid_str, msg in emails_to_process:
            subject = decode_mime_header(msg.get("Subject", ""))
            sender = decode_mime_header(msg.get("From", ""))
            body = extract_text_from_email(msg)
            attachments = get_attachments_info(msg)

            log.info(f"\n{'─'*50}")
            log.info(f"UID={uid_str}")
            log.info(f"  От: {sender}")
            log.info(f"  Тема: {subject}")
            if attachments:
                log.info(f"  Вложения: {', '.join(attachments)}")

            result = is_client_order(subject, sender, body, attachments)
            is_order = result.get("is_order", False)
            confidence = result.get("confidence", 0)
            reason = result.get("reason", "")

            if is_order and confidence >= 0.7:
                log.info(f"  ✅ ЗАЯВКА ({confidence:.0%}) — {reason}")
                if not DRY_RUN:
                    if move_email(mail, uid, TARGET_FOLDER):
                        moved_count += 1
                else:
                    log.info(f"  [DRY_RUN] Было бы перемещено в '{TARGET_FOLDER}'")
            else:
                log.info(f"  ⬜ Не заявка ({confidence:.0%}) — {reason}")

            new_processed.add(uid_str)

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

    log.info(f"\nИтого: обработано {len(new_processed)}, заявок перемещено: {moved_count}")
    log.info(f"{'='*60}\n")


# ============================================================
# ЗАПУСК
# ============================================================

if __name__ == "__main__":
    stop_flag = False

    def handle_signal(sig, frame):
        global stop_flag
        log.info("\n⛔ Получен сигнал остановки. Завершаем после текущего цикла...")
        stop_flag = True

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    log.info(f"🚀 Классификатор запущен (каждые {CHECK_INTERVAL} сек.)")
    log.info(f"   Остановка: Ctrl+C\n")

    while not stop_flag:
        try:
            run()
        except Exception as e:
            log.error(f"Непредвиденная ошибка: {e}", exc_info=True)

        if stop_flag:
            break

        log.info(f"💤 Следующая проверка через {CHECK_INTERVAL} сек...")
        for _ in range(CHECK_INTERVAL):
            if stop_flag:
                break
            time.sleep(1)

    log.info("✅ Классификатор остановлен.")