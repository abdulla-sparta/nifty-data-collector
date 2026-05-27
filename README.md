# NiftyCollector — Railway Deployment

Fully automated data collector. Runs 24/7 on Railway.
You only interact via Telegram — one tap per morning.

---

## Cost
**₹42/day ($0.50/day) | ₹1,260/month ($15/month)**

---

## One-time Setup (30 minutes)

### Step 1 — Create Telegram Bot
1. Message [@BotFather](https://t.me/BotFather) on Telegram
2. Send `/newbot` → follow prompts → get `BOT_TOKEN`
3. Message your new bot → then visit:
   `https://api.telegram.org/bot<BOT_TOKEN>/getUpdates`
4. Get your `chat_id` from the response

### Step 2 — Railway Project
1. Go to [railway.app](https://railway.app) → New Project
2. Add **PostgreSQL** service
3. Add **New Service** → Deploy from GitHub (push this code first)
4. Add a **Volume** → mount at `/data`

### Step 3 — Set Environment Variables
In Railway → Your Service → Variables, add:
```
UPSTOX_API_KEY       = your_key
UPSTOX_API_SECRET    = your_secret
UPSTOX_REDIRECT_URI  = https://YOUR-APP.up.railway.app/callback
TELEGRAM_BOT_TOKEN   = your_bot_token
TELEGRAM_CHAT_ID     = your_chat_id
BACKUP_PATH          = /data/csv
```
Railway auto-injects `DATABASE_URL` — don't set it manually.

### Step 4 — Upstox App Settings
In your Upstox developer app, set redirect URI to:
```
https://YOUR-APP.up.railway.app/callback
```

### Step 5 — Deploy
```bash
git init
git add .
git commit -m "initial"
git remote add origin https://github.com/YOUR/repo
git push origin main
```
Railway auto-deploys on push.

---

## Daily Routine (30 seconds)

```
08:45 IST — Telegram sends you a login link automatically
            Tap the link on your phone
            Log in to Upstox (takes 20 sec)
            Done — collector starts at 09:15 automatically

15:45 IST — Collector stops, CSV backup runs
            Telegram sends you a summary message

You don't need to do anything else.
```

---

## What Telegram sends you

| Time | Message |
|------|---------|
| 08:45 | Login link for today |
| 09:15 | "NiftyCollector Started" |
| 15:45 | EOD summary (candles, OC snaps) |
| 15:45 | "Backup complete" with row count |
| Any time | Error alerts if something fails |
| On restart | Status message with token state |

---

## URLs

| Endpoint | Purpose |
|----------|---------|
| `/health` | Railway healthcheck |
| `/status` | Current state (JSON) |
| `/callback` | Upstox OAuth redirect (don't open manually) |

---

## If you miss the 08:45 login

Telegram sends the link once at 08:45.
If you miss it, the login URL is always:
```
https://api.upstox.com/v2/login/authorization/dialog
  ?response_type=code
  &client_id=YOUR_API_KEY
  &redirect_uri=https://YOUR-APP.up.railway.app/callback
```
Paste this in your browser, log in, and the token is saved.
Collector will start within 60 seconds.

---

## If Railway restarts mid-day

The collector auto-resumes if:
- Token for today exists in DB ✅
- Market is still open ✅

No action needed from you.

---

## Backup Structure (Railway Volume /data)

```
/data/csv/
  2026-05-27/
    candles_60s.csv
    option_chain_agg.csv
    vix_60s.csv
    feature_snapshot.csv
    scenario_hits.csv
  2026-05-28/
    ...
```

Download periodically via Railway CLI:
```bash
railway run -- ls /data/csv
```

---

## Project Structure

```
nifty_railway/
  main.py                  ← Flask app + orchestrator + scheduler
  config.py                ← Env var config
  Procfile                 ← Railway start command
  requirements.txt
  .env.example             ← Variable reference
  collector/
    tick_buffer.py         ← In-memory tick accumulator
    tick_collector.py      ← Upstox V3 WebSocket
    oc_poller.py           ← Option chain 30s aggregated
    candle_writer.py       ← 60s scheduler + features
  db/
    schema.py              ← Tables (auto-runs on boot)
    connection.py          ← PG pool via DATABASE_URL
  utils/
    market.py              ← Hours, expiry, holidays
    token_manager.py       ← Token in PG token_store table
    telegram.py            ← All Telegram interactions
  backup/
    daily_backup.py        ← EOD CSV dump to /data
```
