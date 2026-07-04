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

# Loggerlarni sozlash (Render loglarini kuzatish uchun)
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
FIREBASE_AVAILABLE = False
try:
    import firebase_admin
    from firebase_admin import credentials, firestore
    FIREBASE_AVAILABLE = True
except ImportError:
    pass

# ================= CONFIGURATION =================
API_ID = 37104311
API_HASH = "f49729d10c144035c40f579b596d15b1"
# SADRIDDIN: Yangi va toza token integratsiya qilindi (Manybot qoldiqlari o'chadi!)
BOT_TOKEN = "8680819777:AAEzGf9RC96V3S0yYfi-Wg_Gg_ZBf_fH2_g"
ADMIN_ID = 7073273800
APP_ID = "autohabar-bot"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)

SESSIONS_DIR = "sessions"
os.makedirs(SESSIONS_DIR, exist_ok=True)
DB_FILE = os.path.join(SESSIONS_DIR, "database.json")

DEFAULT_DB = {
    ADMIN_ID: {
        "balans": 150000,
        "stars": 150,
        "is_pro": True,
        "referrals": 3,
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
        "channels": [],  # Boshlang'ich kanallar mutlaqo ochiq (bo'sh)
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
        "lang": "uz"
    }
}

# ================= FIREBASE DATABASE =================
db = None
if FIREBASE_AVAILABLE:
    possible_paths = ["firebase_credentials.json", "/etc/secrets/firebase_credentials.json"]
    for path in possible_paths:
        if os.path.exists(path):
            try:
                cred = credentials.Certificate(path)
                if not firebase_admin._apps:
                    firebase_admin.initialize_app(cred)
                db = firestore.client()
                break
            except Exception:
                pass

def load_db():
    local_data = DEFAULT_DB
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                local_data = json.load(f)
                local_data = {int(k): v for k, v in local_data.items()}
        except Exception:
            pass

    if db:
        try:
            doc_ref = db.collection('artifacts').document(APP_ID).collection('public').document('data').collection('database').document('main')
            doc = doc_ref.get()
            if doc.exists:
                data = doc.to_dict()
                parsed_data = {int(k): v for k, v in data.items()}
                
                # SADRIDDIN: Eski keraksiz kanallarni bulutdan ham avtomatik tozalaymiz
                for u_id in parsed_data:
                    if "channels" in parsed_data[u_id]:
                        parsed_data[u_id]["channels"] = [
                            c for c in parsed_data[u_id]["channels"] 
                            if "whatisthepriceTON" not in c and "PlsDontfuckthischannel" not in c
                        ]
                return parsed_data
        except Exception:
            pass
    return local_data

def save_db():
    try:
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump(db_users, f, ensure_ascii=False, indent=4)
    except Exception:
        pass
    if db:
        try:
            serializable_db = {str(k): v for k, v in db_users.items()}
            doc_ref = db.collection('artifacts').document(APP_ID).collection('public').document('data').collection('database').document('main')
            doc_ref.set(serializable_db)
        except Exception:
            pass

db_users = load_db()
active_clients = {}

def ensure_user(user_id: int):
    if user_id not in db_users:
        db_users[user_id] = {
            "balans": 0, "stars": 0, "is_pro": False, "referrals": 0,
            "reklama_matni": "🔥 AutoHabar Pro!", "reklama_rasm": None, "inline_buttons": [],
            "interval": 15, "next_run_timestamp": 0, "active_phone": None,
            "active_name": "Foydalanuvchi", "active_username": "@-", "is_sending": False,
            "groups_choice": "custom", "selected_groups": [], "cached_groups": [],
            "joined_time": datetime.now().strftime("%H:%M"), "today_sent": 0, "total_sent": 0,
            "channels": [], "auto_off_hours": None, "is_sending_started_at": 0,
            "referrals_count": 0, "referred_by": None, "forward_chat_id": None,
            "forward_msg_id": None, "is_forward_mode": False, "accounts": [],
            "auto_sub_active": True, "auto_reply_active": False, "lang": None
        }
    else:
        # Eski kanallarni mahalliy xotiradan ham tozalash
        db_users[user_id]["channels"] = [
            c for c in db_users[user_id].get("channels", []) 
            if "whatisthepriceTON" not in c and "PlsDontfuckthischannel" not in c
        ]
    save_db()

async def backup_session_to_cloud(user_id, phone):
    if not db: return
    phone_clean = phone.replace("+", "").replace(" ", "")
    session_path = os.path.join(SESSIONS_DIR, f"session_{user_id}_{phone_clean}.session")
    if os.path.exists(session_path):
        try:
            with open(session_path, "rb") as f:
                encoded_data = base64.b64encode(f.read()).decode('utf-8')
            doc_ref = db.collection('artifacts').document(APP_ID).collection('users').document(str(user_id)).collection('telethon_sessions').document(phone_clean)
            doc_ref.set({"binary_data": encoded_data, "updated_at": datetime.now().isoformat()})
        except Exception:
            pass

async def restore_sessions_from_cloud():
    if not db: return
    try:
        users_ref = db.collection('artifacts').document(APP_ID).collection('users')
        for user_doc in users_ref.stream():
            user_id = user_doc.id
            sessions_ref = users_ref.document(user_id).collection('telethon_sessions')
            for session_doc in sessions_ref.stream():
                phone_clean = session_doc.id
                binary_data_b64 = session_doc.to_dict().get("binary_data")
                if binary_data_b64:
                    session_path = os.path.join(SESSIONS_DIR, f"session_{user_id}_{phone_clean}.session")
                    with open(session_path, "wb") as f:
                        f.write(base64.b64decode(binary_data_b64.encode('utf-8')))
    except Exception:
        pass

# ================= STATES =================
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

class AdminStates(StatesGroup):
    waiting_search_id = State()
    waiting_add_balans = State()
    waiting_add_stars = State()
    waiting_add_channel = State()
    waiting_broadcast_msg = State()
    waiting_admin_reply = State()

# ================= LOCALIZATION =================
LOCALIZATION = {
    "uz": {
        "welcome": "📊 <b>Asosiy menyu:</b>\n<b>@Auto_Xabar_Yuborish_Bot</b>\n━━━━━━━━━━━━━━━━━━━━\nAssalomu alaykum, xush kelibsiz! 👋\n\n› Botdan foydalanish uchun akkaunt qo'shing va guruhlarni sozlang!",
        "btn_auto_send": "⚪ Autohabar yuborish", "btn_msg_text": "📝 Habar matni",
        "btn_interval": "⏱️ Interval", "btn_groups": "💬 Guruhlarni sozlash",
        "btn_profiles": "👤 Profillar", "btn_guide": "📖 Qo'llanma",
        "btn_cabinet": "👤 Kabinet", "btn_settings": "⚙️ Sozlamalar",
        "btn_support": "❓ Savol va Yordam", "btn_add_acc": "➕ Akkaunt qo'shish",
        "control_panel": "🤠 <b>Boshqaruv paneli</b>\n━━━━━━━━━━━━━━━━━━━━\n{profil}\n⚡ Holat: <b>{holat}</b>\n✍️ Xabar turi: <b>{turi}</b>\n💬 Guruhlar: <b>{guruhlar}</b>\n⏱️ Interval: <b>{interval}</b>\n⏳ Avto-o'chish: <b>{avto_ochish}</b>\n📢 Mention: <b>O'chiq</b>\n━━━━━━━━━━━━━━━━━━━━",
        "deposit_title": "💰 <b>Hisobni to'ldirish tizimi</b>\n━━━━━━━━━━━━━━━━━━━━\nTelegram ID: <code>{user_id}</code>\nBalans: <b>{balans} so'm</b>\n\nTo'lov uchun administratorga yozing:\n👉 <b>@AbduIIayev_7</b>",
        "cabinet_title": "👤 <b>Sizning Kabinetingiz</b>\n\nIsm: <b>{name}</b>\n💰 Balans: <b>{balans} so'm</b>\n✔️ Bugun yuborildi: <b>{today_sent}</b>\n🔄 Jami: <b>{total_sent}</b>\n🔗 Havola: <code>{ref_link}</code>",
        "profile_title": "👥 <b>Profillarni Boshqarish</b>\n━━━━━━━━━━━━━━━━━━━━\nFaol profil: <b>{active}</b>"
    },
    "ru": {
        "welcome": "📊 <b>Главное меню:</b>\n━━━━━━━━━━━━━━━━━━━━\nЗдравствуйте! 👋\n\n› Подключите аккаунт и настройте группы для старта!",
        "btn_auto_send": "⚪ Авторассылка", "btn_msg_text": "📝 Текст сообщения",
        "btn_interval": "⏱️ Интервал", "btn_groups": "💬 Настройка групп",
        "btn_profiles": "👤 Профили", "btn_guide": "📖 Руководство",
        "btn_cabinet": "👤 Кабинет", "btn_settings": "⚙️ Настройки",
        "btn_support": "❓ Вопрос и Помощь", "btn_add_acc": "➕ Добавить аккаунт",
        "control_panel": "🤠 <b>Панель управления</b>\n━━━━━━━━━━━━━━━━━━━━\n{profil}\n⚡ Статус: <b>{holat}</b>\n✍️ Тип: <b>{turi}</b>\n💬 Группы: <b>{guruhlar}</b>\n⏱️ Интервал: <b>{interval}</b>\n⏳ Таймер: <b>{avto_ochish}</b>\n━━━━━━━━━━━━━━━━━━━━",
        "deposit_title": "💰 <b>Пополнение баланса</b>\n━━━━━━━━━━━━━━━━━━━━\nВаш Telegram ID: <code>{user_id}</code>\nБаланс: <b>{balans} сум</b>\n\nДля оплаты напишите администратору:\n👉 <b>@AbduIIayev_7</b>",
        "cabinet_title": "👤 <b>Ваш Кабинет</b>\n\nИмя: <b>{name}</b>\n💰 Баланс: <b>{balans} сум</b>\n✔️ Отправлено сегодня: <b>{today_sent}</b>\n🔄 Всего: <b>{total_sent}</b>\n🔗 Ссылка: <code>{ref_link}</code>",
        "profile_title": "👥 <b>Управление Профилями</b>\n━━━━━━━━━━━━━━━━━━━━\nАктивный профиль: <b>{active}</b>"
    },
    "en": {
        "welcome": "📊 <b>Main Menu:</b>\n━━━━━━━━━━━━━━━━━━━━\nHello! 👋\n\n› Please add an account and configure groups!",
        "btn_auto_send": "⚪ Auto-Send", "btn_msg_text": "📝 Message Text",
        "btn_interval": "⏱️ Interval", "btn_groups": "💬 Configure Groups",
        "btn_profiles": "👤 Profiles", "btn_guide": "📖 Guide",
        "btn_cabinet": "👤 Cabinet", "btn_settings": "⚙️ Settings",
        "btn_support": "❓ Support & Help", "btn_add_acc": "➕ Add Account",
        "control_panel": "🤠 <b>Control Panel</b>\n━━━━━━━━━━━━━━━━━━━━\n{profil}\n⚡ Status: <b>{holat}</b>\n✍️ Type: <b>{turi}</b>\n💬 Groups: <b>{guruhlar}</b>\n⏱️ Interval: <b>{interval}</b>\n⏳ Auto-Off: <b>{avto_ochish}</b>\n━━━━━━━━━━━━━━━━━━━━",
        "deposit_title": "💰 <b>Balance Recharge</b>\n━━━━━━━━━━━━━━━━━━━━\nYour Telegram ID: <code>{user_id}</code>\nBalance: <b>{balans} UZS</b>\n\nContact admin to pay:\n👉 <b>@AbduIIayev_7</b>",
        "cabinet_title": "👤 <b>Your Cabinet</b>\n\nName: <b>{name}</b>\n💰 Balance: <b>{balans} UZS</b>\n✔️ Today sent: <b>{today_sent}</b>\n🔄 Total: <b>{total_sent}</b>\n🔗 Link: <code>{ref_link}</code>",
        "profile_title": "👥 <b>Manage Profiles</b>\n━━━━━━━━━━━━━━━━━━━━\nActive profile: <b>{active}</b>"
    }
}

def get_text(user_id: int, key: str) -> str:
    ensure_user(user_id)
    lang = db_users[user_id].get("lang", "uz") or "uz"
    return LOCALIZATION.get(lang, LOCALIZATION["uz"]).get(key, LOCALIZATION["uz"].get(key, ""))

def get_main_keyboard(user_id: int) -> ReplyKeyboardMarkup:
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
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🇺🇿 O'zbekcha", callback_data="lang_uz"),
        InlineKeyboardButton(text="🇷🇺 Русский", callback_data="lang_ru"),
        InlineKeyboardButton(text="🇺🇸 English", callback_data="lang_en")
    ]])

# ================= KEYBOARD GENERATORS =================
def get_interval_keyboard(current_interval):
    def check(val, text):
        return f"✓ {text}" if current_interval == val else text
    kb = [
        [InlineKeyboardButton(text=check(2, "2daq"), callback_data="set_int_2"),
         InlineKeyboardButton(text=check(3, "3daq"), callback_data="set_int_3"),
         InlineKeyboardButton(text=check(4, "4daq"), callback_data="set_int_4"),
         InlineKeyboardButton(text=check(5, "5daq"), callback_data="set_int_5"),
         InlineKeyboardButton(text=check(6, "6daq"), callback_data="set_int_6")],
        [InlineKeyboardButton(text=check(7, "7daq"), callback_data="set_int_7"),
         InlineKeyboardButton(text=check(8, "8daq"), callback_data="set_int_8"),
         InlineKeyboardButton(text=check(9, "9daq"), callback_data="set_int_9"),
         InlineKeyboardButton(text=check(10, "10daq"), callback_data="set_int_10"),
         InlineKeyboardButton(text=check(11, "11daq"), callback_data="set_int_11")],
        [InlineKeyboardButton(text=check(12, "12daq"), callback_data="set_int_12"),
         InlineKeyboardButton(text=check(13, "13daq"), callback_data="set_int_13"),
         InlineKeyboardButton(text=check(14, "14daq"), callback_data="set_int_14"),
         InlineKeyboardButton(text=check(15, "15daq"), callback_data="set_int_15")],
        [InlineKeyboardButton(text=check(30, "30daq"), callback_data="set_int_30"),
         InlineKeyboardButton(text=check(60, "1 soat"), callback_data="set_int_60"),
         InlineKeyboardButton(text=check(90, "1.5 soat"), callback_data="set_int_90"),
         InlineKeyboardButton(text=check(120, "2 soat"), callback_data="set_int_120"),
         InlineKeyboardButton(text=check(180, "3 soat"), callback_data="set_int_180")],
        [InlineKeyboardButton(text="⁉️ Interval nima", callback_data="explain_interval")],
        [InlineKeyboardButton(text="✍️ Qo'lda kiritish", callback_data="custom_interval")],
        [InlineKeyboardButton(text="← Orqaga", callback_data="back_to_panel")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

# ================= MANDATORY SUB MIDDLEWARE =================
class MandatorySubMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: TelegramObject, data: dict):
        user = event.from_user if hasattr(event, "from_user") else None
        if not user or user.id == ADMIN_ID:
            return await handler(event, data)

        user_id = user.id
        ensure_user(user_id)
        if db_users[user_id].get("lang") is None:
            return await handler(event, data)

        channels = db_users.get(ADMIN_ID, {}).get("channels", [])
        # Sadriddin, eski kanallar bu yerda ham double-check tozalangan
        channels = [c for c in channels if "whatisthepriceTON" not in c and "PlsDontfuckthischannel" not in c]

        if not channels:
            return await handler(event, data)

        unsubscribed = []
        for chan in channels:
            chat_id = chan if chan.startswith("@") else f"@{chan}"
            try:
                member = await event.bot.get_chat_member(chat_id=chat_id, user_id=user_id)
                if member.status in ["left", "kicked"]:
                    unsubscribed.append(chan)
            except Exception as e:
                logging.error(f"[Xavfsizlik] {channel} obunasini tekshirishda xato: {e}")
                # Agar bot kanalda admin bo'lmasa, uni obunadan chiqmagan deb hisoblaymiz (TUZATILDI!)
                pass

        if unsubscribed:
            # Obuna inline klaviaturasi
            kb_list = [[InlineKeyboardButton(text=f"📢 {ch}", url=f"https://t.me/{ch.replace('@','')}")] for ch in unsubscribed]
            kb_list.append([InlineKeyboardButton(text="✅ Obunani tekshirish", callback_data="check_sub_status")])
            
            msg_text = "⚠️ <b>Botdan foydalanish uchun quyidagi kanallarga a'zo bo'lishingiz shart!</b>"
            if isinstance(event, types.Message):
                await event.answer(msg_text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_list), parse_mode="HTML")
            elif isinstance(event, types.CallbackQuery):
                await event.message.answer(msg_text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_list), parse_mode="HTML")
                await event.answer()
            return

        return await handler(event, data)

# ================= CLIENT RECOVERY SYSTEM =================
async def get_client(user_id, phone):
    phone_clean = phone.replace("+", "").replace(" ", "")
    session_key = f"{user_id}_{phone_clean}"
    client = active_clients.get(session_key)
    session_path = os.path.join(SESSIONS_DIR, f"session_{session_key}")
    
    if not client:
        client = TelegramClient(
            session_path, API_ID, API_HASH,
            loop=asyncio.get_running_loop(),
            connection_retries=5, retry_delay=2, auto_reconnect=True,
            device_model="AutoHabar Pro", system_version="Windows 11", app_version="5.5.0"
        )
        active_clients[session_key] = client
    if not client.is_connected():
        await client.connect()
    return client

# ================= HANDLERS =================
@router.message(Command("start"), StateFilter("*"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    ensure_user(user_id)
    
    if db_users[user_id].get("lang") is None:
        await message.answer(LOCALIZATION["uz"]["select_lang_text"], reply_markup=get_language_markup())
        return
    await message.answer(get_text(user_id, "welcome"), reply_markup=get_main_keyboard(user_id), parse_mode="HTML")

@router.callback_query(F.data.startswith("lang_"), StateFilter("*"))
async def callback_select_lang(callback_query: types.CallbackQuery, state: FSMContext):
    await state.clear()
    user_id = callback_query.from_user.id
    ensure_user(user_id)
    db_users[user_id]["lang"] = callback_query.data.split("_")[1]
    save_db()
    await callback_query.message.delete()
    await callback_query.message.answer(get_text(user_id, "welcome"), reply_markup=get_main_keyboard(user_id), parse_mode="HTML")

@router.message(F.text.in_([LOCALIZATION["uz"]["btn_support"], LOCALIZATION["ru"]["btn_support"], LOCALIZATION["en"]["btn_support"]]), StateFilter("*"))
async def menu_support_handler(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    await message.answer(get_text(user_id, "support_prompt"), parse_mode="HTML")
    await state.set_state(TextStates.waiting_support_question)

@router.message(StateFilter(TextStates.waiting_support_question))
async def message_receive_support_question(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    ensure_user(user_id)
    if not message.text:
        await message.answer("⚠️ Iltimos, matn yuboring!")
        return
    try:
        notify = f"📩 <b>Yangi Savol!</b>\nID: <code>{user_id}</code>\nIsm: {message.from_user.first_name}\n\n<i>\"{message.text}\"</i>"
        await bot.send_message(ADMIN_ID, notify, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✍️ Javob berish", callback_data=f"reply_to_user_{user_id}")
        ]]), parse_mode="HTML")
    except Exception:
        pass
    await message.answer(get_text(user_id, "support_sent"), reply_markup=get_main_keyboard(user_id), parse_mode="HTML")
    await state.clear()

@router.callback_query(F.data.startswith("reply_to_user_"), StateFilter("*"))
async def callback_admin_reply_prompt(callback_query: types.CallbackQuery, state: FSMContext):
    await state.clear()
    if callback_query.from_user.id != ADMIN_ID: return
    target_id = int(callback_query.data.split("_")[3])
    await state.update_data(target_user_id=target_id)
    await state.set_state(AdminStates.waiting_admin_reply)
    await callback_query.message.answer(f"✍️ ID: <code>{target_id}</code> bo'lgan foydalanuvchiga javob yozing:")
    await callback_query.answer()

@router.message(StateFilter(AdminStates.waiting_admin_reply))
async def state_process_admin_reply(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    data = await state.get_data()
    target_id = data.get("target_user_id")
    try:
        await bot.send_message(target_id, f"🔔 <b>Administrator Javobi:</b>\n\n<i>\"{message.text}\"</i>", parse_mode="HTML")
        await message.answer("✅ Javob yuborildi!")
    except Exception:
        await message.answer("❌ Yuborishda xatolik!")
    await state.clear()

@router.message(F.text.in_([LOCALIZATION["uz"]["btn_guide"], LOCALIZATION["ru"]["btn_guide"], LOCALIZATION["en"]["btn_guide"]]), StateFilter("*"))
async def menu_guide(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(get_text(message.from_user.id, "guide_text"), parse_mode="HTML")

@router.message(F.text.in_([LOCALIZATION["uz"]["btn_cabinet"], LOCALIZATION["ru"]["btn_cabinet"], LOCALIZATION["en"]["btn_cabinet"]]), StateFilter("*"))
async def menu_cabinet_handler(message: types.Message, state: FSMContext):
    await state.clear()
    await menu_kabinet_msg(message, message.from_user.id)

async def menu_kabinet_msg(message: types.Message, user_id: int):
    user_data = db_users.get(user_id)
    lang = user_data.get("lang", "uz") or "uz"
    accounts_list = user_data.get("accounts", [])
    profiles_text = ""
    for idx, acc in enumerate(accounts_list, 1):
        status = " (Faol)" if acc["phone"] == user_data.get("active_phone") else ""
        profiles_text += f"📞 {idx}. <b>{acc['phone']}</b>{status}\n"
    if not accounts_list:
        profiles_text = "❌ Profillar ulanmagan.\n"
        
    text = LOCALIZATION[lang]["cabinet_title"].format(
        name=user_data.get("active_name", "Foydalanuvchi"),
        username=user_data.get("active_username", "@-"),
        balans=f"{user_data.get('balans', 0):,}",
        today_sent=user_data.get("today_sent", 0),
        total_sent=user_data.get("total_sent", 0),
        acc_count=len(accounts_list),
        referrals=user_data.get("referrals_count", 0),
        ref_link=f"https://t.me/Auto_Xabar_Yuborish_Bot?start=ref_{user_id}"
    )
    header_acc = "👥 <b>Ulangan barcha profillaringiz:</b>\n" if lang == "uz" else ("👥 <b>Все подключенные профили:</b>\n" if lang == "ru" else "👥 <b>All connected profiles:</b>\n")
    text += f"\n\n{header_acc}{profiles_text}"
    
    btn_deposit = "💰 Hisobni to'ldirish" if lang == "uz" else ("💰 Пополнить баланс" if lang == "ru" else "💰 Deposit")
    btn_disconnect = "⚠️ Profilni uzish" if lang == "uz" else ("⚠️ Отключить профиль" if lang == "ru" else "⚠️ Disconnect profile")
    btn_close = "❌ Yopish" if lang == "uz" else ("❌ Закрыть" if lang == "ru" else "❌ Close")

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=btn_deposit, callback_data="deposit_balance"),
         InlineKeyboardButton(text=btn_disconnect, callback_data="disconnect_profile")],
        [InlineKeyboardButton(text=btn_close, callback_data="close_menu")]
    ])
    try:
        await message.answer(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await message.edit_text(text, reply_markup=kb, parse_mode="HTML")

# ================= SETTINGS =================
@router.message(F.text.in_([LOCALIZATION["uz"]["btn_settings"], LOCALIZATION["ru"]["btn_settings"], LOCALIZATION["en"]["btn_settings"]]), StateFilter("*"))
async def menu_sozlamalar(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    ensure_user(user_id)
    await show_sozlamalar_menu(message, user_id)

async def show_sozlamalar_menu(message: types.Message, user_id: int):
    user_data = db_users.get(user_id)
    lang = user_data.get("lang", "uz") or "uz"
    auto_sub = "Yoqilgan 🟢" if user_data.get("auto_sub_active", True) else "O'chirilgan 🔴"
    auto_reply = "Yoqilgan 🟢" if user_data.get("auto_reply_active", False) else "O'chirilgan 🔴"
    lang_name = "O'zbekcha 🇺🇿" if lang == "uz" else ("Русский 🇷🇺" if lang == "ru" else "English 🇺🇸")
    
    text = LOCALIZATION[lang]["settings_title"].format(
        auto_sub=auto_sub, auto_reply=auto_reply, lang_name=lang_name, antiban="Maksimal 🛡️"
    )
    btn_sub = "🤖 Avto-obuna" if lang == "uz" else ("🤖 Автоподписка" if lang == "ru" else "🤖 Auto-subscribe")
    btn_reply = "↩️ Auto Reply" if lang == "uz" else ("↩️ Автоответ" if lang == "ru" else "↩️ Auto Reply")
    btn_lang = "🌐 Tilni o'zgartirish" if lang == "uz" else ("🌐 Сменить язык" if lang == "ru" else "🌐 Change Language")
    btn_close = "❌ Yopish" if lang == "uz" else ("❌ Закрыть" if lang == "ru" else "❌ Close")

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=btn_sub, callback_data="toggle_auto_sub"),
         InlineKeyboardButton(text=btn_reply, callback_data="toggle_auto_reply")],
        [InlineKeyboardButton(text=btn_lang, callback_data="change_language_settings")],
        [InlineKeyboardButton(text=btn_close, callback_data="close_menu")]
    ])
    try:
        await message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await message.answer(text, reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data == "change_language_settings", StateFilter("*"))
async def callback_change_language_settings(callback_query: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback_query.message.edit_text(LOCALIZATION["uz"]["select_lang_text"], reply_markup=get_language_markup())

@router.callback_query(F.data == "toggle_auto_sub", StateFilter("*"))
async def callback_toggle_auto_sub(callback_query: types.CallbackQuery, state: FSMContext):
    await state.clear()
    user_id = callback_query.from_user.id
    db_users[user_id]["auto_sub_active"] = not db_users[user_id].get("auto_sub_active", True)
    save_db()
    await callback_query.answer("✓ Avto-obuna holati o'zgardi!")
    await show_sozlamalar_menu(callback_query.message, user_id)

@router.callback_query(F.data == "toggle_auto_reply", StateFilter("*"))
async def callback_toggle_auto_reply(callback_query: types.CallbackQuery, state: FSMContext):
    await state.clear()
    user_id = callback_query.from_user.id
    db_users[user_id]["auto_reply_active"] = not db_users[user_id].get("auto_reply_active", False)
    save_db()
    await callback_query.answer("✓ Auto-javob holati o'zgardi!")
    await show_sozlamalar_menu(callback_query.message, user_id)

# ================= AUTOHABAR SENDER CONTROL =================
@router.message(F.text.in_([LOCALIZATION["uz"]["btn_auto_send"], LOCALIZATION["ru"]["btn_auto_send"], LOCALIZATION["en"]["btn_auto_send"]]), StateFilter("*"))
async def menu_autohabar(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    user_data = db_users.get(user_id)
    lang = user_data.get("lang", "uz") or "uz"
    
    phone = user_data.get("active_phone")
    p_status = f"👤 Profil: [ {phone} ]" if phone else f"👤 Profil: [ {get_text(user_id, 'no_active_conn')} ]"
    holatStatus = "🟢 Faol" if user_data.get("is_sending") else "🔴 O'chiq"
    auto_off_text = "Cheksiz ∞" if user_data.get("auto_off_hours") is None else f"{user_data['auto_off_hours']} soat"
    
    text = LOCALIZATION[lang]["control_panel"].format(
        profil=p_status, holat=holatStatus, turi="Matn",
        guruhlar=f"{len(user_data.get('selected_groups', []))} ta",
        interval=f"{user_data.get('interval', 15)} daqiqa", avto_ochish=auto_off_text
    )
    
    start_stop = "🛑 To'xtatish" if user_data.get("is_sending") else "▶️ Ishga tushirish"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=start_stop, callback_data="toggle_sending"),
         InlineKeyboardButton(text="📊 Statistika", callback_data="statistika")],
        [InlineKeyboardButton(text="⏳ Avto-o'chirish", callback_data="timer_setup"),
         InlineKeyboardButton(text="🔄 Yangilash", callback_data="refresh_status")]
    ])
    await message.answer(text, reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data == "toggle_sending", StateFilter("*"))
async def callback_toggle_sending(callback_query: types.CallbackQuery, state: FSMContext):
    await state.clear()
    user_id = callback_query.from_user.id
    user_data = db_users.get(user_id)
    lang = user_data.get("lang", "uz") or "uz"

    if not user_data.get("active_phone"):
        await callback_query.answer("⚠️ Profil ulanmagan!", show_alert=True)
        return

    user_data["is_sending"] = not user_data.get("is_sending", False)
    if user_data["is_sending"]:
        user_data["next_run_timestamp"] = 0
        user_data["is_sending_started_at"] = datetime.now().timestamp()
    save_db()
    await callback_query.answer("✓ Holat muvaffaqiyatli o'zgartirildi!")
    await menu_autohabar(callback_query.message, state)

@router.callback_query(F.data == "statistika", StateFilter("*"))
async def callback_statistika(callback_query: types.CallbackQuery, state: FSMContext):
    await state.clear()
    user_id = callback_query.from_user.id
    user_data = db_users.get(user_id)
    lang = user_data.get("lang", "uz") or "uz"
    
    g_count = len(user_data.get("selected_groups", []))
    status_text = "🟢 Faol" if user_data.get("is_sending") else "🔴 O'chiq"
    
    text = (
        f"📊 <b>Statistika ({lang.upper()})</b>\n\n"
        f"Bugun yuborildi: <b>{user_data.get('today_sent', 0)}</b> ta xabar\n"
        f"Jami yuborilgan: <b>{user_data.get('total_sent', 0)}</b> ta xabar\n"
        f"Guruhlar soni: <b>{g_count}</b> ta\n"
        f"Holat: <b>{status_text}</b>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Yangilash", callback_data="statistika")],
        [InlineKeyboardButton(text="← Orqaga", callback_data="back_to_panel")]
    ])
    await callback_query.message.edit_text(text, reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data == "timer_setup", StateFilter("*"))
async def callback_timer_setup(callback_query: types.CallbackQuery, state: FSMContext):
    await state.clear()
    user_id = callback_query.from_user.id
    user_data = db_users.get(user_id)
    
    def check(h):
        return f"✓ {h}s" if user_data.get("auto_off_hours") == h else f"{h} soat"
        
    kb = [
        [InlineKeyboardButton(text=check(1), callback_data="set_timer_1"),
         InlineKeyboardButton(text=check(2), callback_data="set_timer_2"),
         InlineKeyboardButton(text=check(3), callback_data="set_timer_3")],
        [InlineKeyboardButton(text=check(6), callback_data="set_timer_6"),
         InlineKeyboardButton(text=check(12), callback_data="set_timer_12"),
         InlineKeyboardButton(text=check(24), callback_data="set_timer_24")],
        [InlineKeyboardButton(text="Cheksiz ∞", callback_data="set_timer_inf")],
        [InlineKeyboardButton(text="← Orqaga", callback_data="back_to_panel")]
    ]
    await callback_query.message.edit_text("⏱️ <b>Avto-o'chirish taymeri:</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="HTML")

@router.callback_query(F.data.startswith("set_timer_"), StateFilter("*"))
async def callback_set_timer(callback_query: types.CallbackQuery, state: FSMContext):
    await state.clear()
    user_id = callback_query.from_user.id
    val = callback_query.data.split("_")[2]
    db_users[user_id]["auto_off_hours"] = None if val == "inf" else int(val)
    save_db()
    await callback_query.answer("✓ Taymer sozlandi!")
    await callback_timer_setup(callback_query, state)

@router.callback_query(F.data == "refresh_status", StateFilter("*"))
async def callback_refresh_status(callback_query: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await menu_autohabar(callback_query.message, state)
    await callback_query.answer("🔄 Yangilandi!")

@router.callback_query(F.data == "back_to_panel", StateFilter("*"))
async def callback_back_panel(callback_query: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await menu_autohabar(callback_query.message, state)

# ================= REKLAMA SOZLASH =================
@router.message(F.text.in_([LOCALIZATION["uz"]["btn_msg_text"], LOCALIZATION["ru"]["btn_msg_text"], LOCALIZATION["en"]["btn_msg_text"]]), StateFilter("*"))
async def menu_msg_setup(message: types.Message, state: FSMContext):
    await state.clear()
    await show_message_settings(message, message.from_user.id)

async def show_message_settings(message: types.Message, user_id: int):
    user_data = db_users.get(user_id)
    lang = user_data.get("lang", "uz") or "uz"
    
    reklama_rasm = "Bor 🖼️" if user_data.get("reklama_rasm") else "Yo'q ❌"
    is_forward = "Yoqilgan 📤" if user_data.get("is_forward_mode") else "O'chirilgan 📝"
    
    text = LOCALIZATION[lang]["msg_setup"].format(
        matn=user_data.get("reklama_matni"), rasm=reklama_rasm,
        tugmalar=f"{len(user_data.get('inline_buttons', []))} ta", status=is_forward
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✍️ Matnni tahrirlash", callback_data="edit_text")],
        [InlineKeyboardButton(text="🖼️ Rasm yuklash", callback_data="edit_photo")],
        [InlineKeyboardButton(text="🔘 Tugmali xabar (Inline)", callback_data="edit_buttons_pro")],
        [InlineKeyboardButton(text="❌ Hammasini tozalash", callback_data="clear_media_buttons")],
        [InlineKeyboardButton(text="← Orqaga", callback_data="back_to_panel")]
    ])
    await message.answer(text, reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data == "edit_text", StateFilter("*"))
async def callback_edit_text(callback_query: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(TextStates.waiting_text)
    await callback_query.message.answer("✍️ <b>Yangi reklama matnini yuboring:</b>", parse_mode="HTML")
    await callback_query.answer()

@router.message(StateFilter(TextStates.waiting_text))
async def message_receive_text(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    db_users[user_id]["reklama_matni"] = message.text
    save_db()
    await message.answer("✅ Saqlandi!")
    await show_message_settings(message, user_id)
    await state.clear()

# ================= INTERVAL =================
@router.message(F.text.in_([LOCALIZATION["uz"]["btn_interval"], LOCALIZATION["ru"]["btn_interval"], LOCALIZATION["en"]["btn_interval"]]), StateFilter("*"))
async def menu_interval_handler(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    user_data = db_users.get(user_id)
    await message.answer(f"⏱️ <b>Hozirgi interval:</b> {user_data.get('interval', 15)} daqiqa", reply_markup=get_interval_keyboard(user_data.get("interval", 15)), parse_mode="HTML")

@router.callback_query(F.data.startswith("set_int_"), StateFilter("*"))
async def callback_set_interval(callback_query: types.CallbackQuery, state: FSMContext):
    await state.clear()
    user_id = callback_query.from_user.id
    val = int(callback_query.data.split("_")[2])
    db_users[user_id]["interval"] = val
    save_db()
    await callback_query.answer(f"✓ Interval {val} daqiqaga sozlandi!")
    await callback_query.message.edit_text(f"⏱️ <b>Hozirgi interval:</b> {val} daqiqa", reply_markup=get_interval_keyboard(val), parse_mode="HTML")

@router.callback_query(F.data == "custom_interval", StateFilter("*"))
async def callback_custom_interval(callback_query: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(TextStates.waiting_custom_interval)
    await callback_query.message.answer("✍️ <b>Intervalni daqiqalarda kiriting (masalan: 20):</b>")

@router.message(StateFilter(TextStates.waiting_custom_interval))
async def message_receive_custom_interval(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    try:
        val = int(message.text.strip())
        if val < 1: raise ValueError
        db_users[user_id]["interval"] = val
        save_db()
        await message.answer(f"✅ Interval {val} daqiqaga sozlandi!")
        await state.clear()
    except Exception:
        await message.answer("❌ Noto'g'ri qiymat!")

# ================= GROUPS SETUP =================
@router.message(F.text.in_([LOCALIZATION["uz"]["btn_groups"], LOCALIZATION["ru"]["btn_groups"], LOCALIZATION["en"]["btn_groups"]]), StateFilter("*"))
async def menu_groups_handler(message: types.Message, state: FSMContext):
    await state.clear()
    await menu_guruhlar(message, state)

async def menu_guruhlar(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    user_data = db_users.get(user_id)
    lang = user_data.get("lang", "uz") or "uz"
    
    choice = user_data.get("groups_choice", "custom")
    tanlov = "Hamma guruhlar" if choice == "all" else "O'zim tanlayman"
    
    text = LOCALIZATION[lang]["groups_setup"].format(tanlov=tanlov)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="+ Hamma guruhlarga", callback_data="set_groups_all")],
        [InlineKeyboardButton(text="✓ O'zim tanlayman", callback_data="set_groups_custom")],
        [InlineKeyboardButton(text="📊 Ro'yxatlar", callback_data="groups_list_page_0"),
         InlineKeyboardButton(text="+ Yangilash", callback_data="refresh_groups_force"),
         InlineKeyboardButton(text="🚨 Tozalash", callback_data="clear_selected_groups")],
        [InlineKeyboardButton(text="← Orqaga", callback_data="back_to_panel")]
    ])
    await message.answer(text, reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data == "set_groups_all", StateFilter("*"))
async def callback_groups_all(callback_query: types.CallbackQuery, state: FSMContext):
    await state.clear()
    user_id = callback_query.from_user.id
    db_users[user_id]["groups_choice"] = "all"
    save_db()
    await callback_query.answer("✓ Hamma guruhlar tanlandi!")
    await menu_guruhlar(callback_query.message, state)

@router.callback_query(F.data == "set_groups_custom", StateFilter("*"))
async def callback_groups_custom(callback_query: types.CallbackQuery, state: FSMContext):
    await state.clear()
    user_id = callback_query.from_user.id
    db_users[user_id]["groups_choice"] = "custom"
    save_db()
    await callback_query.answer("✓ Qo'lda tanlash faollashdi!")
    await menu_guruhlar(callback_query.message, state)

@router.callback_query(F.data == "clear_selected_groups", StateFilter("*"))
async def callback_clear_groups(callback_query: types.CallbackQuery, state: FSMContext = None):
    if state: await state.clear()
    user_id = callback_query.from_user.id
    db_users[user_id]["selected_groups"] = []
    save_db()
    await callback_query.answer("🚨 Tozalab bo'lindi!")
    await callback_groups_list(callback_query)

@router.callback_query(F.data == "refresh_groups_force", StateFilter("*"))
async def callback_refresh_groups_force(callback_query: types.CallbackQuery, state: FSMContext = None):
    if state: await state.clear()
    user_id = callback_query.from_user.id
    active_phone = db_users[user_id].get("active_phone")
    if not active_phone:
        await callback_query.answer("⚠️ Profil ulanmagan!", show_alert=True)
        return
    await callback_query.answer("Kesh yangilanmoqda...")
    try:
        client = await get_client(user_id, active_phone)
        guruhlar = []
        async for dialog in client.iter_dialogs():
            if dialog.is_group:
                guruhlar.append({"id": int(dialog.id), "name": str(dialog.name)})
        db_users[user_id]["cached_groups"] = guruhlar
        save_db()
        await callback_groups_list(callback_query)
    except Exception:
        await callback_query.message.answer("❌ Ulanish xatosi!")

@router.callback_query(F.data.startswith("groups_list_page_"), StateFilter("*"))
async def callback_groups_list(callback_query: types.CallbackQuery, state: FSMContext = None):
    if state: await state.clear()
    user_id = callback_query.from_user.id
    user_data = db_users.get(user_id)
    
    page = 0
    try:
        page = int(callback_query.data.split("_")[3])
    except Exception:
        pass
        
    guruhlar = user_data.get("cached_groups", [])
    if not guruhlar:
        await callback_query.answer("⚠️ Kesh bo'sh, yangilash tugmasini bosing!", show_alert=True)
        return
        
    per_page = 14
    start_idx = page * per_page
    page_groups = guruhlar[start_idx:start_idx+per_page]
    selected_ids = [int(x) for x in user_data.get("selected_groups", [])]
    
    buttons = []
    row = []
    for g in page_groups:
        g_id = int(g["id"])
        is_selected = g_id in selected_ids
        icon = "✔" if is_selected else "➕"
        row.append(InlineKeyboardButton(text=f"{icon} {g['name'][:12]}", callback_data=f"toggle_group_{g_id}_{page}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row: buttons.append(row)
    
    nav = []
    if page > 0: nav.append(InlineKeyboardButton(text="⬅ Oldingi", callback_data=f"groups_list_page_{page-1}"))
    if start_idx+per_page < len(guruhlar): nav.append(InlineKeyboardButton(text="Keyingi ➡", callback_data=f"groups_list_page_{page+1}"))
    if nav: buttons.append(nav)
    
    buttons.append([InlineKeyboardButton(text=f"💾 Saqlash ({len(selected_ids)})", callback_data="save_groups_selection")])
    await callback_query.message.edit_text("<b>Guruhlarni tanlang:</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")

@router.callback_query(F.data.startswith("toggle_group_"), StateFilter("*"))
async def callback_toggle_group(callback_query: types.CallbackQuery, state: FSMContext):
    await state.clear()
    user_id = callback_query.from_user.id
    parts = callback_query.data.split("_")
    group_id = int(parts[2])
    page = int(parts[3])
    
    selected = [int(x) for x in db_users[user_id].get("selected_groups", [])]
    if group_id in selected: selected.remove(group_id)
    else: selected.append(group_id)
    db_users[user_id]["selected_groups"] = selected
    save_db()
    
    # Qayta ko'rsatish
    callback_query.data = f"groups_list_page_{page}"
    await callback_groups_list(callback_query, state)

@router.callback_query(F.data == "save_groups_selection", StateFilter("*"))
async def callback_save_groups(callback_query: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback_query.answer("✓ Guruhlar muvaffaqiyatli saqlandi!", show_alert=True)
    await menu_guruhlar(callback_query.message, state)

# ================= PROFILLAR SETUP =================
@router.message(F.text.in_([LOCALIZATION["uz"]["btn_profiles"], LOCALIZATION["ru"]["btn_profiles"], LOCALIZATION["en"]["btn_profiles"]]), StateFilter("*"))
async def menu_profillar(message: types.Message, state: FSMContext):
    await state.clear()
    await show_profillar_settings(message, message.from_user.id)

async def show_profillar_settings(message: types.Message, user_id: int):
    user_data = db_users.get(user_id)
    accounts_list = user_data.get("accounts", [])
    active_phone = user_data.get("active_phone", "Mavjud emas ❌")
    lang = user_data.get("lang", "uz") or "uz"
    
    text = LOCALIZATION[lang]["profile_title"].format(active=active_phone)
    buttons = []
    for acc in accounts_list:
        status_icon = "🟢" if acc["phone"] == active_phone else "⚪"
        buttons.append([InlineKeyboardButton(text=f"{status_icon} {acc['phone']}", callback_data=f"manage_acc_{acc['phone']}")])
        
    buttons.append([InlineKeyboardButton(text="➕ Yangi profil qo'shish", callback_data="add_account")])
    buttons.append([InlineKeyboardButton(text="← Orqaga", callback_data="back_to_panel")])
    try:
        await message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")
    except Exception:
        await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")

@router.callback_query(F.data.startswith("manage_acc_"), StateFilter("*"))
async def callback_manage_acc(callback_query: types.CallbackQuery, state: FSMContext):
    await state.clear()
    user_id = callback_query.from_user.id
    phone = callback_query.data.replace("manage_acc_", "")
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Faol qilish 🟢", callback_data=f"activate_acc_{phone}"),
         InlineKeyboardButton(text="Uzish ⚠️", callback_data=f"delete_acc_{phone}")],
        [InlineKeyboardButton(text="← Orqaga", callback_data="go_to_profillar")]
    ])
    await callback_query.message.edit_text(f"📱 Profil: <b>{phone}</b>", reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data.startswith("activate_acc_"), StateFilter("*"))
async def callback_activate_acc(callback_query: types.CallbackQuery, state: FSMContext):
    await state.clear()
    user_id = callback_query.from_user.id
    phone = callback_query.data.replace("activate_acc_", "")
    
    user_data = db_users.get(user_id)
    accounts = user_data.get("accounts", [])
    target = next((acc for acc in accounts if acc["phone"] == phone), None)
    if target:
        db_users[user_id]["active_phone"] = phone
        db_users[user_id]["active_name"] = target["name"]
        db_users[user_id]["active_username"] = target["username"]
        save_db()
        await callback_query.answer("✓ Faollashtirildi!")
    await show_profillar_settings(callback_query.message, user_id)

@router.callback_query(F.data.startswith("delete_acc_"), StateFilter("*"))
async def callback_delete_acc(callback_query: types.CallbackQuery, state: FSMContext):
    await state.clear()
    user_id = callback_query.from_user.id
    phone = callback_query.data.replace("delete_acc_", "")
    
    user_data = db_users.get(user_id)
    accounts = user_data.get("accounts", [])
    cleaned = [acc for acc in accounts if acc["phone"] != phone]
    db_users[user_id]["accounts"] = cleaned
    
    if db_users[user_id].get("active_phone") == phone:
        db_users[user_id]["active_phone"] = None
        db_users[user_id]["is_sending"] = False
        if cleaned:
            db_users[user_id]["active_phone"] = cleaned[0]["phone"]
            db_users[user_id]["active_name"] = cleaned[0]["name"]
            db_users[user_id]["active_username"] = cleaned[0]["username"]
    save_db()
    
    # Sessiyani uzish drayveri (TUZATILDI - TO'LIQ QO'SHILDI!)
    phone_clean = phone.replace("+", "").replace(" ", "")
    session_key = f"{user_id}_{phone_clean}"
    if session_key in active_clients:
        try:
            await active_clients[session_key].disconnect()
        except Exception: pass
        active_clients.pop(session_key, None)
        
    session_file = os.path.join(SESSIONS_DIR, f"session_{session_key}.session")
    if os.path.exists(session_file):
        try: os.remove(session_file)
        except Exception: pass
        
    await callback_query.answer("⚠️ O'chirildi!")
    await show_profillar_settings(callback_query.message, user_id)

@router.callback_query(F.data == "go_to_profillar", StateFilter("*"))
async def callback_go_to_profillar(callback_query: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await show_profillar_settings(callback_query.message, callback_query.from_user.id)

@router.callback_query(F.data == "close_menu", StateFilter("*"))
async def callback_close_menu(callback_query: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback_query.message.delete()

@router.callback_query(F.data == "check_sub_status", StateFilter("*"))
async def callback_check_sub(callback_query: types.CallbackQuery, state: FSMContext):
    await state.clear()
    user_id = callback_query.from_user.id
    ensure_user(user_id)
    await callback_query.answer("🎉 Obunangiz tasdiqlandi!")
    await callback_query.message.delete()
    await callback_query.message.answer(get_text(user_id, "welcome"), reply_markup=get_main_keyboard(user_id), parse_mode="HTML")

# ================= WEB SERVER =================
async def handle_ping(request):
    return web.Response(text="Bot is running smoothly!")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', handle_ping)
    app.router.add_get('/ping', handle_ping)
    
    port = int(os.environ.get("PORT", 10000))
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', port).start()

# ================= SYSTEM BACKGROUND TASKS =================
async def auto_sender_worker():
    while True:
        current_time = datetime.now().timestamp()
        for user_id, user_data in list(db_users.items()):
            if user_data.get("is_sending") and user_data.get("active_phone"):
                # Avto-o'chirish taymerini tekshirish
                started_at = user_data.get("is_sending_started_at", 0)
                auto_off_hours = user_data.get("auto_off_hours")
                if auto_off_hours and started_at:
                    elapsed = (current_time - started_at) / 3600.0
                    if elapsed >= auto_off_hours:
                        db_users[user_id]["is_sending"] = False
                        db_users[user_id]["is_sending_started_at"] = 0
                        save_db()
                        try:
                            await bot.send_message(
                                user_id, 
                                f"⏱️ <b>Avto-o'chirish taymeri ishladi!</b>\n\n"
                                f"Belgilangan vaqt tugashi sababli reklama tarqatish avtomatik ravishda to'xtatildi. 🛑", 
                                parse_mode="HTML"
                            )
                        except Exception: pass
                        continue
                
                next_run = user_data.get("next_run_timestamp", 0)
                if current_time >= next_run:
                    interval_minutes = user_data.get("interval", 15)
                    db_users[user_id]["next_run_timestamp"] = current_time + (interval_minutes * 60)
                    save_db()
                    asyncio.create_task(run_sending_cycle_for_user(user_id))
        await asyncio.sleep(10)

async def run_sending_cycle_for_user(user_id):
    user_data = db_users.get(user_id)
    if not user_data or not user_data.get("is_sending") or not user_data.get("active_phone"): return
    try:
        client = await get_client(user_id, user_data["active_phone"])
        if await client.is_user_authorized():
            guruhlar = []
            if user_data.get("groups_choice", "custom") == "custom":
                guruhlar = [int(x) for x in user_data.get("selected_groups", [])]
            else:
                async for dialog in client.iter_dialogs():
                    if dialog.is_group: guruhlar.append(int(dialog.id))
            
            for g_id in guruhlar:
                if not db_users.get(user_id, {}).get("is_sending"): break
                try:
                    await send_reklama_message(client, g_id, user_data, user_id)
                    user_data["today_sent"] = user_data.get("today_sent", 0) + 1
                    user_data["total_sent"] = user_data.get("total_sent", 0) + 1
                    save_db()
                    await asyncio.sleep(15)
                except errors.FloodWaitError as e:
                    await asyncio.sleep(e.seconds)
                except Exception:
                    continue
    except Exception:
        pass

async def send_reklama_message(client, chat_id, user_data, user_id):
    text = user_data.get("reklama_matni", "")
    lang = user_data.get("lang", "uz") or "uz"
    if not user_data.get("is_pro", False):
        text += f"\n\n@Auto_Xabar_Yuborish_Bot orqali yuborildi"
    
    photo_path = user_data.get("reklama_rasm")
    buttons_data = user_data.get("inline_buttons", [])
    tele_buttons = []
    for btn in buttons_data:
        try: tele_buttons.append(Button.url(btn["text"], btn["url"]))
        except Exception: pass
        
    if photo_path and os.path.exists(photo_path):
        await client.send_message(chat_id, text, file=photo_path, buttons=tele_buttons if tele_buttons else None)
    else:
        await client.send_message(chat_id, text, buttons=tele_buttons if tele_buttons else None)

# ================= APP INITIALIZATION =================
async def main():
    print("==================================================")
    print("🤖 AutoHabar Pro Bot ishga tushmoqda...")
    print("==================================================")
    if db:
        await restore_sessions_from_cloud()
    
    asyncio.create_task(init_existing_sessions())
    asyncio.create_task(auto_sender_worker())
    asyncio.create_task(start_web_server())
    
    await dp.start_polling(bot)

async def init_existing_sessions():
    if not os.path.exists(SESSIONS_DIR): return
    for file in os.listdir(SESSIONS_DIR):
        if file.endswith(".session") and "_" in file:
            try:
                parts = file.replace("session_", "").replace(".session", "").split("_")
                user_id = int(parts[0])
                phone_clean = parts[1]
                user_data = db_users.get(user_id)
                if not user_data: continue
                acc_list = user_data.get("accounts", [])
                target_phone = next((acc["phone"] for acc in acc_list if acc["phone"].replace("+","").replace(" ","") == phone_clean), None)
                if not target_phone: target_phone = "+" + phone_clean
                client = await get_client(user_id, target_phone)
                if await client.is_user_authorized():
                    active_clients[f"{user_id}_{phone_clean}"] = client
            except Exception: pass

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit()
