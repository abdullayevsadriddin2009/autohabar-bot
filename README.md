# AutoHabar Pro (v2 tuzatilgan)

Telegram orqali avtomatik reklama tarqatish boti. aiogram v3 (bot) + Telethon (real akkaunt) + Firebase Firestore (bulutli baza) asosida.

## ⚠️ Muhim xavfsizlik eslatmasi (avval o'qing!)

Eski kodda barcha kalitlar (BOT_TOKEN, API_HASH, Firebase private_key) ochiq ko'rinishda saqlangan va `firebase_credentials.json` fayli repo'ga yuklangan. **Bu kalitlar allaqachon oshkor bo'lgan.** Zarf:

1. **@BotFather** orqali eski bot tokenini `/revoke` qiling va yangi token oling.
2. **Firebase Console → Project Settings → Service accounts** dan eski kalitni o'chiring (Delete key) va yangi kalit yarating.
3. Yangi kalitlarni faqat `.env` faylga yoki Render **Environment Variables** ga yozing — hech qachon GitHub'ga yuklamang.

## O'zgartirilgan nimalar (v2)

- ✅ Barcha maxfiy kalitlar ENV o'zgaruvchilaridan o'qiladi (`python-dotenv` qo'llab-quvvatlanadi)
- ✅ Firebase faqat `FIREBASE_CREDENTIALS_JSON` env orqali ulanadi — fayl kerak emas
- ✅ Firestore'da har bir foydalanuvchi alohida hujjatda (1MB chegara yo'q)
- ✅ Bulutga yozish 15 soniyada bir marta (debounce) — har bir tugma bosilganda to'liq yozish emas
- ✅ `today_sent` har kuni avtomatik nolga tushadi
- ✅ Watermark (tagidagi "bu bot orqali yuborildi") faqat **Free** foydalanuvchilarda — Pro'da butunlay yo'q (oldingi kodda Pro hamda bor edi)
- ✅ Har bir foydalanuvchi uchun alohida `asyncio.Lock` — yuborish sikllari bir-birining ustiga yig'ilmaydi
- ✅ `FloodWaitError` da akkaunt vaqtincha cooldown'ga olinadi (serverni hammer qilishdan saqlaydi)
- ✅ Rasm base64 ko'rinishida bazada saqlanadi — konteyner qayta ishga tushganda yo'qolmaydi
- ✅ SMS kodida "nuqta qo'yib kiriting" hiylasi olib tashlandi (faqat eski formatni ham qabul qilish qoldirildi)
- ✅ `groups_choice="all"` da `active_phone` bo'lmasa avtomatik birinchi akkaunt olinadi (avval crash bo'lardi)
- ✅ `callback_clear_blacklist` yordamchi funksiya orqali ishlaydi (soxta `FSMContext` olib tashlandi)
- ✅ `ensure_user` faqat haqiqiy o'zgarish bo'lganda saqlaydi

## Render.com da deploy qilish

1. Reponi GitHub'ga yuklang (`.gitignore` ishlayotganiga ishonch hosil qiling — `firebase_credentials.json`, `*.session`, `.env` yuklanmasligi kerak).
2. Render.com → **New → Web Service** → reponi ulang.
3. Sozlamalar:
   - **Runtime**: Docker
   - **Region**: Frankfurt yoki Singapore (eng yaqini)
   - **Instance Type**: Free (yoki Starter)
4. **Environment Variables** ga quyidilarni qo'shing:
   - `BOT_TOKEN` — yangi bot tokeningiz
   - `API_ID` — my.telegram.org dan
   - `API_HASH` — my.telegram.org dan
   - `ADMIN_ID` — sizning Telegram ID raqamingiz
   - `APP_ID` — firebase loyiha ID (standart: `autohabar-bot`)
   - `FIREBASE_CREDENTIALS_JSON` — `firebase_credentials.json` ning **to'liq matni** (bitta qatorda)
   - `PORT` — `7860`
5. **Create Web Service** bosing. Bir necha daqiqada bot ishga tushadi.

## Lokalda ishga tushirish

```bash
pip install -r requirements.txt
cp .env.example .env   # .env ni to'ldiring
python telegram_real_connection_script.py
```

## Fayllar

| Fayl | Tavsif |
|---|---|
| `telegram_real_connection_script.py` | Asosiy bot skripti (3000+ qator) |
| `requirements.txt` | Python kutubxonalari |
| `Dockerfile` | Docker konfiguratsiyasi |
| `.env.example` | Muhit o'zgaruvchilari namunasi |
| `.gitignore` / `.dockerignore` | Maxfiy fayllarni himoya qilish |
