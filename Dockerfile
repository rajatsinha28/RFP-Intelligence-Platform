FROM node:22-bookworm-slim AS frontend-build
WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/home/rag \
    NLTK_DATA=/usr/local/share/nltk_data
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl libgl1 libglib2.0-0 poppler-utils tesseract-ocr tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml README.md requirements.txt ./
COPY app/__init__.py app/__init__.py
COPY evaluation/__init__.py evaluation/__init__.py
RUN pip install --upgrade pip \
    && pip install --index-url https://download.pytorch.org/whl/cpu 'torch>=2.2,<3' \
    && pip install '.[evaluation]' \
    && python -m nltk.downloader -d "$NLTK_DATA" punkt_tab stopwords
COPY app ./app
COPY evaluation ./evaluation
COPY tests ./tests
COPY alembic.ini ./
COPY alembic ./alembic
COPY scripts ./scripts
COPY examples ./examples
COPY --from=frontend-build /frontend/dist ./app/static
RUN addgroup --system rag && adduser --system --ingroup rag --home /home/rag rag \
    && mkdir -p /data/documents /app/reports /home/rag/.cache \
    && chown -R rag:rag /data /app /home/rag
USER rag
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
