#!/usr/bin/env python3
"""Diagnostic probe for the Marion Joke Bot.

Subscribes to channel messages with NO attribute filter and dumps the raw
payload of everything that arrives. The point is to tell three cases apart:

  1. nothing arrives at all      -> the repeater is not fanning channel RX out
                                    to this companion
  2. events arrive but the       -> the attribute filter in jokes.py is wrong
     payload keys differ            (check what the channel field is called)
  3. events arrive and look      -> the trigger matching is at fault
     right

Stop jokes.service before running this so the two are not fighting over the
same companion.

    sudo systemctl stop jokes.service
    /opt/jokes/venv/bin/python3 /opt/jokes/probe.py
    # ... now send "joke cat" on the public channel from a client ...
"""

import argparse
import asyncio
import json
import sys
import time

from meshcore import EventType, MeshCore


def stamp():
    return time.strftime("%H:%M:%S")


async def main(host, port, seconds):
    mc = await MeshCore.create_tcp(host, port)
    print("[%s] connected to %s:%s" % (stamp(), host, port))

    seen = {"count": 0}

    async def on_any_channel_message(event):
        seen["count"] += 1
        payload = getattr(event, "payload", None)
        print("\n[%s] --- CHANNEL_MSG_RECV #%d ---" % (stamp(), seen["count"]))
        print("payload type: %s" % type(payload).__name__)
        try:
            print(json.dumps(payload, indent=2, default=str))
        except (TypeError, ValueError):
            print(repr(payload))
        if isinstance(payload, dict):
            print("keys: %s" % sorted(payload))
            print("text: %r" % payload.get("text"))
            for key in ("channel_idx", "channel", "channel_index", "chan_idx"):
                if key in payload:
                    print("channel field %r = %r (%s)"
                          % (key, payload[key], type(payload[key]).__name__))

    async def on_contact_message(event):
        # If direct messages arrive but channel messages don't, that narrows it
        # to channel fan-out rather than the companion link in general.
        print("\n[%s] --- CONTACT_MSG_RECV ---" % stamp())
        print(repr(getattr(event, "payload", None)))

    mc.subscribe(EventType.CHANNEL_MSG_RECV, on_any_channel_message)
    mc.subscribe(EventType.CONTACT_MSG_RECV, on_contact_message)

    await mc.start_auto_message_fetching()
    print("[%s] listening %ds -- send 'joke cat' on the public channel now"
          % (stamp(), seconds))

    try:
        await asyncio.sleep(seconds)
    finally:
        print("\n[%s] done. channel messages seen: %d" % (stamp(), seen["count"]))
        if not seen["count"]:
            print("NOTHING ARRIVED -- the repeater is not delivering channel RX")
            print("to this companion. That is a repeater-side fan-out problem,")
            print("not a jokes.py problem.")
        await mc.disconnect()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Dump raw companion events.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5055)
    parser.add_argument("-t", "--seconds", type=int, default=120)
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.host, args.port, args.seconds)) or 0)
