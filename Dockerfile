FROM python:3.12-slim AS builder

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

FROM python:3.12-slim

ARG UID=1000
ARG GID=1000

WORKDIR /app

RUN groupadd -r -g ${GID} appuser && useradd -r -u ${UID} -g appuser appuser

COPY --from=builder /root/.local /home/appuser/.local
COPY ./app ./app
COPY ./alembic ./alembic
COPY ./alembic.ini ./alembic.ini
COPY ./pyproject.toml ./pyproject.toml
COPY ./tests ./tests

RUN chown -R appuser:appuser /app
USER appuser

ENV PATH=/home/appuser/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]