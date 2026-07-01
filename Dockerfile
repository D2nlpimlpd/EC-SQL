FROM python:3.10-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV APP_ENTRY=ecsql_service.py
ENV HOST=0.0.0.0
ENV PORT=5000

RUN apt-get update && \
    apt-get install -y --no-install-recommends git curl build-essential && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-raganything.txt requirements-server.txt ./
RUN python -m pip install --upgrade pip wheel setuptools && \
    python -m pip install --no-cache-dir -r requirements.txt && \
    python -m pip install --no-cache-dir -r requirements-raganything.txt && \
    python -m pip install --no-cache-dir -r requirements-server.txt

COPY . .

RUN if [ -d third_party/raganything-1.3.1 ]; then \
      python -m pip install --no-cache-dir -e third_party/raganything-1.3.1; \
    fi

EXPOSE 5000

CMD ["sh", "-c", "python ${APP_ENTRY}"]
