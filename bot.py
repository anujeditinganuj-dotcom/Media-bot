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

try:
    from pyrofork import Client as PyroClient
    from pyrofork.errors import FloodWait, RPCError
    PYROGRAM_AVAILABLE = True
except ImportError:
    PYROGRAM_AVAILABLE = False

# =========================
# Developer: @anujbyedit
# =========================

from flask import Flask, request, Response
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler,
    ContextTypes, MessageHandler, filters,
)
from telegram.request import HTTPXRequest

# =========================
# Settings
# =========================
BOT_TOKEN        = os.environ.get("BOT_TOKEN", "8015464564:AAFe6QCyYpfSWPGbwih_u_XejaDLcho1KOI")
BOT_USERNAME     = os.environ.get("BOT_USERNAME", "unzip_anuj_bot")
WEBHOOK_URL      = os.environ.get("WEBHOOK_URL", "")
PORT             = int(os.environ.get("PORT", 5000))
API_ID           = int(os.environ.get("API_ID", "37476811"))
API_HASH         = os.environ.get("API_HASH", "7aa60670b871050820086c6267371ee6")
ADMIN_USER_ID    = int(os.environ.get("ADMIN_USER_ID", "7168219724"))
YOUTUBE_API_KEY  = os.environ.get("YOUTUBE_API_KEY", "AIzaSyCGfwA660Ba65cheWLn8ybj7eIbA4xhPQ0")

REQUIRED_CHANNEL_USERNAME = os.environ.get("REQUIRED_CHANNEL_USERNAME", "@log_ak_bots")
REQUIRED_CHANNEL_URL      = os.environ.get("REQUIRED_CHANNEL_URL", "https://t.me/log_ak_bots")

INSTAGRAM_COOKIE_FILE = "downloads/instagram_cookies.txt"
TIKTOK_COOKIE_FILE    = "downloads/tiktok_cookies.txt"
YOUTUBE_COOKIE_FILE   = "downloads/youtube_cookies.txt"
FACEBOOK_COOKIE_FILE  = "downloads/facebook_cookies.txt"
SPOTIFY_COOKIE_FILE   = "downloads/spotify_cookies.txt"

BASE_DIR     = Path(__file__).resolve().parent
DOWNLOAD_DIR = BASE_DIR / "downloads"
DOWNLOAD_DIR.mkdir(exist_ok=True)
STATS_FILE = BASE_DIR / "bot_stats.json"

COOKIE_FILES = {
    "youtube":   BASE_DIR / "downloads/youtube_cookies.txt",
    "instagram": BASE_DIR / "downloads/instagram_cookies.txt",
    "facebook":  BASE_DIR / "downloads/facebook_cookies.txt",
    "tiktok":    BASE_DIR / "downloads/tiktok_cookies.txt",
    "spotify":   BASE_DIR / "downloads/spotify_cookies.txt",
}
_cookie_pending: dict[int, str] = {}

MAX_CONCURRENT_DOWNLOADS = 4
DOWNLOAD_TIMEOUT         = 14400
UPLOAD_READ_TIMEOUT      = 7200
UPLOAD_WRITE_TIMEOUT     = 7200
UPLOAD_CONNECT_TIMEOUT   = 60
UPLOAD_POOL_TIMEOUT      = 60
download_semaphore: asyncio.Semaphore

TG_MAX_FILE_SIZE  = 2000 * 1024 * 1024
TG_STANDARD_LIMIT =   50 * 1024 * 1024

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}
AUDIO_EXTS = {".mp3", ".m4a", ".aac", ".flac", ".opus", ".ogg"}

FILE_CAPTION_BASE = "Downloaded by @anujbyedit\n🚀 Bot: @url_ak_uploader_bot"

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("Downloader-Bot")
flask_app = Flask(__name__)

# =========================
# Cookie helpers
# =========================
def get_cookie_expiry_info() -> dict:
    import time as _t
    now = int(_t.time())
    result = {}
    for platform, path in COOKIE_FILES.items():
        if not path.exists():
            result[platform] = {"status": "missing", "days_left": None}
            continue
        min_exp, valid = None, False
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
                        if exp > 0 and (min_exp is None or exp < min_exp):
                            min_exp = exp
                    except ValueError:
                        pass
        except Exception:
            pass
        if not valid:
            result[platform] = {"status": "empty", "days_left": None}
        elif min_exp is None:
            result[platform] = {"status": "ok_session", "days_left": None}
        else:
            dl = (min_exp - now) // 86400
            result[platform] = {
                "status": "expired" if dl < 0 else ("expiring_soon" if dl < 7 else "ok"),
                "days_left": dl,
            }
    return result


def format_cookie_status_text() -> str:
    info  = get_cookie_expiry_info()
    icons = {"ok": "✅", "ok_session": "✅", "expiring_soon": "⚠️", "expired": "❌", "missing": "🚫", "empty": "🚫"}
    lines = ["🍪 <b>Cookie Status</b>\n"]
    for platform, data in info.items():
        icon = icons.get(data["status"], "❓")
        name = platform.capitalize()
        st   = data["status"]
        if st in ("missing", "empty"):
            lines.append(f"{icon} <b>{name}</b>: Not found")
        elif st == "expired":
            lines.append(f"{icon} <b>{name}</b>: EXPIRED {abs(data['days_left'])} days ago")
        elif st == "expiring_soon":
            lines.append(f"{icon} <b>{name}</b>: Expires in {data['days_left']} days ⚠️")
        elif st == "ok_session":
            lines.append(f"{icon} <b>{name}</b>: Active (session cookie)")
        else:
            lines.append(f"{icon} <b>{name}</b>: Valid — {data['days_left']} days left")
    lines += ["", "📋 <b>Commands:</b>",
              "/setcookies youtube", "/setcookies instagram",
              "/setcookies facebook", "/setcookies tiktok",
              "/setcookies spotify", "/cookies — status check karo"]
    return "\n".join(lines)


# =========================
# Pyrogram Client
# =========================
_pyro_client: "PyroClient | None" = None
_pyro_lock:   asyncio.Lock | None = None


async def _get_pyro_lock() -> asyncio.Lock:
    global _pyro_lock
    if _pyro_lock is None:
        _pyro_lock = asyncio.Lock()
    return _pyro_lock


async def get_pyro_client() -> "PyroClient | None":
    global _pyro_client
    if not PYROGRAM_AVAILABLE or not API_ID or not API_HASH:
        return None
    lock = await _get_pyro_lock()
    async with lock:
        if _pyro_client is not None:
            try:
                await _pyro_client.get_me()
                return _pyro_client
            except Exception:
                logger.warning("Pyrogram reconnecting...")
                try:
                    await _pyro_client.stop()
                except Exception:
                    pass
                _pyro_client = None
        for attempt in range(1, 4):
            try:
                client = PyroClient(
                    name="downloader_bot_pyro",
                    api_id=API_ID,
                    api_hash=API_HASH,
                    bot_token=BOT_TOKEN,
                    in_memory=True,
                    max_concurrent_transmissions=4,
                )
                await client.start()
                _pyro_client = client
                logger.info("✅ Pyrogram ready (attempt %d) — 2GB upload!", attempt)
                return _pyro_client
            except Exception as e:
                logger.warning("Pyrogram attempt %d failed: %s", attempt, e)
                await asyncio.sleep(3)
        logger.error("❌ Pyrogram start failed after 3 attempts.")
        return None


# =========================
# Stats Store
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

    def _default_data(self): return {"total_downloads": 0, "users": {}}

    def _load(self) -> dict:
        if not self.path.exists():
            d = self._default_data(); self._save_sync(d); return d
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            d.setdefault("total_downloads", 0); d.setdefault("users", {})
            return d
        except Exception:
            d = self._default_data(); self._save_sync(d); return d

    def _save_sync(self, data):
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    async def register_user(self, user) -> bool:
        uid = str(user.id)
        async with self.lock:
            is_new = uid not in self.data["users"]
            self.data["users"][uid] = {
                "id": user.id, "username": user.username or "",
                "first_name": user.first_name or "", "last_name": user.last_name or "",
            }
            self._save_sync(self.data)
            return is_new

    async def increment_downloads(self):
        async with self.lock:
            self.data["total_downloads"] = int(self.data.get("total_downloads", 0)) + 1
            self._save_sync(self.data)

    async def get_stats(self) -> dict:
        async with self.lock:
            return {"total_users": len(self.data.get("users", {})),
                    "total_downloads": int(self.data.get("total_downloads", 0))}


stats_store = StatsStore(STATS_FILE)

# =========================
# Stores
# =========================
_url_store:      dict[str, tuple] = {}
_search_store:   dict[str, tuple] = {}
_playlist_store: dict[str, tuple] = {}
_STORE_TTL = 3600


def _purge(store: dict, ttl: float):
    now = time.time()
    for k in [k for k, v in store.items() if now - v[-1] > ttl]:
        store.pop(k, None)


def store_url(url, platform, video_info=None) -> str:
    _purge(_url_store, _STORE_TTL)
    key = uuid.uuid4().hex[:8]
    _url_store[key] = (url, platform, video_info, time.time())
    return key

def get_url(key):
    e = _url_store.get(key)
    return (e[0], e[1]) if e else None

def get_url_with_info(key):
    e = _url_store.get(key)
    return (e[0], e[1], e[2]) if e else None

def cleanup_url(key): _url_store.pop(key, None)

def store_search_results(results, query="", page=0) -> str:
    _purge(_search_store, _STORE_TTL)
    key = uuid.uuid4().hex[:8]
    _search_store[key] = (results, query, page, time.time())
    return key

def get_search_results(key): e = _search_store.get(key); return e[0] if e else None
def get_search_query(key):   e = _search_store.get(key); return e[1] if e else ""
def get_search_page(key):    e = _search_store.get(key); return e[2] if e else 0
def cleanup_search_results(key): _search_store.pop(key, None)

def store_playlist(videos, title="", url="") -> str:
    _purge(_playlist_store, _STORE_TTL)
    key = uuid.uuid4().hex[:8]
    _playlist_store[key] = (videos, title, url, time.time())
    return key

def get_playlist(key):
    e = _playlist_store.get(key)
    return (e[0], e[1], e[2]) if e else None

def cleanup_playlist(key): _playlist_store.pop(key, None)

# =========================
# Helpers
# =========================
def extract_first_url(text: str):
    if not text: return None
    m = re.search(r"https?://[^\s]+", text)
    return m.group(0).strip() if m else None


def is_search_query(text: str) -> bool:
    if not text or not text.strip(): return False
    if re.search(r"https?://", text): return False
    if text.startswith("/"): return False
    s = text.strip()
    return len(s.split()) >= 2 or len(s) >= 3


def is_youtube_playlist(url: str) -> bool:
    try:
        from urllib.parse import parse_qs
        parsed = urlparse(url)
        host = (parsed.netloc or "").lower()
        if host not in {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}: return False
        qs = parse_qs(parsed.query)
        return "list" in qs and ("playlist" in parsed.path.lower() or "v" not in qs)
    except Exception:
        return False


def get_platform(url: str):
    try: host = (urlparse(url).netloc or "").lower()
    except Exception: return None
    if host in {"instagram.com", "www.instagram.com"}: return "instagram"
    if host in {"tiktok.com", "www.tiktok.com", "m.tiktok.com", "vm.tiktok.com", "vt.tiktok.com"}: return "tiktok"
    if host in {"youtube.com", "www.youtube.com", "youtu.be", "m.youtube.com"}: return "youtube"
    if host in {"pinterest.com", "www.pinterest.com", "pin.it", "pinterest.co.uk"}: return "pinterest"
    if host in {"snapchat.com", "www.snapchat.com"}: return "snapchat"
    if host in {"likee.video", "www.likee.video", "like.video"}: return "likee"
    if host in {"vk.com", "www.vk.com", "vkvideo.ru", "www.vkvideo.ru"}: return "vk"
    if host in {"facebook.com", "www.facebook.com", "m.facebook.com", "fb.watch"}: return "facebook"
    if host in {"threads.net", "www.threads.net"}: return "threads"
    if host in {"soundcloud.com", "www.soundcloud.com", "on.soundcloud.com",
                "open.spotify.com", "deezer.com", "www.deezer.com", "music.apple.com"}: return "music"
    return None


SHOW_QUALITY_PLATFORMS = {"youtube", "facebook", "instagram", "tiktok", "vk", "snapchat", "likee", "threads", "pinterest"}
SKIP_QUALITY_PLATFORMS = {"music"}
GALLERY_DL_PREFERRED   = {"pinterest"}


def format_size(b: int) -> str:
    if b < 1024: return f"{b} B"
    if b < 1024**2: return f"{b/1024:.1f} KB"
    if b < 1024**3: return f"{b/1024**2:.1f} MB"
    return f"{b/1024**3:.2f} GB"


def format_duration(s: int) -> str:
    if s < 0: return "??:??"
    h, r = divmod(s, 3600)
    m, s = divmod(r, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def format_speed(bps: float) -> str:
    if bps <= 0: return "Starting up..."
    if bps < 1024: return f"{bps:.0f} B/s"
    if bps < 1024**2: return f"{bps/1024:.1f} KB/s"
    return f"{bps/1024**2:.1f} MB/s"


def build_progress_bar(done, total=100, width=10) -> str:
    if not total or total <= 0:
        dots = int((time.time() * 2) % (width + 1))
        bar  = "⬢" * dots + "⬡" * (width - dots)
    else:
        filled = min(width, floor(width * done / total))
        bar    = "⬢" * filled + "⬡" * (width - filled)
    return f"[{bar}]"


def build_video_caption(info: dict | None) -> str:
    if not info: return FILE_CAPTION_BASE
    title   = (info.get("title") or info.get("description") or "")[:80]
    channel = info.get("uploader") or info.get("channel") or info.get("creator") or ""
    handle  = info.get("uploader_id") or info.get("channel_id") or ""
    views   = info.get("view_count") or 0
    dur     = info.get("duration") or 0
    likes   = info.get("like_count") or 0
    comments= info.get("comment_count") or 0
    shares  = info.get("repost_count") or 0
    subs    = info.get("channel_follower_count") or info.get("uploader_follower_count") or 0
    cats    = info.get("categories") or []
    cat     = cats[0] if cats else ""
    ud      = info.get("upload_date") or ""
    if ud and len(ud) == 8: ud = f"{ud[:4]}-{ud[4:6]}-{ud[6:]}"
    lines = []
    if title:   lines.append(f"🎬 {title} →")
    if channel: lines.append(f"👤 {channel}")
    if handle and handle != channel: lines.append(f"@{handle.lstrip('@')} ✓ →")
    if subs:    lines.append(f"👥 {subs:,}")
    if dur:     lines.append(f"🕐 {format_duration(int(dur))}")
    sp = []
    if views:    sp.append(f"👁 {views:,}")
    if likes:    sp.append(f"👍 {likes:,}")
    if comments: sp.append(f"💬 {comments:,}")
    if shares:   sp.append(f"🔁 {shares:,}")
    if sp: lines.append(" | ".join(sp))
    if cat: lines.append(f"🏷 {cat}")
    if ud:  lines.append(f"📅 {ud}")
    lines += ["", FILE_CAPTION_BASE]
    return "\n".join(lines)


def build_welcome_text(first_name) -> str:
    name = (first_name or "there").strip()
    return (
        f"🤝 Hello {name}\n\n"
        "📥 I can help you download videos and images from:\n\n"
        "▶️ YouTube  📷 Instagram  🎵 TikTok  📍 Pinterest\n"
        "👻 Snapchat  💛 Likee  🔷 VK  💬 Facebook  🔘 Threads  🎶 Music\n\n"
        "📋 <b>YouTube Playlist:</b> Playlist link bhejo, sari videos download!\n\n"
        "🔍 <b>YouTube Search:</b> Song ya movie ka naam type karo\n"
        "   Example: <code>haseen dillruba song</code>\n\n"
        "<i>(The bot also works in groups 👇)</i>"
    )


def join_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Join Channel 📢", url=REQUIRED_CHANNEL_URL)],
        [InlineKeyboardButton("I Joined ✅", callback_data="check_join")],
    ])


def welcome_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("➕ Add to Group", url=f"https://t.me/{BOT_USERNAME}?startgroup=true")
    ]])


def parse_format_sizes(info: dict) -> dict[str, int]:
    sizes: dict[str, int] = {}
    formats = info.get("formats") or []
    for fmt in formats:
        h = fmt.get("height") or 0
        if not h or h < 100: continue
        if (fmt.get("vcodec") or "none").lower() == "none": continue
        fs = fmt.get("filesize") or fmt.get("filesize_approx") or 0
        lbl = f"{h}p"
        if lbl not in sizes or fs > sizes[lbl]: sizes[lbl] = int(fs)
    best_fs, best_abr = 0, 0.0
    for fmt in formats:
        if (fmt.get("vcodec") or "none").lower() != "none": continue
        if (fmt.get("acodec") or "none").lower() == "none": continue
        fs  = fmt.get("filesize") or fmt.get("filesize_approx") or 0
        abr = float(fmt.get("abr") or fmt.get("tbr") or 0)
        if abr > best_abr or (abr == best_abr and fs > best_fs):
            best_abr, best_fs = abr, int(fs)
    if best_abr > 0 or best_fs > 0:
        ai = int(best_abr)
        albl = "MP3 320kbps" if ai >= 320 else "MP3 256kbps" if ai >= 256 else "MP3 192kbps" if ai >= 192 else "MP3 128kbps" if ai >= 128 else (f"MP3 {ai}kbps" if ai > 0 else "MP3")
        sizes[albl] = best_fs
    return sizes


def _sorted_video_heights(sizes: dict) -> list[str]:
    lbls = [k for k in sizes if re.match(r"^\d+p$", k)]
    lbls.sort(key=lambda x: int(x[:-1]), reverse=True)
    return lbls


def _audio_labels(sizes: dict) -> list[str]:
    return [k for k in sizes if k.startswith("MP3")]


def quality_keyboard(url_key: str, video_info=None) -> InlineKeyboardMarkup:
    buttons = []

    def _icon(h):
        if h >= 4320: return "⭐"
        if h >= 2160: return "🔵"
        if h >= 1440: return "💎"
        if h >= 1080: return "🖥"
        if h >= 720:  return "📺"
        if h >= 480:  return "📱"
        if h >= 360:  return "📉"
        return "🔹"

    def _qlabel(h):
        m = {4320:"4320p (8K)",2160:"2160p (4K)",1440:"1440p (2K)",
             1080:"1080p (FHD)",720:"720p (HD)",480:"480p (SD)",360:"360p",240:"240p",144:"144p (Lowest)"}
        return m.get(h, f"{h}p")

    if video_info:
        sizes  = parse_format_sizes(video_info)
        vheights = _sorted_video_heights(sizes)
        alabels  = _audio_labels(sizes)
        if vheights or alabels:
            buttons.append([InlineKeyboardButton("🔥 Best Quality", callback_data=f"q|best|{url_key}")])
            for lbl in vheights:
                h  = int(lbl[:-1])
                cb = f"q|{lbl}|{url_key}"
                if len(cb.encode()) > 64: continue
                fs = sizes.get(lbl, 0)
                warn = " ⚠️" if fs > TG_MAX_FILE_SIZE else ""
                sz   = f" ({format_size(fs)})" if fs else ""
                buttons.append([InlineKeyboardButton(f"{_icon(h)} {_qlabel(h)}{sz}{warn}", callback_data=cb)])
            for albl in alabels:
                cb = f"q|audio_only|{url_key}"
                if len(cb.encode()) > 64: continue
                fs = sizes.get(albl, 0)
                sz = f" ({format_size(fs)})" if fs else ""
                buttons.append([InlineKeyboardButton(f"🎵 {albl}{sz}", callback_data=cb)])
            if not alabels:
                buttons.append([InlineKeyboardButton("🎵 Audio Only (MP3)", callback_data=f"q|audio_only|{url_key}")])
            if not vheights:
                buttons[0] = [InlineKeyboardButton("🔥 Best Quality (Audio)", callback_data=f"q|best|{url_key}")]
        else:
            _static_quality_buttons(buttons, url_key)
    else:
        _static_quality_buttons(buttons, url_key)

    buttons.append([
        InlineKeyboardButton("🖼 Thumbnail", callback_data=f"thumb|{url_key}"),
        InlineKeyboardButton("📝 Description", callback_data=f"desc|{url_key}"),
    ])
    return InlineKeyboardMarkup(buttons)


def _static_quality_buttons(buttons, url_key):
    for label, q in [
        ("🔥 Best Quality","best"),("🖥 1080p (FHD)","1080p"),("📺 720p (HD)","720p"),
        ("📱 480p (SD)","480p"),("📉 360p","360p"),("🔹 240p","240p"),("🔹 144p","144p"),
    ]:
        cb = f"q|{q}|{url_key}"
        if len(cb.encode()) <= 64:
            buttons.append([InlineKeyboardButton(label, callback_data=cb)])
    buttons.append([InlineKeyboardButton("🎵 Audio Only (MP3)", callback_data=f"q|audio_only|{url_key}")])


def search_results_keyboard(results, search_key, page=0, has_prev=False) -> InlineKeyboardMarkup:
    buttons = []
    row1 = [InlineKeyboardButton(str(i), callback_data=f"sr|{i-1}|{search_key}") for i in range(1, min(6, len(results)+1))]
    row2 = [InlineKeyboardButton(str(i), callback_data=f"sr|{i-1}|{search_key}") for i in range(6, min(11, len(results)+1))]
    if row1: buttons.append(row1)
    if row2: buttons.append(row2)
    nav = []
    if has_prev: nav.append(InlineKeyboardButton("⬅️", callback_data=f"sr_page|{page}|prev|{search_key}"))
    nav.append(InlineKeyboardButton("➡️", callback_data=f"sr_page|{page}|next|{search_key}"))
    buttons.append(nav)
    buttons.append([InlineKeyboardButton("❌ Cancel", callback_data="sr_cancel")])
    return InlineKeyboardMarkup(buttons)


def playlist_keyboard(playlist_key) -> InlineKeyboardMarkup:
    rows = [
        ("🔥 Best Quality (All)", "best"), ("🖥 1080p", "1080p"),
        ("📺 720p", "720p"), ("📱 480p", "480p"), ("📉 360p", "360p"),
        ("🎵 Audio Only MP3", "audio_only"), ("❌ Cancel", None),
    ]
    buttons = []
    for label, q in rows:
        if q is None:
            buttons.append([InlineKeyboardButton(label, callback_data="pl_cancel")])
        else:
            cb = f"pl|{q}|{playlist_key}"
            if len(cb.encode()) <= 64:
                buttons.append([InlineKeyboardButton(label, callback_data=cb)])
    return InlineKeyboardMarkup(buttons)


def media_priority(path: Path):
    ext = path.suffix.lower()
    if ext in VIDEO_EXTS: return (0, path.name)
    if ext in IMAGE_EXTS: return (1, path.name)
    if ext in AUDIO_EXTS: return (2, path.name)
    return (3, path.name)


def collect_media_files(root: Path) -> list[Path]:
    all_exts = IMAGE_EXTS | VIDEO_EXTS | AUDIO_EXTS
    files = [p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in all_exts]
    files.sort(key=media_priority)
    return files


def build_gallery_dl_command(url, temp_dir, platform) -> list:
    cmd = ["gallery-dl", "--directory", str(temp_dir), "--no-mtime", "--retries", "3", "--timeout", "30"]
    if platform == "pinterest":
        cmd += ["--config-option", "extractor.pinterest.videos=true",
                "--config-option", "extractor.pinterest.video-format=best"]
    cmd.append(url)
    cmap = {"instagram": INSTAGRAM_COOKIE_FILE, "facebook": FACEBOOK_COOKIE_FILE,
            "tiktok": TIKTOK_COOKIE_FILE, "youtube": YOUTUBE_COOKIE_FILE}
    cf = cmap.get(platform)
    if cf:
        cp = BASE_DIR / cf
        if cp.exists(): cmd[1:1] = ["--cookies", str(cp)]
    return cmd


def _make_format_string(quality: str) -> str:
    if quality == "audio_only":
        return "bestaudio/best"
    if quality == "best":
        return ("bestvideo[ext=mp4][vcodec^=avc1]+bestaudio[ext=m4a]"
                "/bestvideo[ext=mp4][vcodec^=avc1]+bestaudio"
                "/bestvideo[ext=mp4]+bestaudio[ext=m4a]"
                "/bestvideo[ext=mp4]+bestaudio/bestvideo+bestaudio/best[ext=mp4]/best")
    if re.match(r"^\d+p$", quality):
        h = quality[:-1]
        return (f"bestvideo[height<={h}][ext=mp4][vcodec^=avc1]+bestaudio[ext=m4a]"
                f"/bestvideo[height<={h}][ext=mp4][vcodec^=avc1]+bestaudio"
                f"/bestvideo[height<={h}][ext=mp4]+bestaudio"
                f"/bestvideo[height<={h}]+bestaudio/best[height<={h}][ext=mp4]"
                f"/best[height<={h}]/bestvideo[ext=mp4]+bestaudio/bestvideo+bestaudio/best")
    return "bestvideo[ext=mp4][vcodec^=avc1]+bestaudio[ext=m4a]/bestvideo[ext=mp4]+bestaudio/bestvideo+bestaudio/best"


def build_ytdlp_command(url, temp_dir, platform, quality="best") -> list:
    out = str(temp_dir / "%(title).50s.%(ext)s")
    flags_map = {
        "youtube": ["--extractor-args","youtube:player_client=ios,web,android_vr,tv_embedded",
                    "--no-check-certificates","--retries","5","--fragment-retries","10",
                    "--retry-sleep","exp=2","--socket-timeout","60","--concurrent-fragments","4",
                    "--sleep-interval","1","--max-sleep-interval","3",
                    "--add-header","User-Agent:com.google.ios.youtube/19.45.4 (iPhone16,2; U; CPU iOS 18_1_0 like Mac OS X;)",
                    "--no-playlist"],
        "facebook": ["--no-check-certificates","--retries","10","--fragment-retries","10",
                     "--retry-sleep","3","--socket-timeout","60","--buffer-size","16K",
                     "--add-header","User-Agent:Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
                     "--add-header","Accept-Language:en-US,en;q=0.9"],
        "instagram":["--no-check-certificates","--retries","5","--socket-timeout","60",
                     "--add-header","User-Agent:Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
                     "--add-header","Accept-Language:en-US,en;q=0.9"],
        "tiktok":   ["--no-check-certificates","--impersonate","chrome","--retries","5","--socket-timeout","60",
                     "--add-header","Accept-Language:en-US,en;q=0.9","--add-header","Referer:https://www.tiktok.com/"],
        "threads":  ["--no-check-certificates","--retries","5","--socket-timeout","60",
                     "--add-header","User-Agent:Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"],
    }
    flags = flags_map.get(platform, ["--no-check-certificates","--retries","5","--socket-timeout","60"])
    if platform in {"vk","snapchat","likee"}:
        flags = ["--no-check-certificates","--retries","5","--socket-timeout","60",
                 "--add-header","User-Agent:Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"]
    fmt = _make_format_string(quality)
    if quality == "audio_only":
        cmd = ["yt-dlp",*flags,"--format",fmt,"--extract-audio","--audio-format","mp3","--audio-quality","0","-o",out,url]
    else:
        cmd = ["yt-dlp",*flags,"--format",fmt,"--merge-output-format","mp4","--postprocessor-args","ffmpeg:-c:v copy -c:a aac","-o",out,url]
    cmap = {"youtube": YOUTUBE_COOKIE_FILE, "facebook": FACEBOOK_COOKIE_FILE,
            "instagram": INSTAGRAM_COOKIE_FILE, "tiktok": TIKTOK_COOKIE_FILE, "music": SPOTIFY_COOKIE_FILE}
    cf = cmap.get(platform)
    if cf:
        cp = BASE_DIR / cf
        if cp.exists(): cmd[1:1] = ["--cookies", str(cp)]
    return cmd


def build_ytdlp_playlist_command(url, temp_dir, quality="best") -> list:
    out = str(temp_dir / "%(playlist_index)s - %(title).50s.%(ext)s")
    fmt = _make_format_string(quality)
    base = ["yt-dlp","--extractor-args","youtube:player_client=ios,web,android_vr,tv_embedded",
            "--no-check-certificates","--retries","5","--fragment-retries","10","--retry-sleep","exp=2",
            "--socket-timeout","60","--concurrent-fragments","4",
            "--add-header","User-Agent:com.google.ios.youtube/19.45.4 (iPhone16,2; U; CPU iOS 18_1_0 like Mac OS X;)",
            "--yes-playlist"]
    if quality == "audio_only":
        cmd = [*base,"--format",fmt,"--extract-audio","--audio-format","mp3","--audio-quality","0","-o",out,url]
    else:
        cmd = [*base,"--format",fmt,"--merge-output-format","mp4","--postprocessor-args","ffmpeg:-c:v copy -c:a aac","-o",out,url]
    cf = BASE_DIR / YOUTUBE_COOKIE_FILE
    if cf.exists(): cmd[1:1] = ["--cookies", str(cf)]
    return cmd


def build_ytdlp_info_command(url, platform) -> list:
    flags_map = {
        "youtube":  ["--extractor-args","youtube:player_client=ios,web,android_vr,tv_embedded",
                     "--no-check-certificates","--socket-timeout","30",
                     "--add-header","User-Agent:com.google.ios.youtube/19.45.4 (iPhone16,2; U; CPU iOS 18_1_0 like Mac OS X;)",
                     "--no-playlist"],
        "facebook": ["--no-check-certificates","--socket-timeout","30",
                     "--add-header","User-Agent:Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
                     "--add-header","Accept-Language:en-US,en;q=0.9","--add-header","Referer:https://www.facebook.com/"],
        "instagram":["--no-check-certificates","--socket-timeout","30",
                     "--add-header","User-Agent:Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
                     "--add-header","Accept-Language:en-US,en;q=0.9"],
        "tiktok":   ["--no-check-certificates","--impersonate","chrome","--socket-timeout","30",
                     "--add-header","Accept-Language:en-US,en;q=0.9","--add-header","Referer:https://www.tiktok.com/"],
        "threads":  ["--no-check-certificates","--socket-timeout","30",
                     "--add-header","User-Agent:Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"],
    }
    flags = flags_map.get(platform, ["--no-check-certificates"])
    if platform in {"vk","snapchat","likee","pinterest"}:
        flags = ["--no-check-certificates","--add-header","User-Agent:Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"]
    cmd = ["yt-dlp",*flags,"--dump-json","--no-playlist",url]
    cmap = {"youtube": YOUTUBE_COOKIE_FILE, "facebook": FACEBOOK_COOKIE_FILE,
            "instagram": INSTAGRAM_COOKIE_FILE, "tiktok": TIKTOK_COOKIE_FILE, "music": SPOTIFY_COOKIE_FILE}
    cf = cmap.get(platform)
    if cf:
        cp = BASE_DIR / cf
        if cp.exists(): cmd[1:1] = ["--cookies", str(cp)]
    return cmd


def build_ytdlp_playlist_info_command(url) -> list:
    cmd = ["yt-dlp","--extractor-args","youtube:player_client=ios,web,android_vr,tv_embedded",
           "--no-check-certificates","--socket-timeout","30",
           "--add-header","User-Agent:com.google.ios.youtube/19.45.4 (iPhone16,2; U; CPU iOS 18_1_0 like Mac OS X;)",
           "--yes-playlist","--flat-playlist","--dump-json",url]
    cf = BASE_DIR / YOUTUBE_COOKIE_FILE
    if cf.exists(): cmd[1:1] = ["--cookies", str(cf)]
    return cmd


def safe_remove_tree(path):
    if not path: return
    try:
        if path.exists(): shutil.rmtree(path, ignore_errors=True)
    except Exception as e:
        logger.warning("Could not delete temp folder %s: %s", path, e)


async def safe_edit_text(message, text: str, reply_markup=None):
    try:
        await message.edit_text(text, reply_markup=reply_markup, parse_mode="HTML")
    except Exception:
        pass


async def is_user_joined(context, user_id) -> bool:
    try:
        member = await context.bot.get_chat_member(chat_id=REQUIRED_CHANNEL_USERNAME, user_id=user_id)
        return getattr(member, "status", "") not in {"left", "kicked", "banned"}
    except Exception as e:
        logger.warning("Could not verify membership: %s", e)
        return False


async def require_join(update, context, pending_action) -> bool:
    user = update.effective_user
    if not user: return True
    if await is_user_joined(context, user.id): return False
    context.user_data["pending_action"] = pending_action
    text = f"You must join our channel first.\n\nChannel: {REQUIRED_CHANNEL_USERNAME}"
    if update.callback_query:
        await update.callback_query.answer()
        try: await update.callback_query.message.reply_text(text, reply_markup=join_keyboard())
        except Exception: pass
    else:
        msg = update.effective_message
        if msg: await msg.reply_text(text, reply_markup=join_keyboard())
    return True


async def notify_admin_new_user(context, user):
    if not ADMIN_USER_ID: return
    try:
        username  = f"@{user.username}" if user.username else "No username"
        full_name = " ".join(p for p in [user.first_name or "", user.last_name or ""] if p).strip() or "No name"
        await context.bot.send_message(
            chat_id=ADMIN_USER_ID,
            text=f"👤 New user joined\n\nName: {full_name}\nUsername: {username}\nUser ID: {user.id}",
        )
    except Exception as e:
        logger.warning("Could not notify admin: %s", e)


async def register_user_and_notify(update, context):
    user = update.effective_user
    if not user: return
    is_new = await stats_store.register_user(user)
    if is_new: await notify_admin_new_user(context, user)


# =========================
# YouTube Search
# =========================
async def _search_youtube_via_api(query, max_results=10, page=0) -> list:
    if not YOUTUBE_API_KEY: return []
    try:
        params = {"part":"snippet","q":query,"type":"video","maxResults":max_results,"key":YOUTUBE_API_KEY}
        async with aiohttp.ClientSession() as s:
            async with s.get("https://www.googleapis.com/youtube/v3/search", params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200: return []
                data = await resp.json()
        items = data.get("items", [])
        video_ids = [i.get("id",{}).get("videoId","") for i in items if i.get("id",{}).get("videoId")]
        durations = {}
        if video_ids:
            vp = {"part":"contentDetails","id":",".join(video_ids),"key":YOUTUBE_API_KEY}
            async with aiohttp.ClientSession() as s:
                async with s.get("https://www.googleapis.com/youtube/v3/videos", params=vp, timeout=aiohttp.ClientTimeout(total=15)) as r2:
                    if r2.status == 200:
                        vd = await r2.json()
                        for v in vd.get("items",[]):
                            iso = v.get("contentDetails",{}).get("duration","PT0S")
                            h = int((re.search(r"(\d+)H",iso) or re.Match()).group(1)) if re.search(r"(\d+)H",iso) else 0
                            m = int((re.search(r"(\d+)M",iso) or re.Match()).group(1)) if re.search(r"(\d+)M",iso) else 0
                            sec = int((re.search(r"(\d+)S",iso) or re.Match()).group(1)) if re.search(r"(\d+)S",iso) else 0
                            durations[v.get("id","")] = h*3600+m*60+sec
        results = []
        for item in items:
            sn = item.get("snippet",{}); vid = item.get("id",{}).get("videoId","")
            if not vid: continue
            results.append({"title":sn.get("title","Unknown"),"duration":durations.get(vid,0),
                             "channel":sn.get("channelTitle",""),"url":f"https://www.youtube.com/watch?v={vid}","views":0,"id":vid})
        return results
    except Exception as e:
        logger.error("YouTube API search error: %s", e)
        return []


async def search_youtube(query, max_results=10, page=0) -> list:
    search_url = f"ytsearch{max_results*(page+1)}:{query}"
    cmd = ["yt-dlp","--extractor-args","youtube:player_client=ios,web,android_vr",
           "--no-check-certificates","--dump-json","--no-playlist","--flat-playlist",
           "--add-header","User-Agent:com.google.ios.youtube/19.45.4 (iPhone16,2; U; CPU iOS 18_1_0 like Mac OS X;)",
           search_url]
    cp = BASE_DIR / YOUTUBE_COOKIE_FILE
    if cp.exists(): cmd[1:1] = ["--cookies", str(cp)]
    try:
        proc = await asyncio.create_subprocess_exec(*cmd, cwd=str(BASE_DIR),
                   stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=40)
        results = []
        if stdout:
            for line in stdout.decode(errors="replace").strip().split("\n"):
                line = line.strip()
                if not line: continue
                try:
                    d = json.loads(line)
                    vid = d.get("id","")
                    url = d.get("url") or d.get("webpage_url") or (f"https://www.youtube.com/watch?v={vid}" if vid else "")
                    if not url: continue
                    results.append({"title":d.get("title","Unknown"),"duration":d.get("duration",0),
                                    "channel":d.get("channel") or d.get("uploader",""),
                                    "url":url,"views":d.get("view_count",0),"id":vid})
                except Exception: continue
        if results:
            start = page * max_results
            paged = results[start:start+max_results]
            return paged if paged else results[:max_results]
        return await _search_youtube_via_api(query, max_results, page)
    except asyncio.TimeoutError:
        return await _search_youtube_via_api(query, max_results, page)
    except Exception as e:
        logger.error("YouTube search error: %s", e)
        return await _search_youtube_via_api(query, max_results, page)


def build_search_results_text(query, results, page=0) -> str:
    if not results:
        return f"❌ <b>No results found for:</b> <code>{query}</code>\n\nPlease try a different search term."
    pl = f" — Page {page+1}" if page > 0 else ""
    lines = [f"🔍 Search results: <b>{query}</b>{pl}\n"]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r['title']}")
    lines.append("\n👇 <i>Number button dabao download ke liye</i>")
    return "\n".join(lines)


# =========================
# Video Info
# =========================
async def fetch_video_info(url, platform) -> dict | None:
    tmap = {"instagram":60,"facebook":60,"tiktok":45,"youtube":50,"threads":45,"vk":45,"snapchat":45,"likee":45,"pinterest":45}
    timeout = tmap.get(platform, 45)

    def _fix_thumb(info):
        thumb = info.get("thumbnail") or ""
        if not thumb or not str(thumb).startswith("http"):
            thumbs = info.get("thumbnails") or []
            valid  = [t for t in thumbs if isinstance(t,dict) and str(t.get("url","")).startswith("http")]
            if valid:
                best = max(valid, key=lambda t:(t.get("width",0))*(t.get("height",0)))
                info["thumbnail"] = best["url"]
        return info

    try:
        cmd  = build_ytdlp_info_command(url, platform)
        proc = await asyncio.create_subprocess_exec(*cmd, cwd=str(BASE_DIR),
                   stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        if proc.returncode == 0 and stdout:
            info = json.loads(stdout.decode(errors="replace").strip().split("\n")[0])
            return _fix_thumb(info)
        if platform == "youtube":
            fb = ["yt-dlp","--extractor-args","youtube:player_client=web,android_vr",
                  "--no-check-certificates","--socket-timeout","30","--no-playlist","--dump-json",url]
            cp = BASE_DIR / YOUTUBE_COOKIE_FILE
            if cp.exists(): fb[1:1] = ["--cookies",str(cp)]
            p2 = await asyncio.create_subprocess_exec(*fb, cwd=str(BASE_DIR),
                     stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            s2, _ = await asyncio.wait_for(p2.communicate(), timeout=40)
            if p2.returncode == 0 and s2:
                return _fix_thumb(json.loads(s2.decode(errors="replace").strip().split("\n")[0]))
        if platform in ("instagram","pinterest"):
            if platform == "pinterest":
                try:
                    gp = await asyncio.create_subprocess_exec(
                        "gallery-dl","--no-mtime","--print","json",
                        "--config-option","extractor.pinterest.videos=true",url,
                        cwd=str(BASE_DIR), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                    go, _ = await asyncio.wait_for(gp.communicate(), timeout=30)
                    if go:
                        for line in go.decode(errors="replace").strip().split("\n"):
                            line = line.strip()
                            if not line: continue
                            try:
                                gd = json.loads(line)
                                item = gd[2] if isinstance(gd,list) and len(gd)>=3 else (gd if isinstance(gd,dict) else None)
                                if not item: continue
                                return _fix_thumb({"title":(item.get("title") or item.get("description") or "Pinterest Video")[:80],
                                                   "thumbnail":item.get("thumbnail") or item.get("image_url",""),
                                                   "duration":item.get("duration",0),"formats":[],"webpage_url":url})
                            except Exception: continue
                except Exception: pass
            return None
    except asyncio.TimeoutError:
        logger.warning("fetch_video_info timeout for %s/%s", platform, url[:60])
    except Exception as e:
        logger.warning("fetch_video_info error for %s: %s", platform, e)
    return None


async def fetch_playlist_info(url) -> tuple[list, str]:
    cmd = build_ytdlp_playlist_info_command(url)
    try:
        proc = await asyncio.create_subprocess_exec(*cmd, cwd=str(BASE_DIR),
                   stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
        videos, title = [], ""
        if stdout:
            for line in stdout.decode(errors="replace").strip().split("\n"):
                line = line.strip()
                if not line: continue
                try:
                    d = json.loads(line)
                    if not title: title = d.get("playlist_title") or d.get("playlist","")
                    vid = d.get("id","")
                    url_v = d.get("url") or d.get("webpage_url") or (f"https://www.youtube.com/watch?v={vid}" if vid else "")
                    if url_v:
                        videos.append({"id":vid,"title":d.get("title","Unknown"),"url":url_v,"duration":d.get("duration",0)})
                except Exception: continue
        return videos, title
    except Exception: return [], ""


def build_info_message(info, platform, sizes) -> str:
    title   = (info.get("title") or "Unknown Title")[:80]
    channel = info.get("uploader") or info.get("channel","")
    handle  = info.get("uploader_id") or info.get("channel_id","")
    views   = info.get("view_count",0) or 0
    dur     = info.get("duration",0) or 0
    likes   = info.get("like_count",0) or 0
    comments= info.get("comment_count",0) or 0
    cats    = info.get("categories",[]) or []
    cat     = cats[0] if cats else ""
    ud      = info.get("upload_date","")
    if ud and len(ud)==8: ud = f"{ud[:4]}-{ud[4:6]}-{ud[6:]}"
    lines = [f"🎬 <b>{title}</b> →"]
    if channel: lines.append(f"👤 {channel}")
    if handle and handle != channel: lines.append(f"@{handle.lstrip('@')} ✓ →")
    if views:   lines.append(f"👥 {views:,}")
    if dur:     lines.append(f"⏱ {format_duration(int(dur))}")
    sp = []
    if views:    sp.append(f"👁 {views:,}")
    if likes:    sp.append(f"👍 {likes:,}")
    if comments: sp.append(f"💬 {comments:,}")
    if sp: lines.append(" | ".join(sp))
    if cat: lines.append(f"🏷 {cat}")
    if ud:  lines.append(f"📅 {ud}")
    if sizes:
        lines.append("")
        for q in _sorted_video_heights(sizes) + _audio_labels(sizes):
            if q in sizes:
                lines.append(f"✅ {q} - {format_size(sizes[q])}")
    lines += ["", "Formats for download 📥"]
    return "\n".join(lines)


# =========================
# StatusProgress
# =========================
class StatusProgress:
    def __init__(self, status_message):
        self.status_message = status_message
        self._task    = None
        self._stopped = False
        self._last_edit = 0
        self._MIN_EDIT_INTERVAL = 4

    async def _throttled_edit(self, text):
        now = time.time()
        if now - self._last_edit >= self._MIN_EDIT_INTERVAL:
            await safe_edit_text(self.status_message, text)
            self._last_edit = now

    async def start_downloading(self, filename="", total_size=0):
        async def runner():
            start = time.time()
            last_bytes = 0
            for pct in [2,5,9,14,20,27,35,44,54,65,75,84,90,94]:
                if self._stopped: return
                elapsed = time.time() - start
                if total_size > 0 and elapsed > 0:
                    dl = int(total_size * pct / 100)
                    speed = (dl - last_bytes) / max(elapsed, 1)
                    eta   = int((total_size - dl) / speed) if speed > 0 else 0
                    last_bytes = dl
                    ss, es = format_speed(speed), format_duration(eta)
                else:
                    ss, es = "Starting up...", "Calculating..."
                bar = build_progress_bar(pct)
                await self._throttled_edit(
                    f"📥 <b>Downloading Video</b>\n\n"
                    f"┌─────《 Progress 》─────┐\n"
                    f"├» {bar} {pct}%\n"
                    f"├» 🚀 Speed: {ss}\n"
                    f"├» ⏱ ETA: {es}\n"
                    f"└──────────────────────┘"
                )
                await asyncio.sleep(3)
            while not self._stopped:
                await self._throttled_edit(
                    f"📥 <b>Downloading Video</b>\n\n"
                    f"┌─────《 Progress 》─────┐\n"
                    f"├» {build_progress_bar(94)} 94%\n"
                    f"├» 🚀 Speed: Processing...\n"
                    f"├» ⏱ ETA: Almost done...\n"
                    f"└──────────────────────┘"
                )
                await asyncio.sleep(5)
        self._task = asyncio.create_task(runner())

    async def finish_downloading(self):
        self._stopped = True
        if self._task:
            self._task.cancel()
            try: await self._task
            except BaseException: pass
        await safe_edit_text(
            self.status_message,
            f"📥 <b>Downloading Video</b>\n\n"
            f"┌─────《 Progress 》─────┐\n"
            f"├» {build_progress_bar(100)} 100%\n"
            f"├» ✅ Download complete!\n"
            f"└──────────────────────┘"
        )

    async def set_uploading(self, percent, filename="", uploaded_bytes=0,
                            total_bytes=0, speed=0, eta=0, duration=0, quality="MP4"):
        now = time.time()
        if now - self._last_edit < self._MIN_EDIT_INTERVAL: return
        self._last_edit = now
        bar  = build_progress_bar(percent)
        sn   = filename[-35:] if filename else "video"
        us   = f"{format_size(uploaded_bytes)} / {format_size(total_bytes)}" if total_bytes else ""
        ss   = format_speed(speed)
        es   = format_duration(eta) if eta > 0 else "Calculating..."
        ds   = format_duration(duration) if duration else ""
        lines = ["📤 <b>Uploading to Telegram</b>","","┌─────《 Progress 》─────┐"]
        if sn: lines.append(f"├» 🎬 File: {sn}")
        if ds: lines.append(f"├» ⏱ Duration: {ds}")
        lines.append(f"├» 📦 Quality: {quality}")
        if us: lines.append(f"├» 📊 Uploaded: {us}")
        lines += [f"├» {bar} {percent}%", f"├» 🚀 Speed: {ss}", f"├» ⏱ ETA: {es}", "└──────────────────────┘"]
        await safe_edit_text(self.status_message, "\n".join(lines))

    async def cleanup(self):
        self._stopped = True
        if self._task:
            self._task.cancel()
            try: await self._task
            except BaseException: pass


# =========================
# Downloader
# =========================
async def _run_command(command) -> tuple[bytes, bytes, int]:
    proc = await asyncio.create_subprocess_exec(*command, cwd=str(BASE_DIR),
               stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=DOWNLOAD_TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill(); await proc.wait()
        raise RuntimeError(f"⏱ Download timeout ({DOWNLOAD_TIMEOUT//3600}h). Chhoti quality try karein.")
    return stdout, stderr, proc.returncode


_ytdlp_updated_this_session = False


def _is_drm_error(s): return any(k.lower() in s.lower() for k in ["DRM protection","DRM protected","known to use DRM","Widevine","PlayReady","FairPlay"])
def _is_sig_error(s):  return any(k.lower() in s.lower() for k in ["Signature extraction failed","nsig extraction failed","Could not find JS function","Sign in to confirm","sabr"])


def _clean_error(err, platform) -> str:
    if not err: return "Download failed. Please try again."
    el = err.lower()
    if _is_drm_error(err):
        if platform == "music":
            return "❌ <b>DRM Protected</b>\n\n💡 Spotify cookies upload karo: /setcookies spotify\nYa SoundCloud / YouTube Music link try karo."
        return "❌ <b>DRM Protected Content</b>\n\nDownload nahi ho sakta."
    if any(k in el for k in ["sign in","login required","private video","members only"]):
        ch = f"/setcookies {platform}" if platform in COOKIE_FILES else ""
        return f"❌ <b>Login Required</b>\n\n" + (f"💡 Cookies upload karo: <code>{ch}</code>" if ch else "")
    if any(k in el for k in ["age-restricted","age restricted","confirm your age"]):
        ch = f"/setcookies {platform}" if platform in COOKIE_FILES else ""
        return f"❌ <b>Age Restricted</b>\n\n" + (f"💡 Cookies upload karo: <code>{ch}</code>" if ch else "")
    if any(k in el for k in ["not available in your country","geo","region"]):
        return "❌ <b>Region Restricted</b>\n\nYeh content aapke region mein available nahi."
    if any(k in el for k in ["video unavailable","has been removed","no longer available","deleted"]):
        return "❌ <b>Content Not Available</b>\n\nYeh video delete ho gayi."
    if any(k in el for k in ["no video formats found","requested format is not available"]):
        return "❌ <b>No Format Available</b>\n\nChhoti quality try karo (720p / 480p)."
    return f"❌ Download failed.\n\n<code>{err.split(chr(10))[0][:300]}</code>"


async def _auto_update_ytdlp() -> bool:
    global _ytdlp_updated_this_session
    if _ytdlp_updated_this_session: return True
    logger.warning("yt-dlp auto-updating...")
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,"-m","pip","install","--quiet","--no-cache-dir","--break-system-packages","--force-reinstall",
            "https://github.com/yt-dlp/yt-dlp/archive/refs/heads/master.zip#egg=yt-dlp",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        _, _ = await asyncio.wait_for(proc.communicate(), timeout=120)
        if proc.returncode == 0:
            _ytdlp_updated_this_session = True
            logger.info("yt-dlp auto-update SUCCESS ✅")
            return True
    except Exception as e:
        logger.error("yt-dlp auto-update error: %s", e)
    return False


async def run_downloader(url, platform, quality="best") -> tuple[list, Path]:
    temp_dir = DOWNLOAD_DIR / f"{platform}_{uuid.uuid4().hex}"
    temp_dir.mkdir(parents=True, exist_ok=True)

    if platform in GALLERY_DL_PREFERRED:
        _, _, rc = await _run_command(build_gallery_dl_command(url, temp_dir, platform))
        if rc == 0:
            files = collect_media_files(temp_dir)
            if files: return files, temp_dir
        stdout, stderr, rc = await _run_command(build_ytdlp_command(url, temp_dir, platform, quality))
        if rc != 0:
            raise RuntimeError(_clean_error((stderr or b"").decode(errors="replace").strip(), platform))
        files = collect_media_files(temp_dir)
        if not files: raise RuntimeError("No downloadable media files were found.")
        return files, temp_dir

    if platform == "instagram":
        await _run_command(build_ytdlp_command(url, temp_dir, platform, quality))
        files = collect_media_files(temp_dir)
        if files: return files, temp_dir
        await _run_command(build_gallery_dl_command(url, temp_dir, "instagram"))
        files = collect_media_files(temp_dir)
        if files: return files, temp_dir
        stdout, stderr, rc = await _run_command(["yt-dlp","--no-check-certificates","--retries","3","--socket-timeout","60",
            "--add-header","User-Agent:Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
            "-o", str(temp_dir/"%(title).50s.%(ext)s"), url])
        files = collect_media_files(temp_dir)
        if files: return files, temp_dir
        raise RuntimeError(_clean_error((stderr or b"").decode(errors="replace").strip(), platform))

    if platform in ("tiktok", "threads"):
        await _run_command(build_ytdlp_command(url, temp_dir, platform, quality))
        files = collect_media_files(temp_dir)
        if files: return files, temp_dir
        stdout, stderr, _ = await _run_command(build_gallery_dl_command(url, temp_dir, platform))
        files = collect_media_files(temp_dir)
        if files: return files, temp_dir
        raise RuntimeError(_clean_error((stderr or b"").decode(errors="replace").strip(), platform))

    stdout, stderr, rc = await _run_command(build_ytdlp_command(url, temp_dir, platform, quality))
    if rc != 0:
        files = collect_media_files(temp_dir)
        if files: return files, temp_dir
        err = (stderr or b"").decode(errors="replace").strip()
        if _is_drm_error(err): raise RuntimeError(_clean_error(err, platform))

        if platform == "youtube":
            is_audio = quality == "audio_only"
            if _is_sig_error(err):
                if await _auto_update_ytdlp():
                    await _run_command(build_ytdlp_command(url, temp_dir, platform, quality))
                    files = collect_media_files(temp_dir)
                    if files: return files, temp_dir
            last_err = err
            for client in ["ios","web","android_vr","tv_embedded","mweb"]:
                fmt = _make_format_string("audio_only" if is_audio else quality)
                base = ["yt-dlp","--extractor-args",f"youtube:player_client={client}",
                        "--no-check-certificates","--retries","5","--fragment-retries","10",
                        "--retry-sleep","exp=2","--socket-timeout","60","--concurrent-fragments","4","--no-playlist"]
                if is_audio:
                    base += ["--format",fmt,"--extract-audio","--audio-format","mp3","--audio-quality","0"]
                else:
                    base += ["--format",fmt,"--merge-output-format","mp4","--postprocessor-args","ffmpeg:-c:v copy -c:a aac"]
                base += ["-o", str(temp_dir/"%(title).50s.%(ext)s"), url]
                cf = BASE_DIR / YOUTUBE_COOKIE_FILE
                if cf.exists(): base[1:1] = ["--cookies", str(cf)]
                _, se, _ = await _run_command(base)
                files = collect_media_files(temp_dir)
                if files: return files, temp_dir
                last_err = (se or b"").decode(errors="replace").strip() or last_err
            raise RuntimeError(f"❌ YouTube download failed after all attempts.\n\n{_clean_error(last_err,'youtube')}\n\n💡 Chhoti quality try karo ya /setcookies youtube")

        if platform == "facebook":
            cf = BASE_DIR / FACEBOOK_COOKIE_FILE
            for ua, ref in [
                ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125.0.0.0 Safari/537.36","https://www.facebook.com/"),
                ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5) AppleWebKit/605.1.15 Safari/604.1","https://m.facebook.com/"),
            ]:
                alt = ["yt-dlp","--no-check-certificates","--retries","5","--socket-timeout","60",
                       "--format",_make_format_string(quality),"--merge-output-format","mp4",
                       "--postprocessor-args","ffmpeg:-c:v copy -c:a aac",
                       "-o",str(temp_dir/"%(title).50s.%(ext)s"),
                       "--add-header",f"User-Agent:{ua}","--add-header",f"Referer:{ref}",url]
                if cf.exists(): alt[1:1] = ["--cookies", str(cf)]
                _, se, _ = await _run_command(alt)
                files = collect_media_files(temp_dir)
                if files: return files, temp_dir
                err = (se or b"").decode(errors="replace").strip() or err
            raise RuntimeError(f"❌ Facebook download failed.\n{_clean_error(err,'facebook')}")

        raise RuntimeError(_clean_error(err, platform))

    files = collect_media_files(temp_dir)
    if not files: raise RuntimeError("No downloadable media files were found.")
    return files, temp_dir


async def run_playlist_downloader(url, quality="best") -> tuple[list, Path]:
    temp_dir = DOWNLOAD_DIR / f"playlist_{uuid.uuid4().hex}"
    temp_dir.mkdir(parents=True, exist_ok=True)
    stdout, stderr, rc = await _run_command(build_ytdlp_playlist_command(url, temp_dir, quality))
    files = collect_media_files(temp_dir)
    if files: return files, temp_dir
    err = (stderr or b"").decode(errors="replace").strip()
    if rc != 0: raise RuntimeError(_clean_error(err, "youtube"))
    raise RuntimeError("Playlist mein koi downloadable files nahi mili.")


# =========================
# Thumbnail
# =========================
async def extract_thumbnail_from_file(video_path: Path) -> Path | None:
    tp = video_path.with_suffix(".thumb.jpg")
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg","-y","-i",str(video_path),"-ss","00:00:01","-vframes","1","-vf","scale=320:-1","-q:v","2",str(tp),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        await asyncio.wait_for(proc.communicate(), timeout=30)
        if proc.returncode == 0 and tp.exists() and tp.stat().st_size > 0:
            return tp
    except Exception as e:
        logger.warning("Thumbnail extract error: %s", e)
    try:
        if tp.exists(): tp.unlink()
    except Exception: pass
    return None


# =========================
# ✅ FIXED: send_media_files — Pyrogram 2GB with real progress
# =========================
async def send_media_files(message, progress: StatusProgress, files: list, video_info=None):
    if not files:
        raise RuntimeError("No files to send.")

    sent_count    = 0
    caption       = build_video_caption(video_info)
    caption_short = caption[:1024]
    thumb_url     = (video_info.get("thumbnail") or "") if video_info else ""

    co_images    = [f for f in files if Path(f).suffix.lower() in IMAGE_EXTS]
    co_videos    = [f for f in files if Path(f).suffix.lower() in (VIDEO_EXTS | AUDIO_EXTS)]
    thumb_only   = set(co_images) if co_videos else set()

    for file_path in files:
        file_path = Path(file_path)
        ext       = file_path.suffix.lower()

        if file_path in thumb_only:
            continue

        file_size = file_path.stat().st_size
        duration  = int(video_info.get("duration") or 0) if video_info else 0

        # 2GB check
        if file_size > TG_MAX_FILE_SIZE:
            await safe_edit_text(
                progress.status_message,
                f"❌ File 2GB se badi hai ({format_size(file_size)}).\n💡 Chhoti quality try karo."
            )
            continue

        # Thumbnail resolve
        local_thumb: Path | None = None
        thumb_str:   str | None  = None
        thumb_tg                 = None

        if ext in VIDEO_EXTS | AUDIO_EXTS:
            if thumb_url:
                _td = file_path.with_suffix(".thumb.jpg")
                try:
                    async with aiohttp.ClientSession() as sess:
                        async with sess.get(thumb_url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                            if resp.status == 200:
                                _td.write_bytes(await resp.read())
                                if _td.stat().st_size > 0:
                                    local_thumb = _td
                                    thumb_str   = str(_td)
                                    thumb_tg    = open(_td, "rb")
                except Exception as e:
                    logger.warning("Thumb download failed: %s", e)

            if thumb_str is None:
                if thumb_url:
                    thumb_str = thumb_url
                    thumb_tg  = thumb_url
                elif co_images:
                    local_thumb = co_images[0]
                    thumb_str   = str(local_thumb)
                    thumb_tg    = open(local_thumb, "rb")
                elif ext in VIDEO_EXTS:
                    local_thumb = await extract_thumbnail_from_file(file_path)
                    if local_thumb and local_thumb.exists():
                        thumb_str = str(local_thumb)
                        thumb_tg  = open(local_thumb, "rb")

        # Pyrogram progress callback
        _start = [time.time()]

        async def _pyro_progress(current: int, total: int):
            now = time.time()
            if now - _start[0] < 4: return
            _start[0] = now
            pct    = int(current / total * 100) if total else 0
            filled = min(10, pct // 10)
            bar    = "⬢" * filled + "⬡" * (10 - filled)
            elapsed = now - _start[0]
            spd     = current / max(elapsed, 0.1)
            eta     = int((total - current) / spd) if spd > 0 else 0
            await safe_edit_text(
                progress.status_message,
                f"📤 <b>Uploading to Telegram</b>\n\n"
                f"┌─────《 Progress 》─────┐\n"
                f"├» [{bar}] {pct}%\n"
                f"├» 📦 {format_size(current)} / {format_size(total)}\n"
                f"├» 🚀 Speed: {format_speed(spd)}\n"
                f"├» ⏱ ETA: {format_duration(eta)}\n"
                f"└──────────────────────┘"
            )

        try:
            chat_id = message.chat_id
            pyro_ok = False

            # ── Pyrogram (2GB) ────────────────────────────────────────────────
            if PYROGRAM_AVAILABLE and API_ID and API_HASH:
                pyro = await get_pyro_client()
                if pyro is not None:
                    for attempt in range(1, 4):
                        try:
                            _start[0] = time.time()
                            if ext in VIDEO_EXTS:
                                await pyro.send_video(
                                    chat_id=chat_id, video=str(file_path),
                                    caption=caption_short, duration=duration or 0,
                                    thumb=thumb_str, supports_streaming=True,
                                    parse_mode="html", progress=_pyro_progress,
                                )
                            elif ext in AUDIO_EXTS:
                                _title  = (video_info.get("title","") or "")[:64]   if video_info else ""
                                _artist = (video_info.get("uploader","") or video_info.get("channel","") or "")[:64] if video_info else ""
                                await pyro.send_audio(
                                    chat_id=chat_id, audio=str(file_path),
                                    caption=caption_short, duration=duration or 0,
                                    thumb=thumb_str, title=_title or None,
                                    performer=_artist or None, parse_mode="html",
                                    progress=_pyro_progress,
                                )
                            elif ext in IMAGE_EXTS:
                                await pyro.send_photo(
                                    chat_id=chat_id, photo=str(file_path),
                                    caption=caption_short, parse_mode="html",
                                    progress=_pyro_progress,
                                )
                            else:
                                await pyro.send_document(
                                    chat_id=chat_id, document=str(file_path),
                                    caption=caption_short, parse_mode="html",
                                    progress=_pyro_progress,
                                )
                            logger.info("✅ Pyrogram upload OK: %s (%.1f MB)", file_path.name, file_size/1024/1024)
                            pyro_ok = True
                            break

                        except FloodWait as fw:
                            wait = fw.value + 2
                            logger.warning("FloodWait %ds", wait)
                            await safe_edit_text(progress.status_message, f"⏳ Telegram rate limit — {wait}s wait karo...")
                            await asyncio.sleep(wait)

                        except RPCError as rpc:
                            logger.warning("Pyrogram RPC error (attempt %d): %s", attempt, rpc)
                            global _pyro_client
                            try:
                                if _pyro_client: await _pyro_client.stop()
                            except Exception: pass
                            _pyro_client = None
                            if attempt < 3:
                                await asyncio.sleep(3)
                                pyro = await get_pyro_client()
                                if pyro is None: break

                        except Exception as pe:
                            logger.warning("Pyrogram attempt %d failed: %s", attempt, pe)
                            if attempt < 3: await asyncio.sleep(2)

                    if not pyro_ok:
                        logger.warning("Pyrogram all attempts failed — Bot API fallback")

            # ── Bot API fallback (50MB limit) ─────────────────────────────────
            if not pyro_ok:
                if file_size > TG_STANDARD_LIMIT:
                    await safe_edit_text(
                        progress.status_message,
                        f"⚠️ Pyrogram unavailable.\n"
                        f"File {format_size(file_size)} > 50MB — Bot API se upload nahi hogi.\n"
                        f"✅ .env mein API_ID aur API_HASH set karo 2GB ke liye."
                    )
                    continue

                upload_to = min(300 + file_size//(1024*1024), 18000)

                async def _bot_send():
                    kw = dict(caption=caption_short, parse_mode="HTML",
                              read_timeout=UPLOAD_READ_TIMEOUT, write_timeout=UPLOAD_WRITE_TIMEOUT,
                              connect_timeout=UPLOAD_CONNECT_TIMEOUT, pool_timeout=UPLOAD_POOL_TIMEOUT)
                    if duration: kw["duration"] = duration
                    if thumb_tg: kw["thumbnail"] = thumb_tg
                    with open(file_path, "rb") as f:
                        if ext in VIDEO_EXTS:
                            kw["supports_streaming"] = True
                            await message.reply_video(video=f, **kw)
                        elif ext in AUDIO_EXTS:
                            if video_info:
                                t = (video_info.get("title","") or "")[:64]
                                a = (video_info.get("uploader","") or video_info.get("channel","") or "")[:64]
                                if t: kw["title"]     = t
                                if a: kw["performer"] = a
                            await message.reply_audio(audio=f, **kw)
                        elif ext in IMAGE_EXTS:
                            await message.reply_photo(photo=f, caption=caption_short, parse_mode="HTML",
                                                      read_timeout=120, write_timeout=120,
                                                      connect_timeout=UPLOAD_CONNECT_TIMEOUT,
                                                      pool_timeout=UPLOAD_POOL_TIMEOUT)
                        else:
                            await message.reply_document(document=f, caption=caption_short[:4096],
                                                         parse_mode="HTML", read_timeout=UPLOAD_READ_TIMEOUT,
                                                         write_timeout=UPLOAD_WRITE_TIMEOUT,
                                                         connect_timeout=UPLOAD_CONNECT_TIMEOUT,
                                                         pool_timeout=UPLOAD_POOL_TIMEOUT)

                await asyncio.wait_for(_bot_send(), timeout=upload_to)

            sent_count += 1

        except asyncio.TimeoutError:
            raise RuntimeError(f"⏱ Upload timeout ({file_path.name}). Connection slow hai. Chhoti quality try karo.")
        except Exception as e:
            err_str = str(e)
            if "413" in err_str or "Request Entity Too Large" in err_str:
                raise RuntimeError(f"❌ File {format_size(file_size)} upload nahi ho saki.\n💡 API_ID + API_HASH set karo 2GB ke liye.")
            raise

        finally:
            if hasattr(thumb_tg, "close"):
                try: thumb_tg.close()
                except Exception: pass
            if local_thumb and local_thumb not in set(files):
                try:
                    if local_thumb.exists(): local_thumb.unlink()
                except Exception: pass

    if sent_count == 0:
        raise RuntimeError("❌ Koi bhi file send nahi ho saki.\nAPI_ID aur API_HASH .env mein set karo Pyrogram ke liye.")

    await stats_store.increment_downloads()


# =========================
# URL Handler
# =========================
async def _show_quality_for_url(msg, url, platform, status_msg):
    video_info = None
    try: video_info = await fetch_video_info(url, platform)
    except Exception as e: logger.warning("Info fetch failed for %s: %s", platform, e)

    url_key = store_url(url, platform, video_info)
    picons  = {"youtube":"▶️ YouTube","facebook":"💬 Facebook","instagram":"📷 Instagram",
               "tiktok":"🎵 TikTok","vk":"🔷 VK","snapchat":"👻 Snapchat",
               "likee":"💛 Likee","threads":"🔘 Threads","pinterest":"📍 Pinterest"}
    plat_display = picons.get(platform, platform.capitalize())

    if video_info:
        sizes    = parse_format_sizes(video_info)
        info_txt = build_info_message(video_info, platform, sizes)
        thumb    = video_info.get("thumbnail")
        try:
            if thumb:
                await msg.reply_photo(photo=thumb, caption=info_txt[:1024],
                                      reply_markup=quality_keyboard(url_key, video_info), parse_mode="HTML")
                try: await status_msg.delete()
                except Exception: pass
            else:
                await safe_edit_text(status_msg, info_txt, reply_markup=quality_keyboard(url_key, video_info))
        except Exception:
            await safe_edit_text(status_msg, info_txt, reply_markup=quality_keyboard(url_key, video_info))
    else:
        await safe_edit_text(
            status_msg,
            f"{plat_display}\n\n🎬 <b>Quality choose karo:</b>\n\n<i>ℹ️ Video info fetch nahi ho saka, lekin download hoga.</i>",
            reply_markup=quality_keyboard(url_key),
        )


async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await handle_cookie_paste(update, context): return
    await register_user_and_notify(update, context)
    msg = update.effective_message
    if not msg or not msg.text: return
    text = msg.text.strip()
    url  = extract_first_url(text)

    if url:
        url      = await resolve_facebook_share_url(url)
        platform = get_platform(url)
        if not platform:
            await msg.reply_text("⚠️ Unsupported platform. Send a link from YouTube, Instagram, TikTok, etc.")
            return
        if await require_join(update, context, {"type":"url","url":url}): return
        await context.bot.send_chat_action(chat_id=msg.chat_id, action=ChatAction.TYPING)
        if platform == "youtube" and is_youtube_playlist(url):
            await handle_youtube_playlist(msg, context, url); return
        if platform in SHOW_QUALITY_PLATFORMS:
            status = await msg.reply_text("🔍 Fetching video info...")
            await _show_quality_for_url(msg, url, platform, status); return
        status   = await msg.reply_text("⏳ Fetching media, please wait...")
        progress = StatusProgress(status)
        vi = None
        try: vi = await fetch_video_info(url, platform)
        except Exception: pass
        async with download_semaphore:
            temp_dir = None
            try:
                await progress.start_downloading()
                files, temp_dir = await run_downloader(url, platform, "best")
                await progress.finish_downloading()
                await send_media_files(msg, progress, files, video_info=vi)
                await safe_edit_text(status, "✅ Done!")
            except Exception as e:
                logger.error("Download error [%s]: %s", platform, e)
                await progress.cleanup()
                await safe_edit_text(status, f"❌ Failed: {e}")
            finally:
                safe_remove_tree(temp_dir)
        return

    if is_search_query(text):
        if await require_join(update, context, {"type":"search","query":text}): return
        await context.bot.send_chat_action(chat_id=msg.chat_id, action=ChatAction.TYPING)
        await _do_youtube_search(msg, context, text); return

    await msg.reply_text(
        "⚠️ Please send a valid URL or type a song/movie name to search YouTube.\n\n"
        "Example: <code>haseen song</code> or <code>liger movie</code>", parse_mode="HTML")


# =========================
# Commands
# =========================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await register_user_and_notify(update, context)
    user = update.effective_user
    if update.message:
        await update.message.reply_text(
            build_welcome_text(user.first_name if user else None),
            reply_markup=welcome_keyboard(), parse_mode="HTML")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "╔══════════════════════╗\n║   📖 BOT HELP GUIDE  ║\n╚══════════════════════╝\n\n"
        "🚀 <b>HOW TO USE</b>\n1️⃣ Copy any video/image link\n2️⃣ Paste & send it here\n"
        "3️⃣ Choose download quality\n4️⃣ Wait — bot downloads & sends!\n\n"
        "🌐 <b>SUPPORTED PLATFORMS</b>\n"
        "▶️ YouTube  📸 Instagram  🎵 TikTok  📌 Pinterest\n"
        "👻 Snapchat  💜 Likee  🌍 VK  🔵 Facebook  🧵 Threads\n"
        "🎶 SoundCloud  🟢 Spotify  🎧 Deezer\n\n"
        "🎬 <b>QUALITY OPTIONS</b>\n"
        "⭐ 8K  🔵 4K  💎 2K  🖥 1080p  📺 720p\n"
        "📱 480p  📉 360p  🔹 240p  🔹 144p  🎵 MP3\n\n"
        "📦 <b>FILE SIZE</b>\n🔝 Maximum: <b>2 GB</b> (Pyrogram)\n\n"
        "⚙️ <b>COMMANDS</b>\n/start  /help  /search &lt;query&gt;\n\n"
        "👨‍💻 Made with ❤️ by @anujedits76"
    )
    if update.message: await update.message.reply_text(text, parse_mode="HTML")


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message: return
    if update.effective_user and update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text("⛔ Admin only command."); return
    stats = await stats_store.get_stats()
    await update.message.reply_text(
        f"📊 <b>Bot Statistics</b>\n\n👥 Total Users: {stats['total_users']:,}\n📥 Total Downloads: {stats['total_downloads']:,}",
        parse_mode="HTML")


async def cmd_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await register_user_and_notify(update, context)
    msg = update.effective_message
    if not msg: return
    query = " ".join(context.args) if context.args else ""
    if not query.strip():
        await msg.reply_text("🔍 <b>YouTube Search</b>\n\nUsage: <code>/search haseen dillruba song</code>", parse_mode="HTML")
        return
    if await require_join(update, context, {"type":"search","query":query}): return
    await _do_youtube_search(msg, context, query)


async def _do_youtube_search(msg, context, query, page=0):
    status  = await msg.reply_text(f"🔍 Searching YouTube for: <b>{query}</b>...", parse_mode="HTML")
    results = await search_youtube(query, max_results=10, page=page)
    if not results:
        await safe_edit_text(status, f"❌ <b>No results found for:</b> <code>{query}</code>\n\nPlease try a different search term.")
        return
    sk  = store_search_results(results, query, page)
    kb  = search_results_keyboard(results, sk, page=page, has_prev=(page>0))
    txt = build_search_results_text(query, results, page)
    try: await safe_edit_text(status, txt, reply_markup=kb)
    except Exception: await safe_edit_text(status, f"🔍 <b>Results for:</b> <code>{query}</code>", reply_markup=kb)


async def resolve_facebook_share_url(url) -> str:
    try:
        parsed = urlparse(url)
        if "facebook.com" in parsed.netloc and "/share/" in parsed.path:
            async with aiohttp.ClientSession() as s:
                async with s.get(url, allow_redirects=True, timeout=aiohttp.ClientTimeout(total=10),
                                  headers={"User-Agent":"Mozilla/5.0 Chrome/125.0.0.0"}) as resp:
                    resolved = str(resp.url)
                    if "facebook.com" in resolved and resolved != url: return resolved
    except Exception: pass
    return url


async def handle_youtube_playlist(msg, context, url):
    status = await msg.reply_text("📋 Playlist info fetch ho rahi hai, please wait...")
    videos, title = await fetch_playlist_info(url)
    if not videos:
        await safe_edit_text(status, "❌ Playlist mein koi videos nahi mili.\nAgar private hai toh /setcookies youtube se cookies upload karo.")
        return
    pk = store_playlist(videos, title, url)
    td = title or "YouTube Playlist"
    pv = [f"{i}. {v.get('title','Unknown')[:50]} [{format_duration(int(v.get('duration',0)))}]" for i,v in enumerate(videos[:5],1)]
    if len(videos) > 5: pv.append(f"... aur {len(videos)-5} videos")
    await safe_edit_text(status,
        f"📋 <b>{td}</b>\n\n📊 Total Videos: <b>{len(videos)}</b>\n\n<b>Preview:</b>\n" + "\n".join(pv) +
        "\n\n👇 <b>Quality choose karo:</b>",
        reply_markup=playlist_keyboard(pk))


# =========================
# Callbacks
# =========================
async def handle_search_result_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query: return
    await query.answer()
    data = query.data or ""

    if data == "sr_cancel":
        try: await query.message.delete()
        except Exception: pass
        return

    if data.startswith("sr_page|"):
        parts = data.split("|", 3)
        if len(parts) != 4: return
        _, cp_str, direction, sk = parts
        cp = int(cp_str) if cp_str.isdigit() else 0
        sq = get_search_query(sk)
        if not sq: await query.answer("⚠️ Search session expired.", show_alert=True); return
        np = max(0, cp-1) if direction == "prev" else cp+1
        nr = await search_youtube(sq, max_results=10, page=np)
        if not nr: await query.answer("❌ Aur results nahi mile.", show_alert=True); return
        nk  = store_search_results(nr, sq, np)
        txt = build_search_results_text(sq, nr, np)
        kb  = search_results_keyboard(nr, nk, page=np, has_prev=(np>0))
        try: await query.message.delete()
        except Exception: pass
        await query.message.chat.send_message(txt, reply_markup=kb, parse_mode="HTML")
        return

    if not data.startswith("sr|"): return
    parts = data.split("|", 2)
    if len(parts) != 3: return
    _, idx_str, sk = parts
    try: idx = int(idx_str)
    except ValueError: return
    results = get_search_results(sk)
    if not results: await query.message.reply_text("⚠️ Search session expired."); return
    if idx < 0 or idx >= len(results): await query.message.reply_text("⚠️ Invalid selection."); return
    url = results[idx]["url"]
    cleanup_search_results(sk)
    try: await query.message.edit_reply_markup(reply_markup=None)
    except Exception: pass
    if await require_join(update, context, {"type":"url","url":url}): return
    status = await query.message.reply_text("🔍 Fetching video info...")
    await _show_quality_for_url(query.message, url, "youtube", status)


async def handle_thumb_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query: return
    await query.answer()
    data = query.data or ""
    if not data.startswith("thumb|"): return
    url_key = data.split("|",1)[1]
    result  = get_url(url_key)
    if not result: await query.answer("⚠️ Session expired.", show_alert=True); return
    url, platform = result
    msg = query.message
    if not msg: return
    vi = None
    try: vi = await fetch_video_info(url, platform)
    except Exception: pass
    if not vi: await msg.reply_text("❌ Could not fetch video info."); return
    thumb = vi.get("thumbnail","")
    if not thumb: await msg.reply_text("❌ No thumbnail found."); return
    try: await msg.reply_photo(photo=thumb, caption=f"🖼 <b>Thumbnail</b>\n{(vi.get('title',''))[:60]}", parse_mode="HTML")
    except Exception as e: await msg.reply_text(f"❌ Thumbnail send nahi ho saka: {e}")


async def handle_desc_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query: return
    await query.answer()
    data = query.data or ""
    if not data.startswith("desc|"): return
    url_key = data.split("|",1)[1]
    result  = get_url(url_key)
    if not result: await query.answer("⚠️ Session expired.", show_alert=True); return
    url, platform = result
    msg = query.message
    if not msg: return
    vi = None
    try: vi = await fetch_video_info(url, platform)
    except Exception: pass
    if not vi: await msg.reply_text("❌ Could not fetch video info."); return
    title = (vi.get("title","Unknown Title"))[:80]
    desc  = (vi.get("description","")).strip()
    if not desc: await msg.reply_text(f"🎬 <b>{title}</b>\n\n📝 <i>No description available.</i>", parse_mode="HTML"); return
    header = f"📝 <b>Description</b>\n🎬 {title}\n\n"
    if len(desc) > 4096 - len(header) - 30: desc = desc[:4096-len(header)-30] + "…"
    await msg.reply_text(header+desc, parse_mode="HTML", disable_web_page_preview=True)


async def handle_quality_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query: return
    await query.answer()
    data = query.data or ""
    if not data.startswith("q|"): return
    parts = data.split("|", 2)
    if len(parts) != 3: return
    _, quality, url_key = parts
    result = get_url_with_info(url_key)
    if not result: await query.message.reply_text("⚠️ Session expired. Please send the URL again."); return
    url, platform, stored_vi = result
    msg = query.message
    if not msg: return
    if await require_join(update, context, {"type":"quality","url":url,"quality":quality,"platform":platform}): return
    cleanup_url(url_key)
    try: await msg.edit_reply_markup(reply_markup=None)
    except Exception: pass
    status   = await msg.reply_text("⏳ Starting download...")
    progress = StatusProgress(status)
    vi = stored_vi
    if not vi:
        try: vi = await fetch_video_info(url, platform)
        except Exception: pass
    async with download_semaphore:
        temp_dir = None
        try:
            hint = 0
            if vi:
                sizes = parse_format_sizes(vi)
                ql    = _audio_labels(sizes)[0] if quality == "audio_only" and _audio_labels(sizes) else (_sorted_video_heights(sizes)[0] if quality == "best" and _sorted_video_heights(sizes) else quality)
                hint  = sizes.get(ql, 0)
            await progress.start_downloading(total_size=hint)
            files, temp_dir = await run_downloader(url, platform, quality)
            await progress.finish_downloading()
            await send_media_files(msg, progress, files, video_info=vi)
            await safe_edit_text(status, "✅ Done!")
        except Exception as e:
            logger.error("Quality download error [%s/%s]: %s", platform, quality, e)
            await progress.cleanup()
            await safe_edit_text(status, f"❌ {e}")
        finally:
            safe_remove_tree(temp_dir)


async def handle_playlist_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query: return
    await query.answer()
    data = query.data or ""
    if data == "pl_cancel":
        try: await query.message.delete()
        except Exception: pass
        return
    if not data.startswith("pl|"): return
    parts = data.split("|", 2)
    if len(parts) != 3: return
    _, quality, pk = parts
    result = get_playlist(pk)
    if not result: await query.message.reply_text("⚠️ Session expired. Please send the playlist URL again."); return
    videos, pl_title, pl_url = result
    cleanup_playlist(pk)
    msg = query.message
    if not msg: return
    if await require_join(update, context, {"type":"playlist","playlist_key":pk,"quality":quality}): return
    try: await msg.edit_reply_markup(reply_markup=None)
    except Exception: pass
    ql_display = {"best":"🔥 Best","1080p":"🖥 1080p","720p":"📺 720p","480p":"📱 480p","360p":"📉 360p","audio_only":"🎵 Audio MP3"}.get(quality, quality)
    status = await msg.reply_text(
        f"📋 <b>Playlist Download Shuru!</b>\n\n📊 Total: {len(videos)} videos\n📦 Quality: {ql_display}\n\n⏳ Please wait...",
        parse_mode="HTML")
    progress = StatusProgress(status)
    async with download_semaphore:
        temp_dir = None
        try:
            await progress.start_downloading()
            files, temp_dir = await run_playlist_downloader(pl_url, quality)
            await progress.finish_downloading()
            await safe_edit_text(status, f"📤 <b>Uploading {len(files)} files...</b>")
            for i, fp in enumerate(files, 1):
                try:
                    fp   = Path(fp)
                    ext  = fp.suffix.lower()
                    fsz  = fp.stat().st_size
                    cap  = f"📋 {pl_title}\n{i}/{len(files)}\n\n{FILE_CAPTION_BASE}"
                    await safe_edit_text(status, f"📤 Uploading {i}/{len(files)}: {fp.name[:40]}...")
                    pyro_ok = False
                    if PYROGRAM_AVAILABLE and API_ID and API_HASH and ext in VIDEO_EXTS | AUDIO_EXTS:
                        pyro = await get_pyro_client()
                        if pyro:
                            try:
                                if ext in VIDEO_EXTS:
                                    await pyro.send_video(chat_id=msg.chat_id, video=str(fp), caption=cap[:1024], supports_streaming=True, parse_mode="html")
                                else:
                                    await pyro.send_audio(chat_id=msg.chat_id, audio=str(fp), caption=cap[:1024], parse_mode="html")
                                pyro_ok = True
                            except Exception as pe:
                                logger.warning("Playlist Pyrogram failed: %s", pe)
                    if not pyro_ok:
                        with open(fp,"rb") as f:
                            if ext in VIDEO_EXTS:
                                if fsz > TG_STANDARD_LIMIT:
                                    await msg.reply_document(document=f, caption=cap[:4096], filename=fp.name, parse_mode="HTML",
                                                             read_timeout=UPLOAD_READ_TIMEOUT, write_timeout=UPLOAD_WRITE_TIMEOUT,
                                                             connect_timeout=UPLOAD_CONNECT_TIMEOUT, pool_timeout=UPLOAD_POOL_TIMEOUT)
                                else:
                                    await msg.reply_video(video=f, caption=cap[:1024], supports_streaming=True, parse_mode="HTML",
                                                          read_timeout=UPLOAD_READ_TIMEOUT, write_timeout=UPLOAD_WRITE_TIMEOUT,
                                                          connect_timeout=UPLOAD_CONNECT_TIMEOUT, pool_timeout=UPLOAD_POOL_TIMEOUT)
                            elif ext in AUDIO_EXTS:
                                await msg.reply_audio(audio=f, caption=cap[:1024], parse_mode="HTML",
                                                      read_timeout=UPLOAD_READ_TIMEOUT, write_timeout=UPLOAD_WRITE_TIMEOUT,
                                                      connect_timeout=UPLOAD_CONNECT_TIMEOUT, pool_timeout=UPLOAD_POOL_TIMEOUT)
                    await stats_store.increment_downloads()
                    await asyncio.sleep(0.5)
                except Exception as e:
                    logger.error("Playlist file send error: %s", e)
                    await msg.reply_text(f"⚠️ {fp.name[:40]} send nahi ho saka: {str(e)[:100]}")
            await safe_edit_text(status, f"✅ <b>Playlist Complete!</b>\n\n📊 {len(files)} files sent!\n📋 {pl_title}")
        except Exception as e:
            logger.error("Playlist download error: %s", e)
            await progress.cleanup()
            await safe_edit_text(status, f"❌ Playlist download failed:\n{e}")
        finally:
            safe_remove_tree(temp_dir)


async def handle_check_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query: return
    user = query.from_user
    if not user: await query.answer("Could not verify.", show_alert=True); return
    if await is_user_joined(context, user.id):
        await query.answer("✅ Verified! You can now use the bot.", show_alert=True)
        try: await query.message.delete()
        except Exception: pass
        pending = context.user_data.pop("pending_action", None)
        if not pending: return
        msg = query.message
        if pending.get("type") == "search":
            if msg: await _do_youtube_search(msg, context, pending.get("query",""))
        elif pending.get("type") == "quality":
            url,quality,platform = pending["url"],pending["quality"],pending["platform"]
            if msg:
                status   = await msg.reply_text("⏳ Starting download...")
                progress = StatusProgress(status)
                vi = None
                try: vi = await fetch_video_info(url, platform)
                except Exception: pass
                async with download_semaphore:
                    td = None
                    try:
                        await progress.start_downloading()
                        files, td = await run_downloader(url, platform, quality)
                        await progress.finish_downloading()
                        await send_media_files(msg, progress, files, video_info=vi)
                        await safe_edit_text(status, "✅ Done!")
                    except Exception as e:
                        await progress.cleanup(); await safe_edit_text(status, f"❌ {e}")
                    finally: safe_remove_tree(td)
        elif pending.get("type") == "url":
            if msg:
                url = pending["url"]; platform = get_platform(url)
                if platform == "youtube" and is_youtube_playlist(url):
                    await handle_youtube_playlist(msg, context, url); return
                if platform in SHOW_QUALITY_PLATFORMS:
                    status = await msg.reply_text("🔍 Fetching video info...")
                    await _show_quality_for_url(msg, url, platform, status)
                elif platform:
                    status   = await msg.reply_text("⏳ Fetching media, please wait...")
                    progress = StatusProgress(status)
                    async with download_semaphore:
                        td = None
                        try:
                            vi = None
                            try: vi = await fetch_video_info(url, platform)
                            except Exception: pass
                            await progress.start_downloading()
                            files, td = await run_downloader(url, platform, "best")
                            await progress.finish_downloading()
                            await send_media_files(msg, progress, files, video_info=vi)
                            await safe_edit_text(status, "✅ Done!")
                        except Exception as e:
                            await progress.cleanup(); await safe_edit_text(status, f"❌ Failed: {e}")
                        finally: safe_remove_tree(td)
    else:
        await query.answer("❌ You haven't joined yet. Please join and try again.", show_alert=True)


# =========================
# Cookie Handlers
# =========================
async def cmd_cookies(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message: return
    if not update.effective_user or update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text("⛔ Admin only command."); return
    await update.message.reply_text(format_cookie_status_text(), parse_mode="HTML")


async def cmd_setcookies(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message: return
    if not update.effective_user or update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text("⛔ Admin only command."); return
    platform = (context.args[0].lower() if context.args else "").strip()
    if platform not in COOKIE_FILES:
        await update.message.reply_text(
            "📋 <b>Cookie Update:</b>\n\n/setcookies youtube\n/setcookies instagram\n"
            "/setcookies facebook\n/setcookies tiktok\n/setcookies spotify", parse_mode="HTML"); return
    _cookie_pending[update.effective_user.id] = platform
    dm = {"youtube":"youtube.com","instagram":"instagram.com","facebook":"facebook.com","tiktok":"tiktok.com","spotify":"open.spotify.com"}
    await update.message.reply_text(
        f"🍪 <b>{platform.capitalize()} Cookie Update</b>\n\n"
        f"Ab <b>{dm.get(platform,platform+'.com')}</b> ka Netscape cookie content paste karo.\n\n"
        f"❌ Cancel: /cancel", parse_mode="HTML")


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message: return
    uid = update.effective_user.id
    if uid in _cookie_pending:
        p = _cookie_pending.pop(uid)
        await update.message.reply_text(f"❌ {p.capitalize()} cookie update cancel ho gaya.")
    else:
        await update.message.reply_text("Koi pending operation nahi hai.")


async def handle_cookie_paste(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if not update.effective_user or update.effective_user.id != ADMIN_USER_ID: return False
    if not update.message: return False
    uid = update.effective_user.id
    if uid not in _cookie_pending: return False
    platform = _cookie_pending[uid]
    text = (update.effective_message.text or "").strip()
    has_header      = "Netscape HTTP Cookie File" in text
    has_tabs        = "\t" in text
    has_cookie_lines = any(len(l.split())>=7 for l in text.splitlines() if l.strip() and not l.startswith("#"))
    if not has_header and not has_tabs and not has_cookie_lines:
        await update.message.reply_text("⚠️ Valid Netscape cookie format nahi hai.\nDobara try karo ya /cancel karo.", parse_mode="HTML")
        return True
    valid_lines = [l for l in text.splitlines() if l.strip() and not l.startswith("#") and (len(l.split("\t"))>=7 or len(l.split())>=7)]
    if not valid_lines:
        await update.message.reply_text("⚠️ Koi valid cookie lines nahi mili.\nDobara try karo ya /cancel karo.", parse_mode="HTML")
        return True
    cp = COOKIE_FILES[platform]; cp.parent.mkdir(parents=True, exist_ok=True)
    try:
        cp.write_text(text, encoding="utf-8"); _cookie_pending.pop(uid, None)
        info = get_cookie_expiry_info(); pi = info.get(platform,{}); dl = pi.get("days_left")
        await update.message.reply_text(
            f"✅ <b>{platform.capitalize()} cookies saved!</b>\n\n📊 {len(valid_lines)} lines saved\n"
            f"⏳ Validity: {f'{dl} days' if dl is not None else 'session only'}\n\nUse /cookies to check.", parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"❌ Cookie save nahi ho saka: {e}")
    return True


async def check_and_notify_cookie_expiry(context: ContextTypes.DEFAULT_TYPE):
    if not ADMIN_USER_ID: return
    info = get_cookie_expiry_info(); alerts = []
    for platform, data in info.items():
        if data["status"] == "expired":      alerts.append(f"❌ <b>{platform.capitalize()}</b>: EXPIRED {abs(data['days_left'])} days ago!")
        elif data["status"] == "expiring_soon": alerts.append(f"⚠️ <b>{platform.capitalize()}</b>: Expires in {data['days_left']} days!")
        elif data["status"] in ("missing","empty"): alerts.append(f"🚫 <b>{platform.capitalize()}</b>: Cookie file missing!")
    if alerts:
        try: await context.bot.send_message(chat_id=ADMIN_USER_ID,
                 text="🍪 <b>Cookie Expiry Alert</b>\n\n" + "\n".join(alerts) + "\n\n/cookies se status check karo.", parse_mode="HTML")
        except Exception: pass


# =========================
# App Setup
# =========================
async def post_init(application: Application):
    logger.info("Bot started: @%s", BOT_USERNAME)
    if PYROGRAM_AVAILABLE and API_ID and API_HASH:
        await get_pyro_client()
        logger.info("🚀 Pyrogram ready — 2GB upload enabled!")
    else:
        logger.info("ℹ️ Pyrogram disabled — set API_ID & API_HASH for 2GB upload.")


def build_application() -> Application:
    global download_semaphore
    download_semaphore = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)
    req = HTTPXRequest(
        connection_pool_size=8,
        read_timeout=UPLOAD_READ_TIMEOUT,
        write_timeout=UPLOAD_WRITE_TIMEOUT,
        connect_timeout=UPLOAD_CONNECT_TIMEOUT,
        pool_timeout=UPLOAD_POOL_TIMEOUT,
    )
    app = (Application.builder().token(BOT_TOKEN).request(req).post_init(post_init)).build()
    app.add_handler(CommandHandler("start",      cmd_start))
    app.add_handler(CommandHandler("help",       cmd_help))
    app.add_handler(CommandHandler("stats",      cmd_stats))
    app.add_handler(CommandHandler("search",     cmd_search))
    app.add_handler(CommandHandler("cookies",    cmd_cookies))
    app.add_handler(CommandHandler("setcookies", cmd_setcookies))
    app.add_handler(CommandHandler("cancel",     cmd_cancel))
    if app.job_queue:
        app.job_queue.run_repeating(check_and_notify_cookie_expiry, interval=86400, first=60)
        app.job_queue.run_repeating(_self_ping, interval=600, first=60)
    app.add_handler(CallbackQueryHandler(handle_check_join,             pattern="^check_join$"))
    app.add_handler(CallbackQueryHandler(handle_search_result_callback, pattern=r"^sr[\|]"))
    app.add_handler(CallbackQueryHandler(handle_search_result_callback, pattern="^sr_cancel$"))
    app.add_handler(CallbackQueryHandler(handle_search_result_callback, pattern=r"^sr_page\|\d+\|"))
    app.add_handler(CallbackQueryHandler(handle_playlist_callback,      pattern=r"^pl\|"))
    app.add_handler(CallbackQueryHandler(handle_playlist_callback,      pattern="^pl_cancel$"))
    app.add_handler(CallbackQueryHandler(handle_quality_callback,       pattern=r"^q\|"))
    app.add_handler(CallbackQueryHandler(handle_thumb_callback,         pattern=r"^thumb\|"))
    app.add_handler(CallbackQueryHandler(handle_desc_callback,          pattern=r"^desc\|"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url))
    return app


# =========================
# Webhook / Polling
# =========================
@flask_app.route(f"/{BOT_TOKEN}", methods=["POST"])
def webhook_handler():
    data = request.get_json(force=True)
    application = flask_app.config["application"]
    loop        = flask_app.config["loop"]
    asyncio.run_coroutine_threadsafe(
        application.process_update(Update.de_json(data, application.bot)), loop,
    ).result(timeout=120)
    return "OK"


@flask_app.route("/", methods=["GET"])
def health(): return "Bot is running ✅"


def _ping_target() -> str:
    for k in ("PING_URL","HEALTHCHECK_URL","RENDER_EXTERNAL_URL","APP_URL"):
        v = os.environ.get(k,"").strip()
        if v: return v.rstrip("/")
    h = os.environ.get("RENDER_EXTERNAL_HOSTNAME","").strip().strip("/")
    return f"https://{h}" if h else f"http://127.0.0.1:{PORT}"


async def _self_ping(context: ContextTypes.DEFAULT_TYPE):
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(_ping_target(), timeout=aiohttp.ClientTimeout(total=10)) as resp:
                logger.debug("Self-ping → %d", resp.status)
    except Exception: pass


def run_flask(app, loop):
    flask_app.config["application"] = app
    flask_app.config["loop"]        = loop
    flask_app.run(host="0.0.0.0", port=PORT, threaded=True)


def auto_update_ytdlp():
    try:
        r = subprocess.run([sys.executable,"-m","pip","install","--force-reinstall","--no-cache-dir","yt-dlp","--quiet"],
                           capture_output=True, text=True, timeout=180)
        if r.returncode == 0:
            v = subprocess.run(["yt-dlp","--version"], capture_output=True, text=True, timeout=10)
            logger.info("✅ yt-dlp updated: v%s", v.stdout.strip())
        else:
            logger.warning("⚠️ yt-dlp update failed: %s", r.stderr[:200])
    except Exception as e:
        logger.warning("⚠️ yt-dlp update error: %s", e)
    try:
        subprocess.run([sys.executable,"-m","pip","install","-U","--no-cache-dir","curl_cffi","--quiet"],
                       capture_output=True, text=True, timeout=180)
        logger.info("✅ curl_cffi updated")
    except Exception: pass


def main():
    auto_update_ytdlp()
    application = build_application()
    if WEBHOOK_URL:
        async def run_webhook():
            await application.initialize()
            await application.bot.set_webhook(
                url=f"{WEBHOOK_URL.rstrip('/')}/{BOT_TOKEN}",
                allowed_updates=["message","callback_query"])
            loop   = asyncio.get_running_loop()
            thread = threading.Thread(target=run_flask, args=(application,loop), daemon=True)
            thread.start()
            logger.info("Webhook mode on port %d", PORT)
            await application.start()
            try:
                while True: await asyncio.sleep(3600)
            except (KeyboardInterrupt, SystemExit): pass
            finally:
                await application.stop(); await application.shutdown()
        asyncio.run(run_webhook())
    else:
        logger.info("Polling mode")
        threading.Thread(target=flask_app.run,
                         kwargs={"host":"0.0.0.0","port":PORT,"threaded":True},
                         daemon=True).start()
        logger.info("Health server started on port %d", PORT)
        application.run_polling(allowed_updates=["message","callback_query"], drop_pending_updates=True)


if __name__ == "__main__":
    main()
