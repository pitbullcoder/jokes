# Jokes

MeshCore Station G3 joke bot. Listens on the **public** channel and answers
`joke dustin` with a clean ham radio joke about Dustin, and `joke cat` with a
cat joke. Jokes come out in order and never repeat until the list wraps.

## How it differs from the other bots

Weather, news, gas and info are timer-driven: they wake up, transmit, and exit.
This one has to *hear* requests, so it is a long-running service that holds the
companion connection open and subscribes to `CHANNEL_MSG_RECV`. There is no
timer unit — `Restart=always` keeps it up, and it reconnects on its own if the
companion socket drops.

## Behavior

- Channel messages arrive with the sender's name folded into the text — a
  request from KF8DDU shows up as `KF8DDU: joke cat`. The bot strips that
  prefix before matching.
- Triggers match the **whole** message once the prefix is off, case
  insensitively, ignoring extra spaces and trailing punctuation. `Joke Cat!`
  works; `hey try joke cat` does not. Whole-message matching is deliberate —
  the bot replies on the same channel it listens to, so substring matching
  would let a reply retrigger it.
- `ignore_senders` drops anything the bot hears from itself, as a second layer
  of loop protection behind whole-message matching.
- The same prefixing applies to replies, so the node name eats into the
  130-character budget. `reply_overhead_chars` reserves it: at 18 (the length
  of `Marion Jokes Bot: `) the longest shipped joke lands at 104 on the air.
- Each list has its own position, persisted to `state.json` after every reply,
  so a restart or reboot resumes where it left off instead of starting over.
- A per-list cooldown (default 60s) keeps someone hammering `joke cat` from
  wallpapering the channel. LoRa is shared and slow.
- Every reply is trimmed to MeshCore's 130-character limit at a word boundary.

## Install

```bash
sudo git clone https://github.com/pitbullcoder/jokes.git /opt/jokes
cd /opt/jokes
sudo python3 -m venv venv
sudo ./venv/bin/pip install -r requirements.txt
sudo nano config.json
```

The bot writes `state.json` in its own directory, so `pi` needs to be able to
write there:

```bash
sudo chown -R pi:pi /opt/jokes
```

Check the replies before putting it on the air:

```bash
/opt/jokes/venv/bin/python3 /opt/jokes/jokes.py --list
/opt/jokes/venv/bin/python3 /opt/jokes/jokes.py --dry-run "joke cat"
```

`--list` shows what each trigger would say next without advancing. `--dry-run`
takes the next joke and does advance the rotation, printing the character count
so you can see the headroom.

Then install the service:

```bash
sudo cp jokes.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now jokes.service
journalctl -u jokes.service -f
```

You want to see `connected to ...` followed by `listening for: joke cat, joke
dustin`. Then send `joke cat` from a client on the public channel.

## Configuration

| Key | Meaning |
| --- | --- |
| `host` / `port` | Companion TCP endpoint — this bot needs its **own** companion |
| `channel_index` | Channel to listen and reply on. `0` is public. |
| `cooldown_secs` | Minimum seconds between replies per list. `0` disables. |
| `reply_overhead_chars` | Characters reserved for the `Node Name: ` prefix on replies |
| `ignore_senders` | Sender names to ignore — put the bot's own node name here |
| `triggers` | Map of trigger phrase to joke list name |
| `jokes_path` | Optional. Defaults to `jokes.json` beside the script. |
| `state_path` | Optional. Defaults to `state.json` beside the script. |

Adding a joke is a `jokes.json` edit — the bot notices the file changed and
reloads it on the next request, no restart needed.

Adding a whole new category means a new array in `jokes.json` plus a line in
`triggers`; that one does need a restart, since triggers are read at startup.

## Tests

```bash
python3 -m unittest jokes_test -v
python3 -m pyflakes jokes.py jokes_test.py
```

The suite covers the matcher, the rotation, the cooldown and the reply loop,
and also checks the shipped `jokes.json`: at least 30 jokes per list, none over
130 characters, no duplicates, and no joke that would trigger the bot itself.

## Troubleshooting

If the bot connects and logs `listening for: ...` but never answers, run it with
`-v` — debug logging prints every channel message it hears, matched or not:

```bash
sudo systemctl stop jokes.service
/opt/jokes/venv/bin/python3 /opt/jokes/jokes.py -v
```

`probe.py` goes one level lower and dumps the raw payload of every event the
companion delivers, with no filtering at all. Use it when you need to know
whether messages are arriving but being rejected, or not arriving in the first
place:

```bash
/opt/jokes/venv/bin/python3 /opt/jokes/probe.py
```

## Notes

- This bot needs its own companion on the repeater. The four existing bots use
  5050–5054; the config here assumes a sixth on 5055. Create it in the web
  setup UI, then give it 2-byte path hashes like the others:

  ```bash
  sudo sqlite3 /var/lib/openhop_repeater/repeater.db \
    "INSERT INTO companion_prefs (companion_hash, prefs_json)
     VALUES ('0xNN', '{\"path_hash_mode\":1}');"
  ```

- Because the bot both listens and replies on channel 0, watch the first live
  test closely. If you ever see it answering itself, stop the service and check
  what the reply text normalizes to.

## License

GPL-3.0
