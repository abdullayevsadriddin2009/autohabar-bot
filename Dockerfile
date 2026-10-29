# Python rasmiy va yengil versiyasi
FROM python:3.10-slim

ENV PYTHONUNBUFFERED=1
# Render web server porti (kodda os.environ.get("PORT", 7860) o'qiladi)
ENV PORT=7860

# Xavfsizlik uchun root emas foydalanuvchi
RUN useradd -m -u 1000 user
USER user
ENV PATH="/home/user/.local/bin:$PATH"

WORKDIR /app

# Avval kutubxonalar ro'yxatini nusxalaymiz (cache uchun)
COPY --chown=user requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Loyiha fayllarini nusxalaymiz (.dockerignore maxfiy fayllarni chiqarib tashlaydi)
COPY --chown=user . /app/

# Sessiya va yuklamalar papkalari uchun ruxsat
RUN mkdir -p /app/sessions /app/downloads

EXPOSE 7860

CMD ["python", "telegram_real_connection_script.py"]
