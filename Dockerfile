# Python rasmiy va yengil versiyasini tanlaymiz
FROM python:3.10-slim

# Terminalda yozuvlar darhol ko'rinishi uchun muhitni sozlaymiz
ENV PYTHONUNBUFFERED=1

# Tizim xavfsizligi uchun foydalanuvchi yaratamiz (Hugging Face talabi)
RUN useradd -m -u 1000 user
USER user
ENV PATH="/home/user/.local/bin:$PATH"

# Ishchi papkani yaratamiz va unga o'tamiz
WORKDIR /app

# Avval kutubxonalar ro'yxatini nusxalab olamiz (TUZATILDI: talablar.txt o'rniga requirements.txt)
COPY --chown=user requirements.txt /app/

# Kutubxonalarni o'rnatamiz
RUN pip install --no-cache-dir -r requirements.txt

# Barcha qolgan loyiha fayllarini nusxalaymiz
COPY --chown=user . /app/

# Standart portni ochamiz
EXPOSE 7860

# Botni ishga tushirish buyrug'i
CMD ["python", "telegram_real_connection_script.py"]
