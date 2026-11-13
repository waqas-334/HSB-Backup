# Notice and attribution

The files in `lists/` are unmodified copies of third-party blocklists, mirrored
byte for byte including their original headers. This repository does not own
them. Each file stays under the licence of its upstream project.

| File | Upstream | Original source | Licence |
|---|---|---|---|
| `malwareAddRisk.txt` | [StevenBlack/hosts](https://github.com/StevenBlack/hosts) `data/add.Risk` | [FadeMind/hosts.extras](https://github.com/FadeMind/hosts.extras) | MIT |
| `scamKadHosts.txt` | StevenBlack/hosts `data/KADhosts` | [FiltersHeroes/KADhosts](https://github.com/FiltersHeroes/KADhosts) | CC BY-SA 4.0 |
| `scamAddSpam.txt` | StevenBlack/hosts `data/add.Spam` | FadeMind/hosts.extras | MIT |
| `scamFakeNews.txt` | StevenBlack/hosts `alternates/fakenews-only` | compilation (see file header) | MIT (compilation) + per-source |
| `adsAdaway.txt` | StevenBlack/hosts `data/adaway.org` | [AdAway](https://github.com/AdAway/adaway.github.io) | CC BY 3.0 |
| `adsYoyo.txt` | StevenBlack/hosts `data/yoyo.org` | [Peter Lowe's list](https://pgl.yoyo.org/adservers/) | see upstream site |
| `adsTrackers.txt` | StevenBlack/hosts `data/add.2o7Net` | FadeMind/hosts.extras | MIT |
| `socialOnly.txt` | StevenBlack/hosts `alternates/social-only` | compilation (see file header) | MIT (compilation) + per-source |
| `gamblingOnly.txt` | StevenBlack/hosts `alternates/gambling-only` | compilation (see file header) | MIT (compilation) + per-source |
| `pornOnly.txt` | StevenBlack/hosts `alternates/porn-only` | compilation (see file header) | MIT (compilation) + per-source |
| `dNSTakeoversList.txt` | [hagezi/dns-blocklists](https://github.com/hagezi/dns-blocklists) `adblock/dyndns.txt` | HaGeZi | GPL-3.0 |
| `piracyList.txt` | hagezi/dns-blocklists `adblock/anti.piracy.txt` | HaGeZi | GPL-3.0 |
| `iPProxiesList.txt` | hagezi/dns-blocklists `adblock/doh-vpn-proxy-bypass.txt` | HaGeZi | GPL-3.0 |

Licence texts:

- [`LICENSES/StevenBlack-hosts-MIT.txt`](LICENSES/StevenBlack-hosts-MIT.txt): StevenBlack/hosts, MIT
- [`LICENSES/hagezi-dns-blocklists-GPL-3.0.txt`](LICENSES/hagezi-dns-blocklists-GPL-3.0.txt): hagezi/dns-blocklists, GPL-3.0

StevenBlack/hosts is MIT-licensed as a project, but several of the lists it
redistributes carry their own licence: AdAway is CC BY 3.0 and KADhosts is
CC BY-SA 4.0. The "alternates" files merge several sources, which are listed in
each file's header. Check these per-source terms before redistributing the
lists further, for example by serving them to the app.
