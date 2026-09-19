# maoyan-monitor

**English** | [简体中文](README.zh-CN.md)

A Maoyan ticket-release watcher: on-sale detection, multi-channel notifications (WeChat / sound / email), and a Playwright auto-seat script.

- Detects when a specific movie goes on sale at a specific cinema, including previews, advance presales and regular sales.
- Notifies you the moment it is detected: WeChat via ServerChan, a local sound alarm, and email.
- Optional auto seating: starts at your best seat, spirals outwards when it is taken, then submits the order.
- Never pays automatically. Once a seat is grabbed it tells you to go pay.
- Records showtime state into a CSV so the release pattern can be analysed afterwards.

## Layout

```text
maoyan-monitor/
├── config.yaml              # main config: movie, cinemas, seats, notify routing, probe
├── config.local.yaml        # secrets only (ServerChan key, mailbox auth code); gitignored
├── config_loader.py         # config loading: config.local.yaml overrides config.yaml
├── requirements.txt         # Python dependencies
├── README.md                # this file (English)
├── README.zh-CN.md          # Chinese version
├── main.py                  # entry point: polling loop + notification dispatch + optional seating
├── login.py                 # one-off manual login, saves the session
├── maoyan_client.py         # Playwright browser wrapper
├── parser.py                # parses on-sale state / purchasable showtimes
├── probe.py                 # showtime probe: appends showtime state to a CSV
├── resolver.py              # finds the showtime URL from movie/cinema pages (key piece)
├── showtime_selector.py     # picks a showtime by time window or hall keyword, switches date
├── seat_selector.py         # seat picking, consecutive blocks, spiral search, cross-showtime and cross-day retry
├── seat_map.py              # prints the seat map of a hall
├── test_seat.py             # debug seat selectors using a movie already on sale (important)
├── notifier.py              # notification channels: ServerChan / sound / email + event routing
├── state.py                 # state: notified (notification dedupe) and ordered (order submitted)
├── webui/                   # local control panel (Flask)
│   ├── app.py
│   └── templates/index.html
├── state/                   # notified.txt / ordered.txt / showtimes.csv
└── logs/                    # run logs
```

## Quick start

### 1. Install dependencies

```bash
pip install -r requirements.txt
playwright install chromium
```

### 2. Log in to Maoyan once

```bash
python login.py
```

A browser opens; scan the QR code or log in with SMS as usual. When you are done, go back to the terminal and press Enter. The session is stored in `./profile`.

### 3. Edit the config

Open `config.yaml`:

- If you already have the combined showtime URL for "this movie at this cinema", put it in `url`:
  ```text
  https://www.maoyan.com/cinemas/12345?movieId=6789
  ```
  where `12345` is the cinema ID and `6789` is the movie ID.
- If you cannot get that combined URL, leave `url` empty and fill in the two separate entry points:
  ```text
  movie page:   https://www.maoyan.com/films/6789
  cinema page:  https://www.maoyan.com/cinemas/12345
  ```
  The script extracts both IDs and builds the showtime URL itself:
  ```text
  https://www.maoyan.com/cinemas/12345?movieId=6789
  ```
  That page normally opens even before showtimes are published, showing "no showtimes yet"; the script keeps polling.
- Movie name and cinema name can be left empty: the script reads them from the pages. Multiple cinemas are supported through the `cinemas` list.
- Fill in the best seat, e.g. `[7, 5]` means row 7, seat 5.
- Optional showtime preferences:
  - `preferred_start_time: "18:00"`
  - `preferred_end_time: "21:00"`
  - only showtimes inside that window are considered.
- Optional hall keyword:
  - `preferred_hall_keyword: "IMAX"`
  - only halls containing that keyword are considered.
- Optional target date `preferred_date: "2026-09-02"`. When set, it alerts only for that date, so showtimes already on sale today or tomorrow will not produce a false alarm. Optional `max_search_dates: 3` rolls over to the next day when the current day has no free seats. Tune `interval_seconds` as needed; the default is 600 seconds.
- Notification routing, sound and mail settings live under `notify:`; secrets go into `config.local.yaml`.

### 4. Start watching

```bash
python main.py
```

## Workflow

1. **Preparation**
   - Create a ServerChan account and copy the SendKey.
   - Provide either the combined `url`, or `movie_url` plus `cinema_url` so the script can build it.
   - Decide the best row and seat.
2. **Login**
   - Run `login.py`, log in manually, and the session is saved.
3. **Watching**
   - Run `main.py`. Every `interval_seconds` it either visits the showtime page directly (when `url` is set) or looks for the showtime link on the movie or cinema page first.
   - It then parses the page for keywords such as 选座购票 / 立即购票 / 预售 / 点映 (select seats / buy now / presale / preview).
4. **On-sale trigger**
   - On detection it fires the `sale` event: WeChat push, a long sound alarm and an email.
   - With `auto_select_seat` enabled it goes straight into seat selection.
5. **Auto seating**
   - Tries your best seat first, then spirals outwards if it is taken.
   - With `seat_rule: consecutive` it looks for a consecutive block close to the target seat.
   - On failure it retries the next showtime of the same day, then the next day, up to `max_search_dates`.
   - It submits the order when `submit_order` is true, and never pays.
6. **You finish it**
   - After a "seat grabbed" notification, open the Maoyan app or website and pay.
   - After a "seating failed" notification, buy manually.

## Debugging auto seating before release day

You do not have to wait for the target movie. Use another movie that is already on sale at the same cinema: the seat-map UI is usually the same.

```bash
python test_seat.py "https://www.maoyan.com/cinemas/12345?movieId=6789"
```

Replace the URL with any on-sale movie at the same cinema.

If it reports that `profile` is in use because `main.py` is running, give the test script its own profile directory:

```bash
# log in once for the test profile
python login.py profile_test

# then run the test
python test_seat.py "https://www.maoyan.com/cinemas/12345?movieId=6789" profile_test
```

`test_seat.py` will:

1. open the showtime page;
2. pick a showtime using the time window and hall keyword from `config.yaml`;
3. click "select seats";
4. print how many seat elements each candidate selector matches;
5. print the HTML of the first seat element;
6. try to locate your best seat with the current `seat_selector.py`.

With that HTML the selectors can be tuned until they really work, so on release day the script is already armed instead of being debugged under pressure.

## Inspecting a hall's seat map

To see which seats in an IMAX/laser hall are free:

```bash
python seat_map.py "https://www.maoyan.com/cinemas/12345?movieId=6789"
```

It picks a showtime by time window and hall keyword, enters the seat map, and prints a matrix like:

```text
Row\Col  1   2   3   4   5
  7      .   .   X   .   .
  8      .   .   .   X   .
```

- `.` available
- `X` taken or unavailable

A screenshot is saved to `logs/seat_map.png`.

## Local control panel

A web panel manages the config, the watcher and the seat tools:

```bash
python webui/app.py
```

then open `http://127.0.0.1:5000`.

- edit and save `config.yaml`
- start / stop the `main.py` watcher
- run `seat_map.py` to fetch a seat map
- run `test_seat.py` to debug seat selectors
- reset the notification dedupe markers
- follow the logs live

## Notification channels (WeChat / sound / email)

Each event fans out to several channels according to its type. The routing table lives in `notify.routing` inside `config.yaml`; credentials live in `config.local.yaml`.

| Event | When | WeChat | Sound | Email | Priority |
|---|---|---|---|---|---|
| `sale` | target showtime became purchasable | yes | 20 s alarm | yes | 2 |
| `seat_ok` | seat grabbed, order submitted | yes | 25 s alarm | yes | 3 |
| `seat_fail` | seating failed (retried next round) | yes | 3 s beep | yes | 1 |
| `error` | page load and similar failures | no | no | no | - |

- **WeChat**: ServerChan, key `serverchan_send_key`.
- **Sound**: `winsound` beeps plus a Chinese announcement through PowerShell `System.Speech`; no credentials needed. Tune `volume`, `beep_count`, `beep_ms`, `beep_freq`; inside `quiet_hours` the alarm is shortened automatically.
- **Preemption**: a higher-priority event interrupts whatever is playing, so the seat-grabbed announcement cuts off the long on-sale alarm; equal or lower priority is skipped.
- **Email**: standard-library `smtplib`, currently configured for QQ Mail (`smtp.qq.com:465`); credentials go into the `email` section of `config.local.yaml`.
- **`error` is log-only**: page-load failures caused by network flakiness are not pushed, so they cannot bury the on-sale notification you actually care about.

Self-checks:

```powershell
python notifier.py --test route    # print the routing decision for all four events
python notifier.py --test sound    # beep once and speak one sentence
python notifier.py --test email    # send a test email
```

The email channel needs a direct SMTP connection. If your proxy forwards everything through an overseas node, such nodes usually block ports 25/465/587 and mail will fail (WeChat and sound are unaffected); for a domestic mailbox, add a DIRECT rule for it in the proxy.

## Showtime probe (recording release patterns)

Every round `probe.py` appends one row of showtime state to `state/showtimes.csv`, answering "at what minute did tickets appear, and when did the target date first show up in the date bar".

Rows are written only when the state changes, plus one heartbeat row every `heartbeat_minutes`, so even a 30-second poll keeps the timeline continuous without bloating the file.

| Field | Meaning |
|---|---|
| `ts` | when the row was recorded |
| `stage` | where the round stopped: `date_not_open` / `movie_missing` / `no_sessions` / `detected` / `seat_ok` / `seat_fail` / `exception` |
| `movie_found` | whether the page body contains the target title |
| `date_ok` | whether the target date tab was clicked |
| `sessions` | how many purchasable showtimes were parsed |
| `keywords` | which keywords matched |
| `date_bar_items` | number of entries in the date bar |
| `date_bar_last` | last entry, showing the edge of the bookable window |
| `target_date_present` | whether the target date already appears in the date bar |
| `error` | exception text, when there is one |
| `url` | showtime page visited this round |

Usage: open the CSV in Excel (it is `utf-8-sig`, so no mojibake) and read the release moment from the timestamps where `stage` changes. To find when the date bar started showing your target date, take the first row with `target_date_present=True`.

## Notes

- Maoyan's markup can change; the selectors in `parser.py` and `seat_selector.py` may need adjusting against the real page.
- If the seat map ever becomes a Canvas or image, the DOM-based `_find_seat` stops working and needs screenshot-plus-coordinate clicking.
- A polling interval of 60-600 seconds is reasonable; do not be too aggressive.
- Keep secrets out of the repository: they belong in `config.local.yaml`, which is gitignored.
- `state/notified.txt` only deduplicates notifications, while `state/ordered.txt` means an order was actually submitted. A failed seating attempt is never skipped forever: the same showtime is retried on the next round.
- This script is for personal ticket buying. Do not use it for scalping or bulk purchasing.
