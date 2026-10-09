import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.interaction_model.run_controller import RunController


class RunControllerShutdownTests(unittest.TestCase):
    def make_controller(self):
        engine_patch = patch("app.interaction_model.run_controller.FFmpegCompressionEngine")
        logger_patch = patch("app.interaction_model.run_controller.LoggingService")
        engine_patch.start()
        logger_patch.start()
        self.addCleanup(engine_patch.stop)
        self.addCleanup(logger_patch.stop)
        controller = RunController()
        self.addCleanup(controller.shutdown)
        return controller

    def test_shutdown_wakes_waiting_worker_and_joins_it(self):
        controller = self.make_controller()
        self.assertTrue(controller._worker.is_alive())
        self.assertTrue(controller.shutdown(timeout=1.0))
        self.assertTrue(controller._stop_requested.is_set())
        self.assertFalse(controller._worker.is_alive())

    def test_repeated_shutdown_is_idempotent(self):
        controller = self.make_controller()
        self.assertTrue(controller.shutdown(timeout=1.0))
        self.assertTrue(controller.shutdown(timeout=1.0))
        self.assertFalse(controller._worker.is_alive())

    def test_shutdown_without_worker_is_safe(self):
        controller = self.make_controller()
        self.assertTrue(controller.shutdown(timeout=1.0))
        controller._worker = None
        self.assertTrue(controller.shutdown(timeout=0.0))

    def test_normal_queue_work_still_runs_before_shutdown(self):
        controller = self.make_controller()
        completed = threading.Event()
        job = SimpleNamespace(status="IDLE", progress=0)
        controller._start_job = lambda queued_job: completed.set()
        controller._enqueue_job(job)
        self.assertTrue(completed.wait(1.0))
        self.assertTrue(controller.shutdown(timeout=1.0))
        self.assertFalse(controller._worker.is_alive())


if __name__ == "__main__":
    unittest.main()
