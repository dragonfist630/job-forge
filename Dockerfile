FROM python:3.11-slim

# Node.js 20
RUN apt-get update && apt-get install -y curl gnupg && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y nodejs && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python deps (separate layer for cache efficiency)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Node deps + Playwright Chromium for PDF generation
COPY package.json package-lock.json* ./
RUN npm ci && npx playwright install chromium --with-deps

# App code
COPY . .

EXPOSE 7070
ENV PYTHONUNBUFFERED=1
ENV JOBFORGE_DOCKER=1

CMD ["python", "main.py"]
