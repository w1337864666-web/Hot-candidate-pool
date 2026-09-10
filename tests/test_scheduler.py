import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from hotspot_agent.scheduler import SCHEDULE_TIMEZONE, run_due_schedule
from hotspot_agent.storage import Store


class SchedulerTests(unittest.TestCase):
    def test_daily_schedule_runs_once_and_records_success(self) -> None:
        calls: list[tuple[str, str]] = []

        def fake_scan(mode, _profile, store, configs, trigger_type):
            calls.append((mode, trigger_type))
            run_id = store.save_run(
                mode,
                configs or [],
                [],
                [],
                [],
                trigger_type=trigger_type,
            )
            return run_id, [], []

        with tempfile.TemporaryDirectory() as directory, Store(Path(directory) / "schedule.db") as store:
            store.save_app_settings({
                "schedule_enabled": 1,
                "schedule_time": "09:00",
                "schedule_mode": "demo",
            })
            current = datetime(2026, 8, 22, 9, 5, tzinfo=SCHEDULE_TIMEZONE)
            first = run_due_schedule(current, store=store, scan_fn=fake_scan)
            second = run_due_schedule(current, store=store, scan_fn=fake_scan)
            store.save_app_settings({"schedule_time": "10:00"})
            changed_time_same_day = run_due_schedule(
                datetime(2026, 8, 22, 10, 5, tzinfo=SCHEDULE_TIMEZONE),
                store=store,
                scan_fn=fake_scan,
            )

            self.assertEqual(first["status"], "succeeded")
            self.assertIsNone(second)
            self.assertIsNone(changed_time_same_day)
            self.assertEqual(calls, [("demo", "scheduled")])
            records = store.list_scheduled_scans()
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["status"], "succeeded")
            self.assertEqual(store.list_runs()[0]["trigger_type"], "scheduled")

    def test_disabled_or_not_yet_due_schedule_does_not_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory, Store(Path(directory) / "schedule.db") as store:
            calls: list[str] = []
            scan_fn = lambda *_args, **_kwargs: calls.append("called")
            store.save_app_settings({
                "schedule_enabled": 0,
                "schedule_time": "09:00",
                "schedule_mode": "live",
            })
            self.assertIsNone(run_due_schedule(
                datetime(2026, 8, 22, 10, 0, tzinfo=SCHEDULE_TIMEZONE),
                store=store,
                scan_fn=scan_fn,
            ))
            store.save_app_settings({"schedule_enabled": 1})
            self.assertIsNone(run_due_schedule(
                datetime(2026, 8, 22, 8, 59, tzinfo=SCHEDULE_TIMEZONE),
                store=store,
                scan_fn=scan_fn,
            ))
            self.assertEqual(calls, [])
            self.assertEqual(store.list_scheduled_scans(), [])

    def test_schedule_failure_is_recorded(self) -> None:
        def failed_scan(*_args, **_kwargs):
            raise RuntimeError("source unavailable")

        with tempfile.TemporaryDirectory() as directory, Store(Path(directory) / "schedule.db") as store:
            store.save_app_settings({
                "schedule_enabled": 1,
                "schedule_time": "09:00",
                "schedule_mode": "domestic",
            })
            result = run_due_schedule(
                datetime(2026, 8, 22, 10, 0, tzinfo=SCHEDULE_TIMEZONE),
                store=store,
                scan_fn=failed_scan,
            )
            self.assertEqual(result["status"], "failed")
            record = store.list_scheduled_scans()[0]
            self.assertEqual(record["status"], "failed")
            self.assertIn("source unavailable", record["error"])


if __name__ == "__main__":
    unittest.main()
