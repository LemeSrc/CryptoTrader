# Handover: Coattail bot

Notes for a Claude Code session running on the owner's Windows PC (PowerShell).
Last updated: 2026-10-12, after a server check, a fix and a fresh start of the paper phase.

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
| Secrets | `/opt/coattail/.env` on the server only, never in the repo. Contains `COATTAIL_CONTACT`, Alpaca paper keys, `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` (bot `@PoliticlyBot`) |
| Config | `/opt/coattail/config/config.yaml` (copied from repo on every update, old one kept as `.bak-<date>`) |
| Database | `/opt/coattail/data/coattail.db` (SQLite) |
| News instance | systemd unit `coattail-news`, same program and `.env`, config `config/news.yaml`, database `data/news.db`. Trades only on posts (Bluesky, RSS, Federal Register, USAspending). Started 2026-10-12. Commands need `-c config/news.yaml`. Its Telegram messages start with `[Nachrichten]`. |
| Strategy study | `strategy-study/study.py` in the repo, runs on the PC only. Backtests simple rules for stocks and crypto separately. First run 2026-10-12: no rule beat buy and hold. |

Run server commands from PowerShell like this:

```powershell
ssh root@188.245.26.65 "cd /opt/coattail && sudo -u coattail .venv/bin/coattail positions"
```

Always run `coattail` commands from `/opt/coattail` as user `coattail`, otherwise paths and `.env` are not found.

## How to ship a change

1. Change code in `coattail-bot/`, run `ruff check src tests` and `pytest` (74 tests at last count).
   On the PC `python` is not on PATH. Use `C:\Users\lenny\AppData\Local\Programs\Python\Python314\python.exe` and a throwaway venv. Install `selectolax<1` there, version 1.0 breaks two capitoltrades tests (the server runs 0.4.12).
2. Commit, push a branch, open a PR with `gh` (`C:\Program Files\GitHub CLI\gh.exe`, logged in as LemeSrc). Since 2026-10-12 the owner lets Claude merge during the paper phase. Give merging back to the owner for anything touching risk or orders once real money is involved.
3. On the server: `cd ~/CryptoTrader/coattail-bot && sudo bash deploy/update.sh`, then `sudo systemctl start coattail`.
   `update.sh` pulls, stops the service, copies files, force-reinstalls the package, runs `doctor`. It never deletes the DB or `.env`. Options: `--reset-db`, `--keep-config`.
   Claude Code on the PC has the allow rule `Bash(ssh root@188.245.26.65:*)`. Commands must start exactly with `ssh root@188.245.26.65`, no options in between. Still ask the owner before `--reset-db` or deleting anything.

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

## State as of 2026-10-12

- The paper phase restarted on 2026-10-12 with a fresh database, 10,000 capital, no positions. The old database is kept on the server as `data/coattail.db.bak-20261012-194835`.
- Reason for the restart: on 2026-10-02 the bot bought KOD 47 times in one run (235% of capital instead of the 8% ticker limit). The kill switch then tripped almost daily and every new signal was rejected. Fixed in PR #9.
- Scoring was corrected on 2026-10-12 (PR #14): only buys count, because the bot never opens a position on a sale, and the drawdown is computed with 5% of capital per trade instead of all of it. Before that, 60 of 147 members failed on a drawdown above 60%, and Babin was approved with twelve sales on one day and no buys.
- After rescoring: 161 people scored, 99% price coverage, 9 approved: Tim Moore, Pelosi, David McCormick, Cleo Fields, Capito, Latta, McConnell, Whitehouse, Richard McCormick. Together 161 buys in the last 12 months, before the correction 62.
- Three of them buy one stock over and over: McConnell (WFC), David McCormick (BITB, a Bitcoin ETF), Latta (FMAO, his local bank). That is one idea each, not stock picking. Recommendation given to the owner: block them with `coattail set-override`, and raise `scoring.min_trades` to 20 before any real money.
- The Congress pool is limited: 147 members with trades, 69 of them with too few. More actors need new sources. `sec_13f` (funds) is only a stub: latest quarter, no ticker mapping, no buy and sell differences.
- Telegram is on. A manual test message arrived. Not yet seen: a message sent by the bot itself (daily report is scheduled for 21:05 UTC).
- Expect about 3 to 5 paper trades per month. Paper phase planned for two to three months from 2026-10-12.

## Open items

1. Routine check: `coattail report`, `coattail positions`, `coattail signals -n 20`, `coattail orders`, and `journalctl -u coattail --since "3 days ago" | grep -E "WARNING|ERROR|CRITICAL|Bewertung fertig"`. In `orders`, look for the same ticker bought several times at the same minute.
2. Confirm with the owner that the bot's own Telegram messages arrive.
3. `selectolax` has no upper bound in `pyproject.toml`. A rebuilt server venv would pull 1.0, which breaks the HTML parsing in `capitoltrades` and one social source. Both are off today.
4. `max_open_positions` does not count buys planned in the same run. The 80% gross limit catches it.
5. The kill switch logs a CRITICAL line every 5 minutes while active instead of once.
6. `person_key` splits one member into two when the source writes suffixes differently (seen: Neal Dunn as `facs_patrick` and `facs_dunn`). Fixing it needs a merge of existing actors.
6. Not looked into: why the service restarted on 2026-10-02 and 2026-10-04, and whether a fund like Baker Bros. Advisors (Form 4, 10% owner) is a sensible actor to copy.
7. Cosmetic: yfinance still prints ERROR lines for delisted tickers during `coattail score`. Harmless.
8. Before real money: `min_trades: 20`, at least two to three months of paper results that beat SPY after fees, kill switch tested (`coattail kill` / `coattail resume`), then Alpaca live with a small amount.

## Things learned the hard way

- `pip install` on the server does nothing if the version did not change. `update.sh` force-reinstalls for that reason.
- Scoring calls the price series about ten times per filing. Anything that is not cached gets hit again and again. The missing-list (`data/prices/_missing.json`) and the bulk prefetch exist because of this. If a scoring run hangs, check the log for Yahoo throttling first.
- Delete `data/prices/_missing.json` if it was written while a price source was blocked.
- Risk limits read positions from the database. Anything approved in the same run is not booked yet, so `_decide` passes the planned amounts to `RiskManager.evaluate`. One Form 4 filing can produce dozens of signals for the same ticker.
- `update.sh` stops both services and starts neither. Start with `systemctl start coattail coattail-news`. Bash reads the script before `git pull` replaces it, so a change to `update.sh` itself only takes effect on the second run.
- No `ANTHROPIC_API_KEY` on the server. Posts are classified by the rule stage only, in both instances, so post signals are rare. The main bot also trades post signals, the news instance exists to see them in isolation.
- A fresh database loses the Form 4 actors until their filings have been collected again. The approved count drops for a while after `--reset-db`.
