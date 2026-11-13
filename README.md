# HSB filter-list mirror

A validated, versioned copy of the 13 third-party blocklists used by the
**High Security Blocking** feature of the Android browser
(`CoinCircle/IncognitoBrowser`, see `FilterListHelper.kt`).

A GitHub Actions workflow runs every day. It downloads each list, validates it,
commits any list whose content changed, and opens an issue when a list is
failing, suspicious or stale. A list that fails to download or fails validation
is never overwritten: the last good copy stays in `lists/`.

Publishing the files (Firebase Hosting etc.) and changes to the app are out of
scope for now.

## Layout

| Path | What |
|---|---|
| `sources.json` | the lists to mirror, with per-list validation settings |
| `lists/` | mirrored files, byte for byte identical to upstream |
| `manifest.json` | per list: url, sha256, bytes, entry_count, etag, timestamps, last status |
| `scripts/sync.py` | the sync script (Python 3, standard library only) |
| `tests/test_sync.py` | unit tests (no network) |
| `.github/workflows/sync.yml` | daily schedule + manual trigger |
| `LICENSES/`, `NOTICE.md` | upstream licences and attribution |

## Run locally

```sh
python3 -m unittest discover -s tests -v   # or: pytest
python3 scripts/sync.py
```

The script prints a summary table and writes `sync-report.json`, which is
git-ignored. It exits with 1 if any list failed or was rejected.

> **macOS + python.org Python:** if you get `CERTIFICATE_VERIFY_FAILED`, run
> `SSL_CERT_FILE=/etc/ssl/cert.pem python3 scripts/sync.py`, or run
> `Install Certificates.command` from your Python folder in `/Applications`.

## Status values (`last_status` in `manifest.json`, and the summary table)

| Status | Meaning | File in `lists/` |
|---|---|---|
| `updated` | new content downloaded and validated | replaced |
| `unchanged` | upstream returned 304, or the same bytes | untouched |
| `fetch_failed: <reason>` | 404, 5xx, timeout or network error after 3 attempts | **last good copy kept** |
| `rejected: <reason>` | downloaded but failed validation (see below) | **last good copy kept** |
| ⚠️ `stale` (flag) | `last_changed_at` is older than `stale_after_days` | untouched; this is a warning only |

`fetch_failed` and `rejected` make the run fail (exit code 1, red workflow run).
Any of `fetch_failed`, `rejected` or `stale` opens a single issue labelled
`mirror-sync`. If that issue is already open, the run adds a comment to it
instead. The first fully green run after that comments "Recovered" and closes
the issue.

`manifest.json` is only committed when a list's content changed. Otherwise the
timestamp-only diff would make a commit every day. This means the committed
`last_checked_at` / `last_status` show the state at the last content change.
The current state of every run is in the workflow job summary.

## Validation rules (applied to every 200 response)

1. The body is not empty and not HTML (checked by content type or by the start of the body).
2. At least `min_parse_ratio` (90%) of the non-comment lines parse in the expected format:
   - **hosts**: `0.0.0.0 example.com` or `127.0.0.1 example.com`. Comments (`#`) and blank
     lines are skipped, and so is the localhost/broadcast boilerplate (`localhost`,
     `localhost.localdomain`, `local`, `broadcasthost`, `ip6-*`, `0.0.0.0 0.0.0.0`, IPv6 loopback lines).
   - **adblock**: `||example.com^`. Lines starting with `!`, `#` or `[` and blank lines are skipped.
3. Entry count ≥ `min_entries`, an absolute floor set to about 50% of the count on 2026-11-12.
4. The entry count has not dropped more than `max_drop_pct` (30%) since the last accepted
   version. This rule is skipped when the previous count is below
   `drop_check_min_previous` (200), because tiny lists swing a lot.

On the first run (no manifest yet) there is no previous count, so only rules 1 to 3 apply.

## sources.json

```jsonc
{
  "defaults": { "max_drop_pct": 30, "stale_after_days": 14,
                "min_parse_ratio": 0.9, "drop_check_min_previous": 200 },
  "sources": [
    { "name": "socialOnly.txt",            // file name in lists/ (the app depends on it)
      "url": "https://...",
      "feature": "SOCIAL_MEDIA_BLOCKING",  // informational
      "format": "hosts",                   // "hosts" or "adblock"
      "min_entries": 1900,
      "stale_after_days": null,            // optional override of any default; null = no staleness check
      "note": "..." }                      // optional, informational
  ]
}
```

The staleness check is turned off (`null`) for five lists that rarely or never
change upstream. `add.Risk`, `add.Spam` and `add.2o7Net` have not changed since
2021–2023. StevenBlack has paused updates for `yoyo.org` and `adaway.org`.
With the check on, these five would keep the alert issue open permanently.

### Add a source

1. Add an entry to `sources.json`. Set `min_entries` to about half of the list's current entry
   count. You can run the script once with a low value to see the count.
2. Run `python3 scripts/sync.py` locally, or push and trigger the workflow
   manually (**Actions → Sync filter lists → Run workflow**).
3. Add the list to `NOTICE.md`, and its licence to `LICENSES/` if it is new.

### Remove a source

1. Delete its entry from `sources.json` and delete `lists/<name>`. The next sync drops it from `manifest.json`.
2. Update `NOTICE.md`.

## Alerts

Two optional repository variables (Settings → Secrets and variables → Actions → Variables) control alerts:

- `ALERT_ASSIGNEES`: comma-separated GitHub logins assigned to a new alert issue.
- `ALERT_MENTION`: text added to the top of every alert, e.g. `@CoinCircle/android`.

## Known limitation

If the workflow stops running entirely (Actions disabled, billing problem, or a
public repo's scheduled workflows being auto-disabled after 60 days without
activity), this repo cannot report that itself. An external staleness check is
planned as part of the app/Firebase work.
