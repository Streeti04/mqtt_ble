import json
import tempfile
import unittest
from pathlib import Path

from learn_mode import (
    DECISION_ALLOW,
    DECISION_IGNORE,
    DECISION_LEARN,
    Allowlist,
    LearnWindow,
    OnceLogger,
    decide,
    parse_switch_payload,
)


class FakeClock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


class LearnWindowTests(unittest.TestCase):
    def test_starts_off(self):
        window = LearnWindow(600, clock=FakeClock())
        self.assertFalse(window.is_active())
        self.assertEqual(window.remaining(), 0.0)
        self.assertFalse(window.poll_expired())

    def test_default_is_ten_minutes(self):
        self.assertEqual(LearnWindow().duration, 600)

    def test_enable_then_expires_once(self):
        clock = FakeClock()
        window = LearnWindow(600, clock=clock)
        window.enable()
        self.assertTrue(window.is_active())
        clock.now += 599
        self.assertTrue(window.is_active())
        self.assertFalse(window.poll_expired())
        clock.now += 1
        self.assertFalse(window.is_active())
        self.assertTrue(window.poll_expired())
        self.assertFalse(window.poll_expired())  # nur einmal melden

    def test_disable_reports_previous_state(self):
        window = LearnWindow(600, clock=FakeClock())
        self.assertFalse(window.disable())
        window.enable()
        self.assertTrue(window.disable())
        self.assertFalse(window.is_active())
        self.assertFalse(window.poll_expired())

    def test_reenable_extends(self):
        clock = FakeClock()
        window = LearnWindow(600, clock=clock)
        window.enable()
        clock.now += 500
        window.enable()
        clock.now += 500
        self.assertTrue(window.is_active())
        self.assertAlmostEqual(window.remaining(), 100)

    def test_invalid_duration(self):
        with self.assertRaises(ValueError):
            LearnWindow(0)


class DecideTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.allowlist = Allowlist.load(Path(self.tmp.name) / "known_devices.json")
        self.allowlist.add("govee_ble_light_known", source="initial")

    def tearDown(self):
        self.tmp.cleanup()

    def test_known_device_allowed_without_learning(self):
        self.assertEqual(decide("govee_ble_light_known", self.allowlist, False), DECISION_ALLOW)

    def test_known_device_allowed_while_learning(self):
        self.assertEqual(decide("govee_ble_light_known", self.allowlist, True), DECISION_ALLOW)

    def test_unknown_device_ignored_without_learning(self):
        self.assertEqual(decide("govee_ble_light_neighbor", self.allowlist, False), DECISION_IGNORE)
        self.assertFalse(self.allowlist.contains("govee_ble_light_neighbor"))

    def test_unknown_device_learned_while_learning(self):
        self.assertEqual(decide("govee_new", self.allowlist, True), DECISION_LEARN)


class AllowlistPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "state" / "known_devices.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_missing_file_is_empty(self):
        allowlist = Allowlist.load(self.path)
        self.assertEqual(len(allowlist), 0)
        self.assertFalse(self.path.exists())

    def test_add_persists_across_reload(self):
        allowlist = Allowlist.load(self.path)
        self.assertTrue(allowlist.add("govee_abc", name="GVH5075_ABCD", source="learn"))
        self.assertFalse(allowlist.add("govee_abc"))  # doppelt -> False
        reloaded = Allowlist.load(self.path)
        self.assertTrue(reloaded.contains("govee_abc"))
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(data["version"], 1)
        self.assertEqual(data["devices"]["govee_abc"]["name"], "GVH5075_ABCD")
        self.assertEqual(data["devices"]["govee_abc"]["source"], "learn")
        leftovers = [p for p in self.path.parent.iterdir() if p.name.endswith(".tmp")]
        self.assertEqual(leftovers, [])

    def test_accepts_plain_list_format(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps({"devices": ["a", "b"]}), encoding="utf-8")
        self.assertEqual(Allowlist.load(self.path).ids(), ["a", "b"])

    def test_corrupt_file_raises_and_is_not_overwritten(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{kaputt", encoding="utf-8")
        with self.assertRaises(ValueError):
            Allowlist.load(self.path)
        self.assertEqual(self.path.read_text(encoding="utf-8"), "{kaputt")


class SwitchPayloadTests(unittest.TestCase):
    def test_payloads(self):
        self.assertIs(parse_switch_payload(b"ON"), True)
        self.assertIs(parse_switch_payload("off"), False)
        self.assertIs(parse_switch_payload(b" On \n"), True)
        self.assertIsNone(parse_switch_payload(b"toggle"))
        self.assertIsNone(parse_switch_payload(b"\xff"))


class OnceLoggerTests(unittest.TestCase):
    def test_logs_once(self):
        log = OnceLogger()
        self.assertTrue(log.first_time("x"))
        self.assertFalse(log.first_time("x"))
        log.forget("x")
        self.assertTrue(log.first_time("x"))

    def test_bounded(self):
        log = OnceLogger(max_entries=2)
        log.first_time("a")
        log.first_time("b")
        log.first_time("c")  # leert und startet neu
        self.assertTrue(log.first_time("a"))


if __name__ == "__main__":
    unittest.main()
