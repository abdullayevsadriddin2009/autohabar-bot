# Python rasmiy va yengil versiyasini tanlaymiz
FROM python:3.10-slim

# Terminalda yozuvlar darhol ko'rinishi uchun muhitni sozlaymiz
ENV PYTHONUNBUFFERED=1

# Ishchi papkani yaratamiz va unga o'tamiz
WORKDIR /app

# Avval kutubxonalar ro'yxatini nusxalab olamiz
COPY requirements.txt /app/

# Kutubxonalarni o'rnatamiz
RUN pip install --no-cache-dir -r requirements.txt

# Barcha qolgan loyiha fayllarini (va sessions papkasini) nusxalaymiz
COPY . /app/

# Botni ishga tushirish buyrug'i
CMD ["python", "telegram_real_connection_script.py"]