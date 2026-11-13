"""Unit tests for scripts/sync.py. No network: HTTP is mocked throughout.

Run with:  python -m unittest discover -s tests -v   (or: pytest)
"""

import datetime as dt
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from email.message import Message
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import sync  # noqa: E402

SETTINGS = dict(sync.DEFAULTS)
NOW = dt.datetime(2026, 11, 12, 3, 23, tzinfo=dt.timezone.utc)


def hosts_body(n, header=True):
    lines = []
    if header:
        lines += [
            "# Title: test hosts",
            "",
            "127.0.0.1 localhost",
            "127.0.0.1 localhost.localdomain",
            "127.0.0.1 local",
            "255.255.255.255 broadcasthost",
            "::1 localhost",
            "fe80::1%lo0 localhost",
            "ff02::1 ip6-allnodes",
            "0.0.0.0 0.0.0.0",
            "",
        ]
    lines += [f"0.0.0.0 host{i}.example.com" for i in range(n)]
    return ("\n".join(lines) + "\n").encode()


def adblock_body(n):
    lines = ["[Adblock Plus]", "! Title: test", "# another comment", ""]
    lines += [f"||host{i}.example.com^" for i in range(n)]
    return ("\n".join(lines) + "\n").encode()


def src(fmt="hosts", min_entries=10, name="test.txt"):
    return {"name": name, "url": f"https://example.invalid/{name}", "format": fmt,
            "min_entries": min_entries}


class FakeFetcher:
    """Returns queued FetchResults / raises queued FetchErrors; records calls."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, etag=None):
        self.calls.append((url, etag))
        r = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(r, Exception):
            raise r
        return r


def ok(body, etag='"v1"', ct="text/plain; charset=utf-8"):
    return sync.FetchResult(200, body, etag, ct)


class ParseHostsTest(unittest.TestCase):
    def test_counts_entries_and_skips_boilerplate(self):
        self.assertEqual(sync.parse_hosts(hosts_body(5).decode()), (5, 0))

    def test_accepts_127_and_inline_comments(self):
        text = "127.0.0.1 a.example.com\n0.0.0.0 b.example.com # tracker\n"
        self.assertEqual(sync.parse_hosts(text), (2, 0))

    def test_counts_malformed_lines(self):
        text = "0.0.0.0 good.example.com\nnot a hosts line at all\n1.2.3.4 x.example.com\n"
        self.assertEqual(sync.parse_hosts(text), (1, 2))


class ParseAdblockTest(unittest.TestCase):
    def test_counts_entries_and_skips_comments(self):
        self.assertEqual(sync.parse_adblock(adblock_body(7).decode()), (7, 0))

    def test_counts_malformed_lines(self):
        text = "||ok.example.com^\n0.0.0.0 hosts.style.com\n/regex/\n"
        self.assertEqual(sync.parse_adblock(text), (1, 2))

    def test_allows_wildcards_and_modifiers(self):
        self.assertEqual(sync.parse_adblock("||*.cdn.example.com^\n||x.example.com^$important\n"), (2, 0))


class ValidateTest(unittest.TestCase):
    def test_rejects_empty(self):
        _, reason = sync.validate(b"  \n", "text/plain", src(), SETTINGS, None)
        self.assertEqual(reason, "empty body")

    def test_rejects_html_by_body(self):
        _, reason = sync.validate(b"<!DOCTYPE html><html>404</html>", "text/plain", src(), SETTINGS, None)
        self.assertEqual(reason, "body is HTML")

    def test_rejects_html_by_content_type(self):
        _, reason = sync.validate(hosts_body(50), "text/html", src(), SETTINGS, None)
        self.assertEqual(reason, "body is HTML")

    def test_rejects_below_min_entries(self):
        count, reason = sync.validate(hosts_body(5), "", src(min_entries=10), SETTINGS, None)
        self.assertEqual(count, 5)
        self.assertIn("min_entries", reason)

    def test_rejects_large_drop(self):
        _, reason = sync.validate(hosts_body(600), "", src(), SETTINGS, previous_count=1000)
        self.assertIn("dropped 40.0%", reason)

    def test_accepts_drop_within_limit(self):
        count, reason = sync.validate(hosts_body(750), "", src(), SETTINGS, previous_count=1000)
        self.assertEqual((count, reason), (750, None))

    def test_small_list_exempt_from_drop_check(self):
        # previous count below 200: a 75% drop is allowed if min_entries is met
        count, reason = sync.validate(hosts_body(30), "", src(min_entries=20), SETTINGS, previous_count=120)
        self.assertEqual((count, reason), (30, None))

    def test_rejects_wrong_format(self):
        _, reason = sync.validate(adblock_body(100), "", src(fmt="hosts"), SETTINGS, None)
        self.assertIn("parse as hosts", reason)

    def test_parse_ratio_threshold(self):
        good = hosts_body(95, header=False)
        bad = b"".join(b"garbage line %d\n" % i for i in range(5))
        self.assertIsNone(sync.validate(good + bad, "", src(), SETTINGS, None)[1])
        bad = b"".join(b"garbage line %d\n" % i for i in range(15))
        self.assertIn("parse", sync.validate(good + bad, "", src(), SETTINGS, None)[1])


class FetchTest(unittest.TestCase):
    def _http_error(self, code):
        headers = Message()
        headers["ETag"] = '"v1"'
        return urllib.error.HTTPError("https://x", code, "msg", headers, io.BytesIO(b""))

    def test_304_via_urlopen(self):
        with mock.patch("urllib.request.urlopen", side_effect=self._http_error(304)) as m:
            res = sync.fetch_once("https://x", etag='"v1"')
        self.assertEqual(res.status, 304)
        self.assertEqual(m.call_args[0][0].get_header("If-none-match"), '"v1"')

    def test_404_via_urlopen_not_retried(self):
        sleeps = []
        with mock.patch("urllib.request.urlopen", side_effect=self._http_error(404)) as m:
            with self.assertRaisesRegex(sync.FetchError, "HTTP 404"):
                sync.fetch("https://x", sleep=sleeps.append)
        self.assertEqual(m.call_count, 1)
        self.assertEqual(sleeps, [])

    def test_retries_5xx_then_succeeds(self):
        f = FakeFetcher(sync.FetchError("HTTP 503"), sync.FetchError("network error: timed out"), ok(b"x"))
        sleeps = []
        res = sync.fetch("https://x", fetcher=f, sleep=sleeps.append)
        self.assertEqual(res.status, 200)
        self.assertEqual(len(f.calls), 3)
        self.assertEqual(sleeps, [sync.BACKOFF_S, 2 * sync.BACKOFF_S])

    def test_gives_up_after_three_attempts(self):
        f = FakeFetcher(sync.FetchError("HTTP 500"))
        with self.assertRaises(sync.FetchError):
            sync.fetch("https://x", fetcher=f, sleep=lambda s: None)
        self.assertEqual(len(f.calls), 3)


class SyncSourceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.path = os.path.join(self.dir, "test.txt")

    def tearDown(self):
        self.tmp.cleanup()

    def run_source(self, fetcher, prev=None, source=None):
        return sync.sync_source(source or src(), prev, self.dir, SETTINGS, NOW, fetcher, lambda s: None)

    def baseline(self, n=500):
        entry = self.run_source(FakeFetcher(ok(hosts_body(n))))
        self.assertEqual(entry["last_status"], "updated")
        return entry

    def test_first_run_writes_file_and_manifest_fields(self):
        body = hosts_body(500)
        entry = self.run_source(FakeFetcher(ok(body)))
        with open(self.path, "rb") as f:
            self.assertEqual(f.read(), body)
        self.assertEqual(entry["entry_count"], 500)
        self.assertEqual(entry["bytes"], len(body))
        self.assertEqual(entry["sha256"], sync.sha256(body))
        self.assertEqual(entry["etag"], '"v1"')
        self.assertEqual(entry["last_changed_at"], "2026-11-12T03:23:00Z")

    def test_304_keeps_file_and_sends_etag(self):
        prev = self.baseline()
        f = FakeFetcher(sync.FetchResult(304))
        entry = self.run_source(f, prev)
        self.assertEqual(f.calls[0][1], '"v1"')
        self.assertEqual(entry["last_status"], "unchanged")
        self.assertEqual(entry["sha256"], prev["sha256"])
        self.assertTrue(os.path.exists(self.path))

    def test_no_etag_sent_when_file_missing(self):
        prev = self.baseline()
        os.unlink(self.path)
        f = FakeFetcher(ok(hosts_body(500)))
        entry = self.run_source(f, prev)
        self.assertIsNone(f.calls[0][1])
        self.assertTrue(os.path.exists(self.path))
        self.assertEqual(entry["last_status"], "updated")

    def test_same_hash_is_unchanged(self):
        prev = self.baseline()
        entry = self.run_source(FakeFetcher(ok(hosts_body(500), etag='"v2"')), prev)
        self.assertEqual(entry["last_status"], "unchanged")
        self.assertEqual(entry["etag"], '"v2"')

    def test_fetch_failure_keeps_last_good_file(self):
        prev = self.baseline()
        with open(self.path, "rb") as f:
            before = f.read()
        entry = self.run_source(FakeFetcher(sync.FetchError("HTTP 404")), prev)
        self.assertEqual(entry["last_status"], "fetch_failed: HTTP 404")
        with open(self.path, "rb") as f:
            self.assertEqual(f.read(), before)
        self.assertEqual(entry["sha256"], prev["sha256"])

    def test_rejection_keeps_last_good_file(self):
        prev = self.baseline(1000)
        entry = self.run_source(FakeFetcher(ok(hosts_body(100))), prev)
        self.assertTrue(entry["last_status"].startswith("rejected: entry count dropped"))
        self.assertEqual(entry["entry_count"], 1000)
        with open(self.path) as f:
            self.assertEqual(sync.parse_hosts(f.read())[0], 1000)

    def test_html_rejected_on_first_run(self):
        entry = self.run_source(FakeFetcher(ok(b"<html>rate limited</html>", ct="text/html")))
        self.assertEqual(entry["last_status"], "rejected: body is HTML")
        self.assertFalse(os.path.exists(self.path))


class RunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        cfg = {"defaults": {}, "sources": [src(name="a.txt"), src("adblock", name="b.txt")]}
        with open(os.path.join(self.root, "sources.json"), "w") as f:
            json.dump(cfg, f)

    def tearDown(self):
        self.tmp.cleanup()

    def fetcher(self, mapping):
        def f(url, etag=None):
            r = mapping[url.rsplit("/", 1)[1]]
            if isinstance(r, Exception):
                raise r
            return r
        return f

    def report(self):
        with open(os.path.join(self.root, "sync-report.json")) as f:
            return json.load(f)

    def test_all_green_then_failure_then_stale(self):
        f = self.fetcher({"a.txt": ok(hosts_body(300)), "b.txt": ok(adblock_body(300))})
        self.assertEqual(sync.run(self.root, f, lambda s: None, NOW, io.StringIO()), 0)
        rep = self.report()
        self.assertTrue(rep["ok"])
        self.assertFalse(rep["needs_alert"])
        self.assertEqual(len(rep["changed"]), 2)
        self.assertIn("Update 2 filter lists", rep["commit_message"])

        f = self.fetcher({"a.txt": sync.FetchError("HTTP 404"), "b.txt": sync.FetchResult(304)})
        self.assertEqual(sync.run(self.root, f, lambda s: None, NOW, io.StringIO()), 1)
        rep = self.report()
        self.assertEqual([x["name"] for x in rep["failed"]], ["a.txt"])
        self.assertTrue(rep["needs_alert"])
        self.assertEqual(rep["commit_message"], "")
        self.assertTrue(os.path.exists(os.path.join(self.root, "lists", "a.txt")))

        later = NOW + dt.timedelta(days=15)
        f = self.fetcher({"a.txt": sync.FetchResult(304), "b.txt": sync.FetchResult(304)})
        self.assertEqual(sync.run(self.root, f, lambda s: None, later, io.StringIO()), 0)
        rep = self.report()
        self.assertTrue(rep["ok"])
        self.assertTrue(rep["needs_alert"])
        self.assertEqual(len(rep["stale"]), 2)

    def test_stale_check_can_be_disabled_per_source(self):
        entry = {"last_changed_at": "2020-01-01T00:00:00Z"}
        self.assertTrue(sync.is_stale(entry, SETTINGS, NOW))
        self.assertFalse(sync.is_stale(entry, {**SETTINGS, "stale_after_days": None}, NOW))


if __name__ == "__main__":
    unittest.main()
