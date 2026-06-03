# 🤖 AK Saver Bot

A Telegram bot to download videos, images, and music from Instagram, TikTok, YouTube, Pinterest, Snapchat, Likee, VK, Facebook, Threads, and music platforms.

Developer: [@anujedits76](https://t.me/anujedits76)

---

## 📁 Repo Structure

```
Downloader-Bot/
├── bot.py                  ← Main bot (Flask + PTB webhook)
├── requirements.txt        ← Python dependencies
├── render.yaml             ← Render deployment config
├── Procfile                ← Process definition
├── .env.example            ← Environment variable template  (copy to .env)
├── .gitignore              ← Files excluded from Git
├── .python-version         ← Python 3.11.9
├── README.md               ← This file
└── downloads/              ← Temp folder (auto-created, gitignored)
    ├── youtube_cookies.txt    ← Optional: YouTube login cookies
    ├── instagram_cookies.txt  ← Optional: Instagram login cookies
    ├── facebook_cookies.txt   ← Optional: Facebook login cookies
    └── tiktok_cookies.txt     ← Optional: TikTok login cookies
```

---

## 🚀 Deploy on Render (Free)

### Step 1 — Push to GitHub

```bash
git init
git add .
git commit -m "Initial commit"
git remote add origin https://github.com/YOUR_USERNAME/Downloader-Bot.git
git push -u origin main
```

### Step 2 — Create Render Web Service

1. Go to [render.com](https://render.com) → **New** → **Web Service**
2. Connect your GitHub repo
3. Render will auto-detect `render.yaml`

### Step 3 — Set Environment Variables in Render Dashboard

Go to your service → **Environment** tab and add:

| Variable | Value |
|---|---|
| `BOT_TOKEN` | Your token from @BotFather |
| `BOT_USERNAME` | Your bot username (without @) |
| `WEBHOOK_URL` | `https://your-app-name.onrender.com` |
| `ADMIN_USER_ID` | Your Telegram numeric user ID |
| `REQUIRED_CHANNEL_USERNAME` | `@YourChannel` |
| `REQUIRED_CHANNEL_URL` | `https://t.me/YourChannel` |
| `PORT` | `10000` ← use 10000 for Render free tier |

> ⚠️ Never set PORT to 8443 on Render free tier — only 10000 works.

### Step 4 — Deploy

Click **Deploy**. After deployment the webhook URL is set automatically.

---

## 🖥 Local Development

```bash
# 1. Clone repo
git clone https://github.com/YOUR_USERNAME/Downloader-Bot.git
cd Downloader-Bot

# 2. Create virtual environment
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Copy and fill .env
cp .env.example .env
# Edit .env with your values

# 5. Run (polling mode when no WEBHOOK_URL set)
python bot.py
```

---

## 🍪 Cookie Management (Bot Commands)

Cookies are managed **directly from Telegram** — no server access needed.

### Admin Commands

| Command | Description |
|---|---|
| `/cookies` | Show expiry status of all platform cookies |
| `/setcookies youtube` | Update YouTube cookies |
| `/setcookies instagram` | Update Instagram cookies |
| `/setcookies facebook` | Update Facebook cookies |
| `/setcookies tiktok` | Update TikTok cookies |
| `/cancel` | Cancel pending cookie update |

### How to Update Cookies

1. Install **[Get cookies.txt LOCALLY](https://chrome.google.com/webstore/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc)** Chrome extension
2. Log in to YouTube / Instagram / Facebook / TikTok in your browser
3. Click the extension → Export cookies for that site
4. Send `/setcookies youtube` (or whichever platform) to your bot
5. Paste the full exported cookie text
6. Bot saves it automatically ✅

### Auto Expiry Alerts

The bot checks cookies **daily** and sends you a Telegram alert if any cookie is:
- Expiring within 7 days ⚠️
- Already expired ❌
- Missing 🚫

> ⚠️ Never commit cookie files to Git — they are gitignored.

---

## 🌐 Supported Platforms

| Platform | Tool Used |
|---|---|
| Instagram | gallery-dl (yt-dlp fallback) |
| TikTok | gallery-dl (yt-dlp fallback) |
| YouTube | yt-dlp |
| Pinterest | gallery-dl |
| Snapchat | yt-dlp |
| Likee | yt-dlp |
| VK | yt-dlp |
| Facebook | yt-dlp |
| Threads | gallery-dl (yt-dlp fallback) |
| SoundCloud | yt-dlp |
| Spotify | yt-dlp |
| Deezer | yt-dlp |

---

## ⚙️ Quality Options

For video platforms a quality menu appears:

- 🔥 Best Quality
- 🖥 1080p (FHD)
- 📺 720p (HD)
- 📱 480p (SD)
- 📉 360p (Low)
- 🎵 Audio Only (MP3)

---

## 👤 Admin Commands

| Command | Description |
|---|---|
| `/stats` | Show total users and downloads |
| `/cookies` | Show cookie expiry status |
| `/setcookies <platform>` | Update cookies for a platform |
| `/cancel` | Cancel pending operation |

---

## 🔒 Security Notes

- `bot_stats.json` is gitignored — stores user data locally
- Cookie files are gitignored — never commit them
- `downloads/` folder is auto-created and cleaned after each download
- All credentials loaded from environment variables — no hardcoded secrets
- Bot works in private chats and groups/supergroups
- Enable group support in BotFather: `/setjoingroups → Enable`
