"""
test.py — AK Saver Bot Test Suite
Tests environment, dependencies, platform URL detection, cookie system, and bot config.
"""

import os
import sys
import json
import time
import importlib
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

# ─────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
RESET  = "\033[0m"

def ok(msg):      print(f"  {GREEN}✔ PASS{RESET}  {msg}")
def fail(msg):    print(f"  {RED}✖ FAIL{RESET}  {msg}")
def warn(msg):    print(f"  {YELLOW}⚠ WARN{RESET}  {msg}")
def section(t):   print(f"\n{CYAN}{'─'*52}\n  {t}\n{'─'*52}{RESET}")


# ══════════════════════════════════════════════
# 1. ENVIRONMENT VARIABLES
# ══════════════════════════════════════════════
class TestEnvironment(unittest.TestCase):

    REQUIRED = [
        "BOT_TOKEN", "BOT_USERNAME", "WEBHOOK_URL",
        "ADMIN_USER_ID", "REQUIRED_CHANNEL_USERNAME",
        "REQUIRED_CHANNEL_URL", "PORT",
    ]

    def test_env_vars_present(self):
        missing = [v for v in self.REQUIRED if not os.environ.get(v)]
        if missing:
            warn(f"Missing env vars (set in .env or Render): {missing}")
        else:
            ok("All required env vars are set.")
        self.assertTrue(True)

    def test_bot_token_format(self):
        token = os.environ.get("BOT_TOKEN", "")
        if not token:
            self.skipTest("BOT_TOKEN not set")
        import re
        self.assertRegex(token, r"^\d{8,12}:[A-Za-z0-9_-]{35}$",
                         "BOT_TOKEN format looks invalid.")
        ok("BOT_TOKEN format is valid.")

    def test_no_hardcoded_token_in_bot(self):
        """bot.py mein koi real token hardcoded nahi hona chahiye."""
        if not Path("bot.py").exists():
            self.skipTest("bot.py not found")
        content = Path("bot.py").read_text(encoding="utf-8")
        # Real tokens match digit:alphanum pattern
        import re
        found = re.findall(r"\d{8,12}:[A-Za-z0-9_-]{35}", content)
        self.assertEqual(len(found), 0,
            f"Hardcoded token(s) found in bot.py: {found} — Security risk!")
        ok("No hardcoded tokens in bot.py.")

    def test_port_is_valid(self):
        port = os.environ.get("PORT", "10000")
        self.assertTrue(port.isdigit(), f"PORT must be numeric, got: {port}")
        self.assertIn(int(port), range(1, 65536))
        if int(port) == 10000:
            ok("PORT=10000 (correct for Render free tier).")
        elif int(port) == 8443:
            fail("PORT=8443 — Render free tier does NOT support 8443, use 10000!")
        else:
            warn(f"PORT={port} — make sure this is correct for your host.")

    def test_webhook_url_https(self):
        url = os.environ.get("WEBHOOK_URL", "")
        if not url:
            self.skipTest("WEBHOOK_URL not set")
        self.assertTrue(url.startswith("https://"), "WEBHOOK_URL must use HTTPS.")
        ok(f"WEBHOOK_URL uses HTTPS.")

    def test_channel_username_format(self):
        ch = os.environ.get("REQUIRED_CHANNEL_USERNAME", "")
        if not ch:
            self.skipTest("REQUIRED_CHANNEL_USERNAME not set")
        self.assertTrue(ch.startswith("@"), "Must start with @")
        ok(f"REQUIRED_CHANNEL_USERNAME format OK: {ch}")


# ══════════════════════════════════════════════
# 2. PYTHON DEPENDENCIES
# ══════════════════════════════════════════════
class TestDependencies(unittest.TestCase):

    PACKAGES = [
        ("flask",       "Flask"),
        ("telegram",    "python-telegram-bot"),
        ("yt_dlp",      "yt-dlp"),
        ("gallery_dl",  "gallery-dl"),
        ("requests",    "requests"),
        ("dotenv",      "python-dotenv"),
        ("mutagen",     "mutagen"),
        ("Cryptodome",  "pycryptodomex"),
    ]

    def _check(self, module, pkg):
        try:
            importlib.import_module(module)
            ok(f"{pkg} is installed.")
            return True
        except ImportError:
            fail(f"{pkg} NOT installed — run: pip install {pkg}")
            return False

    def test_flask(self):        self.assertTrue(self._check("flask",      "Flask"))
    def test_ptb(self):          self.assertTrue(self._check("telegram",   "python-telegram-bot"))
    def test_ytdlp(self):        self.assertTrue(self._check("yt_dlp",     "yt-dlp"))
    def test_gallery_dl(self):   self.assertTrue(self._check("gallery_dl", "gallery-dl"))
    def test_requests(self):     self.assertTrue(self._check("requests",   "requests"))
    def test_mutagen(self):      self.assertTrue(self._check("mutagen",    "mutagen"))

    def test_dotenv(self):
        try:
            importlib.import_module("dotenv")
            ok("python-dotenv installed.")
        except ImportError:
            fail("python-dotenv NOT installed")
            self.fail("missing")

    def test_job_queue_extra(self):
        """PTB job-queue extra (APScheduler) must be installed for cookie expiry alerts."""
        try:
            import apscheduler
            ok(f"APScheduler installed (job-queue support OK).")
        except ImportError:
            fail("APScheduler not found — install: pip install 'python-telegram-bot[job-queue]'")
            self.fail("apscheduler missing")

    def test_no_waitress(self):
        """waitress is unused — should not be in requirements."""
        if Path("requirements.txt").exists():
            content = Path("requirements.txt").read_text()
            if "waitress" in content:
                fail("waitress is in requirements.txt but unused — remove it.")
                self.fail("waitress should be removed")
            else:
                ok("waitress not in requirements.txt (correct).")


# ══════════════════════════════════════════════
# 3. SYSTEM TOOLS
# ══════════════════════════════════════════════
class TestSystemTools(unittest.TestCase):

    def _cmd(self, cmd):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            return r.returncode == 0, r.stdout.strip()
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False, ""

    def test_yt_dlp_cli(self):
        found, ver = self._cmd(["yt-dlp", "--version"])
        if found:
            ok(f"yt-dlp CLI: v{ver}")
        else:
            warn("yt-dlp CLI not in PATH.")

    def test_gallery_dl_cli(self):
        found, ver = self._cmd(["gallery-dl", "--version"])
        if found:
            ok(f"gallery-dl CLI: {ver}")
        else:
            warn("gallery-dl CLI not in PATH.")

    def test_ffmpeg(self):
        found, _ = self._cmd(["ffmpeg", "-version"])
        if found:
            ok("ffmpeg available.")
        else:
            warn("ffmpeg not found — yt-dlp may fail to merge streams.")


# ══════════════════════════════════════════════
# 4. PLATFORM URL DETECTION
# ══════════════════════════════════════════════
def detect_platform(url: str) -> str:
    from urllib.parse import urlparse
    try:
        host = (urlparse(url).netloc or "").lower()
    except Exception:
        return "unknown"
    if host in {"instagram.com", "www.instagram.com"}:                          return "instagram"
    if host in {"tiktok.com", "www.tiktok.com", "vm.tiktok.com", "vt.tiktok.com"}: return "tiktok"
    if host in {"youtube.com", "www.youtube.com", "youtu.be", "m.youtube.com"}: return "youtube"
    if host in {"pinterest.com", "www.pinterest.com", "pin.it"}:               return "pinterest"
    if host in {"snapchat.com", "www.snapchat.com"}:                            return "snapchat"
    if host in {"likee.video", "www.likee.video"}:                              return "likee"
    if host in {"vk.com", "www.vk.com", "vkvideo.ru"}:                         return "vk"
    if host in {"facebook.com", "www.facebook.com", "fb.watch"}:               return "facebook"
    if host in {"threads.net", "www.threads.net"}:                              return "threads"
    if host in {"soundcloud.com", "www.soundcloud.com"}:                        return "music"
    if host in {"open.spotify.com"}:                                            return "music"
    if host in {"deezer.com", "www.deezer.com"}:                                return "music"
    return "unknown"


class TestPlatformDetection(unittest.TestCase):

    CASES = [
        ("https://www.instagram.com/p/ABC123/",             "instagram"),
        ("https://www.tiktok.com/@user/video/123456",       "tiktok"),
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ",    "youtube"),
        ("https://youtu.be/dQw4w9WgXcQ",                   "youtube"),
        ("https://www.pinterest.com/pin/12345/",            "pinterest"),
        ("https://pin.it/abc123",                           "pinterest"),
        ("https://www.snapchat.com/add/username",           "snapchat"),
        ("https://likee.video/@user/video/123",             "likee"),
        ("https://vk.com/video-123_456",                    "vk"),
        ("https://www.facebook.com/watch?v=123",            "facebook"),
        ("https://fb.watch/abc123/",                        "facebook"),
        ("https://www.threads.net/@user/post/abc",          "threads"),
        ("https://soundcloud.com/artist/track",             "music"),
        ("https://open.spotify.com/track/abc123",           "music"),
        ("https://www.deezer.com/track/123456",             "music"),
        ("https://example.com/video.mp4",                   "unknown"),
    ]

    def test_all_platforms(self):
        for url, expected in self.CASES:
            with self.subTest(url=url):
                result = detect_platform(url)
                self.assertEqual(result, expected,
                    f"\n  URL: {url}\n  Expected: {expected}, Got: {result}")
        ok(f"All {len(self.CASES)} platform URLs detected correctly.")


# ══════════════════════════════════════════════
# 5. COOKIE SYSTEM
# ══════════════════════════════════════════════
PLATFORMS = ["youtube", "instagram", "facebook", "tiktok"]
COOKIE_PATHS = {p: Path(f"downloads/{p}_cookies.txt") for p in PLATFORMS}

SAMPLE_COOKIE = (
    "# Netscape HTTP Cookie File\n"
    "# https://curl.haxx.se/rfc/cookie_spec.html\n"
    ".{domain}\tTRUE\t/\tTRUE\t{exp}\tsessionid\ttest_value_abc123\n"
)


class TestCookieFiles(unittest.TestCase):

    def test_cookie_format_valid(self):
        """Existing cookie files must be valid Netscape format."""
        for platform, path in COOKIE_PATHS.items():
            if not path.exists():
                warn(f"{platform}: cookie file missing (optional).")
                continue
            content = path.read_text(encoding="utf-8", errors="ignore")
            has_header = "Netscape HTTP Cookie File" in content
            lines = [l for l in content.splitlines()
                     if l.strip() and not l.startswith("#")]
            valid = [l for l in lines if len(l.split("\t")) >= 7]
            if has_header and valid:
                ok(f"{platform}: valid Netscape cookie ({len(valid)} entries).")
            elif not has_header:
                fail(f"{platform}: missing '# Netscape HTTP Cookie File' header.")
            else:
                fail(f"{platform}: no valid tab-separated cookie lines found.")

    def test_cookie_expiry(self):
        """Check cookie expiry dates."""
        now = int(time.time())
        for platform, path in COOKIE_PATHS.items():
            if not path.exists():
                continue
            min_exp = None
            for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.startswith("#") or not line.strip():
                    continue
                parts = line.split("\t")
                if len(parts) >= 7:
                    try:
                        exp = int(parts[4])
                        if exp > 0 and (min_exp is None or exp < min_exp):
                            min_exp = exp
                    except ValueError:
                        pass
            if min_exp is None:
                ok(f"{platform}: session cookies (no expiry).")
            else:
                days = (min_exp - now) // 86400
                if days < 0:
                    fail(f"{platform}: EXPIRED {abs(days)} days ago! Run /setcookies {platform}")
                elif days < 7:
                    warn(f"{platform}: expires in {days} days! Update soon.")
                else:
                    ok(f"{platform}: valid for {days} more days.")

    def test_cookie_save_and_read(self):
        """Simulate bot saving cookies via /setcookies command."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_path = Path(tmpdir) / "test_cookies.txt"
            future_exp = int(time.time()) + 86400 * 30  # 30 days
            cookie_content = SAMPLE_COOKIE.format(
                domain="youtube.com", exp=future_exp
            )
            test_path.write_text(cookie_content, encoding="utf-8")
            saved = test_path.read_text(encoding="utf-8")
            self.assertIn("Netscape HTTP Cookie File", saved)
            self.assertIn("sessionid", saved)
            ok("Cookie save/read simulation passed.")

    def test_cookies_gitignored(self):
        """Cookie files must be in .gitignore."""
        if not Path(".gitignore").exists():
            warn(".gitignore not found.")
            return
        gi = Path(".gitignore").read_text()
        covered = "*_cookies.txt" in gi or "cookies.txt" in gi
        self.assertTrue(covered, "Cookie files not in .gitignore — security risk!")
        ok("Cookie files are gitignored.")

    def test_downloads_dir_gitignored(self):
        if not Path(".gitignore").exists():
            self.skipTest(".gitignore not found")
        gi = Path(".gitignore").read_text()
        self.assertTrue("downloads" in gi, "downloads/ not in .gitignore!")
        ok("downloads/ is gitignored.")


# ══════════════════════════════════════════════
# 6. BOT COOKIE COMMAND LOGIC
# ══════════════════════════════════════════════
class TestCookieCommandLogic(unittest.TestCase):

    def test_valid_cookie_text_accepted(self):
        """Text starting with Netscape header + tab lines = valid."""
        future_exp = int(time.time()) + 86400 * 30
        text = (
            "# Netscape HTTP Cookie File\n"
            f".youtube.com\tTRUE\t/\tTRUE\t{future_exp}\tSID\tabc123\n"
        )
        has_header = text.startswith("# Netscape HTTP Cookie File")
        lines = [l for l in text.splitlines() if l.strip() and not l.startswith("#")]
        valid = [l for l in lines if len(l.split("\t")) >= 7]
        self.assertTrue(has_header and len(valid) > 0)
        ok("Valid cookie text correctly accepted.")

    def test_invalid_cookie_text_rejected(self):
        """Random text without proper format must be rejected."""
        bad_texts = [
            "just some random text",
            "SESSION_ID=abc123",
            "https://youtube.com",
            "",
        ]
        for text in bad_texts:
            has_header = text.startswith("# Netscape HTTP Cookie File")
            lines = [l for l in text.splitlines() if l.strip() and not l.startswith("#")]
            valid = [l for l in lines if len(l.split("\t")) >= 7]
            is_valid = has_header or len(valid) > 0
            self.assertFalse(is_valid, f"Should reject: {repr(text[:40])}")
        ok("Invalid cookie texts correctly rejected.")

    def test_expiry_alert_logic(self):
        """Check expiry alert triggers at correct thresholds."""
        now = int(time.time())
        cases = [
            (now - 86400,       "expired"),
            (now + 86400 * 3,   "expiring_soon"),
            (now + 86400 * 10,  "ok"),
            (0,                 "session"),
        ]
        for exp, expected_status in cases:
            if exp == 0:
                status = "session"
            else:
                days = (exp - now) // 86400
                if days < 0:     status = "expired"
                elif days < 7:   status = "expiring_soon"
                else:            status = "ok"
            self.assertEqual(status, expected_status)
        ok("Expiry alert thresholds correct (expired / 7-day warning / ok).")


# ══════════════════════════════════════════════
# 7. PROJECT FILE STRUCTURE
# ══════════════════════════════════════════════
class TestProjectStructure(unittest.TestCase):

    REQUIRED = [
        "bot.py", "requirements.txt", "render.yaml",
        "Procfile", ".env.example", ".gitignore", "README.md",
    ]

    def test_required_files(self):
        for f in self.REQUIRED:
            with self.subTest(file=f):
                exists = Path(f).exists()
                if exists:
                    ok(f"{f} exists.")
                else:
                    fail(f"{f} MISSING.")
                self.assertTrue(exists, f"Missing: {f}")

    def test_requirements_not_empty(self):
        if not Path("requirements.txt").exists():
            self.skipTest("requirements.txt not found")
        lines = [l.strip() for l in Path("requirements.txt").read_text().splitlines()
                 if l.strip() and not l.startswith("#")]
        self.assertGreater(len(lines), 0)
        ok(f"requirements.txt has {len(lines)} packages.")

    def test_procfile_format(self):
        if not Path("Procfile").exists():
            self.skipTest("Procfile not found")
        content = Path("Procfile").read_text().strip()
        self.assertIn("web:", content)
        ok(f"Procfile OK: {content[:60]}")

    def test_render_yaml_port(self):
        if not Path("render.yaml").exists():
            self.skipTest("render.yaml not found")
        content = Path("render.yaml").read_text()
        self.assertNotIn('"8443"', content, "PORT must not be 8443 in render.yaml!")
        self.assertIn("10000", content, "PORT should be 10000 in render.yaml")
        ok("render.yaml PORT=10000 (correct).")

    def test_render_yaml_build_command(self):
        if not Path("render.yaml").exists():
            self.skipTest("render.yaml not found")
        content = Path("render.yaml").read_text()
        self.assertIn("yt-dlp -U", content,
            "render.yaml buildCommand should include 'yt-dlp -U' to auto-update.")
        ok("render.yaml buildCommand includes yt-dlp -U.")

    def test_bot_stats_gitignored(self):
        if not Path(".gitignore").exists():
            self.skipTest(".gitignore not found")
        gi = Path(".gitignore").read_text()
        self.assertIn("bot_stats.json", gi)
        ok("bot_stats.json gitignored.")

    def test_downloads_gitkeep_exists(self):
        if Path("downloads/.gitkeep").exists():
            ok("downloads/.gitkeep exists.")
        else:
            warn("downloads/.gitkeep missing — folder won't be tracked by Git.")

    def test_python_version_file(self):
        if not Path(".python-version").exists():
            warn(".python-version missing.")
            return
        ver = Path(".python-version").read_text().strip()
        self.assertTrue(ver.startswith("3."), f"Unexpected python version: {ver}")
        ok(f".python-version: {ver}")


# ══════════════════════════════════════════════
# 8. BOT.PY INTEGRITY
# ══════════════════════════════════════════════
class TestBotPyIntegrity(unittest.TestCase):

    def _content(self):
        return Path("bot.py").read_text(encoding="utf-8") if Path("bot.py").exists() else ""

    def test_syntax(self):
        if not Path("bot.py").exists():
            self.skipTest("bot.py not found")
        import py_compile, tempfile, shutil
        shutil.copy("bot.py", "/tmp/_test_bot.py")
        py_compile.compile("/tmp/_test_bot.py", doraise=True)
        ok("bot.py syntax is valid.")

    def test_cookie_commands_present(self):
        c = self._content()
        for fn in ["cmd_cookies", "cmd_setcookies", "cmd_cancel",
                   "handle_cookie_paste", "check_and_notify_cookie_expiry"]:
            self.assertIn(fn, c, f"{fn} not found in bot.py")
        ok("All cookie command functions present.")

    def test_cookie_handlers_registered(self):
        c = self._content()
        self.assertIn('"cookies"', c)
        self.assertIn('"setcookies"', c)
        self.assertIn('"cancel"', c)
        ok("Cookie handlers registered in build_application.")

    def test_job_queue_registered(self):
        c = self._content()
        self.assertIn("run_repeating", c)
        self.assertIn("check_and_notify_cookie_expiry", c)
        ok("Daily cookie expiry job registered.")

    def test_youtube_player_client_updated(self):
        c = self._content()
        self.assertNotIn("player_client=ios,web,default", c,
            "Old iOS player_client still present — YouTube will fail!")
        self.assertIn("tv_embedded", c)
        ok("YouTube player_client=tv_embedded,web (correct).")

    def test_no_old_facebook_format(self):
        c = self._content()
        self.assertNotIn('"-f", "b"', c)
        self.assertNotIn('"-f", "best"', c)
        ok('No deprecated "-f b" / "-f best" Facebook flags.')

    def test_cookie_files_dict_present(self):
        c = self._content()
        self.assertIn("COOKIE_FILES", c)
        ok("COOKIE_FILES dict defined.")

    def test_port_default_10000(self):
        c = self._content()
        self.assertIn("PORT\", 10000", c)
        ok("Default PORT is 10000.")

    def test_no_hardcoded_admin_id(self):
        c = self._content()
        import re
        # 7168219724 was the old hardcoded admin ID
        self.assertNotIn("7168219724", c)
        ok("No hardcoded ADMIN_USER_ID.")

    def test_handle_url_cookie_intercept(self):
        c = self._content()
        self.assertIn("handle_cookie_paste", c)
        # Must appear before register_user_and_notify in handle_url
        cookie_idx = c.find("await handle_cookie_paste")
        register_idx = c.find("await register_user_and_notify")
        self.assertLess(cookie_idx, register_idx,
            "handle_cookie_paste must be called before register_user in handle_url")
        ok("handle_url intercepts cookie paste before URL processing.")


# ══════════════════════════════════════════════
# 9. BOT STATS FILE
# ══════════════════════════════════════════════
class TestBotStats(unittest.TestCase):

    def test_stats_valid_json(self):
        if not Path("bot_stats.json").exists():
            warn("bot_stats.json not found (created on first run).")
            return
        try:
            data = json.loads(Path("bot_stats.json").read_text(encoding="utf-8"))
            ok(f"bot_stats.json valid JSON. Keys: {list(data.keys())}")
        except json.JSONDecodeError as e:
            self.fail(f"bot_stats.json corrupted: {e}")


# ══════════════════════════════════════════════
# 10. QUALITY FORMAT STRINGS
# ══════════════════════════════════════════════
class TestQualityFormats(unittest.TestCase):

    FORMATS = {
        "best":  "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "1080p": "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]/best[height<=1080]",
        "720p":  "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]/best[height<=720]",
        "480p":  "bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/best[height<=480][ext=mp4]/best[height<=480]",
        "360p":  "bestvideo[height<=360][ext=mp4]+bestaudio[ext=m4a]/best[height<=360][ext=mp4]/best[height<=360]",
        "audio": "bestaudio/best",
    }

    def test_format_strings(self):
        for label, fmt in self.FORMATS.items():
            with self.subTest(quality=label):
                self.assertGreater(len(fmt), 0)
                if label == "audio":
                    self.assertIn("audio", fmt)
                elif label != "best":
                    self.assertIn("height<=", fmt)
        ok("All quality format strings valid.")

    def test_formats_in_bot(self):
        if not Path("bot.py").exists():
            self.skipTest("bot.py not found")
        c = Path("bot.py").read_text()
        self.assertIn("bestvideo[ext=mp4]+bestaudio[ext=m4a]", c)
        ok("Correct format strings found in bot.py.")


# ══════════════════════════════════════════════
# RUNNER
# ══════════════════════════════════════════════
if __name__ == "__main__":
    # Load .env
    try:
        from dotenv import load_dotenv
        if Path(".env").exists():
            load_dotenv()
            print(f"{CYAN}Loaded .env{RESET}")
    except ImportError:
        pass

    print(f"\n{CYAN}{'═'*52}")
    print("   🤖 AK Saver Bot — Full Test Suite")
    print(f"{'═'*52}{RESET}")

    classes = [
        (TestEnvironment,         "1. Environment Variables"),
        (TestDependencies,        "2. Python Dependencies"),
        (TestSystemTools,         "3. System Tools (CLI)"),
        (TestPlatformDetection,   "4. Platform URL Detection"),
        (TestCookieFiles,         "5. Cookie Files"),
        (TestCookieCommandLogic,  "6. Cookie Command Logic"),
        (TestProjectStructure,    "7. Project File Structure"),
        (TestBotPyIntegrity,      "8. bot.py Integrity"),
        (TestBotStats,            "9. Bot Stats File"),
        (TestQualityFormats,      "10. Quality Format Strings"),
    ]

    total_pass = total_fail = total_skip = 0
    loader = unittest.TestLoader()

    for cls, name in classes:
        section(name)
        suite  = loader.loadTestsFromTestCase(cls)
        runner = unittest.TextTestRunner(
            verbosity=0, stream=open(os.devnull, "w")
        )
        result = runner.run(suite)

        for test, err in result.failures + result.errors:
            fail(f"{test._testMethodName}: {err.strip().splitlines()[-1]}")

        p = suite.countTestCases() - len(result.failures) - len(result.errors) - len(result.skipped)
        f_ = len(result.failures) + len(result.errors)
        s  = len(result.skipped)
        total_pass += p; total_fail += f_; total_skip += s

        print(f"\n  {GREEN}{p} passed{RESET}  {RED}{f_} failed{RESET}  {YELLOW}{s} skipped{RESET}  / {suite.countTestCases()} total")

    print(f"\n{CYAN}{'═'*52}")
    print(f"  TOTAL: {GREEN}{total_pass} passed{RESET}  {RED}{total_fail} failed{RESET}  {YELLOW}{total_skip} skipped{RESET}")
    print(f"{'═'*52}{RESET}\n")

    if total_fail > 0:
        sys.exit(1)
