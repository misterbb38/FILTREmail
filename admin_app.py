"""
FILTREmail — Панель администратора
Просмотр классификаций DeepSeek, обратная связь, отслеживание точности ИИ.

Данные берутся из MongoDB Atlas (та же база, что и классификатор на Render).

Запуск:  python admin_app.py
Доступ:  http://localhost:5000
"""

import csv
import io
import os
from datetime import datetime, timedelta

from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, Response
from pymongo import MongoClient

load_dotenv()

app = Flask(__name__)

MONGO_URI = os.getenv("MONGO_URI")
MONGO_DB = os.getenv("MONGO_DB", "email_classifier")
PER_PAGE = 20


def get_db():
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=10_000)
    return client[MONGO_DB]


# ============================================================
# ГЛАВНАЯ СТРАНИЦА — СТАТИСТИКА
# ============================================================
@app.route("/")
def dashboard():
    db = get_db()
    processed = db["processed_emails"]
    feedbacks = db["feedbacks"]

    now = datetime.utcnow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_ago = now - timedelta(days=7)
    month_ago = now - timedelta(days=30)

    # Общая статистика
    total_all = processed.count_documents({})
    total_today = processed.count_documents({"processed_at": {"$gte": today_start}})
    total_week = processed.count_documents({"processed_at": {"$gte": week_ago}})
    total_month = processed.count_documents({"processed_at": {"$gte": month_ago}})

    orders_all = processed.count_documents({"is_order": True})
    not_orders_all = total_all - orders_all

    # Точность ИИ (на основе обратной связи)
    total_feedbacks = feedbacks.count_documents({})
    correct_feedbacks = feedbacks.count_documents({"ai_was_correct": True})
    accuracy = (correct_feedbacks / total_feedbacks * 100) if total_feedbacks > 0 else 0

    # Заявки по дням (7 дней) для графика
    daily_orders = []
    daily_labels = []
    for i in range(6, -1, -1):
        day_start = (now - timedelta(days=i)).replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)
        count = processed.count_documents({
            "is_order": True,
            "processed_at": {"$gte": day_start, "$lt": day_end},
        })
        daily_orders.append(count)
        daily_labels.append(day_start.strftime("%d/%m"))

    # Всего писем по дням (7 дней)
    daily_total = []
    for i in range(6, -1, -1):
        day_start = (now - timedelta(days=i)).replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)
        count = processed.count_documents({
            "processed_at": {"$gte": day_start, "$lt": day_end},
        })
        daily_total.append(count)

    # Распределение по ящикам
    pipeline = [
        {"$group": {"_id": "$mailbox", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
    ]
    mailbox_stats = list(processed.aggregate(pipeline))

    # Непроверенные письма
    verified_uids = set(doc["uid"] for doc in feedbacks.find({}, {"uid": 1}))
    unverified_count = processed.count_documents({"uid": {"$nin": list(verified_uids)}}) if verified_uids else total_all

    return render_template("dashboard.html",
        total_all=total_all,
        total_today=total_today,
        total_week=total_week,
        total_month=total_month,
        orders_all=orders_all,
        not_orders_all=not_orders_all,
        accuracy=accuracy,
        total_feedbacks=total_feedbacks,
        correct_feedbacks=correct_feedbacks,
        unverified_count=unverified_count,
        daily_labels=daily_labels,
        daily_orders=daily_orders,
        daily_total=daily_total,
        mailbox_stats=mailbox_stats,
    )


# ============================================================
# СПИСОК ПИСЕМ
# ============================================================
@app.route("/emails")
def emails_list():
    db = get_db()
    processed = db["processed_emails"]
    feedbacks_col = db["feedbacks"]

    # Фильтры
    mailbox_filter = request.args.get("mailbox", "")
    status_filter = request.args.get("status", "")
    admin_filter = request.args.get("admin", "")
    date_from = request.args.get("date_from", "")
    date_to = request.args.get("date_to", "")
    page = int(request.args.get("page", 1))

    query = {}
    if mailbox_filter:
        query["mailbox"] = mailbox_filter
    if status_filter == "order":
        query["is_order"] = True
    elif status_filter == "not_order":
        query["is_order"] = False
    if date_from:
        try:
            query.setdefault("processed_at", {})["$gte"] = datetime.strptime(date_from, "%Y-%m-%d")
        except ValueError:
            pass
    if date_to:
        try:
            query.setdefault("processed_at", {})["$lt"] = datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1)
        except ValueError:
            pass

    total = processed.count_documents(query)
    total_pages = max(1, (total + PER_PAGE - 1) // PER_PAGE)
    page = max(1, min(page, total_pages))

    emails = list(processed.find(query)
                  .sort("processed_at", -1)
                  .skip((page - 1) * PER_PAGE)
                  .limit(PER_PAGE))

    # Загрузить обратную связь для этих писем
    uids = [e["uid"] for e in emails]
    feedback_map = {}
    for fb in feedbacks_col.find({"uid": {"$in": uids}}):
        feedback_map[fb["uid"]] = fb

    # Фильтр по вердикту админа
    if admin_filter:
        filtered_emails = []
        for e in emails:
            fb = feedback_map.get(e["uid"])
            if admin_filter == "correct" and fb and fb.get("ai_was_correct"):
                filtered_emails.append(e)
            elif admin_filter == "incorrect" and fb and not fb.get("ai_was_correct"):
                filtered_emails.append(e)
            elif admin_filter == "unverified" and not fb:
                filtered_emails.append(e)
        emails = filtered_emails

    # Список ящиков для фильтра
    mailboxes = processed.distinct("mailbox")

    return render_template("emails.html",
        emails=emails,
        feedback_map=feedback_map,
        page=page,
        total_pages=total_pages,
        total=total,
        mailbox_filter=mailbox_filter,
        status_filter=status_filter,
        admin_filter=admin_filter,
        date_from=date_from,
        date_to=date_to,
        mailboxes=mailboxes,
    )


# ============================================================
# ДЕТАЛИ ПИСЬМА + ОБРАТНАЯ СВЯЗЬ
# ============================================================
@app.route("/emails/<path:uid>")
def email_detail(uid):
    db = get_db()
    email_doc = db["processed_emails"].find_one({"uid": uid})
    if not email_doc:
        return "Письмо не найдено", 404

    feedback = db["feedbacks"].find_one({"uid": uid})

    return render_template("email_detail.html",
        email=email_doc,
        feedback=feedback,
    )


@app.route("/emails/<path:uid>/feedback", methods=["POST"])
def submit_feedback(uid):
    db = get_db()
    email_doc = db["processed_emails"].find_one({"uid": uid})
    if not email_doc:
        return "Письмо не найдено", 404

    admin_verdict_str = request.form.get("admin_verdict")
    admin_note = request.form.get("admin_note", "").strip()
    admin_verdict = admin_verdict_str == "true"

    ai_was_correct = (email_doc.get("is_order", False) == admin_verdict)

    db["feedbacks"].update_one(
        {"uid": uid},
        {"$set": {
            "uid": uid,
            "admin_verdict": admin_verdict,
            "ai_was_correct": ai_was_correct,
            "ai_said_order": email_doc.get("is_order", False),
            "admin_note": admin_note,
            "created_at": datetime.utcnow(),
        }},
        upsert=True,
    )

    return redirect(url_for("email_detail", uid=uid))


# ============================================================
# ОБРАТНАЯ СВЯЗЬ — СПИСОК
# ============================================================
@app.route("/feedbacks")
def feedbacks_list():
    db = get_db()
    feedbacks_col = db["feedbacks"]
    processed = db["processed_emails"]

    total_feedbacks = feedbacks_col.count_documents({})
    correct = feedbacks_col.count_documents({"ai_was_correct": True})
    incorrect = feedbacks_col.count_documents({"ai_was_correct": False})
    accuracy = (correct / total_feedbacks * 100) if total_feedbacks > 0 else 0

    feedbacks = list(feedbacks_col.find().sort("created_at", -1).limit(100))

    # Обогатить данными письма
    for fb in feedbacks:
        email_doc = processed.find_one({"uid": fb["uid"]})
        if email_doc:
            fb["sender"] = email_doc.get("sender", "")
            fb["subject"] = email_doc.get("subject", "")
            fb["mailbox"] = email_doc.get("mailbox", "")

    return render_template("feedbacks.html",
        feedbacks=feedbacks,
        total_feedbacks=total_feedbacks,
        correct=correct,
        incorrect=incorrect,
        accuracy=accuracy,
    )


# ============================================================
# ЭКСПОРТ CSV
# ============================================================
@app.route("/export")
def export_csv():
    db = get_db()
    processed = db["processed_emails"]
    feedbacks_col = db["feedbacks"]

    emails = list(processed.find().sort("processed_at", -1))
    feedback_map = {fb["uid"]: fb for fb in feedbacks_col.find()}

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Дата", "Ящик", "Отправитель", "Тема", "Заявка (ИИ)",
        "Уверенность", "Причина ИИ", "Вердикт админа", "ИИ верно", "Заметка админа"
    ])

    for e in emails:
        fb = feedback_map.get(e.get("uid", ""))
        writer.writerow([
            e.get("processed_at", "").strftime("%Y-%m-%d %H:%M") if e.get("processed_at") else "",
            e.get("mailbox", ""),
            e.get("sender", ""),
            e.get("subject", ""),
            "Да" if e.get("is_order") else "Нет",
            f"{e.get('confidence', 0):.0%}",
            e.get("reason", ""),
            ("Заявка" if fb["admin_verdict"] else "Не заявка") if fb else "Не проверено",
            ("Да" if fb["ai_was_correct"] else "Нет") if fb else "",
            fb.get("admin_note", "") if fb else "",
        ])

    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=emails_export.csv"},
    )


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
