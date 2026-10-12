# Handover: Coattail bot

Notes for a Claude Code session running on the owner's Windows PC (PowerShell).
Last updated: 2026-10-12. Status details are from 2026-09-25, the last server log seen.

## The owner

- Speaks German. Answer in German.
- Beginner with GitHub, Linux and servers. Give commands one at a time, exactly as they must be typed, and say which window they go in (PowerShell on the PC or SSH on the server).
- Writing style they asked for: no AI-typical phrasing. No groups of three, no long dashes (–), no "not just X but Y", no filler adjectives, no "Natürlich!" or "Ich hoffe, das hilft". Not everything as a bullet list. Give real opinions.
- Goal: copy trades of members of Congress and good traders, paper first, real money only when paper trading is profitable.

## Where things are

| What | Where |
|---|---|
| Repository | github.com/LemeSrc/CryptoTrader, bot lives in `coattail-bot/` (the rest of the repo is the old, failed bot, ignore it) |
| Server | Hetzner Cloud CX22, Ubuntu 24.04, `188.245.26.65`, login `ssh root@188.245.26.65` with the owner's ed25519 key (`C:\Users\lenny\.ssh\id_ed25519`) |
| Clone on server | `/root/CryptoTrader` (source for updates only) |
| Installed bot | `/opt/coattail`, runs as user `coattail`, venv in `/opt/coattail/.venv` |
| Service | systemd unit `coattail` (`journalctl -u coattail -f` for the log) |
| Secrets | `/opt/coattail/.env` on the server only, never in the repo. Contains `COATTAIL_CONTACT` and Alpaca paper keys |
| Config | `/opt/coattail/config/config.yaml` (copied from repo on every update, old one kept as `.bak-<date>`) |
| Database | `/opt/coattail/data/coattail.db` (SQLite) |

Run server commands from PowerShell like this:

```powershell
ssh root@188.245.26.65 "cd /opt/coattail && sudo -u coattail .venv/bin/coattail positions"
```

Always run `coattail` commands from `/opt/coattail` as user `coattail`, otherwise paths and `.env` are not found.

## How to ship a change

1. Change code in `coattail-bot/`, run `ruff check src tests` and `pytest` (73 tests at last count).
2. Commit, push a branch, open a PR, let the owner merge it on GitHub.
3. On the server: `cd ~/CryptoTrader/coattail-bot && sudo bash deploy/update.sh`, then `sudo systemctl start coattail`.
   `update.sh` pulls, stops the service, copies files, force-reinstalls the package, runs `doctor`. It never deletes the DB or `.env`. Options: `--reset-db`, `--keep-config`.

## What the bot does

Mode `paper` with the built-in paper broker (real prices, slippage, fees, US market hours only).

| Source | Interval | Notes |
|---|---|---|
| `house_ptr`, `senate_ptr` | 3 h | Official PTRs via daily GitHub mirrors (StrokeOfLuck/house-ptr-scraper, senate-ptr-scraper). Main data. |
| `sec_form4` | 15 min | Insider buys over $250k. Insiders rarely reach 12 trades, so almost never copied. |
| `rss` | 5 min | trumpstruth.org feed (Trump's Truth Social posts) and White House presidential actions |
| `bluesky` | 3 min | A few members of Congress |
| `federal_register`, `usaspending` | 30 min / 6 h | Supplementary text signals |

Off and why: `capitoltrades` (bot challenge, 429/503 from the server), `truthsocial` direct (Cloudflare 403), `stockwatcher` (S3 buckets 403), Senate eFD direct (US-only), `x` (paid per post read, enabled only if posts prove useful).

Prices: Alpaca first (keys in `.env`), then Yahoo, then Stooq. Yahoo throttles the server quickly. The code detects throttling, pauses, and does not mark stocks as missing.

Scoring runs nightly at 03:20 plus an hourly catch-up job if no complete run in 36 h. A run with under 50% price coverage does not count as finished.

## State as of 2026-09-25

- 165 people scored, 99% price coverage, 9 approved: McConnell, Morrison, Babin, Moran, Pelosi, McCormick, Sessions, Latta, Wittman.
- Four of them (McConnell, Babin, Latta, Wittman) have only 12 to 14 trades. Recommendation given to the owner: raise `scoring.min_trades` to 20 before any real money.
- Expect about 3 to 5 paper trades per month. Paper phase planned for two to three months.

## Open items

1. Check current state: `coattail report`, `coattail positions`, `coattail signals -n 20`, and `journalctl -u coattail --since "3 days ago" | grep -E "WARNING|ERROR|Bewertung fertig"`. Nothing since 2026-09-25 has been reviewed.
2. Telegram notifications: offered, not set up yet (`notify.telegram_enabled`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`).
3. Cosmetic: yfinance still prints ERROR lines for delisted tickers during `coattail score`. Harmless.
4. Before real money: `min_trades: 20`, at least two to three months of paper results that beat SPY after fees, kill switch tested (`coattail kill` / `coattail resume`), then Alpaca live with a small amount.

## Things learned the hard way

- `pip install` on the server does nothing if the version did not change. `update.sh` force-reinstalls for that reason.
- Scoring calls the price series about ten times per filing. Anything that is not cached gets hit again and again. The missing-list (`data/prices/_missing.json`) and the bulk prefetch exist because of this. If a scoring run hangs, check the log for Yahoo throttling first.
- Delete `data/prices/_missing.json` if it was written while a price source was blocked.
