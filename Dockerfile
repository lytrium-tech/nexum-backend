# syntax=docker/dockerfile:1
FROM python:3.13-slim

LABEL maintainer="Lytrium <hola@lytrium.co>"
LABEL description="Nexum Backend — Sistema operativo financiero personal"

# Variables de entorno para Python
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Instalar uv
RUN pip install uv

# Copiar archivos de dependencias
COPY pyproject.toml README.md uv.lock .

# Instalar dependencias (sin dev)
RUN uv sync --no-dev

# Copiar código fuente
COPY app/ ./app/
COPY scripts/ ./scripts/

# Usuario no-root para seguridad
RUN addgroup --system nexum && adduser --system --group nexum
USER nexum

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
