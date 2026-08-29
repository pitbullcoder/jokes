#!/usr/bin/env python3
"""Marion Joke Bot.

Listens on the public channel for trigger phrases ("joke dustin", "joke cat")
and replies with the next joke from the matching list. Unlike the weather, news,
gas and info bots this one is a long-running service rather than a timer -- it
holds the companion connection open and reacts to incoming channel messages.

Jokes rotate strictly in order and the position survives restarts, so nobody
hears the same joke twice until the list wraps.
"""

import argparse
import asyncio
import json
import logging
import os
import string
import sys
import time

LOG = logging.getLogger("jokes")

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG_PATH = os.path.join(HERE, "config.json")

# MeshCore transmission ceiling. Every reply is built to fit inside one of these.
MAX_MESSAGE_CHARS = 130

# How long to wait before retrying a dropped companion connection.
RECONNECT_DELAY_SECS = 15


def load_config(path):
    """Read and validate the JSON config file."""
    with open(path, "r", encoding="utf-8") as handle:
        cfg = json.load(handle)

    for key in ("host", "port", "channel_index", "triggers"):
        if key not in cfg:
            raise ValueError("config missing required key: %s" % key)

    if not isinstance(cfg["triggers"], dict) or not cfg["triggers"]:
        raise ValueError("config 'triggers' must be a non-empty object")

    for phrase, list_name in cfg["triggers"].items():
        if not isinstance(phrase, str) or not phrase.strip():
            raise ValueError("every trigger phrase must be a non-empty string")
        if not isinstance(list_name, str) or not list_name.strip():
            raise ValueError("every trigger must map to a non-empty list name")

    cfg.setdefault("jokes_path", os.path.join(HERE, "jokes.json"))
    cfg.setdefault("state_path", os.path.join(HERE, "state.json"))
    cfg.setdefault("cooldown_secs", 60)

    if not isinstance(cfg["cooldown_secs"], (int, float)) or cfg["cooldown_secs"] < 0:
        raise ValueError("config 'cooldown_secs' must be a non-negative number")

    return cfg


def load_jokes(path):
    """Read the joke lists and reject anything that would not survive the air."""
    with open(path, "r", encoding="utf-8") as handle:
        book = json.load(handle)

    if not isinstance(book, dict) or not book:
        raise ValueError("jokes file must be a non-empty object")

    for name, jokes in book.items():
        if not isinstance(jokes, list) or not jokes:
            raise ValueError("joke list '%s' must be a non-empty list" % name)
        for joke in jokes:
            if not isinstance(joke, str) or not joke.strip():
                raise ValueError("every joke in '%s' must be a non-empty string" % name)

    return book


def normalize(text):
    """Fold an incoming message down to a comparable form.

    Lowercased, punctuation stripped from the ends, whitespace collapsed -- so
    "Joke Cat!", "  joke   cat  " and "joke cat?" all match the same trigger.
    """
    if not text:
        return ""
    words = text.lower().split()
    cleaned = [word.strip(string.punctuation) for word in words]
    return " ".join(word for word in cleaned if word)


def match_trigger(text, triggers):
    """Return the joke list name for an incoming message, or None.

    Matching is exact against the whole normalized message. Substring matching
    would fire on someone merely talking about the bot, and on a shared channel
    that turns into a feedback loop nobody enjoys.
    """
    normalized = normalize(text)
    if not normalized:
        return None

    for phrase, list_name in triggers.items():
        if normalize(phrase) == normalized:
            return list_name

    return None


def fit(message, limit=MAX_MESSAGE_CHARS):
    """Trim a joke to the radio's ceiling, preferring a clean word boundary."""
    message = " ".join(message.split())
    if len(message) <= limit:
        return message

    clipped = message[:limit]
    spaced = clipped.rsplit(" ", 1)[0]
    return (spaced if len(spaced) > limit // 2 else clipped).rstrip()


class JokeBook:
    """The joke lists plus the rotation position for each one.

    Reloads jokes.json when the file changes on disk, so adding a joke is an
    edit and not a restart. The rotation position is written back after every
    reply, which keeps the order intact across restarts and reboots.
    """

    def __init__(self, jokes_path, state_path):
        self.jokes_path = jokes_path
        self.state_path = state_path
        self._mtime = None
        self._book = {}
        self._state = self._read_state()
        self.reload_if_changed()

    def _read_state(self):
        try:
            with open(self.state_path, "r", encoding="utf-8") as handle:
                state = json.load(handle)
        except (OSError, ValueError):
            return {}

        if not isinstance(state, dict):
            return {}
        return {k: v for k, v in state.items() if isinstance(v, int) and v >= 0}

    def _write_state(self):
        tmp = self.state_path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(self._state, handle)
            os.replace(tmp, self.state_path)
        except OSError as exc:
            # A read-only state file costs us the position across restarts but
            # should never take the bot off the air.
            LOG.warning("could not write state to %s: %s", self.state_path, exc)

    def reload_if_changed(self):
        """Re-read jokes.json if its mtime moved. Keeps the old book on error."""
        try:
            mtime = os.path.getmtime(self.jokes_path)
        except OSError as exc:
            LOG.warning("cannot stat %s: %s", self.jokes_path, exc)
            return

        if mtime == self._mtime:
            return

        try:
            book = load_jokes(self.jokes_path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            LOG.error("keeping previous jokes; %s is bad: %s", self.jokes_path, exc)
            return

        self._book = book
        self._mtime = mtime
        LOG.info(
            "loaded %d list(s): %s",
            len(book),
            ", ".join("%s=%d" % (name, len(jokes)) for name, jokes in sorted(book.items())),
        )

        for name, jokes in book.items():
            oversized = [joke for joke in jokes if len(joke) > MAX_MESSAGE_CHARS]
            if oversized:
                LOG.warning(
                    "%d joke(s) in '%s' exceed %d chars and will be trimmed on air",
                    len(oversized),
                    name,
                    MAX_MESSAGE_CHARS,
                )

    def lists(self):
        return sorted(self._book)

    def peek(self, list_name):
        """The joke that would be sent next, without advancing."""
        jokes = self._book.get(list_name)
        if not jokes:
            return None
        return fit(jokes[self._state.get(list_name, 0) % len(jokes)])

    def next_joke(self, list_name):
        """Take the next joke in sequence and advance the position."""
        jokes = self._book.get(list_name)
        if not jokes:
            LOG.error("no joke list named '%s'", list_name)
            return None

        index = self._state.get(list_name, 0) % len(jokes)
        joke = jokes[index]
        self._state[list_name] = (index + 1) % len(jokes)
        self._write_state()
        return fit(joke)


class Cooldown:
    """Per-list rate limit.

    LoRa is a shared, slow medium. Without this, one person holding down "joke
    cat" would wallpaper the public channel for everyone on the mesh.
    """

    def __init__(self, seconds, clock=time.monotonic):
        self.seconds = seconds
        self._clock = clock
        self._last = {}

    def ready(self, key):
        if self.seconds <= 0:
            return True
        last = self._last.get(key)
        return last is None or (self._clock() - last) >= self.seconds

    def mark(self, key):
        self._last[key] = self._clock()


def make_handler(cfg, book, cooldown, sender):
    """Build the CHANNEL_MSG_RECV callback."""

    async def on_channel_message(event):
        payload = getattr(event, "payload", None) or {}
        text = payload.get("text", "")
        who = payload.get("pubkey_prefix", "unknown")

        list_name = match_trigger(text, cfg["triggers"])
        if not list_name:
            return

        if not cooldown.ready(list_name):
            LOG.info("ignoring '%s' from %s; still cooling down", list_name, who)
            return

        book.reload_if_changed()
        joke = book.next_joke(list_name)
        if not joke:
            return

        cooldown.mark(list_name)
        LOG.info("replying to %s with %s (%d chars): %s", who, list_name, len(joke), joke)
        await sender(joke)

    return on_channel_message


async def run(cfg, book, cooldown):
    """Connect to the companion and serve requests until interrupted.

    Reconnects on its own, because a service that dies quietly on a dropped
    socket is worse than one that keeps trying.
    """
    from meshcore import EventType, MeshCore

    while True:
        try:
            mc = await MeshCore.create_tcp(cfg["host"], cfg["port"])
        except Exception as exc:  # noqa: BLE001 - any transport failure retries
            LOG.error("connect to %s:%s failed: %s", cfg["host"], cfg["port"], exc)
            await asyncio.sleep(RECONNECT_DELAY_SECS)
            continue

        LOG.info("connected to %s:%s, watching channel %s",
                 cfg["host"], cfg["port"], cfg["channel_index"])

        async def sender(message):
            await mc.commands.send_chan_msg(cfg["channel_index"], message)

        try:
            mc.subscribe(
                EventType.CHANNEL_MSG_RECV,
                make_handler(cfg, book, cooldown, sender),
                attribute_filters={"channel_idx": cfg["channel_index"]},
            )
            await mc.start_auto_message_fetching()
            LOG.info("listening for: %s", ", ".join(sorted(cfg["triggers"])))

            while True:
                await asyncio.sleep(3600)
                book.reload_if_changed()
        except asyncio.CancelledError:
            await mc.disconnect()
            raise
        except Exception as exc:  # noqa: BLE001 - drop the socket and retry
            LOG.error("connection lost: %s", exc)
            try:
                await mc.disconnect()
            except Exception:  # noqa: BLE001
                pass
            await asyncio.sleep(RECONNECT_DELAY_SECS)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Answer joke requests on the Marion mesh.")
    parser.add_argument("-c", "--config", default=DEFAULT_CONFIG_PATH, help="path to config.json")
    parser.add_argument(
        "-n",
        "--dry-run",
        metavar="TRIGGER",
        help="print the reply for a trigger phrase without connecting to the radio",
    )
    parser.add_argument(
        "-l",
        "--list",
        action="store_true",
        help="show the configured triggers and the next joke queued for each",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    try:
        cfg = load_config(args.config)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        LOG.error("could not load config from %s: %s", args.config, exc)
        return 1

    try:
        book = JokeBook(cfg["jokes_path"], cfg["state_path"])
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        LOG.error("could not load jokes from %s: %s", cfg["jokes_path"], exc)
        return 1

    if not book.lists():
        LOG.error("no joke lists loaded")
        return 1

    if args.list:
        for phrase, list_name in sorted(cfg["triggers"].items()):
            joke = book.peek(list_name)
            print("%-16s -> %-10s %s" % (phrase, list_name, joke or "(empty list)"))
        return 0

    if args.dry_run:
        list_name = match_trigger(args.dry_run, cfg["triggers"])
        if not list_name:
            print("no trigger matches %r" % args.dry_run)
            return 1
        joke = book.next_joke(list_name)
        print("--- %d of %d chars ---" % (len(joke), MAX_MESSAGE_CHARS))
        print(joke)
        return 0

    cooldown = Cooldown(cfg["cooldown_secs"])
    try:
        asyncio.run(run(cfg, book, cooldown))
    except KeyboardInterrupt:
        LOG.info("shutting down")
        return 0
    except Exception as exc:  # noqa: BLE001 - service unit should log and exit nonzero
        LOG.error("joke bot failed: %s", exc)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
