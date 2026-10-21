import unittest
import json
import tempfile
import threading
import time
import io
from contextlib import ExitStack, redirect_stdout
from unittest.mock import Mock, patch
from pathlib import Path

import pandas as pd

import webnaprenajom_sending_bot_SK as bot


class ParallelSendingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.runs = []
        for key, start in (("maros", 65980), ("matus", 2)):
            state_path = str(self.root / f"{key}.json")
            bot.save_state({"start_index": start}, state_path)
            self.runs.append({
                "key": key, "profile": {"from_addr": f"{key}@sender.sk", "from_name": key.title(),
                                        "reply_to": f"{key}@sender.sk"},
                "state_file": state_path, "log_csv": str(self.root / f"{key}.csv"),
                "delay": 3600 / 190,
            })
        self.df = pd.DataFrame({
            "domain": ["a.sk", "b.sk", "c.sk", "d.sk"],
            "emails": ["info@a.sk", "info@b.sk", "info@c.sk", "info@d.sk"],
        }, index=range(65980, 65984))
        self.shared = str(self.root / "parallel.json")
        self.owners = patch.object(bot, "DOMAIN_OWNER_FILE", str(self.root / "owners.json"))
        self.owner_lock = patch.object(bot, "DOMAIN_OWNER_LOCK_FILE", str(self.root / "owners.lock"))
        self.owners.start()
        self.owner_lock.start()
        self.addCleanup(self.owners.stop)
        self.addCleanup(self.owner_lock.stop)
        for name in ("WNP_LOG_CSV_FILE", "LOG_CSV_FILE", "MATUS_AI_LOG_CSV_FILE"):
            mock = patch.object(bot, name, str(self.root / name))
            mock.start()
            self.addCleanup(mock.stop)

    @staticmethod
    def template(row):
        return "subject", "text", None

    def coordinator(self):
        return bot.ParallelCoordinator(self.df, self.runs, self.shared, self.runs[0]["state_file"])

    def test_alternating_rows_start_from_maros_and_preserve_pending_frontier(self):
        queue = self.coordinator()
        first = queue.take("maros", self.template)
        second = queue.take("matus", self.template)
        self.assertEqual((first[0], second[0]), (65980, 65981))
        queue.complete(65981)
        self.assertEqual(bot.load_state(self.shared)["start_index"], 65980)
        queue.complete(65980)
        self.assertEqual(bot.load_state(self.shared)["start_index"], 65982)
        self.assertEqual(queue.take("maros", self.template)[0], 65982)
        self.assertEqual(queue.take("matus", self.template)[0], 65983)

    def test_restart_reclaims_unfinished_lower_row(self):
        queue = self.coordinator()
        queue.take("maros", self.template)
        queue.take("matus", self.template)
        queue.complete(65981)
        restarted = self.coordinator()
        self.assertEqual(restarted.take("maros", self.template)[0], 65980)

    def test_matus_alone_reclaims_both_unsent_parallel_reservations(self):
        queue = self.coordinator()
        queue.take("maros", self.template)
        queue.take("matus", self.template)
        resumed = bot.ParallelCoordinator(self.df, [self.runs[1]], self.shared, self.runs[0]["state_file"])
        self.assertEqual(resumed.take("matus", self.template)[0], 65980)
        self.assertEqual(resumed.take("matus", self.template)[0], 65981)
        owners = bot._load_domain_owners(bot.DOMAIN_OWNER_FILE)
        self.assertEqual(owners["a.sk"], "matus@sender.sk")
        self.assertEqual(owners["b.sk"], "matus@sender.sk")

    def test_maros_alone_reclaims_matus_unsent_reservation(self):
        queue = self.coordinator()
        queue.take("maros", self.template)
        queue.take("matus", self.template)
        resumed = bot.ParallelCoordinator(self.df, [self.runs[0]], self.shared, self.runs[0]["state_file"])
        self.assertEqual(resumed.take("maros", self.template)[0], 65980)
        self.assertEqual(resumed.take("maros", self.template)[0], 65981)
        self.assertEqual(bot._load_domain_owners(bot.DOMAIN_OWNER_FILE)["b.sk"], "maros@sender.sk")

    def test_matus_alone_cannot_reclaim_ambiguous_maros_smtp_send(self):
        queue = self.coordinator()
        queue.take("maros", self.template)
        queue.mark_sending(65980)
        with self.assertRaisesRegex(ValueError, "nejasný výsledok SMTP"):
            bot.ParallelCoordinator(self.df, [self.runs[1]], self.shared, self.runs[0]["state_file"])
        self.assertEqual(bot._load_domain_owners(bot.DOMAIN_OWNER_FILE)["a.sk"], "maros@sender.sk")

    def test_reassignment_stops_if_owner_changes_after_recovery_check(self):
        queue = self.coordinator()
        queue.take("maros", self.template)
        resumed = bot.ParallelCoordinator(self.df, [self.runs[1]], self.shared, self.runs[0]["state_file"])
        owners = bot._load_domain_owners(bot.DOMAIN_OWNER_FILE)
        owners["a.sk"] = "different@sender.sk"
        bot._save_domain_owners(owners, bot.DOMAIN_OWNER_FILE)
        bot._update_domain_owner_cache(bot.DOMAIN_OWNER_FILE, owners)
        with self.assertRaisesRegex(ValueError, "Vlastníctvo domény"):
            resumed.take("matus", self.template)
        self.assertEqual(bot.load_state(self.shared)["start_index"], 65980)
        self.assertEqual(bot._load_domain_owners(bot.DOMAIN_OWNER_FILE)["a.sk"], "different@sender.sk")

    def test_logged_maros_reservation_is_skipped_and_never_transferred(self):
        queue = self.coordinator()
        queue.take("maros", self.template)
        queue.mark_sending(65980)
        pd.DataFrame([{"domain": "a.sk", "email_address": "info@a.sk", "status": "OK"}]).to_csv(
            self.runs[0]["log_csv"], index=False)
        with patch.object(bot, "WNP_LOG_CSV_FILE", self.runs[0]["log_csv"]):
            resumed = bot.ParallelCoordinator(self.df, [self.runs[1]], self.shared, self.runs[0]["state_file"])
            self.assertEqual(resumed.take("matus", self.template)[0], 65981)
        self.assertEqual(bot._load_domain_owners(bot.DOMAIN_OWNER_FILE)["a.sk"], "maros@sender.sk")

    def test_logs_block_duplicate_domains_and_contacts_across_senders(self):
        pd.DataFrame([{"domain": "WWW.A.SK", "email_address": "info@other.sk", "status": "OK"}]).to_csv(
            self.runs[1]["log_csv"], index=False
        )
        self.df.loc[65981, "emails"] = "INFO@OTHER.SK"
        queue = self.coordinator()
        self.assertEqual(queue.take("maros", self.template)[0], 65982)
        self.assertEqual(queue.take("matus", self.template)[0], 65983)

    def test_duplicate_domain_is_reserved_before_either_send_finishes(self):
        self.df.loc[65981, "domain"] = "WWW.A.SK"
        queue = self.coordinator()
        self.assertEqual(queue.take("maros", self.template)[0], 65980)
        self.assertEqual(queue.take("matus", self.template)[0], 65982)

    def test_stopped_sender_does_not_block_remaining_worker(self):
        queue = self.coordinator()
        queue.take("maros", self.template)
        queue.deactivate("matus")
        self.assertEqual(queue.take("maros", self.template)[0], 65981)

    def test_restart_preserves_lower_pending_row_owned_by_matus(self):
        queue = self.coordinator()
        queue.take("maros", self.template)
        queue.take("matus", self.template)
        queue.complete(65980)
        recovered = self.coordinator()
        results = {}
        thread = threading.Thread(target=lambda: results.update(maros=recovered.take("maros", self.template)))
        thread.start()
        # Matúš prevezme svoj nedokončený riadok; Maroš ho nesmie preskočiť.
        results["matus"] = recovered.take("matus", self.template)
        thread.join(timeout=3)
        if thread.is_alive():
            recovered.stop()
            thread.join()
            self.fail("Ownership recovery deadlocked")
        self.assertEqual(results["matus"][0], 65981)
        self.assertEqual(results["maros"][0], 65982)

    def test_corrupt_shared_state_fails_closed(self):
        Path(self.shared).write_text("{broken", encoding="utf-8")
        with self.assertRaises((ValueError, json.JSONDecodeError)):
            self.coordinator()

    def test_pacer_spaces_all_attempts_and_enforces_account_interval(self):
        now = [0.0]
        starts = []
        def sleep(seconds):
            now[0] += seconds
        pacer = bot.SendPacer(8, {"maros": 3600 / 190, "matus": 3600 / 190})
        for idx in range(3):
            pacer.register(idx)
        with patch.object(bot.time, "monotonic", side_effect=lambda: now[0]), patch.object(pacer.stop_event, "wait", side_effect=sleep):
            pacer.send(0, "maros", lambda: starts.append(now[0]))
            pacer.send(1, "matus", lambda: starts.append(now[0]))
            pacer.send(2, "maros", lambda: starts.append(now[0]))
            pacer.send(2, "maros", lambda: starts.append(now[0]))  # SMTP retry
        self.assertGreaterEqual(starts[1] - starts[0], 8)
        self.assertGreaterEqual(starts[2] - starts[0], 3600 / 190)
        self.assertGreaterEqual(starts[3] - starts[2], 3600 / 190)

    def test_pacer_interval_counts_from_attempt_start_not_end(self):
        now = [0.0]
        starts = []
        def sleep(seconds):
            now[0] += seconds
        def slow_send():
            starts.append(now[0])
            now[0] += 5  # pomalé SMTP
        pacer = bot.SendPacer(8, {"maros": 0})
        for idx in range(2):
            pacer.register(idx)
        with patch.object(bot.time, "monotonic", side_effect=lambda: now[0]), patch.object(pacer.stop_event, "wait", side_effect=sleep):
            pacer.send(0, "maros", slow_send)
            pacer.send(1, "maros", slow_send)
        self.assertAlmostEqual(starts[1] - starts[0], 8)

    def test_dns_failure_is_retried_and_marked_transient(self):
        smtp_cls = Mock(side_effect=bot.socket.gaierror(11001, "getaddrinfo failed"))
        sender = dict(self.runs[0]["profile"], smtp_host="test", smtp_port=465,
            imap_host="test", imap_port=993, email_user="test", email_pass="test")
        with patch.object(bot.smtplib, "SMTP_SSL", smtp_cls):
            result = bot._smtp_send_one(sender, "info@recipient.sk", "test", "text")
        self.assertEqual(result["status"], "FAILED")
        self.assertTrue(result["transient"])
        self.assertEqual(smtp_cls.call_count, 3)

    def test_crash_after_smtp_started_requires_sent_reconciliation(self):
        queue = self.coordinator()
        queue.take("maros", self.template)
        queue.mark_sending(65980)
        with self.assertRaisesRegex(ValueError, "nejasný výsledok SMTP"):
            self.coordinator()
        pd.DataFrame([{"domain": "a.sk", "email_address": "info@a.sk", "status": "OK"}]).to_csv(
            self.runs[0]["log_csv"], index=False
        )
        recovered = self.coordinator()
        self.assertEqual(recovered.take("maros", self.template)[0], 65981)

    def test_two_real_threads_write_separate_logs_and_shared_progress(self):
        queue = self.coordinator()
        sent = []
        errors = []
        def smtp(sender, recipient, *args):
            sent.append((sender["key"], recipient))
            return {"status": "OK", "error_msg": "", "sent_at": "test", "delivery_ok": True, "message_id": "test"}
        def worker(run):
            try:
                bot.send_loop(self.df, self.template, dict(run["profile"], key=run["key"]),
                    run["state_file"], run["log_csv"], "unused.html", 0,
                    session_limit=2, coordinator=queue)
            except BaseException as error:
                errors.append(error)
        with patch.object(bot, "_smtp_send_one", side_effect=smtp):
            threads = [threading.Thread(target=worker, args=(r,)) for r in self.runs]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=5)
            if any(t.is_alive() for t in threads):
                queue.stop()
                self.fail("Workers deadlocked")
        self.assertEqual(errors, [])
        self.assertEqual(len(sent), 4)
        self.assertEqual(len({recipient for _, recipient in sent}), 4)
        self.assertEqual(bot.load_state(self.shared)["start_index"], 65984)
        for run in self.runs:
            self.assertEqual(len(pd.read_csv(run["log_csv"])), 2)

    def test_actual_smtp_boundary_has_eight_second_gap_and_parallel_preparation(self):
        ready = threading.Barrier(2)
        starts = []
        results = []
        class Smtp:
            def __init__(self, *args, **kwargs):
                pass
            def login(self, *args):
                pass
            def sendmail(self, sender, *args):
                starts.append((sender, time.monotonic()))
            def quit(self):
                pass
        class Imap:
            def __init__(self, *args, **kwargs):
                pass
            def login(self, *args):
                pass
            def append(self, *args):
                pass
            def logout(self):
                pass
        pacer = bot.SendPacer(8, {"maros": 0, "matus": 0})
        profiles = []
        for index, run in enumerate(self.runs):
            pacer.register(index)
            profiles.append(dict(run["profile"], key=run["key"], from_name=run["key"],
                reply_to=run["profile"]["from_addr"], smtp_host="test", smtp_port=465,
                imap_host="test", imap_port=993, email_user="test", email_pass="test",
                imap_sent_folder="Sent", _send_pacer=pacer, _send_index=index))
        original_create = bot.create_mime_message
        def create(*args):
            ready.wait(timeout=3)  # obaja pripravujú správu súčasne, pred časovým slotom
            return original_create(*args)
        with patch.object(bot.smtplib, "SMTP_SSL", Smtp), patch.object(bot.imaplib, "IMAP4_SSL", Imap), \
             patch.object(bot, "create_mime_message", side_effect=create):
            threads = [threading.Thread(target=lambda p=p: results.append(
                bot._smtp_send_one(p, "info@recipient.sk", "test", "text"))) for p in profiles]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=12)
            if any(t.is_alive() for t in threads):
                pacer.stop()
                for thread in threads:
                    thread.join()
                self.fail("SMTP pacing deadlocked")
        self.assertEqual([result["status"] for result in results], ["OK", "OK"])
        self.assertEqual([sender for sender, _ in starts], ["maros@sender.sk", "matus@sender.sk"])
        self.assertGreaterEqual(starts[1][1] - starts[0][1], 8)

    def test_smtp_connection_is_not_open_during_twenty_second_pacing_wait(self):
        now = [0]
        events = []
        class IdleSmtp:
            def __init__(self, *args, **kwargs):
                self.opened = now[0]
                events.append("connect")
            def login(self, *args):
                pass
            def sendmail(self, sender, *args):
                if now[0] - self.opened > 15:
                    raise bot.smtplib.SMTPSenderRefused(421, b"4.4.2 Error: timeout exceeded", sender)
                events.append("send")
            def quit(self):
                pass
            def logout(self):
                pass
        class Pacer:
            def send(self, index, key, action):
                events.append("wait")
                now[0] += 20
                return action()
        sender = dict(self.runs[1]["profile"], key="matus", from_name="matus",
            reply_to="matus@sender.sk", smtp_host="test", smtp_port=465,
            imap_host="test", imap_port=993, email_user="test", email_pass="test",
            imap_sent_folder="Sent", _send_pacer=Pacer(), _send_index=0)
        with patch.object(bot.smtplib, "SMTP_SSL", IdleSmtp), patch.object(bot.imaplib, "IMAP4_SSL"):
            result = bot._smtp_send_one(sender, "info@recipient.sk", "test", "text")
        self.assertEqual(result["status"], "OK")
        self.assertEqual(events, ["wait", "connect", "send"])

    def test_stop_interrupts_long_pacing_wait(self):
        pacer = bot.SendPacer(60, {"maros": 60})
        pacer.register(0)
        pacer.send(0, "maros", lambda: None)
        pacer.register(1)
        waiting = threading.Event()
        actual_wait = pacer.stop_event.wait
        interrupted = []
        def wait(seconds):
            waiting.set()
            return actual_wait(seconds)
        def worker():
            try:
                pacer.send(1, "maros", lambda: self.fail("Stopped send executed"))
            except KeyboardInterrupt:
                interrupted.append(True)
        with patch.object(pacer.stop_event, "wait", side_effect=wait):
            thread = threading.Thread(target=worker)
            thread.start()
            self.assertTrue(waiting.wait(timeout=2))
            pacer.stop()
            thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(interrupted, [True])

    def test_ambiguous_smtp_disconnect_is_not_retried(self):
        smtp = Mock()
        smtp.sendmail.side_effect = bot.smtplib.SMTPServerDisconnected("connection lost")
        sender = dict(self.runs[0]["profile"], smtp_host="test", smtp_port=465,
            imap_host="test", imap_port=993, email_user="test", email_pass="test")
        message = Mock()
        message.as_string.return_value = "test message"
        with patch.object(bot.smtplib, "SMTP_SSL", return_value=smtp), \
             patch.object(bot.imaplib, "IMAP4_SSL"), \
             patch.object(bot, "create_mime_message", return_value=message), \
             patch.object(bot, "validate_outgoing_message"):
            result = bot._smtp_send_one(sender, "info@recipient.sk", "test", "text")
        self.assertEqual(result["status"], "FAILED")
        self.assertIn("bez automatického opakovania", result["error_msg"])
        self.assertEqual(smtp.sendmail.call_count, 1)

    def test_smtp_error_includes_phase_exception_and_response_code(self):
        smtp = Mock()
        smtp.sendmail.side_effect = bot.smtplib.SMTPSenderRefused(421, b"4.4.2 timeout exceeded", "matus@sender.sk")
        sender = dict(self.runs[1]["profile"], smtp_host="test", smtp_port=465,
            imap_host="test", imap_port=993, email_user="test", email_pass="test")
        message = Mock()
        message.as_string.return_value = "test message"
        with patch.object(bot.smtplib, "SMTP_SSL", return_value=smtp), \
             patch.object(bot.imaplib, "IMAP4_SSL") as imap, \
             patch.object(bot, "create_mime_message", return_value=message), \
             patch.object(bot, "validate_outgoing_message"):
            result = bot._smtp_send_one(sender, "info@recipient.sk", "test", "text")
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["error_msg"], "smtp_send: SMTPSenderRefused SMTP 421: 4.4.2 timeout exceeded")
        imap.assert_not_called()

    def test_imap_failure_after_smtp_success_is_still_marked_sent(self):
        smtp = Mock()
        sender = dict(self.runs[0]["profile"], smtp_host="test", smtp_port=465,
            imap_host="test", imap_port=993, email_user="test", email_pass="test")
        message = Mock()
        message.as_string.return_value = "test message"
        with patch.object(bot.smtplib, "SMTP_SSL", return_value=smtp), \
             patch.object(bot.imaplib, "IMAP4_SSL", side_effect=TimeoutError("test timeout")), \
             patch.object(bot, "create_mime_message", return_value=message), \
             patch.object(bot, "validate_outgoing_message"):
            result = bot._smtp_send_one(sender, "info@recipient.sk", "test", "text")
        self.assertEqual(result["status"], "OK")
        self.assertFalse(result["delivery_ok"])
        self.assertIn("Sent but not saved: imap_connect: TimeoutError", result["error_msg"])
        self.assertEqual(smtp.sendmail.call_count, 1)

    def test_full_account_window_releases_turn_without_reserving_a_row(self):
        bot.save_state({"start_index": 65980, "sent_count_window": bot.WINDOW_LIMIT,
            "window_start": bot.datetime.now(bot.timezone.utc).isoformat()}, self.runs[0]["state_file"])
        queue = self.coordinator()
        with patch.object(bot, "_smtp_send_one") as smtp:
            bot.send_loop(self.df, self.template, dict(self.runs[0]["profile"], key="maros"),
                self.runs[0]["state_file"], self.runs[0]["log_csv"], "unused.html", 0, coordinator=queue)
        smtp.assert_not_called()
        self.assertEqual(queue.take("matus", self.template)[0], 65980)

    def test_single_process_lock_cannot_be_overridden(self):
        path = str(self.root / "running.lock")
        with patch.object(bot, "LOCK_FILE", path):
            self.assertTrue(bot.acquire_lock())
            self.assertFalse(bot.acquire_lock())
            bot.release_lock()
        self.assertFalse(Path(path).exists())

    def test_cli_golden_path_uses_common_start_and_both_accounts(self):
        for run in self.runs:
            bot.save_state({"start_index": 0}, run["state_file"])
        csv_path = str(self.root / "input.csv")
        self.df.reset_index(drop=True).to_csv(csv_path, index=False)
        profiles = {r["key"]: dict(r["profile"], key=r["key"], from_name=r["key"],
            email_pass="test", emails_per_hour_default=190, emails_per_hour_max=190,
            sender_title="test", sender_phone="test") for r in self.runs}
        def smtp(*args):
            return {"status": "OK", "error_msg": "", "sent_at": "test", "delivery_ok": True, "message_id": "test"}
        with ExitStack() as stack:
            for name, value in {
                "CSV_FILE": csv_path, "WNP_STATE_FILE": self.runs[0]["state_file"],
                "WNP_LOG_CSV_FILE": self.runs[0]["log_csv"],
                "MATUS_AI_STATE_FILE": self.runs[1]["state_file"],
                "MATUS_AI_LOG_CSV_FILE": self.runs[1]["log_csv"],
                "LOCK_FILE": str(self.root / "running.lock"),
            }.items():
                stack.enter_context(patch.object(bot, name, value))
            stack.enter_context(patch.object(bot, "load_sender_profiles", return_value=profiles))
            stack.enter_context(patch.object(bot, "_smtp_send_one", side_effect=smtp))
            stack.enter_context(patch("builtins.input", side_effect=["2", "3", "", "2", "", "2", "n", "n", "a"]))
            output = stack.enter_context(redirect_stdout(io.StringIO()))
            bot.main()
        self.assertIn("Spoločné pokračovanie: riadok 0", output.getvalue())
        self.assertEqual(pd.read_csv(self.runs[0]["log_csv"])["index"].tolist(), [0, 2])
        self.assertEqual(pd.read_csv(self.runs[1]["log_csv"])["index"].tolist(), [1, 3])
        self.assertEqual(bot.load_state(str(self.root / "maros_parallel.json"))["start_index"], 4)
        self.assertFalse((self.root / "running.lock").exists())

    def test_cli_matus_alone_resumes_reserved_rows_from_both_accounts(self):
        for run in self.runs:
            bot.save_state({"start_index": 0}, run["state_file"])
        csv_path = str(self.root / "input.csv")
        rows = self.df.reset_index(drop=True)
        rows.to_csv(csv_path, index=False)
        profiles = {r["key"]: dict(r["profile"], key=r["key"], from_name=r["key"],
            email_pass="test", emails_per_hour_default=190, emails_per_hour_max=190,
            sender_title="test", sender_phone="test") for r in self.runs}
        smtp_result = {"status": "OK", "error_msg": "", "sent_at": "test", "delivery_ok": True, "message_id": "test"}
        shared = str(self.root / "maros_parallel.json")
        with ExitStack() as stack:
            for name, value in {
                "CSV_FILE": csv_path, "WNP_STATE_FILE": self.runs[0]["state_file"],
                "WNP_LOG_CSV_FILE": self.runs[0]["log_csv"],
                "MATUS_AI_STATE_FILE": self.runs[1]["state_file"],
                "MATUS_AI_LOG_CSV_FILE": self.runs[1]["log_csv"],
                "LOCK_FILE": str(self.root / "running.lock"),
            }.items():
                stack.enter_context(patch.object(bot, name, value))
            stack.enter_context(patch.object(bot, "load_sender_profiles", return_value=profiles))
            smtp = stack.enter_context(patch.object(bot, "_smtp_send_one", return_value=smtp_result))
            stack.enter_context(patch("builtins.input", side_effect=["2", "2", "", "4", "n", "a"]))
            output = stack.enter_context(redirect_stdout(io.StringIO()))
            seed = bot.ParallelCoordinator(rows, self.runs, shared, self.runs[0]["state_file"])
            seed.take("maros", self.template)
            seed.take("matus", self.template)
            bot.main()
        self.assertIn("Spoločné pokračovanie: riadok 0", output.getvalue())
        self.assertEqual(pd.read_csv(self.runs[1]["log_csv"])["index"].tolist(), [0, 1, 2, 3])
        self.assertEqual({call.args[0]["key"] for call in smtp.call_args_list}, {"matus"})
        self.assertFalse(Path(self.runs[0]["log_csv"]).exists())
        self.assertEqual(bot.load_state(shared)["start_index"], 4)
        self.assertFalse((self.root / "running.lock").exists())


class CsvMigrationTests(unittest.TestCase):
    def test_position_number_is_preserved_when_csv_changes(self):
        df = pd.DataFrame(
            {
                "domain": ["before.sk", "resume.sk", "after.sk"],
                "merge_old_row_index": [100, 65282, 65283],
            }
        )
        state = {"start_index": 65282, "last_index": 65281}

        migrated, changed = bot.migrate_state_to_csv(
            state, df, "universal_marketing_leads_2026_relevant_only_unified_deduplicated.csv"
        )

        self.assertTrue(changed)
        self.assertEqual(migrated["start_index"], 65282)
        self.assertEqual(migrated["previous_start_index"], 65282)
        self.assertEqual(
            migrated["csv_file"],
            "universal_marketing_leads_2026_relevant_only_unified_deduplicated.csv",
        )

    def test_existing_new_csv_state_is_not_moved_again(self):
        df = pd.DataFrame({"merge_old_row_index": [65282]})
        state = {
            "csv_file": "universal_marketing_leads_2026_relevant_only_unified_deduplicated.csv",
            "start_index": 65282,
        }

        migrated, changed = bot.migrate_state_to_csv(
            state, df, "universal_marketing_leads_2026_relevant_only_unified_deduplicated.csv"
        )

        self.assertFalse(changed)
        self.assertEqual(migrated["start_index"], 65282)


class NewCsvTemplateTests(unittest.TestCase):
    def test_wnp_email_limits_audit_to_three_issues(self):
        sender = {
            "from_name": "Tester",
            "sender_title": "Consultant",
            "sender_phone": "+421 900 000 000",
        }
        template_fn = bot.make_wnp_template_fn(
            "<html>{{ PERSONALIZED_DETAILS_HTML | safe }}</html>", "wnp_html", sender
        )

        _, text_body, html_body = template_fn(
            {"domain": "nova.sk", "selected_issues": "One | Two | Three | Four"}
        )

        self.assertEqual(html_body.count("<li "), 3)
        self.assertIn("- One", text_body)
        self.assertNotIn("- Four", text_body)

    def test_wnp_html_template_has_specific_cta_and_stop_notice(self):
        template = Path("web-na-prenajom-email.html").read_text(encoding="utf-8")

        self.assertIn("Ako funguje web za 35&nbsp;€ mesačne", template)
        self.assertIn("verejne dostupný firemný kontakt", template)
        self.assertIn("textom „STOP“", template)
        self.assertNotIn("AI riešenia", template)
        self.assertEqual(bot.WNP_SUBJECT, "Návrh nového webu pre Vašu firmu")

    def test_c6_email_uses_three_personalized_issues_and_stop_notice(self):
        sender = {
            "from_name": "Tester",
            "sender_title": "Consultant",
            "sender_phone": "+421 900 000 000",
        }
        c6_template = Path("template_c6_collab.html").read_text(encoding="utf-8")
        template_fn = bot.make_wnp_template_fn(
            "<html>WNP</html>", "wnp_html", sender, c6_template
        )

        _, text_body, html_body = template_fn(
            {
                "domain": "nova.sk",
                "campaign": "C6",
                "selected_issues": "One | Two | Three | Four",
            }
        )

        self.assertEqual(html_body.count("<li "), 3)
        self.assertNotIn("Four", text_body)
        self.assertIn("Ako môže web prinášať viac dopytov", html_body)
        self.assertIn("verejne dostupný firemný kontakt", html_body)

    def test_primary_email_uses_new_best_email_and_keeps_remaining_addresses(self):
        primary, other = bot.select_primary_email(
            {
                "emails": "old@example.com, shared@example.com",
                "new_best_email": "best@example.com",
                "new_email_details": "best@example.com|98|homepage_mailto; second@example.com|65|contact_page_mailto",
            }
        )

        self.assertEqual(primary, "best@example.com")
        self.assertEqual(other, ["second@example.com", "old@example.com", "shared@example.com"])

    def test_primary_email_falls_back_to_old_email_column(self):
        primary, other = bot.select_primary_email(
            {"emails": "first@example.com, second@example.com"}
        )

        self.assertEqual(primary, "first@example.com")
        self.assertEqual(other, ["second@example.com"])

    def test_no_email_does_not_create_fake_owner_address(self):
        primary, other = bot.select_primary_email({"domain": "empty.sk", "emails": ""})

        self.assertEqual(primary, "")
        self.assertEqual(other, [])

    def test_dataset_blacklist_is_skipped_and_safe_fallback_is_selected(self):
        primary, other = bot.select_primary_email(
            {
                "new_best_email": "podatelna@obec.sk",
                "new_removed_blacklist": "podatelna@obec.sk [role:urad]",
                "new_email_details": "podatelna@obec.sk|98|homepage; owner@firma.sk|80|contact",
            }
        )

        self.assertEqual(primary, "owner@firma.sk")
        self.assertEqual(other, [])

    def test_public_bank_hosting_and_agency_addresses_are_filtered(self):
        row = {
            "emails": (
                "urad@mestogov.sk, info@vub.sk, admin@websupport.sk, "
                "hello@digitalagency.sk, owner@firma.sk"
            ),
            "domain": "firma.sk",
        }

        primary, other = bot.select_primary_email(row)

        self.assertEqual(primary, "owner@firma.sk")
        self.assertEqual(other, [])
        self.assertEqual(bot.email_suppression_reason("urad@mestogov.sk", row), "public_authority")
        self.assertEqual(bot.email_suppression_reason("info@vub.sk", row), "bank")
        self.assertEqual(bot.email_suppression_reason("admin@websupport.sk", row), "hosting_provider")
        self.assertEqual(
            bot.email_suppression_reason("hello@digitalagency.sk", row),
            "agency_or_marketing_competitor",
        )

    def test_all_suppressed_addresses_leave_domain_without_recipient(self):
        primary, other = bot.select_primary_email(
            {
                "emails": "info@vub.sk, admin@websupport.sk",
                "new_removed_blacklist": "info@vub.sk",
            }
        )

        self.assertEqual(primary, "")
        self.assertEqual(other, [])

    def test_new_metrics_replace_stale_old_html(self):
        row = {
            "domain": "nova.sk",
            "campaign": "C1",
            "merge_metric_source": "new",
            "personalized_details_html": "<ul><li>Stará chyba</li></ul>",
            "selected_issues": "Missing CSP | PHP version is end-of-life",
        }

        details = bot.build_personalized_details_html(row)

        self.assertIn("Missing CSP", details)
        self.assertIn("PHP version is end-of-life", details)
        self.assertNotIn("Stará chyba", details)

    def test_new_issue_columns_have_safe_html_fallback(self):
        row = {
            "domain": "nova.sk",
            "campaign": "C3",
            "personalized_details_html": "",
            "new_issues_ranked": "Missing <script> | Missing HSTS",
        }

        details = bot.build_personalized_details_html(row)

        self.assertIn("&lt;script&gt;", details)
        self.assertNotIn("<script>", details)
        self.assertEqual(details.count("<li>"), 2)

    def test_campaign_and_template_use_new_row_values(self):
        sender = {
            "from_name": "Tester",
            "sender_title": "Consultant",
            "sender_phone": "+421 900 000 000",
        }
        template_fn = bot.make_campaigns_template_fn(
            {"C1": "<html>{{ DOMAIN }} {{ PERSONALIZED_DETAILS_HTML | safe }}</html>"},
            "<html>C6 {{ DOMAIN }}</html>",
            sender,
        )

        subject, text_body, html_body = template_fn(
            {
                "domain": "nova.sk",
                "campaign": "c1",
                "selected_issues": "Missing CSP | Missing HSTS",
            }
        )

        self.assertEqual(bot.row_campaign({"campaign": "c1"}), "C1")
        self.assertEqual(subject, "Návrh na zlepšenie webu.")
        self.assertIn("nova.sk", html_body)
        self.assertIn("Missing CSP", html_body)
        self.assertIn("Missing HSTS", text_body)

    def test_row_identity_is_index_independent(self):
        self.assertEqual(
            bot.row_identity("Nova.SK", "c1", "Info@Nova.SK"),
            ("nova.sk", "C1", "info@nova.sk"),
        )

    def test_wnp_mode_uses_one_template_for_c1_to_c5_and_one_for_c6(self):
        sender = {
            "from_name": "Tester",
            "sender_title": "Consultant",
            "sender_phone": "+421 900 000 000",
        }
        template_fn = bot.make_wnp_template_fn(
            "<html>{{ DOMAIN }} {{ PERSONALIZED_DETAILS_HTML | safe }}</html>",
            "wnp_html",
            sender,
            "<html>C6 {{ DOMAIN }} {{ PERSONALIZED_DETAILS_HTML | safe }}</html>",
        )

        for campaign in ("C1", "C2", "C3", "C4", "C5"):
            subject, _, _ = template_fn(
                {"domain": "nova.sk", "campaign": campaign, "selected_issues": "Missing HSTS"}
            )
            self.assertEqual(subject, bot.WNP_SUBJECT)

        subject, _, html_body = template_fn(
            {"domain": "nova.sk", "campaign": "C6", "selected_issues": "Missing HSTS"}
        )
        self.assertEqual(subject, bot.C6_COLLAB_SUBJECT)
        self.assertIn("C6 nova.sk", html_body)


if __name__ == "__main__":
    unittest.main()
