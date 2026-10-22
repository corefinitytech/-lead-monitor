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
3. The first run sends one "monitor is live" Telegram message with the number of indexed posts, saves those posts as "already seen", and sends no old lead alerts. From the second run on, only new posts alert you. A failed activation message leaves initialization pending for the next run.

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
- `python -m unittest -v` runs the offline regression checks (standard library only).

## Reliability and troubleshooting
- Each source logs its HTTP status on success or HTTP failure. Network and parsing failures are logged separately. Partial source failures allow the other sources to continue; total failure fails the run without advancing state.
- HN searches the title of the latest indexed freelancer thread directly, then reads every page of comments. The thread title/date is logged: the latest thread may be older than the current month.
- Reddit requests remain seven seconds apart. HTTP 429 is retried up to three attempts, respecting numeric or HTTP-date `Retry-After`. If the requested wait exceeds 45 seconds, that source fails for this run rather than retrying too early.
- Telegram credentials come only from `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`. Missing credentials or failed delivery fail the run; successful deliveries are still saved. Leads above the per-run limit remain eligible while they are still present in the source feeds.
- `seen.json` is runtime state committed by Actions. Do not delete it unless you want to initialize again. Invalid state fails visibly; IDs are retained to avoid alerting twice on older posts. Avoid running a second local writer alongside Actions.
- Actions serializes runs, rebases before each push, retries three times, and fails visibly if state cannot be saved. A conflicting manual edit of `seen.json` may still require manual resolution; edits to other files are preserved by the rebase.

## Rules to follow
- Reply by hand. Automated replies or DMs break Reddit's and Hacker News's rules and get you banned.
- Reply in the thread with something specific to their post, not a pitch.
