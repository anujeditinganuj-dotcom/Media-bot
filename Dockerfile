# Node.js 20 base (Python ke saath) - yt-dlp JS runtime ke liye Node 18+ chahiye
FROM node:20-slim

# Python install karo
RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    python3-venv \
    ffmpeg \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# pip ko python3 se link karo
RUN ln -sf /usr/bin/python3 /usr/bin/python && \
    ln -sf /usr/bin/pip3 /usr/bin/pip

WORKDIR /app

# Python dependencies install karo
COPY requirements.txt .
RUN pip install --no-cache-dir --break-system-packages -r requirements.txt

# yt-dlp latest GitHub se (PyPI version issue bypass)
RUN pip install --no-cache-dir --break-system-packages --force-reinstall \
    "https://github.com/yt-dlp/yt-dlp/archive/refs/heads/master.zip#egg=yt-dlp"

# Verify versions
RUN node --version && yt-dlp --version

# Bot code copy karo
COPY . .

ENV PORT=10000
EXPOSE 10000

CMD ["python", "bot.py"]
