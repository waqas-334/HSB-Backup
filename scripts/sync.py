#!/usr/bin/env python3
"""Mirror the HSB filter lists listed in sources.json into lists/.

Standard library only. For every source: conditional GET (ETag), validate,
write atomically, update manifest.json. A list that fails to download or fails
validation keeps its last good copy. See README.md for the status meanings.
"""

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DEFAULTS = {
    "max_drop_pct": 30,
    "stale_after_days": 14,
    "min_parse_ratio": 0.9,
    "drop_check_min_previous": 200,
}

TIMEOUT_S = 30
ATTEMPTS = 3
BACKOFF_S = 5
USER_AGENT = "hsb-filter-mirror/1.0 (+https://github.com/waqas-334/HSB-Backup)"

# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)([a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?)(\.[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?)*\.?$"
)
ADBLOCK_RE = re.compile(r"^\|\|([a-z0-9*_.-]+)\^(\$[^\s]*)?$")

HOSTS_BLOCK_IPS = {"0.0.0.0", "127.0.0.1"}
HOSTS_BOILERPLATE_IPS = {
    "::1", "fe80::1%lo0", "fe00::0", "ff00::0", "ff02::1", "ff02::2", "ff02::3",
    "255.255.255.255",
}
HOSTS_BOILERPLATE_DOMAINS = {
    "localhost", "localhost.localdomain", "local", "broadcasthost", "0.0.0.0",
}


def parse_hosts(text):
    """Return (entries, malformed) for a hosts-format file."""
    entries = malformed = 0
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        ip = parts[0]
        domain = parts[1].lower() if len(parts) > 1 else ""
        if ip in HOSTS_BOILERPLATE_IPS or domain in HOSTS_BOILERPLATE_DOMAINS or domain.startswith("ip6-"):
            continue
        if ip in HOSTS_BLOCK_IPS and len(parts) == 2 and HOSTNAME_RE.match(domain):
            entries += 1
        else:
            malformed += 1
    return entries, malformed


def parse_adblock(text):
    """Return (entries, malformed) for an adblock-format (||domain^) file."""
    entries = malformed = 0
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("!", "#", "[")):
            continue
        if ADBLOCK_RE.match(line.lower()):
            entries += 1
        else:
            malformed += 1
    return entries, malformed


PARSERS = {"hosts": parse_hosts, "adblock": parse_adblock}


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def looks_like_html(body, content_type=""):
    if "html" in (content_type or "").lower():
        return True
    head = body[:1024].lstrip().lower()
    return head.startswith((b"<!doctype html", b"<html", b"<head", b"<body"))


def validate(body, content_type, source, settings, previous_count):
    """Return (entry_count, None) if acceptable, else (entry_count, reason)."""
    if not body.strip():
        return 0, "empty body"
    if looks_like_html(body, content_type):
        return 0, "body is HTML"
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return 0, "body is not valid UTF-8"

    entries, malformed = PARSERS[source["format"]](text)
    total = entries + malformed
    ratio = entries / total if total else 0.0
    if ratio < settings["min_parse_ratio"]:
        return entries, (
            f"only {ratio:.0%} of lines parse as {source['format']} "
            f"(need {settings['min_parse_ratio']:.0%})"
        )
    if entries < source["min_entries"]:
        return entries, f"{entries} entries < min_entries {source['min_entries']}"
    if previous_count and previous_count >= settings["drop_check_min_previous"]:
        drop_pct = (previous_count - entries) * 100 / previous_count
        if drop_pct > settings["max_drop_pct"]:
            return entries, (
                f"entry count dropped {drop_pct:.1f}% ({previous_count} -> {entries}), "
                f"max allowed {settings['max_drop_pct']}%"
            )
    return entries, None


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

class FetchResult:
    def __init__(self, status, body=b"", etag=None, content_type=""):
        self.status = status
        self.body = body
        self.etag = etag
        self.content_type = content_type


class FetchError(Exception):
    pass


def fetch_once(url, etag=None, timeout=TIMEOUT_S):
    headers = {"User-Agent": USER_AGENT}
    if etag:
        headers["If-None-Match"] = etag
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return FetchResult(
                resp.status,
                resp.read(),
                resp.headers.get("ETag"),
                resp.headers.get("Content-Type", ""),
            )
    except urllib.error.HTTPError as e:
        if e.code == 304:
            return FetchResult(304, etag=e.headers.get("ETag") if e.headers else None)
        raise FetchError(f"HTTP {e.code}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        reason = getattr(e, "reason", e)
        raise FetchError(f"network error: {reason}") from e


def fetch(url, etag=None, fetcher=fetch_once, sleep=time.sleep, attempts=ATTEMPTS):
    """Fetch with retries. 4xx errors (except 408/429) are not retried."""
    last = None
    for attempt in range(1, attempts + 1):
        try:
            return fetcher(url, etag)
        except FetchError as e:
            last = e
            msg = str(e)
            if msg.startswith("HTTP 4") and msg not in ("HTTP 408", "HTTP 429"):
                break
            if attempt < attempts:
                sleep(BACKOFF_S * attempt)
    raise last


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------

def utcnow():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s):
    return dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)


def atomic_write(path, data):
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def sync_source(source, prev, lists_dir, settings, now, fetcher=fetch_once, sleep=time.sleep):
    """Process one source. Returns the new manifest entry (prev is never mutated)."""
    entry = dict(prev or {})
    entry["url"] = source["url"]
    entry["last_checked_at"] = iso(now)
    path = os.path.join(lists_dir, source["name"])
    have_file = os.path.exists(path)

    # Only send the ETag if we still have the file it refers to.
    etag = entry.get("etag") if have_file else None
    try:
        res = fetch(source["url"], etag, fetcher=fetcher, sleep=sleep)
    except FetchError as e:
        entry["last_status"] = f"fetch_failed: {e}"
        return entry

    if res.status == 304:
        entry["last_status"] = "unchanged"
        return entry
    if res.status != 200:
        entry["last_status"] = f"fetch_failed: HTTP {res.status}"
        return entry

    previous_count = entry.get("entry_count") if have_file else None
    count, reason = validate(res.body, res.content_type, source, settings, previous_count)
    if reason:
        entry["last_status"] = f"rejected: {reason}"
        return entry

    digest = sha256(res.body)
    if res.etag:
        entry["etag"] = res.etag
    if have_file and digest == entry.get("sha256"):
        entry["last_status"] = "unchanged"
        return entry

    atomic_write(path, res.body)
    entry.update({
        "sha256": digest,
        "bytes": len(res.body),
        "entry_count": count,
        "last_changed_at": iso(now),
        "last_status": "updated",
    })
    if not res.etag:
        entry.pop("etag", None)
    return entry


def is_stale(entry, settings, now):
    """stale_after_days = null in sources.json disables the check for a list."""
    changed = entry.get("last_changed_at")
    if not changed or settings["stale_after_days"] is None:
        return False
    return now - parse_iso(changed) > dt.timedelta(days=settings["stale_after_days"])


def fmt_delta(old, new):
    if old is None or new is None:
        return "new" if new is not None else "–"
    d = new - old
    return f"{d:+d}" if d else "0"


def build_summary(rows):
    lines = [
        "| List | Status | Entries | Change since last run |",
        "|---|---|---:|---:|",
    ]
    for r in rows:
        status = r["status"] + (" ⚠️ stale" if r["stale"] else "")
        entries = "–" if r["entries"] is None else f"{r['entries']:,}"
        lines.append(f"| `{r['name']}` | {status} | {entries} | {r['delta']} |")
    return "\n".join(lines)


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def run(root=ROOT, fetcher=fetch_once, sleep=time.sleep, now=None, out=sys.stdout):
    now = now or utcnow()
    config = load_json(os.path.join(root, "sources.json"), None)
    if config is None:
        raise SystemExit("sources.json not found")
    settings = {**DEFAULTS, **config.get("defaults", {})}
    manifest_path = os.path.join(root, "manifest.json")
    manifest = load_json(manifest_path, {"lists": {}})
    old_lists = manifest.get("lists", {})
    lists_dir = os.path.join(root, "lists")

    new_lists, rows = {}, []
    report = {"ok": True, "failed": [], "rejected": [], "stale": [], "changed": []}

    for source in config["sources"]:
        name = source["name"]
        src_settings = {**settings, **{k: source[k] for k in DEFAULTS if k in source}}
        prev = old_lists.get(name)
        entry = sync_source(source, prev, lists_dir, src_settings, now, fetcher, sleep)
        new_lists[name] = entry

        status = entry["last_status"]
        old_count = (prev or {}).get("entry_count")
        new_count = entry.get("entry_count")
        stale = is_stale(entry, src_settings, now)

        if status.startswith("fetch_failed"):
            report["failed"].append({"name": name, "reason": status})
        elif status.startswith("rejected"):
            report["rejected"].append({"name": name, "reason": status})
        elif status == "updated":
            report["changed"].append({"name": name, "old": old_count, "new": new_count})
        if stale:
            report["stale"].append({"name": name, "last_changed_at": entry["last_changed_at"]})

        rows.append({
            "name": name,
            "status": status,
            "stale": stale,
            "entries": new_count,
            "delta": (fmt_delta(old_count, new_count) if status == "updated"
                      else "0" if status == "unchanged" else "–"),
        })

    manifest = {"lists": new_lists}
    atomic_write(manifest_path, (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode())

    report["ok"] = not (report["failed"] or report["rejected"])
    report["needs_alert"] = bool(report["failed"] or report["rejected"] or report["stale"])
    summary = build_summary(rows)
    report["summary_md"] = summary
    report["commit_message"] = commit_message(report["changed"])
    atomic_write(os.path.join(root, "sync-report.json"),
                 (json.dumps(report, indent=2) + "\n").encode())

    print(summary, file=out)
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as f:
            f.write("## HSB filter-list sync\n\n" + summary + "\n")
    return 0 if report["ok"] else 1


def commit_message(changed):
    if not changed:
        return ""
    title = f"Update {len(changed)} filter list{'s' if len(changed) != 1 else ''}"
    body = "\n".join(f"- {c['name']}: {fmt_delta(c['old'], c['new'])} "
                     f"({c['new']:,} entries)" for c in changed)
    return f"{title}\n\n{body}\n"


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", default=ROOT, help="repo root (default: parent of scripts/)")
    args = p.parse_args(argv)
    return run(root=args.root)


if __name__ == "__main__":
    sys.exit(main())
