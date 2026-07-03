# -*- coding: utf-8 -*-
"""
AutoHabar Pro - Real Telegram Bot va Avtomatlashtirilgan Tarqatish Tizimi.
Ushbu skript Telegram Bot (aiogram v3) va Telegram MTProto Client (telethon) 
tizimlarini yagona asinxron motor va Google Cloud Firestore xizmati orqali birlashtiradi.
Render, Railway va barcha bulutli platformalarda 24/7 ishlaydi.
"""

import asyncio
import logging
import os
import sys
import shutil
import json
import base64
from datetime import datetime

# Loggerlarni eng tepada sozlaymiz (barcha xabarlar Renderda aniq ko'rinishi uchun)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

from aiogram import Bot, Dispatcher, types, Router, F, BaseMiddleware
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters import StateFilter, Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton, TelegramObject
from telethon import TelegramClient, errors, Button
from aiohttp import web

# Google Firebase Admin SDK import qilish
print("[Tizim] Firebase kutubxonalarini tekshirish boshlanmoqda...")
FIREBASE_AVAILABLE = False
try:
    import firebase_admin
    from firebase_admin import credentials, firestore
    FIREBASE_AVAILABLE = True
    print("[Firebase] OK: Barcha kutubxonalar muvaffaqiyatli yuklandi!")
except ImportError as e:
    print(f"[Firebase] XATO: Kutubxona import qilinmadi! Sababi: {e}")

# ================= CONFIGURATION =================
API_ID = 37104311
API_HASH = "f49729d10c144035c40f579b596d15b1"
BOT_TOKEN = "8680819777:AAFmbPFc6hNUk841ZaKlrnHlx1VrYfwebZA"
ADMIN_ID = 7073273800
APP_ID = "autohabar-bot"  # Loyihangizning maxsus ID raqami

# Papkalarni yaratish
SESSIONS_DIR = "sessions"
if os.path.exists(SESSIONS_DIR) and not os.path.isdir(SESSIONS_DIR):
    try:
        os.remove(SESSIONS_DIR)
    except Exception:
        pass

os.makedirs(SESSIONS_DIR, exist_ok=True)
DB_FILE = os.path.join(SESSIONS_DIR, "database.json")

# Boshlang'ich baza andozasi
DEFAULT_DB = {
    ADMIN_ID: {
        "balans": 0,
        "stars": 0,
        "is_pro": True,
        "referrals": 0,
        "reklama_matni": "🔥 AutoHabar Pro yordamida ishingizni yengillating!",
        "reklama_rasm": None,
        "inline_buttons": [],
        "interval": 15,
        "next_run_timestamp": 0,
        "active_phone": None,
        "active_name": "Admin",
        "active_username": "@admin",
        "is_sending": False,
        "groups_choice": "custom",
        "selected_groups": [],
        "cached_groups": [],
        "joined_time": datetime.now().strftime("%H:%M"),
        "today_sent": 0,
        "total_sent": 0,
        "channels": [],
        "auto_off_hours": None,
        "is_sending_started_at": 0,
        "referrals_count": 0,
        "referred_by": None,
        "forward_chat_id": None,
        "forward_msg_id": None,
        "is_forward_mode": False,
        "accounts": [],  # Barcha ulangan akkauntlar ro'yxati
        "auto_sub_active": True,
        "auto_reply_active": False,
        "lang": "uz"
    }
}

# ================= GOOGLE FIRESTORE CLOUD DATABASE =================
db = None
if FIREBASE_AVAILABLE:
    print("[Firebase] Baza ulanish yo'llarini qidirish...")
    possible_paths = [
        "firebase_credentials.json", 
        "/etc/secrets/firebase_credentials.json",
        "../firebase_credentials.json"
    ]
    for path in possible_paths:
        if os.path.exists(path):
            try:
                print(f"[Firebase] Topildi: {path} kalit fayli mavjud. Ulanyapti...")
                cred = credentials.Certificate(path)
                if not firebase_admin._apps:
                    firebase_admin.initialize_app(cred)
                db = firestore.client()
                print(f"[Firebase] MUVAFFAQIYAT: {path} orqali ulanish o'rnatildi!")
                break
            except Exception as e:
                print(f"[Firebase] XATO: {path} faylidan foydalanishda xatolik: {e}")
                
    if not db:
        print("[Firebase] Maxfiy fayllar topilmadi. Environment Variable tekshirilmoqda...")
        firebase_config_env = os.environ.get("FIREBASE_CONFIG_JSON")
        if firebase_config_env:
            try:
                cred_dict = json.loads(firebase_config_env)
                cred = credentials.Certificate(cred_dict)
                if not firebase_admin._apps:
                    firebase_admin.initialize_app(cred)
                db = firestore.client()
                print("[Firebase] MUVAFFAQIYAT: Render Environment Variable orqali ulandi!")
            except Exception as e:
                print(f"[Firebase] XATO: Env orqali ulanishda xato: {e}")
else:
    print("[Firebase] DIQQAT! Kutubxona o'rnatilmaganligi sababli Firebase tizimi o'chirildi.")

# ================= SESSIONS & DATABASE PERSISTENCE =================

def load_db():
    local_data = DEFAULT_DB
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                local_data = json.load(f)
                local_data = {int(k): v for k, v in local_data.items()}
        except Exception as e:
            logging.error(f"[Baza] Mahalliy bazani o'qishda xato: {e}")

    if db:
        try:
            doc_ref = db.collection('artifacts').document(APP_ID).collection('public').document('data').collection('database').document('main')
            doc = doc_ref.get()
            if doc.exists:
                data = doc.to_dict()
                print("[Baza] MUVAFFAQIYAT: Ma'lumotlar Google Cloud-dan muvaffaqiyatli yuklandi!")
                parsed_data = {int(k): v for k, v in data.items()}
                
                # Eski default kanallarni avtomatik tozalash
                if ADMIN_ID in parsed_data:
                    old_defaults = ["@autoxabarc_news", "@autoxabar_chat"]
                    current_chans = parsed_data[ADMIN_ID].get("channels", [])
                    cleaned_chans = [c for c in current_chans if c not in old_defaults]
                    parsed_data[ADMIN_ID]["channels"] = cleaned_chans
                    
                return parsed_data
            else:
                print("[Baza] Firestore - bo'sh. Mahalliy database.json bulutga nusxalanmoqda...")
                serializable_db = {str(k): v for k, v in local_data.items()}
                doc_ref.set(serializable_db)
                print("[Baza] MUVAFFAQIYAT: Mahalliy ma'lumotlar Firebase'ga to'liq ko'chirildi!")
                return local_data
        except Exception as e:
            print(f"[Baza] Firestore'dan yuklashda kutilmagan xatolik: {e}")
    else:
        print("[Baza] OGOHLANTIRISH: Firebase ulanmaganligi sababli vaqtinchalik mahalliy bazadan foydalanilmoqda.")
    
    return local_data

def save_db():
    try:
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump(db_users, f, ensure_ascii=False, indent=4)
    except Exception as e:
        logging.error(f"[Baza] Mahalliy bazaga yozishda xato: {e}")

    if db:
        try:
            serializable_db = {str(k): v for k, v in db_users.items()}
            doc_ref = db.collection('artifacts').document(APP_ID).collection('public').document('data').collection('database').document('main')
            doc_ref.set(serializable_db)
            logging.info("[Baza] Ma'lumotlar Google Cloud Firestore omboriga yozildi!")
        except Exception as e:
            logging.error(f"[Baza] Firestore'ga yozishda xatolik: {e}")

db_users = load_db()
active_clients = {}

def ensure_user(user_id: int):
    if user_id not in db_users:
        db_users[user_id] = {
            "balans": 0,
            "stars": 0,
            "is_pro": False,
            "referrals": 0,
            "reklama_matni": "🔥 AutoHabar Pro yordamida ishingizni yengillating!",
            "reklama_rasm": None,
            "inline_buttons": [],
            "interval": 15,
            "next_run_timestamp": 0,
            "active_phone": None,
            "active_name": "Foydalanuvchi",
            "active_username": "@-",
            "is_sending": False,
            "groups_choice": "custom",
            "selected_groups": [],
            "cached_groups": [],
            "joined_time": datetime.now().strftime("%H:%M"),
            "today_sent": 0,
            "total_sent": 0,
            "channels": [],
            "auto_off_hours": None,
            "is_sending_started_at": 0,
            "referrals_count": 0,
            "referred_by": None,
            "forward_chat_id": None,
            "forward_msg_id": None,
            "is_forward_mode": False,
            "accounts": [],
            "auto_sub_active": True,
            "auto_reply_active": False,
            "lang": None
        }
    else:
        # Maydonlarni to'g'ri yangilash
        if "referrals_count" not in db_users[user_id]:
            db_users[user_id]["referrals_count"] = 0
        if "referred_by" not in db_users[user_id]:
            db_users[user_id]["referred_by"] = None
        if "forward_chat_id" not in db_users[user_id]:
            db_users[user_id]["forward_chat_id"] = None
        if "forward_msg_id" not in db_users[user_id]:
            db_users[user_id]["forward_msg_id"] = None
        if "is_forward_mode" not in db_users[user_id]:
            db_users[user_id]["is_forward_mode"] = False
        if "auto_off_hours" not in db_users[user_id]:
            db_users[user_id]["auto_off_hours"] = None
        if "is_sending_started_at" not in db_users[user_id]:
            db_users[user_id]["is_sending_started_at"] = 0
        if "accounts" not in db_users[user_id]:
            db_users[user_id]["accounts"] = []
            if db_users[user_id].get("active_phone"):
                db_users[user_id]["accounts"].append({
                    "phone": db_users[user_id]["active_phone"],
                    "name": db_users[user_id].get("active_name", "Foydalanuvchi"),
                    "username": db_users[user_id].get("active_username", "@-")
                })
        if "auto_sub_active" not in db_users[user_id]:
            db_users[user_id]["auto_sub_active"] = True
        if "auto_reply_active" not in db_users[user_id]:
            db_users[user_id]["auto_reply_active"] = False
        if "lang" not in db_users[user_id]:
            db_users[user_id]["lang"] = None
    save_db()

async def backup_session_to_cloud(user_id, phone):
    if not db:
        return
    phone_clean = phone.replace("+", "").replace(" ", "")
    session_path = os.path.join(SESSIONS_DIR, f"session_{user_id}_{phone_clean}.session")
    if os.path.exists(session_path):
        try:
            temp_path = session_path + ".tmp"
            shutil.copy2(session_path, temp_path)
            with open(temp_path, "rb") as f:
                encoded_data = base64.b64encode(f.read()).decode('utf-8')
            os.remove(temp_path)
            
            doc_ref = db.collection('artifacts').document(APP_ID).collection('users').document(str(user_id)).collection('telethon_sessions').document(phone_clean)
            doc_ref.set({"binary_data": encoded_data, "updated_at": datetime.now().isoformat()})
            print(f"[Sessiya] Profil {user_id} ({phone}) ulanishi bulutga zaxiralandi!")
        except Exception as e:
            print(f"[Sessiya] Bulutga saqlashda xatolik: {e}")

async def restore_sessions_from_cloud():
    if not db:
        return
    try:
        users_ref = db.collection('artifacts').document(APP_ID).collection('users')
        users_docs = users_ref.stream()
        
        for user_doc in users_docs:
            user_id = user_doc.id
            sessions_ref = users_ref.document(user_id).collection('telethon_sessions')
            sessions_docs = sessions_ref.stream()
            
            for session_doc in sessions_docs:
                phone_clean = session_doc.id
                binary_data_b64 = session_doc.to_dict().get("binary_data")
                if binary_data_b64:
                    session_path = os.path.join(SESSIONS_DIR, f"session_{user_id}_{phone_clean}.session")
                    with open(session_path, "wb") as f:
                        f.write(base64.b64decode(binary_data_b64.encode('utf-8')))
                    print(f"[Sessiya] Bulutdan muvaffaqiyatli tiklandi: user_{user_id}_{phone_clean}.session")
    except Exception as e:
        print(f"[Sessiya] Bulutdan qayta tiklashda xatolik: {e}")

# ================= STATES FOR LOGIN & ACTIONS =================
class LoginStates(StatesGroup):
    waiting_phone = State()
    waiting_code = State()
    waiting_2fa = State()

class TextStates(StatesGroup):
    waiting_text = State()
    waiting_photo = State()
    waiting_buttons = State()
    waiting_forward = State()
    waiting_support_question = State()

class AdminStates(StatesGroup):
    waiting_search_id = State()
    waiting_add_balans = State()
    waiting_add_stars = State()
    waiting_add_channel = State()
    waiting_broadcast_msg = State()
    waiting_admin_reply = State()

# ================= MULTI-LANGUAGE LOCALIZATION SYSTEM =================
LOCALIZATION = {
    "uz": {
        "welcome": "📊 <b>Asosiy menyu:</b>\n<b>@Auto_Xabar_Yuborish_Bot</b>\n━━━━━━━━━━━━━━━━━━━━\nAssalomu alaykum, xush kelibsiz! 👋\n\n› Botimizdan foydalanish uchun\n› Akkaunt qo'shing\n› Guruhlarni sozlang\n› Habarni sozlang\n› Autohabarni ishga tushuring\n\n❓ Botdan qanday foydalanishni bilmasangiz, quyidagi <b>📖 Qo'llanma</b> tugmasini bosing!",
        "btn_auto_send": "⚪ Autohabar yuborish",
        "btn_msg_text": "📝 Habar matni",
        "btn_interval": "⏱️ Interval",
        "btn_groups": "💬 Guruhlarni sozlash",
        "btn_profiles": "👤 Profillar",
        "btn_guide": "📖 Qo'llanma",
        "btn_cabinet": "👤 Kabinet",
        "btn_settings": "⚙️ Sozlamalar",
        "btn_support": "❓ Savol va Yordam",
        "select_lang_text": "🌐 Iltimos, o'zingizga qulay bo'lgan tilni tanlang:\n\n🌐 Пожалуйста, выберите удобный для вас язык:\n\n🌐 Please select your preferred language:",
        "support_prompt": "✍️ <b>Savol yuborish bo'limi</b>\n\nIltimos, o'z savolingizni yoki murojaatingizni batafsil yozib yuboring. Tizim administratori tez fursatda sizga bot orqali javob yo'llaydi!",
        "support_sent": "✅ Savolingiz administratorga muvaffaqiyatli yetkazildi! Tez fursatda javob yo'llaymiz.",
        "settings_title": "⚙️ <b>Qo'shimcha Tizim Sozlamalari</b>\n━━━━━━━━━━━━━━━━━━━━\n🤖 Avto-obuna: <b>{auto_sub}</b>\n↩️ Auto Reply: <b>{auto_reply}</b>\n🌐 Til: <b>{lang_name}</b>\n🛡️ Anti-Ban: <b>Eng yuqori darajada (Maksimal) 🛡️</b>\n━━━━━━━━━━━━━━━━━━━━\nSozlamalarni o'zgartirish uchun kerakli tugmani bosing:",
        "guide_text": "📖 <b>AutoHabar Pro - Foydalanish Bo'yicha Batafsil Qo'llanma</b>\n━━━━━━━━━━━━━━━━━━━━\n1️⃣ <b>Akkaunt ulash:</b>\n• Profil bo'limidan akkaunt qo'shish tugmasini bosing va telefon raqamingizni xalqaro formatda kiriting.\n• SMS kod kelganda raqamlar orasiga albatta <b>nuqta qo'yib</b> kiriting (Format: <code>5.8.2.9.1</code>).\n\n2️⃣ <b>Guruhlarni sozlash:</b>\n• Guruhlarni sozlash bo'limiga kirib, xabar yuboriladigan guruhlarni belgilang va saqlang.\n\n3️⃣ <b>Interval va Taymer:</b>\n• Guruhlar orasidagi kutish vaqtini (Interval) va bot avtomatik o'chadigan taymer muddatini belgilang.\n\n4️⃣ <b>Tugatish:</b>\n• Autohabar yuborish bo'limidan <b>▶️ Ishga tushirish</b> tugmasini bosing!",
        "cabinet_title": "👤 <b>Sizning Kabinetingiz</b>\n\n👥 Ism: <b>{name}</b>\n🌐 Username: <b>{username}</b>\n💰 Balans: <b>{balans} so'm</b>\n\n📊 <b>Statistika:</b>\n✔️ Bugun yuborildi: <b>{today_sent}</b>\n🔄 Jami yuborilgan: <b>{total_sent}</b>\n👥 Ulangan akkauntlar: <b>{acc_count} / 5 ta</b>\n👥 Taklif qilingan a'zolar: <b>{referrals} / 6 ta</b>\n🔗 Havola: <code>{ref_link}</code>",
        "btn_change_lang": "🌐 Tilni o'zgartirish",
        "btn_add_acc": "➕ Akkaunt qo'shish"
    },
    "ru": {
        "welcome": "📊 <b>Главное меню:</b>\n<b>@Auto_Xabar_Yuborish_Bot</b>\n━━━━━━━━━━━━━━━━━━━━\nЗдравствуйте, добро пожаловать! 👋\n\n› Чтобы использовать нашего бота\n› Добавьте аккаунт\n› Настройте группы\n› Настройте сообщение\n› Запустите авторассылку\n\n❓ Если вы не знаете, как использовать бота, нажмите кнопку <b>📖 Руководство</b> ниже!",
        "btn_auto_send": "⚪ Авторассылка",
        "btn_msg_text": "📝 Текст сообщения",
        "btn_interval": "⏱️ Интервал",
        "btn_groups": "💬 Настройка групп",
        "btn_profiles": "👤 Профили",
        "btn_guide": "📖 Руководство",
        "btn_cabinet": "👤 Кабинет",
        "btn_settings": "⚙️ Настройки",
        "btn_support": "❓ Вопрос и Помощь",
        "select_lang_text": "🌐 Пожалуйста, выберите удобный для вас язык:",
        "support_prompt": "✍️ <b>Раздел отправки вопросов</b>\n\nПожалуйста, подробно напишите ваш вопрос или обращение. Администратор ответит вам через бота в ближайшее время!",
        "support_sent": "✅ Ваш вопрос успешно доставлен администратору! Мы ответим вам в ближайшее время.",
        "settings_title": "⚙️ <b>Дополнительные Системные Настройки</b>\n━━━━━━━━━━━━━━━━━━━━\n🤖 Автоподписка: <b>{auto_sub}</b>\n↩️ Автоответ: <b>{auto_reply}</b>\n🌐 Язык: <b>{lang_name}</b>\n🛡️ Анти-Ban: <b>На высшем уровне (Максимальный) 🛡️</b>\n━━━━━━━━━━━━━━━━━━━━\nНажмите кнопку для изменения настроек:",
        "guide_text": "📖 <b>AutoHabar Pro - Подробное Руководство</b>\n━━━━━━━━━━━━━━━━━━━━\n1️⃣ <b>Подключение аккаунта:</b>\n• В разделе профилей нажмите кнопку добавления аккаунта и введите номер телефона в международном формате.\n• При получении СМС-кода обязательно вводите его через <b>точку</b> (Формат: <code>5.8.2.9.1</code>).\n\n2️⃣ <b>Настройка групп:</b>\n• Перейдите в раздел настройки групп, выберите группы для рассылки и сохраните.\n\n3️⃣ <b>Интервал и Таймер:</b>\n• Установите время ожидания между группами (Интервал) и время автоотключения таймера.\n\n4️⃣ <b>Запуск:</b>\n• В разделе авторассылки нажмите кнопку <b>▶️ Запустить</b>!",
        "cabinet_title": "👤 <b>Ваш Кабинет</b>\n\n👥 Имя: <b>{name}</b>\n🌐 Юзернейм: <b>{username}</b>\n💰 Баланс: <b>{balans} сум</b>\n\n📊 <b>Статистика:</b>\n✔️ Сегодня отправлено: <b>{today_sent}</b>\n🔄 Всего отправлено: <b>{total_sent}</b>\n👥 Подключено аккаунтов: <b>{acc_count} / 5</b>\n👥 Приглашено друзей: <b>{referrals} / 6</b>\n🔗 Ссылка: <code>{ref_link}</code>",
        "btn_change_lang": "🌐 Сменить язык",
        "btn_add_acc": "➕ Добавить аккаунт"
    },
    "en": {
        "welcome": "📊 <b>Main Menu:</b>\n<b>@Auto_Xabar_Yuborish_Bot</b>\n━━━━━━━━━━━━━━━━━━━━\nHello, welcome! 👋\n\n› To use our bot\n› Add an account\n› Configure groups\n› Configure message\n› Start auto-send\n\n❓ If you don't know how to use the bot, click the <b>📖 Guide</b> button below!",
        "btn_auto_send": "⚪ Auto-send",
        "btn_msg_text": "📝 Message Text",
        "btn_interval": "⏱️ Interval",
        "btn_groups": "💬 Configure Groups",
        "btn_profiles": "👤 Profiles",
        "btn_guide": "📖 Guide",
        "btn_cabinet": "👤 Cabinet",
        "btn_settings": "⚙️ Settings",
        "btn_support": "❓ Q&A & Support",
        "select_lang_text": "🌐 Please select your preferred language:",
        "support_prompt": "✍️ <b>Support & Question Section</b>\n\nPlease write your question or appeal in detail. The administrator will reply to you through the bot shortly!",
        "support_sent": "✅ Your question has been successfully delivered to the admin! We will reply shortly.",
        "settings_title": "⚙️ <b>Additional System Settings</b>\n━━━━━━━━━━━━━━━━━━━━\n🤖 Auto-subscribe: <b>{auto_sub}</b>\n↩️ Auto Reply: <b>{auto_reply}</b>\n🌐 Language: <b>{lang_name}</b>\n🛡️ Anti-Ban: <b>On maximum level 🛡️</b>\n━━━━━━━━━━━━━━━━━━━━\nClick a button to change settings:",
        "guide_text": "📖 <b>AutoHabar Pro - Detailed User Guide</b>\n━━━━━━━━━━━━━━━━━━━━\n1️⃣ <b>Connecting Account:</b>\n• Go to Profiles, click add account and enter your phone number in international format.\n• When you receive the SMS code, enter it with <b>dots</b> between numbers (Format: <code>5.8.2.9.1</code>).\n\n2️⃣ <b>Configure Groups:</b>\n• Go to Configure Groups, select targeted groups and save.\n\n3️⃣ <b>Interval & Timer:</b>\n• Set the delay between groups (Interval) and auto-off timer duration.\n\n4️⃣ <b>Start:</b>\n• Click <b>▶️ Start</b> in the Auto-send section!",
        "cabinet_title": "👤 <b>Your Cabinet</b>\n\n👥 Name: <b>{name}</b>\n🌐 Username: <b>{username}</b>\n💰 Balance: <b>{balans} UZS</b>\n\n📊 <b>Statistics:</b>\n✔️ Today sent: <b>{today_sent}</b>\n🔄 Total sent: <b>{total_sent}</b>\n👥 Accounts connected: <b>{acc_count} / 5</b>\n👥 Referrals invited: <b>{referrals} / 6</b>\n🔗 Link: <code>{ref_link}</code>",
        "btn_change_lang": "🌐 Change Language",
        "btn_add_acc": "➕ Add Account"
    }
}

# Tillarni qulay olish uchun yordamchi funksiya
def get_text(user_id: int, key: str) -> str:
    ensure_user(user_id)
    lang = db_users[user_id].get("lang", "uz") or "uz"
    return LOCALIZATION.get(lang, LOCALIZATION["uz"]).get(key, LOCALIZATION["uz"].get(key, ""))

def get_main_keyboard(user_id: int) -> ReplyKeyboardMarkup:
    ensure_user(user_id)
    kb = [
        [KeyboardButton(text=get_text(user_id, "btn_auto_send")), KeyboardButton(text=get_text(user_id, "btn_msg_text"))],
        [KeyboardButton(text=get_text(user_id, "btn_interval")), KeyboardButton(text=get_text(user_id, "btn_groups"))],
        [KeyboardButton(text=get_text(user_id, "btn_profiles")), KeyboardButton(text=get_text(user_id, "btn_guide"))],
        [KeyboardButton(text=get_text(user_id, "btn_cabinet")), KeyboardButton(text=get_text(user_id, "btn_settings"))],
        [KeyboardButton(text=get_text(user_id, "btn_support"))]  # Savol va Yordam tugmasi
    ]
    if user_id == ADMIN_ID:
        kb.append([KeyboardButton(text="🛡️ Admin Panel")])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

# Tillarni tanlash inline klaviaturasi
def get_language_markup() -> InlineKeyboardMarkup:
    kb = [
        [
            InlineKeyboardButton(text="🇺🇿 O'zbekcha", callback_data="lang_uz"),
            InlineKeyboardButton(text="🇷🇺 Русский", callback_data="lang_ru"),
            InlineKeyboardButton(text="🇺🇸 English", callback_data="lang_en")
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)


# ================= GLOBAL MAJBURIY OBUNA NAZORATCHISI (MIDDLEWARE) =================

class MandatorySubMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: TelegramObject, data: dict):
        user = None
        if isinstance(event, types.Message):
            user = event.from_user
        elif isinstance(event, types.CallbackQuery):
            user = event.from_user

        if not user:
            return await handler(event, data)

        user_id = user.id

        # Admin har doim cheklovlardan ozod
        if user_id == ADMIN_ID:
            return await handler(event, data)

        # Tekshirish, til va to'ldirish tugmalarini aylanib qolmasligi uchun o'tkazamiz
        if isinstance(event, types.CallbackQuery) and (event.data in ["check_sub_status", "back_to_deposit", "deposit_balance", "back_to_panel"] or event.data.startswith("lang_")):
            return await handler(event, data)

        # Agar til hali belgilanmagan bo'lsa, obunani tekshirishdan oldin til tanlash oynasini ko'rsatamiz
        ensure_user(user_id)
        if db_users[user_id].get("lang") is None:
            return await handler(event, data)

        admin_data = db_users.get(ADMIN_ID, {})
        channels = admin_data.get("channels", [])

        if not channels:
            return await handler(event, data)

        unsubscribed_channels = []
        for channel in channels:
            chat_id = channel if channel.startswith("@") else f"@{channel}"
            try:
                member = await event.bot.get_chat_member(chat_id=chat_id, user_id=user_id)
                if member.status in ["left", "kicked"]:
                    unsubscribed_channels.append(channel)
            except Exception as e:
                logging.error(f"[Xavfsizlik] {channel} obunasini tekshirishda xato: {e}")
                unsubscribed_channels.append(channel)

        if unsubscribed_channels:
            try:
                await event.bot.send_message(
                    chat_id=user_id,
                    text="👋 <b>AutoHabar Pro botiga xush kelibsiz!</b>\n\nTizim boshqaruv menyusi yuklanmoqda...",
                    reply_markup=get_main_keyboard(user_id),
                    parse_mode="HTML"
                )
            except Exception as e:
                logging.error(f"[Xavfsizlik] Reply keyboard yuborishda xato: {e}")

            # Keyin esa Inline obuna tugmalarini ustidan chiqaramiz (Reply Keyboard aslo kollaps bo'lmaydi)
            markup_buttons = []
            for chan in unsubscribed_channels:
                clean_name = chan.replace("@", "")
                markup_buttons.append([InlineKeyboardButton(text=f"📢 {chan} kanaliga ulanish", url=f"https://t.me/{clean_name}")])
            
            markup_buttons.append([InlineKeyboardButton(text="✅ Obunani tekshirish", callback_data="check_sub_status")])
            markup = InlineKeyboardMarkup(inline_keyboard=markup_buttons)

            block_text = (
                "⚠️ <b>Bot xizmatlaridan foydalanish uchun quyidagi kanallarga a'zo bo'lishingiz shart!</b>\n\n"
                "Iltimos, obuna bo'ling va keyin pastdagi <b>✅ Obunani tekshirish</b> tugmasini bosing:"
            )

            if isinstance(event, types.Message):
                await event.answer(block_text, reply_markup=markup, parse_mode="HTML")
            elif isinstance(event, types.CallbackQuery):
                await event.message.answer(block_text, reply_markup=markup, parse_mode="HTML")
                await event.answer()
            return  

        return await handler(event, data)

# ================= CLIENT RECOVERY ENGINE =================
async def get_client(user_id, phone):
    phone_clean = phone.replace("+", "").replace(" ", "")
    session_key = f"{user_id}_{phone_clean}"
    client = active_clients.get(session_key)
    session_path = os.path.join(SESSIONS_DIR, f"session_{session_key}")
    
    if not client:
        client = TelegramClient(
            session_path, 
            API_ID, 
            API_HASH,
            loop=asyncio.get_running_loop(),
            connection_retries=15,
            retry_delay=2,
            auto_reconnect=True,
            device_model="AutoHabar Pro Client",
            system_version="Windows 11",
            app_version="2.2.0"
        )
        active_clients[session_key] = client
        
    if not client.is_connected():
        await client.connect()
        
    return client

# ================= BOT HANDLERS =================

@router.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    
    is_new_user = user_id not in db_users
    ensure_user(user_id)
    
    # Referal taklif havolasini tekshirish
    args = message.text.split()
    if is_new_user and len(args) > 1 and args[1].startswith("ref_"):
        try:
            referrer_id = int(args[1].split("_")[1])
            if referrer_id in db_users and referrer_id != user_id:
                db_users[user_id]["referred_by"] = referrer_id
                db_users[referrer_id]["referrals_count"] = db_users[referrer_id].get("referrals_count", 0) + 1
                save_db()
                
                # Refererga xabar yuborish
                try:
                    await bot.send_message(
                        referrer_id,
                        f"👤 Yangi do'stingiz sizning referal havolangiz orqali botga qo'shildi!\n"
                        f"Jami taklif qilgan faol a'zolaringiz: <b>{db_users[referrer_id]['referrals_count']} / 6 ta</b>",
                        parse_mode="HTML"
                    )
                except Exception:
                    pass
                
                # 6 ta yangi a'zo taklif qilsa, avtomatik ravishda bepul PRO beriladi!
                if db_users[referrer_id]["referrals_count"] >= 6 and not db_users[referrer_id].get("is_pro", False):
                    db_users[referrer_id]["is_pro"] = True
                    save_db()
                    try:
                        await bot.send_message(
                            referrer_id,
                            "👑 <b>TABRIKLAYMIZ!</b>\n\n"
                            "Siz muvaffaqiyatli ravishda 6 ta faol do'stingizni taklif qildingiz va "
                            "<b>AutoHabar PRO</b> tarifini butunlay bepul qo'lga kiritdingiz! 🎉",
                            parse_mode="HTML"
                        )
                    except Exception:
                        pass
        except Exception as e:
            logging.error(f"[Referral] Tizim xatosi: {e}")
            
    # Agar foydalanuvchi tilni tanlamagan bo'lsa, til tanlash oynasini birinchi yuboramiz
    if db_users[user_id].get("lang") is None:
        await message.answer(LOCALIZATION["uz"]["select_lang_text"], reply_markup=get_language_markup())
        return

    await send_welcome_and_keyboard(message, user_id)

async def send_welcome_and_keyboard(message: types.Message, user_id: int):
    text = get_text(user_id, "welcome")
    user_data = db_users.get(user_id)
    
    if user_data and not user_data.get("active_phone"):
        inline_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=get_text(user_id, "btn_add_acc"), callback_data="add_account")]
        ])
        await message.answer(text, reply_markup=inline_kb, parse_mode="HTML")
    else:
        await message.answer(text, parse_mode="HTML")
        
    await message.answer(
        "🎛️ " + get_text(user_id, "btn_settings") + "...",
        reply_markup=get_main_keyboard(user_id)
    )

# Til tanlangandagi callback drayveri
@router.callback_query(F.data.startswith("lang_"))
async def callback_select_lang(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    ensure_user(user_id)
    
    selected_lang = callback_query.data.split("_")[1]
    db_users[user_id]["lang"] = selected_lang
    save_db()
    
    lang_name = "O'zbekcha 🇺🇿" if selected_lang == "uz" else "Русский 🇷🇺" if selected_lang == "ru" else "English 🇺🇸"
    await callback_query.answer(f"✓ {lang_name}", show_alert=True)
    await callback_query.message.delete()
    
    await send_welcome_and_keyboard(callback_query.message, user_id)

# ================= 📩 SAVOL VA YORDAM (SUPPORT SYSTEM) =================

@router.message(F.text.in_([LOCALIZATION["uz"]["btn_support"], LOCALIZATION["ru"]["btn_support"], LOCALIZATION["en"]["btn_support"]]))
async def menu_support_handler(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    ensure_user(user_id)
    
    await message.answer(get_text(user_id, "support_prompt"), parse_mode="HTML")
    await state.set_state(TextStates.waiting_support_question)

@router.message(StateFilter(TextStates.waiting_support_question))
async def message_receive_support_question(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    ensure_user(user_id)
    
    question_text = message.text
    if not question_text:
        await message.answer("⚠️ Iltimos, savolingizni matn ko'rinishida yuboring!")
        return
        
    # Adminga avtomatik tarzda jo'natish
    user_lang = db_users[user_id].get("lang", "uz") or "uz"
    admin_notification = (
        f"📩 <b>Yangi Yordam So'rovi!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Foydalanuvchi: <b>{message.from_user.first_name}</b>\n"
        f"Username: @{message.from_user.username or 'yoq'}\n"
        f"ID: <code>{user_id}</code>\n"
        f"Tanlangan til: <b>{user_lang.upper()}</b>\n"
        f"Vaqt: <i>{datetime.now().strftime('%d.%m %H:%M')}</i>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Savol:\n<i>\"{question_text}\"</i>"
    )
    
    # Javob berish tugmasi
    admin_markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✍️ Javob berish", callback_data=f"reply_to_user_{user_id}")]
    ])
    
    try:
        await bot.send_message(chat_id=ADMIN_ID, text=admin_notification, reply_markup=admin_markup, parse_mode="HTML")
    except Exception as e:
        logging.error(f"[Support] Adminga xabar yuborishda xato: {e}")
        
    await message.answer(get_text(user_id, "support_sent"), reply_markup=get_main_keyboard(user_id), parse_mode="HTML")
    await state.clear()

# Admin javob berishni bosganda
@router.callback_query(F.data.startswith("reply_to_user_"))
async def callback_admin_reply_prompt(callback_query: types.CallbackQuery, state: FSMContext):
    if callback_query.from_user.id != ADMIN_ID:
        return
        
    target_user_id = int(callback_query.data.replace("reply_to_user_", ""))
    await state.update_data(target_user_id=target_user_id)
    await state.set_state(AdminStates.waiting_admin_reply)
    
    await callback_query.message.answer(
        f"✍️ <b>Foydalanuvchiga javob yozish</b>\n\n"
        f"Target User ID: <code>{target_user_id}</code>\n\n"
        f"Iltimos, yuboriladigan javob matnini yozing:",
        parse_mode="HTML"
    )
    await callback_query.answer()

# Admin javobni yuborganida foydalanuvchiga yetkazish
@router.message(StateFilter(AdminStates.waiting_admin_reply))
async def state_process_admin_reply(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
        
    data = await state.get_data()
    target_user_id = data.get("target_user_id")
    reply_text = message.text
    
    if not reply_text:
        await message.answer("⚠️ Iltimos, javobni matn ko'rinishida yozing!")
        return
        
    user_msg = (
        f"🔔 <b>Administrator Javobi Yo'llandi</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>\"{reply_text}\"</i>\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Yordam kerak bo'lsa, yana murojaat qilishingiz mumkin. Rahmat!"
    )
    
    try:
        await bot.send_message(chat_id=target_user_id, text=user_msg, reply_markup=get_main_keyboard(target_user_id), parse_mode="HTML")
        await message.answer("✅ Javobingiz foydalanuvchiga muvaffaqiyatli yuborildi!")
    except Exception as e:
        await message.answer(f"❌ Javobni yuborishda xatolik yuz berdi (Foydalanuvchi botni bloklagan bo'limda): {e}")
        
    await state.clear()

# ===================================================================================

@router.message(F.text.in_([LOCALIZATION["uz"]["btn_guide"], LOCALIZATION["ru"]["btn_guide"], LOCALIZATION["en"]["btn_guide"]]))
async def menu_guide_handler(message: types.Message):
    user_id = message.from_user.id
    ensure_user(user_id)
    await message.answer(get_text(user_id, "guide_text"), reply_markup=get_main_keyboard(user_id), parse_mode="HTML")

@router.message(F.text.in_([LOCALIZATION["uz"]["btn_cabinet"], LOCALIZATION["ru"]["btn_cabinet"], LOCALIZATION["en"]["btn_cabinet"]]))
async def menu_kabinet(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    ensure_user(user_id)
    await menu_kabinet_msg(message, user_id)

async def menu_kabinet_msg(message: types.Message, user_id: int):
    user_data = db_users.get(user_id)
    
    username = user_data.get("active_username") or "@-"
    name = user_data.get("active_name") or "Mavjud emas"
    
    ref_link = f"https://t.me/Auto_Xabar_Yuborish_Bot?start=ref_{user_id}"
    accounts_list = user_data.get("accounts", [])
    
    profiles_text = ""
    if accounts_list:
        for idx, acc in enumerate(accounts_list, 1):
            status = " (Faol)" if acc["phone"] == user_data.get("active_phone") else ""
            profiles_text += f"📞 {idx}. <b>{acc['phone']}</b>{status}\n"
    else:
        profiles_text = "❌ Profillar ulanmagan.\n"
        
    lang_name = "uz" if user_data.get("lang") == "uz" else "ru" if user_data.get("lang") == "ru" else "en"
    cabinet_template = LOCALIZATION[lang_name]["cabinet_title"]
    
    text = cabinet_template.format(
        name=name,
        username=username,
        balans=f"{user_data.get('balans', 0):,}",
        today_sent=user_data.get('today_sent', 0),
        total_sent=user_data.get('total_sent', 0),
        acc_count=len(accounts_list),
        referrals=user_data.get('referrals_count', 0),
        ref_link=ref_link
    )
    
    # Profil ulangan ro'yxatini matnga qo'shamiz
    text += f"\n\n👥 <b>Ulangan barcha profillaringiz:</b>\n{profiles_text}"
    
    inline_kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="💰 Hisobni to'ldirish", callback_data="deposit_balance"),
            InlineKeyboardButton(text="⚠️ Profilni uzish", callback_data="disconnect_profile")
        ],
        [InlineKeyboardButton(text="❌ Yopish", callback_data="close_menu")]
    ])
    try:
        await message.answer(text, reply_markup=inline_kb, parse_mode="HTML")
    except Exception:
        await message.edit_text(text, reply_markup=inline_kb, parse_mode="HTML")

@router.message(F.text.in_([LOCALIZATION["uz"]["btn_settings"], LOCALIZATION["ru"]["btn_settings"], LOCALIZATION["en"]["btn_settings"]]))
async def menu_sozlamalar(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    ensure_user(user_id)
    await show_sozlamalar_menu(message, user_id)

async def show_sozlamalar_menu(message: types.Message, user_id: int):
    user_data = db_users.get(user_id)
    
    auto_sub = "Yoqilgan 🟢" if user_data.get("auto_sub_active", True) else "O'chirilgan 🔴"
    auto_reply = "Yoqilgan 🟢" if user_data.get("auto_reply_active", False) else "O'chirilgan 🔴"
    
    lang_code = user_data.get("lang", "uz") or "uz"
    lang_name = "O'zbekcha 🇺🇿" if lang_code == "uz" else "Русский 🇷🇺" if lang_code == "ru" else "English 🇺🇸"
    
    settings_template = LOCALIZATION[lang_code]["settings_title"]
    text = settings_template.format(
        auto_sub=auto_sub,
        auto_reply=auto_reply,
        lang_name=lang_name
    )
    
    kb = [
        [
            InlineKeyboardButton(text="🤖 Avto-obunani o'zgartirish", callback_data="toggle_auto_sub"),
            InlineKeyboardButton(text="↩️ Auto Reply o'zgartirish", callback_data="toggle_auto_reply")
        ],
        [
            InlineKeyboardButton(text="🌐 Tilni o'zgartirish / Сменить язык / Change Language", callback_data="change_language_settings")
        ],
        [InlineKeyboardButton(text="❌ Yopish", callback_data="close_menu")]
    ]
    
    try:
        await message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="HTML")
    except Exception:
        await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="HTML")

@router.callback_query(F.data == "change_language_settings")
async def callback_change_language_settings(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    ensure_user(user_id)
    
    # Foydalanuvchiga til tanlash inline tugmalarini chizamiz
    await callback_query.message.edit_text(LOCALIZATION["uz"]["select_lang_text"], reply_markup=get_language_markup())
    await callback_query.answer()

@router.callback_query(F.data == "toggle_auto_sub")
async def callback_toggle_auto_sub(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    ensure_user(user_id)
    db_users[user_id]["auto_sub_active"] = not db_users[user_id].get("auto_sub_active", True)
    save_db()
    
    status = "yoqildi 🟢" if db_users[user_id]["auto_sub_active"] else "o'chirildi 🔴"
    await callback_query.answer(f"✓ Avto-obuna {status}!", show_alert=True)
    await show_sozlamalar_menu(callback_query.message, user_id)

@router.callback_query(F.data == "toggle_auto_reply")
async def callback_toggle_auto_reply(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    ensure_user(user_id)
    db_users[user_id]["auto_reply_active"] = not db_users[user_id].get("auto_reply_active", False)
    save_db()
    
    status = "yoqildi 🟢" if db_users[user_id]["auto_reply_active"] else "o'chirildi 🔴"
    await callback_query.answer(f"✓ Auto Reply {status}!", show_alert=True)
    await show_sozlamalar_menu(callback_query.message, user_id)

@router.callback_query(F.data == "close_menu")
async def callback_close_menu(callback_query: types.CallbackQuery):
    try:
        await callback_query.message.delete()
    except Exception:
        pass
    await callback_query.answer()

# ================= ADMIN PANEL HANDLERS =================

@router.message(F.text == "🛡️ Admin Panel")
async def cmd_admin(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await state.clear()
    
    text = (
        "🛡️ <b>AutoHabar Pro - Tizim Admin Paneli</b>\n\n"
        "Boshqaruv bo'limini tanlang:"
    )
    await message.answer(text, reply_markup=get_admin_main_markup(), parse_mode="HTML")

@router.callback_query(F.data == "adm_main_menu")
async def callback_adm_main(callback_query: types.CallbackQuery, state: FSMContext = None):
    if callback_query.from_user.id != ADMIN_ID:
        await callback_query.answer("Ruxsat berilmagan!", show_alert=True)
        return
    if state:
        await state.clear()
    text = (
        "🛡️ <b>AutoHabar Pro - Tizim Admin Paneli</b>\n\n"
        "Boshqaruv bo'limini tanlang:"
    )
    try:
        await callback_query.message.edit_text(text, reply_markup=get_admin_main_markup(), parse_mode="HTML")
    except Exception:
        await callback_query.message.answer(text, reply_markup=get_admin_main_markup(), parse_mode="HTML")
    await callback_query.answer()

@router.callback_query(F.data == "adm_stats")
async def callback_adm_stats(callback_query: types.CallbackQuery):
    if callback_query.from_user.id != ADMIN_ID:
        return
    total_users = len(db_users)
    pro_users = sum(1 for u in db_users.values() if u.get("is_pro", False))
    active_senders = sum(1 for u in db_users.values() if u.get("is_sending", False))
    total_sent = sum(u.get("total_sent", 0) for u in db_users.values())
    
    text = (
        "📊 <b>Botning real vaqt rejimidagi statistikasi:</b>\n\n"
        f"👥 Jami foydalanuvchilar: <b>{total_users} ta</b>\n"
        f"👑 VIP (PRO) a'zolar: <b>{pro_users} ta</b>\n"
        f"🟢 Faol yuboruvchilar: <b>{active_senders} ta</b>\n"
        f"📤 Jami tarqatilgan xabarlar: <b>{total_sent} ta</b>\n\n"
        f"🕒 Yangilangan vaqt: <i>{datetime.now().strftime('%d.%m.%Y %H:%M:%S')}</i>"
    )
    inline_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Admin Menyu", callback_data="adm_main_menu")]
    ])
    await callback_query.message.edit_text(text, reply_markup=inline_kb, parse_mode="HTML")
    await callback_query.answer()

@router.callback_query(F.data == "adm_search_user")
async def callback_adm_search_prompt(callback_query: types.CallbackQuery, state: FSMContext):
    if callback_query.from_user.id != ADMIN_ID:
        return
    await state.set_state(AdminStates.waiting_search_id)
    text = (
        "👤 <b>Foydalanuvchini sozlash bo'limi</b>\n\n"
        "Iltimos, boshqarmoqchi bo'lgan foydalanuvchining <b>Telegram ID</b> raqamini kiriting:"
    )
    inline_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Orqaga", callback_data="adm_main_menu")]
    ])
    await callback_query.message.edit_text(text, reply_markup=inline_kb, parse_mode="HTML")
    await callback_query.answer()

@router.message(StateFilter(AdminStates.waiting_search_id))
async def admin_user_search_process(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    try:
        target_id = int(message.text.strip())
        if target_id in db_users:
            await state.update_data(target_id=target_id)
            user_data = db_users[target_id]
            
            tarif_nomi = "PRO 👑" if user_data.get("is_pro") else "FREE 👤"
            active_phone = user_data.get("active_phone") or "Ulanmagan"
            
            text = (
                f"👤 <b>Foydalanuvchi topildi! (ID: {target_id})</b>\n\n"
                f"🏷️ Ism: <b>{user_data.get('active_name', 'Mavjud emas')}</b>\n"
                f"🌐 Username: <b>{user_data.get('active_username', '@-')}</b>\n"
                f"📞 Aloqa raqam: <b>+{active_phone.replace('+', '') if active_phone != 'Ulanmagan' else active_phone}</b>\n"
                f"🛡️\n🛡️ Joriy tarif: <b>{tarif_nomi}</b>\n"
                f"💰 Pul Balans: <b>{user_data.get('balans', 0):,} so'm</b>\n"
                f"⭐ Stars Balans: <b>{user_data.get('stars', 0)} ⭐️</b>\n"
                f"📤 Jami yuborgan xabarlari: <b>{user_data.get('total_sent', 0)} ta</b>"
            )
            
            inline_kb = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="💰 Balans tahrirlash", callback_data=f"adm_chg_bal_{target_id}"),
                    InlineKeyboardButton(text="⭐️ Stars tahrirlash", callback_data=f"adm_chg_stars_{target_id}"),
                ],
                [
                    InlineKeyboardButton(text="👑 PRO / FREE o'tkazish", callback_data=f"adm_chg_tarif_{target_id}")
                ],
                [
                    InlineKeyboardButton(text="⬅️ Admin Menyu", callback_data="adm_main_menu")
                ]
            ])
            await message.answer(text, reply_markup=inline_kb, parse_mode="HTML")
            await state.clear()
        else:
            await message.answer("❌ Bunday IDga ega foydalanuvchi topilmadi! Qaytadan kiriting yoki ⬅️ Orqaga tugmasini bosing:")
    except ValueError:
        await message.answer("❌ ID raqam faqat butun sonlardan iborat bo'lik kerak! Qaytadan kiriting:")

@router.callback_query(F.data.startswith("adm_chg_bal_"))
async def callback_adm_chg_bal_prompt(callback_query: types.CallbackQuery, state: FSMContext):
    if callback_query.from_user.id != ADMIN_ID:
        return
    target_id = int(callback_query.data.split("_")[3])
    await state.update_data(target_id=target_id)
    await state.set_state(AdminStates.waiting_add_balans)
    
    await callback_query.message.edit_text(
        f"💰 <b>Balansni tahrirlash (User ID: {target_id})</b>\n\n"
        "Balansga pul qo'shish uchun: <code>+50000</code>\n"
        "Hisobdan pul ayirish uchun: <code>-30000</code> kabi qiymat yuboring:",
        parse_mode="HTML"
    )
    await callback_query.answer()

@router.message(StateFilter(AdminStates.waiting_add_balans))
async def state_process_add_balans(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    data = await state.get_data()
    target_id = data.get("target_id")
    val_str = message.text.strip()
    
    try:
        change_amount = int(val_str)
        if target_id in db_users:
            current_bal = db_users[target_id].get("balans", 0)
            new_bal = current_bal + change_amount
            if new_bal < 0:
                new_bal = 0
            db_users[target_id]["balans"] = new_bal
            save_db()
            
            await message.answer(
                f"✅ <b>Balans muvaffaqiyatli o'zgartirildi!</b>\n"
                f"Eski balans: {current_bal:,} so'm\n"
                f"Yangi balans: <b>{new_bal:,} so'm</b>",
                reply_markup=get_main_keyboard(target_id),
                parse_mode="HTML"
            )
            try:
                await bot.send_message(target_id, f"💰 Tizim administratori hisobingiz balansini o'zgartirdi!\nJoriy balans: <b>{new_bal:,} so'm</b>", parse_mode="HTML")
            except Exception:
                pass
        else:
            await message.answer("❌ Foydalanuvchi bazadan o'chib ketgan.")
    except ValueError:
        await message.answer("❌ Noto'g'ri qiymat kiritildi. Faqat raqam yoki + / - belgisidan foydalaning (masalan: +25000):")
    await state.clear()

@router.callback_query(F.data.startswith("adm_chg_stars_"))
async def callback_adm_chg_stars_prompt(callback_query: types.CallbackQuery, state: FSMContext):
    if callback_query.from_user.id != ADMIN_ID:
        return
    target_id = int(callback_query.data.split("_")[3])
    await state.update_data(target_id=target_id)
    await state.set_state(AdminStates.waiting_add_stars)
    
    await callback_query.message.edit_text(
        f"⭐️ <b>Telegram Stars balansini tahrirlash (User ID: {target_id})</b>\n\n"
        "Stars qo'shish uchun: <code>+50</code>\n"
        "Stars ayirish uchun: <code>-30</code> kabi qiymat yuboring:",
        parse_mode="HTML"
    )
    await callback_query.answer()

@router.message(StateFilter(AdminStates.waiting_add_stars))
async def state_process_add_stars(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    data = await state.get_data()
    target_id = data.get("target_id")
    val_str = message.text.strip()
    
    try:
        change_amount = int(val_str)
        if target_id in db_users:
            current_stars = db_users[target_id].get("stars", 0)
            new_stars = current_stars + change_amount
            if new_stars < 0:
                new_stars = 0
            db_users[target_id]["stars"] = new_stars
            save_db()
            
            await message.answer(
                f"✅ <b>Stars balans o'zgartirildi!</b>\n"
                f"Eski: {current_stars} ⭐️\n"
                f"Yangi: <b>{new_stars} ⭐️</b>",
                reply_markup=get_main_keyboard(target_id),
                parse_mode="HTML"
            )
            try:
                await bot.send_message(target_id, f"⭐️ Tizim administratori hisobingizga Stars taqdim etdi!\nJoriy stars: <b>{new_stars} ⭐️</b>", parse_mode="HTML")
            except Exception:
                pass
        else:
            await message.answer("❌ Foydalanuvchi topilmadi.")
    except ValueError:
        await message.answer("❌ Noto'g'ri format! Faqat son yozing (masalan: +10):")
    await state.clear()

@router.callback_query(F.data.startswith("adm_chg_tarif_"))
async def callback_adm_chg_tarif(callback_query: types.CallbackQuery):
    if callback_query.from_user.id != ADMIN_ID:
        return
    target_id = int(callback_query.data.split("_")[3])
    if target_id in db_users:
        current_status = db_users[target_id].get("is_pro", False)
        new_status = not current_status
        db_users[target_id]["is_pro"] = new_status
        save_db()
        
        status_nomi = "PRO 👑" if new_status else "FREE 👤"
        await callback_query.answer(f"Tarif muvaffaqiyatli {status_nomi} ga o'zgartirildi!", show_alert=True)
        try:
            tabrik = "👑 <b>Tabriklaymiz! Tizim administratori sizga cheksiz PRO tarifini taqdim etdi!</b>\nEndi barcha yopiq xizmatlar siz uchun ochoq." if new_status else "⚠️ Hisobingizdagi PRO tarifi administrator tomonidan bekor qilindi va bepul rejimga qaytarildingiz."
            await bot.send_message(target_id, tabrik, parse_mode="HTML")
        except Exception:
            pass
        
        text = (
            "🛡️ <b>AutoHabar Pro - Tizim Admin Paneli</b>\n\n"
            "Boshqaruv bo'limini tanlang:"
        )
        try:
            await callback_query.message.edit_text(text, reply_markup=get_admin_main_markup(), parse_mode="HTML")
        except Exception:
            pass
    else:
        await callback_query.answer("Foydalanuvchi topilmadi!", show_alert=True)

@router.callback_query(F.data == "adm_mandatory_sub")
async def callback_adm_sub_menu(callback_query: types.CallbackQuery):
    if callback_query.from_user.id != ADMIN_ID:
        return
    channels = db_users[ADMIN_ID].get("channels", [])
    text = (
        "📢 <b>Majburiy obuna kanallarini sozlash</b>\n\n"
        "Foydalanuvchi botni start qilganda quyidagi majburiy kanallarga a'zo bo'lishi shart qilib ko'rsatiladi:\n\n"
    )
    if channels:
        for idx, chan in enumerate(channels, 1):
            text += f"{idx}. <b>{chan}</b>\n"
    else:
        text += "❌ Hozirda hech qanday majburiy kanal o'rnatilmagan."
        
    inline_kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="➕ Kanal qo'shish", callback_data="adm_sub_add_chan"),
            InlineKeyboardButton(text="❌ Hammasini tozalash", callback_data="adm_sub_clear_chan")
        ],
        [InlineKeyboardButton(text="⬅️ Admin Menyu", callback_data="adm_main_menu")]
    ])
    await callback_query.message.edit_text(text, reply_markup=inline_kb, parse_mode="HTML")
    await callback_query.answer()

@router.callback_query(F.data == "adm_sub_add_chan")
async def callback_adm_add_chan_prompt(callback_query: types.CallbackQuery, state: FSMContext):
    if callback_query.from_user.id != ADMIN_ID:
        return
    await state.set_state(AdminStates.waiting_add_channel)
    text = (
        "📢 <b>Yangi majburiy kanal qo'shish</b>\n\n"
        "Iltimos, kanalning user-id nomini yozib yuboring (masalan: <code>@autoxabarc_news</code>):"
    )
    inline_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Bekor qilish", callback_data="adm_mandatory_sub")]
    ])
    await callback_query.message.edit_text(text, reply_markup=inline_kb, parse_mode="HTML")
    await callback_query.answer()

@router.message(StateFilter(AdminStates.waiting_add_channel))
async def state_save_mandatory_channel(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    chan_name = message.text.strip()
    if not chan_name.startswith("@") or len(chan_name) < 4:
        await message.answer("❌ Noto'g'ri kanal nomi! Format: @autoxabarc_news shaklida bo'lishi shart.")
        return
        
    channels = db_users[ADMIN_ID].get("channels", [])
    if chan_name not in channels:
        channels.append(chan_name)
        db_users[ADMIN_ID]["channels"] = channels
        save_db()
        await message.answer(f"✅ <b>{chan_name}</b> majburiy obuna ro'yxatiga muvaqiyatli qo'shildi!", reply_markup=get_main_keyboard(ADMIN_ID), parse_mode="HTML")
    else:
        await message.answer("⚠️ Usbuhu kanal allaqachon ro'yxatda bor.")
    await state.clear()

@router.callback_query(F.data == "adm_sub_clear_chan")
async def callback_adm_clear_chans(callback_query: types.CallbackQuery):
    if callback_query.from_user.id != ADMIN_ID:
        return
    db_users[ADMIN_ID]["channels"] = []
    save_db()
    await callback_query.answer("📢 Barcha majburiy kanallar olib tashlandi!", show_alert=True)
    await callback_adm_sub_menu(callback_query)

@router.callback_query(F.data == "adm_broadcast_prompt")
async def callback_adm_broadcast_prompt(callback_query: types.CallbackQuery, state: FSMContext):
    if callback_query.from_user.id != ADMIN_ID:
        return
    await state.set_state(AdminStates.waiting_broadcast_msg)
    text = (
        "✉️ <b>Ommaviy reklama tarqatish bo'limi</b>\n\n"
        "Istalgan rasm yoki matnli xabarni yuboring. Ushabar botga start bosgan barcha foydalanuvchilarga avtomatik asinxron tarzda tarqatiladi!"
    )
    inline_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Bekor qilish", callback_data="adm_main_menu")]
    ])
    await callback_query.message.edit_text(text, reply_markup=inline_kb, parse_mode="HTML")
    await callback_query.answer()

@router.message(StateFilter(AdminStates.waiting_broadcast_msg))
async def state_process_broadcast(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await state.clear()
    progress_msg = await message.answer("🔄 <b>Ommaviy reklama tarqatish boshlandi...</b>", parse_mode="HTML")
    
    sent_count = 0
    fail_count = 0
    
    for u_id in list(db_users.keys()):
        try:
            await message.copy_to(chat_id=u_id)
            sent_count += 1
            await asyncio.sleep(0.05)
        except Exception:
            fail_count += 1
            
    await progress_msg.delete()
    await message.answer(
        f"✅ <b>Ommaviy reklama yakunlandi!</b>\n\n"
        f"📤 Yuborildi: <b>{sent_count} ta foydalanuvchiga</b>\n"
        f"❌ O'chib ketgan/Bloklagan: <b>{fail_count} ta</b>",
        reply_markup=get_main_keyboard(message.from_user.id),
        parse_mode="HTML"
    )

# ================= SENDER ENGINE (REAL VAQT INTERVALLI) =================

async def send_reklama_message(client, chat_id, user_data, user_id):
    if user_data.get("is_pro") and user_data.get("is_forward_mode") and user_data.get("forward_msg_id") and user_data.get("forward_chat_id"):
        try:
            await client.forward_messages(chat_id, user_data.get("forward_msg_id"), user_data.get("forward_chat_id"))
        except Exception as e:
            logging.error(f"[Forward] Postni uzatishda xatolik: {e}")
            await client.send_message(chat_id, user_data.get("reklama_matni", ""))
    else:
        text = user_data.get("reklama_matni", "")
        text += "\n\n@Auto_Xabar_Yuborish_Bot orqali yuborildi"
            
        photo_path = user_data.get("reklama_rasm") 
        buttons_data = user_data.get("inline_buttons", [])
        
        telethon_buttons = None
        if buttons_data:
            telethon_buttons = []
            for btn in buttons_data:
                try:
                    telethon_buttons.append(Button.url(btn["text"], btn["url"]))
                except Exception:
                    continue
                    
        if photo_path and os.path.exists(photo_path):
            try:
                await client.send_message(chat_id, text, file=photo_path, buttons=telethon_buttons)
            except Exception as e:
                logging.warning(f"Rasm bilan yuborishda muammo: {e}. Faqat matn ko'rinishida yuborilmoqda.")
                await client.send_message(chat_id, text, buttons=telethon_buttons)
        else:
            await client.send_message(chat_id, text, buttons=telethon_buttons)

async def auto_sender_worker():
    while True:
        current_time = datetime.now().timestamp()
        
        for user_id, user_data in list(db_users.items()):
            if user_data.get("is_sending") and user_data.get("active_phone"):
                # ================= AVTO-O'CHIRISH TAYMERINI TEKSHIRISH =================
                started_at = user_data.get("is_sending_started_at", 0)
                auto_off_hours = user_data.get("auto_off_hours")  # None yoki int
                
                if auto_off_hours and started_at:
                    elapsed_hours = (current_time - started_at) / 3600.0
                    if elapsed_hours >= auto_off_hours:
                        db_users[user_id]["is_sending"] = False
                        db_users[user_id]["is_sending_started_at"] = 0
                        save_db()
                        logging.info(f"[Timer] Foydalanuvchi {user_id} uchun avto-o'chirish taymeri ishladi ({auto_off_hours} soat).")
                        try:
                            await bot.send_message(
                                user_id, 
                                f"⏱️ <b>Avto-o'chirish taymeri ishladi!</b>\n\n"
                                f"Belgilangan <b>{auto_off_hours} soatlik</b> muddat tugagani sababli reklama tarqatish avtomatik ravishda to'xtatildi. 🛑", 
                                parse_mode="HTML"
                            )
                        except Exception:
                            pass
                        continue
                # =====================================================================

                next_run = user_data.get("next_run_timestamp", 0)
                if current_time >= next_run:
                    interval_minutes = user_data.get("interval", 15)
                    db_users[user_id]["next_run_timestamp"] = current_time + (interval_minutes * 60)
                    save_db()
                    
                    asyncio.create_task(run_sending_cycle_for_user(user_id))
                        
        await asyncio.sleep(10)

async def run_sending_cycle_for_user(user_id):
    user_data = db_users.get(user_id)
    if not user_data or not user_data.get("is_sending"):
        return
        
    try:
        active_phone = user_data.get("active_phone")
        if not active_phone:
            return
            
        client = await get_client(user_id, active_phone)
        if await client.is_user_authorized():
            guruhlar = []
            choice = user_data.get("groups_choice", "custom")
            
            if choice == "custom":
                guruhlar = [int(x) for x in user_data.get("selected_groups", [])]
            else:
                async for dialog in client.iter_dialogs():
                    if dialog.is_group:
                        guruhlar.append(int(dialog.id))
            
            if not guruhlar:
                logging.warning(f"[Sender] Foydalanuvchi {user_id} uchun guruh topilmadi.")
                return
                
            logging.info(f"[Sender] Foydalanuvchi {user_id} ({active_phone}) uchun {len(guruhlar)} ta guruhga tarqatish boshlandi...")
            
            for g_id in guruhlar:
                if not db_users.get(user_id, {}).get("is_sending"):
                    break
                    
                try:
                    await send_reklama_message(client, g_id, user_data, user_id)
                    user_data["today_sent"] = user_data.get("today_sent", 0) + 1
                    user_data["total_sent"] = user_data.get("total_sent", 0) + 1
                    save_db()
                    logging.info(f"[Sender] {user_id} -> Guruh {g_id} ga reklama yuborildi.")
                    await asyncio.sleep(15)
                except errors.FloodWaitError as e:
                    logging.warning(f"FloodWait cheklovi! {e.seconds} soniya kutiladi...")
                    await asyncio.sleep(e.seconds)
                except Exception as e:
                    continue
                    
    except Exception as e:
        logging.error(f"Sender asinxron xatolik user {user_id}: {str(e)}")


# ================= GROUP SELECTION & CALLBACKS =================

@router.callback_query(F.data == "disconnect_profile")
async def callback_disconnect(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    ensure_user(user_id)
    
    phone = db_users[user_id].get("active_phone")
    if not phone:
        await callback_query.answer("⚠️ Faol ulanish mavjud emas!", show_alert=True)
        return
        
    db_users[user_id]["active_phone"] = None
    db_users[user_id]["is_sending"] = False
    db_users[user_id]["is_sending_started_at"] = 0
    
    # Ro'yxatdan o'chirish
    accounts_list = db_users[user_id].get("accounts", [])
    cleaned_accounts = [acc for acc in accounts_list if acc["phone"] != phone]
    db_users[user_id]["accounts"] = cleaned_accounts
    
    if cleaned_accounts:
        db_users[user_id]["active_phone"] = cleaned_accounts[0]["phone"]
        db_users[user_id]["active_name"] = cleaned_accounts[0]["name"]
        db_users[user_id]["active_username"] = cleaned_accounts[0]["username"]
        
    save_db()
    
    phone_clean = phone.replace("+", "").replace(" ", "")
    session_key = f"{user_id}_{phone_clean}"
    if session_key in active_clients:
        try:
            await active_clients[session_key].disconnect()
        except Exception:
            pass
        active_clients.pop(session_key, None)
        
    session_file = os.path.join(SESSIONS_DIR, f"session_{session_key}.session")
    if os.path.exists(session_file):
        try:
            os.remove(session_file)
        except Exception:
            pass
            
    if db:
        try:
            doc_ref = db.collection('artifacts').document(APP_ID).collection('users').document(str(user_id)).collection('telethon_sessions').document(phone_clean)
            doc_ref.delete()
        except Exception:
            pass

    await callback_query.answer("⚠️ Faol profil muvaffaqiyatli uzildi!", show_alert=True)
    await menu_kabinet_msg(callback_query.message, user_id)

@router.callback_query(F.data == "set_groups_all")
async def callback_groups_all(callback_query: types.CallbackQuery):
    try:
        await callback_query.answer()
    except Exception:
        pass
    user_id = callback_query.from_user.id
    ensure_user(user_id)
    db_users[user_id]["groups_choice"] = "all"
    save_db()
    await callback_query.answer("✓ Hamma guruhlar tanlandi!", show_alert=True)
    await menu_guruhlar(callback_query.message, FSMContext(storage=MemoryStorage(), key=None))

@router.callback_query(F.data == "set_groups_custom")
async def callback_groups_custom(callback_query: types.CallbackQuery):
    try:
        await callback_query.answer()
    except Exception:
        pass
    user_id = callback_query.from_user.id
    ensure_user(user_id)
    db_users[user_id]["groups_choice"] = "custom"
    save_db()
    await callback_query.answer("✓ Qo'lda tanlash rejimi faollashdi!", show_alert=True)
    await menu_guruhlar(callback_query.message, FSMContext(storage=MemoryStorage(), key=None))

@router.callback_query(F.data == "clear_selected_groups")
async def callback_clear_groups(callback_query: types.CallbackQuery):
    try:
        await callback_query.answer()
    except Exception:
        pass
    user_id = callback_query.from_user.id
    ensure_user(user_id)
    db_users[user_id]["selected_groups"] = []
    save_db()
    await callback_query.answer("🚨 Barcha tanlangan guruhlar belgilanishi tozalandi!", show_alert=True)
    await callback_groups_list(callback_query)

@router.callback_query(F.data == "refresh_groups_force")
async def callback_refresh_groups_force(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    ensure_user(user_id)
    try:
        await callback_query.answer("Kesh yangilanmoqda, kuting...", show_alert=False)
    except Exception:
        pass

    try:
        active_phone = db_users[user_id].get("active_phone")
        if not active_phone:
            await callback_query.message.answer("⚠️ Avval profil bog'lashingiz kerak!")
            return
            
        client = await get_client(user_id, active_phone)
        if not await client.is_user_authorized():
            await callback_query.message.answer("⚠️ Seans muddati tugagan! Iltimos, profilni qayta qo'shing.")
            return

        guruhlar = []
        async for dialog in client.iter_dialogs():
            if dialog.is_group:
                participants = getattr(dialog.entity, 'participants_count', None) or 150
                guruhlar.append({
                    "id": int(dialog.id),
                    "name": str(dialog.name),
                    "participants_count": int(participants)
                })
        
        db_users[user_id]["cached_groups"] = guruhlar
        current_selected = [int(x) for x in db_users[user_id].get("selected_groups", [])]
        existing_selected = [x for x in current_selected if any(int(g["id"]) == x for g in guruhlar)]
        db_users[user_id]["selected_groups"] = existing_selected
        save_db()
        await callback_groups_list(callback_query)
    except Exception as e:
        logging.error(f"Guruhlarni yangilashda xato: {e}")
        await callback_query.message.answer(f"❌ Guruhlarni yuklashda xatolik yuz berdi: {str(e)}")

@router.callback_query(F.data.startswith("groups_list_page_"))
async def callback_groups_list(callback_query: types.CallbackQuery):
    try:
        await callback_query.answer()
    except Exception:
        pass

    user_id = callback_query.from_user.id
    ensure_user(user_id)
    user_data = db_users.get(user_id)
    parts = callback_query.data.split("_")
    page = 0
    if len(parts) >= 4:
        try:
            page = int(parts[3])
        except (ValueError, IndexError):
            page = 0
    
    guruhlar = user_data.get("cached_groups", [])
    if not guruhlar:
        active_phone = user_data.get("active_phone")
        if not active_phone:
            await callback_query.message.answer("⚠️ Avval profilingizni ulashingiz shart!")
            return
            
        try:
            client = await get_client(user_id, active_phone)
        except Exception:
            await callback_query.message.answer("⚠️ Avval profilingizni ulashingiz shart!")
            return
            
        try:
            async for dialog in client.iter_dialogs():
                if dialog.is_group:
                    participants = getattr(dialog.entity, 'participants_count', None) or 150
                    guruhlar.append({
                        "id": int(dialog.id),
                        "name": str(dialog.name),
                        "participants_count": int(participants)
                    })
            db_users[user_id]["cached_groups"] = guruhlar
            save_db()
        except Exception as e:
            await callback_query.message.answer(f"❌ Guruhlarni yuklashda xatolik: {str(e)}")
            return
            
    total_groups = len(guruhlar)
    per_page = 14
    start_idx = page * per_page
    end_idx = start_idx + per_page
    page_groups = guruhlar[start_idx:end_idx]
    selected_ids = [int(x) for x in user_data.get("selected_groups", [])]
    
    buttons = []
    row = []
    for g in page_groups:
        if isinstance(g, dict):
            g_id = int(g["id"])
            g_name = str(g["name"])
            g_part = int(g["participants_count"])
        else:
            g_id = int(g)
            g_name = f"Guruh {g}"
            g_part = 150

        is_selected = g_id in selected_ids
        icon = "✔" if is_selected else "➕"
        btn_text = f"{icon} {g_name[:12]} ({g_part})"
        row.append(InlineKeyboardButton(text=btn_text, callback_data=f"toggle_group_{g_id}_{page}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
        
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton(text="⬅ Oldingi", callback_data=f"groups_list_page_{page-1}"))
    if end_idx < total_groups:
        nav_buttons.append(InlineKeyboardButton(text="Keyingi ➡", callback_data=f"groups_list_page_{page+1}"))
    if nav_buttons:
        buttons.append(nav_buttons)
        
    buttons.append([
        InlineKeyboardButton(text="✔ Hammasini qo'shish", callback_data=f"select_all_groups_{page}"),
        InlineKeyboardButton(text="❌ Hammasini o'chirish", callback_data=f"deselect_all_groups_{page}")
    ])
    buttons.append([
        InlineKeyboardButton(text=f"💾 Saqlash ({len(selected_ids)} ta)", callback_data="save_groups_selection")
    ])
    
    text = (
        f"<b>Guruhlarni tanlang (Tanlangan: {len(selected_ids)} ta)</b>\n"
        f"Jami a'zo bo'lingan: <b>{total_groups} ta</b> guruh.\n"
        f"<b>{'-' * 30}</b>\n"
        "Guruhlar ro'yxati quyida ko'rsatilgan. Tanlang va pastdagi Saqlash tugmasini bosing:"
    )
    
    try:
        await callback_query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")
    except Exception:
        await callback_query.message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")

@router.callback_query(F.data.startswith("toggle_group_"))
async def callback_toggle_group(callback_query: types.CallbackQuery):
    try:
        await callback_query.answer()
    except Exception:
        pass

    user_id = callback_query.from_user.id
    ensure_user(user_id)
    parts = callback_query.data.split("_")
    if len(parts) < 4:
        return
        
    try:
        group_id = int(parts[2])
        page = int(parts[3])
    except (ValueError, IndexError):
        return
    
    user_data = db_users.get(user_id)
    selected_ids = [int(x) for x in user_data.get("selected_groups", [])]
    
    if group_id in selected_ids:
        selected_ids.remove(group_id)
    else:
        selected_ids.append(group_id)
        
    db_users[user_id]["selected_groups"] = selected_ids
    save_db()
    await callback_groups_list(callback_query)

@router.callback_query(F.data.startswith("select_all_groups_"))
async def callback_select_all_groups(callback_query: types.CallbackQuery):
    try:
        await callback_query.answer()
    except Exception:
        pass

    user_id = callback_query.from_user.id
    ensure_user(user_id)
    user_data = db_users.get(user_id)
    guruhlar = user_data.get("cached_groups", [])
    selected_ids = []
    
    for g in guruhlar:
        if isinstance(g, dict):
            selected_ids.append(int(g["id"]))
        else:
            selected_ids.append(int(g))
            
    db_users[user_id]["selected_groups"] = selected_ids
    save_db()
    await callback_query.answer("Barcha guruhlar qo'shildi ✔", show_alert=True)
    await callback_groups_list(callback_query)

@router.callback_query(F.data.startswith("deselect_all_groups_"))
async def callback_deselect_all_groups(callback_query: types.CallbackQuery):
    try:
        await callback_query.answer()
    except Exception:
        pass

    user_id = callback_query.from_user.id
    ensure_user(user_id)
    db_users[user_id]["selected_groups"] = []
    save_db()
    
    await callback_query.answer("Barcha guruhlar ro'yxatdan olib tashlandi! ❌", show_alert=True)
    await callback_groups_list(callback_query)

@router.callback_query(F.data == "save_groups_selection")
async def callback_save_groups(callback_query: types.CallbackQuery):
    try:
        await callback_query.answer()
    except Exception:
        pass
    user_id = callback_query.from_user.id
    ensure_user(user_id)
    user_data = db_users.get(user_id)
    user_data["selected_groups"] = [int(x) for x in user_data.get("selected_groups", [])]
    save_db()
    g_count = len(user_data["selected_groups"])
    await callback_query.answer(f"✓ Tanlangan {g_count} ta guruh muvaffaqiyatli saqlandi!", show_alert=True)
    await menu_guruhlar(callback_query.message, FSMContext(storage=MemoryStorage(), key=None))

# ================= SESSIONS RE-INITIALIZATION SERVICE =================

async def init_existing_sessions():
    if not os.path.exists(SESSIONS_DIR):
        return
    for file in os.listdir(SESSIONS_DIR):
        if file.endswith(".session") and "_" in file:
            user_id_str = file.replace("session_", "").replace(".session", "")
            parts = user_id_str.split("_")
            if len(parts) < 2:
                continue
            try:
                user_id = int(parts[0])
                phone_clean = parts[1]
                
                user_data = db_users.get(user_id)
                if not user_data:
                    continue
                accounts_list = user_data.get("accounts", [])
                target_phone = next((acc["phone"] for acc in accounts_list if acc["phone"].replace("+", "").replace(" ", "") == phone_clean), None)
                
                if not target_phone:
                    target_phone = "+" + phone_clean
                
                client = await get_client(user_id, target_phone)
                
                if await client.is_user_authorized():
                    session_key = f"{user_id}_{phone_clean}"
                    active_clients[session_key] = client
                    me = await client.get_me()
                    
                    ensure_user(user_id)
                    accounts_list = db_users[user_id].get("accounts", [])
                    if not any(acc["phone"] == target_phone for acc in accounts_list):
                        accounts_list.append({
                            "phone": target_phone,
                            "name": me.first_name,
                            "username": f"@{me.username}" if me.username else "@-"
                        })
                        db_users[user_id]["accounts"] = accounts_list
                    save_db()
                    logging.info(f"Mavjud seans muvaffaqiyatli qayta tiklandi: {target_phone} (ID: {user_id})")
            except Exception as e:
                logging.error(f"Sessiya yuklashda xatolik ({file}): {e}")

# ================= OTHER ACTIONS & LOGIN WIZARD =================

@router.callback_query(F.data == "toggle_sending")
async def callback_toggle_sending(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    ensure_user(user_id)
    user_data = db_users.get(user_id)
    
    if not user_data.get("active_phone"):
        await callback_query.answer("Avvalo profilingizni ulashingiz shart! 📱", show_alert=True)
        return
        
    user_data["is_sending"] = not user_data.get("is_sending")
    status_text = "ishga tushirildi! 🚀" if user_data["is_sending"] else "to'xtatildi! 🛑"
    
    if user_data["is_sending"]:
        user_data["next_run_timestamp"] = 0
        user_data["is_sending_started_at"] = datetime.now().timestamp()
    else:
        user_data["is_sending_started_at"] = 0
        
    save_db()
    await callback_query.answer(f"Avto-xabar tarqatish muvaffaqiyatli {status_text}", show_alert=True)
    await menu_autohabar(callback_query.message, FSMContext(storage=MemoryStorage(), key=None))
    
    if user_data["is_sending"]:
        logging.info(f"[Sender] Foydalanuvchi {user_id} uchun reklama tarqatish darhol ishga tushirildi!")
        asyncio.create_task(trigger_immediate_sending(user_id))

async def trigger_immediate_sending(user_id):
    ensure_user(user_id)
    user_data = db_users.get(user_id)
    if not user_data.get("is_sending"):
        return
    interval_minutes = user_data.get("interval", 15)
    db_users[user_id]["next_run_timestamp"] = datetime.now().timestamp() + (interval_minutes * 60)
    save_db()
    await run_sending_cycle_for_user(user_id)

@router.callback_query(F.data == "add_account")
async def callback_add_account_wizard(callback_query: types.CallbackQuery, state: FSMContext):
    user_id = callback_query.from_user.id
    ensure_user(user_id)
    
    # Sadriddin, free / PRO cheklovlarini qat'iy tekshiramiz!
    user_data = db_users.get(user_id)
    accounts_list = user_data.get("accounts", [])
    is_pro = user_data.get("is_pro", False)
    limit = 5 if is_pro else 1
    
    if len(accounts_list) >= limit:
        if not is_pro:
            await callback_query.message.answer(
                "⚠️ <b>Bepul tarif cheklovi!</b>\n\n"
                "Free tarifda faqat <b>1 ta</b> profil ulashingiz mumkin.\n"
                "Ko'p profil qo'shish (maksimal 5 tagacha) va barcha imkoniyatlar uchun <b>👑 Pro tarif</b> sotib oling yoki do'stlarni taklif qiling!",
                parse_mode="HTML"
            )
        else:
            await callback_query.message.answer(
                "⚠️ <b>Maksimal profil cheklovi!</b>\n\n"
                "PRO tarifda maksimal <b>5 ta</b> profil ulashga ruxsat beriladi.",
                parse_mode="HTML"
            )
        await callback_query.answer()
        return

    await callback_query.message.answer(
        "📱 <b>Real Telegram akkaunt ulash</b>\n\n"
        "Iltimos, telefon raqamingizni xalqaro formatda kiriting (masalan: <code>+998901234567</code>):",
        parse_mode="HTML"
    )
    await state.set_state(LoginStates.waiting_phone)
    await callback_query.answer()

@router.message(StateFilter(LoginStates.waiting_phone))
async def state_phone_received(message: types.Message, state: FSMContext):
    phone = message.text.strip()
    if not phone.startswith("+") or len(phone) < 9:
        await message.answer("❌ Noto'g'ri telefon raqam! Format: +998901234567")
        return
    
    await state.update_data(phone=phone)
    await message.answer("🔄 Telegram serverlariga mutloq toza ulanish o'rnatilmoqda. Iltimos kuting...")
    
    try:
        client = await get_client(message.from_user.id, phone)
        send_code_result = await client.send_code_request(phone)
        await state.update_data(phone_code_hash=send_code_result.phone_code_hash)
        await state.set_state(LoginStates.waiting_code)
        
        instructions = (
            "💬 <b>Sms ulanish kodi yuborildi!</b>\n\n"
            "⚠️ <b>MUHIM ESLATMA:</b>\n"
            "Kodni albatta raqamlar orasiga <b>nuqta qo'yib</b> kiriting!\n"
            "Format: <b>1.2.3.4.5</b>\n\n"
            "Iltimos, Telegram ilovangizga kelgan 5 xonali kodni yozing:"
        )
        await message.answer(instructions, parse_mode="HTML")
        
    except Exception as e:
        await message.answer(f"❌ Ulanishda xatolik yuz berdi: {str(e)}")
        await state.clear()

@router.message(StateFilter(LoginStates.waiting_code))
async def state_code_received(message: types.Message, state: FSMContext):
    code = message.text.strip().replace(".", "").replace(" ", "")
    data = await state.get_data()
    phone = data.get("phone")
    phone_code_hash = data.get("phone_code_hash")
    user_id = message.from_user.id
    ensure_user(user_id)
    
    try:
        client = await get_client(user_id, phone)
        await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
        
        me = await client.get_me()
        
        # Akkauntlar ro'yxatiga qo'shamiz
        accounts_list = db_users[user_id].get("accounts", [])
        if not any(acc["phone"] == phone for acc in accounts_list):
            accounts_list.append({
                "phone": phone,
                "name": me.first_name,
                "username": f"@{me.username}" if me.username else "@-"
            })
            db_users[user_id]["accounts"] = accounts_list
            
        db_users[user_id]["active_phone"] = phone
        db_users[user_id]["active_name"] = me.first_name
        db_users[user_id]["active_username"] = f"@{me.username}" if me.username else "@-"
        save_db()
        
        await backup_session_to_cloud(user_id, phone)
        
        await message.answer(
            "<b>Tabriklaymiz! Akkauntingiz muvaffaqiyatli bog'landi va bulutga xavfsiz zaxiralandi.</b>\n\n"
            "Endi autohabar bo'limiga o'tib, botni faollashtirishingiz mumkin!",
            reply_markup=get_main_keyboard(user_id),
            parse_mode="HTML"
        )
        await state.clear()
        
    except errors.PhoneCodeExpiredError:
        await message.answer(
            "❌ <b>Ulanish kodi muddati tugadi!</b>\n\n"
            "Sessiya zanjiri buzilgan. Iltimos, qaytadan telefon raqamingizni kiritib ulaning.",
            parse_mode="HTML"
        )
        await state.clear()
        
    except errors.PhoneCodeInvalidError:
        await message.answer(
            "❌ <b>Kiritilgan kod xato!</b>\n\n"
            "Iltimos, kodni tekshirib qayta kiriting.",
            parse_mode="HTML"
        )
        
    except errors.SessionPasswordNeededError:
        await state.set_state(LoginStates.waiting_2fa)
        await message.answer(
            "🛡️ <b>Akkauntingizda Ikki bosqichli himoya (2FA) aniqlandi!</b>\n\n"
            "Iltimos, o'z shaxsiy 2-bosqichli parolingizni kiriting:",
            parse_mode="HTML"
        )
    except Exception as e:
        await message.answer(f"❌ Xatolik yuz berdi: {str(e)}")

@router.message(StateFilter(LoginStates.waiting_2fa))
async def state_2fa_received(message: types.Message, state: FSMContext):
    password = message.text.strip()
    user_id = message.from_user.id
    ensure_user(user_id)
    data = await state.get_data()
    phone = data.get("phone")
    
    try:
        client = await get_client(user_id, phone)
        await client.sign_in(phone=phone, password=password)
        me = await client.get_me()
        
        # Akkauntlar ro'yxatiga qo'shish
        accounts_list = db_users[user_id].get("accounts", [])
        if not any(acc["phone"] == phone for acc in accounts_list):
            accounts_list.append({
                "phone": phone,
                "name": me.first_name,
                "username": f"@{me.username}" if me.username else "@-"
            })
            db_users[user_id]["accounts"] = accounts_list
            
        db_users[user_id]["active_phone"] = phone
        db_users[user_id]["active_name"] = me.first_name
        db_users[user_id]["active_username"] = f"@{me.username}" if me.username else "@-"
        save_db()
        
        await backup_session_to_cloud(user_id, phone)
        
        await message.answer(
            "<b>Tabriklaymiz! Akkauntingiz muvaffaqiyatli bog'landi va bulutga xavfsiz zaxiralandi.</b>\n\n"
            "Endi autohabar bo'limiga o'tib, botni faollashtirishingiz mumkin!",
            reply_markup=get_main_keyboard(user_id),
            parse_mode="HTML"
        )
        await state.clear()
    except errors.PasswordHashInvalidError:
        await message.answer("❌ <b>Ikki bosqichli parol noto'g'ri!</b>\n\nIltimos, parolingizni qayta kiriting.")
    except Exception as e:
        await message.answer(f"❌ Ulanishda xatolik yuz berdi: {str(e)}")


# ================= SOXTA WEB SERVER (PORT BINDING UCHUN) =================

async def handle_ping(request):
    return web.Response(text="Bot is running smoothly!")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', handle_ping)
    app.router.add_get('/ping', handle_ping)
    
    port = int(os.environ.get("PORT", 10000))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logging.info(f"Port {port}-portda muvaffaqiyatli ishga tushirildi!")

# ================= INTERVAL MENYUSI (TUZATILDI - HANDLER QO'SHILDI) =================
# Sadriddin, mana shu asinxron xizmat siz bosgan reply klaviaturadagi "⏱️ Interval" xabarini tutadi!

@router.message(F.text.in_([LOCALIZATION["uz"]["btn_interval"], LOCALIZATION["ru"]["btn_interval"], LOCALIZATION["en"]["btn_interval"]]))
async def menu_interval(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    ensure_user(user_id)
    user_data = db_users.get(user_id)
    current_interval = user_data.get('interval', 15)
    
    if current_interval >= 60:
        hours = current_interval / 60
        interval_text = f"{int(hours) if hours.is_integer() else hours} soat"
    else:
        interval_text = f"{current_interval} daqiqa"
        
    text = (
        "⏱️ <b>Xabar yuborish oralig'i (Interval)</b>\n\n"
        f"Joriy faol interval: <b>{interval_text}</b>\n\n"
        "Har bir reklama tarqatish sikli to'liq yakunlangach, bot belgilangan muddat davomida to'xtab (kutib) turadi."
    )
    
    # TUZATILDI: reply_markup kalit so'zi bilan to'g'ri bog'landi!
    await message.answer(text, reply_markup=get_interval_keyboard(current_interval), parse_mode="HTML")

# Interval o'zgartirilgandagi asinxron callback drayveri
@router.callback_query(F.data.startswith("set_int_"))
async def callback_set_interval(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    val = int(callback_query.data.split("_")[2])

    ensure_user(user_id)
    db_users[user_id]["interval"] = val
    save_db()

    if val >= 60:
        hours = val / 60
        interval_text = f"{int(hours) if hours.is_integer() else hours} soat"
    else:
        interval_text = f"{val} daqiqa"

    await callback_query.answer(f"✓ Interval {interval_text} ga sozlandi!", show_alert=True)
    
    text = (
        "⏱️ <b>Xabar yuborish oralig'i (Interval)</b>\n\n"
        f"Joriy faol interval: <b>{interval_text}</b>\n\n"
        "Har bir reklama tarqatish sikli to'liq yakunlangach, bot belgilangan muddat davomida to'xtab (kutib) turadi."
    )
    try:
        await callback_query.message.edit_text(text, reply_markup=get_interval_keyboard(val), parse_mode="HTML")
    except Exception:
        pass

@router.callback_query(F.data == "explain_interval")
async def callback_explain_interval(callback_query: types.CallbackQuery):
    explanation = (
        "⁉️ <b>Interval nima va u nega kerak?</b>\n\n"
        "<b>Interval</b> — bu siz ulatgan profilingiz barcha tanlangan guruhlarga reklama xabaringizni yuborib bo'lgandan so'ng, keyingi sikl boshlanguncha **qancha vaqt kutishini** belgilaydi.\n\n"
        "💡 <i>Tavsiya: Telegram spam-filtrlaridan (Spam-blok) saqlanish uchun intervalni kamida 10-15 daqiqa qilib belgilash tavsiya etiladi.</i>"
    )
    await callback_query.message.answer(explanation, parse_mode="HTML")
    await callback_query.answer()


# ================= MAIN MAIN MOTORS =================

async def main():
    global bot
    print("==================================================")
    print("🤖 AutoHabar Pro Telegram Bot ishga tushmoqda...")
    print("==================================================")
    print(f"[Tizim] Bot tokeni: {BOT_TOKEN[:15]}...")
    print(f"[Tizim] Admin ID: {ADMIN_ID}")
    
    if db:
        print("[Sessiya] Bulutdan eski ulanishlarni tiklash boshlandi...")
        await restore_sessions_from_cloud()
    
    bot = Bot(token=BOT_TOKEN)
    
    # Global majburiy obuna nazoratchisini dispatcherga ulash
    dp.message.outer_middleware(MandatorySubMiddleware())
    dp.callback_query.outer_middleware(MandatorySubMiddleware())
    
    asyncio.create_task(init_existing_sessions())
    asyncio.create_task(auto_sender_worker())
    logging.info("Auto-sender asinxron xizmati muvaffaqiyatli yoqildi!")
    
    asyncio.create_task(start_web_server())
    
    print("\n✅ BOT MUVAFFAQIYATLI ISHGA TUSHDI!")
    print("💬 Endi Telegram ilovangizni oching va botingizga kiring.")
    print("👉 Botingizga /start buyrug'ini yuboring.")
    print("\n==================================================")
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[👋] Avtomatlashtirish jarayoni foydalanuvchi tomonidan to'xtatildi.")
        sys.exit()
