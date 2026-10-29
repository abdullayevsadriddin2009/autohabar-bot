# -*- coding: utf-8 -*-
"""
AutoHabar Pro - Real Telegram Bot va Avtomatlashtirilgan Tarqatish Tizimi (v2 tuzatilgan).
O'zgartirishlar:
  - Barcha maxfiy kalitlar ENV o'zgaruvchilaridan o'qiladi (python-dotenv qo'llab-quvvatlanadi)
  - Firebase faqat FIREBASE_CREDENTIALS_JSON env orqali ulanadi (fayl kerak emas)
  - Firestore'da har bir foydalanuvchi alohida hujjatda saqlanadi (1MB chegara yo'q)
  - Bulutga yozish 15 soniyada bir marta (debounce) - har bir tugma bosilganda yozilmaydi
  - today_sent har kuni avtomatik nolga tushadi
  - Watermark faqat Free foydalanuvchilarda (Pro'da yo'q)
  - Har bir foydalanuvchi uchun alohida asyncio.Lock - sikl重叠 bo'lmaydi
  - FloodWaitError da akkaunt cooldown'ga olinadi
  - Rasm base64 ko'rinishida bazada saqlanadi (konteyner qayta ishga tushganda yo'qolmaydi)
  - SMS kodida "nuqta" hiylasi olib tashlandi (faqat oldingi kodlar uchun moslik qoldirildi)
  - groups_choice="all" da active_phone bo'lmasa avtomatik birinchi akkaunt olinadi
  - callback_clear_blacklist yordamchi funksiya orqali ishlaydi (soxta FSMContext olib tashlandi)
"""

import asyncio
import logging
import os
import sys
import shutil
import json
import base64
import random
import re
import csv
import time
from datetime import datetime

# Loggerlarni eng tepada sozlaymiz
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

# .env faylini yuklash (agar mavjud bo'lsa)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from aiogram import Bot, Dispatcher, types, Router, F, BaseMiddleware
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters import StateFilter, Command
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton,
    TelegramObject, BufferedInputFile, FSInputFile
)
from telethon import TelegramClient, errors, Button
from telethon.tl.functions.contacts import SearchRequest
from telethon.tl.functions.channels import JoinChannelRequest
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

# ================= CONFIGURATION (ENV orqali) =================

API_ID = int(os.getenv("API_ID", str(_FALLBACK_API_ID)))
API_HASH = os.getenv("API_HASH", _FALLBACK_API_HASH)
BOT_TOKEN = os.getenv("BOT_TOKEN", _FALLBACK_BOT_TOKEN)
ADMIN_ID = int(os.getenv("ADMIN_ID", str(_FALLBACK_ADMIN_ID)))
APP_ID = os.getenv("APP_ID", "autohabar-bot")

# Fallback ishlatilgan bo'lsa ogohlantirish
_used_fallback = []
if os.getenv("API_ID") is None: _used_fallback.append("API_ID")
if os.getenv("API_HASH") is None: _used_fallback.append("API_HASH")
if os.getenv("BOT_TOKEN") is None: _used_fallback.append("BOT_TOKEN")
if os.getenv("ADMIN_ID") is None: _used_fallback.append("ADMIN_ID")
if _used_fallback:
    print(f"[XAVFSIZLIK OGOHLANTIRISH] Quyidagi kalitlar eski (oshkor bo'lgan) qiymatlardan ishlatilmoqda: {', '.join(_used_fallback)}")
    print("[XAVFSIZLIK OGOHLANTIRISH] Iltimos, ularni darhol revoke qilib, yangisini ENV o'zgaruvchisi sifatida belgilang!")

# Bot, Dispatcher va Routerlarni e'lon qilish
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)

# Papkalarni yaratish
SESSIONS_DIR = "sessions"
DOWNLOADS_DIR = "downloads"
if os.path.exists(SESSIONS_DIR) and not os.path.isdir(SESSIONS_DIR):
    try:
        os.remove(SESSIONS_DIR)
    except Exception:
        pass
os.makedirs(SESSIONS_DIR, exist_ok=True)
os.makedirs(DOWNLOADS_DIR, exist_ok=True)
DB_FILE = os.path.join(SESSIONS_DIR, "database.json")

# Boshlang'ich baza andozasi
DEFAULT_DB = {
    ADMIN_ID: {
        "balans": 150000,
        "stars": 150,
        "is_pro": True,
        "referrals": 3,
        "reklama_matni": "🔥 AutoHabar Pro yordamida ishingizni yengillating!",
        "reklama_rasm": None,
        "reklama_rasm_b64": None,
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
        "today_sent_date": datetime.now().strftime("%Y-%m-%d"),
        "total_sent": 0,
        "channels": ["@autoxabarc_news", "@autoxabar_chat"],
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
        "lang": "uz",
        "blacklist": [],
        "delete_after_hours": None,
        "sent_messages_log": [],
        "rotation_index": 0
    }
}

# ================= GOOGLE FIRESTORE CLOUD DATABASE CONNECTION =================
# Faqat ENV o'zgaruvchisi (FIREBASE_CREDENTIALS_JSON) orqali ulanadi - fayl kerak emas.
db = None
if FIREBASE_AVAILABLE:
    firebase_config_env = os.environ.get("FIREBASE_CREDENTIALS_JSON")
    if firebase_config_env:
        try:
            cred_dict = json.loads(firebase_config_env)
            cred = credentials.Certificate(cred_dict)
            if not firebase_admin._apps:
                firebase_admin.initialize_app(cred)
            db = firestore.client()
            print("[Firebase] MUVAFFAQIYAT: FIREBASE_CREDENTIALS_JSON env orqali ulandi!")
        except Exception as e:
            print(f"[Firebase] XATO: Env orqali ulanishda xato: {e}")
    else:
        print("[Firebase] DIQQAT: FIREBASE_CREDENTIALS_JSON env o'zgaruvchisi topilmadi. Firebase o'chirildi, faqat mahalliy baza ishlatiladi.")
        print("[Firebase] Render'da Environment Variables ga FIREBASE_CREDENTIALS_JSON ni qo'shing (firebase_credentials.json ning to'liq matni).")
else:
    print("[Firebase] DIQQAT! firebase-admin kutubxonasi o'rnatilmagan.")


# ================= SESSIONS & DATABASE PERSISTENCE =================
# Bulutga yozishni 15 soniyada bir marta (debounce) qilamiz,
# har bir tugma bosilganda to'liq yozish emas.
_cloud_flush_needed = False

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
            # Har bir foydalanuvchi alohida hujjatda: artifacts/{APP_ID}/users/{user_id}
            users_ref = db.collection('artifacts').document(APP_ID).collection('users')
            cloud_users = {}
            for doc in users_ref.stream():
                try:
                    uid = int(doc.id)
                    udata = doc.to_dict()
                    if isinstance(udata, dict):
                        cloud_users[uid] = udata
                except Exception:
                    continue

            if cloud_users:
                # Bulutdagi ma'lumot ustunlik qiladi
                local_data.update(cloud_users)
                print(f"[Baza] MUVAFFAQIYAT: {len(cloud_users)} ta foydalanuvchi bulutdan yuklandi!")
                # Eski default kanallarni avtomatik tozalash
                if ADMIN_ID in local_data:
                    old_defaults = ["@autoxabarc_news", "@autoxabar_chat"]
                    current_chans = local_data[ADMIN_ID].get("channels", [])
                    cleaned_chans = [c for c in current_chans if c not in old_defaults]
                    local_data[ADMIN_ID]["channels"] = cleaned_chans
            else:
                print("[Baza] Bulut bo'sh. Mahalliy baza bulutga yuklanmoqda...")
                for uid, udata in local_data.items():
                    try:
                        users_ref.document(str(uid)).set(udata)
                    except Exception:
                        pass
                print("[Baza] MUVAFFAQIYAT: Mahalliy ma'lumotlar Firebase'ga ko'chirildi!")

            # Rasm base64 dan mahalliy faylni tiklash
            for uid, udata in local_data.items():
                b64 = udata.get("reklama_rasm_b64")
                local_path = udata.get("reklama_rasm")
                if b64 and (not local_path or not os.path.exists(local_path)):
                    try:
                        img_bytes = base64.b64decode(b64.encode('utf-8'))
                        new_path = os.path.join(DOWNLOADS_DIR, f"reklama_{uid}.jpg")
                        with open(new_path, "wb") as f:
                            f.write(img_bytes)
                        udata["reklama_rasm"] = new_path
                    except Exception as e:
                        logging.error(f"[Baza] Rasmni tiklashda xato (user {uid}): {e}")

            return local_data
        except Exception as e:
            print(f"[Baza] Firestore'dan yuklashda xatolik: {e}")
    else:
        print("[Baza] OGOHLANTIRISH: Firebase ulanmagan, faqat mahalliy baza ishlatilmoqda.")

    return local_data

def save_db():
    """Mahalliy faylga darhol yozadi, bulutga esa keyingi flush da yoziladi (debounce)."""
    global _cloud_flush_needed
    try:
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump(db_users, f, ensure_ascii=False, indent=4)
    except Exception as e:
        logging.error(f"[Baza] Mahalliy bazaga yozishda xato: {e}")
    _cloud_flush_needed = True

async def force_flush_db():
    """Muhim operatsiyalardan keyin (login, Pro sotib olish) darhol bulutga yozadi."""
    if not db:
        return
    try:
        users_ref = db.collection('artifacts').document(APP_ID).collection('users')
        for uid, udata in db_users.items():
            try:
                users_ref.document(str(uid)).set(udata)
            except Exception:
                pass
        logging.info("[Baza] Majburiy bulut sinxronizatsiyasi bajarildi!")
    except Exception as e:
        logging.error(f"[Baza] Majburiy flush da xato: {e}")

async def cloud_flush_worker():
    """Har 15 soniyada bir bulutga sinxronlaydi (debounce)."""
    global _cloud_flush_needed
    while True:
        await asyncio.sleep(15)
        if _cloud_flush_needed and db:
            _cloud_flush_needed = False
            try:
                users_ref = db.collection('artifacts').document(APP_ID).collection('users')
                for uid, udata in db_users.items():
                    try:
                        users_ref.document(str(uid)).set(udata)
                    except Exception:
                        pass
                logging.info("[Baza] Ma'lumotlar bulutga sinxronlandi.")
            except Exception as e:
                logging.error(f"[Baza] Bulutga yozishda xato: {e}")
                _cloud_flush_needed = True  # Keyingi tsiklda qayta urinish

# Mahalliy va bulutli ma'lumotlarni yuklash
db_users = load_db()
active_clients = {}
_user_send_locks = {}          # Foydalanuvchi bo'yicha jonli sikl bloklari
_account_cooldowns = {}        # FloodWait cooldown: phone -> timestamp

def get_user_send_lock(user_id: int) -> asyncio.Lock:
    if user_id not in _user_send_locks:
        _user_send_locks[user_id] = asyncio.Lock()
    return _user_send_locks[user_id]

def ensure_user(user_id: int):
    """Foydalanuvchi bazada yo'q bo'lsa yaratadi; faqat o'zgarish bo'lsa saqlaydi."""
    changed = False
    if user_id not in db_users:
        db_users[user_id] = {
            "balans": 0,
            "stars": 0,
            "is_pro": False,
            "referrals": 0,
            "reklama_matni": "🔥 AutoHabar Pro yordamida ishingizni yengillating!",
            "reklama_rasm": None,
            "reklama_rasm_b64": None,
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
            "today_sent_date": datetime.now().strftime("%Y-%m-%d"),
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
            "lang": None,
            "blacklist": [],
            "delete_after_hours": None,
            "sent_messages_log": [],
            "rotation_index": 0
        }
        changed = True
    else:
        u = db_users[user_id]
        defaults = {
            "referrals_count": 0, "referred_by": None, "forward_chat_id": None,
            "forward_msg_id": None, "is_forward_mode": False, "auto_off_hours": None,
            "is_sending_started_at": 0, "auto_sub_active": True, "auto_reply_active": False,
            "blacklist": [], "delete_after_hours": None, "sent_messages_log": [],
            "rotation_index": 0, "reklama_rasm_b64": None,
            "today_sent_date": datetime.now().strftime("%Y-%m-%d"),
        }
        for key, val in defaults.items():
            if key not in u:
                u[key] = val
                changed = True
        if "accounts" not in u:
            u["accounts"] = []
            if u.get("active_phone"):
                u["accounts"].append({
                    "phone": u["active_phone"],
                    "name": u.get("active_name", "Foydalanuvchi"),
                    "username": u.get("active_username", "@-")
                })
            changed = True
        if "lang" not in u:
            u["lang"] = None
            changed = True
    if changed:
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
                    print(f"[Sessiya] Bulutdan tiklandi: user_{user_id}_{phone_clean}.session")
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
    waiting_custom_interval = State()
    waiting_blacklist_gids = State()
    waiting_search_query = State()

class AdminStates(StatesGroup):
    waiting_search_id = State()
    waiting_add_balans = State()
    waiting_add_stars = State()
    waiting_add_channel = State()
    waiting_broadcast_msg = State()
    waiting_admin_reply = State()

# ================= KEYBOARD MARKUPS GENERATORS =================
def get_interval_keyboard(current_interval):
    def check(val, text):
        return f"✓ {text}" if current_interval == val else text
    kb = [
        [
            InlineKeyboardButton(text=check(2, "2daq"), callback_data="set_int_2"),
            InlineKeyboardButton(text=check(3, "3daq"), callback_data="set_int_3"),
            InlineKeyboardButton(text=check(4, "4daq"), callback_data="set_int_4"),
            InlineKeyboardButton(text=check(5, "5daq"), callback_data="set_int_5"),
            InlineKeyboardButton(text=check(6, "6daq"), callback_data="set_int_6")
        ],
        [
            InlineKeyboardButton(text=check(7, "7daq"), callback_data="set_int_7"),
            InlineKeyboardButton(text=check(8, "8daq"), callback_data="set_int_8"),
            InlineKeyboardButton(text=check(9, "9daq"), callback_data="set_int_9"),
            InlineKeyboardButton(text=check(10, "10daq"), callback_data="set_int_10"),
            InlineKeyboardButton(text=check(11, "11daq"), callback_data="set_int_11")
        ],
        [
            InlineKeyboardButton(text=check(12, "12daq"), callback_data="set_int_12"),
            InlineKeyboardButton(text=check(13, "13daq"), callback_data="set_int_13"),
            InlineKeyboardButton(text=check(14, "14daq"), callback_data="set_int_14"),
            InlineKeyboardButton(text=check(15, "15daq"), callback_data="set_int_15")
        ],
        [
            InlineKeyboardButton(text=check(30, "30daq"), callback_data="set_int_30"),
            InlineKeyboardButton(text=check(60, "1 soat"), callback_data="set_int_60"),
            InlineKeyboardButton(text=check(90, "1.5 soat"), callback_data="set_int_90"),
            InlineKeyboardButton(text=check(120, "2 soat"), callback_data="set_int_120"),
            InlineKeyboardButton(text=check(180, "3 soat"), callback_data="set_int_180")
        ],
        [InlineKeyboardButton(text="⁉️ Interval nima", callback_data="explain_interval")],
        [InlineKeyboardButton(text="✍️ Qo'lda kiritish", callback_data="custom_interval")],
        [InlineKeyboardButton(text="← Orqaga", callback_data="back_to_panel")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

def get_admin_main_markup():
    kb = [
        [
            InlineKeyboardButton(text="📊 Statistika", callback_data="adm_stats"),
            InlineKeyboardButton(text="Foydalanuvchini tahrirlash 👤", callback_data="adm_search_user")
        ],
        [
            InlineKeyboardButton(text="📢 Majburiy obuna kanallari", callback_data="adm_mandatory_sub"),
            InlineKeyboardButton(text="✉️ Ommaviy reklama yuborish", callback_data="adm_broadcast_prompt")
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

# ================= MULTI-LANGUAGE LOCALIZATION SYSTEM =================
# Eslatma: sms_sent dan "nuqta qo'yib kiriting" hiylasi olib tashlandi.
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
        "settings_title": "⚙️ <b>Qo'shimcha Tizim Sozlamalari</b>\n━━━━━━━━━━━━━━━━━━━━\n🤖 Avto-obuna: <b>{auto_sub}</b>\n↩️ Auto Reply: <b>{auto_reply}</b>\n🌐 Til: <b>{lang_name}</b>\n🛡️ Anti-Ban: <b>{antiban}</b>\n━━━━━━━━━━━━━━━━━━━━\nSozlamalarni o'zgartirish uchun kerakli tugmani bosing:",
        "guide_text": "📖 <b>AutoHabar Pro - Foydalanish Bo'yicha Batafsil Qo'llanma</b>\n━━━━━━━━━━━━━━━━━━━━\n1️⃣ <b>Akkaunt ulash:</b>\n• Profil bo'limidan akkaunt qo'shish tugmasini bosing va telefon raqamingizni xalqaro formatda kiriting.\n• SMS kod kelganda uni to'g'ridan-to'g'ri kiriting (masalan: <code>58291</code>).\n\n2️⃣ <b>Guruhlarni sozlash:</b>\n• Guruhlarni sozlash bo'limiga kirib, xabar yuboriladigan guruhlarni belgilang va saqlang.\n\n3️⃣ <b>Interval va Taymer:</b>\n• Guruhlar orasidagi kutish vaqtini (Interval) va bot avtomatik o'chadigan taymer muddatini belgilang.\n\n4️⃣ <b>Tugatish:</b>\n• Autohabar yuborish bo'limidan <b>▶️ Ishga tushirish</b> tugmasini bosing!",
        "cabinet_title": "👤 <b>Sizning Kabinetingiz</b>\n\n👥 Ism: <b>{name}</b>\n🌐 Username: <b>{username}</b>\n💰 Balans: <b>{balans} so'm</b>\n\n📊 <b>Statistika:</b>\n✔️ Bugun yuborildi: <b>{today_sent}</b>\n🔄 Jami yuborilgan: <b>{total_sent}</b>\n👥 Ulangan akkauntlar: <b>{acc_count} / 5 ta</b>\n👥 Taklif qilingan a'zolar: <b>{referrals} / 6 ta</b>\n🔗 Havola: <code>{ref_link}</code>",
        "btn_change_lang": "🌐 Tilni o'zgartirish",
        "btn_add_acc": "➕ Akkaunt qo'shish",
        "control_panel": "🤠 <b>Boshqaruv paneli</b>\n━━━━━━━━━━━━━━━━━━━━\n{profil}\n⚡ Holat: <b>{holat}</b>\n✍️ Xabar turi: <b>{turi}</b>\n💬 Guruhlar: <b>{guruhlar}</b>\n⏱️ Interval: <b>{interval}</b>\n⏳ Avto-o'chish: <b>{avto_ochish}</b>\n📢 Mention: <b>O'chiq</b>\n━━━━━━━━━━━━━━━━━━━━",
        "deposit_title": "💰 <b>Hisobni to'ldirish tizimi</b>\n━━━━━━━━━━━━━━━━━━━━\nSizning Telegram ID raqamingiz: <code>{user_id}</code>\nJoriy balansingiz: <b>{balans} so'm</b>\n\nHisobingizni xavfsiz to'ldirish orqali administratorga murojaat qiling:\n👉 <b>Administrator: @AbduIIayev_7</b>\n\n💡 <i>Eslatma: To'lovingiz tasdiqlanishi bilanoq balans hisobingizga soniyada avtomatik qo'shiladi!</i>\n━━━━━━━━━━━━━━━━━━━━",
        "profile_title": "👥 <b>Ulangan Akkauntlarni Boshqarish</b>\n━━━━━━━━━━━━━━━━━━━━\nJoriy faol ulanish: <b>{active}</b>\n\nFree tarifda faqat 1 ta profil qo'shish mumkin.\n👑 PRO tarifda 5 tagacha profil qo'shishingiz va boshqarishingiz mumkin!\n━━━━━━━━━━━━━━━━━━━━\nQuyidagi ro'yxatdan faollashtirish yoki o'chirish uchun profilni tanlang:",
        "msg_setup": "💬 <b>Habarni sozlash</b>\n\n📝 <b>Joriy matn:</b>\n<i>\"{matn}\"</i>\n\n🖼️ <b>Biriktirilgan rasm:</b> <b>{rasm}</b>\n🔘 <b>Inline tugmalar:</b> <b>{tugmalar}</b>\n📤 <b>Forward Rejim status:</b> <b>{status}</b>\n\n📌 <b>Xabar turini tanlang:</b>",
        "groups_setup": "🎯 <b>Guruhlarni sozlash</b>\n\nQaysi guruhlarga xabar yuboramiz?\nJoriy tanlov: <b>{tanlov}</b>\n\nGuruhlar ro'yxatini yuklash va sozlash uchun pastdagi tugmalardan foydalaning:",
        "pro_info": "👑 <b>AutoXabar Pro imkoniyatlari:</b>\n\n📤 <b>Forward xabarlarni tarqatish:</b>\n<i>Kanal postlarini barcha guruhlarga forward qibly uzatadi. Bu esa kanalingiz ko'rishlar sonini (views) jadal oshirishga yordam beradi!</i>\n\n👤 <b>Ko'p profil ulanishi:</b>\n• Botga 5 tagacha turli profil qo'shish imkoniyati\n\n🔘 <b>Tugmali inline xabarlar:</b>\n• Reklamalar tagiga havolali tugmalar biriktirish\n\n❌ <b>Watermarksiz toza interfeys:</b>\n• Xabar tagidagi reklama so'zlarini butunlay olib tashlash\n\n💰 <b>Narxi:</b>\n• <b>10,000 so'm</b> (Kabinetingizdagi pul hisobidan)\n• Yoki <b>6 ta yangi do'stlarni</b> taklif qilish (Mutlaqo bepul!)\n\n🔗 <b>Sizning shaxsiy taklif havolangiz:</b>\n<code>{ref_link}</code>",
        "already_pro": "👑 Sizda allaqachon PRO tarif faollashtirilgan!",
        "pro_activated": "🎉 Tabriklaymiz! PRO tarif muvaffaqiyatli faollashtirildi! 👑",
        "insufficient_funds": "❌ Hisobingizda mablag' yetarli emas!\nJoriy balans: {balans} so'm\nPRO narxi: 10,000 so'm.\n\nBotga 6 ta yangi odam taklif qibly, bepul PRO oling!",
        "no_active_conn": "⚠️ Faol ulanish vaqtinchalik mavjud emas!",
        "disconnected_success": "⚠️ Profilni uzish muvaffaqiyatli bajarildi!",
        "acc_limit_free": "⚠️ <b>Bepul tarif cheklovi!</b>\n\nFree tarifda faqat <b>1 ta</b> profil ulashingiz mumkin.\nKo'p profil qo'shish (maksimal 5 tagacha) va barcha imkoniyatlar uchun <b>👑 Pro tarif</b> sotib oling yoki do'stlarni taklif qiling!",
        "acc_limit_pro": "⚠️ <b>Maksimal profil cheklovi!</b>\n\nPRO tarifda maksimal <b>5 ta</b> profil ulashga ruxsat beriladi.",
        "enter_phone": "📱 <b>Real Telegram akkaunt ulash</b>\n\nIltimos, telefon raqamingizni xalqaro formatda kiriting (masalan: <code>+998901234567</code>):",
        "invalid_phone": "❌ <b>Format xato kiritildi!</b>\n\nFormat: <code>+[davlat_kodi][raqam]</code>\n<i>(Masalan: +998901234567, +79001234567, +12025550123)</i>",
        "connecting_tg": "🔄 Telegram serverlariga mutloq toza ulanish o'rnatilmoqda. Iltimos kuting...",
        "sms_sent": "💬 <b>Sms ulanish kodi yuborildi!</b>\n\nIltimos, Telegram ilovangizga kelgan 5 xonali kodni to'g'ridan-to'g'ri yozing:",
        "conn_error": "❌ Ulanishda xatolik yuz berdi: {error}",
        "acc_bound": "<b>Tabriklaymiz! Akkauntingiz muvaffaqiyatli bog'landi va bulutga xavfsiz zaxiralandi.</b>\n\nEndi autohabar bo'limiga o'tib, botni faollashtirishingiz mumkin!",
        "sms_expired": "❌ <b>Ulanish kodi muddati tugadi!</b>\n\nSessiya zanjiri buzilgan. Iltimos, qaytadan telefon raqamingizni kiritib ulaning.",
        "sms_invalid": "❌ <b>Kiritilgan kod xato!</b>\n\nIltimos, kodni tekshirib qayta kiriting.",
        "two_fa_required": "🛡️ <b>Akkauntingizda Ikki bosqichli himoya (2FA) aniqlandi!</b>\n\nIltimos, o'z shaxsiy 2-bosqichli parolingizni kiriting:",
        "two_fa_invalid": "❌ <b>Ikki bosqichli parol noto'g'ri!</b>\n\nIltimos, parolingizni qayta kiriting.",
        "custom_interval_prompt": "✍️ <b>Xabar yuborish oralig'ini (Intervalni) daqiqalarda kiriting (masalan: 20):</b>",
        "min_interval_error": "❌ Minimal interval vaqti - 1 daqiqa!",
        "interval_set_success": "✅ <b>Interval successfully set to {val} minutes!</b>",
        "invalid_integer": "❌ Iltimos, faqat butun son kiriting (masalan: 25):",
        "groups_all_selected": "✓ Hamma guruhlar tanlandi!",
        "groups_custom_selected": "✓ Qo'lda tanlash rejimi faollashdi!",
        "groups_cleared": "🚨 Tanlangan barcha guruhlar tozalandi!",
        "groups_refreshing": "Guruh keshini yangilash boshlandi...",
        "need_profile_first": "⚠️ Avval profil bog'lashingiz kerak!",
        "group_cache_empty": "⚠️ Kesh bo'sh, iltimos '+ Qo'shish' (Yangilash) tugmasini bosing!",
        "profile_not_found": "⚠️ Profil topilmadi!",
        "profile_activated": "✓ {phone} muvaffaqiyatli faollashtirildi!",
        "profile_deleted": "⚠️ Profil muvaffaqiyatli o'chirildi!",
        "sub_channels_alert": "⚠️ Diqqat! Barcha kanallarga a'zo bo'lishingiz shart!",
        "sub_confirmed": "🎉 Rahmat! Obuna to'liq tasdiqlandi. Bot faollashtirildi!",
        "panel_reloaded": "Boshqaruv paneli joriy holatda.",
        "panel_refreshed": "🔄 Boshqaruv paneli muvaffaqiyatli yangilandi!"
    },
    "ru": {
        "welcome": "📊 <b>Главное меню:</b>\n<b>@Auto_Xabar_Yuborish_Bot</b>\n━━━━━━━━━━━━━━━━━━━━\nЗдравствуйте, добро пожаловать! 👋\n\n› Чтобы использовать нашего бота\n› Добавьте аккаунт\n› Настройте группы\n› Настройте сообщение\n› Запустите авторассылку\n\n❓ Если вы не знаете, как использовать бота, нажмите кнопку <b>📖 Руководство</b> ниже!",
        "btn_auto_send": "⚪ Авторассылка",
        "btn_msg_text": "📝 Текст сообщения",
        "btn_interval": "⏱️ Interval",
        "btn_groups": "💬 Настройка групп",
        "btn_profiles": "👤 Профили",
        "btn_guide": "📖 Руководство",
        "btn_cabinet": "👤 Кабинет",
        "btn_settings": "⚙️ Настройки",
        "btn_support": "❓ Вопрос и Помощь",
        "select_lang_text": "🌐 Пожалуйста, выберите удобный для вас язык:",
        "support_prompt": "✍️ <b>Раздел отправки вопросов</b>\n\nПожалуйста, подробно напишите ваш вопрос или обращение. Администратор ответит вам через бота в ближайшее время!",
        "support_sent": "✅ Ваш вопрос успешно доставлен администратору! Мы ответим вам в ближайшее время.",
        "settings_title": "⚙️ <b>Дополнительные Системные Настройки</b>\n━━━━━━━━━━━━━━━━━━━━\n🤖 Автоподписка: <b>{auto_sub}</b>\n↩️ Автоответ: <b>{auto_reply}</b>\n🌐 Язык: <b>{lang_name}</b>\n🛡️ Анти-Бан: <b>{antiban}</b>\n━━━━━━━━━━━━━━━━━━━━\nНажмите кнопку для изменения настроек:",
        "guide_text": "📖 <b>AutoHabar Pro - Подробное Руководство</b>\n━━━━━━━━━━━━━━━━━━━━\n1️⃣ <b>Подключение аккаунта:</b>\n• В разделе профилей нажмите кнопку добавления аккаунта и введите номер телефона в международном формате.\n• При получении СМС-кода введите его напрямую (например: <code>58291</code>).\n\n2️⃣ <b>Настройка групп:</b>\n• Перейдите в раздел настройки групп, выберите группы для рассылки и сохраните.\n\n3️⃣ <b>Интервал и Таймер:</b>\n• Установите время ожидания между группами (Интервал) и время автоотключения таймера.\n\n4️⃣ <b>Запуск:</b>\n• В разделе авторассылки нажмите кнопку <b>▶️ Запустить</b>!",
        "cabinet_title": "👤 <b>Ваш Кабинет</b>\n\n👥 Имя: <b>{name}</b>\n🌐 Юзернейм: <b>{username}</b>\n💰 Баланс: <b>{balans} сум</b>\n\n📊 <b>Статистика:</b>\n✔️ Сегодня отправлено: <b>{today_sent}</b>\n🔄 Всего отправлено: <b>{total_sent}</b>\n👥 Подключено аккаунтов: <b>{acc_count} / 5 ta</b>\n👥 Приглашено друзей: <b>{referrals} / 6 ta</b>\n🔗 Ссылка: <code>{ref_link}</code>",
        "btn_change_lang": "🌐 Сменить язык",
        "btn_add_acc": "➕ Добавить аккаунт",
        "control_panel": "🤠 <b>Панель управления</b>\n━━━━━━━━━━━━━━━━━━━━\n{profil}\n⚡ Статус: <b>{holat}</b>\n✍️ Тип сообщения: <b>Текст</b>\n💬 Группы: <b>{guruhlar} групп</b>\n⏱️ Интервал: <b>{interval} минут</b>\n⏳ Автовыключение: <b>{avto_ochish}</b>\n📢 Упоминание: <b>Выкл</b>\n━━━━━━━━━━━━━━━━━━━━",
        "deposit_title": "💰 <b>Система пополнения баланса</b>\n━━━━━━━━━━━━━━━━━━━━\nВаш Telegram ID: <code>{user_id}</code>\nТекущий баланс: <b>{balans} сум</b>\n\nЧтобы безопасно пополнить счет, отправьте свой ID администратору:\n👉 <b>Администратор: @AbduIIayev_7</b>\n\n💡 <i>Примечание: Баланс будет автоматически пополнен сразу после подтверждения оплаты!</i>\n━━━━━━━━━━━━━━━━━━━━",
        "profile_title": "👥 <b>Управление подключенными аккаунтами</b>\n━━━━━━━━━━━━━━━━━━━━\nТекущее active подключение: <b>{active}</b>\n\nНа бесплатном тарифе можно подключить только 1 профиль.\n👑 На тарифе PRO вы можете подключить и управлять до 5 профилей!\n━━━━━━━━━━━━━━━━━━━━\nВыберите профиль из списка ниже, чтобы активировать или отключить его:",
        "msg_setup": "💬 <b>Настройка сообщения</b>\n\n📝 <b>Текущий текст:</b>\n<i>\"{matn}\"</i>\n\n🖼️ <b>Прикрепленное изображение:</b> <b>{rasm}</b>\n🔘 <b>Инлайн-кнопки:</b> <b>{tugmalar}</b>\n📤 <b>Статус режима Forward:</b> <b>{status}</b>\n\n📌 <b>Выберите тип сообщения:</b>",
        "groups_setup": "🎯 <b>Настройка групп</b>\n\nВ какие группы отправлять сообщения?\nТекущий выбор: <b>{tanlov}</b>\n\nИспользуйте кнопки ниже для загрузки и настройки групп:",
        "pro_info": "👑 <b>Возможности AutoXabar Pro:</b>\n\n📤 <b>Рассылка пересланных (Forward) сообщений:</b>\n<i>Пересылает посты каналов во все группы. Это помогает быстро увеличить количество просмотров (views) вашего главного канала и сохраняет ссылки!</i>\n\n👤 <b>Мультиаккаунт:</b>\n• Возможность добавить до 5 различных профилей в бот\n\n🔘 <b>Кнопки под сообщением:</b>\n• Прикрепление интерактивных кнопок-ссылок под рекламой\n\n❌ <b>Чистый интерфейс без водяных знаков:</b>\n• Полное удаление рекламной подписи бота в конце сообщения\n\n💰 <b>Размер оплаты:</b>\n• <b>10,000 сум</b> (вычитается из баланса вашего кабинета)\n• Либо бесплатно за приглашение <b>6 новых друзей</b>!\n\n🔗 <b>Ваша реферальная ссылка:</b>\n<code>{ref_link}</code>",
        "already_pro": "👑 У вас уже активирован тариф PRO!",
        "pro_activated": "🎉 Поздравляем! Тариф PRO успешно активирован! 👑",
        "insufficient_funds": "❌ Недостаточно средств на балансе!\nТекущий баланс: {balans} сум\nСтоимость PRO: 10,000 сум.\n\nПригласите 6 друзей и получите PRO бесплатно!",
        "no_active_conn": "⚠️ Нет активного подключения!",
        "disconnected_success": "⚠️ Профиль успешно отключен!",
        "acc_limit_free": "⚠️ <b>Ограничение бесплатного тарифа!</b>\n\nВы можете подключить только <b>1</b> профиль.\nДля подключения большего количества профилей (до 5) приобретите <b>👑 тариф Pro</b> или приглашайте друзей!",
        "acc_limit_pro": "⚠️ <b>Максимальный предел аккаунтов!</b>\n\nНа тарифе PRO разрешено подключать не более <b>5</b> профилей.",
        "enter_phone": "📱 <b>Подключение реального Telegram аккаунта</b>\n\nПожалуйста, введите ваш номер телефона в международном формате (например: <code>+998901234567</code>):",
        "invalid_phone": "❌ <b>Введен неверный формат номера телефона!</b>\n\nФормат: <code>+[код_страны][номер]</code>\n<i>(Пример: Узбекистан: <code>+998901234567</code>, Россия: <code>+79001234567</code>)</i>",
        "connecting_tg": "🔄 Устанавливается чистое подключение к серверам Telegram. Пожалуйста, подождите...",
        "sms_sent": "💬 <b>СМС-код отправлен!</b>\n\nПожалуйста, введите 5-значный код из вашего приложения Telegram напрямую:",
        "conn_error": "❌ Ошибка подключения: {error}",
        "acc_bound": "<b>Поздравляем! Ваш аккаунт успешно подключен и безопасно сохранен в облаке.</b>\n\nТеперь вы можете перейти в раздел авторассылки и запустить бота!",
        "sms_expired": "❌ <b>Срок действия кода истек!</b>\n\nЦепочка сессий нарушена. Пожалуйста, заново введите номер телефона.",
        "sms_invalid": "❌ <b>Введен неверный код!</b>\n\nПожалуйста, проверьте и введите код еще раз.",
        "two_fa_required": "🛡️ <b>На вашем аккаунте обнаружена двухэтапная аутентификация (2FA)!</b>\n\nПожалуйста, введите ваш личный пароль двухэтапной защиты:",
        "two_fa_invalid": "❌ <b>Двухэтапный пароль неверен!</b>\n\nПожалуйста, попробуйте ввести пароль еще раз.",
        "custom_interval_prompt": "✍️ <b>Введите задержку отправки (Интервал) в минутах (например: 20):</b>",
        "min_interval_error": "❌ Минимальный интервал - 1 минута!",
        "interval_set_success": "✅ <b>Интервал настроен на {val} минут!</b>",
        "invalid_integer": "❌ Пожалуйста, вводите только целые числа (например: 25):",
        "groups_all_selected": "✓ Выбраны все группы!",
        "groups_custom_selected": "✓ Режим ручного выбора активирован!",
        "groups_cleared": "🚨 Списки выбранных групп очищены!",
        "groups_refreshing": "Запущено обновление списка групп...",
        "need_profile_first": "⚠️ Сначала вам нужно подключить аккаунт!",
        "group_cache_empty": "⚠️ Кэш пуст, пожалуйста, нажмите кнопку '+ Добавить' (Обновить)!"
    },
    "en": {
        "welcome": "📊 <b>Main Menu:</b>\n<b>@Auto_Xabar_Yuborish_Bot</b>\n━━━━━━━━━━━━━━━━━━━━\nHello, welcome! 👋\n\n› To use our bot\n› Add an account\n› Configure groups\n› Configure message\n› Start auto-send\n\n❓ If you don't know how to use the bot, click the <b>📖 Guide</b> button below!",
        "btn_auto_send": "⚪ Auto-Send",
        "btn_msg_text": "📝 Message Text",
        "btn_interval": "⏱️ Interval",
        "btn_groups": "💬 Configure Groups",
        "btn_profiles": "👤 Profiles",
        "btn_guide": "📖 Guide",
        "btn_cabinet": "👤 Cabinet",
        "btn_settings": "⚙️ Settings",
        "btn_support": "❓ Support & Help",
        "select_lang_text": "🌐 Please select your preferred language:",
        "support_prompt": "✍️ <b>Support & Question Section</b>\n\nPlease write your question or appeal in detail. The administrator will reply to you through the bot shortly!",
        "support_sent": "✅ Your question has been successfully delivered to the admin! We will reply shortly.",
        "settings_title": "⚙️ <b>Additional System Settings</b>\n━━━━━━━━━━━━━━━━━━━━\n🤖 Auto-subscribe: <b>{auto_sub}</b>\n↩️ Auto Reply: <b>{auto_reply}</b>\n🌐 Language: <b>{lang_name}</b>\n🛡️ Anti-Ban: <b>{antiban}</b>\n━━━━━━━━━━━━━━━━━━━━\nClick a button to change settings:",
        "guide_text": "📖 <b>AutoHabar Pro - Detailed User Guide</b>\n━━━━━━━━━━━━━━━━━━━━\n1️⃣ <b>Connecting Account:</b>\n• Go to Profiles, click add account and enter your phone number in international format.\n• When you receive the SMS code, enter it directly (e.g. <code>58291</code>).\n\n2️⃣ <b>Configure Groups:</b>\n• Go to Configure Groups, select targeted groups and save.\n\n3️⃣ <b>Interval & Timer:</b>\n• Set the delay between groups (Interval) and auto-off timer duration.\n\n4️⃣ <b>Start:</b>\n• Click <b>▶️ Start</b> in the Auto-send section!",
        "cabinet_title": "👤 <b>Your Cabinet</b>\n\n👥 Name: <b>{name}</b>\n🌐 Username: <b>{username}</b>\n💰 Balance: <b>{balans} UZS</b>\n\n📊 <b>Statistics:</b>\n✔️ Today sent: <b>{today_sent}</b>\n🔄 Total sent: <b>{total_sent}</b>\n👥 Accounts connected: <b>{acc_count} / 5 ta</b>\n👥 Referrals invited: <b>{referrals} / 6 ta</b>\n🔗 Link: <code>{ref_link}</code>",
        "btn_change_lang": "🌐 Change Language",
        "btn_add_acc": "➕ Add Account",
        "control_panel": "🤠 <b>Control Panel</b>\n━━━━━━━━━━━━━━━━━━━━\n{profil}\n⚡ Status: <b>{holat}</b>\n✍️ Message Type: <b>Text</b>\n💬 Groups: <b>{guruhlar} groups</b>\n⏱️ Interval: <b>{interval} minutes</b>\n⏳ Auto-Off: <b>{avto_ochish}</b>\n📢 Mention: <b>Off</b>\n━━━━━━━━━━━━━━━━━━━━",
        "deposit_title": "💰 <b>Balance Recharge System</b>\n━━━━━━━━━━━━━━━━━━━━\nYour Telegram ID: <code>{user_id}</code>\nCurrent balance: <b>{balans} UZS</b>\n\nTo securely top up your account, send your shaxsiy ID to our administrator:\n👉 <b>Administrator: @AbduIIayev_7</b>\n\n💡 <i>Note: The balance will be automatically added to your account instantly after payment confirmation!</i>\n━━━━━━━━━━━━━━━━━━━━",
        "profile_title": "👥 <b>Manage Connected Accounts</b>\n━━━━━━━━━━━━━━━━━━━━\nCurrent active connection: <b>{active}</b>\n\nOn the free plan, you can only connect 1 profile.\n👑 On the PRO plan, you can connect and manage up to 5 profiles!\n━━━━━━━━━━━━━━━━━━━━\nSelect a profile from the list below to activate or disconnect it:",
        "msg_setup": "💬 <b>Configure Message</b>\n\n📝 <b>Current text:</b>\n<i>\"{matn}\"</i>\n\n🖼️ <b>Attached image:</b> <b>{rasm}</b>\n🔘 <b>Inline buttons:</b> <b>{tugmalar}</b>\n📤 <b>Forward Mode status:</b> <b>{status}</b>\n\n📌 <b>Select message type:</b>",
        "groups_setup": "🎯 <b>Setup Groups</b>\n\nWhich groups should messages be sent to?\nCurrent choice: <b>{tanlov}</b>\n\nUse the buttons below to load and configure groups:",
        "pro_info": "👑 <b>AutoXabar Pro Features:</b>\n\n📤 <b>Forward Messaging:</b>\n<i>Forward posts from your channel to groups. This helps rapidly increase your views!</i>\n\n👤 <b>Multi-Accounts:</b>\n• Up to 5 Telegram accounts connected simultaneously\n\n🔘 <b>Buttoned Inline Messages:</b>\n• Attach custom links & buttons below ads\n\n❌ <b>Ad-Free Interace:</b>\n• Remove default bot watermarks entirely"
    }
}

# Spintax (Sinonimlar) randomizatsiyasi funksiyasi
def apply_spintax(text: str) -> str:
    pattern = re.compile(r"\{([^{}]+)\}")
    while True:
        match = pattern.search(text)
        if not match:
            break
        options = match.group(1).split("|")
        text = text.replace(match.group(0), random.choice(options), 1)
    return text

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
        [KeyboardButton(text=get_text(user_id, "btn_support"))]
    ]
    if user_id == ADMIN_ID:
        kb.append([KeyboardButton(text="🛡️ Admin Panel")])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

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
        if user_id == ADMIN_ID:
            return await handler(event, data)
        if isinstance(event, types.CallbackQuery) and (event.data in ["check_sub_status", "back_to_deposit", "deposit_balance", "back_to_panel"] or event.data.startswith("lang_")):
            return await handler(event, data)
        if isinstance(event, types.Message) and event.text and (event.text.startswith("/start") or event.text.startswith("/admin")):
            return await handler(event, data)
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
                pass
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

@router.message(Command("start"), StateFilter("*"))
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
                try:
                    await bot.send_message(
                        referrer_id,
                        f"👤 Yangi do'stingiz sizning referal havolangiz orqali botga qo'shildi!\n"
                        f"Jami taklif qilgan faol a'zolaringiz: <b>{db_users[referrer_id]['referrals_count']} / 6 ta</b>",
                        parse_mode="HTML"
                    )
                except Exception:
                    pass
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

@router.callback_query(F.data.startswith("lang_"), StateFilter("*"))
async def callback_select_lang(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    ensure_user(user_id)
    selected_lang = callback_query.data.split("_")[1]
    db_users[user_id]["lang"] = selected_lang
    save_db()
    lang_name = "O'zbekcha 🇺🇿" if selected_lang == "uz" else "Русский 🇷🇺" if selected_lang == "ru" else "English 🇺🇸"
    await callback_query.answer(f"✓ {lang_name}", show_alert=True)
    try:
        await callback_query.message.delete()
    except Exception:
        pass
    await send_welcome_and_keyboard(callback_query.message, user_id)

@router.callback_query(F.data == "check_sub_status", StateFilter("*"))
async def callback_check_sub_status(callback_query: types.CallbackQuery, state: FSMContext):
    await state.clear()
    user_id = callback_query.from_user.id
    ensure_user(user_id)
    await callback_query.answer(get_text(user_id, "sub_confirmed"), show_alert=True)
    try:
        await callback_query.message.delete()
    except Exception:
        pass
    await send_welcome_and_keyboard(callback_query.message, user_id)

# ================= 📩 SAVOL VA YORDAM (SUPPORT SYSTEM) =================

@router.message(F.text.in_([LOCALIZATION["uz"]["btn_support"], LOCALIZATION["ru"]["btn_support"], LOCALIZATION["en"]["btn_support"]]), StateFilter("*"))
async def menu_support_handler(message: types.Message, state: FSMContext):
    await state.clear()
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
    admin_markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✍️ Javob berish", callback_data=f"reply_to_user_{user_id}")]
    ])
    try:
        await bot.send_message(chat_id=ADMIN_ID, text=admin_notification, reply_markup=admin_markup, parse_mode="HTML")
    except Exception as e:
        logging.error(f"[Support] Adminga xabar yuborishda xato: {e}")
    await message.answer(get_text(user_id, "support_sent"), reply_markup=get_main_keyboard(user_id), parse_mode="HTML")
    await state.clear()

@router.callback_query(F.data.startswith("reply_to_user_"), StateFilter("*"))
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
        await message.answer(f"❌ Javobni yuborishda xatolik (Foydalanuvchi botni bloklagan bo'lishi mumkin): {e}")
    await state.clear()

# ================= GUIDE / CABINET / SETTINGS =================

@router.message(F.text.in_([LOCALIZATION["uz"]["btn_guide"], LOCALIZATION["ru"]["btn_guide"], LOCALIZATION["en"]["btn_guide"]]), StateFilter("*"))
async def menu_guide_handler(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    ensure_user(user_id)
    await message.answer(get_text(user_id, "guide_text"), reply_markup=get_main_keyboard(user_id), parse_mode="HTML")

@router.message(F.text.in_([LOCALIZATION["uz"]["btn_cabinet"], LOCALIZATION["ru"]["btn_cabinet"], LOCALIZATION["en"]["btn_cabinet"]]), StateFilter("*"))
async def menu_kabinet(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    ensure_user(user_id)
    await menu_kabinet_msg(message, user_id)

async def menu_kabinet_msg(message: types.Message, user_id: int):
    user_data = db_users.get(user_id)
    lang = user_data.get("lang", "uz") or "uz"
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
        profiles_text = "❌ Profillar ulanmagan.\n" if lang == "uz" else ("❌ Профили не подключены.\n" if lang == "ru" else "❌ Profiles not connected.\n")
    cabinet_template = LOCALIZATION[lang]["cabinet_title"]
    text = cabinet_template.format(
        name=name, username=username,
        balans=f"{user_data.get('balans', 0):,}",
        today_sent=user_data.get('today_sent', 0),
        total_sent=user_data.get('total_sent', 0),
        acc_count=len(accounts_list),
        referrals=user_data.get('referrals_count', 0),
        ref_link=ref_link
    )
    header_acc = "👥 <b>Ulangan barcha profillaringiz:</b>\n" if lang == "uz" else ("👥 <b>Все подключенные профили:</b>\n" if lang == "ru" else "👥 <b>All connected profiles:</b>\n")
    text += f"\n\n{header_acc}\n{profiles_text}"
    btn_deposit = "💰 Hisobni to'ldirish" if lang == "uz" else ("💰 Пополнить баланс" if lang == "ru" else "💰 Deposit")
    btn_disconnect = "⚠️ Profilni uzish" if lang == "uz" else ("⚠️ Отключить профиль" if lang == "ru" else "⚠️ Disconnect profile")
    btn_close = "❌ Yopish" if lang == "uz" else ("❌ Закрыть" if lang == "ru" else "❌ Close")
    inline_kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=btn_deposit, callback_data="deposit_balance"),
            InlineKeyboardButton(text=btn_disconnect, callback_data="disconnect_profile")
        ],
        [InlineKeyboardButton(text=btn_close, callback_data="close_menu")]
    ])
    try:
        await message.answer(text, reply_markup=inline_kb, parse_mode="HTML")
    except Exception:
        await message.edit_text(text, reply_markup=inline_kb, parse_mode="HTML")

@router.message(F.text.in_([LOCALIZATION["uz"]["btn_settings"], LOCALIZATION["ru"]["btn_settings"], LOCALIZATION["en"]["btn_settings"]]), StateFilter("*"))
async def menu_sozlamalar(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    ensure_user(user_id)
    await show_sozlamalar_menu(message, user_id)

async def show_sozlamalar_menu(message: types.Message, user_id: int):
    user_data = db_users.get(user_id)
    lang = user_data.get("lang", "uz") or "uz"
    status_on = "Yoqilgan 🟢" if lang == "uz" else ("Включено 🟢" if lang == "ru" else "Enabled 🟢")
    status_off = "O'chirilgan 🔴" if lang == "uz" else ("Выключено 🔴" if lang == "ru" else "Disabled 🔴")
    anti_ban_text = "Eng yuqori darajada (Maksimal) 🛡️" if lang == "uz" else ("На высшем уровне (Максимальный) 🛡️" if lang == "ru" else "Maximum safety 🛡️")
    auto_sub = status_on if user_data.get("auto_sub_active", True) else status_off
    auto_reply = status_on if user_data.get("auto_reply_active", False) else status_off
    lang_name = "O'zbekcha 🇺🇿" if lang == "uz" else ("Русский 🇷🇺" if lang == "ru" else "English 🇺🇸")
    settings_template = (
        "⚙️ <b>Qo'shimcha Tizim Sozlamalari</b>\n━━━━━━━━━━━━━━━━━━━━\n🤖 Avto-obuna: <b>{auto_sub}</b>\n↩️ Auto Reply: <b>{auto_reply}</b>\n🌐 Til: <b>{lang_name}</b>\n🛡️ Anti-Ban: <b>{antiban}</b>\n━━━━━━━━━━━━━━━━━━━━\nSozlamalarni o'zgartirish uchun kerakli tugmani bosing:"
        if lang == "uz" else (
            "⚙️ <b>Дополнительные системные настройки</b>\n━━━━━━━━━━━━━━━━━━━━\n🤖 Автоподписка: <b>{auto_sub}</b>\n↩️ Автоответ: <b>{auto_reply}</b>\n🌐 Язык: <b>{lang_name}</b>\n🛡️ Анти-Бан: <b>{antiban}</b>\n━━━━━━━━━━━━━━━━━━━━\nНажмите кнопку для изменения настроек:"
            if lang == "ru" else
            "⚙️ <b>Additional System Settings</b>\n━━━━━━━━━━━━━━━━━━━━\n🤖 Auto-subscribe: <b>{auto_sub}</b>\n↩️ Auto Reply: <b>{auto_reply}</b>\n🌐 Language: <b>{lang_name}</b>\n🛡️ Anti-Ban: <b>{antiban}</b>\n━━━━━━━━━━━━━━━━━━━━\nClick a button to change settings:"
        )
    )
    text = settings_template.format(auto_sub=auto_sub, auto_reply=auto_reply, lang_name=lang_name, antiban=anti_ban_text)
    btn_sub = "🤖 Avto-obuna" if lang == "uz" else ("🤖 Автоподписка" if lang == "ru" else "🤖 Auto-sub")
    btn_reply = "↩️ Auto Reply" if lang == "uz" else ("↩️ Автоответ" if lang == "ru" else "↩️ Auto Reply")
    btn_lang = "🌐 Tilni o'zgartirish" if lang == "uz" else ("🌐 Сменить язык" if lang == "ru" else "🌐 Change Language")
    btn_blacklist = "🚫 Qora Ro'yxat" if lang == "uz" else ("🚫 Черный список" if lang == "ru" else "🚫 Blacklist")
    btn_del_msg = "🗑️ Avto-O'chirish" if lang == "uz" else ("🗑️ Автоудаление" if lang == "ru" else "🗑️ Auto-Delete")
    btn_close = "❌ Yopish" if lang == "uz" else ("❌ Закрыть" if lang == "ru" else "❌ Close")
    kb = [
        [
            InlineKeyboardButton(text=btn_sub, callback_data="toggle_auto_sub"),
            InlineKeyboardButton(text=btn_reply, callback_data="toggle_auto_reply")
        ],
        [
            InlineKeyboardButton(text=btn_blacklist, callback_data="manage_blacklist"),
            InlineKeyboardButton(text=btn_del_msg, callback_data="manage_del_msg_timer")
        ],
        [InlineKeyboardButton(text=btn_lang, callback_data="change_language_settings")],
        [InlineKeyboardButton(text=btn_close, callback_data="close_menu")]
    ]
    try:
        await message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="HTML")
    except Exception:
        await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="HTML")

@router.callback_query(F.data == "change_language_settings", StateFilter("*"))
async def callback_change_language_settings(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    ensure_user(user_id)
    await callback_query.message.edit_text(LOCALIZATION["uz"]["select_lang_text"], reply_markup=get_language_markup())
    await callback_query.answer()

@router.callback_query(F.data == "toggle_auto_sub", StateFilter("*"))
async def callback_toggle_auto_sub(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    ensure_user(user_id)
    db_users[user_id]["auto_sub_active"] = not db_users[user_id].get("auto_sub_active", True)
    save_db()
    status = "yoqildi 🟢" if db_users[user_id]["auto_sub_active"] else "o'chirildi 🔴"
    await callback_query.answer(f"✓ Avto-obuna {status}!", show_alert=True)
    await show_sozlamalar_menu(callback_query.message, user_id)

@router.callback_query(F.data == "toggle_auto_reply", StateFilter("*"))
async def callback_toggle_auto_reply(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    ensure_user(user_id)
    db_users[user_id]["auto_reply_active"] = not db_users[user_id].get("auto_reply_active", False)
    save_db()
    status = "yoqildi 🟢" if db_users[user_id]["auto_reply_active"] else "o'chirildi 🔴"
    await callback_query.answer(f"✓ Auto Reply {status}!", show_alert=True)
    await show_sozlamalar_menu(callback_query.message, user_id)

@router.callback_query(F.data == "close_menu", StateFilter("*"))
async def callback_close_menu(callback_query: types.CallbackQuery):
    try:
        await callback_query.message.delete()
    except Exception:
        pass
    await callback_query.answer()

# ================= BLACKLIST & AUTO-DELETE SETTINGS =================
# Yordamchi funksiya (soxta FSMContext olib tashlandi)
async def render_blacklist_menu(message: types.Message, user_id: int):
    lang = db_users[user_id].get("lang", "uz") or "uz"
    blacklist = db_users[user_id].get("blacklist", [])
    if lang == "uz":
        text = (
            "🚫 <b>Guruhlar Qora Ro'yxati (Blacklist)</b>\n━━━━━━━━━━━━━━━━━━━━\n"
            "Quyidagi guruhlarga xabarlar mutlaqo yuborilmaydi va sikllarda tashlab ketiladi.\n\n"
            f"Joriy guruh ID lari: <code>{', '.join(map(str, blacklist)) if blacklist else 'Bo`sh'}</code>\n\n"
            "Yangi ID kiritish uchun quyidagi tugmani bosing:"
        )
    elif lang == "ru":
        text = (
            "🚫 <b>Черный список групп (Blacklist)</b>\n━━━━━━━━━━━━━━━━━━━━\n"
            "В эти группы авторассылка не будет отправлять сообщения.\n\n"
            f"Текущие ID групп: <code>{', '.join(map(str, blacklist)) if blacklist else 'Пусто'}</code>"
        )
    else:
        text = (
            "🚫 <b>Group Blacklist</b>\n━━━━━━━━━━━━━━━━━━━━\n"
            "Messages will never be sent to these groups.\n\n"
            f"Current group IDs: <code>{', '.join(map(str, blacklist)) if blacklist else 'Empty'}</code>"
        )
    kb = [
        [InlineKeyboardButton(text="➕ ID qo'shish / o'chirish", callback_data="add_blacklist_prompt")],
        [InlineKeyboardButton(text="🧹 Tozalash", callback_data="clear_blacklist")],
        [InlineKeyboardButton(text="← Orqaga", callback_data="back_to_settings")]
    ]
    try:
        await message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="HTML")
    except Exception:
        await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="HTML")

@router.callback_query(F.data == "manage_blacklist", StateFilter("*"))
async def callback_manage_blacklist(callback_query: types.CallbackQuery, state: FSMContext):
    await state.clear()
    user_id = callback_query.from_user.id
    ensure_user(user_id)
    await render_blacklist_menu(callback_query.message, user_id)
    await callback_query.answer()

@router.callback_query(F.data == "add_blacklist_prompt", StateFilter("*"))
async def callback_add_blacklist_prompt(callback_query: types.CallbackQuery, state: FSMContext):
    user_id = callback_query.from_user.id
    await state.set_state(TextStates.waiting_blacklist_gids)
    await callback_query.message.answer("✍️ <b>Qora ro'yxatga qo'shish yoki undan olib tashlash uchun guruh ID raqamini yuboring (masalan: -100123456789):</b>", parse_mode="HTML")
    await callback_query.answer()

@router.message(StateFilter(TextStates.waiting_blacklist_gids))
async def state_save_blacklist_gid(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    ensure_user(user_id)
    try:
        gid = int(message.text.strip())
        blacklist = db_users[user_id].get("blacklist", [])
        if gid in blacklist:
            blacklist.remove(gid)
            await message.answer(f"✅ Guruh ID (<code>{gid}</code>) qora ro'yxatdan olib tashlandi!", parse_mode="HTML")
        else:
            blacklist.append(gid)
            await message.answer(f"✅ Guruh ID (<code>{gid}</code>) qora ro'yxatga muvaffaqiyatli qo'shildi!", parse_mode="HTML")
        db_users[user_id]["blacklist"] = blacklist
        save_db()
    except ValueError:
        await message.answer("❌ Noto'g'ri ID. Iltimos, faqat butun son yuboring.")
    await state.clear()

@router.callback_query(F.data == "clear_blacklist", StateFilter("*"))
async def callback_clear_blacklist(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    ensure_user(user_id)
    db_users[user_id]["blacklist"] = []
    save_db()
    await callback_query.answer("🧹 Qora ro'yxat tozalandi!", show_alert=True)
    await render_blacklist_menu(callback_query.message, user_id)

@router.callback_query(F.data == "back_to_settings", StateFilter("*"))
async def callback_back_to_settings(callback_query: types.CallbackQuery):
    await show_sozlamalar_menu(callback_query.message, callback_query.from_user.id)
    await callback_query.answer()

@router.callback_query(F.data == "manage_del_msg_timer", StateFilter("*"))
async def callback_manage_del_msg_timer(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    ensure_user(user_id)
    lang = db_users[user_id].get("lang", "uz") or "uz"
    timer = db_users[user_id].get("delete_after_hours")
    current_val_text = f"{timer} soat" if timer else "O'chirilgan 🔴"
    text = (
        "🗑️ <b>Xabarlarni Avtomatik O'chirish Taymeri</b>\n━━━━━━━━━━━━━━━━━━━━\n"
        "Guruhlar adminlari reklamalarni ko'rib bloklab yubormasligi va guruhlarda xabarlaringiz tartibli bo'lib turishi uchun "
        "yuborilgan reklama xabarlarini belgilangan vaqtdan so'ng o'chirib tashlaydi.\n\n"
        f"Joriy holat: <b>{current_val_text}</b>"
    )
    def check_mark(val):
        return "✓ " if timer == val else ""
    kb = [
        [
            InlineKeyboardButton(text=f"{check_mark(1)}1 soat", callback_data="set_del_timer_1"),
            InlineKeyboardButton(text=f"{check_mark(2)}2 soat", callback_data="set_del_timer_2"),
            InlineKeyboardButton(text=f"{check_mark(6)}6 soat", callback_data="set_del_timer_6")
        ],
        [
            InlineKeyboardButton(text=f"{check_mark(12)}12 soat", callback_data="set_del_timer_12"),
            InlineKeyboardButton(text=f"{check_mark(24)}24 soat", callback_data="set_del_timer_24"),
            InlineKeyboardButton(text=f"{check_mark(None)}O'chirish", callback_data="set_del_timer_none")
        ],
        [InlineKeyboardButton(text="← Orqaga", callback_data="back_to_settings")]
    ]
    await callback_query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="HTML")
    await callback_query.answer()

@router.callback_query(F.data.startswith("set_del_timer_"), StateFilter("*"))
async def callback_set_del_timer(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    ensure_user(user_id)
    val_part = callback_query.data.split("_")[3]
    if val_part == "none":
        db_users[user_id]["delete_after_hours"] = None
        alert = "✓ Avto-o'chirish taymeri o'chirildi!"
    else:
        hours = int(val_part)
        db_users[user_id]["delete_after_hours"] = hours
        alert = f"✓ Xabarlarni yuborilgach {hours} soatdan keyin o'chirish faollashdi!"
    save_db()
    await callback_query.answer(alert, show_alert=True)
    await callback_manage_del_msg_timer(callback_query)

# ================= ADMIN PANEL HANDLERS =================

@router.message(F.text == "🛡️ Admin Panel", StateFilter("*"))
async def cmd_admin(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await state.clear()
    text = "🛡️ <b>AutoHabar Pro - Tizim Admin Paneli</b>\n\nBoshqaruv bo'limini tanlang:"
    await message.answer(text, reply_markup=get_admin_main_markup(), parse_mode="HTML")

@router.callback_query(F.data == "adm_main_menu", StateFilter("*"))
async def callback_adm_main(callback_query: types.CallbackQuery, state: FSMContext = None):
    if callback_query.from_user.id != ADMIN_ID:
        await callback_query.answer("Ruxsat berilmagan!", show_alert=True)
        return
    if state:
        await state.clear()
    text = "🛡️ <b>AutoHabar Pro - Tizim Admin Paneli</b>\n\nBoshqaruv bo'limini tanlang:"
    try:
        await callback_query.message.edit_text(text, reply_markup=get_admin_main_markup(), parse_mode="HTML")
    except Exception:
        await callback_query.message.answer(text, reply_markup=get_admin_main_markup(), parse_mode="HTML")
    await callback_query.answer()

@router.callback_query(F.data == "adm_stats", StateFilter("*"))
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

@router.callback_query(F.data == "adm_search_user", StateFilter("*"))
async def callback_adm_search_prompt(callback_query: types.CallbackQuery, state: FSMContext):
    if callback_query.from_user.id != ADMIN_ID:
        return
    await state.set_state(AdminStates.waiting_search_id)
    text = "👤 <b>Foydalanuvchini sozlash bo'limi</b>\n\nIltimos, boshqarmoqchi bo'lgan foydalanuvchining <b>Telegram ID</b> raqamini kiriting:"
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
                f"🛡️ Joriy tarif: <b>{tarif_nomi}</b>\n"
                f"💰 Pul Balans: <b>{user_data.get('balans', 0):,} so'm</b>\n"
                f"⭐ Stars Balans: <b>{user_data.get('stars', 0)} ⭐️</b>\n"
                f"📤 Jami yuborgan xabarlari: <b>{user_data.get('total_sent', 0)} ta</b>"
            )
            inline_kb = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="💰 Balans tahrirlash", callback_data=f"adm_chg_bal_{target_id}"),
                    InlineKeyboardButton(text="⭐️ Stars tahrirlash", callback_data=f"adm_chg_stars_{target_id}"),
                ],
                [InlineKeyboardButton(text="👑 PRO / FREE o'tkazish", callback_data=f"adm_chg_tarif_{target_id}")],
                [InlineKeyboardButton(text="⬅️ Admin Menyu", callback_data="adm_main_menu")]
            ])
            await message.answer(text, reply_markup=inline_kb, parse_mode="HTML")
            await state.clear()
        else:
            await message.answer("❌ Bunday IDga ega foydalanuvchi topilmadi! Qaytadan kiriting yoki ⬅️ Orqaga tugmasini bosing:")
    except ValueError:
        await message.answer("❌ ID raqam faqat butun sonlardan iborat bo'lishi kerak! Qaytadan kiriting:")

@router.callback_query(F.data.startswith("adm_chg_bal_"), StateFilter("*"))
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
            new_bal = max(0, current_bal + change_amount)
            db_users[target_id]["balans"] = new_bal
            save_db()
            await message.answer(
                f"✅ <b>Balans muvaffaqiyatli o'zgartirildi!</b>\n"
                f"Eski balans: {current_bal:,} so'm\n"
                f"Yangi balans: <b>{new_bal:,} so'm</b>",
                reply_markup=get_main_keyboard(target_id), parse_mode="HTML"
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

@router.callback_query(F.data.startswith("adm_chg_stars_"), StateFilter("*"))
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
            new_stars = max(0, current_stars + change_amount)
            db_users[target_id]["stars"] = new_stars
            save_db()
            await message.answer(
                f"✅ <b>Stars balans o'zgartirildi!</b>\nEski: {current_stars} ⭐️\nYangi: <b>{new_stars} ⭐️</b>",
                reply_markup=get_main_keyboard(target_id), parse_mode="HTML"
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

@router.callback_query(F.data.startswith("adm_chg_tarif_"), StateFilter("*"))
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
            tabrik = "👑 <b>Tabriklaymiz! Tizim administratori sizga cheksiz PRO tarifini taqdim etdi!</b>\nEndi barcha yopiq xizmatlar siz uchun ochiq." if new_status else "⚠️ Hisobingizdagi PRO tarifi administrator tomonidan bekor qilindi va bepul rejimga qaytarildingiz."
            await bot.send_message(target_id, tabrik, parse_mode="HTML")
        except Exception:
            pass
        text = "🛡️ <b>AutoHabar Pro - Tizim Admin Paneli</b>\n\nBoshqaruv bo'limini tanlang:"
        try:
            await callback_query.message.edit_text(text, reply_markup=get_admin_main_markup(), parse_mode="HTML")
        except Exception:
            pass
    else:
        await callback_query.answer("Foydalanuvchi topilmadi!", show_alert=True)

@router.callback_query(F.data == "adm_mandatory_sub", StateFilter("*"))
async def callback_adm_sub_menu(callback_query: types.CallbackQuery):
    if callback_query.from_user.id != ADMIN_ID:
        return
    channels = db_users[ADMIN_ID].get("channels", [])
    text = "📢 <b>Majburiy obuna kanallarini sozlash</b>\n\nFoydalanuvchi botni start qilganda quyidagi majburiy kanallarga a'zo bo'lishi shart qibly ko'rsatiladi:\n\n"
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

@router.callback_query(F.data == "adm_sub_add_chan", StateFilter("*"))
async def callback_adm_add_chan_prompt(callback_query: types.CallbackQuery, state: FSMContext):
    if callback_query.from_user.id != ADMIN_ID:
        return
    await state.set_state(AdminStates.waiting_add_channel)
    text = "📢 <b>Yangi majburiy kanal qo'shish</b>\n\nIltimos, kanalning user-id nomini yozib yuboring (masalan: <code>@autoxabarc_news</code>):"
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
        await message.answer(f"✅ <b>{chan_name}</b> majburiy obuna ro'yxatiga muvaffaqiyatli qo'shildi!", reply_markup=get_main_keyboard(ADMIN_ID), parse_mode="HTML")
    else:
        await message.answer("⚠️ Ushbu kanal allaqachon ro'yxatda bor.")
    await state.clear()

@router.callback_query(F.data == "adm_sub_clear_chan", StateFilter("*"))
async def callback_adm_clear_chans(callback_query: types.CallbackQuery):
    if callback_query.from_user.id != ADMIN_ID:
        return
    db_users[ADMIN_ID]["channels"] = []
    save_db()
    await callback_query.answer("📢 Barcha majburiy kanallar olib tashlandi!", show_alert=True)
    await callback_adm_sub_menu(callback_query)

@router.callback_query(F.data == "adm_broadcast_prompt", StateFilter("*"))
async def callback_adm_broadcast_prompt(callback_query: types.CallbackQuery, state: FSMContext):
    if callback_query.from_user.id != ADMIN_ID:
        return
    await state.set_state(AdminStates.waiting_broadcast_msg)
    text = "✉️ <b>Ommaviy reklama tarqatish bo'limi</b>\n\nIstalgan rasm yoki matnli xabarni yuboring. Ushbu xabar botga start bosgan barcha foydalanuvchilarga avtomatik asinxron tarzda tarqatiladi!"
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
    try:
        await progress_msg.delete()
    except Exception:
        pass
    await message.answer(
        f"✅ <b>Ommaviy reklama yakunlandi!</b>\n\n"
        f"📤 Yuborildi: <b>{sent_count} ta foydalanuvchiga</b>\n"
        f"❌ O'chib ketgan/Bloklagan: <b>{fail_count} ta</b>",
        reply_markup=get_main_keyboard(message.from_user.id), parse_mode="HTML"
    )


# ================= ⚪ AUTOHABAR YUBORISH MENYUSI =================

async def generate_control_panel_text(user_id: int):
    user_data = db_users.get(user_id)
    lang = user_data.get("lang", "uz") or "uz"
    phone = user_data.get("active_phone")
    p_status = f"👤 Profil: [ {phone} ]" if phone else f"👤 Profil: [ {get_text(user_id, 'no_active_conn')} ]"
    s_status_on = "🟢 Faol (Yuborilmoqda...)" if lang == "uz" else ("🟢 Активно (Идет рассылка...)" if lang == "ru" else "🟢 Active (Sending...)")
    s_status_off = "🔴 O'chiq" if lang == "uz" else ("🔴 Выключено" if lang == "ru" else "🔴 Disabled")
    holatStatus = s_status_on if user_data.get("is_sending") else s_status_off
    auto_off = user_data.get("auto_off_hours")
    auto_off_text = ("∞ Cheksiz" if lang == "uz" else ("∞ Без ограничений" if lang == "ru" else "∞ Unlimited")) if auto_off is None else f"{auto_off} " + ("soat" if lang == "uz" else ("час" if lang == "ru" else "hours"))
    guruhlar_count = f"{len(user_data.get('selected_groups', []))} ta" if lang == "uz" else (f"{len(user_data.get('selected_groups', []))} групп" if lang == "ru" else f"{len(user_data.get('selected_groups', []))} groups")
    interval_text = f"{user_data.get('interval', 15)} " + ("daqiqa" if lang == "uz" else ("минут" if lang == "ru" else "minutes"))
    msg_type = "Matn" if lang == "uz" else ("Текст" if lang == "ru" else "Text")
    cabinet_template = LOCALIZATION[lang]["control_panel"]
    responseText = cabinet_template.format(
        profil=p_status, holat=holatStatus, turi=msg_type,
        guruhlar=guruhlar_count, interval=interval_text, avto_ochish=auto_off_text
    )
    return responseText, lang

async def get_control_panel_markup(user_id: int):
    user_data = db_users.get(user_id)
    lang = user_data.get("lang", "uz") or "uz"
    start_stop_text = ("🛑 To'xtatish" if lang == "uz" else ("🛑 Остановить" if lang == "ru" else "🛑 Stop")) if user_data.get("is_sending") else ("▶️ Ishga tushirish" if lang == "uz" else ("▶️ Запустить" if lang == "ru" else "▶️ Start"))
    stat_btn_text = "📊 Statistika" if lang == "uz" else ("📊 Статистика" if lang == "ru" else "📊 Statistics")
    timer_btn_text = "⏳ Avto-o'chirish" if lang == "uz" else ("⏳ Автовыключение" if lang == "ru" else "⏳ Auto-Off")
    refresh_btn_text = "🔄 Yangilash" if lang == "uz" else ("🔄 Обновить" if lang == "ru" else "🔄 Refresh")
    inline_kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=start_stop_text, callback_data="toggle_sending"),
            InlineKeyboardButton(text=stat_btn_text, callback_data="statistika")
        ],
        [
            InlineKeyboardButton(text=timer_btn_text, callback_data="timer_setup"),
            InlineKeyboardButton(text=refresh_btn_text, callback_data="refresh_status")
        ]
    ])
    return inline_kb

@router.message(F.text.in_([LOCALIZATION["uz"]["btn_auto_send"], LOCALIZATION["ru"]["btn_auto_send"], LOCALIZATION["en"]["btn_auto_send"]]), StateFilter("*"))
async def menu_autohabar(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    ensure_user(user_id)
    responseText, _ = await generate_control_panel_text(user_id)
    inline_kb = await get_control_panel_markup(user_id)
    await message.answer(responseText, reply_markup=inline_kb, parse_mode="HTML")

@router.callback_query(F.data == "toggle_sending", StateFilter("*"))
async def callback_toggle_sending(callback_query: types.CallbackQuery, state: FSMContext):
    user_id = callback_query.from_user.id
    ensure_user(user_id)
    user_data = db_users.get(user_id)
    lang = user_data.get("lang", "uz") or "uz"
    if not user_data.get("active_phone"):
        err = "⚠️ Avval profil bog'lashingiz kerak!" if lang == "uz" else ("⚠️ Сначала вам нужно подключить аккаунт!" if lang == "ru" else "⚠️ You must connect an account first!")
        await callback_query.answer(err, show_alert=True)
        return
    is_sending = not user_data.get("is_sending", False)
    db_users[user_id]["is_sending"] = is_sending
    if is_sending:
        db_users[user_id]["is_sending_started_at"] = datetime.now().timestamp()
        db_users[user_id]["next_run_timestamp"] = datetime.now().timestamp()
        alert = "▶️ Reklama yuborish muvaffaqiyatli ishga tushirildi!" if lang == "uz" else ("▶️ Рассылка успешно запущена!" if lang == "ru" else "▶️ Broadcasting started!")
    else:
        db_users[user_id]["is_sending_started_at"] = 0
        alert = "🛑 Reklama yuborish to'xtatildi!" if lang == "uz" else ("🛑 Рассылка остановлена!" if lang == "ru" else "🛑 Broadcasting stopped!")
    save_db()
    await callback_query.answer(alert, show_alert=True)
    responseText, _ = await generate_control_panel_text(user_id)
    inline_kb = await get_control_panel_markup(user_id)
    try:
        await callback_query.message.edit_text(responseText, reply_markup=inline_kb, parse_mode="HTML")
    except Exception:
        pass

@router.callback_query(F.data == "statistika", StateFilter("*"))
async def callback_user_statistika(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    ensure_user(user_id)
    user_data = db_users.get(user_id)
    lang = user_data.get("lang", "uz") or "uz"
    today_sent = user_data.get("today_sent", 0)
    total_sent = user_data.get("total_sent", 0)
    if lang == "uz":
        text = (f"📊 <b>Sizning reklamalar statistikangiz:</b>\n\n"
                f"✔️ Bugun yuborilgan reklamalar: <b>{today_sent} ta</b>\n"
                f"🔄 Umumiy yuborilgan reklamalar: <b>{total_sent} ta</b>\n\n"
                f"🕒 Yangilangan vaqt: <i>{datetime.now().strftime('%d.%m.%Y %H:%M')}</i>")
    elif lang == "ru":
        text = (f"📊 <b>Ваша статистика рассылки:</b>\n\n"
                f"✔️ Отправлено сегодня: <b>{today_sent} шт.</b>\n"
                f"🔄 Всего отправлено: <b>{total_sent} шт.</b>\n\n"
                f"🕒 Обновлено: <i>{datetime.now().strftime('%d.%m.%Y %H:%M')}</i>")
    else:
        text = (f"📊 <b>Your broadcasting statistics:</b>\n\n"
                f"✔️ Sent today: <b>{today_sent}</b>\n"
                f"🔄 Total sent: <b>{total_sent}</b>\n\n"
                f"🕒 Updated: <i>{datetime.now().strftime('%d.%m.%Y %H:%M')}</i>")
    back_label = "← Orqaga" if lang == "uz" else ("← Назад" if lang == "ru" else "← Back")
    inline_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📥 CSV Hisobot yuklash", callback_data="export_stats_csv")],
        [InlineKeyboardButton(text=back_label, callback_data="back_to_panel")]
    ])
    await callback_query.message.edit_text(text, reply_markup=inline_kb, parse_mode="HTML")
    await callback_query.answer()

@router.callback_query(F.data == "export_stats_csv", StateFilter("*"))
async def callback_export_stats_csv(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    ensure_user(user_id)
    user_data = db_users.get(user_id)
    csv_data = [["Guruh Nomi / ID", "Holat", "Vaqt"]]
    groups = user_data.get("cached_groups", [])
    selected_gids = user_data.get("selected_groups", [])
    blacklist = user_data.get("blacklist", [])
    for g in groups:
        gid = g.get("id")
        gname = g.get("name", "Noma'lum Guruh")
        if gid in blacklist:
            status = "Qora Ro'yxatda (Bloklangan)"
        elif gid in selected_gids:
            status = "Faol Tarqatilmoqda"
        else:
            status = "Navbatda Yo'q"
        csv_data.append([gname, status, datetime.now().strftime("%Y-%m-%d %H:%M")])
    output = ""
    for row in csv_data:
        output += ",".join([f'"{str(cell)}"' for cell in row]) + "\n"
    csv_bytes = output.encode('utf-8-sig')
    input_file = BufferedInputFile(csv_bytes, filename=f"AutoHabar_Hisobot_{user_id}.csv")
    try:
        await bot.send_document(
            chat_id=user_id, document=input_file,
            caption="📊 <b>Sizning guruhlaringiz va tarqatish holatlari jadvali (Excel formatda)</b>",
            parse_mode="HTML"
        )
        await callback_query.answer("✅ Hisobot muvaffaqiyatli yuborildi!", show_alert=True)
    except Exception as e:
        await callback_query.answer(f"❌ Yuklashda xatolik: {e}", show_alert=True)

@router.callback_query(F.data == "refresh_status", StateFilter("*"))
async def callback_refresh_status(callback_query: types.CallbackQuery, state: FSMContext):
    user_id = callback_query.from_user.id
    ensure_user(user_id)
    await callback_query.answer(get_text(user_id, "panel_refreshed"))
    responseText, _ = await generate_control_panel_text(user_id)
    inline_kb = await get_control_panel_markup(user_id)
    try:
        await callback_query.message.edit_text(responseText, reply_markup=inline_kb, parse_mode="HTML")
    except Exception:
        pass

# ================= ⏳ AVTO-O'CHIRISH TAYMERINI SOZLASH =================

@router.callback_query(F.data == "timer_setup", StateFilter("*"))
async def callback_timer_setup(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    ensure_user(user_id)
    user_data = db_users.get(user_id)
    lang = user_data.get("lang", "uz") or "uz"
    auto_off = user_data.get("auto_off_hours")
    current_val_text = ("Cheksiz ∞" if lang == "uz" else ("Без ограничений ∞" if lang == "ru" else "Unlimited ∞")) if auto_off is None else f"{auto_off} " + ("soat" if lang == "uz" else ("час" if lang == "ru" else "hours"))
    text = (
        "⏱️ <b>Avto-o'chirish taymerini sozlash</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Joriy o'chish vaqti: <b>{current_val_text}</b>\n\n"
        "Ushbu taymer reklama tarqatish ishga tushganidan so'ng, "
        "belgilangan muddat o'tgach avtomatik ravishda to'xtatish imkonini beradi. "
        "Bu guruhlar orasida ko'p reklama tarqatib, spamga tushib qolmaslikka yordam beradi."
    )
    def check_mark(val):
        return "✓ " if auto_off == val else ""
    hr_suffix = "soat" if lang == "uz" else ("час" if lang == "ru" else "hours")
    inf_text = f"{check_mark(None)}" + ("Cheksiz" if lang == "uz" else ("Без ограничений" if lang == "ru" else "Unlimited"))
    back_label = "← Orqaga" if lang == "uz" else ("← Назад" if lang == "ru" else "← Back")
    kb = [
        [
            InlineKeyboardButton(text=f"{check_mark(1)}1 {hr_suffix}", callback_data="set_timer_1"),
            InlineKeyboardButton(text=f"{check_mark(2)}2 {hr_suffix}", callback_data="set_timer_2"),
            InlineKeyboardButton(text=f"{check_mark(3)}3 {hr_suffix}", callback_data="set_timer_3")
        ],
        [
            InlineKeyboardButton(text=f"{check_mark(6)}6 {hr_suffix}", callback_data="set_timer_6"),
            InlineKeyboardButton(text=f"{check_mark(12)}12 {hr_suffix}", callback_data="set_timer_12"),
            InlineKeyboardButton(text=f"{check_mark(24)}24 {hr_suffix}", callback_data="set_timer_24")
        ],
        [
            InlineKeyboardButton(text=f"{check_mark(48)}48 {hr_suffix}", callback_data="set_timer_48"),
            InlineKeyboardButton(text=f"{check_mark(72)}72 {hr_suffix}", callback_data="set_timer_72"),
            InlineKeyboardButton(text=inf_text, callback_data="set_timer_inf")
        ],
        [InlineKeyboardButton(text=back_label, callback_data="back_to_panel")]
    ]
    await callback_query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="HTML")
    await callback_query.answer()

@router.callback_query(F.data.startswith("set_timer_"), StateFilter("*"))
async def callback_set_timer_val(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    ensure_user(user_id)
    user_data = db_users.get(user_id)
    lang = user_data.get("lang", "uz") or "uz"
    val_part = callback_query.data.split("_")[2]
    if val_part == "inf":
        db_users[user_id]["auto_off_hours"] = None
        alert = "✓ Taymer butunlay o'chirildi (Cheksiz)!" if lang == "uz" else ("✓ Таймер отключен (Без ограничений)!" if lang == "ru" else "✓ Timer disabled (Unlimited)!")
    else:
        hours = int(val_part)
        db_users[user_id]["auto_off_hours"] = hours
        alert = f"✓ Avto-o'chirish {hours} soat qilib belgilandi!" if lang == "uz" else (f"✓ Автовыключение установлено на {hours} ч.!" if lang == "ru" else f"✓ Auto-off successfully set to {hours} hours!")
    save_db()
    await callback_query.answer(alert, show_alert=True)
    await callback_timer_setup(callback_query)

# ================= 📝 HABAR MATNI MENYUSI =================

@router.message(F.text.in_([LOCALIZATION["uz"]["btn_msg_text"], LOCALIZATION["ru"]["btn_msg_text"], LOCALIZATION["en"]["btn_msg_text"]]), StateFilter("*"))
async def menu_habar_matni_msg(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    ensure_user(user_id)
    await show_message_settings(message, user_id)

async def show_message_settings(message: types.Message, user_id: int):
    ensure_user(user_id)
    user_data = db_users.get(user_id)
    lang = user_data.get("lang", "uz") or "uz"
    lbl_yes = "Bor 🖼️" if lang == "uz" else ("Есть 🖼️" if lang == "ru" else "Yes 🖼️")
    lbl_no = "Yo'q ❌" if lang == "uz" else ("Нет ❌" if lang == "ru" else "No ❌")
    reklama_rasm = lbl_yes if user_data.get("reklama_rasm") else lbl_no
    btn_count = len(user_data.get("inline_buttons", []))
    tuglama_soni = (f"Bor ({btn_count} ta) 🔘" if lang == "uz" else (f"Есть ({btn_count} шт) 🔘" if lang == "ru" else f"Yes ({btn_count}) 🔘")) if user_data.get("inline_buttons") else lbl_no
    status_on = "Yoqilgan 📤 (Forward rejim)" if lang == "uz" else ("Включено 📤 (Режим Forward)" if lang == "ru" else "Enabled 📤 (Forward mode)")
    status_off = "O'chirilgan 📝 (Matn rejim)" if lang == "uz" else ("Выключено 📝 (Режим текста)" if lang == "ru" else "Disabled 📝 (Text mode)")
    is_forward = status_on if user_data.get("is_forward_mode") else status_off
    textDetail = LOCALIZATION[lang]["msg_setup"].format(
        matn=user_data.get('reklama_matni'), rasm=reklama_rasm,
        tugmalar=tuglama_soni, status=is_forward
    )
    btn_edit_txt = "✍️ Matnni tahrirlash" if lang == "uz" else ("✍️ Редактировать текст" if lang == "ru" else "✍️ Edit text")
    btn_edit_photo = "🖼️ Rasm yuklash / o'zgartirish" if lang == "uz" else ("🖼️ Загрузить фото" if lang == "ru" else "🖼️ Upload photo")
    btn_edit_forward = "📤 Forward xabar sozlash (Faqat PRO)" if lang == "uz" else ("📤 Настройка Forward (Только PRO)" if lang == "ru" else "📤 Setup Forward (PRO Only)")
    btn_edit_buttons = "🔘 Tugmali xabar (Inline PRO)" if lang == "uz" else ("🔘 Кнопочное сообщение (Inline PRO)" if lang == "ru" else "🔘 Buttoned message (Inline PRO)")
    btn_toggle = "🔄 Rejimni almashtirish (Matn/Forward)" if lang == "uz" else ("🔄 Сменить режим (Текст/Forward)" if lang == "ru" else "🔄 Toggle mode (Text/Forward)")
    btn_clear = "❌ Rasm va tugmalarni tozalash" if lang == "uz" else ("❌ Очистить медиа и кнопки" if lang == "ru" else "❌ Clear media & buttons")
    btn_preview = "👁️ Xabarni ko'rish" if lang == "uz" else ("👁️ Посмотреть сообщение" if lang == "ru" else "👁️ Preview message")
    btn_back = "← Orqaga" if lang == "uz" else ("← Назад" if lang == "ru" else "← Back")
    inline_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=btn_edit_txt, callback_data="edit_text")],
        [InlineKeyboardButton(text=btn_edit_photo, callback_data="edit_photo")],
        [InlineKeyboardButton(text=btn_edit_forward, callback_data="edit_forward")],
        [InlineKeyboardButton(text=btn_edit_buttons, callback_data="edit_buttons_pro")],
        [InlineKeyboardButton(text=btn_toggle, callback_data="toggle_forward_mode")],
        [InlineKeyboardButton(text=btn_clear, callback_data="clear_media_buttons")],
        [InlineKeyboardButton(text=btn_preview, callback_data="preview_ad_post")],
        [InlineKeyboardButton(text=btn_back, callback_data="back_to_panel")]
    ])
    await message.answer(textDetail, reply_markup=inline_kb, parse_mode="HTML")

@router.callback_query(F.data == "preview_ad_post", StateFilter("*"))
async def callback_preview_ad_post(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    ensure_user(user_id)
    user_data = db_users.get(user_id)
    parts = user_data.get("reklama_matni", "").split("===")
    raw_text = parts[0].strip() if parts else "Xabar matni bo'sh"
    spin_text = apply_spintax(raw_text)
    # Watermark faqat Free foydalanuvchilar uchun
    is_pro = user_data.get("is_pro", False)
    bot_link = "https://t.me/Auto_Xabar_Yuborish_Bot"
    watermark = '' if is_pro else f'\n\n<a href="{bot_link}">bu bot orqali yuborildi</a>'
    final_preview_text = f"{spin_text}{watermark}"
    await callback_query.message.answer("👁️ <b>Xabaringiz guruhlarda quyidagi ko'rinishda chiqadi:</b>\n━━━━━━━━━━━━━━━━━━━━", parse_mode="HTML")
    buttons_data = user_data.get("inline_buttons", [])
    kb_list = []
    for btn in buttons_data:
        kb_list.append([InlineKeyboardButton(text=btn["text"], url=btn["url"])])
    kb_markup = InlineKeyboardMarkup(inline_keyboard=kb_list) if kb_list else None
    photo_path = user_data.get("reklama_rasm")
    if photo_path and os.path.exists(photo_path):
        try:
            photo_file = FSInputFile(photo_path)
            await bot.send_photo(chat_id=user_id, photo=photo_file, caption=final_preview_text, reply_markup=kb_markup, parse_mode="HTML")
        except Exception:
            await bot.send_message(chat_id=user_id, text=final_preview_text, reply_markup=kb_markup, parse_mode="HTML")
    else:
        await bot.send_message(chat_id=user_id, text=final_preview_text, reply_markup=kb_markup, parse_mode="HTML")
    await callback_query.answer()

@router.callback_query(F.data == "edit_text", StateFilter("*"))
async def callback_edit_text(callback_query: types.CallbackQuery, state: FSMContext):
    await state.clear()
    user_id = callback_query.from_user.id
    lang = db_users[user_id].get("lang", "uz") or "uz"
    prompt = "✍️ <b>Yangi reklama matnini yuboring:</b>" if lang == "uz" else ("✍️ <b>Отправьте новый рекламный текст:</b>" if lang == "ru" else "✍️ <b>Send new ad text:</b>")
    await state.set_state(TextStates.waiting_text)
    await callback_query.message.answer(prompt, parse_mode="HTML")
    await callback_query.answer()

@router.message(StateFilter(TextStates.waiting_text))
async def message_receive_text(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    ensure_user(user_id)
    lang = db_users[user_id].get("lang", "uz") or "uz"
    new_text = message.text
    db_users[user_id]["reklama_matni"] = new_text
    save_db()
    success = "✅ <b>Reklama matni o'zgartirildi!</b>" if lang == "uz" else ("✅ <b>Рекламный текст изменен!</b>" if lang == "ru" else "✅ <b>Ad text successfully updated!</b>")
    await message.answer(success, reply_markup=get_main_keyboard(user_id), parse_mode="HTML")
    await show_message_settings(message, user_id)
    await state.clear()

@router.callback_query(F.data == "edit_photo", StateFilter("*"))
async def callback_edit_photo(callback_query: types.CallbackQuery, state: FSMContext):
    await state.clear()
    user_id = callback_query.from_user.id
    lang = db_users[user_id].get("lang", "uz") or "uz"
    prompt = "🖼️ <b>Reklama uchun rasmni oddiy rasm ko'rinishida yuboring:</b>" if lang == "uz" else ("🖼️ <b>Отправьте изображение для рекламы как обычное фото:</b>" if lang == "ru" else "🖼️ <b>Send an image for the ad as a regular photo:</b>")
    await state.set_state(TextStates.waiting_photo)
    await callback_query.message.answer(prompt, parse_mode="HTML")
    await callback_query.answer()

@router.message(StateFilter(TextStates.waiting_photo), F.photo)
async def message_receive_photo(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    ensure_user(user_id)
    lang = db_users[user_id].get("lang", "uz") or "uz"
    photo = message.photo[-1]
    file_info = await bot.get_file(photo.file_id)
    local_path = os.path.join(DOWNLOADS_DIR, f"reklama_{user_id}.jpg")
    await bot.download_file(file_info.file_path, local_path)
    # Rasmni base64 ga o'tkazib bazada saqlaymiz (konteyner qayta ishga tushganda tiklanadi)
    try:
        with open(local_path, "rb") as f:
            img_bytes = f.read()
        b64 = base64.b64encode(img_bytes).decode('utf-8')
        # Firestore hujjat chegarasi ~1MB; juda katta bo'lsa base64 ni saqlamaymiz
        if len(b64) < 700000:
            db_users[user_id]["reklama_rasm_b64"] = b64
        else:
            db_users[user_id]["reklama_rasm_b64"] = None
            logging.warning(f"[Rasm] User {user_id} rasmi juda katta, base64 saqlanmadi (faqat mahalliy fayl).")
    except Exception as e:
        logging.error(f"[Rasm] Base64 kodlashda xato: {e}")
        db_users[user_id]["reklama_rasm_b64"] = None
    db_users[user_id]["reklama_rasm"] = local_path
    save_db()
    success = "✅ <b>Reklama rasmi muvaffaqiyatli saqlandi!</b>" if lang == "uz" else ("✅ <b>Рекламное изображение успешно сохранено!</b>" if lang == "ru" else "✅ <b>Ad image successfully saved!</b>")
    await message.answer(success, reply_markup=get_main_keyboard(user_id), parse_mode="HTML")
    await show_message_settings(message, user_id)
    await state.clear()

@router.message(StateFilter(TextStates.waiting_photo))
async def message_receive_photo_invalid(message: types.Message):
    user_id = message.from_user.id
    lang = db_users[user_id].get("lang", "uz") or "uz"
    err = "⚠️ Iltimos, reklama uchun rasm shaklida fayl yuboring!" if lang == "uz" else ("⚠️ Пожалуйста, отправьте файл в формате изображения!" if lang == "ru" else "⚠️ Please send a file in image format!")
    await message.answer(err)

@router.callback_query(F.data == "clear_media_buttons", StateFilter("*"))
async def callback_clear_media_buttons(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    ensure_user(user_id)
    lang = db_users[user_id].get("lang", "uz") or "uz"
    old_path = db_users[user_id].get("reklama_rasm")
    if old_path and os.path.exists(old_path):
        try:
            os.remove(old_path)
        except Exception:
            pass
    db_users[user_id]["reklama_rasm"] = None
    db_users[user_id]["reklama_rasm_b64"] = None
    db_users[user_id]["inline_buttons"] = []
    db_users[user_id]["is_forward_mode"] = False
    db_users[user_id]["forward_chat_id"] = None
    db_users[user_id]["forward_msg_id"] = None
    save_db()
    alert = "❌ Barcha media va tugmalar olib tashlandi!" if lang == "uz" else ("❌ Все медиа и кнопки удалены!" if lang == "ru" else "❌ All media and buttons cleared!")
    await callback_query.answer(alert, show_alert=True)
    try:
        await callback_query.message.delete()
    except Exception:
        pass
    await show_message_settings(callback_query.message, user_id)

@router.callback_query(F.data == "edit_buttons_pro", StateFilter("*"))
async def callback_edit_buttons_pro(callback_query: types.CallbackQuery, state: FSMContext):
    user_id = callback_query.from_user.id
    ensure_user(user_id)
    lang = db_users[user_id].get("lang", "uz") or "uz"
    pro_alert = "👑 Bu funksiyadan foydalanish uchun PRO bo'lishingiz shart!" if lang == "uz" else ("👑 Для использования этой функции у вас должен быть статус PRO!" if lang == "ru" else "👑 You need PRO status to use this feature!")
    if not db_users.get(user_id, {}).get("is_pro", False):
        await callback_query.answer(pro_alert, show_alert=True)
        return
    await state.clear()
    await state.set_state(TextStates.waiting_buttons)
    prompt = (
        "🔘 <b>Tugmalarni quyidagi formatda yozib yuboring:</b>\n\n"
        "<code>Mening saytim | https://havola.uz</code>\n\n"
        "<i>Agar tugmalar soni bir nechta bo'lsa, har birini yangi qatordan kiriting.</i>"
    )
    await callback_query.message.answer(prompt, parse_mode="HTML")
    await callback_query.answer()

@router.message(StateFilter(TextStates.waiting_buttons))
async def message_receive_buttons(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    ensure_user(user_id)
    lines = message.text.strip().split("\n")
    buttons = []
    for line in lines:
        if "|" in line:
            parts = line.split("|")
            text = parts[0].strip()
            url = parts[1].strip()
            if url.startswith("http://") or url.startswith("https://"):
                buttons.append({"text": text, "url": url})
    if buttons:
        db_users[user_id]["inline_buttons"] = buttons
        save_db()
        await message.answer(f"✅ <b>{len(buttons)} ta tugma muvaffaqiyatli saqlandi!</b>", reply_markup=get_main_keyboard(user_id), parse_mode="HTML")
        await show_message_settings(message, user_id)
        await state.clear()
    else:
        await message.answer("❌ <b>Format xato!</b> Namunadagidek yozing:\n<code>Telegram | https://t.me/kanal</code>", parse_mode="HTML")

@router.callback_query(F.data == "edit_forward", StateFilter("*"))
async def callback_edit_forward(callback_query: types.CallbackQuery, state: FSMContext):
    user_id = callback_query.from_user.id
    ensure_user(user_id)
    lang = db_users[user_id].get("lang", "uz") or "uz"
    pro_alert = "👑 Bu funksiyadan foydalanish uchun PRO bo'lishingiz shart!" if lang == "uz" else ("👑 Для использования этой функции у вас должен быть статус PRO!" if lang == "ru" else "👑 You need PRO status to use this feature!")
    if not db_users[user_id].get("is_pro", False):
        await callback_query.answer(pro_alert, show_alert=True)
        return
    await state.clear()
    await state.set_state(TextStates.waiting_forward)
    prompt = (
        "📤 <b>Forward xabar sozlash bo'limi (Faqat PRO)</b>\n\n"
        "Iltimos, o'zingizning kanalingizdan istalgan xabarni (rasmli, tugmali, matnli) **ushbu botga forward (uzatish)** qiling.\n\n"
        "<i>Bot o'sha xabarni guruhlarga ko'rishlar sonini oshiradigan va kanal havolasini saqlaydigan qibly yuboradi.</i>"
    )
    await callback_query.message.answer(prompt, parse_mode="HTML")
    await callback_query.answer()

@router.message(StateFilter(TextStates.waiting_forward))
async def message_receive_forward(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    ensure_user(user_id)
    if not message.forward_origin:
        await message.answer("⚠️ Iltimos, xabarni o'zingiz yozmang! Kanaldan **forward (uzatish)** qiling.")
        return
    forward_chat_id = None
    if isinstance(message.forward_origin, types.MessageOriginChannel):
        forward_chat_id = message.forward_origin.chat.id
    elif isinstance(message.forward_origin, types.MessageOriginUser):
        forward_chat_id = user_id
    elif isinstance(message.forward_origin, types.MessageOriginChat):
        forward_chat_id = message.forward_origin.sender_chat.id
    if not forward_chat_id:
        forward_chat_id = message.forward_from_chat.id if message.forward_from_chat else user_id
    forward_msg_id = message.forward_from_message_id or message.message_id
    db_users[user_id]["forward_chat_id"] = forward_chat_id
    db_users[user_id]["forward_msg_id"] = forward_msg_id
    db_users[user_id]["is_forward_mode"] = True
    save_db()
    success = (
        "✅ <b>Uzatilgan (Forward) xabaringiz muvaffaqiyatli saqlandi!</b>\n\n"
        "Endi bot guruhlarga ushbu xabarni ko'rishlar sonini ko'paytiradigan va kanal havolasini saqlab qoladigan qibly yuboradi."
    )
    await message.answer(success, reply_markup=get_main_keyboard(user_id), parse_mode="HTML")
    await show_message_settings(message, user_id)
    await state.clear()

@router.callback_query(F.data == "toggle_forward_mode", StateFilter("*"))
async def callback_toggle_forward_mode(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    ensure_user(user_id)
    lang = db_users[user_id].get("lang", "uz") or "uz"
    pro_alert = "👑 Bu funksiyadan foydalanish uchun PRO bo'lishingiz shart!" if lang == "uz" else ("👑 Для использования этой функции у вас должен быть статус PRO!" if lang == "ru" else "👑 You need PRO status to use this feature!")
    if not db_users[user_id].get("is_pro", False):
        await callback_query.answer(pro_alert, show_alert=True)
        return
    db_users[user_id]["is_forward_mode"] = not db_users[user_id].get("is_forward_mode", False)
    save_db()
    status_msg = ("Forward rejimga o'tkazildi! 📤" if db_users[user_id]["is_forward_mode"] else "Matn/Media rejimga o'tkazildi! 📝")
    await callback_query.answer(f"✓ {status_msg}", show_alert=True)
    await show_message_settings(callback_query.message, user_id)

# ================= GURUHLARNI SOZLASH MENYUSI =================

@router.message(F.text.in_([LOCALIZATION["uz"]["btn_groups"], LOCALIZATION["ru"]["btn_groups"], LOCALIZATION["en"]["btn_groups"]]), StateFilter("*"))
async def menu_guruhlar(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    ensure_user(user_id)
    user_data = db_users.get(user_id)
    lang = user_data.get("lang", "uz") or "uz"
    choice = user_data.get("groups_choice", "custom")
    if lang == "uz":
        tanlov_nomi = "Hamma guruhlarga" if choice == "all" else "O'zim tanlayman"
    elif lang == "ru":
        tanlov_nomi = "Во все группы" if choice == "all" else "Выбираю сам"
    else:
        tanlov_nomi = "All groups" if choice == "all" else "Custom selection"
    cabinet_template = LOCALIZATION[lang]["groups_setup"]
    text = cabinet_template.format(tanlov=tanlov_nomi)
    btn_all = "+ Hamma guruhlarga" if lang == "uz" else ("+ Во все группы" if lang == "ru" else "+ All groups")
    btn_custom = "✓ O'zim tanlayman" if lang == "uz" else ("✓ Выбираю сам" if lang == "ru" else "✓ Custom selection")
    btn_lists = "📊 Ro'yxatlar" if lang == "uz" else ("📊 Списки" if lang == "ru" else "📊 Lists")
    btn_add = "+ Qo'shish" if lang == "uz" else ("+ Обновить" if lang == "ru" else "+ Reload")
    btn_search = "🔍 Guruh Qidirish" if lang == "uz" else ("🔍 Поиск Групп" if lang == "ru" else "🔍 Group Scraper")
    btn_clear = "🚨 O'chirish" if lang == "uz" else ("🚨 Очистить" if lang == "ru" else "🚨 Clear")
    btn_back = "← Orqaga" if lang == "uz" else ("← Назад" if lang == "ru" else "← Back")
    inline_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=btn_all, callback_data="set_groups_all")],
        [InlineKeyboardButton(text=btn_custom, callback_data="set_groups_custom")],
        [
            InlineKeyboardButton(text=btn_lists, callback_data="groups_list_page_0"),
            InlineKeyboardButton(text=btn_add, callback_data="refresh_groups_force"),
            InlineKeyboardButton(text=btn_clear, callback_data="clear_selected_groups")
        ],
        [InlineKeyboardButton(text=btn_search, callback_data="open_group_scraper")],
        [InlineKeyboardButton(text=btn_back, callback_data="back_to_panel")]
    ])
    await message.answer(text, reply_markup=inline_kb, parse_mode="HTML")

@router.callback_query(F.data == "open_group_scraper", StateFilter("*"))
async def callback_open_group_scraper(callback_query: types.CallbackQuery, state: FSMContext):
    user_id = callback_query.from_user.id
    ensure_user(user_id)
    if not db_users[user_id].get("active_phone"):
        await callback_query.answer("⚠️ Avval profil bog'lashingiz kerak!", show_alert=True)
        return
    await state.set_state(TextStates.waiting_search_query)
    await callback_query.message.answer(
        "🔍 <b>Avtomatik Guruh Qidirish va A'zo Bo'lish (Group Scraper)</b>\n━━━━━━━━━━━━━━━━━━━━\n"
        "Kalit so'z kiriting (masalan: <i>reklama, bozor, vakansiya</i>) "
        "va bot ochiq Telegram guruhlarini qidirib, sizning profilingizga avtomatik a'zo qilib beradi!",
        parse_mode="HTML"
    )
    await callback_query.answer()

@router.message(StateFilter(TextStates.waiting_search_query))
async def state_save_search_query(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    ensure_user(user_id)
    query = message.text.strip()
    active_phone = db_users[user_id].get("active_phone")
    if not active_phone:
        await message.answer("⚠️ Avval profil bog'lashingiz kerak!")
        await state.clear()
        return
    progress_msg = await message.answer("🔍 <b>Telegram tarmoqlari qidirilmoqda, kuting...</b>", parse_mode="HTML")
    try:
        client = await get_client(user_id, active_phone)
        result = await client(SearchRequest(q=query, limit=10))
        joined_count = 0
        for chat in result.chats:
            try:
                await client(JoinChannelRequest(chat))
                joined_count += 1
                await asyncio.sleep(2)
            except Exception:
                continue
        try:
            await progress_msg.delete()
        except Exception:
            pass
        await message.answer(
            f"✅ <b>Guruh qidirish yakunlandi!</b>\n"
            f"🔍 Kalit so'z: <b>\"{query}\"</b>\n"
            f"➕ Muvaffaqiyatli a'zo bo'lindi: <b>{joined_count} ta guruhga</b>\n\n"
            f"💡 <i>Guruhlarni sozlash bo'limidan '+ Qo'shish' (Yangilash) tugmasini bosing!</i>",
            parse_mode="HTML"
        )
    except Exception as e:
        try:
            await progress_msg.delete()
        except Exception:
            pass
        await message.answer(f"❌ Qidiruv xatosi: {e}")
    await state.clear()

@router.callback_query(F.data == "set_groups_all", StateFilter("*"))
async def callback_groups_all(callback_query: types.CallbackQuery, state: FSMContext):
    user_id = callback_query.from_user.id
    ensure_user(user_id)
    lang = db_users[user_id].get("lang", "uz") or "uz"
    db_users[user_id]["groups_choice"] = "all"
    save_db()
    alert = "✓ Hamma guruhlar tanlandi!" if lang == "uz" else ("✓ Выбраны все группы!" if lang == "ru" else "✓ All groups selected!")
    await callback_query.answer(alert, show_alert=True)
    await menu_guruhlar(callback_query.message, state)

@router.callback_query(F.data == "set_groups_custom", StateFilter("*"))
async def callback_groups_custom(callback_query: types.CallbackQuery, state: FSMContext):
    user_id = callback_query.from_user.id
    ensure_user(user_id)
    lang = db_users[user_id].get("lang", "uz") or "uz"
    db_users[user_id]["groups_choice"] = "custom"
    save_db()
    alert = "✓ Qo'lda tanlash rejimi faollashdi!" if lang == "uz" else ("✓ Режим ручного выбора активирован!" if lang == "ru" else "✓ Custom selection mode activated!")
    await callback_query.answer(alert, show_alert=True)
    await menu_guruhlar(callback_query.message, state)

@router.callback_query(F.data == "clear_selected_groups", StateFilter("*"))
async def callback_clear_groups(callback_query: types.CallbackQuery, state: FSMContext = None):
    user_id = callback_query.from_user.id
    ensure_user(user_id)
    lang = db_users[user_id].get("lang", "uz") or "uz"
    db_users[user_id]["selected_groups"] = []
    save_db()
    alert = "🚨 Tanlangan barcha guruhlar tozalandi!" if lang == "uz" else ("🚨 Списки выбранных групп очищены!" if lang == "ru" else "🚨 Selected groups cleared!")
    await callback_query.answer(alert, show_alert=True)
    await callback_groups_list(callback_query)

@router.callback_query(F.data == "refresh_groups_force", StateFilter("*"))
async def callback_refresh_groups_force(callback_query: types.CallbackQuery, state: FSMContext = None):
    user_id = callback_query.from_user.id
    ensure_user(user_id)
    lang = db_users[user_id].get("lang", "uz") or "uz"
    active_phone = db_users[user_id].get("active_phone")
    # active_phone bo'lmasa, birinchi akkauntdan foydalanamiz
    if not active_phone:
        accounts_list = db_users[user_id].get("accounts", [])
        if accounts_list:
            active_phone = accounts_list[0]["phone"]
            db_users[user_id]["active_phone"] = active_phone
            save_db()
    if not active_phone:
        err = "⚠️ Avval profil bog'lashingiz kerak!" if lang == "uz" else ("⚠️ Сначала вам нужно подключить аккаунт!" if lang == "ru" else "⚠️ You must connect an account first!")
        await callback_query.answer(err, show_alert=True)
        return
    prompt = "Guruh keshini yangilash boshlandi..." if lang == "uz" else ("Запущено обновление списка групп..." if lang == "ru" else "Group list reload started...")
    await callback_query.answer(prompt)
    try:
        client = await get_client(user_id, active_phone)
        guruhlar = []
        async for dialog in client.iter_dialogs():
            if dialog.is_group:
                guruhlar.append({"id": int(dialog.id), "name": str(dialog.name), "participants_count": 150})
        db_users[user_id]["cached_groups"] = guruhlar
        save_db()
        await callback_groups_list(callback_query)
    except Exception as e:
        await callback_query.message.answer(f"❌ Xatolik yuz berdi: {e}")

@router.callback_query(F.data.startswith("groups_list_page_"), StateFilter("*"))
async def callback_groups_list(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    ensure_user(user_id)
    user_data = db_users.get(user_id)
    lang = user_data.get("lang", "uz") or "uz"
    page = 0
    if callback_query.data.startswith("groups_list_page_"):
        try:
            page = int(callback_query.data.split("_")[3])
        except Exception:
            page = 0
    guruhlar = user_data.get("cached_groups", [])
    if not guruhlar:
        err = "⚠️ Kesh bo'sh, iltimos '+ Qo'shish' (Yangilash) tugmasini bosing!" if lang == "uz" else ("⚠️ Список пуст, пожалуйста, нажмите кнопку '+ Обновить'!" if lang == "ru" else "⚠️ List is empty, please click the '+ Reload' button!")
        await callback_query.answer(err, show_alert=True)
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
        g_id = int(g["id"])
        g_name = str(g["name"])
        is_selected = g_id in selected_ids
        icon = "✔" if is_selected else "➕"
        btn_text = f"{icon} {g_name[:12]}"
        row.append(InlineKeyboardButton(text=btn_text, callback_data=f"toggle_group_{g_id}_{page}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    nav_buttons = []
    btn_prev = "⬅ Oldingi" if lang == "uz" else ("⬅ Назад" if lang == "ru" else "⬅ Prev")
    btn_next = "Keyingi ➡" if lang == "uz" else ("Вперед ➡" if lang == "ru" else "Next ➡")
    if page > 0:
        nav_buttons.append(InlineKeyboardButton(text=btn_prev, callback_data=f"groups_list_page_{page-1}"))
    if end_idx < total_groups:
        nav_buttons.append(InlineKeyboardButton(text=btn_next, callback_data=f"groups_list_page_{page+1}"))
    if nav_buttons:
        buttons.append(nav_buttons)
    btn_all_sel = "✔ Hammasi" if lang == "uz" else ("✔ Все" if lang == "ru" else "✔ All")
    btn_del_sel = "❌ O'chirish" if lang == "uz" else ("❌ Сбросить" if lang == "ru" else "❌ Reset")
    btn_save = f"💾 Saqlash ({len(selected_ids)} ta)" if lang == "uz" else (f"💾 Сохранить ({len(selected_ids)} шт)" if lang == "ru" else f"💾 Save ({len(selected_ids)})")
    buttons.append([
        InlineKeyboardButton(text=btn_all_sel, callback_data=f"select_all_groups_{page}"),
        InlineKeyboardButton(text=btn_del_sel, callback_data=f"deselect_all_groups_{page}")
    ])
    buttons.append([InlineKeyboardButton(text=btn_save, callback_data="save_groups_selection")])
    if lang == "uz":
        text = f"<b>Guruhlarni tanlang (Tanlangan: {len(selected_ids)} ta)</b>\nJami: <b>{total_groups} ta</b> guruh."
    elif lang == "ru":
        text = f"<b>Выберите группы (Выбрано: {len(selected_ids)} шт)</b>\nВсего групп: <b>{total_groups}</b>."
    else:
        text = f"<b>Select groups (Selected: {len(selected_ids)})</b>\nTotal groups: <b>{total_groups}</b>."
    try:
        await callback_query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")
    except Exception:
        await callback_query.message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")

@router.callback_query(F.data.startswith("toggle_group_"), StateFilter("*"))
async def callback_toggle_group(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    ensure_user(user_id)
    parts = callback_query.data.split("_")
    group_id = int(parts[2])
    selected_ids = [int(x) for x in db_users[user_id].get("selected_groups", [])]
    if group_id in selected_ids:
        selected_ids.remove(group_id)
    else:
        selected_ids.append(group_id)
    db_users[user_id]["selected_groups"] = selected_ids
    save_db()
    await callback_groups_list(callback_query)

@router.callback_query(F.data.startswith("select_all_groups_"), StateFilter("*"))
async def callback_select_all_groups(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    ensure_user(user_id)
    guruhlar = db_users[user_id].get("cached_groups", [])
    selected_ids = [int(g["id"]) for g in guruhlar]
    db_users[user_id]["selected_groups"] = selected_ids
    save_db()
    await callback_groups_list(callback_query)

@router.callback_query(F.data.startswith("deselect_all_groups_"), StateFilter("*"))
async def callback_deselect_all_groups(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    ensure_user(user_id)
    db_users[user_id]["selected_groups"] = []
    save_db()
    await callback_groups_list(callback_query)

@router.callback_query(F.data == "save_groups_selection", StateFilter("*"))
async def callback_save_groups(callback_query: types.CallbackQuery, state: FSMContext):
    user_id = callback_query.from_user.id
    ensure_user(user_id)
    lang = db_users[user_id].get("lang", "uz") or "uz"
    g_count = len(db_users[user_id].get("selected_groups", []))
    alert = f"✓ {g_count} ta guruh saqlandi!" if lang == "uz" else (f"✓ {g_count} групп сохранено!" if lang == "ru" else f"✓ {g_count} groups saved!")
    await callback_query.answer(alert, show_alert=True)
    await menu_guruhlar(callback_query.message, state)

# ================= 👤 PROFILLAR BO'LIMI (MULTI-ACCOUNT) =================

@router.message(F.text.in_([LOCALIZATION["uz"]["btn_profiles"], LOCALIZATION["ru"]["btn_profiles"], LOCALIZATION["en"]["btn_profiles"]]), StateFilter("*"))
async def menu_profillar(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    ensure_user(user_id)
    await show_profillar_settings(message, user_id)

async def show_profillar_settings(message: types.Message, user_id: int):
    user_data = db_users.get(user_id)
    accounts_list = user_data.get("accounts", [])
    active_phone = user_data.get("active_phone")
    lang = user_data.get("lang", "uz") or "uz"
    lbl_none = "Mavjud emas ❌" if lang == "uz" else ("Не подключен ❌" if lang == "ru" else "None ❌")
    p_active = active_phone if active_phone else lbl_none
    cabinet_template = LOCALIZATION[lang]["profile_title"]
    text = cabinet_template.format(active=p_active)
    buttons = []
    for acc in accounts_list:
        phone = acc["phone"]
        status_icon = "🟢" if phone == active_phone else "⚪"
        buttons.append([
            InlineKeyboardButton(text=f"{status_icon} {phone} ({acc['name'][:10]})", callback_data="manage_acc_" + phone)
        ])
    btn_add = "➕ Yangi profil qo'shish" if lang == "uz" else ("➕ Добавить новый аккаунт" if lang == "ru" else "➕ Add new profile")
    btn_back = "← Orqaga" if lang == "uz" else ("← Назад" if lang == "ru" else "← Back")
    buttons.append([InlineKeyboardButton(text=btn_add, callback_data="add_account")])
    buttons.append([InlineKeyboardButton(text=btn_back, callback_data="back_to_panel")])
    try:
        await message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")
    except Exception:
        await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")

@router.callback_query(F.data.startswith("manage_acc_"), StateFilter("*"))
async def callback_manage_acc(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    ensure_user(user_id)
    lang = db_users[user_id].get("lang", "uz") or "uz"
    phone = callback_query.data.replace("manage_acc_", "")
    accounts_list = db_users[user_id].get("accounts", [])
    target_acc = next((acc for acc in accounts_list if acc["phone"] == phone), None)
    if not target_acc:
        err = "⚠️ Profil topilmadi!" if lang == "uz" else ("⚠️ Профиль не найден!" if lang == "ru" else "⚠️ Profile not found!")
        await callback_query.answer(err, show_alert=True)
        return
    text = (
        "📱 <b>Profil sozlamalari: " + phone + "</b>\n"
        "🏷️ Ism: <b>" + target_acc['name'] + "</b>\n"
        "🌐 Username: <b>" + target_acc['username'] + "</b>\n\n"
        "Ushbu profilni nima qilishni xohlajesiz?"
    )
    btn_active = "🟢 Faol qilish" if lang == "uz" else ("🟢 Сделать активным" if lang == "ru" else "🟢 Make active")
    btn_del = "⚠️ Uzish (O'chirish)" if lang == "uz" else ("⚠️ Отключить" if lang == "ru" else "⚠️ Disconnect")
    btn_back = "← Profillarga qaytish" if lang == "uz" else ("← Назад к профилям" if lang == "ru" else "← Back to profiles")
    kb = [
        [
            InlineKeyboardButton(text=btn_active, callback_data="activate_acc_" + phone),
            InlineKeyboardButton(text=btn_del, callback_data="delete_acc_" + phone)
        ],
        [InlineKeyboardButton(text=btn_back, callback_data="go_to_profillar")]
    ]
    await callback_query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="HTML")
    await callback_query.answer()

@router.callback_query(F.data == "go_to_profillar", StateFilter("*"))
async def callback_go_to_profillar(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    ensure_user(user_id)
    await show_profillar_settings(callback_query.message, user_id)
    await callback_query.answer()

@router.callback_query(F.data.startswith("activate_acc_"), StateFilter("*"))
async def callback_activate_acc(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    ensure_user(user_id)
    phone = callback_query.data.replace("activate_acc_", "")
    accounts_list = db_users[user_id].get("accounts", [])
    target_acc = next((acc for acc in accounts_list if acc["phone"] == phone), None)
    if target_acc:
        db_users[user_id]["active_phone"] = phone
        db_users[user_id]["active_name"] = target_acc["name"]
        db_users[user_id]["active_username"] = target_acc["username"]
        save_db()
        await callback_query.answer("✓ " + phone + " muvaffaqiyatli faollashtirildi!", show_alert=True)
    await show_profillar_settings(callback_query.message, user_id)

@router.callback_query(F.data.startswith("delete_acc_"), StateFilter("*"))
async def callback_delete_acc(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    ensure_user(user_id)
    phone = callback_query.data.replace("delete_acc_", "")
    accounts_list = db_users[user_id].get("accounts", [])
    cleaned_accounts = [acc for acc in accounts_list if acc["phone"] != phone]
    db_users[user_id]["accounts"] = cleaned_accounts
    if db_users[user_id].get("active_phone") == phone:
        db_users[user_id]["active_phone"] = None
        db_users[user_id]["is_sending"] = False
        db_users[user_id]["is_sending_started_at"] = 0
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
    await callback_query.answer("⚠️ Profil muvaffaqiyatli o'chirildi!", show_alert=True)
    await show_profillar_settings(callback_query.message, user_id)

# ================= PRO TARIF / INTERVAL / DEPOSIT =================

@router.message(F.text == "👑 Pro tarif", StateFilter("*"))
async def menu_pro_tarif(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    ensure_user(user_id)
    user_data = db_users.get(user_id)
    lang = user_data.get("lang", "uz") or "uz"
    ref_link = f"https://t.me/Auto_Xabar_Yuborish_Bot?start=ref_{user_id}"
    pro_info_template = LOCALIZATION[lang]["pro_info"] if lang in ["uz", "ru"] else LOCALIZATION["en"]["pro_info"]
    text = pro_info_template.format(ref_link=ref_link)
    btn_buy = "💳 10,000 UZS bilan sotib olish" if lang == "uz" else ("💳 Купить за 10,000 UZS" if lang == "ru" else "💳 Buy for 10,000 UZS")
    btn_share = "🔗 Taklif havolasini ulashish" if lang == "uz" else ("🔗 Поделиться ссылкой" if lang == "ru" else "🔗 Share referral link")
    btn_back = "← Orqaga" if lang == "uz" else ("← Назад" if lang == "ru" else "← Back")
    inline_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=btn_buy, callback_data="buy_pro_balance")],
        [InlineKeyboardButton(text=btn_share, url="https://t.me/share/url?url=" + ref_link + "&text=Guruhlarga+avtomatik+reklama+yuboruvchi+zor+botni+sinab+koring!")],
        [InlineKeyboardButton(text=btn_back, callback_data="back_to_panel")]
    ])
    await message.answer(text, reply_markup=inline_kb, parse_mode="HTML")

@router.callback_query(F.data == "buy_pro_balance", StateFilter("*"))
async def callback_buy_pro_balance(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    ensure_user(user_id)
    user_data = db_users.get(user_id)
    lang = user_data.get("lang", "uz") or "uz"
    pro_active_alert = "👑 Sizda allaqachon PRO tarif faollashtirilgan!" if lang == "uz" else ("👑 У вас уже активирован тариф PRO!" if lang == "ru" else "👑 You already have PRO status enabled!")
    if user_data.get("is_pro", False):
        await callback_query.answer(pro_active_alert, show_alert=True)
        return
    if user_data.get("balans", 0) >= 10000:
        db_users[user_id]["balans"] = user_data["balans"] - 10000
        db_users[user_id]["is_pro"] = True
        save_db()
        await force_flush_db()  # Muhim operatsiya - darhol bulutga yozamiz
        await callback_query.answer("🎉 Tabriklaymiz! PRO tarif muvaffaqiyatli faollashtirildi! 👑", show_alert=True)
        await menu_kabinet_msg(callback_query.message, user_id)
    else:
        err = (f"❌ Hisobingizda mablag' yetarli emas!\n"
               f"Joriy balans: {user_data.get('balans', 0):,} so'm\n"
               f"PRO narxi: 10,000 so'm.\n\n"
               f"Botga 6 ta yangi odam taklif qibly, bepul PRO oling!")
        await callback_query.answer(err, show_alert=True)

@router.message(F.text.in_([LOCALIZATION["uz"]["btn_interval"], LOCALIZATION["ru"]["btn_interval"], LOCALIZATION["en"]["btn_interval"]]), StateFilter("*"))
async def menu_interval(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    ensure_user(user_id)
    user_data = db_users.get(user_id)
    lang = user_data.get("lang", "uz") or "uz"
    current_interval = user_data.get('interval', 15)
    if current_interval >= 60:
        hours = current_interval / 60
        unit = "soat" if lang == "uz" else ("час" if lang == "ru" else "hours")
        interval_text = f"{int(hours) if hours.is_integer() else hours} {unit}"
    else:
        unit = "daqiqa" if lang == "uz" else ("минут" if lang == "ru" else "minutes")
        interval_text = f"{current_interval} {unit}"
    text = "⏱️ <b>Habar oraligi</b>\n\n" + f"Joriy interval: <b>{interval_text}</b>\n\n" + "Kerakli vaqtni tanlang:"
    await message.answer(text, reply_markup=get_interval_keyboard(current_interval), parse_mode="HTML")

@router.callback_query(F.data.startswith("set_int_"), StateFilter("*"))
async def callback_set_interval(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    val = int(callback_query.data.split("_")[2])
    ensure_user(user_id)
    db_users[user_id]["interval"] = val
    save_db()
    lang = db_users[user_id].get("lang", "uz") or "uz"
    if val >= 60:
        hours = val / 60
        unit = "soat" if lang == "uz" else ("час" if lang == "ru" else "hours")
        interval_text = f"{int(hours) if hours.is_integer() else hours} {unit}"
    else:
        unit = "daqiqa" if lang == "uz" else ("минут" if lang == "ru" else "minutes")
        interval_text = f"{val} {unit}"
    await callback_query.answer(f"✓ Interval {interval_text} ga sozlandi!", show_alert=True)
    text = "⏱️ <b>Habar oraligi</b>\n\n" + f"Joriy interval: <b>{interval_text}</b>\n\n" + "Kerakli vaqtni tanlang:"
    try:
        await callback_query.message.edit_text(text, reply_markup=get_interval_keyboard(val), parse_mode="HTML")
    except Exception:
        pass

@router.callback_query(F.data == "explain_interval", StateFilter("*"))
async def callback_explain_interval(callback_query: types.CallbackQuery):
    explanation = (
        "⁉️ <b>Interval nima va u nega kerak?</b>\n\n"
        "<b>Interval</b> — bu siz ulatgan profilingiz barcha tanlangan guruhlarga reklama xabaringizni yuborib bo'lgandan so'ng, keyingi sikl boshlanguncha **qancha vaqt kutishini** belgilaydi.\n\n"
        "💡 <i>Tavsiya: Telegram spam-filtrlaridan (Spam-blok) saqlanish uchun intervalni kamida 10-15 daqiqa qilib belgilash tavsiya etiladi.</i>"
    )
    await callback_query.message.answer(explanation, parse_mode="HTML")
    await callback_query.answer()

@router.callback_query(F.data == "custom_interval", StateFilter("*"))
async def callback_custom_interval(callback_query: types.CallbackQuery, state: FSMContext):
    await state.clear()
    user_id = callback_query.from_user.id
    await state.set_state(TextStates.waiting_custom_interval)
    await callback_query.message.answer("✍️ <b>Xabar yuborish oralig'ini (Intervalni) daqiqalarda kiriting (masalan: 20):</b>", parse_mode="HTML")
    await callback_query.answer()

@router.message(StateFilter(TextStates.waiting_custom_interval))
async def message_receive_custom_interval(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    ensure_user(user_id)
    try:
        val = int(message.text.strip())
        if val < 1:
            await message.answer("❌ Minimal interval vaqti - 1 daqiqa!")
            return
        db_users[user_id]["interval"] = val
        save_db()
        await message.answer(f"✅ <b>Interval {val} daqiqaga sozlandi!</b>", reply_markup=get_main_keyboard(user_id), parse_mode="HTML")
        await state.clear()
        await menu_interval(message, state)
    except ValueError:
        await message.answer("❌ Iltimos, faqat butun son kiriting (masalan: 25):")

@router.callback_query(F.data == "deposit_balance", StateFilter("*"))
async def callback_deposit_balance(callback_query: types.CallbackQuery, state: FSMContext):
    await state.clear()
    user_id = callback_query.from_user.id
    ensure_user(user_id)
    lang = db_users[user_id].get("lang", "uz") or "uz"
    deposit_template = LOCALIZATION[lang]["deposit_title"] if lang in ["uz", "ru"] else LOCALIZATION["en"]["deposit_title"]
    deposit_text = deposit_template.format(user_id=user_id, balans=f"{db_users[user_id].get('balans', 0):,}")
    btn_admin = "✍️ Administratorga yozish" if lang == "uz" else ("✍️ Написать администратору" if lang == "ru" else "✍️ Contact administrator")
    btn_back = "← Kabinetga qaytish" if lang == "uz" else ("← Назад в кабинет" if lang == "ru" else "← Back to cabinet")
    kb = [
        [InlineKeyboardButton(text=btn_admin, url="https://t.me/AbduIIayev_7")],
        [InlineKeyboardButton(text=btn_back, callback_data="back_to_kabinet")]
    ]
    await callback_query.message.edit_text(deposit_text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="HTML")
    await callback_query.answer()

@router.callback_query(F.data == "back_to_kabinet", StateFilter("*"))
async def callback_back_to_kabinet(callback_query: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await menu_kabinet_msg(callback_query.message, callback_query.from_user.id)
    await callback_query.answer()

@router.callback_query(F.data == "back_to_panel", StateFilter("*"))
async def callback_back_panel(callback_query: types.CallbackQuery, state: FSMContext):
    user_id = callback_query.from_user.id
    ensure_user(user_id)
    responseText, _ = await generate_control_panel_text(user_id)
    inline_kb = await get_control_panel_markup(user_id)
    try:
        await callback_query.message.edit_text(responseText, reply_markup=inline_kb, parse_mode="HTML")
    except Exception:
        pass
    await callback_query.answer()


# ================= SOXTA WEB SERVER (PORT BINDING UCHUN) =================

async def handle_ping(request):
    return web.Response(text="Bot is running smoothly!")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', handle_ping)
    app.router.add_get('/ping', handle_ping)
    port = int(os.environ.get("PORT", 7860))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logging.info(f"[Web] Port {port}-da muvaffaqiyatli ishga tushirildi!")

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
                    logging.info(f"[Sessiya] Mavjud seans qayta tiklandi: {target_phone} (ID: {user_id})")
            except Exception as e:
                logging.error(f"[Sessiya] Sessiya yuklashda xatolik ({file}): {e}")

# ================= SENDER ENGINE (REAL VAQT INTERVALLI & ROTATSIYA) =================

async def send_reklama_message(client, chat_id, user_data, user_id):
    is_pro = user_data.get("is_pro", False)
    bot_link = "https://t.me/Auto_Xabar_Yuborish_Bot"
    # Watermark faqat Free foydalanuvchilar uchun (Pro'da butunlay yo'q)
    watermark = '' if is_pro else f'\n\n<a href="{bot_link}">bu bot orqali yuborildi</a>'

    # Multi-Post: === orqali ajratilgan xabarlardan birini tasodifiy tanlash
    text_field = user_data.get("reklama_matni", "")
    parts = text_field.split("===")
    selected_raw_text = random.choice(parts).strip()
    final_text_part = apply_spintax(selected_raw_text)
    final_text = f"{final_text_part}{watermark}"

    sent_message_obj = None

    if is_pro and user_data.get("is_forward_mode") and user_data.get("forward_msg_id") and user_data.get("forward_chat_id"):
        try:
            f_msgs = await client.forward_messages(chat_id, user_data.get("forward_msg_id"), user_data.get("forward_chat_id"))
            if f_msgs:
                sent_message_obj = f_msgs[0] if isinstance(f_msgs, list) else f_msgs
            # Forward rejimida alohida watermark xabari faqat Free uchun (Pro'da yo'q)
            if not is_pro:
                await client.send_message(chat_id, watermark, parse_mode="html")
        except Exception as e:
            logging.error(f"[Forward] Postni uzatishda xatolik: {e}")
            sent_message_obj = await client.send_message(chat_id, final_text, parse_mode="html")
    else:
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
                sent_message_obj = await client.send_message(chat_id, final_text, file=photo_path, buttons=telethon_buttons, parse_mode="html")
            except Exception as e:
                logging.warning(f"[Sender] Rasm bilan yuborishda muammo: {e}. Faqat matn yuborilmoqda.")
                sent_message_obj = await client.send_message(chat_id, final_text, buttons=telethon_buttons, parse_mode="html")
        else:
            sent_message_obj = await client.send_message(chat_id, final_text, buttons=telethon_buttons, parse_mode="html")

    # Avtomatik o'chirish taymeri jurnali
    if sent_message_obj and user_data.get("delete_after_hours"):
        log_entry = {
            "chat_id": int(chat_id),
            "msg_id": int(sent_message_obj.id),
            "sent_at": datetime.now().timestamp(),
            "phone_used": getattr(client, 'phone', None) or user_data.get("active_phone")
        }
        if "sent_messages_log" not in db_users[user_id]:
            db_users[user_id]["sent_messages_log"] = []
        db_users[user_id]["sent_messages_log"].append(log_entry)
        save_db()

async def auto_sender_worker():
    """Tizim asinxron motori va xabarlarni o'chiruvchi background task"""
    while True:
        current_time = datetime.now().timestamp()
        today_str = datetime.now().strftime("%Y-%m-%d")

        for user_id, user_data in list(db_users.items()):
            # today_sent ni har kuni avtomatik nolga tushirish
            if user_data.get("today_sent_date") != today_str:
                user_data["today_sent"] = 0
                user_data["today_sent_date"] = today_str
                save_db()

            # AVTO-O'CHIRISH TAYMERINI TEKSHIRISH
            started_at = user_data.get("is_sending_started_at", 0)
            auto_off_hours = user_data.get("auto_off_hours")
            if auto_off_hours and started_at:
                elapsed_hours = (current_time - started_at) / 3600.0
                if elapsed_hours >= auto_off_hours:
                    user_data["is_sending"] = False
                    user_data["is_sending_started_at"] = 0
                    save_db()
                    logging.info(f"[Timer] Foydalanuvchi {user_id} uchun avto-o'chirish taymeri ishladi.")
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

            # YUBORILGAN XABARLARNI AVTOMATIK O'CHIRISH
            del_timer_hours = user_data.get("delete_after_hours")
            msg_logs = user_data.get("sent_messages_log", [])
            if del_timer_hours and msg_logs:
                remaining_logs = []
                for entry in msg_logs:
                    sent_age_hours = (current_time - entry["sent_at"]) / 3600.0
                    if sent_age_hours >= del_timer_hours:
                        try:
                            phone_used = entry.get("phone_used")
                            if phone_used:
                                client = await get_client(user_id, phone_used)
                                if await client.is_user_authorized():
                                    await client.delete_messages(entry["chat_id"], entry["msg_id"])
                                    logging.info(f"[Auto-Delete] Guruh {entry['chat_id']} dagi xabar {entry['msg_id']} o'chirildi.")
                        except Exception as e:
                            logging.error(f"[Auto-Delete] Xabarni o'chirishda xatolik: {e}")
                    else:
                        remaining_logs.append(entry)
                if len(remaining_logs) != len(msg_logs):
                    user_data["sent_messages_log"] = remaining_logs
                    save_db()

            # SIKLNI YOQISH
            if user_data.get("is_sending"):
                next_run = user_data.get("next_run_timestamp", 0)
                if current_time >= next_run:
                    interval_minutes = user_data.get("interval", 15)
                    user_data["next_run_timestamp"] = current_time + (interval_minutes * 60)
                    save_db()
                    asyncio.create_task(run_sending_cycle_for_user(user_id))

        await asyncio.sleep(10)

async def run_sending_cycle_for_user(user_id):
    """Guruhlarga xabarlarni aylanma akkauntlar rotatsiyasi va qora ro'yxatni tekshirib yuboruvchi drayver"""
    # Per-user lock: bir vaqtda faqat bitta sikl ishlaydi (重叠 oldini oladi)
    lock = get_user_send_lock(user_id)
    if lock.locked():
        logging.info(f"[Sender] User {user_id} sikli allaqachon ishlayapti, o'tkazib yuboriladi.")
        return
    async with lock:
        user_data = db_users.get(user_id)
        if not user_data or not user_data.get("is_sending"):
            return
        try:
            accounts_list = user_data.get("accounts", [])
            if not accounts_list:
                logging.warning(f"[Rotatsiya] Foydalanuvchi {user_id} da ulangan akkauntlar topilmadi.")
                return

            # active_phone bo'lmasa, birinchi akkauntdan foydalanamiz (groups_choice="all" crashini tuzatish)
            active_phone = user_data.get("active_phone")
            if not active_phone:
                active_phone = accounts_list[0]["phone"]
                user_data["active_phone"] = active_phone
                user_data["active_name"] = accounts_list[0].get("name", "Foydalanuvchi")
                user_data["active_username"] = accounts_list[0].get("username", "@-")
                save_db()

            guruhlar = []
            choice = user_data.get("groups_choice", "custom")
            blacklist = user_data.get("blacklist", [])
            rot_idx = user_data.get("rotation_index", 0)
            now_ts = time.time()

            if choice == "custom":
                raw_guruhlar = [int(x) for x in user_data.get("selected_groups", [])]
                guruhlar = [g for g in raw_guruhlar if g not in blacklist]
            else:
                temp_client = await get_client(user_id, active_phone)
                async for dialog in temp_client.iter_dialogs():
                    if dialog.is_group and int(dialog.id) not in blacklist:
                        guruhlar.append(int(dialog.id))

            if not guruhlar:
                logging.warning(f"[Sender] Foydalanuvchi {user_id} uchun yuborishga guruh qolmadi.")
                return

            logging.info(f"[Sender] Foydalanuvchi {user_id} uchun {len(guruhlar)} ta guruhga tarqatish tsikli boshlandi...")

            for g_id in guruhlar:
                if not db_users.get(user_id, {}).get("is_sending"):
                    break

                # 🔄 AKKAUNTLAR ROTATSIYASI (cooldown'dagi akkauntlarni o'tkazib yuboramiz)
                attempts = 0
                target_acc = None
                rot_phone = None
                while attempts < len(accounts_list):
                    candidate = accounts_list[rot_idx % len(accounts_list)]
                    cand_phone = candidate["phone"]
                    cooldown_until = _account_cooldowns.get(cand_phone, 0)
                    if cooldown_until > now_ts:
                        rot_idx += 1
                        attempts += 1
                        continue
                    target_acc = candidate
                    rot_phone = cand_phone
                    break
                if not rot_phone:
                    # Hammasi cooldown'da — keyingi tsiklgacha kutamiz
                    logging.warning(f"[Sender] User {user_id}: barcha akkauntlar FloodWait cooldown'da.")
                    break

                try:
                    client = await get_client(user_id, rot_phone)
                    client.phone = rot_phone
                    if await client.is_user_authorized():
                        await send_reklama_message(client, g_id, user_data, user_id)
                        user_data["today_sent"] = user_data.get("today_sent", 0) + 1
                        user_data["total_sent"] = user_data.get("total_sent", 0) + 1
                        save_db()
                        logging.info(f"[Sender] {user_id} ({rot_phone}) -> Guruh {g_id} ga xabar ketdi.")
                        rot_idx += 1
                        user_data["rotation_index"] = rot_idx
                        save_db()
                        delay_time = random.randint(12, 25)
                        await asyncio.sleep(delay_time)
                except errors.FloodWaitError as e:
                    logging.warning(f"[FloodWait] {rot_phone} uchun {e.seconds} soniya cheklovi! Cooldown'ga olindi.")
                    _account_cooldowns[rot_phone] = time.time() + e.seconds
                    rot_idx += 1
                    user_data["rotation_index"] = rot_idx
                    save_db()
                    await asyncio.sleep(5)
                except Exception as e:
                    logging.error(f"[Sender] Xabar yuborishda xato (Guruh: {g_id}): {e}")
                    rot_idx += 1
                    user_data["rotation_index"] = rot_idx
                    save_db()
                    continue
        except Exception as e:
            logging.error(f"[Sender] Asinxron xatolik user {user_id}: {str(e)}")

# ================= LOGIN WIZARD & DISCONNECT =================

@router.callback_query(F.data == "add_account", StateFilter("*"))
async def callback_add_account_wizard(callback_query: types.CallbackQuery, state: FSMContext):
    await state.clear()
    user_id = callback_query.from_user.id
    ensure_user(user_id)
    user_data = db_users.get(user_id)
    accounts_list = user_data.get("accounts", [])
    is_pro = user_data.get("is_pro", False)
    limit = 5 if is_pro else 1
    if len(accounts_list) >= limit:
        msg = get_text(user_id, "acc_limit_pro") if is_pro else get_text(user_id, "acc_limit_free")
        await callback_query.message.answer(msg, parse_mode="HTML")
        await callback_query.answer()
        return
    await callback_query.message.answer(get_text(user_id, "enter_phone"), parse_mode="HTML")
    await state.set_state(LoginStates.waiting_phone)
    await callback_query.answer()

@router.message(StateFilter(LoginStates.waiting_phone))
async def state_phone_received(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    lang = db_users[user_id].get("lang", "uz") or "uz"
    phone = message.text.strip().replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    if not phone.startswith("+"):
        phone = "+" + phone
    phone_digits = "".join(filter(str.isdigit, phone))
    if len(phone_digits) < 7 or len(phone_digits) > 18:
        await message.answer(get_text(user_id, "invalid_phone"), parse_mode="HTML")
        return
    await state.update_data(phone=phone)
    await message.answer(get_text(user_id, "connecting_tg"), parse_mode="HTML")
    try:
        client = await get_client(user_id, phone)
        send_code_result = await client.send_code_request(phone)
        await state.update_data(phone_code_hash=send_code_result.phone_code_hash)
        await state.set_state(LoginStates.waiting_code)
        await message.answer(get_text(user_id, "sms_sent"), parse_mode="HTML")
    except Exception as e:
        await message.answer(get_text(user_id, "conn_error").format(error=str(e)))
        await state.clear()

@router.message(StateFilter(LoginStates.waiting_code))
async def state_code_received(message: types.Message, state: FSMContext):
    # Eski "nuqta" formatini ham qabul qilamiz (moslik uchun), lekin endi talab qilinmaydi
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
        await force_flush_db()  # Muhim operatsiya - darhol bulutga yozamiz
        await message.answer(get_text(user_id, "acc_bound"), reply_markup=get_main_keyboard(user_id), parse_mode="HTML")
        await state.clear()
    except errors.PhoneCodeExpiredError:
        await message.answer(get_text(user_id, "sms_expired"), parse_mode="HTML")
        await state.clear()
    except errors.PhoneCodeInvalidError:
        await message.answer(get_text(user_id, "sms_invalid"), parse_mode="HTML")
    except errors.SessionPasswordNeededError:
        await state.set_state(LoginStates.waiting_2fa)
        await message.answer(get_text(user_id, "two_fa_required"), parse_mode="HTML")
    except Exception as e:
        await message.answer(get_text(user_id, "conn_error").format(error=str(e)))

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
        await force_flush_db()
        await message.answer(get_text(user_id, "acc_bound"), reply_markup=get_main_keyboard(user_id), parse_mode="HTML")
        await state.clear()
    except errors.PasswordHashInvalidError:
        await message.answer(get_text(user_id, "two_fa_invalid"), parse_mode="HTML")
    except Exception as e:
        await message.answer(get_text(user_id, "conn_error").format(error=str(e)))

@router.callback_query(F.data == "disconnect_profile", StateFilter("*"))
async def callback_disconnect(callback_query: types.CallbackQuery, state: FSMContext):
    await state.clear()
    user_id = callback_query.from_user.id
    ensure_user(user_id)
    phone = db_users[user_id].get("active_phone")
    if not phone:
        await callback_query.answer(get_text(user_id, "no_active_conn"), show_alert=True)
        return
    db_users[user_id]["active_phone"] = None
    db_users[user_id]["is_sending"] = False
    db_users[user_id]["is_sending_started_at"] = 0
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
    await callback_query.answer(get_text(user_id, "disconnected_success"), show_alert=True)
    await menu_kabinet_msg(callback_query.message, user_id)

# ================= MAIN MOTOR =================

async def main():
    global bot
    print("==================================================")
    print("🤖 AutoHabar Pro Telegram Bot (v2) ishga tushmoqda...")
    print("==================================================")
    print(f"[Tizim] Bot tokeni: {BOT_TOKEN[:15]}...")
    print(f"[Tizim] Admin ID: {ADMIN_ID}")
    print(f"[Tizim] Firebase ulangan: {'HA' if db else 'YOQ (faqat mahalliy baza)'}")

    if db:
        print("[Sessiya] Bulutdan eski ulanishlarni tiklash boshlandi...")
        await restore_sessions_from_cloud()

    bot = Bot(token=BOT_TOKEN)

    # Global majburiy obuna nazoratchisini dispatcherga ulash
    dp.message.outer_middleware(MandatorySubMiddleware())
    dp.callback_query.outer_middleware(MandatorySubMiddleware())

    asyncio.create_task(init_existing_sessions())
    asyncio.create_task(auto_sender_worker())
    asyncio.create_task(cloud_flush_worker())
    logging.info("[Tizim] Auto-sender va bulut sinxronizatsiya xizmatlari yoqildi!")

    asyncio.create_task(start_web_server())

    print("\n✅ BOT MUVAFFAQIYATLI ISHGA TUSHDI!")
    print("💬 Telegram ilovangizni oching va botingizga /start yuboring.")
    print("\n==================================================")

    max_retries = 10
    for attempt in range(1, max_retries + 1):
        try:
            logging.info(f"[Tarmoq] Telegramga ulanishga urinish {attempt}/{max_retries}...")
            await dp.start_polling(bot)
            break
        except Exception as e:
            logging.error(f"[Tarmoq] Ulanish uzildi: {e}")
            if attempt == max_retries:
                raise e
            wait_time = attempt * 5
            logging.info(f"[Tarmoq] {wait_time} soniyadan so'ng qayta urinish...")
            await asyncio.sleep(wait_time)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[👋] Avtomatlashtirish jarayoni foydalanuvchi tomonidan to'xtatildi.")
        sys.exit()

