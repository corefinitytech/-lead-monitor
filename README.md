# Lead monitor: runs 24/7 for free

It checks Reddit and Hacker News every 15 minutes for people asking for a developer, scores each post, and sends the good ones to your phone on Telegram. It runs on GitHub's servers, so your laptop can be off.

## Setup (about 15 minutes)

### 1. Telegram bot (2 min)
1. In Telegram, search for **@BotFather**, send `/newbot`, pick a name and a username.
2. BotFather gives you a token like `123456:ABC...`. Copy it. This is `TELEGRAM_BOT_TOKEN`.
3. Open your new bot and press **Start**, then send it any message ("hi").
4. In your browser open `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`. Find `"chat":{"id": 123456789` and copy that number. This is `TELEGRAM_CHAT_ID`.

### 2. GitHub repo (5 min)
1. Create a free GitHub account if you don't have one, then **New repository**. Name it `lead-monitor`.
   - **Public** repo: unlimited free runs, so you can poll every 15 minutes. The code and keywords are visible, but your tokens stay secret.
   - **Private** repo: you get 2,000 free minutes a month. Change the cron in `.github/workflows/monitor.yml` to `*/30 * * * *`.
2. Upload every file in this folder (keep the `.github/workflows/monitor.yml` path). On github.com: **Add file > Upload files**, drag the folder contents in.
3. Go to **Settings > Secrets and variables > Actions > New repository secret** and add:
   - `TELEGRAM_BOT_TOKEN` = the token
   - `TELEGRAM_CHAT_ID` = the number
4. Go to **Settings > Actions > General > Workflow permissions** and choose **Read and write permissions**. Save.

### 3. Start it
1. Open the **Actions** tab, choose **lead-monitor**, click **Run workflow**.
2. Open the run and read the log. You'll see one line per source:
   - `OK  r/startups: 25` means it works.
   - `BLOCKED r/startups: HTTP 403` means Reddit is blocking GitHub's servers (see below).
3. The first run saves all current posts as "already seen" and sends nothing. From the second run on, only new posts alert you.

## If Reddit shows BLOCKED (403)
Reddit blocks some cloud servers. If that happens:
- Hacker News and any extra RSS feeds still work from GitHub.
- For Reddit, run `python monitor.py` on your own computer or phone (Termux) while you work. Your home internet isn't blocked, and you can use Task Scheduler or cron to run it every 15 minutes.
- Or use a free Oracle Cloud "Always Free" VM, but its IP may be blocked too. Test it first.

## Tune it
Everything is in `config.json`:
- `subreddits`: add or remove communities.
- `positive_patterns`: phrases that mean "someone wants a developer".
- `hardware_patterns`: extra score for IoT, BLE, wearable, firmware, etc.
- `negative_patterns`: skip people offering their services, unpaid work, and "equity only".
- `min_score`: 3 is a catch-everything level. Raise it to 5 if there's too much noise.
- `extra_rss`: add any RSS feed, for example `{"weworkremotely": "https://weworkremotely.com/categories/remote-programming-jobs.rss"}`.

## Test without sending anything
- `python monitor.py --fixture` scores sample posts, no internet.
- `python monitor.py --dry-run` fetches real posts and prints leads without sending or saving.

## Rules to follow
- Reply by hand. Automated replies or DMs break Reddit's and Hacker News's rules and get you banned.
- Reply in the thread with something specific to their post, not a pitch.
