FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/src

WORKDIR /app

RUN adduser --disabled-password --gecos "" botuser \
    && mkdir -p /data \
    && chown botuser:botuser /data

COPY src ./src
COPY README.md ./

USER botuser

CMD ["python", "-m", "md_channel_bot"]
