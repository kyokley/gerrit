import threading
import unittest

from gerrit.monitoring import MonitorError, ReviewMonitor


class FakeClock:
    def __init__(self, now=100.0):
        self.now = now

    def __call__(self):
        return self.now


class FakeReview:
    def __init__(self, feedback=None):
        self.items = list(feedback or [])
        self.calls = 0
        self.fail = False
        self.last_changes = None
        self.lock = threading.Lock()

    def feedback(self, changes):
        with self.lock:
            self.calls += 1
            self.last_changes = changes
            if self.fail:
                raise RuntimeError("diagnostic includes secret-do-not-publish")
            return list(self.items)


def item(kind="comment", **fields):
    return {"kind": kind, **fields}


class ReviewMonitorTests(unittest.TestCase):
    def make_monitor(self, review, *, clock=None, deadline=110, interval=60, published=None):
        published = published if published is not None else []
        monitor = ReviewMonitor(review, [{"change_id": "Ichange", "branch": "agents/task"}],
                                poll_interval=interval, deadline=deadline,
                                publish=published.append, emit=lambda *_: None,
                                clock=clock or FakeClock())
        return monitor, published

    def test_immediate_success_establishes_coverage_and_start_is_idempotent(self):
        review = FakeReview()
        monitor, published = self.make_monitor(review)
        monitor.start()
        monitor.start()
        try:
            self.assertEqual(review.calls, 1)
            state = monitor.check_health()
            self.assertTrue(state["coverage"])
            self.assertIsNotNone(state["last_poll"])
            self.assertTrue(published)
        finally:
            monitor.stop("test shutdown")

    def test_initial_auth_failure_does_not_claim_coverage_or_last_poll(self):
        review = FakeReview()
        review.fail = True
        monitor, published = self.make_monitor(review)
        with self.assertRaises(MonitorError):
            monitor.start()
        state = monitor.snapshot()
        self.assertFalse(state["coverage"])
        self.assertIsNone(state["last_poll"])
        self.assertTrue(state["gaps"])
        self.assertNotIn("secret-do-not-publish", repr(state))
        self.assertNotIn("secret-do-not-publish", repr(published))
        with self.assertRaises(MonitorError):
            monitor.check_health()
        monitor.stop("test shutdown")

    def test_poll_failure_keeps_last_success_timestamp_and_clears_coverage(self):
        review = FakeReview()
        monitor, _ = self.make_monitor(review, interval=0.01)
        monitor.start()
        first_poll = monitor.snapshot()["last_poll"]
        review.fail = True
        limit = __import__("time").monotonic() + 1
        while monitor.snapshot()["coverage"] and __import__("time").monotonic() < limit:
            __import__("time").sleep(0.005)
        try:
            state = monitor.snapshot()
            self.assertFalse(state["coverage"])
            self.assertEqual(state["last_poll"], first_poll)
            self.assertIn("authenticated feedback poll failed", state["gaps"])
        finally:
            monitor.stop("test shutdown")

    def test_feedback_claims_use_stable_identity_across_revisions(self):
        first = item("comment", id="comment-1", change_id="Ichange", current_revision="rev-a", text="fix")
        review = FakeReview([first])
        monitor, _ = self.make_monitor(review)
        monitor.start()
        try:
            fresh = monitor.feedback(fresh_only=True)
            self.assertEqual(len(fresh), 1)
            claimed = monitor.claim_feedback(fresh)
            self.assertEqual(len(claimed), 1)
            self.assertEqual(monitor.feedback(fresh_only=True), [])
            self.assertEqual(len(monitor.feedback()), 1)
            monitor.handled(fresh, [])
            self.assertEqual(len(monitor.feedback()), 1)
            review.items = [dict(first, current_revision="rev-b")]
            monitor._poll_once()
            self.assertEqual(monitor.feedback()[0]["current_revision"], "rev-b")
            self.assertEqual(monitor.feedback(fresh_only=True), [])
            monitor.handled(fresh, ["comment-1"])
            self.assertEqual(monitor.feedback(), [])
            monitor._poll_once()
            self.assertEqual(monitor.feedback(), [])
            review.items = [dict(first, current_revision="rev-b", text="edited"),
                            dict(first, current_revision="rev-b", id="comment-2")]
            monitor._poll_once()
            self.assertEqual({entry["id"] for entry in monitor.feedback(fresh_only=True)},
                             {"comment-1", "comment-2"})
        finally:
            monitor.stop("test shutdown")

    def test_set_changes_polls_only_confirmed_entries_and_retires_old_revision_checks(self):
        review = FakeReview([item("checks", id="checks-a", change_id="Ichange", current_revision="rev-a",
                                  supported=True, evidence=[{"name": "CI", "result": "SUCCESS"}])])
        monitor, _ = self.make_monitor(review)
        monitor.start()
        try:
            monitor.set_changes([
                {"change_id": "Ichange", "branch": "agents/task", "status": "confirmed", "server_revision": "rev-a"},
                {"change_id": "Inot-uploaded", "branch": "agents/task", "status": "uploading"},
            ])
            monitor._poll_once()
            self.assertEqual(review.last_changes, [{"change_id": "Ichange", "branch": "agents/task",
                                                    "status": "confirmed", "server_revision": "rev-a"}])
            self.assertEqual(len(monitor.snapshot()["checks"]), 1)
            review.items = [item("checks", id="checks-b", change_id="Ichange", current_revision="rev-b",
                                 supported=True, evidence=[{"name": "CI", "result": "PENDING"}])]
            monitor.set_changes([{"change_id": "Ichange", "branch": "agents/task", "status": "confirmed"}])
            monitor._poll_once()
            checks = monitor.snapshot()["checks"]
            self.assertEqual(len(checks), 1)
            self.assertEqual(checks[0]["current_revision"], "rev-b")
            self.assertFalse(monitor.ready_to_complete())
        finally:
            monitor.stop("test shutdown")

    def test_unsupported_failed_and_pending_checks_block_completion(self):
        cases = [
            item("checks", id="checks-a", change_id="Ichange", current_revision="rev-a", supported=False),
            item("checks", id="checks-b", change_id="Ichange", current_revision="rev-a", supported=True,
                 evidence=[{"name": "CI", "status": "FAILED"}]),
            item("checks", id="checks-c", change_id="Ichange", current_revision="rev-a", supported=True,
                 evidence=[{"name": "CI", "status": "RUNNING"}]),
            item("checks", id="checks-d", change_id="Ichange", current_revision="rev-a", supported=True,
                 evidence=[{"name": "CI", "status": "COMPLETED", "result": "FAILED"}]),
            item("checks", id="checks-e", change_id="Ichange", current_revision="rev-a", supported=True,
                 evidence=[{"name": "CI", "status": "COMPLETED"}]),
            item("checks", id="checks-f", change_id="Ichange", current_revision="rev-a", supported=True,
                 evidence=[{"name": "CI", "status": "PENDING"}]),
        ]
        for check in cases:
            with self.subTest(check=check["id"]):
                clock = FakeClock(100)
                monitor, _ = self.make_monitor(FakeReview([check]), clock=clock, deadline=110)
                monitor.start()
                clock.now = 111
                monitor._poll_once()
                self.assertFalse(monitor.ready_to_complete())
                self.assertTrue(monitor.snapshot()["pending"])
                monitor.stop("check remains unresolved")

    def test_completion_requires_deadline_and_successful_fresh_final_sweep(self):
        clock = FakeClock(100)
        review = FakeReview([item("checks", id="checks", change_id="Ichange", current_revision="rev-a",
                                   supported=True, evidence=[{"name": "CI", "status": "COMPLETED", "result": "SUCCESS"}])])
        monitor, _ = self.make_monitor(review, clock=clock, deadline=110)
        monitor.start()
        self.assertFalse(monitor.ready_to_complete())
        clock.now = 111
        monitor._poll_once()
        self.assertTrue(monitor.ready_to_complete())
        monitor.stop("verified deadline sweep", completed=True)
        state = monitor.snapshot()
        self.assertFalse(state["coverage"])
        self.assertEqual(state["gaps"], [])

    def test_missed_poll_interval_is_a_permanent_completion_gap(self):
        clock = FakeClock(100)
        monitor, _ = self.make_monitor(FakeReview(), clock=clock, deadline=101, interval=2)
        monitor.start()
        clock.now = 104
        monitor._poll_once()
        self.assertIn("monitor poll interval missed; coverage gap requires handoff", monitor.snapshot()["gaps"])
        self.assertFalse(monitor.ready_to_complete())
        monitor.stop("handoff required")

    def test_feedback_disposition_requires_a_new_final_sweep(self):
        clock = FakeClock(100)
        review = FakeReview()
        monitor, _ = self.make_monitor(review, clock=clock, deadline=110)
        monitor.start()
        clock.now = 111
        monitor._poll_once()
        self.assertTrue(monitor.ready_to_complete())
        incoming = item("comment", id="c1", change_id="Ichange", current_revision="rev-a", text="please fix")
        review.items = [incoming]
        clock.now = 112
        monitor._poll_once()
        self.assertFalse(monitor.ready_to_complete())
        monitor.handled(monitor.feedback(), ["c1"])
        self.assertFalse(monitor.ready_to_complete())
        clock.now = 113
        monitor._poll_once()
        self.assertTrue(monitor.ready_to_complete())
        monitor.stop("verified", completed=True)

    def test_stop_joins_poller_and_interruption_records_coverage_gap(self):
        monitor, _ = self.make_monitor(FakeReview(), interval=0.01)
        monitor.start()
        poller = monitor._thread
        monitor.stop("foreground shutdown")
        self.assertIsNotNone(poller)
        self.assertFalse(poller.is_alive())
        state = monitor.snapshot()
        self.assertFalse(state["coverage"])
        self.assertTrue(state["gaps"])

    def test_pending_capacity_overflow_is_explicit_and_blocks_completion(self):
        feedback = [item("comment", id=f"c-{index}", change_id="Ichange", current_revision="rev-a", text="x")
                    for index in range(205)]
        monitor, _ = self.make_monitor(FakeReview(feedback))
        monitor.start()
        state = monitor.snapshot()
        self.assertTrue(any(gap.startswith("monitor pending capacity exceeded") for gap in state["gaps"]))
        self.assertTrue(any(pending.get("kind") == "overflow" for pending in state["pending"]))
        self.assertFalse(monitor.ready_to_complete())
        monitor.stop("overflow handoff")


if __name__ == "__main__":
    unittest.main()
