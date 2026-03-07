from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

import main as app_main


def make_args(cmd: str) -> SimpleNamespace:
    return SimpleNamespace(
        cmd=cmd,
        config="config.yaml",
        verbose=False,
        scenario="",
        interval=1.0,
        once=False,
        bridge_timeout=180.0,
        keep_last_error=False,
        keep_goal_retries=False,
        no_purge_quest_markers=False,
        reason="manual_stop",
        source="cli",
        verify_timeout=2.0,
    )


def make_config() -> SimpleNamespace:
    return SimpleNamespace(
        window=SimpleNamespace(),
        control=SimpleNamespace(),
        capture=SimpleNamespace(),
        ocr=SimpleNamespace(),
        planner=SimpleNamespace(),
        baritone=SimpleNamespace(transport="bridge_file"),
    )


class MainShutdownTest(unittest.TestCase):
    def test_run_invokes_fail_closed_stop_on_clean_exit(self) -> None:
        fake_baritone = mock.Mock()
        fake_baritone.fail_closed_stop.return_value = {"shutdownVerified": True}
        fake_runtime = mock.Mock()

        with mock.patch("main.parse_args", return_value=make_args("run")):
            with mock.patch("main.setup_logging"):
                with mock.patch("main.load_config", return_value=make_config()):
                    with mock.patch("main.GameController", return_value=mock.Mock()):
                        with mock.patch("main.ScreenPerception", return_value=mock.Mock()):
                            with mock.patch("main.BaritoneBridge", return_value=fake_baritone):
                                with mock.patch("main._build_runtime_goals", return_value=[]):
                                    with mock.patch("main.GoalPlanner", return_value=mock.Mock()):
                                        with mock.patch("main.AgentRuntime", return_value=fake_runtime):
                                            app_main.main()

        fake_runtime.run.assert_called_once_with()
        fake_baritone.fail_closed_stop.assert_called_once_with(
            reason="run_return",
            source="main.run.finally",
        )

    def test_run_invokes_fail_closed_stop_on_exception_exit(self) -> None:
        fake_baritone = mock.Mock()
        fake_baritone.fail_closed_stop.return_value = {"shutdownVerified": False}
        fake_runtime = mock.Mock()
        fake_runtime.run.side_effect = RuntimeError("boom")

        with mock.patch("main.parse_args", return_value=make_args("run")):
            with mock.patch("main.setup_logging"):
                with mock.patch("main.load_config", return_value=make_config()):
                    with mock.patch("main.GameController", return_value=mock.Mock()):
                        with mock.patch("main.ScreenPerception", return_value=mock.Mock()):
                            with mock.patch("main.BaritoneBridge", return_value=fake_baritone):
                                with mock.patch("main._build_runtime_goals", return_value=[]):
                                    with mock.patch("main.GoalPlanner", return_value=mock.Mock()):
                                        with mock.patch("main.AgentRuntime", return_value=fake_runtime):
                                            with self.assertRaises(RuntimeError):
                                                app_main.main()

        fake_runtime.run.assert_called_once_with()
        fake_baritone.fail_closed_stop.assert_called_once_with(
            reason="run_exception",
            source="main.run.finally",
        )


if __name__ == "__main__":
    unittest.main()
