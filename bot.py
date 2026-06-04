import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from math import floor
import threading
import uuid
from pathlib import Path
from urllib.parse import urlparse
import aiohttp

# =========================
# Developer: @anujbyedit
# =========================

from flask import Flask, request
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.request import HTTPXRequest

# =========================
# Settings (from environment variables)
# =========================
BOT_TOKEN        = os.environ.get("BOT_TOKEN", "8741784728:AAFLpwz7UZvEUumoxgO2I7ii8Lo-9ZSpa1o")
BOT_USERNAME     = os.environ.get("BOT_USERNAME", "terabox_video_down_bot")
WEBHOOK_URL      = os.environ.get("WEBHOOK_URL", "")
PORT             = int(os.environ.get("PORT", 5000))

ADMIN_USER_ID    = int(os.environ.get("ADMIN_USER_ID", "7168219724"))

# YouTube Data API v3 key (optional - cookies fail hone par fallback use hoga)
YOUTUBE_API_KEY  = os.environ.get("YOUTUBE_API_KEY", "AIzaSyCGfwA660Ba65cheWLn8ybj7eIbA4xhPQ0")

REQUIRED_CHANNEL_USERNAME = os.environ.get("REQUIRED_CHANNEL_USERNAME", "@log_ak_bots")
REQUIRED_CHANNEL_URL      = os.environ.get("REQUIRED_CHANNEL_URL", "https://t.me/log_ak_bots")

INSTAGRAM_COOKIE_FILE = "downloads/instagram_cookies.txt"
TIKTOK_COOKIE_FILE    = "downloads/tiktok_cookies.txt"
YOUTUBE_COOKIE_FILE   = "downloads/youtube_cookies.txt"
FACEBOOK_COOKIE_FILE  = "downloads/facebook_cookies.txt"

BASE_DIR     = Path(__file__).resolve().parent
DOWNLOAD_DIR = BASE_DIR / "downloads"
DOWNLOAD_DIR.mkdir(exist_ok=True)

STATS_FILE = BASE_DIR / "bot_stats.json"

# =========================
# Cookie Management System
# =========================
COOKIE_FILES = {
    "youtube":   BASE_DIR / "downloads/youtube_cookies.txt",
    "instagram": BASE_DIR / "downloads/instagram_cookies.txt",
    "facebook":  BASE_DIR / "downloads/facebook_cookies.txt",
    "tiktok":    BASE_DIR / "downloads/tiktok_cookies.txt",
}

# State store for cookie upload flow
_cookie_pending: dict[int, str] = {}   # user_id -> platform


def get_cookie_expiry_info() -> dict[str, dict]:
    """Parse each cookie file and return expiry info per platform."""
    import time as _time
    now = int(_time.time())
    result = {}
    for platform, path in COOKIE_FILES.items():
        if not path.exists():
            result[platform] = {"status": "missing", "days_left": None, "expires": None}
            continue
        min_exp = None
        valid = False
        try:
            for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) >= 7:
                    valid = True
                    try:
                        exp = int(parts[4])
                        if exp > 0:
                            if min_exp is None or exp < min_exp:
                                min_exp = exp
                    except ValueError:
                        pass
        except Exception:
            pass
        if not valid:
            result[platform] = {"status": "empty", "days_left": None, "expires": None}
        elif min_exp is None:
            result[platform] = {"status": "ok_session", "days_left": None, "expires": "Session only"}
        else:
            days_left = (min_exp - now) // 86400
            if days_left < 0:
                result[platform] = {"status": "expired", "days_left": days_left, "expires": min_exp}
            elif days_left < 7:
                result[platform] = {"status": "expiring_soon", "days_left": days_left, "expires": min_exp}
            else:
                result[platform] = {"status": "ok", "days_left": days_left, "expires": min_exp}
    return result


def format_cookie_status_text() -> str:
    import time as _time
    info = get_cookie_expiry_info()
    icons = {
        "ok":           "✅",
        "ok_session":   "✅",
        "expiring_soon":"⚠️",
        "expired":      "❌",
        "missing":      "🚫",
        "empty":        "🚫",
    }
    lines = ["🍪 <b>Cookie Status</b>\n"]
    for platform, data in info.items():
        icon = icons.get(data["status"], "❓")
        name = platform.capitalize()
        status = data["status"]
        if status in ("missing", "empty"):
            lines.append(f"{icon} <b>{name}</b>: Not found")
        elif status == "expired":
            lines.append(f"{icon} <b>{name}</b>: EXPIRED {abs(data['days_left'])} days ago")
        elif status == "expiring_soon":
            lines.append(f"{icon} <b>{name}</b>: Expires in {data['days_left']} days ⚠️")
        elif status == "ok_session":
            lines.append(f"{icon} <b>{name}</b>: Active (session cookie)")
        else:
            lines.append(f"{icon} <b>{name}</b>: Valid — {data['days_left']} days left")
    lines.append("")
    lines.append("📋 <b>Commands:</b>")
    lines.append("/setcookies youtube — YouTube cookies update karo")
    lines.append("/setcookies instagram — Instagram cookies update karo")
    lines.append("/setcookies facebook — Facebook cookies update karo")
    lines.append("/setcookies tiktok — TikTok cookies update karo")
    lines.append("/cookies — Yeh status dobara dekho")
    return "\n".join(lines)



MAX_CONCURRENT_DOWNLOADS = 4

# ─── Timeout settings ─────────────────────────────────────────────────────
# Download: 2GB file pe ~2 hours lag sakti hai slow connection pe
DOWNLOAD_TIMEOUT = 7200   # 2 hours

# Upload: Telegram pe 2GB upload = ~30-40 min
UPLOAD_READ_TIMEOUT    = 3600   # 1 hour
UPLOAD_WRITE_TIMEOUT   = 3600   # 1 hour
UPLOAD_CONNECT_TIMEOUT = 60
UPLOAD_POOL_TIMEOUT    = 60

# download_semaphore initialized in build_application() to avoid event-loop issues
download_semaphore: asyncio.Semaphore  # assigned at startup

FILE_CAPTION_BASE = "Downloaded by @anujbyedit 🤖\n🚀 Bot without ads: @url_ak_uploader_bot"


def build_video_caption(info: dict | None) -> str:
    if not info:
        return FILE_CAPTION_BASE

    title    = (info.get("title") or info.get("description") or "")[:80]
    channel  = info.get("uploader") or info.get("channel") or info.get("creator") or ""
    handle   = info.get("uploader_id") or info.get("channel_id") or ""
    views    = info.get("view_count") or 0
    duration = info.get("duration") or 0
    likes    = info.get("like_count") or 0
    comments = info.get("comment_count") or 0
    shares   = info.get("repost_count") or 0
    subs     = info.get("channel_follower_count") or info.get("uploader_follower_count") or 0
    category_list = info.get("categories") or []
    category = category_list[0] if category_list else ""
    upload_date = info.get("upload_date") or ""
    if upload_date and len(upload_date) == 8:
        upload_date = f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:]}"

    lines = []
    if title:
        lines.append(f"🎬 {title} →")
    if channel:
        lines.append(f"👤 {channel}")
    if handle and handle != channel:
        clean_handle = handle.lstrip('@')
        lines.append(f"@{clean_handle} ✓ →")
    if subs:
        lines.append(f"👥 {subs:,}")
    if duration:
        lines.append(f"🕐 {format_duration(int(duration))}")

    stats_parts = []
    if views:
        stats_parts.append(f"👁 {views:,}")
    if likes:
        stats_parts.append(f"👍 {likes:,}")
    if comments:
        stats_parts.append(f"💬 {comments:,}")
    if shares:
        stats_parts.append(f"🔁 {shares:,}")
    if stats_parts:
        lines.append(" | ".join(stats_parts))

    if category:
        lines.append(f"🏷 {category}")
    if upload_date:
        lines.append(f"📅 {upload_date}")

    lines.append("")
    lines.append(FILE_CAPTION_BASE)
    return "\n".join(lines)


TG_MAX_FILE_SIZE  = 2000 * 1024 * 1024   # 2 GB
TG_STANDARD_LIMIT =   50 * 1024 * 1024   # 50 MB (inline play limit)

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}
AUDIO_EXTS = {".mp3", ".m4a", ".aac", ".flac", ".opus", ".ogg"}

QUALITY_OPTIONS = ["best", "1080p", "720p", "480p", "360p", "audio_only"]
QUALITY_LABELS  = {
    "best":       "🔥 Best Quality",
    "1080p":      "🖥 1080p (FHD)",
    "720p":       "📺 720p (HD)",
    "480p":       "📱 480p (SD)",
    "360p":       "📉 360p (Low)",
    "audio_only": "🎵 Audio Only (MP3)",
}

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("Downloader-Bot")

flask_app = Flask(__name__)


# =========================
# Persistent Stats Storage
# =========================
class StatsStore:
    def __init__(self, path: Path):
        self.path = path
        self._lock: asyncio.Lock | None = None
        self.data = self._load()

    @property
    def lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    def _default_data(self) -> dict:
        return {"total_downloads": 0, "users": {}}

    def _load(self) -> dict:
        if not self.path.exists():
            data = self._default_data()
            self._save_sync(data)
            return data
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("Stats file invalid")
            data.setdefault("total_downloads", 0)
            data.setdefault("users", {})
            if not isinstance(data["users"], dict):
                data["users"] = {}
            return data
        except Exception as e:
            logger.warning("Could not load stats, recreating: %s", e)
            data = self._default_data()
            self._save_sync(data)
            return data

    def _save_sync(self, data: dict) -> None:
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    async def save(self) -> None:
        async with self.lock:
            self._save_sync(self.data)

    async def register_user(self, user) -> bool:
        user_id = str(user.id)
        async with self.lock:
            is_new = user_id not in self.data["users"]
            self.data["users"][user_id] = {
                "id":         user.id,
                "username":   user.username or "",
                "first_name": user.first_name or "",
                "last_name":  user.last_name or "",
            }
            self._save_sync(self.data)
            return is_new

    async def increment_downloads(self) -> None:
        async with self.lock:
            self.data["total_downloads"] = int(self.data.get("total_downloads", 0)) + 1
            self._save_sync(self.data)

    async def get_stats(self) -> dict:
        async with self.lock:
            return {
                "total_users":     len(self.data.get("users", {})),
                "total_downloads": int(self.data.get("total_downloads", 0)),
            }


stats_store = StatsStore(STATS_FILE)

# =========================
# URL Store (with TTL to prevent memory leak)
# =========================
_url_store: dict[str, tuple[str, str, float]] = {}   # key -> (url, platform, timestamp)
_STORE_TTL = 3600   # 1 hour — expired entries auto-purge

def _purge_expired_store(store: dict, ttl: float) -> None:
    now = time.time()
    expired = [k for k, v in store.items() if now - v[-1] > ttl]
    for k in expired:
        store.pop(k, None)

def store_url(url: str, platform: str) -> str:
    _purge_expired_store(_url_store, _STORE_TTL)
    key = uuid.uuid4().hex[:8]
    _url_store[key] = (url, platform, time.time())
    return key

def get_url(key: str) -> tuple[str, str] | None:
    entry = _url_store.get(key)
    if entry is None:
        return None
    return (entry[0], entry[1])

def cleanup_url(key: str) -> None:
    _url_store.pop(key, None)

# =========================
# YouTube Search Store (with TTL)
# =========================
_search_store: dict[str, tuple[list[dict], float]] = {}   # key -> (results, timestamp)

def store_search_results(results: list[dict]) -> str:
    _purge_expired_store(_search_store, _STORE_TTL)
    key = uuid.uuid4().hex[:8]
    _search_store[key] = (results, time.time())
    return key

def get_search_results(key: str) -> list[dict] | None:
    entry = _search_store.get(key)
    if entry is None:
        return None
    return entry[0]

def cleanup_search_results(key: str) -> None:
    _search_store.pop(key, None)


# =========================
# Helpers
# =========================
def extract_first_url(text: str) -> str | None:
    if not text:
        return None
    match = re.search(r"https?://[^\s]+", text)
    return match.group(0).strip() if match else None


def is_search_query(text: str) -> bool:
    if not text or not text.strip():
        return False
    if re.search(r"https?://", text):
        return False
    if text.startswith("/"):
        return False
    stripped = text.strip()
    words = stripped.split()
    if len(words) >= 2:
        return True
    if len(stripped) >= 3:
        return True
    return False


def get_platform(url: str) -> str | None:
    try:
        host = (urlparse(url).netloc or "").lower()
    except Exception:
        return None

    if host in {"instagram.com", "www.instagram.com"}:
        return "instagram"
    if host in {"tiktok.com", "www.tiktok.com", "m.tiktok.com", "vm.tiktok.com", "vt.tiktok.com"}:
        return "tiktok"
    if host in {"youtube.com", "www.youtube.com", "youtu.be", "m.youtube.com"}:
        return "youtube"
    if host in {"pinterest.com", "www.pinterest.com", "pin.it", "pinterest.co.uk"}:
        return "pinterest"
    if host in {"snapchat.com", "www.snapchat.com"}:
        return "snapchat"
    if host in {"likee.video", "www.likee.video", "like.video"}:
        return "likee"
    if host in {"vk.com", "www.vk.com", "vkvideo.ru", "www.vkvideo.ru"}:
        return "vk"
    if host in {"facebook.com", "www.facebook.com", "m.facebook.com", "fb.watch"}:
        return "facebook"
    if host in {"threads.net", "www.threads.net"}:
        return "threads"
    if host in {
        "soundcloud.com", "www.soundcloud.com", "on.soundcloud.com",
        "open.spotify.com",
        "deezer.com", "www.deezer.com",
        "music.apple.com",
    }:
        return "music"
    return None


VIDEO_QUALITY_PLATFORMS = {"youtube", "instagram", "tiktok", "facebook", "vk", "snapchat", "likee", "threads"}
SKIP_QUALITY_PLATFORMS  = {"pinterest", "music"}
YTDLP_PLATFORMS         = {"youtube", "facebook", "vk", "snapchat", "likee", "music"}
GALLERY_DL_PREFERRED    = {"instagram", "tiktok", "pinterest", "threads"}


def format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def format_duration(seconds: int) -> str:
    if seconds < 0:
        return "??:??"
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def format_speed(bytes_per_sec: float) -> str:
    if bytes_per_sec <= 0:
        return "Starting up..."
    elif bytes_per_sec < 1024:
        return f"{bytes_per_sec:.0f} B/s"
    elif bytes_per_sec < 1024 * 1024:
        return f"{bytes_per_sec / 1024:.1f} KB/s"
    else:
        return f"{bytes_per_sec / (1024 * 1024):.1f} MB/s"


def build_progress_bar(done, total=100, width=10):
    if not total or total <= 0:
        dots = int((time.time() * 2) % (width + 1))
        bar = "⬢" * dots + "⬡" * (width - dots)
    else:
        filled = min(width, floor(width * done / total))
        bar = "⬢" * filled + "⬡" * (width - filled)
    return f"[{bar}]"


def build_welcome_text(first_name: str | None) -> str:
    name = (first_name or "there").strip()
    return (
        f"🤝 Hello {name}\n\n"
        "📥 I can help you download videos and images from:\n\n"
        "▶️ YouTube\n"
        "📷 Instagram\n"
        "🎵 TikTok\n"
        "📍 Pinterest\n"
        "👻 Snapchat\n"
        "💛 Likee\n"
        "🔷 VK\n"
        "💬 Facebook\n"
        "🔘 Threads\n"
        "🎶 Music\n\n"
        "🔍 <b>YouTube Search:</b> Koi bhi song ya movie ka naam type karo, seedha download milega!\n"
        "   Example: <code>haseen dillruba song</code> ya <code>liger trailer</code>\n\n"
        "• To download a video, send me a link to the video or image\n\n"
        "<i>(The bot also works in groups, if you want to use it in a group, press the button 👇)</i>"
    )


def join_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Join Channel 📢", url=REQUIRED_CHANNEL_URL)],
        [InlineKeyboardButton("I Joined ✅", callback_data="check_join")],
    ])


def welcome_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "➕ Add to Group",
            url=f"https://t.me/{BOT_USERNAME}?startgroup=true",
        )],
    ])


def quality_keyboard(url_key: str) -> InlineKeyboardMarkup:
    buttons = []
    for q in QUALITY_OPTIONS:
        callback = f"q|{q}|{url_key}"
        if q == "audio_only":
            buttons.append([InlineKeyboardButton("🎵 Download music from video", callback_data=callback)])
        else:
            buttons.append([InlineKeyboardButton(QUALITY_LABELS[q], callback_data=callback)])
    return InlineKeyboardMarkup(buttons)


def search_results_keyboard(results: list[dict], search_key: str) -> InlineKeyboardMarkup:
    buttons = []
    for i, r in enumerate(results, 1):
        dur_str = format_duration(int(r["duration"])) if r["duration"] else ""
        title_short = r["title"][:38]
        if len(r["title"]) > 38:
            title_short += "…"
        label = f"{i}. {title_short}"
        if dur_str:
            label += f" [{dur_str}]"
        buttons.append([InlineKeyboardButton(label, callback_data=f"sr|{i-1}|{search_key}")])
    buttons.append([InlineKeyboardButton("❌ Cancel", callback_data="sr_cancel")])
    return InlineKeyboardMarkup(buttons)


def media_priority(path: Path) -> tuple[int, str]:
    ext = path.suffix.lower()
    if ext in VIDEO_EXTS:  return (0, path.name)
    if ext in IMAGE_EXTS:  return (1, path.name)
    if ext in AUDIO_EXTS:  return (2, path.name)
    return (3, path.name)


def collect_media_files(root: Path) -> list[Path]:
    all_exts = IMAGE_EXTS | VIDEO_EXTS | AUDIO_EXTS
    files = [p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in all_exts]
    files.sort(key=media_priority)
    return files


def build_gallery_dl_command(url: str, temp_dir: Path, platform: str) -> list[str]:
    command = [
        "gallery-dl",
        "--directory", str(temp_dir),
        "--no-mtime",
        "--retries", "3",
        "--timeout", "30",
    ]

    # Pinterest ke liye video format explicitly set karo
    if platform == "pinterest":
        command += [
            "--config-option", "extractor.pinterest.videos=true",
            "--config-option", "extractor.pinterest.video-format=best",
        ]

    command.append(url)

    cookie_map = {
        "instagram": INSTAGRAM_COOKIE_FILE,
        "facebook":  FACEBOOK_COOKIE_FILE,
        "tiktok":    TIKTOK_COOKIE_FILE,
        "youtube":   YOUTUBE_COOKIE_FILE,
    }
    cf = cookie_map.get(platform)
    if cf:
        cp = BASE_DIR / cf
        if cp.exists():
            command[1:1] = ["--cookies", str(cp)]
    return command


def build_ytdlp_instagram_command(url: str, temp_dir: Path) -> list[str]:
    output_template = str(temp_dir / "%(title).50s.%(ext)s")
    command = [
        "yt-dlp",
        "--no-check-certificates",
        "--retries", "3",
        "--socket-timeout", "60",
        "--add-header", "User-Agent:Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
        "--add-header", "Accept-Language:en-US,en;q=0.9",
        "-o", output_template,
        url,
    ]
    cp = BASE_DIR / INSTAGRAM_COOKIE_FILE
    if cp.exists():
        command[1:1] = ["--cookies", str(cp)]
    return command


def build_ytdlp_command(url: str, temp_dir: Path, platform: str, quality: str = "best") -> list[str]:
    output_template = str(temp_dir / "%(title).50s.%(ext)s")

    common_flags = []
    if platform == "youtube":
        common_flags = [
            # 2026 fix: web_creator + android bypass signature/JS runtime errors
            "--extractor-args",
            "youtube:player_client=tv_embedded,android,ios;skip=webpage,configs",
            "--no-check-certificates",
            "--retries", "10",
            "--fragment-retries", "10",
            "--retry-sleep", "exp=2",
            "--socket-timeout", "60",
            "--concurrent-fragments", "4",
            "--sleep-interval", "1",
            "--max-sleep-interval", "3",
            "--add-header", "User-Agent:Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.6367.82 Mobile Safari/537.36",
            "--compat-options", "no-youtube-unavailable-videos",
        ]
    elif platform == "facebook":
        common_flags = [
            "--no-check-certificates",
            "--retries", "10",
            "--fragment-retries", "10",
            "--retry-sleep", "3",
            "--socket-timeout", "60",
            "--buffer-size", "16K",
            "--add-header", "User-Agent:Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            "--add-header", "Accept-Language:en-US,en;q=0.9",
            "--add-header", "Accept:text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        ]
    elif platform == "tiktok":
        common_flags = [
            "--no-check-certificates",
            "--retries", "5",
            "--socket-timeout", "60",
            "--add-header", "User-Agent:Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
            "--add-header", "Accept-Language:en-US,en;q=0.9",
            "--add-header", "Referer:https://www.tiktok.com/",
        ]
    else:
        # Generic flags for other platforms
        common_flags = [
            "--no-check-certificates",
            "--retries", "5",
            "--socket-timeout", "60",
        ]

    if quality == "audio_only":
        command = [
            "yt-dlp",
            *common_flags,
            "--format", "bestaudio/best",
            "--extract-audio",
            "--audio-format", "mp3",
            "--audio-quality", "0",
            "-o", output_template,
            url,
        ]
    else:
        fmt_map = {
            "best":  "bestvideo+bestaudio/best",
            "1080p": "bestvideo[height<=1080]+bestaudio/best[height<=1080]",
            "720p":  "bestvideo[height<=720]+bestaudio/best[height<=720]",
            "480p":  "bestvideo[height<=480]+bestaudio/best[height<=480]",
            "360p":  "bestvideo[height<=360]+bestaudio/best[height<=360]",
        }
        fmt = fmt_map.get(quality, fmt_map["best"])
        command = [
            "yt-dlp",
            *common_flags,
            "--format", fmt,
            "--merge-output-format", "mp4",
            "-o", output_template,
            url,
        ]

    cookie_map = {
        "youtube":   YOUTUBE_COOKIE_FILE,
        "facebook":  FACEBOOK_COOKIE_FILE,
        "instagram": INSTAGRAM_COOKIE_FILE,
        "tiktok":    TIKTOK_COOKIE_FILE,
    }
    cf = cookie_map.get(platform)
    if cf:
        cp = BASE_DIR / cf
        if cp.exists():
            command[1:1] = ["--cookies", str(cp)]
    return command


def build_ytdlp_info_command(url: str, platform: str) -> list[str]:
    common_flags = []
    if platform == "youtube":
        common_flags = [
            "--extractor-args",
            "youtube:player_client=tv_embedded,android,ios;skip=webpage,configs",
            "--no-check-certificates",
            "--socket-timeout", "30",
            "--add-header", "User-Agent:Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.6367.82 Mobile Safari/537.36",
            "--compat-options", "no-youtube-unavailable-videos",
        ]
    elif platform == "facebook":
        common_flags = [
            "--no-check-certificates",
            "--add-header", "User-Agent:Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "--add-header", "Accept-Language:en-US,en;q=0.9",
        ]
    elif platform == "instagram":
        common_flags = [
            "--no-check-certificates",
            "--add-header", "User-Agent:Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
            "--add-header", "Accept-Language:en-US,en;q=0.9",
        ]
    elif platform == "tiktok":
        common_flags = [
            "--no-check-certificates",
            "--add-header", "User-Agent:Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
            "--add-header", "Accept-Language:en-US,en;q=0.9",
        ]
    elif platform in {"vk", "snapchat", "likee", "threads"}:
        common_flags = [
            "--no-check-certificates",
            "--add-header", "User-Agent:Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        ]
    elif platform == "music":
        common_flags = ["--no-check-certificates"]

    command = [
        "yt-dlp",
        *common_flags,
        "--dump-json",
        "--no-playlist",
        url,
    ]

    cookie_map = {
        "youtube":   YOUTUBE_COOKIE_FILE,
        "facebook":  FACEBOOK_COOKIE_FILE,
        "instagram": INSTAGRAM_COOKIE_FILE,
        "tiktok":    TIKTOK_COOKIE_FILE,
    }
    cf = cookie_map.get(platform)
    if cf:
        cp = BASE_DIR / cf
        if cp.exists():
            command[1:1] = ["--cookies", str(cp)]
    return command


def safe_remove_tree(path: Path | None) -> None:
    if not path:
        return
    try:
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
    except Exception as e:
        logger.warning("Could not delete temp folder %s: %s", path, e)


async def safe_edit_text(message, text: str, reply_markup=None):
    try:
        await message.edit_text(text, reply_markup=reply_markup, parse_mode="HTML")
    except Exception:
        pass


async def is_user_joined(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    try:
        member = await context.bot.get_chat_member(
            chat_id=REQUIRED_CHANNEL_USERNAME, user_id=user_id
        )
        return getattr(member, "status", "") not in {"left", "kicked", "banned"}
    except Exception as e:
        logger.warning("Could not verify membership for %s: %s", user_id, e)
        return False


async def require_join(update: Update, context: ContextTypes.DEFAULT_TYPE, pending_action: dict) -> bool:
    user = update.effective_user
    if not user:
        return True
    if await is_user_joined(context, user.id):
        return False

    context.user_data["pending_action"] = pending_action
    text = f"You must join our channel first to use this bot.\n\nChannel: {REQUIRED_CHANNEL_USERNAME}"

    if update.callback_query:
        await update.callback_query.answer()
        try:
            await update.callback_query.message.reply_text(text, reply_markup=join_keyboard())
        except Exception:
            pass
    else:
        msg = update.effective_message
        if msg:
            await msg.reply_text(text, reply_markup=join_keyboard())
    return True


async def notify_admin_new_user(context: ContextTypes.DEFAULT_TYPE, user) -> None:
    if not ADMIN_USER_ID:
        return
    try:
        username  = f"@{user.username}" if user.username else "No username"
        full_name = " ".join(p for p in [user.first_name or "", user.last_name or ""] if p).strip() or "No name"
        await context.bot.send_message(
            chat_id=ADMIN_USER_ID,
            text=(
                "👤 New user joined the bot\n\n"
                f"Name: {full_name}\n"
                f"Username: {username}\n"
                f"User ID: {user.id}"
            ),
        )
    except Exception as e:
        logger.warning("Could not notify admin: %s", e)


async def register_user_and_notify(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        return
    is_new = await stats_store.register_user(user)
    if is_new:
        await notify_admin_new_user(context, user)


# =========================
# YouTube Search
# =========================
async def _search_youtube_via_api(query: str, max_results: int = 8) -> list[dict]:
    """YouTube Data API v3 se search karo — cookies fail hone par fallback."""
    if not YOUTUBE_API_KEY:
        return []
    try:
        params = {
            "part": "snippet",
            "q": query,
            "type": "video",
            "maxResults": max_results,
            "key": YOUTUBE_API_KEY,
        }
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://www.googleapis.com/youtube/v3/search",
                params=params,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    logger.error("YouTube API error status: %s", resp.status)
                    return []
                data = await resp.json()

        results = []
        video_ids = []
        items = data.get("items", [])
        for item in items:
            vid_id = item.get("id", {}).get("videoId", "")
            if vid_id:
                video_ids.append(vid_id)

        # Duration ke liye videos endpoint call karo
        durations = {}
        if video_ids:
            vparams = {
                "part": "contentDetails,statistics",
                "id": ",".join(video_ids),
                "key": YOUTUBE_API_KEY,
            }
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    "https://www.googleapis.com/youtube/v3/videos",
                    params=vparams,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp2:
                    if resp2.status == 200:
                        vdata = await resp2.json()
                        def _iso_int(pattern, text):
                            match = re.search(pattern, text)
                            return int(match.group(1)) if match else 0
                        for v in vdata.get("items", []):
                            vid = v.get("id", "")
                            iso = v.get("contentDetails", {}).get("duration", "PT0S")
                            # ISO 8601 duration parse (PT1H2M3S)
                            h = _iso_int(r"(\d+)H", iso)
                            m_val = _iso_int(r"(\d+)M", iso)
                            s = _iso_int(r"(\d+)S", iso)
                            durations[vid] = h * 3600 + m_val * 60 + s

        for item in items:
            snippet = item.get("snippet", {})
            vid_id  = item.get("id", {}).get("videoId", "")
            if not vid_id:
                continue
            results.append({
                "title":    snippet.get("title", "Unknown Title"),
                "duration": durations.get(vid_id, 0),
                "channel":  snippet.get("channelTitle", ""),
                "url":      f"https://www.youtube.com/watch?v={vid_id}",
                "views":    0,  # search endpoint mein views nahi milte
                "id":       vid_id,
            })
        logger.info("YouTube API search successful: %d results", len(results))
        return results
    except Exception as e:
        logger.error("YouTube API search error: %s", e)
        return []


async def search_youtube(query: str, max_results: int = 8) -> list[dict]:
    search_url = f"ytsearch{max_results}:{query}"
    command = [
        "yt-dlp",
        "--extractor-args",
        "youtube:player_client=tv_embedded,android,ios;skip=webpage,configs",
        "--no-check-certificates",
        "--dump-json",
        "--no-playlist",
        "--flat-playlist",
        "--add-header", "User-Agent:Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.6367.82 Mobile Safari/537.36",
        search_url,
    ]
    cp = BASE_DIR / YOUTUBE_COOKIE_FILE
    if cp.exists():
        command[1:1] = ["--cookies", str(cp)]

    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(BASE_DIR),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=40)
        results = []
        if stdout:
            for line in stdout.decode(errors="replace").strip().split("\n"):
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    title    = data.get("title") or "Unknown Title"
                    duration = data.get("duration") or 0
                    channel  = data.get("channel") or data.get("uploader") or data.get("channel_id") or ""
                    vid_id   = data.get("id") or ""
                    url      = data.get("url") or data.get("webpage_url") or (f"https://www.youtube.com/watch?v={vid_id}" if vid_id else "")
                    views    = data.get("view_count") or 0
                    if not url:
                        continue
                    results.append({
                        "title":    title,
                        "duration": duration,
                        "channel":  channel,
                        "url":      url,
                        "views":    views,
                        "id":       vid_id,
                    })
                except Exception:
                    continue
        # yt-dlp se results mile to return karo
        if results:
            return results
        # Nahi mile (cookies block) to YouTube API try karo
        logger.warning("yt-dlp search returned no results, trying YouTube API fallback...")
        return await _search_youtube_via_api(query, max_results)
    except asyncio.TimeoutError:
        logger.error("YouTube search timeout for: %s — trying API fallback", query)
        return await _search_youtube_via_api(query, max_results)
    except Exception as e:
        logger.error("YouTube search error: %s — trying API fallback", e)
        return await _search_youtube_via_api(query, max_results)


def build_search_results_text(query: str, results: list[dict]) -> str:
    if not results:
        return (
            f"❌ <b>No results found for:</b> <code>{query}</code>\n\n"
            "Please try a different search term."
        )

    lines = [f"🔍 <b>Search results for:</b> <code>{query}</code>\n"]
    for i, r in enumerate(results, 1):
        dur_str   = format_duration(int(r["duration"])) if r["duration"] else "--:--"
        views_str = f"{r['views']:,}" if r.get("views") else ""
        chan      = (r.get("channel") or "")[:28]
        title     = r["title"][:55]

        line = f"<b>{i}.</b> 🎬 {title}\n"
        meta = []
        if chan:
            meta.append(f"👤 {chan}")
        meta.append(f"⏱ {dur_str}")
        if views_str:
            meta.append(f"👁 {views_str}")
        line += "    " + "  |  ".join(meta)
        lines.append(line)

    lines.append("\n👆 <i>Neeche buttons mein se apna result choose karo</i>")
    return "\n".join(lines)


# =========================
# Video Info Fetcher
# =========================
async def fetch_video_info(url: str, platform: str) -> dict | None:
    try:
        cmd = build_ytdlp_info_command(url, platform)
        process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(BASE_DIR),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=45)
        if process.returncode == 0 and stdout:
            return json.loads(stdout.decode(errors="replace").strip().split("\n")[0])
    except Exception as e:
        logger.warning("Could not fetch video info for %s: %s", url, e)
    return None


def parse_format_sizes(info: dict) -> dict[str, int]:
    sizes: dict[str, int] = {}
    formats = info.get("formats", [])

    quality_map = {
        1080: "1080p",
        720:  "720p",
        480:  "480p",
        360:  "360p",
        240:  "240p",
        144:  "144p",
    }

    for fmt in formats:
        h = fmt.get("height") or 0
        fs = fmt.get("filesize") or fmt.get("filesize_approx") or 0
        if h and fs:
            label = quality_map.get(h, f"{h}p")
            if label not in sizes or fs > sizes[label]:
                sizes[label] = fs

    audio_size = 0
    for fmt in formats:
        if fmt.get("vcodec") == "none" and (fmt.get("acodec") or "none") != "none":
            fs = fmt.get("filesize") or fmt.get("filesize_approx") or 0
            if fs > audio_size:
                audio_size = fs
    if audio_size:
        sizes["MP3"] = audio_size

    return sizes


def build_info_message(info: dict, platform: str, sizes: dict[str, int]) -> str:
    title    = (info.get("title") or "Unknown Title")[:80]
    channel  = info.get("uploader") or info.get("channel") or ""
    handle   = info.get("uploader_id") or info.get("channel_id") or ""
    views    = info.get("view_count") or 0
    duration = info.get("duration") or 0
    likes    = info.get("like_count") or 0
    comments = info.get("comment_count") or 0
    category_list = info.get("categories") or []
    category = category_list[0] if category_list else ""
    upload_date = info.get("upload_date") or ""
    if upload_date and len(upload_date) == 8:
        upload_date = f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:]}"

    lines = []
    lines.append(f"🎬 <b>{title}</b> →")

    if channel:
        lines.append(f"👤 {channel}")
    if handle and handle != channel:
        lines.append(f"@{handle.lstrip('@')} ✓ →")

    if views:
        lines.append(f"👥 {views:,}")
    if duration:
        lines.append(f"⏱ {format_duration(int(duration))}")

    stats_parts = []
    if views:
        stats_parts.append(f"👁 {views:,}")
    if likes:
        stats_parts.append(f"👍 {likes:,}")
    if comments:
        stats_parts.append(f"💬 {comments:,}")
    if stats_parts:
        lines.append(" | ".join(stats_parts))

    if category:
        lines.append(f"🏷 {category}")
    if upload_date:
        lines.append(f"📅 {upload_date}")

    if sizes:
        lines.append("")
        quality_order = ["1080p", "720p", "480p", "360p", "240p", "144p", "MP3"]
        best_shown = False
        for q in quality_order:
            if q in sizes:
                icon = "🚀" if not best_shown else "✅"
                best_shown = True
                size_label = format_size(sizes[q])
                warn = " ⚠️ Large" if sizes[q] > TG_STANDARD_LIMIT else ""
                lines.append(f"{icon}  {q} - {size_label}{warn}")

    lines.append("")
    lines.append("Formats for download 📥")

    return "\n".join(lines)


# =========================
# Progress Tracker
# =========================
class StatusProgress:
    def __init__(self, status_message):
        self.status_message = status_message
        self._task    = None
        self._stopped = False
        self._last_edit = 0
        self._MIN_EDIT_INTERVAL = 4   # 4 sec — Telegram rate limit safe

    async def _throttled_edit(self, text: str) -> None:
        now = time.time()
        if now - self._last_edit >= self._MIN_EDIT_INTERVAL:
            await safe_edit_text(self.status_message, text)
            self._last_edit = now

    async def start_downloading(self, filename: str = "", total_size: int = 0) -> None:
        async def runner():
            start_time = time.time()
            last_bytes = 0
            checkpoints = [2, 5, 9, 14, 20, 27, 35, 44, 54, 65, 75, 84, 90, 94]
            for pct in checkpoints:
                if self._stopped:
                    return
                elapsed = time.time() - start_time
                if total_size > 0 and elapsed > 0:
                    downloaded = int(total_size * pct / 100)
                    speed = (downloaded - last_bytes) / max(elapsed, 1)
                    remaining_bytes = total_size - downloaded
                    eta = int(remaining_bytes / speed) if speed > 0 else 0
                    last_bytes = downloaded
                    speed_str = format_speed(speed)
                    eta_str = format_duration(eta)
                else:
                    speed_str = "Starting up..."
                    eta_str = "Calculating..."

                bar = build_progress_bar(pct)
                text = (
                    f"📥 <b>Downloading Video</b>\n\n"
                    f"┌─────《 Progress 》─────┐\n"
                    f"├» {bar} {pct}%\n"
                    f"├» 🚀 Speed: {speed_str}\n"
                    f"├» ⏱ ETA: {eta_str}\n"
                    f"└──────────────────────┘"
                )
                await self._throttled_edit(text)
                await asyncio.sleep(3)

            # Stay at 94% until done
            while not self._stopped:
                bar = build_progress_bar(94)
                text = (
                    f"📥 <b>Downloading Video</b>\n\n"
                    f"┌─────《 Progress 》─────┐\n"
                    f"├» {bar} 94%\n"
                    f"├» 🚀 Speed: Processing...\n"
                    f"├» ⏱ ETA: Almost done...\n"
                    f"└──────────────────────┘"
                )
                await self._throttled_edit(text)
                await asyncio.sleep(5)

        self._task = asyncio.create_task(runner())

    async def finish_downloading(self) -> None:
        self._stopped = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except BaseException:
                pass
        bar = build_progress_bar(100)
        await safe_edit_text(
            self.status_message,
            f"📥 <b>Downloading Video</b>\n\n"
            f"┌─────《 Progress 》─────┐\n"
            f"├» {bar} 100%\n"
            f"├» ✅ Download complete!\n"
            f"└──────────────────────┘"
        )

    async def set_uploading(
        self,
        percent: int,
        filename: str = "",
        uploaded_bytes: int = 0,
        total_bytes: int = 0,
        speed: float = 0,
        eta: int = 0,
        duration: int = 0,
        quality: str = "MP4",
    ) -> None:
        now = time.time()
        if now - self._last_edit < self._MIN_EDIT_INTERVAL:
            return
        self._last_edit = now

        bar = build_progress_bar(percent)
        short_name = filename[-35:] if filename else "video"
        uploaded_str = f"{format_size(uploaded_bytes)} / {format_size(total_bytes)}" if total_bytes else ""
        speed_str = format_speed(speed)
        eta_str = format_duration(eta) if eta > 0 else "Calculating..."
        dur_str = format_duration(duration) if duration else ""

        lines = [
            f"📤 <b>Uploading to Telegram</b>",
            f"",
            f"┌─────《 Progress 》─────┐",
        ]
        if short_name:
            lines.append(f"├» 🎬 File: {short_name}")
        if dur_str:
            lines.append(f"├» ⏱ Duration: {dur_str}")
        lines.append(f"├» 📦 Quality: {quality}")
        if uploaded_str:
            lines.append(f"├» 📊 Uploaded: {uploaded_str}")
        lines.append(f"├» {bar} {percent}%")
        lines.append(f"├» 🚀 Speed: {speed_str}")
        lines.append(f"├» ⏱ ETA: {eta_str}")
        lines.append(f"└──────────────────────┘")

        await safe_edit_text(self.status_message, "\n".join(lines))

    async def cleanup(self) -> None:
        self._stopped = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except BaseException:
                pass


# =========================
# Downloader
# =========================
async def _run_command(command: list[str]) -> tuple[bytes, bytes, int]:
    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=str(BASE_DIR),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=DOWNLOAD_TIMEOUT   # 2 hours — badi file support
        )
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        raise RuntimeError(
            f"⏱ Download timeout ({DOWNLOAD_TIMEOUT//3600}h). "
            "File bahut badi hai ya connection slow hai. "
            "Chhoti quality try karein."
        )
    return stdout, stderr, process.returncode


# Track whether auto-update has already run this session (avoid repeated updates)
_ytdlp_updated_this_session: bool = False


def _is_signature_error(stderr_text: str) -> bool:
    """Detect yt-dlp signature/JS extraction errors that require an update."""
    sig_keywords = [
        "Signature extraction failed",
        "nsig extraction failed",
        "Could not find JS function",
        "player_js_url",
        "player-plasma",
        "base.js",
        "Sign in to confirm",
        "This video is not available",
    ]
    return any(kw.lower() in stderr_text.lower() for kw in sig_keywords)


async def _auto_update_ytdlp() -> bool:
    """
    Silently update yt-dlp from GitHub master.
    Returns True if update succeeded, False otherwise.
    Only runs once per bot session to avoid hammering pip.
    """
    global _ytdlp_updated_this_session
    if _ytdlp_updated_this_session:
        logger.info("yt-dlp auto-update: already updated this session, skipping.")
        return True

    logger.warning("yt-dlp signature error detected — auto-updating yt-dlp from GitHub master...")
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "pip", "install", "--quiet",
            "--no-cache-dir", "--break-system-packages", "--force-reinstall",
            "https://github.com/yt-dlp/yt-dlp/archive/refs/heads/master.zip#egg=yt-dlp",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, pip_err = await asyncio.wait_for(proc.communicate(), timeout=120)
        if proc.returncode == 0:
            _ytdlp_updated_this_session = True
            logger.info("yt-dlp auto-update: SUCCESS ✅")
            return True
        else:
            logger.error("yt-dlp auto-update FAILED: %s", pip_err.decode(errors="replace")[:300])
            return False
    except Exception as exc:
        logger.error("yt-dlp auto-update exception: %s", exc)
        return False


async def run_downloader(url: str, platform: str, quality: str = "best") -> tuple[list[Path], Path]:
    temp_dir = DOWNLOAD_DIR / f"{platform}_{uuid.uuid4().hex}"
    temp_dir.mkdir(parents=True, exist_ok=True)

    if platform in GALLERY_DL_PREFERRED:
        gdl_cmd = build_gallery_dl_command(url, temp_dir, platform)
        stdout, stderr, rc = await _run_command(gdl_cmd)

        if rc == 0:
            files = collect_media_files(temp_dir)
            if files:
                return files, temp_dir
            logger.warning("gallery-dl rc=0 but no files for %s, trying yt-dlp", platform)
        else:
            logger.info("gallery-dl failed (rc=%d) for %s, trying yt-dlp fallback", rc, platform)

        if platform == "instagram":
            ytdlp_cmd = build_ytdlp_instagram_command(url, temp_dir)
        else:
            ytdlp_cmd = build_ytdlp_command(url, temp_dir, platform, "best")
        stdout, stderr, rc = await _run_command(ytdlp_cmd)

        if rc != 0:
            err = (stderr or b"").decode(errors="replace").strip()
            raise RuntimeError(err or "Download failed")

        files = collect_media_files(temp_dir)
        if not files:
            raise RuntimeError("No downloadable media files were found for this post.")
        return files, temp_dir

    ytdlp_cmd = build_ytdlp_command(url, temp_dir, platform, quality)
    stdout, stderr, rc = await _run_command(ytdlp_cmd)

    if rc != 0:
        err = (stderr or b"").decode(errors="replace").strip()

        # YouTube fallback chain with auto-update on signature errors
        if platform == "youtube":
            logger.info("YouTube attempt 1 failed: %s", err[:200])

            def _make_yt_cmd(client_arg, fmt, is_audio=False):
                base = [
                    "yt-dlp",
                    "--extractor-args", f"youtube:player_client={client_arg};skip=webpage,configs",
                    "--no-check-certificates",
                    "--retries", "5",
                    "--fragment-retries", "5",
                    "--retry-sleep", "exp=2",
                    "--socket-timeout", "60",
                    "--concurrent-fragments", "4",
                    "--compat-options", "no-youtube-unavailable-videos",
                    "--add-header", "User-Agent:Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.6367.82 Mobile Safari/537.36",
                ]
                if is_audio:
                    base += ["--format", "bestaudio/best", "--extract-audio", "--audio-format", "mp3", "--audio-quality", "0"]
                else:
                    base += ["--format", fmt, "--merge-output-format", "mp4"]
                base += ["-o", str(temp_dir / "%(title).50s.%(ext)s"), url]
                cf = BASE_DIR / YOUTUBE_COOKIE_FILE
                if cf.exists():
                    base[1:1] = ["--cookies", str(cf)]
                return base

            is_audio = quality == "audio_only"
            fallback_fmt = (
                "bestaudio/best" if is_audio
                else {
                    "best":  "bestvideo+bestaudio/best",
                    "1080p": "bestvideo[height<=1080]+bestaudio/best[height<=1080]",
                    "720p":  "bestvideo[height<=720]+bestaudio/best[height<=720]",
                    "480p":  "bestvideo[height<=480]+bestaudio/best[height<=480]",
                    "360p":  "bestvideo[height<=360]+bestaudio/best[height<=360]",
                }.get(quality, "bestvideo+bestaudio/best")
            )

            # --- Auto-update yt-dlp if signature error detected ---
            if _is_signature_error(err):
                logger.warning("Signature error detected on attempt 1 — triggering auto-update...")
                updated = await _auto_update_ytdlp()
                if updated:
                    logger.info("Auto-update done. Retrying original command with updated yt-dlp...")
                    stdout_u, stderr_u, rc_u = await _run_command(ytdlp_cmd)
                    if rc_u == 0:
                        files = collect_media_files(temp_dir)
                        if files:
                            return files, temp_dir
                    err = (stderr_u or b"").decode(errors="replace").strip()
                    logger.info("Post-update attempt failed: %s", err[:200])

            # Attempt 2: android client (no JS runtime needed)
            logger.info("YouTube retry 2: android client...")
            cmd2 = _make_yt_cmd("android", fallback_fmt, is_audio)
            stdout2, stderr2, rc2 = await _run_command(cmd2)
            if rc2 == 0:
                files = collect_media_files(temp_dir)
                if files:
                    return files, temp_dir
            err2 = (stderr2 or b"").decode(errors="replace").strip()
            logger.info("YouTube retry 2 failed: %s", err2[:200])

            # Attempt 3: tv_embedded (bypasses age/region restrictions, no JS needed)
            logger.info("YouTube retry 3: tv_embedded client...")
            cmd3 = _make_yt_cmd("tv_embedded", fallback_fmt, is_audio)
            stdout3, stderr3, rc3 = await _run_command(cmd3)
            if rc3 == 0:
                files = collect_media_files(temp_dir)
                if files:
                    return files, temp_dir
            err3 = (stderr3 or b"").decode(errors="replace").strip()
            logger.info("YouTube retry 3 failed: %s", err3[:200])

            # Attempt 4: mweb (mobile web — different token path)
            logger.info("YouTube retry 4: mweb client...")
            cmd4 = _make_yt_cmd("mweb", fallback_fmt, is_audio)
            stdout4, stderr4, rc4 = await _run_command(cmd4)
            if rc4 == 0:
                files = collect_media_files(temp_dir)
                if files:
                    return files, temp_dir
            err4 = (stderr4 or b"").decode(errors="replace").strip()
            logger.info("YouTube retry 4 failed: %s", err4[:200])

            # Attempt 5 (post-update): android_vr — last resort after all clients failed
            if _is_signature_error(err4 or err3 or err2 or err):
                logger.info("YouTube retry 5: android_vr (post-update last resort)...")
                cmd5 = _make_yt_cmd("android_vr", fallback_fmt, is_audio)
                stdout5, stderr5, rc5 = await _run_command(cmd5)
                if rc5 == 0:
                    files = collect_media_files(temp_dir)
                    if files:
                        return files, temp_dir
                err5 = (stderr5 or b"").decode(errors="replace").strip()
                logger.info("YouTube retry 5 failed: %s", err5[:200])

            raise RuntimeError(
                f"❌ YouTube download failed after all attempts.\n\n"
                f"Error: {err4[:300] if err4 else err3[:300] if err3 else err[:300]}"
            )

        if platform == "facebook":
            logger.info("Facebook attempt 1 failed: %s", err[:200])
            logger.info("Facebook retry 2: Chrome 125 UA + Referer...")
            cf = BASE_DIR / FACEBOOK_COOKIE_FILE
            alt_cmd2 = [
                "yt-dlp",
                "--no-check-certificates",
                "--retries", "5",
                "--socket-timeout", "60",
                "--format", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best",
                "--merge-output-format", "mp4",
                "-o", str(temp_dir / "%(title).50s.%(ext)s"),
                "--add-header", "User-Agent:Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
                "--add-header", "Accept-Language:en-US,en;q=0.9",
                "--add-header", "Accept:text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "--add-header", "Referer:https://www.facebook.com/",
                "--add-header", "Sec-Fetch-Mode:navigate",
                url,
            ]
            if cf.exists():
                alt_cmd2[1:1] = ["--cookies", str(cf)]
            stdout2, stderr2, rc2 = await _run_command(alt_cmd2)

            if rc2 == 0:
                files = collect_media_files(temp_dir)
                if files:
                    return files, temp_dir

            err2 = (stderr2 or b"").decode(errors="replace").strip()
            logger.info("Facebook retry 2 failed: %s", err2[:200])

            # Retry 3: mobile UA (sometimes parses better for mobile-uploaded videos)
            logger.info("Facebook retry 3: mobile UA...")
            alt_cmd3 = [
                "yt-dlp",
                "--no-check-certificates",
                "--retries", "3",
                "--format", "best[ext=mp4]/best",
                "--merge-output-format", "mp4",
                "-o", str(temp_dir / "%(title).50s.%(ext)s"),
                "--add-header", "User-Agent:Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
                "--add-header", "Accept-Language:en-US,en;q=0.9",
                "--add-header", "Referer:https://m.facebook.com/",
                url,
            ]
            if cf.exists():
                alt_cmd3[1:1] = ["--cookies", str(cf)]
            stdout3, stderr3, rc3 = await _run_command(alt_cmd3)

            if rc3 == 0:
                files = collect_media_files(temp_dir)
                if files:
                    return files, temp_dir

            err3 = (stderr3 or b"").decode(errors="replace").strip()
            raise RuntimeError(
                f"❌ Facebook download failed after 3 attempts.\n"
                f"Last error: {err3[:300] if err3 else err2[:300] if err2 else err[:300]}"
            )

        raise RuntimeError(err or "Download failed")

    files = collect_media_files(temp_dir)
    if not files:
        raise RuntimeError("No downloadable media files were found for this post.")
    return files, temp_dir


# =========================
# Media Sender — 2GB support with per-file timeout
# =========================
async def send_media_files(
    message,
    progress: StatusProgress,
    files: list[Path],
    video_info: dict | None = None,
) -> None:
    if not files:
        raise RuntimeError("No files to send.")

    sent_count = 0
    caption = build_video_caption(video_info)
    caption_short = caption[:1024]
    caption_doc   = caption[:4096]

    thumbnail_url = (video_info.get("thumbnail") or "") if video_info else ""

    for idx, file_path in enumerate(files):
        ext = file_path.suffix.lower()
        file_size = file_path.stat().st_size

        if file_size > TG_MAX_FILE_SIZE:
            await safe_edit_text(
                progress.status_message,
                f"⚠️ File too large ({format_size(file_size)}).\n"
                f"Maximum: 2 GB. Please choose lower quality."
            )
            continue

        duration = int(video_info.get("duration") or 0) if video_info else 0
        quality_label = "MP4" if ext in VIDEO_EXTS else ("MP3" if ext in AUDIO_EXTS else "Image")
        upload_start = time.time()

        # ── Upload progress task ──────────────────────────────────────────
        async def update_upload_progress(
            fp: Path, fs: int, dl: int, qual: str, dur: int, prog: StatusProgress
        ):
            # Speed estimate based on file size
            if fs > 500 * 1024 * 1024:
                avg_speed = 250 * 1024   # ~250 KB/s for very large files
            elif fs > 100 * 1024 * 1024:
                avg_speed = 400 * 1024
            else:
                avg_speed = 600 * 1024
            estimated_total_secs = max(fs / avg_speed, 2)
            reported_pct = 0
            while reported_pct < 98:
                elapsed = time.time() - upload_start
                pct = min(int(elapsed / estimated_total_secs * 100), 98)
                if pct > reported_pct:
                    reported_pct = pct
                    uploaded_bytes = int(fs * pct / 100)
                    speed = uploaded_bytes / max(elapsed, 0.1)
                    remaining = fs - uploaded_bytes
                    eta = int(remaining / speed) if speed > 0 else 0
                    await prog.set_uploading(
                        percent=pct,
                        filename=fp.name,
                        uploaded_bytes=uploaded_bytes,
                        total_bytes=fs,
                        speed=speed,
                        eta=eta,
                        duration=dur,
                        quality=qual,
                    )
                await asyncio.sleep(3)

        progress_task = asyncio.create_task(
            update_upload_progress(file_path, file_size, idx, quality_label, duration, progress)
        )

        # ── Per-file upload timeout: 2GB @ ~250KB/s = ~2h, safety margin ─
        # 300 sec base + 1 sec per MB, capped at 3 hours
        upload_timeout_secs = min(300 + (file_size // (1024 * 1024)), 10800)

        try:
            async def _do_send():
                if ext in VIDEO_EXTS:
                    if file_size > TG_STANDARD_LIMIT:
                        logger.info(
                            "File %s is %s > 50MB, sending as document",
                            file_path.name, format_size(file_size)
                        )
                        with open(file_path, "rb") as f:
                            await message.reply_document(
                                document=f,
                                caption=caption_doc,
                                filename=file_path.name,
                                parse_mode="HTML",
                                read_timeout=UPLOAD_READ_TIMEOUT,
                                write_timeout=UPLOAD_WRITE_TIMEOUT,
                                connect_timeout=UPLOAD_CONNECT_TIMEOUT,
                                pool_timeout=UPLOAD_POOL_TIMEOUT,
                            )
                    else:
                        send_kwargs = dict(
                            caption=caption_short,
                            supports_streaming=True,
                            parse_mode="HTML",
                            read_timeout=UPLOAD_READ_TIMEOUT,
                            write_timeout=UPLOAD_WRITE_TIMEOUT,
                            connect_timeout=UPLOAD_CONNECT_TIMEOUT,
                            pool_timeout=UPLOAD_POOL_TIMEOUT,
                        )
                        if duration:
                            send_kwargs["duration"] = duration
                        if thumbnail_url:
                            send_kwargs["thumbnail"] = thumbnail_url
                        with open(file_path, "rb") as f:
                            await message.reply_video(video=f, **send_kwargs)

                elif ext in AUDIO_EXTS:
                    if file_size > TG_STANDARD_LIMIT:
                        with open(file_path, "rb") as f:
                            await message.reply_document(
                                document=f,
                                caption=caption_doc,
                                filename=file_path.name,
                                parse_mode="HTML",
                                read_timeout=UPLOAD_READ_TIMEOUT,
                                write_timeout=UPLOAD_WRITE_TIMEOUT,
                                connect_timeout=UPLOAD_CONNECT_TIMEOUT,
                                pool_timeout=UPLOAD_POOL_TIMEOUT,
                            )
                    else:
                        send_kwargs = dict(
                            caption=caption_short,
                            parse_mode="HTML",
                            read_timeout=UPLOAD_READ_TIMEOUT,
                            write_timeout=UPLOAD_WRITE_TIMEOUT,
                            connect_timeout=UPLOAD_CONNECT_TIMEOUT,
                            pool_timeout=UPLOAD_POOL_TIMEOUT,
                        )
                        if duration:
                            send_kwargs["duration"] = duration
                        if thumbnail_url:
                            send_kwargs["thumbnail"] = thumbnail_url
                        with open(file_path, "rb") as f:
                            await message.reply_audio(audio=f, **send_kwargs)

                elif ext in IMAGE_EXTS:
                    with open(file_path, "rb") as f:
                        await message.reply_photo(
                            photo=f,
                            caption=caption_short,
                            parse_mode="HTML",
                            read_timeout=120,
                            write_timeout=120,
                            connect_timeout=UPLOAD_CONNECT_TIMEOUT,
                            pool_timeout=UPLOAD_POOL_TIMEOUT,
                        )

            await asyncio.wait_for(_do_send(), timeout=upload_timeout_secs)
            sent_count += 1

        except asyncio.TimeoutError:
            raise RuntimeError(
                f"⏱ Upload timeout for {file_path.name} ({format_size(file_size)}).\n"
                "Server connection slow hai. Dobara try karein ya chhoti quality choose karein."
            )
        except Exception as e:
            err_str = str(e)
            if "413" in err_str or "Request Entity Too Large" in err_str:
                logger.warning("413 error for %s (%s). Falling back to document.", file_path.name, format_size(file_size))
                try:
                    with open(file_path, "rb") as f:
                        await message.reply_document(
                            document=f,
                            caption=caption_doc,
                            filename=file_path.name,
                            parse_mode="HTML",
                            read_timeout=UPLOAD_READ_TIMEOUT,
                            write_timeout=UPLOAD_WRITE_TIMEOUT,
                            connect_timeout=UPLOAD_CONNECT_TIMEOUT,
                            pool_timeout=UPLOAD_POOL_TIMEOUT,
                        )
                    sent_count += 1
                except Exception as e2:
                    logger.error("Document fallback also failed: %s", e2)
                    raise RuntimeError(
                        f"Upload failed — file too large for bot setup: {format_size(file_size)}\n"
                        f"2GB support ke liye Local Bot API Server chahiye.\n"
                        f"See: https://core.telegram.org/bots/api#using-a-local-bot-api-server"
                    )
            else:
                raise
        finally:
            progress_task.cancel()
            try:
                await progress_task
            except BaseException:
                pass

    if sent_count == 0:
        raise RuntimeError("Failed to send any files.")

    await stats_store.increment_downloads()


# =========================
# Handlers
# =========================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await register_user_and_notify(update, context)
    user = update.effective_user
    text = build_welcome_text(user.first_name if user else None)
    if update.message:
        await update.message.reply_text(text, reply_markup=welcome_keyboard(), parse_mode="HTML")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    help_text = (
        "╔══════════════════════╗\n"
        "║   📖 BOT HELP GUIDE  ║\n"
        "╚══════════════════════╝\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🚀 <b>HOW TO USE</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "1️⃣  Copy any video/image link\n"
        "2️⃣  Paste & send it here\n"
        "3️⃣  Choose download quality\n"
        "4️⃣  Wait — bot downloads & sends!\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🔍 <b>YOUTUBE SEARCH</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Just type any song or movie name!\n"
        "Examples:\n"
        "• <code>haseen dillruba song</code>\n"
        "• <code>liger trailer</code>\n"
        "• <code>arijit singh best songs</code>\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🌐 <b>SUPPORTED PLATFORMS</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "▶️  YouTube  📸  Instagram  🎵  TikTok\n"
        "📌  Pinterest  👻  Snapchat  💜  Likee\n"
        "🌍  VK  🔵  Facebook  🧵  Threads\n"
        "🎶  SoundCloud  🟢  Spotify  🎧  Deezer\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🎬 <b>QUALITY OPTIONS</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🔥 Best  🖥 1080p  📺 720p  📱 480p  📉 360p  🎵 MP3\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📦 <b>FILE SIZE</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "✅ ≤ 50MB → plays inline in Telegram\n"
        "📁 > 50MB → sent as document\n"
        "🔝 Maximum: <b>2 GB</b>\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "⚙️ <b>COMMANDS</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "/start  /help  /search &lt;query&gt;\n\n"

        "👨‍💻 Made with ❤️ by @anujedits76"
    )
    if update.message:
        await update.message.reply_text(help_text, parse_mode="HTML")


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    if update.effective_user and update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text("⛔ Admin only command.")
        return
    stats = await stats_store.get_stats()
    await update.message.reply_text(
        f"📊 <b>Bot Statistics</b>\n\n"
        f"👥 Total Users: {stats['total_users']:,}\n"
        f"📥 Total Downloads: {stats['total_downloads']:,}",
        parse_mode="HTML",
    )


async def cmd_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await register_user_and_notify(update, context)
    msg = update.effective_message
    if not msg:
        return

    query = " ".join(context.args) if context.args else ""
    if not query.strip():
        await msg.reply_text(
            "🔍 <b>YouTube Search</b>\n\n"
            "Usage: <code>/search haseen dillruba song</code>\n\n"
            "Ya seedha text type karo bina /search ke!",
            parse_mode="HTML",
        )
        return

    if await require_join(update, context, {"type": "search", "query": query}):
        return

    await _do_youtube_search(msg, context, query)


async def _do_youtube_search(msg, context, query: str) -> None:
    status = await msg.reply_text(f"🔍 Searching YouTube for: <b>{query}</b>...", parse_mode="HTML")

    results = await search_youtube(query, max_results=8)

    if not results:
        await safe_edit_text(
            status,
            f"❌ <b>No results found for:</b> <code>{query}</code>\n\nPlease try a different search term."
        )
        return

    search_key = store_search_results(results)
    result_text = build_search_results_text(query, results)
    kb = search_results_keyboard(results, search_key)

    try:
        await safe_edit_text(status, result_text, reply_markup=kb)
    except Exception:
        short_text = f"🔍 <b>Results for:</b> <code>{query}</code>\n\n👆 Choose a result below:"
        await safe_edit_text(status, short_text, reply_markup=kb)


async def resolve_facebook_share_url(url: str) -> str:
    """
    Facebook share/r/ short URLs ko real video URL mein resolve karo.
    e.g. facebook.com/share/r/xxx → facebook.com/videos/xxx
    """
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        if "facebook.com" in parsed.netloc and "/share/" in parsed.path:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    allow_redirects=True,
                    timeout=aiohttp.ClientTimeout(total=10),
                    headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
                        "Accept-Language": "en-US,en;q=0.9",
                    }
                ) as resp:
                    resolved = str(resp.url)
                    if "facebook.com" in resolved and resolved != url:
                        logger.info("Facebook share URL resolved: %s → %s", url, resolved)
                        return resolved
    except Exception as e:
        logger.warning("Facebook URL resolve failed, using original: %s", e)
    return url


async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Cookie paste flow — admin only, intercept before normal URL handling
    if await handle_cookie_paste(update, context):
        return

    await register_user_and_notify(update, context)
    msg = update.effective_message
    if not msg or not msg.text:
        return

    text = msg.text.strip()
    url  = extract_first_url(text)

    if url:
        # Facebook share/r/ short URLs resolve karke real URL nikalo
        url = await resolve_facebook_share_url(url)

        platform = get_platform(url)
        if not platform:
            await msg.reply_text("⚠️ Unsupported platform. Send a link from YouTube, Instagram, TikTok, etc.")
            return

        if await require_join(update, context, {"type": "url", "url": url}):
            return

        await context.bot.send_chat_action(chat_id=msg.chat_id, action=ChatAction.TYPING)

        if platform in SKIP_QUALITY_PLATFORMS or platform in GALLERY_DL_PREFERRED:
            status   = await msg.reply_text("⏳ Fetching media, please wait...")
            progress = StatusProgress(status)

            video_info = None
            try:
                video_info = await fetch_video_info(url, platform)
            except Exception as e:
                logger.warning("Info fetch failed for %s: %s", platform, e)

            async with download_semaphore:
                temp_dir = None
                try:
                    await progress.start_downloading()
                    files, temp_dir = await run_downloader(url, platform, "best")
                    await progress.finish_downloading()
                    await send_media_files(msg, progress, files, video_info=video_info)
                    await safe_edit_text(status, "✅ Done!")
                except Exception as e:
                    logger.error("Download error [%s]: %s", platform, e)
                    await progress.cleanup()
                    await safe_edit_text(status, f"❌ Failed: {e}")
                finally:
                    safe_remove_tree(temp_dir)
            return

        status = await msg.reply_text("🔍 Fetching video info...")

        video_info = None
        try:
            video_info = await fetch_video_info(url, platform)
        except Exception as e:
            logger.warning("Info fetch failed: %s", e)

        url_key = store_url(url, platform)

        if video_info:
            sizes     = parse_format_sizes(video_info)
            info_text = build_info_message(video_info, platform, sizes)
            thumbnail_url = video_info.get("thumbnail")
            try:
                if thumbnail_url:
                    await msg.reply_photo(
                        photo=thumbnail_url,
                        caption=info_text,
                        reply_markup=quality_keyboard(url_key),
                        parse_mode="HTML",
                    )
                    await status.delete()
                else:
                    await safe_edit_text(status, info_text, reply_markup=quality_keyboard(url_key))
            except Exception:
                await safe_edit_text(status, info_text, reply_markup=quality_keyboard(url_key))
        else:
            await safe_edit_text(
                status,
                "🎬 <b>Choose download quality:</b>",
                reply_markup=quality_keyboard(url_key),
            )
        return

    if is_search_query(text):
        if await require_join(update, context, {"type": "search", "query": text}):
            return
        await context.bot.send_chat_action(chat_id=msg.chat_id, action=ChatAction.TYPING)
        await _do_youtube_search(msg, context, text)
        return

    await msg.reply_text(
        "⚠️ Please send a valid URL or type a song/movie name to search YouTube.\n\n"
        "Example: <code>haseen song</code> or <code>liger movie</code>",
        parse_mode="HTML",
    )


async def handle_search_result_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()

    data = query.data or ""

    if data == "sr_cancel":
        try:
            await query.message.delete()
        except Exception:
            pass
        return

    if not data.startswith("sr|"):
        return

    parts = data.split("|", 2)
    if len(parts) != 3:
        return

    _, idx_str, search_key = parts
    try:
        idx = int(idx_str)
    except ValueError:
        return

    results = get_search_results(search_key)
    if not results:
        await query.message.reply_text("⚠️ Search session expired. Please search again.")
        return

    if idx < 0 or idx >= len(results):
        await query.message.reply_text("⚠️ Invalid selection.")
        return

    chosen   = results[idx]
    url      = chosen["url"]
    platform = "youtube"

    cleanup_search_results(search_key)

    try:
        await query.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    if await require_join(update, context, {"type": "url", "url": url}):
        return

    msg    = query.message
    status = await msg.reply_text("🔍 Fetching video info...")

    video_info = None
    try:
        video_info = await fetch_video_info(url, platform)
    except Exception as e:
        logger.warning("Info fetch failed: %s", e)

    url_key = store_url(url, platform)

    if video_info:
        sizes     = parse_format_sizes(video_info)
        info_text = build_info_message(video_info, platform, sizes)
        thumbnail_url = video_info.get("thumbnail")
        try:
            if thumbnail_url:
                await msg.reply_photo(
                    photo=thumbnail_url,
                    caption=info_text,
                    reply_markup=quality_keyboard(url_key),
                    parse_mode="HTML",
                )
                await status.delete()
            else:
                await safe_edit_text(status, info_text, reply_markup=quality_keyboard(url_key))
        except Exception:
            await safe_edit_text(status, info_text, reply_markup=quality_keyboard(url_key))
    else:
        title = chosen.get("title", "Video")
        await safe_edit_text(
            status,
            f"🎬 <b>{title}</b>\n\nChoose download quality:",
            reply_markup=quality_keyboard(url_key),
        )


async def handle_quality_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()

    data = query.data or ""
    if not data.startswith("q|"):
        return

    parts = data.split("|", 2)
    if len(parts) != 3:
        return

    _, quality, url_key = parts
    result = get_url(url_key)
    if not result:
        await query.message.reply_text("⚠️ Session expired. Please send the URL again.")
        return

    url, platform = result
    cleanup_url(url_key)

    msg = query.message
    if not msg:
        return

    if await require_join(update, context, {"type": "quality", "url": url, "quality": quality, "platform": platform}):
        return

    try:
        await msg.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    status   = await msg.reply_text("⏳ Starting download...")
    progress = StatusProgress(status)

    video_info = None
    try:
        video_info = await fetch_video_info(url, platform)
    except Exception:
        pass

    async with download_semaphore:
        temp_dir = None
        try:
            file_size_hint = 0
            if video_info:
                sizes = parse_format_sizes(video_info)
                if quality == "audio_only":
                    q_label = "MP3"
                elif quality == "best":
                    q_label = next((q for q in ["1080p", "720p", "480p", "360p"] if q in sizes), "720p")
                else:
                    q_label = quality.upper()
                file_size_hint = sizes.get(q_label, sizes.get("720p", 0))

            await progress.start_downloading(total_size=file_size_hint)
            files, temp_dir = await run_downloader(url, platform, quality)
            await progress.finish_downloading()
            await send_media_files(msg, progress, files, video_info=video_info)
            await safe_edit_text(status, "✅ Done!")
        except Exception as e:
            logger.error("Quality download error [%s/%s]: %s", platform, quality, e)
            await progress.cleanup()
            await safe_edit_text(status, f"❌ {e}")
        finally:
            safe_remove_tree(temp_dir)


async def handle_check_join(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    user = query.from_user
    if not user:
        await query.answer("Could not verify.", show_alert=True)
        return

    if await is_user_joined(context, user.id):
        await query.answer("✅ Verified! You can now use the bot.", show_alert=True)
        try:
            await query.message.delete()
        except Exception:
            pass

        pending = context.user_data.pop("pending_action", None)
        if pending:
            msg = query.message

            if pending.get("type") == "search":
                query_text = pending.get("query", "")
                if query_text and msg:
                    await _do_youtube_search(msg, context, query_text)

            elif pending.get("type") == "quality":
                url      = pending["url"]
                quality  = pending["quality"]
                platform = pending["platform"]
                if msg:
                    status   = await msg.reply_text("⏳ Starting download...")
                    progress = StatusProgress(status)
                    video_info = None
                    try:
                        video_info = await fetch_video_info(url, platform)
                    except Exception:
                        pass
                    async with download_semaphore:
                        temp_dir = None
                        try:
                            file_size_hint = 0
                            if video_info:
                                sizes = parse_format_sizes(video_info)
                                if quality == "audio_only":
                                    q_label = "MP3"
                                elif quality == "best":
                                    q_label = next((q for q in ["1080p", "720p", "480p", "360p"] if q in sizes), "720p")
                                else:
                                    q_label = quality.upper()
                                file_size_hint = sizes.get(q_label, sizes.get("720p", 0))
                            await progress.start_downloading(total_size=file_size_hint)
                            files, temp_dir = await run_downloader(url, platform, quality)
                            await progress.finish_downloading()
                            await send_media_files(msg, progress, files, video_info=video_info)
                            await safe_edit_text(status, "✅ Done!")
                        except Exception as e:
                            logger.error("Quality download error after join [%s/%s]: %s", platform, quality, e)
                            await progress.cleanup()
                            await safe_edit_text(status, f"❌ {e}")
                        finally:
                            safe_remove_tree(temp_dir)

            elif pending.get("type") == "url":
                if msg:
                    url      = pending["url"]
                    platform = get_platform(url)
                    if platform and platform not in SKIP_QUALITY_PLATFORMS and platform not in GALLERY_DL_PREFERRED:
                        url_key = store_url(url, platform)
                        video_info_pending = None
                        try:
                            video_info_pending = await fetch_video_info(url, platform)
                        except Exception:
                            pass
                        if video_info_pending:
                            sizes = parse_format_sizes(video_info_pending)
                            info_text = build_info_message(video_info_pending, platform, sizes)
                            thumbnail_url = video_info_pending.get("thumbnail")
                            try:
                                if thumbnail_url:
                                    await msg.reply_photo(
                                        photo=thumbnail_url,
                                        caption=info_text,
                                        reply_markup=quality_keyboard(url_key),
                                        parse_mode="HTML",
                                    )
                                else:
                                    await msg.reply_text(
                                        info_text,
                                        reply_markup=quality_keyboard(url_key),
                                        parse_mode="HTML",
                                    )
                            except Exception:
                                await msg.reply_text(
                                    "🎬 <b>Choose download quality:</b>",
                                    reply_markup=quality_keyboard(url_key),
                                    parse_mode="HTML",
                                )
                        else:
                            await msg.reply_text(
                                "🎬 <b>Choose download quality:</b>",
                                reply_markup=quality_keyboard(url_key),
                                parse_mode="HTML",
                            )
                    elif platform:
                        status   = await msg.reply_text("⏳ Fetching media, please wait...")
                        progress = StatusProgress(status)
                        async with download_semaphore:
                            temp_dir = None
                            try:
                                vi = None
                                try:
                                    vi = await fetch_video_info(url, platform)
                                except Exception:
                                    pass
                                await progress.start_downloading()
                                files, temp_dir = await run_downloader(url, platform, "best")
                                await progress.finish_downloading()
                                await send_media_files(msg, progress, files, video_info=vi)
                                await safe_edit_text(status, "✅ Done!")
                            except Exception as e:
                                await progress.cleanup()
                                await safe_edit_text(status, f"❌ Failed: {e}")
                            finally:
                                safe_remove_tree(temp_dir)
    else:
        await query.answer("❌ You haven't joined yet. Please join and try again.", show_alert=True)



# =========================
# Cookie Command Handlers
# =========================

async def cmd_cookies(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin: /cookies — Show all cookie expiry status."""
    if not update.message:
        return
    if not update.effective_user or update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text("⛔ Admin only command.")
        return
    await update.message.reply_text(format_cookie_status_text(), parse_mode="HTML")


async def cmd_setcookies(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin: /setcookies <platform> — Start cookie update flow."""
    if not update.message:
        return
    if not update.effective_user or update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text("⛔ Admin only command.")
        return

    valid_platforms = list(COOKIE_FILES.keys())
    platform = (context.args[0].lower() if context.args else "").strip()

    if platform not in valid_platforms:
        lines = [
            "📋 <b>Cookie Update — Platform choose karo:</b>\n",
            "/setcookies youtube",
            "/setcookies instagram",
            "/setcookies facebook",
            "/setcookies tiktok",
            "",
            "ℹ️ Cookies export karne ke liye browser mein",
            "<b>Get cookies.txt LOCALLY</b> extension use karo.",
        ]
        await update.message.reply_text("\n".join(lines), parse_mode="HTML")
        return

    user_id = update.effective_user.id
    _cookie_pending[user_id] = platform
    await update.message.reply_text(
        f"🍪 <b>{platform.capitalize()} Cookie Update</b>\n\n"
        f"Ab <b>{platform}.com</b> ka poora Netscape cookie content paste karo.\n\n"
        f"Format aise hona chahiye:\n"
        f"<code># Netscape HTTP Cookie File\n"
        f".{platform}.com   TRUE   /   TRUE   1234567890   cookiename   value</code>\n\n"
        f"⚠️ Pehli line <code># Netscape HTTP Cookie File</code> honi chahiye.\n"
        f"❌ Cancel karne ke liye /cancel bhejo.",
        parse_mode="HTML",
    )


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Cancel pending cookie update."""
    if not update.effective_user or not update.message:
        return
    user_id = update.effective_user.id
    if user_id in _cookie_pending:
        platform = _cookie_pending.pop(user_id)
        await update.message.reply_text(
            f"❌ {platform.capitalize()} cookie update cancel ho gaya."
        )
    else:
        await update.message.reply_text("Koi pending operation nahi hai.")


async def handle_cookie_paste(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Called from handle_url — returns True if message was a cookie paste, else False.
    Handles multi-message cookie text (Telegram 4096 char limit splits long pastes).
    """
    if not update.effective_user or update.effective_user.id != ADMIN_USER_ID:
        return False
    if not update.message:
        return False
    user_id = update.effective_user.id
    if user_id not in _cookie_pending:
        return False

    platform = _cookie_pending[user_id]
    text = (update.effective_message.text or "").strip()

    # Telegram sometimes converts tabs to spaces in messages
    # Try to detect and handle both cases
    has_header = "Netscape HTTP Cookie File" in text
    has_tabs = "\t" in text
    # Some exporters use spaces instead of tabs; also accept lines with 7+ whitespace-separated fields
    has_cookie_lines = any(
        len(line.split()) >= 7
        for line in text.splitlines()
        if line.strip() and not line.startswith("#")
    )

    # Validate: must look like a Netscape cookie file
    if not has_header and not has_tabs and not has_cookie_lines:
        await update.message.reply_text(
            "⚠️ Yeh valid Netscape cookie format nahi lag raha.\n\n"
            "Pehli line honi chahiye:\n"
            "<code># Netscape HTTP Cookie File</code>\n\n"
            "Dobara try karo ya /cancel karo.",
            parse_mode="HTML",
        )
        return True   # Was a cookie attempt, stop further processing

    # Count valid cookie lines (accept both tab and space separated)
    lines = [l for l in text.splitlines() if l.strip() and not l.startswith("#")]
    valid_lines = [l for l in lines if len(l.split("\t")) >= 7 or len(l.split()) >= 7]

    if not valid_lines:
        await update.message.reply_text(
            "⚠️ Koi valid cookie lines nahi mili.\n"
            "Tab-separated (\t) Netscape format chahiye.\n"
            "Dobara try karo ya /cancel karo.",
            parse_mode="HTML",
        )
        return True

    # Save cookie file
    cookie_path = COOKIE_FILES[platform]
    cookie_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        cookie_path.write_text(text, encoding="utf-8")
        _cookie_pending.pop(user_id, None)

        # Show updated status
        info = get_cookie_expiry_info()
        p_info = info.get(platform, {})
        days = p_info.get("days_left")
        days_str = f"{days} days" if days is not None else "session only"

        await update.message.reply_text(
            f"✅ <b>{platform.capitalize()} cookies saved!</b>\n\n"
            f"📊 {len(valid_lines)} cookie lines saved\n"
            f"⏳ Validity: {days_str}\n\n"
            f"Use /cookies to check all platforms.",
            parse_mode="HTML",
        )
    except Exception as e:
        await update.message.reply_text(
            f"❌ Cookie file save nahi ho saka: {e}\n"
            f"Dobara try karo ya /cancel karo."
        )
    return True


async def check_and_notify_cookie_expiry(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Job: Daily check karo — agar koi cookie 7 din mein expire ho to admin ko notify karo.
    """
    if not ADMIN_USER_ID:
        return
    info = get_cookie_expiry_info()
    alerts = []
    for platform, data in info.items():
        if data["status"] == "expired":
            alerts.append(f"❌ <b>{platform.capitalize()}</b>: EXPIRED {abs(data['days_left'])} days ago!")
        elif data["status"] == "expiring_soon":
            alerts.append(f"⚠️ <b>{platform.capitalize()}</b>: Expires in {data['days_left']} days!")
        elif data["status"] in ("missing", "empty"):
            alerts.append(f"🚫 <b>{platform.capitalize()}</b>: Cookie file missing!")
    if alerts:
        text = "🍪 <b>Cookie Expiry Alert</b>\n\n" + "\n".join(alerts) + "\n\n/cookies se status check karo."
        try:
            await context.bot.send_message(chat_id=ADMIN_USER_ID, text=text, parse_mode="HTML")
        except Exception as e:
            logger.warning("Could not send cookie expiry alert: %s", e)


# =========================
# Application Setup
# =========================
async def post_init(application: Application) -> None:
    logger.info("Bot started: @%s", BOT_USERNAME)


def build_application() -> Application:
    global download_semaphore
    download_semaphore = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)
    # HTTPXRequest — sab timeouts generous rakhe 2GB support ke liye
    request_handler = HTTPXRequest(
        connection_pool_size=8,
        read_timeout=UPLOAD_READ_TIMEOUT,
        write_timeout=UPLOAD_WRITE_TIMEOUT,
        connect_timeout=UPLOAD_CONNECT_TIMEOUT,
        pool_timeout=UPLOAD_POOL_TIMEOUT,
    )
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .request(request_handler)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start",      cmd_start))
    app.add_handler(CommandHandler("help",       cmd_help))
    app.add_handler(CommandHandler("stats",      cmd_stats))
    app.add_handler(CommandHandler("search",     cmd_search))
    app.add_handler(CommandHandler("cookies",    cmd_cookies))
    app.add_handler(CommandHandler("setcookies", cmd_setcookies))
    app.add_handler(CommandHandler("cancel",     cmd_cancel))

    # Daily cookie expiry check — every 24 hours
    if app.job_queue:
        app.job_queue.run_repeating(
            check_and_notify_cookie_expiry,
            interval=86400,   # 24 hours
            first=60,         # 1 min after start
        )
    app.add_handler(CallbackQueryHandler(handle_check_join,              pattern="^check_join$"))
    app.add_handler(CallbackQueryHandler(handle_search_result_callback,  pattern=r"^sr[\|]"))
    app.add_handler(CallbackQueryHandler(handle_search_result_callback,  pattern="^sr_cancel$"))
    app.add_handler(CallbackQueryHandler(handle_quality_callback,        pattern=r"^q\|"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url))

    return app


# =========================
# Webhook / Polling
# =========================
@flask_app.route(f"/{BOT_TOKEN}", methods=["POST"])
def webhook_handler():
    data        = request.get_json(force=True)
    application = flask_app.config["application"]
    loop        = flask_app.config["loop"]
    asyncio.run_coroutine_threadsafe(
        application.process_update(Update.de_json(data, application.bot)),
        loop,
    ).result(timeout=120)   # Webhook handler timeout 2 min — long ops async mein hain
    return "OK"


@flask_app.route("/", methods=["GET"])
def health():
    return "Bot is running ✅"


def run_flask(app, loop):
    flask_app.config["application"] = app
    flask_app.config["loop"]        = loop
    flask_app.run(host="0.0.0.0", port=PORT, threaded=True)


def auto_update_ytdlp():
    """Deploy hote hi yt-dlp latest version force install karo."""
    try:
        logger.info("🔄 yt-dlp force-update shuru ho raha hai...")
        # --force-reinstall ensures Replit/any host ka cached version override ho
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--force-reinstall", "--no-cache-dir", "yt-dlp", "--quiet"],
            capture_output=True, text=True, timeout=180
        )
        if result.returncode == 0:
            ver = subprocess.run(
                ["yt-dlp", "--version"],
                capture_output=True, text=True, timeout=10
            )
            logger.info("✅ yt-dlp force-updated: v%s", ver.stdout.strip())
        else:
            logger.warning("⚠️ yt-dlp update failed: %s", result.stderr[:200])
            # Fallback: normal upgrade try karo
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "-U", "yt-dlp"],
                capture_output=True, text=True, timeout=120
            )
    except Exception as e:
        logger.warning("⚠️ yt-dlp auto-update error: %s", e)


def main():
    auto_update_ytdlp()
    application = build_application()

    if WEBHOOK_URL:
        async def run_webhook():
            await application.initialize()
            await application.bot.set_webhook(
                url=f"{WEBHOOK_URL.rstrip('/')}/{BOT_TOKEN}",
                allowed_updates=["message", "callback_query"],
            )
            loop   = asyncio.get_running_loop()
            thread = threading.Thread(target=run_flask, args=(application, loop), daemon=True)
            thread.start()
            logger.info("Webhook mode on port %d", PORT)
            await application.start()
            try:
                while True:
                    await asyncio.sleep(3600)
            except (KeyboardInterrupt, SystemExit):
                pass
            finally:
                await application.stop()
                await application.shutdown()

        asyncio.run(run_webhook())
    else:
        logger.info("Polling mode")
        flask_thread = threading.Thread(
            target=flask_app.run,
            kwargs={"host": "0.0.0.0", "port": PORT, "threaded": True},
            daemon=True,
        )
        flask_thread.start()
        logger.info("Health server started on port %d", PORT)

        application.run_polling(
            allowed_updates=["message", "callback_query"],
            drop_pending_updates=True,
        )


if __name__ == "__main__":
    main()
