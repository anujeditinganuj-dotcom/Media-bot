FROM python:3.11-slim

# System dependencies + Node.js (YouTube JS runtime ke liye)
RUN apt-get update && apt-get install -y \
    ffmpeg \
    curl \
    git \
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install all dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Override yt-dlp with latest from GitHub
RUN pip install --no-cache-dir --force-reinstall \
    "https://github.com/yt-dlp/yt-dlp/archive/refs/heads/master.zip#egg=yt-dlp" \
    && yt-dlp --version

# Copy bot code
COPY . .

ENV PORT=10000
EXPOSE 10000

CMD ["python", "bot.py"]
