"""Tests for the Marion Joke Bot matcher, rotation and reply loop."""

import asyncio
import json
import os
import tempfile
import unittest

import jokes


class RecordingSender:
    """Stand-in for the radio transport; records what it was asked to send."""

    def __init__(self):
        self.messages = []

    async def __call__(self, message):
        self.messages.append(message)


class FakeEvent:
    def __init__(self, text, pubkey_prefix="abc123"):
        self.payload = {"text": text, "pubkey_prefix": pubkey_prefix}


class TempFileMixin:
    def _write_json(self, payload, suffix=".json"):
        handle = tempfile.NamedTemporaryFile(
            "w", suffix=suffix, delete=False, encoding="utf-8"
        )
        json.dump(payload, handle)
        handle.close()
        self.addCleanup(self._unlink, handle.name)
        return handle.name

    def _temp_path(self, suffix=".json"):
        handle = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        handle.close()
        os.unlink(handle.name)
        self.addCleanup(self._unlink, handle.name)
        return handle.name

    @staticmethod
    def _unlink(path):
        for candidate in (path, path + ".tmp"):
            try:
                os.unlink(candidate)
            except OSError:
                pass


class NormalizeTest(unittest.TestCase):
    def test_case_is_folded(self):
        self.assertEqual(jokes.normalize("Joke Cat"), "joke cat")

    def test_surrounding_whitespace_is_stripped(self):
        self.assertEqual(jokes.normalize("   joke cat   "), "joke cat")

    def test_inner_whitespace_is_collapsed(self):
        self.assertEqual(jokes.normalize("joke    cat"), "joke cat")

    def test_trailing_punctuation_is_ignored(self):
        self.assertEqual(jokes.normalize("joke cat!"), "joke cat")

    def test_empty_input_yields_empty_string(self):
        self.assertEqual(jokes.normalize(""), "")

    def test_none_input_yields_empty_string(self):
        self.assertEqual(jokes.normalize(None), "")

    def test_punctuation_only_yields_empty_string(self):
        self.assertEqual(jokes.normalize("!!!"), "")


class SplitSenderTest(unittest.TestCase):
    def test_callsign_prefix_is_split_off(self):
        self.assertEqual(jokes.split_sender("KF8DDU: joke cat"), ("KF8DDU", "joke cat"))

    def test_multiword_node_name_is_split_off(self):
        self.assertEqual(
            jokes.split_sender("Marion Jokes Bot: hello"), ("Marion Jokes Bot", "hello")
        )

    def test_message_without_a_prefix_is_returned_whole(self):
        self.assertEqual(jokes.split_sender("joke cat"), ("", "joke cat"))

    def test_only_the_first_separator_splits(self):
        self.assertEqual(
            jokes.split_sender("KF8DDU: note: read this"), ("KF8DDU", "note: read this")
        )

    def test_empty_text_is_survivable(self):
        self.assertEqual(jokes.split_sender(""), ("", ""))

    def test_none_is_survivable(self):
        self.assertEqual(jokes.split_sender(None), ("", ""))


class MatchTriggerTest(unittest.TestCase):
    TRIGGERS = {"joke dustin": "dustin", "joke cat": "cat"}

    def test_exact_phrase_matches(self):
        self.assertEqual(jokes.match_trigger("joke cat", self.TRIGGERS), "cat")

    def test_message_with_a_callsign_prefix_matches(self):
        # This is the shape that actually arrives over the air.
        self.assertEqual(jokes.match_trigger("KF8DDU: joke cat", self.TRIGGERS), "cat")

    def test_prefixed_message_is_still_case_insensitive(self):
        self.assertEqual(jokes.match_trigger("KF8DDU: JOKE DUSTIN", self.TRIGGERS), "dustin")

    def test_prefixed_message_tolerates_punctuation(self):
        self.assertEqual(jokes.match_trigger("KF8DDU: joke cat!", self.TRIGGERS), "cat")

    def test_matching_is_case_insensitive(self):
        self.assertEqual(jokes.match_trigger("JOKE DUSTIN", self.TRIGGERS), "dustin")

    def test_messy_spacing_still_matches(self):
        self.assertEqual(jokes.match_trigger("  joke   cat  ", self.TRIGGERS), "cat")

    def test_question_mark_still_matches(self):
        self.assertEqual(jokes.match_trigger("joke cat?", self.TRIGGERS), "cat")

    def test_unknown_phrase_does_not_match(self):
        self.assertIsNone(jokes.match_trigger("joke dog", self.TRIGGERS))

    def test_substring_in_a_sentence_does_not_match(self):
        self.assertIsNone(
            jokes.match_trigger("someone should ask for a joke cat later", self.TRIGGERS)
        )

    def test_prefixed_substring_does_not_match(self):
        self.assertIsNone(
            jokes.match_trigger("KF8DDU: you should try joke cat", self.TRIGGERS)
        )

    def test_a_joke_about_the_trigger_does_not_match(self):
        # The reply goes to the same channel the bot listens on, so a joke that
        # merely contains the phrase must not trigger another reply.
        self.assertIsNone(
            jokes.match_trigger("Dustin told me to type joke cat and I did", self.TRIGGERS)
        )

    def test_empty_message_does_not_match(self):
        self.assertIsNone(jokes.match_trigger("", self.TRIGGERS))


class FitTest(unittest.TestCase):
    def test_short_message_is_untouched(self):
        self.assertEqual(jokes.fit("a short joke"), "a short joke")

    def test_long_message_is_trimmed_to_the_limit(self):
        trimmed = jokes.fit("word " * 100)
        self.assertLessEqual(len(trimmed), jokes.MAX_MESSAGE_CHARS)

    def test_trimming_prefers_a_word_boundary(self):
        trimmed = jokes.fit("word " * 100)
        self.assertFalse(trimmed.endswith("wor"))

    def test_a_single_long_run_is_still_capped(self):
        trimmed = jokes.fit("x" * 400)
        self.assertEqual(len(trimmed), jokes.MAX_MESSAGE_CHARS)


class JokeBookTest(TempFileMixin, unittest.TestCase):
    def _book(self, payload=None):
        payload = payload or {"cat": ["c1", "c2", "c3"], "dustin": ["d1", "d2"]}
        return jokes.JokeBook(self._write_json(payload), self._temp_path())

    def test_jokes_come_out_in_order(self):
        book = self._book()
        self.assertEqual(
            [book.next_joke("cat") for _ in range(3)], ["c1", "c2", "c3"]
        )

    def test_rotation_wraps_to_the_start(self):
        book = self._book()
        self.assertEqual(
            [book.next_joke("cat") for _ in range(4)], ["c1", "c2", "c3", "c1"]
        )

    def test_lists_advance_independently(self):
        book = self._book()
        book.next_joke("cat")
        book.next_joke("cat")
        self.assertEqual(book.next_joke("dustin"), "d1")

    def test_position_survives_a_restart(self):
        jokes_path = self._write_json({"cat": ["c1", "c2", "c3"]})
        state_path = self._temp_path()

        first = jokes.JokeBook(jokes_path, state_path)
        first.next_joke("cat")
        first.next_joke("cat")

        second = jokes.JokeBook(jokes_path, state_path)
        self.assertEqual(second.next_joke("cat"), "c3")

    def test_peek_does_not_advance(self):
        book = self._book()
        self.assertEqual(book.peek("cat"), "c1")
        self.assertEqual(book.peek("cat"), "c1")
        self.assertEqual(book.next_joke("cat"), "c1")

    def test_unknown_list_yields_nothing(self):
        self.assertIsNone(self._book().next_joke("nope"))

    def test_replies_are_trimmed_to_the_limit(self):
        book = self._book({"cat": ["x" * 400]})
        self.assertEqual(len(book.next_joke("cat")), jokes.MAX_MESSAGE_CHARS)

    def test_corrupt_state_falls_back_to_the_start(self):
        jokes_path = self._write_json({"cat": ["c1", "c2"]})
        state_path = self._temp_path()
        with open(state_path, "w", encoding="utf-8") as handle:
            handle.write("{ not json")
        self.assertEqual(jokes.JokeBook(jokes_path, state_path).next_joke("cat"), "c1")

    def test_out_of_range_state_is_wrapped(self):
        # A stale position from a since-shortened list must land back in range
        # rather than raise: 99 against a two-joke list wraps to index 1.
        jokes_path = self._write_json({"cat": ["c1", "c2"]})
        state_path = self._write_json({"cat": 99})
        self.assertEqual(jokes.JokeBook(jokes_path, state_path).next_joke("cat"), "c2")

    def test_lists_are_reported(self):
        self.assertEqual(self._book().lists(), ["cat", "dustin"])


class LoadJokesTest(TempFileMixin, unittest.TestCase):
    def test_empty_object_is_rejected(self):
        with self.assertRaises(ValueError):
            jokes.load_jokes(self._write_json({}))

    def test_empty_list_is_rejected(self):
        with self.assertRaises(ValueError):
            jokes.load_jokes(self._write_json({"cat": []}))

    def test_non_string_joke_is_rejected(self):
        with self.assertRaises(ValueError):
            jokes.load_jokes(self._write_json({"cat": [42]}))

    def test_blank_joke_is_rejected(self):
        with self.assertRaises(ValueError):
            jokes.load_jokes(self._write_json({"cat": ["   "]}))


class LoadConfigTest(TempFileMixin, unittest.TestCase):
    BASE = {
        "host": "127.0.0.1",
        "port": 5055,
        "channel_index": 0,
        "triggers": {"joke cat": "cat"},
    }

    def test_defaults_are_applied(self):
        cfg = jokes.load_config(self._write_json(dict(self.BASE)))
        self.assertEqual(cfg["cooldown_secs"], 60)
        self.assertTrue(cfg["jokes_path"].endswith("jokes.json"))
        self.assertTrue(cfg["state_path"].endswith("state.json"))

    def test_missing_required_key_is_rejected(self):
        payload = dict(self.BASE)
        del payload["port"]
        with self.assertRaises(ValueError):
            jokes.load_config(self._write_json(payload))

    def test_empty_triggers_is_rejected(self):
        payload = dict(self.BASE, triggers={})
        with self.assertRaises(ValueError):
            jokes.load_config(self._write_json(payload))

    def test_trigger_mapping_to_a_blank_name_is_rejected(self):
        payload = dict(self.BASE, triggers={"joke cat": "  "})
        with self.assertRaises(ValueError):
            jokes.load_config(self._write_json(payload))

    def test_negative_cooldown_is_rejected(self):
        payload = dict(self.BASE, cooldown_secs=-1)
        with self.assertRaises(ValueError):
            jokes.load_config(self._write_json(payload))

    def test_reply_overhead_defaults_to_zero(self):
        cfg = jokes.load_config(self._write_json(dict(self.BASE)))
        self.assertEqual(cfg["reply_overhead_chars"], 0)
        self.assertEqual(cfg["ignore_senders"], [])

    def test_negative_reply_overhead_is_rejected(self):
        payload = dict(self.BASE, reply_overhead_chars=-1)
        with self.assertRaises(ValueError):
            jokes.load_config(self._write_json(payload))

    def test_reply_overhead_swallowing_the_whole_limit_is_rejected(self):
        payload = dict(self.BASE, reply_overhead_chars=jokes.MAX_MESSAGE_CHARS)
        with self.assertRaises(ValueError):
            jokes.load_config(self._write_json(payload))

    def test_non_list_ignore_senders_is_rejected(self):
        payload = dict(self.BASE, ignore_senders="Marion Jokes Bot")
        with self.assertRaises(ValueError):
            jokes.load_config(self._write_json(payload))


class CooldownTest(unittest.TestCase):
    def setUp(self):
        self.now = 1000.0

    def _clock(self):
        return self.now

    def test_first_request_is_allowed(self):
        self.assertTrue(jokes.Cooldown(60, self._clock).ready("cat"))

    def test_second_request_is_blocked(self):
        cooldown = jokes.Cooldown(60, self._clock)
        cooldown.mark("cat")
        self.assertFalse(cooldown.ready("cat"))

    def test_request_is_allowed_again_after_the_window(self):
        cooldown = jokes.Cooldown(60, self._clock)
        cooldown.mark("cat")
        self.now += 61
        self.assertTrue(cooldown.ready("cat"))

    def test_lists_cool_down_independently(self):
        cooldown = jokes.Cooldown(60, self._clock)
        cooldown.mark("cat")
        self.assertTrue(cooldown.ready("dustin"))

    def test_zero_disables_the_limit(self):
        cooldown = jokes.Cooldown(0, self._clock)
        cooldown.mark("cat")
        self.assertTrue(cooldown.ready("cat"))


class HandlerTest(TempFileMixin, unittest.TestCase):
    def setUp(self):
        self.cfg = {
            "channel_index": 0,
            "triggers": {"joke dustin": "dustin", "joke cat": "cat"},
            "ignore_senders": ["Marion Jokes Bot"],
            "reply_overhead_chars": 0,
        }
        self.book = jokes.JokeBook(
            self._write_json({"cat": ["c1", "c2"], "dustin": ["d1", "d2"]}),
            self._temp_path(),
        )
        self.sender = RecordingSender()

    def _handler(self, cooldown_secs=0):
        return jokes.make_handler(
            self.cfg, self.book, jokes.Cooldown(cooldown_secs), self.sender
        )

    def test_trigger_produces_one_reply(self):
        asyncio.run(self._handler()(FakeEvent("joke cat")))
        self.assertEqual(self.sender.messages, ["c1"])

    def test_over_the_air_format_produces_a_reply(self):
        # The exact payload shape captured from a live request.
        asyncio.run(self._handler()(FakeEvent("KF8DDU: joke cat")))
        self.assertEqual(self.sender.messages, ["c1"])

    def test_unrelated_chatter_is_ignored(self):
        asyncio.run(self._handler()(FakeEvent("KF8DDU: good morning everyone")))
        self.assertEqual(self.sender.messages, [])

    def test_the_bot_ignores_its_own_traffic(self):
        asyncio.run(self._handler()(FakeEvent("Marion Jokes Bot: joke cat")))
        self.assertEqual(self.sender.messages, [])

    def test_self_ignore_is_case_insensitive(self):
        asyncio.run(self._handler()(FakeEvent("marion jokes bot: joke cat")))
        self.assertEqual(self.sender.messages, [])

    def test_repeat_requests_advance_the_rotation(self):
        handler = self._handler()
        asyncio.run(handler(FakeEvent("KF8DDU: joke cat")))
        asyncio.run(handler(FakeEvent("KF8DDU: joke cat")))
        self.assertEqual(self.sender.messages, ["c1", "c2"])

    def test_each_trigger_uses_its_own_list(self):
        handler = self._handler()
        asyncio.run(handler(FakeEvent("KF8DDU: joke dustin")))
        asyncio.run(handler(FakeEvent("KF8DDU: joke cat")))
        self.assertEqual(self.sender.messages, ["d1", "c1"])

    def test_cooldown_suppresses_a_rapid_repeat(self):
        handler = self._handler(cooldown_secs=60)
        asyncio.run(handler(FakeEvent("joke cat")))
        asyncio.run(handler(FakeEvent("joke cat")))
        self.assertEqual(self.sender.messages, ["c1"])

    def test_a_suppressed_request_does_not_burn_a_joke(self):
        handler = self._handler(cooldown_secs=60)
        asyncio.run(handler(FakeEvent("joke cat")))
        asyncio.run(handler(FakeEvent("joke cat")))
        self.assertEqual(self.book.peek("cat"), "c2")

    def test_reply_overhead_shrinks_the_usable_limit(self):
        self.cfg["reply_overhead_chars"] = 18
        self.book = jokes.JokeBook(
            self._write_json({"cat": ["x" * 400]}), self._temp_path()
        )
        asyncio.run(self._handler()(FakeEvent("joke cat")))
        self.assertEqual(
            len(self.sender.messages[0]), jokes.MAX_MESSAGE_CHARS - 18
        )

    def test_a_payloadless_event_is_survivable(self):
        asyncio.run(self._handler()(FakeEvent(None)))
        self.assertEqual(self.sender.messages, [])


class ShippedContentTest(unittest.TestCase):
    """The jokes.json that actually ships has to be airworthy."""

    @classmethod
    def setUpClass(cls):
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "jokes.json")
        cls.book = jokes.load_jokes(path)

    def test_both_lists_are_present(self):
        self.assertEqual(sorted(self.book), ["cat", "dustin"])

    def test_each_list_has_at_least_thirty_jokes(self):
        for name, entries in self.book.items():
            self.assertGreaterEqual(len(entries), 30, "%s is short" % name)

    def test_every_joke_fits_in_one_transmission(self):
        for name, entries in self.book.items():
            for joke in entries:
                self.assertLessEqual(
                    len(joke), jokes.MAX_MESSAGE_CHARS, "%s: %s" % (name, joke)
                )

    def test_no_duplicates_within_a_list(self):
        for name, entries in self.book.items():
            self.assertEqual(len(entries), len(set(entries)), "%s has repeats" % name)

    def test_no_joke_would_trigger_the_bot(self):
        # A reply that matches a trigger would loop on the shared channel.
        triggers = {"joke dustin": "dustin", "joke cat": "cat"}
        for name, entries in self.book.items():
            for joke in entries:
                self.assertIsNone(
                    jokes.match_trigger(joke, triggers), "%s: %s" % (name, joke)
                )


if __name__ == "__main__":
    unittest.main()
