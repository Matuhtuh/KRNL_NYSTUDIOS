from __future__ import annotations

import json
import os
import unittest
from types import SimpleNamespace
from unittest import mock

from agent.dashboard_chat import DashboardChatService


class FakeQueries:
    def get_bridge_status(self) -> dict:
        return {"freshness": {"fresh": True}, "status": {"inWorld": True}}

    def get_inventory_summary(self) -> dict:
        return {"freeSlots": 2, "topStacks": []}

    def get_stage_knowledge(self, stage_hint: str = "") -> dict:
        return {
            "stageHint": stage_hint or "hammer_to_resources",
            "label": "Hammer",
            "summary": "Stage",
            "evidence": [{"path": "stage.json", "label": "stage.json"}],
        }

    def get_item_route(self, item_id: str) -> dict:
        return {
            "itemId": item_id,
            "routes": [
                {
                    "routeId": "hammer__minecraft_cobblestone__minecraft_gravel",
                    "mechanism": "hammer",
                    "actionClass": "local_in_place",
                    "evidence": [{"path": "hammer.js", "label": "hammer.js"}],
                }
            ],
        }

    def get_next_obtainable_targets(self, *, limit: int = 6) -> dict:
        return {"targets": [{"itemId": "minecraft:gravel"}][:limit]}

    def search_knowledge(self, query: str, *, limit: int = 8) -> dict:
        return {
            "query": query,
            "matches": [
                {
                    "kind": "route",
                    "routeId": "hammer__minecraft_dirt__minecraft_sand",
                    "displayName": "Dirt to Sand",
                    "mechanism": "hammer",
                    "actionClass": "local_in_place",
                    "evidence": [{"path": "hammer.js", "label": "hammer.js"}],
                }
            ][:limit],
        }

    def explain_current_action(self) -> dict:
        return {
            "explanation": "quest_item_acquire minecraft:gravel x12",
            "actionClass": "local_in_place",
            "successCriteriaUsed": "inventory_or_stage_delta",
            "routeId": "hammer__minecraft_cobblestone__minecraft_gravel",
            "evidence": [{"path": "hammer.js", "label": "hammer.js"}],
        }

    def snapshot(self, preview_state: dict | None = None) -> dict:
        return {
            "stage": {"stageHint": "hammer_to_resources"},
            "currentAction": {
                "taskText": "quest_item_acquire minecraft:gravel x12",
                "activeCommand": "",
                "explanation": "quest",
                "actionClass": "local_in_place",
                "successCriteriaUsed": "inventory_or_stage_delta",
                "routeId": "hammer__minecraft_cobblestone__minecraft_gravel",
                "evidence": [{"path": "hammer.js", "label": "hammer.js"}],
                "successEvaluation": {"finalSuccess": True},
            },
            "inventory": {"freeSlots": 2},
            "live": {"status": {"phase": "task_running"}},
            "operatorLoop": {
                "currentTask": "quest_item_acquire minecraft:gravel x12",
                "previousCompletedTask": "quest_item_acquire minecraft:cobblestone x12",
                "nextSelectedTask": "quest_item_acquire minecraft:dirt x12",
                "nextTaskReason": "planner selected ready goal stone_chain",
                "lastBlocker": "inventory full before local StoneBlock conversion for minecraft:sand",
                "deferredTasks": [{"kind": "goal", "id": "quest_sand", "remainingSeconds": 42.0}],
            },
        }


class FakeControls:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def pause_automation(self, *, reason: str = "") -> dict:
        self.calls.append(("pause_automation", {"reason": reason}))
        return {"ok": True}

    def stop_automation(self, *, reason: str = "") -> dict:
        self.calls.append(("stop_automation", {"reason": reason}))
        return {"ok": True}

    def resume_automation(self, *, reason: str = "") -> dict:
        self.calls.append(("resume_automation", {"reason": reason}))
        return {"ok": True}

    def start_goal(self, goal_id: str, *, reason: str = "") -> dict:
        self.calls.append(("start_goal", {"goal_id": goal_id, "reason": reason}))
        return {"ok": True}


class DashboardChatServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        cfg = SimpleNamespace()
        self.controls = FakeControls()
        self.service = DashboardChatService(cfg, FakeQueries(), self.controls)
        self.service._ollama_is_available = lambda: True  # type: ignore[method-assign]
        self.session = self.service.create_session()["sessionId"]

    def test_local_model_json_contract_is_merged_into_contract(self) -> None:
        self.service._call_ollama_api = mock.Mock(  # type: ignore[method-assign]
            return_value={"id": "resp_1", "output_text": "{\"text\":\"Gravel comes from hammering cobblestone.\"}"}
        )

        events = list(self.service.stream_message(self.session, "Explain the gravel acquisition route."))
        final = events[-1]["data"]

        self.assertEqual("ok", final["status"])
        self.assertEqual("Gravel comes from hammering cobblestone.", final["text"])
        self.assertEqual([], final["tool_calls"])

    def test_control_tool_requires_confirmation_before_execution(self) -> None:
        events = list(self.service.stream_message(self.session, "/pause"))
        final = events[-1]["data"]

        self.assertTrue(final["requires_confirmation"])
        self.assertTrue(final["confirmation_token"])
        self.assertEqual([], self.controls.calls)

        confirmed = self.service.confirm_action(self.session, final["confirmation_token"])
        self.assertEqual("action_result", confirmed["intent"])
        self.assertEqual("pause_automation", self.controls.calls[0][0])

    def test_explain_current_action_tool_dispatch_preserves_action_fields(self) -> None:
        session = self.service._get_session(self.session)

        result, tool_log = self.service._dispatch_tool(session, "explain_current_action", {})

        self.assertEqual("explain_current_action", tool_log["name"])
        self.assertEqual("local_in_place", result["actionClass"])
        self.assertEqual("inventory_or_stage_delta", result["successCriteriaUsed"])
        self.assertEqual("hammer.js", result["evidence"][0]["label"])

    def test_dashboard_chat_model_defaults_to_lower_cost_operator_model(self) -> None:
        service = DashboardChatService(SimpleNamespace(), FakeQueries(), FakeControls())
        service._ollama_is_available = lambda: True  # type: ignore[method-assign]
        session = service._get_session(service.create_session()["sessionId"])

        with mock.patch.dict(os.environ, {}, clear=False):
            with mock.patch.dict(os.environ, {"OLLAMA_DASHBOARD_MODEL": ""}, clear=False):
                self.assertEqual("phi4-mini:latest", service._model_name(session))

    def test_dashboard_chat_model_prefers_env_then_config(self) -> None:
        configured = DashboardChatService(
            SimpleNamespace(dashboard=SimpleNamespace(ollama_model="gemma3:4b")),
            FakeQueries(),
            FakeControls(),
        )
        configured._ollama_is_available = lambda: True  # type: ignore[method-assign]
        session = configured._get_session(configured.create_session()["sessionId"])

        with mock.patch.dict(os.environ, {"OLLAMA_DASHBOARD_MODEL": ""}, clear=False):
            self.assertEqual("gemma3:4b", configured._model_name(session))

        with mock.patch.dict(os.environ, {"OLLAMA_DASHBOARD_MODEL": "phi4-mini:latest"}, clear=False):
            self.assertEqual("phi4-mini:latest", configured._model_name(session))

    def test_non_default_profile_uses_profile_model_over_global_default(self) -> None:
        configured = DashboardChatService(
            SimpleNamespace(dashboard=SimpleNamespace(ollama_model="phi4-mini:latest")),
            FakeQueries(),
            FakeControls(),
        )
        configured._ollama_is_available = lambda: True  # type: ignore[method-assign]
        session_id = configured.create_session(profile="planner_pack_knowledge")["sessionId"]
        session = configured._get_session(session_id)

        with mock.patch.dict(os.environ, {"OLLAMA_DASHBOARD_MODEL": ""}, clear=False):
            self.assertEqual("gemma3:4b", configured._model_name(session))

    def test_tool_dispatch_is_unchanged_under_lower_cost_model_setting(self) -> None:
        service = DashboardChatService(
            SimpleNamespace(dashboard=SimpleNamespace(ollama_model="phi4-mini:latest")),
            FakeQueries(),
            FakeControls(),
        )
        service._ollama_is_available = lambda: True  # type: ignore[method-assign]
        session = service._get_session(service.create_session()["sessionId"])

        result, tool_log = service._dispatch_tool(session, "get_item_route", {"item_id": "minecraft:gravel"})

        self.assertEqual("phi4-mini:latest", service._model_name(session))
        self.assertEqual("get_item_route", tool_log["name"])
        self.assertEqual("ok", tool_log["status"])
        self.assertEqual("hammer__minecraft_cobblestone__minecraft_gravel", result["routes"][0]["routeId"])

    def test_operator_status_command_uses_existing_tool_surface(self) -> None:
        events = list(self.service.stream_message(self.session, "/status"))
        final = events[-1]["data"]

        self.assertEqual("ok", final["status"])
        self.assertIn("Stage=hammer_to_resources", final["text"])
        self.assertEqual("get_bridge_status", final["tool_calls"][0]["name"])
        self.assertEqual("explain_current_action", final["tool_calls"][1]["name"])

    def test_natural_language_status_falls_back_to_local_operator_command(self) -> None:
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": ""}, clear=False):
            events = list(self.service.stream_message(self.session, "what am i doing now?"))
            final = events[-1]["data"]

        self.assertEqual("ok", final["status"])
        self.assertIn("Stage=hammer_to_resources", final["text"])
        self.assertEqual("get_bridge_status", final["tool_calls"][0]["name"])

    def test_colloquial_status_falls_back_to_local_operator_command(self) -> None:
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": ""}, clear=False):
            events = list(self.service.stream_message(self.session, "What you doing?"))
            final = events[-1]["data"]

        self.assertEqual("ok", final["status"])
        self.assertIn("Stage=hammer_to_resources", final["text"])
        self.assertEqual("get_bridge_status", final["tool_calls"][0]["name"])

    def test_natural_language_uses_local_model_when_available(self) -> None:
        self.service._call_ollama_api = mock.Mock(  # type: ignore[method-assign]
            return_value={"id": "resp_1", "output_text": "{\"text\":\"AI-backed status reply.\"}"}
        )

        events = list(self.service.stream_message(self.session, "tell me the current situation"))
        final = events[-1]["data"]

        self.assertEqual("ok", final["status"])
        self.assertEqual("AI-backed status reply.", final["text"])
        self.service._call_ollama_api.assert_called()

    def test_configured_openai_provider_dispatches_natural_language_to_responses_api(self) -> None:
        service = DashboardChatService(
            SimpleNamespace(dashboard=SimpleNamespace(chat_provider="openai", chat_model="gpt-4.1-nano")),
            FakeQueries(),
            FakeControls(),
        )
        service._has_responses_api_key = lambda: True  # type: ignore[method-assign]
        service._call_responses_api = mock.Mock(  # type: ignore[method-assign]
            return_value={"id": "resp_openai", "output_text": "{\"text\":\"OpenAI-backed status reply.\"}"}
        )
        service._call_ollama_api = mock.Mock(side_effect=AssertionError("ollama path should not be used"))  # type: ignore[method-assign]
        session_id = service.create_session()["sessionId"]

        events = list(service.stream_message(session_id, "tell me the current situation"))
        final = events[-1]["data"]

        self.assertEqual("openai", service.chat_runtime_status(service._get_session(session_id))["provider"])
        self.assertEqual("ok", final["status"])
        self.assertEqual("OpenAI-backed status reply.", final["text"])
        service._call_responses_api.assert_called_once()
        service._call_ollama_api.assert_not_called()

    def test_auto_provider_prefers_openai_dispatch_when_available(self) -> None:
        service = DashboardChatService(
            SimpleNamespace(dashboard=SimpleNamespace(chat_provider="auto", chat_model="gpt-4.1-nano", ollama_model="phi4-mini:latest")),
            FakeQueries(),
            FakeControls(),
        )
        service._has_responses_api_key = lambda: True  # type: ignore[method-assign]
        service._ollama_is_available = lambda: True  # type: ignore[method-assign]
        service._call_responses_api = mock.Mock(  # type: ignore[method-assign]
            return_value={"id": "resp_auto_openai", "output_text": "{\"text\":\"Auto picked OpenAI.\"}"}
        )
        service._call_ollama_api = mock.Mock(side_effect=AssertionError("ollama path should not be used"))  # type: ignore[method-assign]
        session_id = service.create_session()["sessionId"]

        events = list(service.stream_message(session_id, "tell me the current situation"))
        final = events[-1]["data"]

        self.assertEqual("openai", service.chat_runtime_status(service._get_session(session_id))["provider"])
        self.assertEqual("Auto picked OpenAI.", final["text"])
        service._call_responses_api.assert_called_once()
        service._call_ollama_api.assert_not_called()

    def test_auto_provider_falls_back_to_ollama_dispatch_without_openai_key(self) -> None:
        service = DashboardChatService(
            SimpleNamespace(dashboard=SimpleNamespace(chat_provider="auto", chat_model="gpt-4.1-nano", ollama_model="phi4-mini:latest")),
            FakeQueries(),
            FakeControls(),
        )
        service._has_responses_api_key = lambda: False  # type: ignore[method-assign]
        service._ollama_is_available = lambda: True  # type: ignore[method-assign]
        service._call_ollama_api = mock.Mock(  # type: ignore[method-assign]
            return_value={"id": "resp_auto_ollama", "output_text": "{\"text\":\"Auto picked Ollama.\"}"}
        )
        service._call_responses_api = mock.Mock(side_effect=AssertionError("responses path should not be used"))  # type: ignore[method-assign]
        session_id = service.create_session()["sessionId"]

        events = list(service.stream_message(session_id, "tell me the current situation"))
        final = events[-1]["data"]

        self.assertEqual("ollama", service.chat_runtime_status(service._get_session(session_id))["provider"])
        self.assertEqual("Auto picked Ollama.", final["text"])
        service._call_ollama_api.assert_called_once()
        service._call_responses_api.assert_not_called()

    def test_operator_route_command_includes_local_evidence(self) -> None:
        events = list(self.service.stream_message(self.session, "/route minecraft:sand"))
        final = events[-1]["data"]

        self.assertEqual("ok", final["status"])
        self.assertIn("minecraft:sand via hammer", final["text"])
        self.assertEqual("hammer.js", final["citations"][0]["label"])

    def test_natural_language_route_falls_back_to_local_operator_command(self) -> None:
        events = list(self.service.stream_message(self.session, "how do i get sand?"))
        final = events[-1]["data"]

        self.assertEqual("ok", final["status"])
        self.assertIn("minecraft:sand via hammer", final["text"])
        self.assertEqual("hammer.js", final["citations"][0]["label"])

    def test_operator_why_blocked_uses_operator_loop_blocker(self) -> None:
        events = list(self.service.stream_message(self.session, "/why blocked"))
        final = events[-1]["data"]

        self.assertEqual("ok", final["status"])
        self.assertIn("inventory full before local StoneBlock conversion", final["text"])

    def test_natural_language_pause_stays_confirmation_gated(self) -> None:
        events = list(self.service.stream_message(self.session, "pause automation"))
        final = events[-1]["data"]

        self.assertTrue(final["requires_confirmation"])
        self.assertEqual("pause_automation", final["proposed_action"]["tool"])
        self.assertEqual([], self.controls.calls)

    def test_stream_message_surfaces_specific_error_text(self) -> None:
        self.service._call_ollama_api = mock.Mock(side_effect=RuntimeError("ollama API unavailable"))  # type: ignore[method-assign]

        events = list(self.service.stream_message(self.session, "tell me something broad"))
        final = events[-1]["data"]

        self.assertEqual("error", final["status"])
        self.assertIn("ollama API unavailable", final["text"])
        self.assertEqual("ollama API unavailable", final["error"])

    def test_chat_runtime_status_reports_local_ollama_provider(self) -> None:
        service = DashboardChatService(
            SimpleNamespace(dashboard=SimpleNamespace(ollama_model="phi4-mini:latest", ollama_base_url="http://127.0.0.1:11434")),
            FakeQueries(),
            FakeControls(),
        )
        service._ollama_is_available = lambda: True  # type: ignore[method-assign]
        session = service._get_session(service.create_session()["sessionId"])
        status = service.chat_runtime_status(session)

        self.assertTrue(status["apiConfigured"])
        self.assertEqual("ollama", status["provider"])
        self.assertEqual("phi4-mini:latest", status["model"])
        self.assertEqual("ollama:http://127.0.0.1:11434", status["apiSource"])

    def test_chat_runtime_status_reports_ollama_unavailable_reason(self) -> None:
        service = DashboardChatService(
            SimpleNamespace(dashboard=SimpleNamespace(ollama_model="phi4-mini:latest", ollama_base_url="http://127.0.0.1:11434")),
            FakeQueries(),
            FakeControls(),
        )
        service._ollama_is_available = lambda: False  # type: ignore[method-assign]
        session = service._get_session(service.create_session()["sessionId"])
        status = service.chat_runtime_status(session)

        self.assertFalse(status["apiConfigured"])
        self.assertIn("Start Ollama", status["reason"])

    def test_mode_command_switches_profile(self) -> None:
        events = list(self.service.stream_message(self.session, "/mode diagnostics_explainer"))
        final = events[-1]["data"]

        self.assertEqual("ok", final["status"])
        self.assertEqual("diagnostics_explainer", self.service._get_session(self.session).profile)
        self.assertIn("diagnostics_explainer", final["text"])

    def test_pause_command_stays_confirmation_gated(self) -> None:
        events = list(self.service.stream_message(self.session, "/pause"))
        final = events[-1]["data"]

        self.assertTrue(final["requires_confirmation"])
        self.assertTrue(final["confirmation_token"])
        self.assertEqual([], self.controls.calls)

        confirmed = self.service.confirm_action(self.session, final["confirmation_token"])
        self.assertEqual("pause_automation", self.controls.calls[0][0])
        self.assertEqual("pause_automation", self.service._get_session(self.session).memory["last_confirmed_control_action"])

    def test_read_only_and_control_tool_surface_remains_available(self) -> None:
        service = DashboardChatService(
            SimpleNamespace(dashboard=SimpleNamespace(ollama_model="phi4-mini:latest")),
            FakeQueries(),
            FakeControls(),
        )
        service._ollama_is_available = lambda: True  # type: ignore[method-assign]
        session = service._get_session(service.create_session()["sessionId"])

        read_only_cases = [
            ("get_bridge_status", {}),
            ("get_inventory_summary", {}),
            ("get_stage_knowledge", {"stage_hint": "pre_sieving"}),
            ("get_item_route", {"item_id": "minecraft:gravel"}),
            ("get_next_obtainable_targets", {"limit": 2}),
            ("explain_current_action", {}),
        ]
        for name, args in read_only_cases:
            result, tool_log = service._dispatch_tool(session, name, args)
            self.assertEqual(name, tool_log["name"])
            self.assertEqual("ok", tool_log["status"])
            self.assertIsInstance(result, dict)

        control_cases = [
            ("pause_automation", {"reason": "pause"}),
            ("stop_automation", {"reason": "stop"}),
            ("resume_automation", {"reason": "resume"}),
            ("start_goal", {"goal_id": "goal_123", "reason": "start"}),
        ]
        for name, args in control_cases:
            queued, tool_log = service._dispatch_tool(session, name, args)
            self.assertEqual(name, tool_log["name"])
            self.assertEqual("confirmation_required", tool_log["status"])
            self.assertEqual("confirmation_required", queued["status"])
            confirmed = service.confirm_action(session.session_id, queued["confirmationToken"])
            self.assertEqual("action_result", confirmed["intent"])

    def test_call_ollama_api_uses_configured_model(self) -> None:
        service = DashboardChatService(
            SimpleNamespace(dashboard=SimpleNamespace(ollama_model="phi4-mini:latest", ollama_base_url="http://127.0.0.1:11434")),
            FakeQueries(),
            FakeControls(),
        )
        service._ollama_is_available = lambda: True  # type: ignore[method-assign]
        captured: dict[str, object] = {}

        class _FakeResponse:
            def __enter__(self) -> "_FakeResponse":
                return self

            def __exit__(self, exc_type, exc, tb) -> None:
                return None

            def read(self) -> bytes:
                return b'{"created_at":"resp_test","message":{"content":"{\\"text\\":\\"ok\\"}"}}'

        def _fake_urlopen(req, timeout=0):
            captured["body"] = json.loads(req.data.decode("utf-8"))
            captured["timeout"] = timeout
            return _FakeResponse()

        with mock.patch("agent.dashboard_chat.urllib_request.urlopen", side_effect=_fake_urlopen):
            session = service._get_session(service.create_session()["sessionId"])
            response = service._call_ollama_api(
                session=session,
                user_text="hi",
                snapshot=FakeQueries().snapshot(),
            )

        self.assertEqual("phi4-mini:latest", captured["body"]["model"])
        self.assertEqual("resp_test", response["id"])


if __name__ == "__main__":
    unittest.main()
