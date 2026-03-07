from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator
from urllib import error as urllib_error
from urllib import request as urllib_request

from agent.config import AppConfig
from agent.dashboard_control import DashboardControlExecutor
from agent.dashboard_state import DashboardQueryService
from agent.stoneblock4_knowledge import default_chat_schema_path

log = logging.getLogger(__name__)


_SYSTEM_PROMPT = (
    "You are the local StoneBlock 4 companion dashboard assistant. "
    "Ground every answer in the provided local tools and their evidence paths. "
    "Do not invent modpack facts. "
    "If a control tool reports confirmation_required, respond with requires_confirmation=true "
    "and surface the proposed action clearly. "
    "Keep responses concise and operational."
)

_PROFILE_PROMPTS: dict[str, str] = {
    "operator_cheap": (
        "Default operator mode. Prefer direct status, route, blocker, and control explanations. "
        "Use the cheapest viable tool-using behavior."
    ),
    "planner_pack_knowledge": (
        "Pack-knowledge mode. Emphasize locally extracted routes, stage gates, machine gates, and evidence paths."
    ),
    "diagnostics_explainer": (
        "Diagnostics mode. Emphasize blockers, recent action evaluations, operator loop decisions, and exact local evidence."
    ),
}

_PROFILE_MODELS: dict[str, str] = {
    "operator_cheap": "gpt-4.1-nano",
    "planner_pack_knowledge": "gpt-4.1-mini",
    "diagnostics_explainer": "gpt-4.1-nano",
}

_PROFILE_OLLAMA_MODELS: dict[str, str] = {
    "operator_cheap": "phi4-mini:latest",
    "planner_pack_knowledge": "gemma3:4b",
    "diagnostics_explainer": "phi4-mini:latest",
}

_ITEM_ALIAS_MAP: dict[str, str] = {
    "gravel": "minecraft:gravel",
    "dirt": "minecraft:dirt",
    "sand": "minecraft:sand",
    "cobblestone": "minecraft:cobblestone",
    "dust": "ftbstuff:dust",
}

_ITEM_ID_RE = re.compile(r"\b([a-z0-9_.-]+:[a-z0-9_./-]+)\b", re.IGNORECASE)
_STATUS_FALLBACK_PATTERNS: tuple[str, ...] = (
    "what am i doing",
    "what are you doing",
    "what you doing",
    "what u doing",
    "what's going on",
    "whats going on",
)


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _chat_schema() -> dict[str, Any]:
    raw = json.loads(default_chat_schema_path().read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("chat schema must be a JSON object")
    return raw


@dataclass
class PendingAction:
    token: str
    tool: str
    args: dict[str, Any]
    display: str
    created_at: str


@dataclass
class ChatSession:
    session_id: str
    created_at: str
    profile: str = "operator_cheap"
    transcript: list[dict[str, str]] = field(default_factory=list)
    pending_actions: dict[str, PendingAction] = field(default_factory=dict)
    memory: dict[str, Any] = field(default_factory=dict)


def _tool_call_summary(name: str, args: dict[str, Any], status: str, summary: str) -> dict[str, Any]:
    return {
        "name": name,
        "args": dict(args),
        "status": status,
        "summary": summary,
    }


def _snapshot_contract(snapshot: dict[str, Any]) -> dict[str, Any]:
    stage = dict(snapshot.get("stage", {}))
    current_action = dict(snapshot.get("currentAction", {}))
    inventory = dict(snapshot.get("inventory", {}))
    live = dict(snapshot.get("live", {}))
    status = dict(live.get("status", {}))
    return {
        "phase": str(status.get("phase", "")),
        "stage_hint": str(stage.get("stageHint", "")),
        "current_task": str(current_action.get("taskText", "") or current_action.get("activeCommand", "") or current_action.get("explanation", "")),
        "inventory_free_slots": int(inventory.get("freeSlots", 0) or 0),
    }


def _extract_response_text(payload: dict[str, Any]) -> str:
    text = str(payload.get("output_text", "")).strip()
    if text:
        return text
    output = payload.get("output")
    if not isinstance(output, list):
        return ""
    chunks: list[str] = []
    for item in output:
        if not isinstance(item, dict) or str(item.get("type", "")) != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            part_type = str(part.get("type", ""))
            if part_type in {"output_text", "text"}:
                value = str(part.get("text", "")).strip()
                if value:
                    chunks.append(value)
    return "\n".join(chunks).strip()


def _extract_function_calls(payload: dict[str, Any]) -> list[dict[str, Any]]:
    output = payload.get("output")
    if not isinstance(output, list):
        return []
    calls: list[dict[str, Any]] = []
    for item in output:
        if not isinstance(item, dict):
            continue
        if str(item.get("type", "")) == "function_call":
            calls.append(item)
    return calls


def _validate_contract(payload: dict[str, Any]) -> list[str]:
    required = [
        "message_id",
        "session_id",
        "created_at",
        "status",
        "intent",
        "text",
        "citations",
        "tool_calls",
        "proposed_action",
        "requires_confirmation",
        "confirmation_token",
        "snapshot",
        "safety_notes",
        "error",
    ]
    errors: list[str] = []
    for key in required:
        if key not in payload:
            errors.append(f"missing field: {key}")
    if "citations" in payload and not isinstance(payload.get("citations"), list):
        errors.append("citations must be a list")
    if "tool_calls" in payload and not isinstance(payload.get("tool_calls"), list):
        errors.append("tool_calls must be a list")
    if "snapshot" in payload and not isinstance(payload.get("snapshot"), dict):
        errors.append("snapshot must be an object")
    return errors


class DashboardChatService:
    def __init__(
        self,
        cfg: AppConfig,
        queries: DashboardQueryService,
        controls: DashboardControlExecutor,
    ) -> None:
        self.cfg = cfg
        self.queries = queries
        self.controls = controls
        self._sessions: dict[str, ChatSession] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _normalize_profile(profile: str | None) -> str:
        token = str(profile or "").strip().lower() or "operator_cheap"
        return token if token in _PROFILE_PROMPTS else "operator_cheap"

    def create_session(self, profile: str | None = None) -> dict[str, Any]:
        session = ChatSession(
            session_id=uuid.uuid4().hex,
            created_at=_iso_now(),
            profile=self._normalize_profile(profile),
        )
        with self._lock:
            self._sessions[session.session_id] = session
        return {
            "sessionId": session.session_id,
            "createdAt": session.created_at,
            "profile": session.profile,
            "chatStatus": self.chat_runtime_status(session),
        }

    def _get_session(self, session_id: str) -> ChatSession:
        token = str(session_id).strip()
        with self._lock:
            session = self._sessions.get(token)
        if session is None:
            raise KeyError(f"unknown session_id: {token}")
        return session

    def _tool_definitions(self) -> list[dict[str, Any]]:
        return [
            {"type": "function", "name": "get_bridge_status", "description": "Return the latest bridge status and freshness.", "parameters": {"type": "object", "properties": {}, "additionalProperties": False}},
            {"type": "function", "name": "get_inventory_summary", "description": "Return summarized inventory and hotbar state.", "parameters": {"type": "object", "properties": {}, "additionalProperties": False}},
            {"type": "function", "name": "get_stage_knowledge", "description": "Return stage-hint knowledge from the local StoneBlock instance.", "parameters": {"type": "object", "properties": {"stage_hint": {"type": "string"}}, "additionalProperties": False}},
            {"type": "function", "name": "get_item_route", "description": "Return local acquisition routes for an item.", "parameters": {"type": "object", "properties": {"item_id": {"type": "string"}}, "required": ["item_id"], "additionalProperties": False}},
            {"type": "function", "name": "get_next_obtainable_targets", "description": "Return nearby obtainable targets based on the current stage and inventory.", "parameters": {"type": "object", "properties": {"limit": {"type": "integer"}}, "additionalProperties": False}},
            {"type": "function", "name": "explain_current_action", "description": "Explain the current runtime task and active command.", "parameters": {"type": "object", "properties": {}, "additionalProperties": False}},
            {"type": "function", "name": "pause_automation", "description": "Pause automation after explicit UI confirmation.", "parameters": {"type": "object", "properties": {"reason": {"type": "string"}}, "additionalProperties": False}},
            {"type": "function", "name": "stop_automation", "description": "Stop automation after explicit UI confirmation.", "parameters": {"type": "object", "properties": {"reason": {"type": "string"}}, "additionalProperties": False}},
            {"type": "function", "name": "resume_automation", "description": "Resume automation after explicit UI confirmation.", "parameters": {"type": "object", "properties": {"reason": {"type": "string"}}, "additionalProperties": False}},
            {"type": "function", "name": "start_goal", "description": "Start a ready goal after explicit UI confirmation.", "parameters": {"type": "object", "properties": {"goal_id": {"type": "string"}, "reason": {"type": "string"}}, "required": ["goal_id"], "additionalProperties": False}},
        ]

    def _dashboard_cfg(self) -> Any:
        return getattr(self.cfg, "dashboard", None)

    def _configured_chat_provider(self) -> str:
        dashboard_cfg = self._dashboard_cfg()
        raw = str(getattr(dashboard_cfg, "chat_provider", "ollama") or "ollama").strip().lower()
        return raw if raw in {"ollama", "openai", "auto"} else "ollama"

    def _api_key_env_var_name(self) -> str:
        dashboard_cfg = self._dashboard_cfg()
        return str(getattr(dashboard_cfg, "openai_api_key_env_var", "OPENAI_API_KEY") or "OPENAI_API_KEY").strip() or "OPENAI_API_KEY"

    def _api_key_file_path(self) -> Path | None:
        dashboard_cfg = self._dashboard_cfg()
        raw = str(getattr(dashboard_cfg, "openai_api_key_file", "") or "").strip()
        if not raw:
            return None
        return Path(raw).expanduser()

    def _resolve_api_key(self) -> tuple[str, str]:
        env_name = self._api_key_env_var_name()
        env_value = str(os.environ.get(env_name, "")).replace("\ufeff", "").strip()
        if env_value:
            return env_value, f"env:{env_name}"
        key_path = self._api_key_file_path()
        if key_path is not None and key_path.exists() and key_path.is_file():
            try:
                file_value = key_path.read_text(encoding="utf-8").replace("\ufeff", "").strip()
            except Exception:
                file_value = ""
            if file_value:
                return file_value, f"file:{key_path}"
        return "", "missing"

    def _openai_headers(self) -> dict[str, str]:
        api_key, _ = self._resolve_api_key()
        if not api_key:
            env_name = self._api_key_env_var_name()
            key_path = self._api_key_file_path()
            if key_path is not None:
                raise RuntimeError(
                    f"{env_name} is not set and chat key file is missing or empty: {key_path}"
                )
            raise RuntimeError(f"{env_name} is not set")
        return {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    def _responses_url(self) -> str:
        dashboard_cfg = self._dashboard_cfg()
        configured_base = str(getattr(dashboard_cfg, "openai_base_url", "") or "").strip()
        base = str(os.environ.get("OPENAI_BASE_URL", configured_base or "https://api.openai.com/v1")).rstrip("/")
        return f"{base}/responses"

    def _ollama_base_url(self) -> str:
        dashboard_cfg = self._dashboard_cfg()
        configured_base = str(getattr(dashboard_cfg, "ollama_base_url", "") or "").strip()
        base = str(os.environ.get("OLLAMA_BASE_URL", configured_base or "http://127.0.0.1:11434")).strip()
        return base.rstrip("/")

    def _ollama_chat_url(self) -> str:
        return f"{self._ollama_base_url()}/api/chat"

    def _ollama_tags_url(self) -> str:
        return f"{self._ollama_base_url()}/api/tags"

    def _prefer_model_for_natural_language(self) -> bool:
        dashboard_cfg = self._dashboard_cfg()
        return bool(getattr(dashboard_cfg, "prefer_model_for_natural_language", True))

    def _has_responses_api_key(self) -> bool:
        api_key, _ = self._resolve_api_key()
        return bool(api_key)

    def _ollama_is_available(self) -> bool:
        req = urllib_request.Request(self._ollama_tags_url(), method="GET")
        try:
            with urllib_request.urlopen(req, timeout=2) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except Exception:
            return False
        return isinstance(payload, dict) and isinstance(payload.get("models"), list)

    def _provider_name(self, session: ChatSession | None = None) -> str:
        configured = self._configured_chat_provider()
        if configured in {"ollama", "openai"}:
            return configured
        if self._has_responses_api_key():
            return "openai"
        if self._ollama_is_available():
            return "ollama"
        return "openai"

    def chat_runtime_status(self, session: ChatSession | None = None) -> dict[str, Any]:
        provider = self._provider_name(session)
        _, source = self._resolve_api_key()
        profile = self._normalize_profile(getattr(session, "profile", "operator_cheap") if session is not None else "operator_cheap")
        configured = False
        source_label = source
        base_url = self._responses_url()
        reason = ""
        if provider == "ollama":
            configured = self._ollama_is_available()
            source_label = f"ollama:{self._ollama_base_url()}"
            base_url = self._ollama_chat_url()
            if not configured:
                reason = f"Start Ollama at {self._ollama_base_url()}"
        else:
            configured = source != "missing"
            if not configured:
                env_name = self._api_key_env_var_name()
                key_path = self._api_key_file_path()
                if key_path is not None:
                    reason = f"Set {env_name} or populate {key_path}"
                else:
                    reason = f"Set {env_name}"
            else:
                reason = ""
        return {
            "apiConfigured": configured,
            "apiSource": source_label,
            "provider": provider,
            "model": self._model_name(session),
            "profile": profile,
            "preferModelForNaturalLanguage": self._prefer_model_for_natural_language(),
            "baseUrl": base_url,
            "reason": reason,
        }

    def _openai_model_name(self, session: ChatSession | None = None) -> str:
        env_model = str(os.environ.get("OPENAI_DASHBOARD_MODEL", "")).strip()
        if env_model:
            return env_model
        profile = self._normalize_profile(getattr(session, "profile", "operator_cheap") if session is not None else "operator_cheap")
        if profile != "operator_cheap":
            return _PROFILE_MODELS.get(profile, "gpt-4.1-nano")
        dashboard_cfg = getattr(self.cfg, "dashboard", None)
        configured_model = str(getattr(dashboard_cfg, "chat_model", "")).strip() if dashboard_cfg is not None else ""
        if configured_model:
            return configured_model
        return _PROFILE_MODELS.get(profile, "gpt-4.1-nano")

    def _ollama_model_name(self, session: ChatSession | None = None) -> str:
        env_model = str(os.environ.get("OLLAMA_DASHBOARD_MODEL", "")).strip()
        if env_model:
            return env_model
        profile = self._normalize_profile(getattr(session, "profile", "operator_cheap") if session is not None else "operator_cheap")
        if profile != "operator_cheap":
            return _PROFILE_OLLAMA_MODELS.get(profile, "phi4-mini:latest")
        dashboard_cfg = getattr(self.cfg, "dashboard", None)
        configured_model = str(getattr(dashboard_cfg, "ollama_model", "")).strip() if dashboard_cfg is not None else ""
        if configured_model:
            return configured_model
        return _PROFILE_OLLAMA_MODELS.get(profile, "phi4-mini:latest")

    def _model_name(self, session: ChatSession | None = None) -> str:
        provider = self._provider_name(session)
        if provider == "ollama":
            return self._ollama_model_name(session)
        return self._openai_model_name(session)

    def _call_responses_api(
        self,
        *,
        session: ChatSession | None = None,
        input_payload: list[dict[str, Any]],
        previous_response_id: str | None = None,
    ) -> dict[str, Any]:
        profile = self._normalize_profile(getattr(session, "profile", "operator_cheap") if session is not None else "operator_cheap")
        model_name = self._model_name(session)
        instructions = f"{_SYSTEM_PROMPT} Profile={profile}. {_PROFILE_PROMPTS.get(profile, '')}".strip()
        body: dict[str, Any] = {
            "model": model_name,
            "instructions": instructions,
            "input": input_payload,
            "tools": self._tool_definitions(),
            "tool_choice": "auto",
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "stoneblock_dashboard_response",
                    "schema": _chat_schema(),
                    "strict": True,
                }
            },
        }
        if previous_response_id:
            body["previous_response_id"] = previous_response_id
        raw_body = json.dumps(body).encode("utf-8")
        started_at = time.perf_counter()
        session_id = str(getattr(session, "session_id", "")).strip()
        log.info(
            "dashboard_chat_request %s",
            json.dumps(
                {
                    "event": "dashboard_chat_request",
                    "phase": "request_start",
                    "route": "/v1/responses",
                    "sessionId": session_id,
                    "profile": profile,
                    "model": model_name,
                    "previousResponseId": previous_response_id or "",
                    "inputItems": len(input_payload),
                },
                ensure_ascii=True,
                sort_keys=True,
            ),
        )
        req = urllib_request.Request(
            self._responses_url(),
            data=raw_body,
            headers=self._openai_headers(),
            method="POST",
        )
        try:
            with urllib_request.urlopen(req, timeout=90) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib_error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            log.error(
                "dashboard_chat_request %s",
                json.dumps(
                    {
                        "event": "dashboard_chat_request",
                        "phase": "request_http_error",
                        "route": "/v1/responses",
                        "sessionId": session_id,
                        "profile": profile,
                        "model": model_name,
                        "durationMs": round((time.perf_counter() - started_at) * 1000.0, 2),
                        "httpStatus": int(exc.code),
                        "error": detail,
                    },
                    ensure_ascii=True,
                    sort_keys=True,
                ),
            )
            raise RuntimeError(f"responses API HTTP {exc.code}: {detail}") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("responses API returned non-object payload")
        log.info(
            "dashboard_chat_request %s",
            json.dumps(
                {
                    "event": "dashboard_chat_request",
                    "phase": "request_complete",
                    "route": "/v1/responses",
                    "sessionId": session_id,
                    "profile": profile,
                    "model": model_name,
                    "durationMs": round((time.perf_counter() - started_at) * 1000.0, 2),
                    "responseId": str(payload.get("id", "")).strip(),
                    "outputItems": len(payload.get("output", [])) if isinstance(payload.get("output"), list) else 0,
                },
                ensure_ascii=True,
                sort_keys=True,
            ),
        )
        return payload

    def _ollama_messages(self, session: ChatSession, user_text: str, snapshot: dict[str, Any]) -> list[dict[str, str]]:
        context = self._chat_context(session, user_text, snapshot)
        profile = self._normalize_profile(session.profile)
        system_prompt = (
            f"{_SYSTEM_PROMPT} Profile={profile}. {_PROFILE_PROMPTS.get(profile, '')} "
            "You are running on a local model without direct tool calling. "
            "Use the provided context, examples, citations, and memory. "
            "Do not invent pack facts. "
            "If the user asks for control actions, explain the action and mention that dashboard confirmation is required. "
            "Reply as a JSON object with fields: text (string), status (string), intent (string), citations (array, optional)."
        ).strip()
        messages: list[dict[str, str]] = [
            {"role": "system", "content": system_prompt},
            {"role": "system", "content": f"Context JSON: {json.dumps(context, ensure_ascii=True, sort_keys=True)}"},
        ]
        for message in session.transcript[-10:]:
            messages.append({"role": message["role"], "content": message["text"]})
        messages.append({"role": "user", "content": user_text})
        return messages

    def _call_ollama_api(
        self,
        *,
        session: ChatSession,
        user_text: str,
        snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        profile = self._normalize_profile(session.profile)
        model_name = self._model_name(session)
        body = {
            "model": model_name,
            "stream": False,
            "format": "json",
            "messages": self._ollama_messages(session, user_text, snapshot),
            "options": {"temperature": 0.2},
        }
        raw_body = json.dumps(body).encode("utf-8")
        started_at = time.perf_counter()
        log.info(
            "dashboard_chat_request %s",
            json.dumps(
                {
                    "event": "dashboard_chat_request",
                    "phase": "request_start",
                    "route": "/api/chat",
                    "provider": "ollama",
                    "sessionId": session.session_id,
                    "profile": profile,
                    "model": model_name,
                },
                ensure_ascii=True,
                sort_keys=True,
            ),
        )
        req = urllib_request.Request(
            self._ollama_chat_url(),
            data=raw_body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib_request.urlopen(req, timeout=90) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib_error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"ollama API HTTP {exc.code}: {detail}") from exc
        except urllib_error.URLError as exc:
            raise RuntimeError(f"ollama API unavailable: {exc.reason}") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("ollama API returned non-object payload")
        content = ""
        message = payload.get("message")
        if isinstance(message, dict):
            content = str(message.get("content", "")).strip()
        log.info(
            "dashboard_chat_request %s",
            json.dumps(
                {
                    "event": "dashboard_chat_request",
                    "phase": "request_complete",
                    "route": "/api/chat",
                    "provider": "ollama",
                    "sessionId": session.session_id,
                    "profile": profile,
                    "model": model_name,
                    "durationMs": round((time.perf_counter() - started_at) * 1000.0, 2),
                    "finalAssistantText": content,
                },
                ensure_ascii=True,
                sort_keys=True,
            ),
        )
        return {"id": str(payload.get("created_at", "")).strip() or uuid.uuid4().hex, "output_text": content}

    def _read_only_tool_result(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name == "get_bridge_status":
            return self.queries.get_bridge_status()
        if name == "get_inventory_summary":
            return self.queries.get_inventory_summary()
        if name == "get_stage_knowledge":
            return self.queries.get_stage_knowledge(str(args.get("stage_hint", "")).strip())
        if name == "get_item_route":
            return self.queries.get_item_route(str(args.get("item_id", "")).strip())
        if name == "get_next_obtainable_targets":
            limit = int(args.get("limit", 6) or 6)
            return self.queries.get_next_obtainable_targets(limit=limit)
        if name == "explain_current_action":
            return self.queries.explain_current_action()
        raise KeyError(name)

    def _queue_confirmation(self, session: ChatSession, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        token = uuid.uuid4().hex
        display = tool
        if tool == "start_goal":
            display = f"Start goal {str(args.get('goal_id', '')).strip()}"
        elif tool in {"pause_automation", "stop_automation", "resume_automation"}:
            display = tool.replace("_", " ")
        pending = PendingAction(
            token=token,
            tool=tool,
            args=dict(args),
            display=display,
            created_at=_iso_now(),
        )
        with self._lock:
            session.pending_actions[token] = pending
        return {
            "status": "confirmation_required",
            "tool": tool,
            "confirmationToken": token,
            "display": display,
            "args": dict(args),
        }

    def _dispatch_tool(
        self,
        session: ChatSession,
        name: str,
        args: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if name in {
            "get_bridge_status",
            "get_inventory_summary",
            "get_stage_knowledge",
            "get_item_route",
            "get_next_obtainable_targets",
            "explain_current_action",
        }:
            result = self._read_only_tool_result(name, args)
            summary = f"{name} completed"
            return result, _tool_call_summary(name, args, "ok", summary)
        if name in {"pause_automation", "stop_automation", "resume_automation", "start_goal"}:
            result = self._queue_confirmation(session, name, args)
            summary = f"{name} requires confirmation"
            return result, _tool_call_summary(name, args, "confirmation_required", summary)
        result = {"status": "error", "error": f"unsupported tool: {name}"}
        return result, _tool_call_summary(name, args, "error", result["error"])

    @staticmethod
    def _citations_from_evidence(evidence: list[dict[str, Any]], *, kind: str) -> list[dict[str, Any]]:
        citations: list[dict[str, Any]] = []
        for entry in evidence[:6]:
            if not isinstance(entry, dict):
                continue
            citations.append(
                {
                    "kind": kind,
                    "source": str(entry.get("path", "")).strip(),
                    "label": str(entry.get("label", "")).strip() or str(entry.get("path", "")).strip(),
                    "evidence": str(entry.get("label", "")).strip() or str(entry.get("path", "")).strip(),
                }
            )
        return citations

    def _mentioned_item_ids(self, text: str) -> list[str]:
        lowered = str(text).strip().lower()
        found: list[str] = []
        for match in _ITEM_ID_RE.findall(lowered):
            token = str(match).strip().lower()
            if token and token not in found:
                found.append(token)
        for alias, item_id in _ITEM_ALIAS_MAP.items():
            if alias in lowered and item_id not in found:
                found.append(item_id)
        return found[:3]

    def _retrieved_examples(self, session: ChatSession, user_text: str, snapshot: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        examples: list[dict[str, Any]] = []
        citations: list[dict[str, Any]] = []
        stage_hint = str((snapshot.get("stage") or {}).get("stageHint", "")).strip()
        if stage_hint:
            stage = self.queries.get_stage_knowledge(stage_hint)
            examples.append(
                {
                    "type": "stage",
                    "stageHint": stage_hint,
                    "summary": str(stage.get("summary", "")).strip(),
                    "focusItems": list(stage.get("focusItems", []))[:6],
                }
            )
            citations.extend(self._citations_from_evidence(list(stage.get("evidence", [])), kind="knowledge"))
            for route in list(stage.get("routes", []))[:4]:
                if not isinstance(route, dict):
                    continue
                examples.append(
                    {
                        "type": "stage_route",
                        "routeId": str(route.get("routeId", "")).strip(),
                        "displayName": str(route.get("displayName", "")).strip(),
                        "mechanism": str(route.get("mechanism", "")).strip(),
                        "actionClass": str(route.get("actionClass", "")).strip(),
                    }
                )
                citations.extend(self._citations_from_evidence(list(route.get("evidence", [])), kind="kubejs"))

        current_action = dict(snapshot.get("currentAction", {}))
        if current_action:
            success_eval = dict(current_action.get("successEvaluation", {}))
            examples.append(
                {
                    "type": "current_action",
                    "actionClass": str(current_action.get("actionClass", "")).strip(),
                    "routeId": str(current_action.get("routeId", "")).strip(),
                    "successCriteriaUsed": str(current_action.get("successCriteriaUsed", "")).strip(),
                    "finalSuccess": bool(success_eval.get("finalSuccess", False)),
                }
            )
            citations.extend(self._citations_from_evidence(list(current_action.get("evidence", [])), kind="bridge"))

        operator_loop = dict(snapshot.get("operatorLoop", {}))
        blocker = str(operator_loop.get("lastBlocker", "") or snapshot.get("lastError", "")).strip()
        if blocker:
            examples.append({"type": "blocker", "text": blocker})

        next_targets = self.queries.get_next_obtainable_targets(limit=4)
        for target in list(next_targets.get("targets", []))[:4]:
            if not isinstance(target, dict):
                continue
            examples.append(
                {
                    "type": "next_target",
                    "itemId": str(target.get("itemId", "")).strip(),
                    "observedCount": int(target.get("observedCount", 0) or 0),
                    "targetMin": int(target.get("targetMin", 0) or 0),
                    "routes": [
                        str(route.get("routeId", "")).strip()
                        for route in list(target.get("routes", []))[:3]
                        if isinstance(route, dict)
                    ],
                }
            )
            for route in list(target.get("routes", []))[:3]:
                if isinstance(route, dict):
                    citations.extend(self._citations_from_evidence(list(route.get("evidence", [])), kind="kubejs"))

        for item_id in self._mentioned_item_ids(user_text):
            route_payload = self.queries.get_item_route(item_id)
            routes = list(route_payload.get("routes", []))
            if not routes:
                continue
            route = dict(routes[0]) if isinstance(routes[0], dict) else {}
            examples.append(
                {
                    "type": "route",
                    "itemId": item_id,
                    "routeId": str(route.get("routeId", "")).strip(),
                    "mechanism": str(route.get("mechanism", "")).strip(),
                    "actionClass": str(route.get("actionClass", "")).strip(),
                }
            )
            citations.extend(self._citations_from_evidence(list(route.get("evidence", [])), kind="kubejs"))
            requirements = list(route_payload.get("machineRequirements", []))
            if requirements:
                examples.append(
                    {
                        "type": "machine_requirements",
                        "itemId": item_id,
                        "requirements": [
                            {
                                "routeId": str(req.get("routeId", "")).strip(),
                                "displayName": str(req.get("displayName", "")).strip(),
                                "upgradeKey": str(req.get("upgradeKey", "")).strip(),
                            }
                            for req in requirements[:4]
                            if isinstance(req, dict)
                        ],
                    }
                )
                for req in requirements[:4]:
                    if isinstance(req, dict):
                        citations.extend(self._citations_from_evidence(list(req.get("evidence", [])), kind="knowledge"))

        search_hits = self.queries.search_knowledge(user_text, limit=6)
        for match in list(search_hits.get("matches", []))[:6]:
            if not isinstance(match, dict):
                continue
            entry = {"type": "search_match", "kind": str(match.get("kind", "")).strip()}
            for key in ("routeId", "displayName", "mechanism", "actionClass", "itemId", "stageHint", "label", "summary"):
                value = str(match.get(key, "")).strip()
                if value:
                    entry[key] = value
            if isinstance(match.get("requirements"), list):
                entry["requirements"] = list(match.get("requirements", []))[:3]
            examples.append(entry)
            citations.extend(self._citations_from_evidence(list(match.get("evidence", [])), kind="knowledge"))
        return examples[:16], citations[:12]

    def _update_session_memory(self, session: ChatSession, snapshot: dict[str, Any], *, last_confirmed_control_action: str = "") -> None:
        current_action = dict(snapshot.get("currentAction", {}))
        operator_loop = dict(snapshot.get("operatorLoop", {}))
        success_eval = dict(current_action.get("successEvaluation", {}))
        memory: dict[str, Any] = {
            "current_stage": str((snapshot.get("stage") or {}).get("stageHint", "")).strip(),
            "current_goal_or_pinned_target": str(operator_loop.get("currentGoalId", "")).strip(),
            "last_blocker": str(operator_loop.get("lastBlocker", "") or snapshot.get("lastError", "")).strip(),
            "last_successful_route": "",
            "last_confirmed_control_action": str(last_confirmed_control_action or session.memory.get("last_confirmed_control_action", "")).strip(),
        }
        if bool(success_eval.get("finalSuccess", False)):
            memory["last_successful_route"] = str(
                current_action.get("routeId") or success_eval.get("routeId") or session.memory.get("last_successful_route", "")
            ).strip()
        else:
            memory["last_successful_route"] = str(session.memory.get("last_successful_route", "")).strip()
        session.memory = memory

    def _chat_context(self, session: ChatSession, user_text: str, snapshot: dict[str, Any]) -> dict[str, Any]:
        examples, citations = self._retrieved_examples(session, user_text, snapshot)
        return {
            "snapshot": _snapshot_contract(snapshot),
            "operatorLoop": dict(snapshot.get("operatorLoop", {})),
            "memory": dict(session.memory),
            "examples": examples,
            "citations": citations,
        }

    def _direct_contract(
        self,
        session: ChatSession,
        *,
        status: str,
        intent: str,
        text: str,
        tool_calls: list[dict[str, Any]],
        snapshot: dict[str, Any],
        citations: list[dict[str, Any]] | None = None,
        proposed_action: dict[str, Any] | None = None,
        requires_confirmation: bool = False,
        confirmation_token: str | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        return self._normalize_contract(
            session,
            {
                "status": status,
                "intent": intent,
                "text": text,
                "citations": list(citations or []),
                "tool_calls": list(tool_calls),
                "proposed_action": proposed_action,
                "requires_confirmation": requires_confirmation,
                "confirmation_token": confirmation_token,
                "snapshot": _snapshot_contract(snapshot),
                "safety_notes": [],
                "error": error,
            },
            tool_calls=tool_calls,
            snapshot=snapshot,
        )

    def _handle_operator_command(self, session: ChatSession, user_text: str) -> tuple[dict[str, Any], list[dict[str, Any]]] | None:
        raw = str(user_text).strip()
        if not raw.startswith("/"):
            return None
        parts = raw.split(maxsplit=1)
        command = parts[0].strip().lower()
        arg_text = parts[1].strip() if len(parts) > 1 else ""
        snapshot = self.queries.snapshot()

        if command == "/mode":
            profile = self._normalize_profile(arg_text or session.profile)
            session.profile = profile
            tool_logs = [_tool_call_summary("profile_select", {"profile": profile}, "ok", f"profile set to {profile}")]
            contract = self._direct_contract(
                session,
                status="ok",
                intent="answer",
                text=f"Chat profile set to {profile}.",
                tool_calls=tool_logs,
                snapshot=snapshot,
            )
            return contract, tool_logs

        if command == "/status":
            action = self.queries.explain_current_action()
            bridge = self.queries.get_bridge_status()
            operator_loop = dict(snapshot.get("operatorLoop", {}))
            stage_hint = str(action.get("stageHint") or (snapshot.get("stage") or {}).get("stageHint") or "-")
            tool_logs = [
                _tool_call_summary("get_bridge_status", {}, "ok", "get_bridge_status completed"),
                _tool_call_summary("explain_current_action", {}, "ok", "explain_current_action completed"),
            ]
            text = (
                f"Stage={stage_hint}; action={action.get('taskText') or action.get('activeCommand') or action.get('explanation') or 'idle'}; "
                f"bridgeFresh={bool((bridge.get('freshness') or {}).get('fresh', False))}; "
                f"next={operator_loop.get('nextSelectedTask') or '-'}; reason={operator_loop.get('nextTaskReason') or '-'}"
            )
            citations = self._citations_from_evidence(list(action.get("evidence", [])), kind="bridge")
            return self._direct_contract(
                session,
                status="ok",
                intent="answer",
                text=text,
                tool_calls=tool_logs,
                snapshot=snapshot,
                citations=citations,
            ), tool_logs

        if command == "/why" and arg_text.lower() == "blocked":
            action = self.queries.explain_current_action()
            operator_loop = dict(snapshot.get("operatorLoop", {}))
            blocker = str(operator_loop.get("lastBlocker", "") or snapshot.get("lastError", "") or action.get("error", "")).strip()
            tool_logs = [_tool_call_summary("explain_current_action", {}, "ok", "explain_current_action completed")]
            text = blocker or "No current blocker is recorded."
            citations = self._citations_from_evidence(list(action.get("evidence", [])), kind="bridge")
            return self._direct_contract(
                session,
                status="ok",
                intent="answer",
                text=text,
                tool_calls=tool_logs,
                snapshot=snapshot,
                citations=citations,
            ), tool_logs

        if command == "/route":
            item_id = (arg_text or "").strip().lower()
            route_payload = self.queries.get_item_route(item_id)
            routes = list(route_payload.get("routes", []))
            route = dict(routes[0]) if routes and isinstance(routes[0], dict) else {}
            tool_logs = [_tool_call_summary("get_item_route", {"item_id": item_id}, "ok", "get_item_route completed")]
            if route:
                text = (
                    f"{item_id} via {route.get('mechanism') or 'unknown'} "
                    f"({route.get('actionClass') or 'unknown'}) route={route.get('routeId') or '-'}"
                )
                citations = self._citations_from_evidence(list(route.get("evidence", [])), kind="kubejs")
            else:
                text = f"No local route found for {item_id}."
                citations = []
            return self._direct_contract(
                session,
                status="ok",
                intent="answer",
                text=text,
                tool_calls=tool_logs,
                snapshot=snapshot,
                citations=citations,
            ), tool_logs

        if command in {"/pause", "/stop", "/resume"}:
            tool_name = {
                "/pause": "pause_automation",
                "/stop": "stop_automation",
                "/resume": "resume_automation",
            }[command]
            result, tool_log = self._dispatch_tool(session, tool_name, {"reason": f"chat command {command}"})
            requires_confirmation = str(tool_log.get("status", "")) == "confirmation_required"
            contract = self._direct_contract(
                session,
                status="needs_confirmation" if requires_confirmation else str(result.get("status", "ok")),
                intent="propose_action" if requires_confirmation else "action_result",
                text=f"{tool_name} ready for confirmation." if requires_confirmation else f"Executed {tool_name}.",
                tool_calls=[tool_log],
                snapshot=snapshot,
                proposed_action={
                    "tool": tool_name,
                    "args": dict(result.get("args", {})),
                    "display": str(result.get("display", tool_name)),
                }
                if requires_confirmation
                else None,
                requires_confirmation=requires_confirmation,
                confirmation_token=str(result.get("confirmationToken", "")).strip() or None,
            )
            return contract, [tool_log]

        if command == "/goal":
            goal_id = arg_text.strip()
            if not goal_id:
                tool_logs = [_tool_call_summary("start_goal", {}, "error", "goal_id is required")]
                return self._direct_contract(
                    session,
                    status="error",
                    intent="error",
                    text="Usage: /goal <goal_id>",
                    tool_calls=tool_logs,
                    snapshot=snapshot,
                    error="goal_id is required",
                ), tool_logs
            result, tool_log = self._dispatch_tool(session, "start_goal", {"goal_id": goal_id, "reason": "chat command /goal"})
            requires_confirmation = str(tool_log.get("status", "")) == "confirmation_required"
            contract = self._direct_contract(
                session,
                status="needs_confirmation" if requires_confirmation else str(result.get("status", "ok")),
                intent="propose_action" if requires_confirmation else "action_result",
                text=f"start_goal ready for confirmation: {goal_id}" if requires_confirmation else f"Executed start_goal for {goal_id}.",
                tool_calls=[tool_log],
                snapshot=snapshot,
                proposed_action={
                    "tool": "start_goal",
                    "args": dict(result.get("args", {})),
                    "display": str(result.get("display", goal_id)),
                }
                if requires_confirmation
                else None,
                requires_confirmation=requires_confirmation,
                confirmation_token=str(result.get("confirmationToken", "")).strip() or None,
            )
            return contract, [tool_log]

        return None

    def _handle_local_intent_fallback(self, session: ChatSession, user_text: str) -> tuple[dict[str, Any], list[dict[str, Any]]] | None:
        lowered = str(user_text).strip().lower()
        if not lowered:
            return None

        if any(pattern in lowered for pattern in _STATUS_FALLBACK_PATTERNS) or lowered in {"status", "current status"}:
            return self._handle_operator_command(session, "/status")

        if "why blocked" in lowered or "what is blocking" in lowered or "why are you blocked" in lowered:
            return self._handle_operator_command(session, "/why blocked")

        if (
            "how do i get " in lowered
            or "how do you get " in lowered
            or "how to get " in lowered
            or "route for " in lowered
            or lowered.startswith("route ")
        ):
            item_ids = self._mentioned_item_ids(lowered)
            if item_ids:
                return self._handle_operator_command(session, f"/route {item_ids[0]}")

        if lowered in {"pause", "pause automation", "please pause", "please pause automation"}:
            return self._handle_operator_command(session, "/pause")

        if lowered in {"stop", "stop automation", "please stop", "please stop automation"}:
            return self._handle_operator_command(session, "/stop")

        if lowered in {"resume", "resume automation", "please resume", "please resume automation"}:
            return self._handle_operator_command(session, "/resume")

        if lowered.startswith("start goal "):
            goal_id = lowered.removeprefix("start goal ").strip()
            if goal_id:
                return self._handle_operator_command(session, f"/goal {goal_id}")

        return None

    def _normalize_contract(
        self,
        session: ChatSession,
        payload: dict[str, Any],
        *,
        tool_calls: list[dict[str, Any]],
        snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        contract = dict(payload)
        contract.setdefault("message_id", uuid.uuid4().hex)
        contract.setdefault("session_id", session.session_id)
        contract.setdefault("created_at", _iso_now())
        contract.setdefault("status", "ok")
        contract.setdefault("intent", "answer")
        contract.setdefault("text", "")
        contract.setdefault("citations", [])
        if not isinstance(contract.get("tool_calls"), list) or not contract.get("tool_calls"):
            contract["tool_calls"] = tool_calls
        contract.setdefault("proposed_action", None)
        contract.setdefault("requires_confirmation", False)
        contract.setdefault("confirmation_token", None)
        contract.setdefault("snapshot", _snapshot_contract(snapshot))
        contract.setdefault("safety_notes", [])
        contract.setdefault("error", None)

        if not contract["proposed_action"]:
            for call in tool_calls:
                if str(call.get("status", "")) == "confirmation_required":
                    contract["proposed_action"] = {
                        "tool": str(call.get("name", "")),
                        "args": dict(call.get("args", {})),
                        "display": str(call.get("summary", "")),
                    }
                    contract["requires_confirmation"] = True
                    break

        if contract.get("requires_confirmation") and not contract.get("confirmation_token"):
            pending = next(iter(session.pending_actions.values()), None)
            if pending is not None:
                contract["confirmation_token"] = pending.token
                if contract.get("proposed_action") is None:
                    contract["proposed_action"] = {
                        "tool": pending.tool,
                        "args": dict(pending.args),
                        "display": pending.display,
                    }
                contract["status"] = "needs_confirmation"
                contract["intent"] = "propose_action"

        errors = _validate_contract(contract)
        if errors:
            contract["status"] = "error"
            contract["intent"] = "error"
            contract["error"] = "; ".join(errors)
            contract["text"] = contract.get("text") or "Chat contract validation failed."
        return contract

    def _chat_input(self, session: ChatSession, user_text: str, snapshot: dict[str, Any]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        context = self._chat_context(session, user_text, snapshot)
        items.append(
            {
                "role": "system",
                "content": [{"type": "input_text", "text": json.dumps(context, ensure_ascii=True, sort_keys=True)}],
            }
        )
        for message in session.transcript[-10:]:
            items.append(
                {
                    "role": message["role"],
                    "content": [{"type": "input_text", "text": message["text"]}],
                }
            )
        items.append({"role": "user", "content": [{"type": "input_text", "text": user_text}]})
        return items

    def _run_turn(self, session: ChatSession, user_text: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        direct = self._handle_operator_command(session, user_text)
        if direct is not None:
            snapshot = self.queries.snapshot()
            self._update_session_memory(session, snapshot)
            return direct

        provider = self._provider_name(session)
        use_model_first = self._prefer_model_for_natural_language() and (
            provider == "ollama" or self._has_responses_api_key()
        )
        if provider == "ollama":
            local_fallback = self._handle_local_intent_fallback(session, user_text)
            if local_fallback is not None:
                snapshot = self.queries.snapshot()
                self._update_session_memory(session, snapshot)
                return local_fallback
        elif not use_model_first:
            local_fallback = self._handle_local_intent_fallback(session, user_text)
            if local_fallback is not None:
                snapshot = self.queries.snapshot()
                self._update_session_memory(session, snapshot)
                return local_fallback

        tool_logs: list[dict[str, Any]] = []
        initial_snapshot = self.queries.snapshot()
        if provider == "ollama":
            response = self._call_ollama_api(
                session=session,
                user_text=user_text,
                snapshot=initial_snapshot,
            )
            text = _extract_response_text(response)
            parsed: dict[str, Any]
            if text:
                try:
                    payload = json.loads(text)
                    parsed = payload if isinstance(payload, dict) else {}
                except Exception:
                    parsed = {"text": text}
            else:
                parsed = {}
            snapshot = self.queries.snapshot()
            examples, citations = self._retrieved_examples(session, user_text, snapshot)
            contract = self._normalize_contract(session, parsed, tool_calls=tool_logs, snapshot=snapshot)
            if not list(contract.get("citations", [])) and citations:
                contract["citations"] = citations
            if examples and not contract.get("text"):
                contract["text"] = json.dumps({"examples": examples}, ensure_ascii=True)
            self._update_session_memory(session, snapshot)
            return contract, tool_logs

        response = self._call_responses_api(
            session=session,
            input_payload=self._chat_input(session, user_text, initial_snapshot),
        )
        previous_response_id = str(response.get("id", "")).strip() or None
        for _ in range(8):
            function_calls = _extract_function_calls(response)
            if not function_calls:
                break
            tool_outputs: list[dict[str, Any]] = []
            for call in function_calls:
                name = str(call.get("name", "")).strip()
                call_id = str(call.get("call_id", "")).strip()
                args_raw = str(call.get("arguments", "")).strip() or "{}"
                try:
                    args = json.loads(args_raw)
                    if not isinstance(args, dict):
                        args = {}
                except Exception:
                    args = {}
                result, tool_log = self._dispatch_tool(session, name, args)
                tool_logs.append(tool_log)
                tool_outputs.append(
                    {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": json.dumps(result),
                    }
                )
            response = self._call_responses_api(
                session=session,
                input_payload=tool_outputs,
                previous_response_id=previous_response_id,
            )
            previous_response_id = str(response.get("id", "")).strip() or previous_response_id
        text = _extract_response_text(response)
        parsed: dict[str, Any]
        if text:
            try:
                payload = json.loads(text)
                parsed = payload if isinstance(payload, dict) else {}
            except Exception:
                parsed = {"text": text}
        else:
            parsed = {}
        snapshot = self.queries.snapshot()
        examples, citations = self._retrieved_examples(session, user_text, snapshot)
        contract = self._normalize_contract(session, parsed, tool_calls=tool_logs, snapshot=snapshot)
        if not list(contract.get("citations", [])) and citations:
            contract["citations"] = citations
        if examples and not contract.get("text"):
            contract["text"] = json.dumps({"examples": examples}, ensure_ascii=True)
        self._update_session_memory(session, snapshot)
        return contract, tool_logs

    def stream_message(self, session_id: str, user_text: str) -> Generator[dict[str, Any], None, None]:
        session = self._get_session(session_id)
        started_at = time.perf_counter()
        model_name = self._model_name(session)
        yield {
            "event": "status",
            "data": {
                "phase": "request_started",
                "handler": "/api/chat/message",
                "sessionId": session.session_id,
                "profile": session.profile,
                "model": model_name,
                "requestStartedAt": _iso_now(),
            },
        }
        try:
            contract, tool_logs = self._run_turn(session, user_text)
        except Exception as exc:
            snapshot = self.queries.snapshot()
            visible_error = f"Chat request failed: {exc}"
            log.error(
                "dashboard_chat_stream %s",
                json.dumps(
                    {
                        "event": "dashboard_chat_stream",
                        "phase": "stream_error",
                        "handler": "/api/chat/message",
                        "sessionId": session.session_id,
                        "profile": session.profile,
                        "model": model_name,
                        "durationMs": round((time.perf_counter() - started_at) * 1000.0, 2),
                        "browserVisibleErrorText": visible_error,
                        "error": str(exc),
                    },
                    ensure_ascii=True,
                    sort_keys=True,
                ),
            )
            contract = self._normalize_contract(
                session,
                {
                    "status": "error",
                    "intent": "error",
                    "text": visible_error,
                    "error": str(exc),
                    "citations": [],
                    "tool_calls": [],
                    "proposed_action": None,
                    "requires_confirmation": False,
                    "confirmation_token": None,
                    "snapshot": _snapshot_contract(snapshot),
                    "safety_notes": [],
                },
                tool_calls=[],
                snapshot=snapshot,
            )
            yield {"event": "final", "data": contract}
            return

        for tool_log in tool_logs:
            log.info(
                "dashboard_chat_stream %s",
                json.dumps(
                    {
                        "event": "dashboard_chat_stream",
                        "phase": "tool_event",
                        "handler": "/api/chat/message",
                        "sessionId": session.session_id,
                        "toolName": str(tool_log.get("name", "")).strip(),
                        "toolStatus": str(tool_log.get("status", "")).strip(),
                    },
                    ensure_ascii=True,
                    sort_keys=True,
                ),
            )
            yield {"event": "tool", "data": tool_log}
        text = str(contract.get("text", ""))
        first_token_logged = False
        if text:
            for index in range(0, len(text), 80):
                if not first_token_logged:
                    first_token_logged = True
                    yield {
                        "event": "status",
                        "data": {
                            "phase": "first_token",
                            "handler": "/api/chat/message",
                            "sessionId": session.session_id,
                            "model": model_name,
                            "firstTokenMs": round((time.perf_counter() - started_at) * 1000.0, 2),
                            "toolCallsSeen": len(tool_logs),
                        },
                    }
                yield {"event": "delta", "data": {"text": text[index : index + 80]}}
        session.transcript.append({"role": "user", "text": user_text})
        session.transcript.append({"role": "assistant", "text": str(contract.get("text", ""))})
        session.transcript = session.transcript[-20:]
        self._update_session_memory(session, self.queries.snapshot())
        log.info(
            "dashboard_chat_stream %s",
            json.dumps(
                {
                    "event": "dashboard_chat_stream",
                    "phase": "stream_complete",
                    "handler": "/api/chat/message",
                    "sessionId": session.session_id,
                    "profile": session.profile,
                    "model": model_name,
                    "durationMs": round((time.perf_counter() - started_at) * 1000.0, 2),
                    "toolCallsSeen": len(tool_logs),
                    "finalAssistantText": text,
                },
                ensure_ascii=True,
                sort_keys=True,
            ),
        )
        yield {
            "event": "status",
            "data": {
                "phase": "stream_complete",
                "handler": "/api/chat/message",
                "sessionId": session.session_id,
                "model": model_name,
                "streamEndMs": round((time.perf_counter() - started_at) * 1000.0, 2),
                "toolCallsSeen": len(tool_logs),
            },
        }
        yield {"event": "final", "data": contract}

    def confirm_action(self, session_id: str, confirmation_token: str) -> dict[str, Any]:
        session = self._get_session(session_id)
        token = str(confirmation_token).strip()
        with self._lock:
            pending = session.pending_actions.pop(token, None)
        if pending is None:
            raise KeyError(f"unknown confirmation token: {token}")
        tool = pending.tool
        args = dict(pending.args)
        if tool == "pause_automation":
            result = self.controls.pause_automation(reason=str(args.get("reason", "")).strip())
        elif tool == "stop_automation":
            result = self.controls.stop_automation(reason=str(args.get("reason", "")).strip())
        elif tool == "resume_automation":
            result = self.controls.resume_automation(reason=str(args.get("reason", "")).strip())
        elif tool == "start_goal":
            result = self.controls.start_goal(str(args.get("goal_id", "")).strip(), reason=str(args.get("reason", "")).strip())
        else:
            raise ValueError(f"unsupported confirmed tool: {tool}")
        snapshot = self.queries.snapshot()
        self._update_session_memory(session, snapshot, last_confirmed_control_action=tool)
        contract = self._normalize_contract(
            session,
            {
                "status": "ok",
                "intent": "action_result",
                "text": f"Executed {tool}.",
                "citations": [],
                "tool_calls": [_tool_call_summary(tool, args, "ok", f"{tool} executed")],
                "proposed_action": None,
                "requires_confirmation": False,
                "confirmation_token": None,
                "snapshot": _snapshot_contract(snapshot),
                "safety_notes": ["Preview remains read-only; confirmed actions run through local scripts/state only."],
                "error": None,
            },
            tool_calls=[_tool_call_summary(tool, args, "ok", f"{tool} executed")],
            snapshot=snapshot,
        )
        session.transcript.append({"role": "assistant", "text": str(contract.get("text", ""))})
        session.transcript = session.transcript[-20:]
        return contract
