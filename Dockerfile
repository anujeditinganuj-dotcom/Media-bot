FROM node:20-slim

# Python + ffmpeg + Deno (yt-dlp JS challenge solver)
RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    python3-venv \
    ffmpeg \
    curl \
    git \
    unzip \
    && rm -rf /var/lib/apt/lists/*

# Deno install (yt-dlp JS runtime ke liye)
RUN curl -fsSL https://deno.land/install.sh | sh
ENV DENO_INSTALL="/root/.deno"
ENV PATH="$DENO_INSTALL/bin:$PATH"

# Python symlinks
RUN ln -sf /usr/bin/python3 /usr/bin/python && \
    ln -sf /usr/bin/pip3 /usr/bin/pip

WORKDIR /app

# Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --break-system-packages -r requirements.txt

# yt-dlp latest from GitHub
RUN pip install --no-cache-dir --break-system-packages --force-reinstall \
    "https://github.com/yt-dlp/yt-dlp/archive/refs/heads/master.zip#egg=yt-dlp"

# Verify
RUN node --version && deno --version && yt-dlp --version

# Bot code
COPY . .

ENV PORT=10000
EXPOSE 10000

CMD ["python", "bot.py"]
