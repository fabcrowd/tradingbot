FROM python:3.11-slim

WORKDIR /app

COPY bots/polymarket_bot/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY bots/polymarket_bot /app/bots/polymarket_bot

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

EXPOSE 8091

CMD ["python", "-m", "bots.polymarket_bot.src.runner"]
