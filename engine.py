"""Daily Paper engine: find feeds, gather + rank stories, render the PDF, send the email."""
import base64, calendar, io, json, os, re, smtplib, time
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import urljoin, urlparse

import feedparser, requests, trafilatura
from bs4 import BeautifulSoup
from jinja2 import Environment, FileSystemLoader, select_autoescape
from PIL import Image, ImageOps

ROOT = Path(__file__).parent
UA = {"User-Agent": "Mozilla/5.0 (compatible; DailyPaper/2.0)"}
SETTINGS = ROOT / "settings.json"
DEFAULTS = {
    "title": "The Daily Paper", "tagline": "All the News That's Fit to Print",
    "edition_start": datetime.now().strftime("%Y-%m-%d"),
    "sources": [], "sections": ["General"], "keywords": [], "boost": "subtle",
    "email": {"to": "", "host": "smtp.gmail.com", "port": 465},
    "schedule": {"enabled": False, "time": "07:00"}, "timezone": "America/New_York",
    "max_total": 14, "max_per_source": 4, "lookback_hours": 36, "max_words": 350,
}
PRIORITY = {"lead": 0, "high": 30, "normal": 0, "low": -15}


# ---------- settings ----------
def load():
    cfg = json.loads(json.dumps(DEFAULTS))
    if SETTINGS.exists():
        for k, v in json.loads(SETTINGS.read_text()).items():
            cfg[k] = {**cfg[k], **v} if isinstance(v, dict) and isinstance(cfg.get(k), dict) else v
    return cfg


def save(cfg):
    tmp = SETTINGS.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2))
    tmp.replace(SETTINGS)


# ---------- finding a feed from just a URL ----------
def page_links(url):
    """Fallback for sites with no feed: article-looking links on a listing page, in page order."""
    r = requests.get(url, headers=UA, timeout=15)
    r.raise_for_status()
    soup, base, out, seen = BeautifulSoup(r.text, "lxml"), urlparse(r.url), [], set()
    depth = len([p for p in base.path.split("/") if p])
    for a in soup.find_all("a", href=True):
        href = urljoin(r.url, a["href"]).split("#")[0]
        u, text = urlparse(href), " ".join(a.get_text(" ", strip=True).split())
        if (u.netloc != base.netloc or href in seen or href.rstrip("/") == r.url.rstrip("/")
                or len(text) < 25 or re.search(r"\.(jpe?g|png|gif|pdf|zip|mp\d)$", u.path, re.I)
                or len([p for p in u.path.split("/") if p]) <= depth):
            continue
        seen.add(href)
        out.append({"title": text, "link": href, "summary": "", "ts": None, "img": None})
    return out


def discover(url):
    """Turn any site/feed URL into a source: {name, url, kind: rss|page}."""
    url = url.strip()
    if "://" not in url:
        url = "https://" + url
    r = requests.get(url, headers=UA, timeout=15)
    r.raise_for_status()
    host = urlparse(r.url).netloc.replace("www.", "")
    d = feedparser.parse(r.content)
    if d.entries:
        return {"name": (d.feed.get("title") or host)[:40], "url": r.url, "kind": "rss"}
    soup, base = BeautifulSoup(r.text, "lxml"), urlparse(r.url)
    origin = f"{base.scheme}://{base.netloc}"
    cands = [urljoin(r.url, l["href"]) for l in soup.find_all("link", href=True)
             if "alternate" in (l.get("rel") or []) and re.search(r"rss|atom", l.get("type", ""))]
    cands += [r.url.rstrip("/") + s for s in ("/feed", "/rss", "/feed.xml", "/rss.xml")]
    if base.path in ("", "/"):       # site-wide feeds only make sense for a homepage, not a section URL
        cands += [origin + s for s in ("/feed", "/rss", "/rss.xml", "/feed.xml", "/atom.xml")]
    title = (soup.title.get_text(strip=True) if soup.title else host).split(" | ")[0].split(" - ")[0][:40]
    for c in dict.fromkeys(cands):
        try:
            d = feedparser.parse(requests.get(c, headers=UA, timeout=10).content)
        except Exception:
            continue
        if d.entries:
            return {"name": (d.feed.get("title") or title)[:40], "url": c, "kind": "rss"}
    if page_links(r.url):
        return {"name": title or host, "url": r.url, "kind": "page"}
    raise ValueError("Couldn't find a feed or article links at that address.")


# ---------- gathering ----------
def fetch_items(src):
    if src["kind"] == "page":
        return page_links(src["url"])
    d = feedparser.parse(src["url"], request_headers=UA)
    out = []
    for e in d.entries:
        t = e.get("published_parsed") or e.get("updated_parsed")
        img = next((m["url"] for k in ("media_content", "media_thumbnail") for m in (e.get(k) or []) if m.get("url")), None)
        out.append({"title": re.sub(r"\s+", " ", e.get("title", "")).strip(), "link": e.get("link"),
                    "summary": e.get("summary", ""), "ts": calendar.timegm(t) if t else None,
                    "img": img, "author": e.get("author")})
    return out


def fetch_article(url):
    out = {"text": "", "img": None, "author": None, "ts": None}
    try:
        r = requests.get(url, headers=UA, timeout=15)
        r.raise_for_status()
        out["text"] = trafilatura.extract(r.text, include_comments=False, include_tables=False) or ""
        m = trafilatura.extract_metadata(r.text)
        if m:
            out["img"], out["author"] = m.image, m.author
            if m.date:
                out["ts"] = calendar.timegm(datetime.fromisoformat(m.date[:10]).timetuple())
    except Exception:
        pass
    return out


def to_data_uri(url, max_w=900):
    try:
        r = requests.get(url, headers=UA, timeout=15)
        img = Image.open(io.BytesIO(r.content))
        if img.width < 300:
            return None
        img = ImageOps.autocontrast(ImageOps.grayscale(img), cutoff=1).convert("RGB")   # vintage newsprint look
        if img.width > max_w:
            img = img.resize((max_w, int(img.height * max_w / img.width)))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=82)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return None


def trim_words(text, limit):
    w = text.split()
    if len(w) <= limit:
        return text
    cut = " ".join(w[:limit])
    m = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return cut[: m + 1] if m > len(cut) * 0.5 else cut.rstrip(",;: ") + "…"


def make_deck(summary, body, limit=170):
    d = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", summary or "")).strip()
    if len(d) < 30 or d[:40].lower() in body[:300].lower():
        return None
    if len(d) > limit:
        cut = d[:limit]
        m = max(cut.rfind(". "), cut.rfind("; "))
        d = cut[: m + 1] if m > 60 else cut.rsplit(" ", 1)[0].rstrip(",;: ") + "…"
    return d


def score(title_text, body, ts, src, cfg):
    """Priority + keyword boost + freshness. Keyword hits in the headline count double."""
    s, w = PRIORITY[src.get("priority", "normal")], (20 if cfg["boost"] == "strong" else 10)
    for k in (k.lower() for k in cfg["keywords"] if k.strip()):
        s += 2 * w * (k in title_text.lower()) + w * (k in body.lower())
    if ts:
        s += max(0, 10 * (1 - (time.time() - ts) / 3600 / cfg["lookback_hours"]))
    return s


def collect(cfg, log=print):
    cutoff, seen, cands = time.time() - cfg["lookback_hours"] * 3600, set(), []
    srcs = [s for s in cfg["sources"] if s.get("enabled", True)]
    if not srcs:
        raise ValueError("Add at least one source first.")
    for s in srcs:
        try:
            if not s.get("kind"):          # added in the settings page with just a URL: detect feed vs page now
                s = {**s, **{k: v for k, v in discover(s["url"]).items() if k != "name" or not s.get("name")}}
            log(f"Reading {s['name']}…")
            items = fetch_items(s)
        except Exception as e:
            log(f"Skipped {s.get('name') or s['url']}: {e}")
            continue
        items = [i for i in items if i["link"] and i["link"] not in seen and (not i["ts"] or i["ts"] >= cutoff)]
        items.sort(key=lambda i: i["ts"] or 0, reverse=True)
        for i in items[: cfg["max_per_source"]]:
            seen.add(i["link"])
            i["src"], i["pre"] = s, score(i["title"] + " " + i["summary"], "", i["ts"], s, cfg)
            cands.append(i)
    cands.sort(key=lambda i: i["pre"], reverse=True)
    arts = []
    for i in cands[: cfg["max_total"] + 4]:          # a few spares in case some pages fail
        s = i["src"]
        log(f"Fetching: {i['title'][:50]}")
        p = fetch_article(i["link"])
        text = p["text"] or re.sub(r"<[^>]+>", "", i["summary"]).strip()
        ts = i["ts"] or p["ts"]
        if len(text.split()) < 25 or (ts and ts < cutoff):
            continue
        u = i["img"] or p["img"]
        arts.append({
            "source": s["name"], "title": i["title"], "author": i.get("author") or p["author"],
            "date": datetime.fromtimestamp(ts or time.time()).strftime("%b %d"),
            "deck": make_deck(i["summary"], text), "url": i["link"],
            "paragraphs": [x for x in trim_words(text, cfg["max_words"]).split("\n") if x.strip()],
            "image": to_data_uri(u) if u else None, "section": s.get("section"),
            "is_lead": s.get("priority") == "lead", "score": score(i["title"], text, ts, s, cfg)})
    arts.sort(key=lambda a: -a["score"])
    return arts[: cfg["max_total"]]


def arrange(cfg, arts):
    """Pick the lead story, then group the rest into sections in your chosen order."""
    lead = max(arts, key=lambda a: (a["is_lead"], a["score"] + (5 if a["image"] else 0)))
    rest, secs = [a for a in arts if a is not lead], cfg["sections"] or ["General"]
    groups = []
    for name in secs:
        g = [a for a in rest if (a["section"] if a["section"] in secs else secs[0]) == name]
        if g:
            groups.append({"name": name, "articles": sorted(g, key=lambda a: -a["score"])})
    return lead, groups or [{"name": secs[0], "articles": []}]


# ---------- output ----------
def render(cfg, arts, out):
    from weasyprint import HTML    # imported late so the settings page still opens if Pango is missing
    lead, sections = arrange(cfg, arts)
    start = datetime.strptime(cfg["edition_start"], "%Y-%m-%d")
    env = Environment(loader=FileSystemLoader(ROOT / "templates"), autoescape=select_autoescape())
    html = env.get_template("classic.html.j2").render(
        cfg=cfg, lead=lead, sections=sections, total=len(arts), edition_no=(datetime.now() - start).days + 1,
        today=datetime.now().strftime("%A, %B %-d, %Y") if hasattr(time, "tzset") else datetime.now().strftime("%A, %B %#d, %Y"),
        sources=sorted({a["source"] for a in arts}))
    HTML(string=html, base_url=str(ROOT)).write_pdf(out)


def send_email(cfg, pdf, n):
    e = cfg["email"]
    user, pw, to = os.environ.get("SMTP_USER"), os.environ.get("SMTP_PASS"), e.get("to") or os.environ.get("MAIL_TO")
    if not (user and pw and to):
        raise ValueError("Missing the SMTP_USER / SMTP_PASS secrets or a 'Send to' address.")
    m = EmailMessage()
    m["Subject"] = f"{cfg['title']} — {datetime.now().strftime('%A, %b %d')}"
    m["From"], m["To"] = user, to
    m.set_content(f"Your paper is attached: {n} stories.")
    m.add_attachment(Path(pdf).read_bytes(), maintype="application", subtype="pdf", filename=Path(pdf).name)
    with smtplib.SMTP_SSL(e["host"], int(e["port"])) as s:
        s.login(user, pw)
        s.send_message(m)


def build(cfg, send=False, log=print, demo=False):
    arts = demo_articles() if demo else collect(cfg, log)
    if not arts:
        raise ValueError("No stories found in the time window. Try again later or add sources.")
    out = ROOT / "editions" / f"daily-paper-{datetime.now():%Y-%m-%d}.pdf"
    out.parent.mkdir(exist_ok=True)
    log("Typesetting…")
    render(cfg, arts, out)
    if send:
        log("Sending email…")
        send_email(cfg, out, len(arts))
    return out, len(arts)


def demo_articles():
    body = ("City officials said on Thursday that the plan, which has been under review for several months, would be "
            "phased in over the next two years. Residents who attended the public hearing offered mixed reactions, "
            "with some praising the focus on long-term savings and others warning about short-term disruption.")
    im = ImageOps.colorize(Image.radial_gradient("L").resize((900, 560)), (110, 100, 90), (20, 20, 20))
    buf = io.BytesIO(); im.save(buf, "JPEG")
    uri = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    heads = ["Council Approves Sweeping Transit Overhaul After Months of Debate", "New Battery Design Promises Faster Charging",
             "Markets Steady as Investors Weigh Rate Outlook", "Researchers Map Hidden Coral Reef System",
             "Local Schools Adopt Four-Day Week", "Chip Makers Race to Expand Capacity"]
    return [{"source": ["NPR", "BBC", "Ars"][i % 3], "title": h, "author": "Staff Reporter", "date": "Oct 02",
             "deck": "Officials say the plan will be phased in over two years." if i % 2 == 0 else None,
             "paragraphs": [body] * (4 if i == 0 else 2), "image": uri if i % 2 == 0 else None, "url": "https://example.com",
             "section": ["World", "Tech"][i % 2], "is_lead": False, "score": 50 - i} for i, h in enumerate(heads)]
