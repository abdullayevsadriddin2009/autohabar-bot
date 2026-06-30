# -*- coding: utf-8 -*-
"""
AutoHabar Pro - Real Telegram Bot va Avtomatlashtirilgan Tarqatish Tizimi.
Ushbu skript Telegram Bot (aiogram v3) va Telegram MTProto Client (telethon) 
tizimlarini yagona asinxron motor, xavfsiz JSON ma'lumotlar bazasi va Render.com
uchun soxta Web Server orqali birlashtiradi.

Ishga tushirishdan oldin terminalda:
pip install aiogram telethon aiohttp
"""

import asyncio
import logging
import os
import sys
import shutil
import json
from datetime import datetime
from aiogram import Bot, Dispatcher, types, Router, F
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters import StateFilter, Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from telethon import TelegramClient, errors, Button
from aiohttp import web

# ================= CONFIGURATION =================
API_ID = 37104311
API_HASH = "f49729d10c144035c40f579b596d15b1"
BOT_TOKEN = "8680819777:AAFmbPFc6hNUk841ZaKlrnHlx1VrYfwebZA"
ADMIN_ID = 7073273800

# Papkalarni yaratish (Muhim: inglizcha nomlar bilan)
SESSIONS_DIR = "sessions"
os.makedirs(SESSIONS_DIR, exist_ok=True)
DB_FILE = os.path.join(SESSIONS_DIR, "database.json")

# Loggerlarni sozlash
logging.basicConfig(level=logging.INFO)

# Bot va Dispatcherni yaratish
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)

# ================= PERSISTENT JSON DATABASE =================
def load_db():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return {int(k): v for k, v in data.items()}
        except Exception as e:
            logging.error(f"Bazani o'qishda xato: {e}")
    
    # Boshlang'ich baza (fayl bo'lmasa yoki xato bo'lsa)
    return {
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
            "channels": ["@autoxabarc_news", "@autoxabar_chat"]
        }
    }

def save_db():
    try:
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump(db_users, f, ensure_ascii=False, indent=4)
    except Exception as e:
        logging.error(f"Bazani yozishda xato: {e}")

db_users = load_db()
active_clients = {}

# ================= STATES FOR LOGIN & ACTIONS =================
class LoginStates(StatesGroup):
    waiting_phone = State()
    waiting_code = State()
    waiting_2fa = State()

class TextStates(StatesGroup):
    waiting_text = State()
    waiting_photo = State()
    waiting_buttons = State()

class AdminStates(StatesGroup):
    waiting_search_id = State()
    waiting_add_balans = State()
    waiting_add_stars = State()
    waiting_add_channel = State()
    waiting_broadcast_msg = State()

# ================= CLIENT RECOVERY ENGINE =================
async def get_client(user_id):
    client = active_clients.get(user_id)
    session_path = os.path.join(SESSIONS_DIR, f"session_{user_id}")
    
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
        active_clients[user_id] = client
        
    if not client.is_connected():
        await client.connect()
        
    return client

# ================= KEYBOARDS =================
def get_main_keyboard(user_id):
    kb = [
        [KeyboardButton(text="⚪ Autohabar yuborish"), KeyboardButton(text="📝 Habar matni")],
        [KeyboardButton(text="⏱️ Interval"), KeyboardButton(text="💬 Guruhlarni sozlash")],
        [KeyboardButton(text="👤 Profillar"), KeyboardButton(text="👑 Pro tarif")],
        [KeyboardButton(text="👤 Kabinet"), KeyboardButton(text="⚙️ Sozlamalar")]
    ]
    if user_id == ADMIN_ID:
        kb.append([KeyboardButton(text="🛡️ Admin Panel")])
    
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_admin_main_markup():
    kb = [
        [
            InlineKeyboardButton(text="📊 Bot Statistikasi", callback_data="adm_stats"),
            InlineKeyboardButton(text="👤 Foydalanuvchini sozlash", callback_data="adm_search_user")
        ],
        [
            InlineKeyboardButton(text="📢 Majburiy Obuna", callback_data="adm_mandatory_sub"),
            InlineKeyboardButton(text="✉️ Ommaviy Reklama", callback_data="adm_broadcast_prompt")
        ],
        [InlineKeyboardButton(text="❌ Yopish", callback_data="close_menu")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

# ================= BOT HANDLERS =================

@router.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
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
            "active_name": message.from_user.full_name,
            "active_username": f"@{message.from_user.username}" if message.from_user.username else "@-",
            "is_sending": False,
            "groups_choice": "custom",
            "selected_groups": [],
            "cached_groups": [],
            "joined_time": datetime.now().strftime("%H:%M"),
            "today_sent": 0,
            "total_sent": 0,
            "channels": ["@autoxabarc_news", "@autoxabar_chat"]
        }
        save_db()
    
    text = (
        "📊 <b>Asosiy menyu:</b>\n"
        "<b>O AUTO HABAR PRO</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Salom, xush kelibsiz! 👋\n\n"
        "› Akkaunt qo'shing\n"
        "› Guruhlarni sozlang\n"
        "› Habarni sozlang\n"
        "› Autohabarni ishga tushuring"
    )
    
    inline_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Akkaunt qo'shish", callback_data="add_account")]
    ])
    
    await message.answer(text, reply_markup=get_main_keyboard(user_id), parse_mode="HTML")
    await message.answer("Tizimdan foydalanish uchun quyidagi tugmani bosing:", reply_markup=inline_kb)

@router.message(F.text == "⚪ Autohabar yuborish")
async def menu_autohabar(message: types.Message):
    user_id = message.from_user.id
    user_data = db_users.get(user_id, {})
    
    phone = user_data.get("active_phone")
    profilStatus = f"👤 Profil: [ {phone} ]" if phone else "👤 Profil: [ Profil ulanmagan ]"
    holatStatus = "🟢 Faol (Yuborilmoqda...)" if user_data.get("is_sending") else "🔴 O'chiq"
    
    responseText = (
        "🤠 <b>Boshqaruv paneli</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"{profilStatus}\n"
        f"⚡ Holat: <b>{holatStatus}</b>\n"
        f"✍️ Xabar turi: <b>Matn</b>\n"
        f"💬 Guruhlar: <b>{len(user_data.get('selected_groups', [])) if user_data.get('groups_choice') == 'custom' else 'Barchasi'} ta</b>\n"
        f"⏱️ Interval: <b>{user_data.get('interval', 15)} daqiqa</b>\n"
        "⌛ Avto-o'chish: <b>∞ Cheksiz</b>\n"
        "📢 Mention: <b>O'chiq</b>\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )
    
    start_stop_text = "🛑 To'xtatish" if user_data.get("is_sending") else "▶️ Ishga tushirish"
    
    inline_kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=start_stop_text, callback_data="toggle_sending"),
            InlineKeyboardButton(text="📊 Statistika", callback_data="statistika")
        ],
        [
            InlineKeyboardButton(text="⏳ Avto-o'chirish taymeri", callback_data="timer_setup"),
            InlineKeyboardButton(text="🔄 Yangilash", callback_data="refresh_status")
        ]
    ])
    
    await message.answer(responseText, reply_markup=inline_kb, parse_mode="HTML")

@router.message(F.text == "📝 Habar matni")
async def menu_habar_matni_msg(message: types.Message):
    user_id = message.from_user.id
    await show_message_settings(message, user_id)

async def show_message_settings(message: types.Message, user_id: int):
    user_data = db_users.get(user_id, {})
    
    reklama_rasm = "Bor 🖼️" if user_data.get("reklama_rasm") else "Yo'q ❌"
    tugmalar_soni = f"Bor ({len(user_data.get('inline_buttons', []))} ta) 🔘" if user_data.get("inline_buttons") else "Yo'q ❌"
    
    textDetail = (
        "💬 <b>Habarni sozlash</b>\n\n"
        f"📝 <b>Joriy matn:</b>\n<i>\"{user_data.get('reklama_matni')}\"</i>\n\n"
        f"🖼️ <b>Biriktirilgan rasm:</b> <b>{reklama_rasm}</b>\n"
        f"🔘 <b>Inline tugmalar:</b> <b>{tugmalar_soni}</b>\n\n"
        "📌 <b>Xabar turini tanlang:</b>"
    )
    
    inline_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✍️ Matnni tahrirlash", callback_data="edit_text")],
        [InlineKeyboardButton(text="🖼️ Rasm yuklash / o'zgartirish", callback_data="edit_photo")],
        [InlineKeyboardButton(text="🔘 Tugmali xabar (Inline PRO)", callback_data="edit_buttons_pro")], 
        [InlineKeyboardButton(text="❌ Rasm va tugmalarni tozalash", callback_data="clear_media_buttons")],
        [InlineKeyboardButton(text="⬅️ Orqaga", callback_data="back_to_panel")]
    ])

    await message.answer(textDetail, reply_markup=inline_kb, parse_mode="HTML")

# ================= AD REKLAMA EDIT HANDLERS (YANGI 🛠️) =================

@router.callback_query(F.data == "edit_text")
async def callback_edit_text(callback_query: types.CallbackQuery, state: FSMContext):
    await state.set_state(TextStates.waiting_text)
    await callback_query.message.answer("✍️ <b>Yangi reklama matnini yuboring:</b>", parse_mode="HTML")
    await callback_query.answer()

@router.message(StateFilter(TextStates.waiting_text))
async def message_receive_text(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    new_text = message.text
    if user_id in db_users:
        db_users[user_id]["reklama_matni"] = new_text
        save_db()
        await message.answer("✅ <b>Reklama matni muvaffaqiyatli o'zgartirildi!</b>", reply_markup=get_main_keyboard(user_id), parse_mode="HTML")
        await show_message_settings(message, user_id)
    await state.clear()

@router.callback_query(F.data == "edit_photo")
async def callback_edit_photo(callback_query: types.CallbackQuery, state: FSMContext):
    await state.set_state(TextStates.waiting_photo)
    await callback_query.message.answer("🖼️ <b>Reklama uchun rasmni oddiy rasm ko'rinishida yuboring:</b>", parse_mode="HTML")
    await callback_query.answer()

@router.message(StateFilter(TextStates.waiting_photo), F.photo)
async def message_receive_photo(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    # Eng yuqori sifatli rasmni olamiz
    photo = message.photo[-1]
    file_info = await bot.get_file(photo.file_id)
    
    # Yuklab olinadigan downloads papkasini yaratamiz
    downloads_dir = "downloads"
    os.makedirs(downloads_dir, exist_ok=True)
    local_path = os.path.join(downloads_dir, f"reklama_{user_id}.jpg")
    
    await bot.download_file(file_info.file_path, local_path)
    
    if user_id in db_users:
        db_users[user_id]["reklama_rasm"] = local_path
        save_db()
        await message.answer("✅ <b>Reklama rasmi muvaffaqiyatli saqlandi!</b>", reply_markup=get_main_keyboard(user_id), parse_mode="HTML")
        await show_message_settings(message, user_id)
    await state.clear()

@router.message(StateFilter(TextStates.waiting_photo))
async def message_receive_photo_invalid(message: types.Message):
    await message.answer("⚠️ Iltimos, reklama uchun rasm shaklida fayl yuboring!")

@router.callback_query(F.data == "clear_media_buttons")
async def callback_clear_media_buttons(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    if user_id in db_users:
        # Eski rasmni xavfsiz o'chirib yuborish
        old_path = db_users[user_id].get("reklama_rasm")
        if old_path and os.path.exists(old_path):
            try:
                os.remove(old_path)
            except Exception:
                pass
        db_users[user_id]["reklama_rasm"] = None
        db_users[user_id]["inline_buttons"] = []
        save_db()
        await callback_query.answer("❌ Barcha media va tugmalar olib tashlandi!", show_alert=True)
        await show_message_settings(callback_query.message, user_id)

@router.callback_query(F.data == "edit_buttons_pro")
async def callback_edit_buttons_pro(callback_query: types.CallbackQuery, state: FSMContext):
    user_id = callback_query.from_user.id
    if not db_users.get(user_id, {}).get("is_pro", False):
        await callback_query.answer("👑 Bu funksiyadan foydalanish uchun hisobingizda PRO tarif bo'lishi shart!", show_alert=True)
        return
        
    await state.set_state(TextStates.waiting_buttons)
    await callback_query.message.answer(
        "🔘 <b>Tugmalarni quyidagi formatda yozib yuboring:</b>\n\n"
        "<code>Mening saytim | https://havola.uz</code>\n\n"
        "<i>Agar tugmalar soni bir nechta bo'lsa, har birini yangi qatordan kiriting.</i>",
        parse_mode="HTML"
    )
    await callback_query.answer()

@router.message(StateFilter(TextStates.waiting_buttons))
async def message_receive_buttons(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
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
        await message.answer("❌ <b>Format xato!</b> Namunadagidek yozing:\n<code>Telegram | https://t.me/kanal</code>")

# =======================================================================

@router.message(F.text == "👤 Kabinet")
async def menu_kabinet(message: types.Message):
    user_id = message.from_user.id
    user_data = db_users.get(user_id, {})
    
    phone = user_data.get("active_phone") or "Profil ulanmagan"
    username = user_data.get("active_username") or "@-"
    name = user_data.get("active_name") or "Mavjud emas"
    is_pro_text = "Pro" if user_data.get("is_pro") else "Free"
    premium_text = "Bor 👑" if user_data.get("is_pro") else "Pro yo'q"
    
    text = (
        "👤 <b>Sizning Kabinetingiz</b>\n\n"
        f"👥 Ism: <b>{name}</b>\n"
        f"📞 Raqam: <b>+{phone.replace('+', '') if phone != 'Profil ulanmagan' else phone}</b>\n"
        f"🌐 Username: <b>{username}</b>\n\n"
        "📊 <b>Statistika:</b>\n"
        f"✔️ Bugun yuborildi: <b>{user_data.get('today_sent', 0)}</b>\n"
        f"🔄 Jami yuborilgan: <b>{user_data.get('total_sent', 0)}</b>\n"
        f"➕ Guruhlar: <b>{len(user_data.get('selected_groups', [])) if user_data.get('groups_choice') == 'custom' else 'Barchasi'}</b>\n"
        f"👥 Jami profillar: <b>{1 if user_data.get('active_phone') else 0}</b>\n"
        f"📅 Qo'shilgan: <b>{user_data.get('joined_time', '22:37')}</b>\n\n"
        f"🛡️ Tarif: 👤 <b>{is_pro_text}</b>\n"
        f"🗂️ Premium: <b>{premium_text}</b>\n"
        f"️ Interval: <b>{user_data.get('interval', 15)} daqiqa</b>"
    )
    
    inline_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚠️ Profilni uzish", callback_data="disconnect_profile")],
        [InlineKeyboardButton(text="❌ Yopish", callback_data="close_menu")]
    ])
    await message.answer(text, reply_markup=inline_kb, parse_mode="HTML")

@router.message(F.text == "💬 Guruhlarni sozlash")
async def menu_guruhlar(message: types.Message):
    user_id = message.from_user.id
    user_data = db_users.get(user_id, {})
    
    choice = user_data.get("groups_choice", "custom")
    hamma_check = "✓" if choice == "all" else " "
    ozim_check = "✓" if choice == "custom" else " "
    
    tanlov_nomi = "Hamma guruhlarga" if choice == "all" else "O'zim tanlayman"
    
    text = (
        "🎯 <b>Guruhlarni sozlash</b>\n\n"
        "Qaysi guruhlarga xabar yuboramiz?\n"
        f"<b>{hamma_check} Tanlangan</b>\n"
        f"<b>{ozim_check} Tanlanmagan</b>\n\n"
        f"📌 Hozirgi tanlov: <b>{tanlov_nomi}</b>\n\n"
        "📉 Guruhlarni tanlang"
    )
    
    inline_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="+ Hamma guruhlarga", callback_data="set_groups_all")],
        [InlineKeyboardButton(text="✓ O'zim tanlayman", callback_data="set_groups_custom")],
        [
            InlineKeyboardButton(text="📊 Ro'yxatlar", callback_data="groups_list_page_0"),
            InlineKeyboardButton(text="+ Qo'shish", callback_data="refresh_groups_force"),  
            InlineKeyboardButton(text="🚨 O'chirish", callback_data="clear_selected_groups") 
        ],
        [InlineKeyboardButton(text="← Orqaga", callback_data="back_to_panel")]
    ])
    await message.answer(text, reply_markup=inline_kb, parse_mode="HTML")

@router.message(F.text == "👤 Profillar")
async def menu_profillar(message: types.Message):
    user_id = message.from_user.id
    user_data = db_users.get(user_id, {})
    phone = user_data.get("active_phone")
    
    text = "👥 <b>Ulangan Profillar:</b>\n\n"
    if phone:
        text += f"✅ 1. <b>{phone}</b> (Faol)\n"
    else:
        text += "❌ Hali hech qanday profil yo'q.\n"
    
    text += "\n<i>Free tarifda faqat 1 ta profil ulashingiz mumkin. Premium tarifda 5 tagacha profil qo'shish imkoni bor!</i>"
    
    inline_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Yangi profil qo'shish", callback_data="add_account")],
        [InlineKeyboardButton(text="← Orqaga", callback_data="back_to_panel")]
    ])
    await message.answer(text, reply_markup=inline_kb, parse_mode="HTML")

@router.message(F.text == "👑 Pro tarif")
async def menu_pro_tarif(message: types.Message):
    text = (
        "👑 <b>AutoXabar Pro imkoniyatlari:</b>\n\n"
        "👤 Ko'p profil: 5 tagacha akkaunt ulasiz\n"
        "🔕 Watermarksiz va reklamasiz toza interfeys\n"
        "📤 Forward xabarlarni tarqatish\n"
        "🔘 Tugmali inline xabarlar tayyorlash\n"
        "💬 Guruh a'zolarini ommaviy @mention qilish\n\n"
        "💰 <b>Narxlar:</b>\n"
        "• Karta orqali: 35,000 so'm / 30 kun\n"
        "• Stars orqali: 140 ⭐️ / 30 kun\n"
        "• USDT orqali: 2.50 $ / 30 kun"
    )
    inline_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Sotib olish", callback_data="buy_pro")]
    ])
    await message.answer(text, reply_markup=inline_kb, parse_mode="HTML")

@router.message(F.text == "⏱️ Interval")
async def menu_interval(message: types.Message):
    user_id = message.from_user.id
    user_data = db_users.get(user_id, {})
    
    text = (
        "⏱️ <b>Habar oralig'i (Interval)</b>\n\n"
        f"Joriy interval: <b>{user_data.get('interval', 15)} daqiqa</b>\n\n"
        "Har bir sikl yakunlangach, bot belgilangan muddat davomida to'xtab turadi."
    )
    inline_kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="5daq", callback_data="set_int_5"),
            InlineKeyboardButton(text="15daq", callback_data="set_int_15"),
            InlineKeyboardButton(text="30daq", callback_data="set_int_30"),
            InlineKeyboardButton(text="1 soat", callback_data="set_int_60")
        ]
    ])
    await message.answer(text, reply_markup=inline_kb, parse_mode="HTML")

@router.message(F.text == "⚙️ Sozlamalar")
async def menu_sozlamalar(message: types.Message):
    text = (
        "⚙️ <b>Qo'shimcha Sozlamalar:</b>\n\n"
        "🤖 Avto-obuna: <b>Yoqilgan</b> (Bot majburiy guruhlarni o'zi aniqlaydi)\n"
        "↩️ Auto Reply: <b>O'chiq</b>\n"
        "🛡️ Anti-Ban: <b>Eng yuqori darajada</b>"
    )
    await message.answer(text, parse_mode="HTML")


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
async def callback_adm_main(callback_query: types.CallbackQuery, state: FSMContext):
    await state.clear()
    text = (
        "🛡️ <b>AutoHabar Pro - Tizim Admin Paneli</b>\n\n"
        "Boshqaruv bo'limini tanlang:"
    )
    await callback_query.message.edit_text(text, reply_markup=get_admin_main_markup(), parse_mode="HTML")
    await callback_query.answer()

@router.callback_query(F.data == "adm_stats")
async def callback_adm_stats(callback_query: types.CallbackQuery):
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
                    InlineKeyboardButton(text="⭐️ Stars tahrirlash", callback_data=f"adm_chg_stars_{target_id}")
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
        await message.answer("❌ ID raqam faqat butun sonlardan iborat bo'lishi kerak! Qaytadan kiriting:")

@router.callback_query(F.data.startswith("adm_chg_bal_"))
async def callback_adm_chg_bal_prompt(callback_query: types.CallbackQuery, state: FSMContext):
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
                reply_markup=get_main_keyboard(message.from_user.id),
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
                reply_markup=get_main_keyboard(message.from_user.id),
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
        await callback_adm_main(callback_query, FSMContext(storage=MemoryStorage(), key=None))
    else:
        await callback_query.answer("Foydalanuvchi topilmadi!", show_alert=True)

@router.callback_query(F.data == "adm_mandatory_sub")
async def callback_adm_sub_menu(callback_query: types.CallbackQuery):
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

@router.callback_query(F.data == "adm_sub_clear_chan")
async def callback_adm_clear_chans(callback_query: types.CallbackQuery):
    db_users[ADMIN_ID]["channels"] = []
    save_db()
    await callback_query.answer("📢 Barcha majburiy kanallar olib tashlandi!", show_alert=True)
    await callback_adm_sub_menu(callback_query)

@router.callback_query(F.data == "adm_broadcast_prompt")
async def callback_adm_broadcast_prompt(callback_query: types.CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.waiting_broadcast_msg)
    text = (
        "✉️ <b>Ommaviy reklama tarqatish bo'limi</b>\n\n"
        "Istalgan rasm yoki matnli xabarni yuboring. Ushbu xabar botga start bosgan barcha foydalanuvchilarga avtomatik asinxron tarzda tarqatiladi!"
    )
    inline_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Bekor qilish", callback_data="adm_main_menu")]
    ])
    await callback_query.message.edit_text(text, reply_markup=inline_kb, parse_mode="HTML")
    await callback_query.answer()

@router.message(StateFilter(AdminStates.waiting_broadcast_msg))
async def state_process_broadcast(message: types.Message, state: FSMContext):
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

# =========================================================================

@router.callback_query(F.data == "back_to_panel")
async def callback_back_panel(callback_query: types.CallbackQuery):
    await callback_query.answer()
    await menu_autohabar(callback_query.message)


# ================= SENDER ENGINE (REAL VAQT INTERVALLI) =================

async def send_reklama_message(client, chat_id, user_data, user_id):
    """ Media (Rasm), Inline tugmalar va textni yaxlit bitta xabar sifatida yuboruvchi asinxron motor """
    text = user_data.get("reklama_matni", "")
    
    # Oddiy a'zolar xabariga avtomatik ravishda maxsus imzo qo'shiladi!
    if int(user_id) != ADMIN_ID:
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
        client = await get_client(user_id)
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
                
            logging.info(f"[Sender] Foydalanuvchi {user_id} uchun {len(guruhlar)} ta guruhga tarqatish boshlandi...")
            
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
                    logging.warning(f"FloodWait cheklovi! {e.seconds} soniya kutiladi.")
                    await asyncio.sleep(e.seconds)
                except Exception as e:
                    logging.error(f"Guruh {g_id} ga yuborishda xato: {e}")
                    continue
                    
    except Exception as e:
        logging.error(f"Sender asinxron xatolik user {user_id}: {str(e)}")


# ================= GROUP SELECTION & CALLBACKS =================

@router.callback_query(F.data == "disconnect_profile")
async def callback_disconnect(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    if user_id in db_users:
        db_users[user_id]["active_phone"] = None
        db_users[user_id]["is_sending"] = False
        save_db()
    
    if user_id in active_clients:
        try:
            await active_clients[user_id].disconnect()
        except Exception:
            pass
        active_clients.pop(user_id, None)

    await callback_query.answer("⚠️ Profil muvaffaqiyatli uzildi!", show_alert=True)
    await menu_kabinet(callback_query.message)

@router.callback_query(F.data == "set_groups_all")
async def callback_groups_all(callback_query: types.CallbackQuery):
    try:
        await callback_query.answer()
    except Exception:
        pass
    user_id = callback_query.from_user.id
    if user_id in db_users:
        db_users[user_id]["groups_choice"] = "all"
        save_db()
    await callback_query.answer("✓ Hamma guruhlar tanlandi!", show_alert=True)
    await menu_guruhlar(callback_query.message)

@router.callback_query(F.data == "set_groups_custom")
async def callback_groups_custom(callback_query: types.CallbackQuery):
    try:
        await callback_query.answer()
    except Exception:
        pass
    user_id = callback_query.from_user.id
    if user_id in db_users:
        db_users[user_id]["groups_choice"] = "custom"
        save_db()
    await callback_query.answer("✓ Qo'lda tanlash rejimi faollashdi!", show_alert=True)
    await menu_guruhlar(callback_query.message)

@router.callback_query(F.data == "clear_selected_groups")
async def callback_clear_groups(callback_query: types.CallbackQuery):
    try:
        await callback_query.answer()
    except Exception:
        pass
    user_id = callback_query.from_user.id
    if user_id in db_users:
        db_users[user_id]["selected_groups"] = []
        save_db()
    await callback_query.answer("🚨 Barcha tanlangan guruhlar belgilanishi tozalandi!", show_alert=True)
    await callback_groups_list(callback_query)

@router.callback_query(F.data == "refresh_groups_force")
async def callback_refresh_groups_force(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    try:
        await callback_query.answer("Kesh yangilanmoqda, kuting...", show_alert=False)
    except Exception:
        pass

    try:
        client = await get_client(user_id)
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
    user_data = db_users.get(user_id, {})
    parts = callback_query.data.split("_")
    page = 0
    if len(parts) >= 4:
        try:
            page = int(parts[3])
        except (ValueError, IndexError):
            page = 0
    
    guruhlar = user_data.get("cached_groups", [])
    if not guruhlar:
        try:
            client = await get_client(user_id)
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
    parts = callback_query.data.split("_")
    if len(parts) < 4:
        return
        
    try:
        group_id = int(parts[2])
        page = int(parts[3])
    except (ValueError, IndexError):
        return
    
    user_data = db_users.get(user_id, {})
    selected_ids = [int(x) for x in user_data.get("selected_groups", [])]
    
    if group_id in selected_ids:
        selected_ids.remove(group_id)
    else:
        selected_ids.append(group_id)
        
    user_data["selected_groups"] = selected_ids
    save_db()
    await callback_groups_list(callback_query)

@router.callback_query(F.data.startswith("select_all_groups_"))
async def callback_select_all_groups(callback_query: types.CallbackQuery):
    try:
        await callback_query.answer()
    except Exception:
        pass

    user_id = callback_query.from_user.id
    user_data = db_users.get(user_id, {})
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
    if user_id in db_users:
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
    user_data = db_users.get(user_id, {})
    user_data["selected_groups"] = [int(x) for x in user_data.get("selected_groups", [])]
    save_db()
    g_count = len(user_data["selected_groups"])
    await callback_query.answer(f"✓ Tanlangan {g_count} ta guruh muvaffaqiyatli saqlandi!", show_alert=True)
    await menu_guruhlar(callback_query.message)


# ================= OTHER ACTIONS & LOGIN WIZARD =================

@router.callback_query(F.data == "toggle_sending")
async def callback_toggle_sending(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    user_data = db_users.get(user_id, {})
    
    if not user_data.get("active_phone"):
        await callback_query.answer("Avvalo profilingizni ulashingiz shart! 📱", show_alert=True)
        return
        
    user_data["is_sending"] = not user_data.get("is_sending")
    status_text = "ishga tushirildi! 🚀" if user_data["is_sending"] else "to'xtatildi! 🛑"
    
    if user_data["is_sending"]:
        user_data["next_run_timestamp"] = 0
        
    save_db()
    await callback_query.answer(f"Avto-xabar tarqatish muvaffaqiyatli {status_text}", show_alert=True)
    await menu_autohabar(callback_query.message)
    
    if user_data["is_sending"]:
        logging.info(f"[Sender] Foydalanuvchi {user_id} uchun reklama tarqatish darhol ishga tushirildi!")
        asyncio.create_task(trigger_immediate_sending(user_id))

async def trigger_immediate_sending(user_id):
    user_data = db_users.get(user_id, {})
    if not user_data.get("is_sending"):
        return
    interval_minutes = user_data.get("interval", 15)
    db_users[user_id]["next_run_timestamp"] = datetime.now().timestamp() + (interval_minutes * 60)
    save_db()
    await run_sending_cycle_for_user(user_id)

@router.callback_query(F.data == "add_account")
async def callback_add_account_wizard(callback_query: types.CallbackQuery, state: FSMContext):
    try:
        await callback_query.answer()
    except Exception:
        pass
    
    user_id = callback_query.from_user.id
    if user_id in active_clients:
        try:
            await active_clients[user_id].disconnect()
        except Exception:
            pass
        active_clients.pop(user_id, None)

    session_file = os.path.join(SESSIONS_DIR, f"session_{user_id}.session")
    if os.path.exists(session_file):
        for attempt in range(5):
            try:
                os.remove(session_file)
                break
            except Exception:
                await asyncio.sleep(0.3)

    await callback_query.message.answer(
        "📱 <b>Real Telegram akkaunt ulash</b>\n\n"
        "Iltimos, telefon raqamingizni xalqaro formatda kiriting (masalan: <code>+998901234567</code>):",
        parse_mode="HTML"
    )
    await state.set_state(LoginStates.waiting_phone)

@router.message(StateFilter(LoginStates.waiting_phone))
async def state_phone_received(message: types.Message, state: FSMContext):
    phone = message.text.strip()
    if not phone.startswith("+") or len(phone) < 9:
        await message.answer("❌ Noto'g'ri telefon raqam! Format: +998901234567")
        return
    
    await state.update_data(phone=phone)
    await message.answer("🔄 Telegram serverlariga mutloq toza ulanish o'rnatilmoqda. Iltimos kuting...")
    
    try:
        client = await get_client(message.from_user.id)
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
    
    try:
        client = await get_client(message.from_user.id)
        await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
        
        me = await client.get_me()
        user_id = message.from_user.id
        
        db_users[user_id]["active_phone"] = phone
        db_users[user_id]["active_name"] = me.first_name
        db_users[user_id]["active_username"] = f"@{me.username}" if me.username else "@-"
        save_db()
        
        await message.answer(
            "<b>Tabriklaymiz! Akkauntingiz muvaffaqiyatli bog'landi.</b>\n\n"
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
    data = await state.get_data()
    phone = data.get("phone")
    
    try:
        client = await get_client(user_id)
        await client.sign_in(password=password)
        me = await client.get_me()
        
        db_users[user_id]["active_phone"] = phone
        db_users[user_id]["active_name"] = me.first_name
        db_users[user_id]["active_username"] = f"@{me.username}" if me.username else "@-"
        save_db()
        
        await message.answer(
            "✅ <b>Akkauntingiz ikki bosqichli parol orqali muvaffaqiyatli bog'landi!</b>",
            reply_markup=get_main_keyboard(user_id),
            parse_mode="HTML"
        )
        await state.clear()
    except errors.PasswordHashInvalidError:
        await message.answer("❌ <b>Ikki bosqichli parol noto'g'ri!</b>\n\nIltimos, parolingizni qayta kiriting.")
    except Exception as e:
        await message.answer(f"❌ Parol noto'g'ri: {str(e)}")


# ================= SOXTA WEB SERVER (RENDER PORT BINDING UCHUN) =================

async def handle_ping(request):
    return web.Response(text="Bot is running smoothly on Render!")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', handle_ping)
    app.router.add_get('/ping', handle_ping)
    
    port = int(os.environ.get("PORT", 10000))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logging.info(f"Render uchun soxta veb-server {port}-portda muvaffaqiyatli ishga tushirildi!")

# ==================================================================================


# Mavjud sessiya fayllarini tekshirish va avtomatik ulanish
async def init_existing_sessions():
    # Eski fayllarni xavfsiz moslashtirish va yuklash
    for file in os.listdir(SESSIONS_DIR):
        if file.endswith(".session"):
            user_id_str = file.replace("session_", "").replace(".session", "")
            try:
                user_id = int(user_id_str)
                client = await get_client(user_id)
                
                if await client.is_user_authorized():
                    active_clients[user_id] = client
                    me = await client.get_me()
                    
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
                            "active_phone": f"+{me.phone}",
                            "active_name": me.first_name,
                            "active_username": f"@{me.username}" if me.username else "@-",
                            "is_sending": False,
                            "groups_choice": "all",
                            "selected_groups": [],
                            "cached_groups": [],
                            "joined_time": datetime.now().strftime("%H:%M"),
                            "today_sent": 0,
                            "total_sent": 0,
                            "channels": ["@autoxabarc_news", "@autoxabar_chat"]
                        }
                    else:
                        db_users[user_id]["active_phone"] = f"+{me.phone}"
                        db_users[user_id]["active_name"] = me.first_name
                        db_users[user_id]["active_username"] = f"@{me.username}" if me.username else "@-"
                        
                    save_db()
                    logging.info(f"Mavjud sessiya muvaffaqiyatli yuklandi: +{me.phone} (ID: {user_id})")
            except Exception as e:
                logging.error(f"Sessiya yuklashda xatolik ({file}): {e}")

async def main():
    print("==================================================")
    print("🤖 AutoHabar Pro Telegram Bot ishga tushmoqda...")
    print("==================================================")
    print(f"[Tizim] Bot tokeni: {BOT_TOKEN[:15]}...")
    print(f"[Tizim] Admin ID: {ADMIN_ID}")
    
    await init_existing_sessions()
    
    # Asinxron ishlovchi fon xizmatlarini yoqamiz
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
        print("\n[👋] Dastur to'xtatildi.")
        sys.exit()
