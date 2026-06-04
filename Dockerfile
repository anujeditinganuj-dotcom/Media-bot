FROM node:20-slim

# System packages
RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    python3-venv \
    ffmpeg \
    curl \
    git \
    unzip \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Python symlinks
RUN ln -sf /usr/bin/python3 /usr/bin/python && \
    ln -sf /usr/bin/pip3 /usr/bin/pip

# Deno (JS runtime for yt-dlp challenge solving)
RUN curl -fsSL https://deno.land/install.sh | sh

ENV DENO_INSTALL=/root/.deno
ENV PATH=${DENO_INSTALL}/bin:${PATH}

WORKDIR /app

# Python dependencies
COPY requirements.txt .

RUN pip install --upgrade pip setuptools wheel --break-system-packages

RUN pip install --no-cache-dir --break-system-packages \
    -r requirements.txt

# Extra yt-dlp dependencies
RUN pip install --no-cache-dir --break-system-packages \
    brotli \
    websockets \
    curl-cffi \
    certifi

# Install latest yt-dlp directly from GitHub
RUN pip uninstall -y yt-dlp || true

RUN pip install --no-cache-dir --break-system-packages \
    "https://github.com/yt-dlp/yt-dlp/archive/master.zip"

# Verify installations
RUN python --version && \
    pip --version && \
    node --version && \
    deno --version && \
    yt-dlp --version && \
    ffmpeg -version

# Copy bot source
COPY . .

ENV PYTHONUNBUFFERED=1
ENV YT_DLP_NO_UPDATE=1
ENV PORT=5000

EXPOSE 5000

CMD ["python", "bot.py"]
