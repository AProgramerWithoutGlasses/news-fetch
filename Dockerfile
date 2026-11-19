FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY news_fetch /app/news_fetch
RUN useradd --uid 10001 --create-home news && mkdir -p /app/data /app/reports && chown -R news:news /app
USER news
CMD ["python", "-m", "news_fetch", "daemon"]
