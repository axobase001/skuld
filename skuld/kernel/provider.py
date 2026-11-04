"""Fail-closed OpenAI-compatible model organ for the V1.7 product runtime."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

import httpx

from skuld.kernel.llm_client import parse_json_response
from skuld.kernel.admission.semantic_coordinates import ProviderSchemaAdapter


# Copied verbatim in S1 from skuld/legacy/runtime/cognitive_operations.py when skuld/legacy was removed.
@dataclass(frozen=True)
class ProviderResponse:
    """Normalized provider response without retaining secrets or raw headers."""

    parsed_output: Any
    raw_response: Any
    served_provider: str
    served_model: str
    provider_request_id: str
    prompt_tokens: int
    completion_tokens: int
    estimated_cost_usd: float
    retries: int = 0


def normalise_provider_identity(value: str) -> str:
    return "".join(ch for ch in value.lower() if ch.isalnum())


def response_provider_identity(raw: dict[str, Any]) -> dict[str, Any]:
    """Read actual Responses routing evidence, never the requested pin.

    OpenRouter documents opt-in metadata.endpoints.available[].selected.
    Missing evidence is UNKNOWN; conflicting/malformed evidence fails closed.
    """
    result = {"provider": None, "status": "UNKNOWN", "origin": "missing"}
    top = raw.get("provider")
    if top is not None and (not isinstance(top, str) or len(top) > 200):
        return {**result, "status": "MALFORMED"}
    top = top.strip() if isinstance(top, str) else None
    metadata = raw.get("openrouter_metadata")
    selected = []
    if metadata is not None:
        if not isinstance(metadata, dict):
            return {**result, "status": "MALFORMED"}
        endpoints = metadata.get("endpoints")
        if endpoints is not None:
            if not isinstance(endpoints, dict) or not isinstance(endpoints.get("available", []), list):
                return {**result, "status": "MALFORMED"}
            for endpoint in endpoints.get("available", []):
                if not isinstance(endpoint, dict) or type(endpoint.get("selected", False)) is not bool:
                    return {**result, "status": "MALFORMED"}
                if endpoint.get("selected") is True:
                    provider = endpoint.get("provider")
                    if not isinstance(provider, str) or not provider.strip() or len(provider) > 200:
                        return {**result, "status": "MALFORMED"}
                    selected.append(provider.strip())
    if len(selected) > 1 or top and selected and normalise_provider_identity(top) != normalise_provider_identity(selected[0]):
        return {**result, "status": "CONFLICT"}
    provider = selected[0] if selected else top
    if provider:
        return {"provider": provider, "status": "VERIFIED",
                "origin": "selected_endpoint" if selected else "response_field"}
    return result


class OpenAICompatibleProductProvider:
    """Synchronous provider used by canonical cognitive operations.

    The canonical pipelines own prompts, information-flow receipts and typed
    commits.  This adapter only performs transport and normalises provider
    metadata.  Invalid JSON is an explicit failure; it is never repaired by a
    second model or replaced with a rule result.
    """

    VERSION = "openai-compatible-product-provider/v1.7.0"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        provider_name: str,
        timeout_seconds: float = 60.0,
        provider_only: tuple[str, ...] = (),
        allow_provider_fallbacks: bool = True,
        require_provider_parameters: bool = False,
        minimum_request_interval_seconds: float = 0.0,
        reasoning_effort: str | None = None,
    ) -> None:
        if not api_key or not base_url or not provider_name:
            raise ValueError("product provider configuration is incomplete")
        self._api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.provider_name = provider_name
        self.timeout_seconds = float(timeout_seconds)
        cleaned_provider_only = tuple(
            dict.fromkeys(value.strip() for value in provider_only if value.strip())
        )
        if len(cleaned_provider_only) != len(provider_only):
            raise ValueError(
                "provider routing allowlist contains empty or duplicate tags"
            )
        self.provider_only = cleaned_provider_only
        self.allow_provider_fallbacks = bool(allow_provider_fallbacks)
        self.require_provider_parameters = bool(require_provider_parameters)
        self.minimum_request_interval_seconds = float(minimum_request_interval_seconds)
        if self.minimum_request_interval_seconds < 0:
            raise ValueError("minimum request interval cannot be negative")
        self._last_request_started_monotonic: float | None = None
        cleaned_reasoning_effort = (reasoning_effort or "").strip().lower()
        allowed_reasoning_efforts = {
            "none",
            "minimal",
            "low",
            "medium",
            "high",
            "xhigh",
            "max",
        }
        if (
            cleaned_reasoning_effort
            and cleaned_reasoning_effort not in allowed_reasoning_efforts
        ):
            raise ValueError("unsupported reasoning effort")
        self.reasoning_effort = cleaned_reasoning_effort or None
        self.conversation_semantic_operations: list[dict[str, Any]] = []
        self.semantic_ir_operations: list[dict[str, Any]] = []

    @property
    def provider_routing(self) -> dict[str, Any] | None:
        if not self.provider_only:
            return None
        return {
            "only": list(self.provider_only),
            "allow_fallbacks": self.allow_provider_fallbacks,
            "require_parameters": self.require_provider_parameters,
        }

    @staticmethod
    def _normalise_provider_identity(value: str) -> str:
        return normalise_provider_identity(value)

    def _apply_provider_routing(self, payload: dict[str, Any]) -> None:
        routing = self.provider_routing
        if routing is not None:
            payload["provider"] = routing

    def _apply_reasoning_policy(self, payload: dict[str, Any]) -> None:
        if self.reasoning_effort is not None:
            payload["reasoning"] = {"effort": self.reasoning_effort}

    def _served_provider(self, raw: dict[str, Any]) -> tuple[str, bool]:
        identity = response_provider_identity(raw)
        if identity["status"] in ("CONFLICT", "MALFORMED"):
            raise ValueError("conflicting or malformed served provider identity")
        served = identity["provider"] or ""
        if not self.provider_only:
            return served or self.provider_name, True
        if not served:
            raise ValueError(
                "routed provider response omitted served provider identity"
            )
        allowed = {
            self._normalise_provider_identity(tag.split("/", 1)[0])
            for tag in self.provider_only
        }
        if self._normalise_provider_identity(served) not in allowed:
            raise ValueError(
                "served provider is outside the requested routing allowlist"
            )
        return served, True

    def _pace_request(self) -> None:
        if self._last_request_started_monotonic is not None:
            elapsed = time.monotonic() - self._last_request_started_monotonic
            remaining = self.minimum_request_interval_seconds - elapsed
            if remaining > 0:
                time.sleep(remaining)
        self._last_request_started_monotonic = time.monotonic()

    @property
    def endpoint(self) -> str:
        suffix = "/chat/completions"
        if self.base_url.endswith("/v1"):
            return self.base_url + suffix
        return self.base_url + "/v1" + suffix

    def __call__(
        self,
        model: str,
        prompt: str,
        output_schema: dict[str, Any],
        *,
        strict_output_schema: bool = False,
        max_tokens: int = 4096,
    ) -> ProviderResponse:
        if not model or not str(prompt).strip() or not output_schema:
            raise ValueError("canonical provider call is incomplete")
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Return one JSON value conforming to the supplied "
                        "schema. Do not wrap it in prose or markdown."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {"prompt": prompt, "output_schema": output_schema},
                        ensure_ascii=False,
                        sort_keys=True,
                        default=str,
                    ),
                },
            ],
            "temperature": 0.0,
            "max_tokens": int(max_tokens),
            "response_format": (
                {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "semantic_event_ir",
                        "strict": True,
                        "schema": output_schema,
                    },
                }
                if strict_output_schema
                else {"type": "json_object"}
            ),
        }
        if self.provider_name.strip().lower() == "deepseek":
            payload.update(
                {
                    "thinking": {"type": "disabled"},
                }
            )
        self._apply_provider_routing(payload)
        self._apply_reasoning_policy(payload)
        self._pace_request()
        response = httpx.post(
            self.endpoint,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        raw = response.json()
        choice = dict(raw["choices"][0])
        content = str(choice["message"]["content"] or "")
        parsed = parse_json_response(content)
        if parsed is None:
            finish_reason = str(choice.get("finish_reason") or "UNKNOWN")
            native_finish_reason = str(
                choice.get("native_finish_reason") or "UNKNOWN"
            )
            raise ValueError(
                "product provider returned invalid JSON "
                f"(finish_reason={finish_reason}, "
                f"native_finish_reason={native_finish_reason})"
            )
        usage = dict(raw.get("usage") or {})
        request_id = str(
            raw.get("id")
            or response.headers.get("x-request-id")
            or response.headers.get("request-id")
            or "response-" + sha256(response.content).hexdigest()[:24]
        )
        served_provider, _route_verified = self._served_provider(raw)
        return ProviderResponse(
            parsed_output=parsed,
            raw_response=raw,
            served_provider=served_provider,
            served_model=str(raw.get("model") or model),
            provider_request_id=request_id,
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            estimated_cost_usd=float(usage.get("cost") or 0.0),
            retries=0,
        )

    def complete_text(
        self,
        *,
        model: str,
        system_prompt: str,
        messages: list[dict[str, str]],
        temperature: float = 0.3,
        max_tokens: int = 1800,
    ) -> tuple[str, dict[str, Any]]:
        """Generate an ordinary product answer without a hidden substitute."""

        if not model or not system_prompt or not messages:
            raise ValueError("product conversation call is incomplete")
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                *messages,
            ],
            "temperature": float(temperature),
            "max_tokens": int(max_tokens),
        }
        self._apply_provider_routing(payload)
        self._apply_reasoning_policy(payload)
        self._pace_request()
        response = httpx.post(
            self.endpoint,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        raw = response.json()
        content = str(raw["choices"][0]["message"]["content"] or "").strip()
        if not content:
            raise ValueError("product provider returned an empty answer")
        usage = dict(raw.get("usage") or {})
        served_provider, route_verified = self._served_provider(raw)
        return content, {
            "provider": served_provider,
            "requested_provider": self.provider_name,
            "provider_routing": self.provider_routing,
            "provider_route_verified": route_verified,
            "minimum_request_interval_seconds": self.minimum_request_interval_seconds,
            "reasoning_effort_requested": self.reasoning_effort,
            "requested_model": model,
            "served_model": str(raw.get("model") or model),
            "provider_request_id": str(
                raw.get("id")
                or response.headers.get("x-request-id")
                or response.headers.get("request-id")
                or "response-" + sha256(response.content).hexdigest()[:24]
            ),
            "prompt_tokens": int(usage.get("prompt_tokens") or 0),
            "completion_tokens": int(usage.get("completion_tokens") or 0),
            "fallback_used": False,
        }

    def request_json_object_raw(
        self,
        *,
        model: str,
        system_prompt: str,
        user_payload: dict[str, Any],
        max_tokens: int = 8192,
    ) -> dict[str, Any]:
        """Make one JSON-object attempt while preserving invalid raw output."""

        if not model or not system_prompt or not user_payload:
            raise ValueError("semantic JSON provider call is incomplete")
        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(
                        user_payload,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                },
            ],
            "temperature": 0.0,
            "max_tokens": int(max_tokens),
            "response_format": {"type": "json_object"},
        }
        if self.provider_name.strip().lower() == "deepseek":
            payload["thinking"] = {"type": "disabled"}
        self._apply_provider_routing(payload)
        self._apply_reasoning_policy(payload)
        self._pace_request()
        response = httpx.post(
            self.endpoint,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        raw = response.json()
        choice = dict(raw["choices"][0])
        content = str(choice["message"]["content"] or "")
        usage = dict(raw.get("usage") or {})
        completion_details = dict(usage.get("completion_tokens_details") or {})
        request_id = str(
            raw.get("id")
            or response.headers.get("x-request-id")
            or response.headers.get("request-id")
            or "response-" + sha256(response.content).hexdigest()[:24]
        )
        served_provider, route_verified = self._served_provider(raw)
        return {
            "raw_output": content,
            "parsed_output": parse_json_response(content),
            "requested_provider": self.provider_name,
            "served_provider": served_provider,
            "provider_routing": self.provider_routing,
            "provider_route_verified": route_verified,
            "minimum_request_interval_seconds": self.minimum_request_interval_seconds,
            "reasoning_effort_requested": self.reasoning_effort,
            "served_model": str(raw.get("model") or model),
            "provider_request_id": request_id,
            "prompt_tokens": int(usage.get("prompt_tokens") or 0),
            "completion_tokens": int(usage.get("completion_tokens") or 0),
            "reasoning_tokens": int(completion_details.get("reasoning_tokens") or 0),
            "finish_reason": str(choice.get("finish_reason") or ""),
            "native_finish_reason": str(choice.get("native_finish_reason") or ""),
            "estimated_cost_usd": float(usage.get("cost") or 0.0),
            "fallback_used": False,
        }

    def propose_conversation_semantics(
        self,
        *,
        model: str,
        conversation_turn: dict[str, Any],
        semantic_context: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Let the live model propose spans; deterministic gates retain authority."""

        raw_content = str(conversation_turn.get("raw_content") or "")
        # Compatibility for direct provider callers predating V1.7.4. Product
        # assembly always supplies the persisted receipt prompt view.
        semantic_context = dict(semantic_context or {})
        context_candidates = list(semantic_context.get("candidate_objects") or ())
        if len(context_candidates) > 32:
            raise ValueError("semantic context candidate bound exceeded")
        serialized_context = json.dumps(
            semantic_context, ensure_ascii=False, sort_keys=True
        )
        for forbidden in (
            "canonical_object_id",
            "approval_token",
            "connector_credential",
            "api_key",
            "password",
        ):
            if forbidden in serialized_context.lower():
                raise PermissionError(
                    f"semantic context contains forbidden field: {forbidden}"
                )
        operation_id = "live-conversation-semantics:" + str(
            conversation_turn.get("id") or ""
        )
        schema = {
            "type": "object",
            "additionalProperties": False,
            "required": ["candidates"],
            "properties": {
                "candidates": {
                    "type": "array",
                    "maxItems": 8,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [
                            "proposal_type",
                            "exact_evidence_span",
                            "span_start",
                            "span_end",
                            "normalized_proposition",
                            "modality",
                            "negation",
                            "confidence",
                        ],
                        "properties": {
                            "proposal_type": {
                                "type": "string",
                                "enum": [
                                    "USER_ASSERTION",
                                    "USER_GOAL",
                                    "USER_COMMITMENT",
                                    "USER_CONSTRAINT",
                                    "USER_CORRECTION",
                                    "USER_CONFIRMATION",
                                    "USER_DENIAL",
                                    "USER_PREFERENCE",
                                    "USER_PERMISSION_GRANT",
                                    "USER_PERMISSION_REVOKE",
                                    "OUTCOME_REPORT",
                                    "ACTION_REQUEST",
                                    "QUESTION",
                                    "HYPOTHETICAL",
                                    "QUOTED_CLAIM",
                                    "THIRD_PARTY_CLAIM",
                                    "CASUAL_CHAT",
                                ],
                            },
                            "exact_evidence_span": {"type": "string"},
                            "span_start": {"type": "integer"},
                            "span_end": {"type": "integer"},
                            "normalized_proposition": {"type": "object"},
                            "modality": {"type": "string"},
                            "negation": {"type": "boolean"},
                            "confidence": {"type": "number"},
                            "candidate_target_refs": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "requires_corroboration": {"type": "boolean"},
                            "requires_clarification": {"type": "boolean"},
                        },
                    },
                }
            },
        }
        prompt = (
            "Propose atomic semantics for the exact verified-user turn below. "
            "Copy each exact_evidence_span byte-for-byte from the turn and give "
            "Python-style Unicode character offsets. Preserve negation. Questions, "
            "hypotheticals, quotes, roleplay, and casual chat must use their "
            "non-effect proposal type. Never infer approval. A correction or "
            "outcome without a known canonical target must require clarification. "
            "Choose only candidate_target_refs present in BOUNDED_CONTEXT; never "
            "invent or output an opaque canonical identifier. "
            "The model proposes only; deterministic code decides commit. "
            "Each normalized proposition must include text and semantic_key. "
            f"BOUNDED_CONTEXT={serialized_context}\n"
            f"TURN={json.dumps(raw_content, ensure_ascii=False)}"
        )
        started = time.perf_counter()
        response = self(model, prompt, schema)
        elapsed_ms = round((time.perf_counter() - started) * 1000.0, 3)
        parsed = response.parsed_output
        candidates = (
            list(parsed.get("candidates") or ()) if isinstance(parsed, dict) else []
        )
        normalized: list[dict[str, Any]] = []
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            value = dict(candidate)
            proposal_type = str(value.get("proposal_type") or "").upper()
            value.setdefault("speech_act", proposal_type.lower())
            value.setdefault("subject_role", "modeled_subject")
            value.setdefault("entity_mentions", [])
            value.setdefault("temporal_scope", {})
            value.setdefault("candidate_target_refs", [])
            value.setdefault("requires_corroboration", False)
            value.setdefault("requires_clarification", False)
            normalized.append(value)
        raw_hash = sha256(
            json.dumps(
                response.raw_response,
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            ).encode("utf-8")
        ).hexdigest()
        self.conversation_semantic_operations.append(
            {
                "operation_id": operation_id,
                "provider": response.served_provider,
                "served_model": response.served_model,
                "provider_request_id": response.provider_request_id,
                "prompt_policy_version": ("live-conversation-semantic-proposal/v1.7.4"),
                "semantic_context_hash": semantic_context.get("semantic_content_hash"),
                "raw_parsed_output_hash": raw_hash,
                "candidate_count": len(normalized),
                "latency_ms": elapsed_ms,
                "fallback_used": False,
            }
        )
        return normalized

    def compile_semantic_event_ir(
        self,
        *,
        model: str,
        system_prompt: str,
        raw_text: str,
        response_schema: dict[str, Any],
        turn_id: str,
    ) -> str:
        """Compile only lossless turn-local IR; deterministic code owns commit."""

        provider_profile = ProviderSchemaAdapter.profile_for_provider(
            self.provider_name
        )
        wire_schema, adapter_receipt = ProviderSchemaAdapter.adapt(
            response_schema,
            provider_profile=provider_profile,
        )
        prompt = (
            system_prompt
            + "\n\nTURN_ID="
            + json.dumps(turn_id, ensure_ascii=False)
            + "\nRAW_HUMAN_MESSAGE="
            + json.dumps(raw_text, ensure_ascii=False)
        )
        response = self(
            model,
            prompt,
            wire_schema,
            strict_output_schema=True,
            max_tokens=8192,
        )
        self.semantic_ir_operations.append(
            {
                "turn_id": turn_id,
                "served_provider": response.served_provider,
                "served_model": response.served_model,
                "provider_request_id": response.provider_request_id,
                "prompt_tokens": response.prompt_tokens,
                "completion_tokens": response.completion_tokens,
                "estimated_cost_usd": response.estimated_cost_usd,
                "fallback_used": False,
                "primary_semantic_call_count": 1,
                "schema_adapter_receipt": adapter_receipt.model_dump(mode="json"),
            }
        )
        return response.parsed_output


__all__ = ["OpenAICompatibleProductProvider"]
