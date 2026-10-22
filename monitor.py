#!/usr/bin/env python3
"""Lead monitor: watches Reddit and Hacker News for people asking for a developer,
scores each post, and sends the good ones to Telegram. Standard library only.

Usage:
  python monitor.py              # normal run (needs TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
  python monitor.py --dry-run    # fetch + score, print matches, send nothing, save nothing
  python monitor.py --fixture    # score the built-in sample posts (no network)
"""
import json, os, re, sys, time, html, urllib.request, urllib.parse, urllib.error
import xml.etree.ElementTree as ET
from pathlib import Path
from email.utils import parsedate_to_datetime
from decimal import Decimal

HERE = Path(__file__).parent
CONFIG = json.loads((HERE / "config.json").read_text(encoding="utf-8"))
SEEN_FILE = HERE / "seen.json"
UA = "Mozilla/5.0 (compatible; lead-monitor/1.0)"
DRY = "--dry-run" in sys.argv
FIXTURE = "--fixture" in sys.argv


# ---------- fetching ----------
def get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 2:
                retry = e.headers.get("Retry-After", "")
                try:
                    wait = float(retry)
                except ValueError:
                    try:
                        wait = parsedate_to_datetime(retry).timestamp() - time.time()
                    except (ValueError, TypeError, OverflowError):
                        wait = 20 * (attempt + 1)
                # Do not retry before the server permits, or exhaust the job timeout.
                if wait > 45:
                    raise
                wait = max(7, wait)
                print(f"RETRY   HTTP 429: waiting {wait:.0f}s (attempt {attempt + 2}/3)", flush=True)
                time.sleep(wait)
                continue
            raise


def strip_html(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def fetch_reddit(sub):
    """Reddit RSS (Atom). Returns list of posts."""
    txt = get(f"https://www.reddit.com/r/{sub}/new/.rss?limit=50")
    ns = {"a": "http://www.w3.org/2005/Atom"}
    root = ET.fromstring(txt)
    out = []
    for e in root.findall("a:entry", ns):
        link = next((link for link in e.findall("a:link", ns)
                     if link.get("rel", "alternate") == "alternate"), None)
        href = link.get("href", "") if link is not None else ""
        identity = e.findtext("a:id", "", ns) or href
        if not identity:
            raise ValueError("Atom entry has no ID or alternate link")
        out.append({
            "id": "reddit:" + identity,
            "source": f"r/{sub}",
            "title": e.findtext("a:title", "", ns),
            "text": strip_html(e.findtext("a:content", "", ns)),
            "url": href,
            "author": (e.find("a:author/a:name", ns).text if e.find("a:author/a:name", ns) is not None else ""),
        })
    return out


def fetch_hn_freelancer_thread():
    """Latest monthly 'Freelancer? Seeking freelancer?' thread: comments starting SEEKING FREELANCER."""
    q = urllib.parse.urlencode({"query": "Freelancer", "restrictSearchableAttributes": "title",
                               "tags": "story,author_whoishiring", "hitsPerPage": 10})
    stories = json.loads(get(f"https://hn.algolia.com/api/v1/search_by_date?{q}"))["hits"]
    thread = next((s for s in stories if "freelancer" in (s.get("title") or "").lower()), None)
    if not thread:
        raise ValueError("No HN freelancer thread found")
    sid = thread["objectID"]
    print(f"INFO    HN freelancer thread: {thread.get('title')} (id {sid})")
    hits, page = [], 0
    while True:
        q = urllib.parse.urlencode({"tags": f"comment,story_{sid}", "hitsPerPage": 200, "page": page})
        result = json.loads(get(f"https://hn.algolia.com/api/v1/search_by_date?{q}"))
        hits.extend(result["hits"])
        page += 1
        if page >= result.get("nbPages", 1):
            break
    out = []
    for h in hits:
        text = strip_html(h.get("comment_text"))
        if text.upper().startswith("SEEKING FREELANCER"):
            out.append({
                "id": f"hn:{h['objectID']}",
                "source": "HN freelancer thread",
                "title": text[:90],
                "text": text,
                "url": f"https://news.ycombinator.com/item?id={h['objectID']}",
                "author": h.get("author", ""),
            })
    print(f"INFO    HN freelancer thread: inspected {len(hits)} comments; "
          f"{len(out)} SEEKING FREELANCER posts", flush=True)
    return out


def fetch_hn_search(query):
    """HN stories/comments matching a phrase, newest first."""
    q = urllib.parse.urlencode({"query": query, "tags": "(story,comment)", "hitsPerPage": 30})
    hits = json.loads(get(f"https://hn.algolia.com/api/v1/search_by_date?{q}"))["hits"]
    out = []
    for h in hits:
        text = strip_html(h.get("comment_text") or h.get("story_text") or "")
        title = h.get("title") or text[:90]
        out.append({
            "id": f"hn:{h['objectID']}",
            "source": "HN search",
            "title": title,
            "text": text,
            "url": f"https://news.ycombinator.com/item?id={h['objectID']}",
            "author": h.get("author", ""),
        })
    return out


def fetch_rss(name, url):
    """Any RSS/Atom feed (e.g. extra feeds you add in config.json)."""
    root = ET.fromstring(get(url))
    out = []
    for it in root.iter():
        tag = it.tag.split("}")[-1]
        if tag not in ("item", "entry"):
            continue
        def f(n):
            for c in it:
                if c.tag.split("}")[-1] == n:
                    return c
        link = next((c for c in it if c.tag.split("}")[-1] == "link"
                     and c.get("rel", "alternate") == "alternate"), None)
        href = (link.get("href") or link.text) if link is not None else ""
        t = f("title")
        d = next((el for n in ("description", "summary", "content")
                  if (el := f(n)) is not None), None)
        identity = next((el.text for n in ("guid", "id")
                         if (el := f(n)) is not None and el.text), None) or href
        if not identity:
            raise ValueError("RSS entry has no ID or link")
        out.append({
            "id": f"rss:{name}:{identity}",
            "source": name,
            "title": t.text if t is not None else "",
            "text": strip_html(" ".join(d.itertext()) if d is not None else ""),
            "url": href or "",
            "author": "",
        })
    return out


# ---------- scoring ----------
def compile_all(lst):
    return [re.compile(p, re.I) for p in lst]

POS = compile_all(CONFIG["positive_patterns"])
HW = compile_all(CONFIG["hardware_patterns"])
NEG = compile_all(CONFIG["negative_patterns"])
URG = compile_all(CONFIG.get("urgency_patterns", []))
MONEY = re.compile(r"\$\s?(\d[\d,]*(?:\.\d+)?)\s*(k)?\b", re.I)


def score(post):
    blob = f"{post['title']} \n {post['text']}"
    title = post["title"]
    if any(p.search(title) for p in NEG) or any(p.search(blob[:300]) for p in NEG):
        return 0, ["negative"]
    s, why = 0, []
    pos = [p for p in POS if p.search(blob)]
    if pos:
        s += 3 + min(len(pos) - 1, 3)
        why.append(f"ask x{len(pos)}")
    if re.match(r"\s*\[?\s*hiring\b", title, re.I):
        s += 3; why.append("[hiring]")
    hw = [p for p in HW if p.search(blob)]
    if hw:
        s += 3; why.append("hardware/IoT")
    if any(p.search(blob) for p in URG):
        s += 2; why.append("urgent/funded")
    for m in MONEY.finditer(blob):
        n = int(Decimal(m.group(1).replace(",", "")) * (1000 if m.group(2) else 1))
        if n >= 500:
            s += 2; why.append(f"budget ${n:,}"); break
    if not pos and not re.match(r"\s*\[?\s*hiring\b", title, re.I):
        s = 0   # must contain an actual ask
    return s, why


# ---------- state + notify ----------
def load_seen():
    try:
        data = json.loads(SEEN_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return set()
    if not isinstance(data, list) or not all(isinstance(x, str) and x for x in data):
        raise ValueError("seen.json must be an array of nonempty post IDs")
    return set(data)


def save_seen(seen):
    temporary = SEEN_FILE.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(sorted(seen)) + "\n", encoding="utf-8")
    temporary.replace(SEEN_FILE)


def telegram(text):
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        raise RuntimeError("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set")
    data = urllib.parse.urlencode({"chat_id": chat, "text": text, "disable_web_page_preview": "true"}).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data), timeout=20) as response:
            if not json.load(response).get("ok"):
                raise RuntimeError("Telegram rejected sendMessage")
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"Telegram sendMessage failed: HTTP {error.code}") from None
    except Exception:
        # Exception strings and tracebacks can contain the token-bearing request URL.
        raise RuntimeError("Telegram sendMessage failed; check credentials and connectivity") from None


def fmt(post, s, why):
    snippet = post["text"][:380] + ("…" if len(post["text"]) > 380 else "")
    return (f"LEAD {s} | {post['source']} | {', '.join(why)}\n\n{post['title'][:140]}\n\n{snippet}\n\n{post['url']}")


FIXTURE_POSTS = [
    {"id": "t6", "source": "r/startups", "title": "Our developer ghosted us, need someone to finish our app ASAP", "text": "We raised a seed round and launch next month. Looking for a developer who can take over the mobile app.", "url": "https://example.com/6", "author": "f"},
    {"id": "t1", "source": "r/startups", "title": "Looking for a mobile app developer to build our BLE companion app", "text": "We have a wearable prototype and need a developer for the iOS/Android app and an AWS backend. Budget around $8k.", "url": "https://example.com/1", "author": "a"},
    {"id": "t2", "source": "r/forhire", "title": "[For Hire] Full-stack developer available, portfolio inside", "text": "I am a developer available for work.", "url": "https://example.com/2", "author": "b"},
    {"id": "t3", "source": "r/SideProject", "title": "Need a developer for my MVP, SaaS dashboard", "text": "Non-technical founder, have a Figma design. Looking to hire someone to build it.", "url": "https://example.com/3", "author": "c"},
    {"id": "t4", "source": "r/startups", "title": "How do you validate a startup idea?", "text": "Just curious about methods.", "url": "https://example.com/4", "author": "d"},
    {"id": "t5", "source": "r/forhire", "title": "[Hiring] Need IoT engineer for MQTT dashboard", "text": "Paid contract, remote. $3,000 budget.", "url": "https://example.com/5", "author": "e"},
]


def main():
    seen = load_seen()
    posts, status = [], []
    if FIXTURE:
        posts = FIXTURE_POSTS
    else:
        jobs = [(f"r/{s}", lambda s=s: fetch_reddit(s)) for s in CONFIG["subreddits"]]
        if CONFIG.get("hn_freelancer_thread", True):
            jobs.append(("HN freelancer thread", fetch_hn_freelancer_thread))
        jobs += [(f"HN search '{q}'", lambda q=q: fetch_hn_search(q)) for q in CONFIG.get("hn_queries", [])]
        jobs += [(f"rss {n}", lambda n=n, u=u: fetch_rss(n, u)) for n, u in CONFIG.get("extra_rss", {}).items()]
        for name, fn in jobs:
            try:
                got = fn(); posts += got; status.append(f"OK      {name}: {len(got)} (HTTP 200)")
            except urllib.error.HTTPError as e:
                status.append(f"BLOCKED {name}: HTTP {e.code}")
            except Exception as e:
                status.append(f"ERROR   {name}: {type(e).__name__} (no successful HTTP response/parse)")
            print(status[-1], flush=True)
            time.sleep(7 if name.startswith("r/") else 1.5)

    min_score = CONFIG.get("min_score", 5)
    first_run = not SEEN_FILE.exists() and not DRY and not FIXTURE
    # The same HN item may occur in several searches and in the monthly thread.
    posts = list({p["id"]: p for p in posts}.values())
    matches = []
    for p in posts:
        if p["id"] in seen and not FIXTURE:
            continue
        s, why = score(p)
        if s >= min_score:
            matches.append((s, p, why))
        else:
            seen.add(p["id"])
    matches.sort(key=lambda x: -x[0])

    print(f"{len(posts)} unique posts fetched, {len(matches)} new leads")
    blocked = [x for x in status if x.startswith(("BLOCKED", "ERROR"))]
    if status and len(blocked) == len(status):
        if not DRY and not FIXTURE and not os.environ.get("QUIET"):
            telegram("Lead monitor: every source failed this run.\n" + "\n".join(blocked))
        raise RuntimeError("Every source failed; state was not advanced")
    if first_run:
        # Send before saving: failed delivery must not silently consume activation.
        telegram(f"Lead monitor is live. Indexed {len(posts)} current posts; old leads will not alert."
                 + (f" {len(blocked)} sources failed; see Actions logs." if blocked else ""))
        save_seen(seen | {p["id"] for p in posts})
        print("First run: live confirmation sent; current posts saved as seen.")
        return
    try:
        for s, p, why in matches[: CONFIG.get("max_alerts_per_run", 10)]:
            if DRY or FIXTURE:
                print("-" * 60); print(fmt(p, s, why))
            else:
                telegram(fmt(p, s, why))
                seen.add(p["id"])
    finally:
        # Keep successful deliveries even if a subsequent send fails. Unsent
        # leads stay eligible on the next run while present in a source feed.
        if not DRY and not FIXTURE:
            save_seen(seen)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Never print arbitrary exception strings: URLs can contain credentials.
        detail = str(error) if isinstance(error, RuntimeError) else type(error).__name__
        for variable in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
            secret = os.environ.get(variable)
            if secret:
                detail = detail.replace(secret, "[REDACTED]")
        print(f"ERROR   {detail}", file=sys.stderr)
        sys.exit(1)
