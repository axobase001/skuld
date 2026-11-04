"""S5 model routes for the simulator.
- offline_config / TemplateProvider: no model at all. The template provider answers every request
  from the request itself (GATE_S5 §4: the qualification pre-check runs A and B on it, at no cost).
- real_config: the product default route (deepseek-flash), key and base URL from the environment.
- Paraphraser: the LLM that turns a scripted intent into the user's words. It sees the intent text and
  nothing else; answers are cached in a file, so paired arms hear the same words."""
import json
import os
import threading
from pathlib import Path

import httpx

MODEL = "deepseek-flash"   # the V4.0 default route (卓冉 2026-10-23)
OFFLINE_ROUTE = {"key": "offline-sim-key-never-sent", "base_url": "https://unused.invalid/v1", "model": "scripted-model", "provider": "scripted"}
COMMON = {"simulation_only": True, "product_provider_enabled": True, "product_use_runtime_clock": True}


def offline_config(max_calls=100_000):
    return {"gateway_key": "skuld-sim-offline", "default_route": "scripted", "max_model_calls": max_calls,
            "routes": {"scripted": dict(OFFLINE_ROUTE)}, **COMMON}


def real_config(max_calls=4000):
    return {"gateway_key": "skuld-sim", "default_route": "deepseek", "max_model_calls": max_calls,
            "routes": {"deepseek": {"key": os.environ["DEEPSEEK_API_KEY"], "base_url": os.environ["DEEPSEEK_BASE_URL"],
                                    "model": MODEL, "provider": "deepseek"}}, **COMMON}


def completion(answer):
    body = {"id": "template", "model": "template", "provider": "template",
            "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(answer, ensure_ascii=False)}}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "cost": 0}}
    return {"http_status": 200, "body": json.dumps(body)}


class TemplateProvider:
    """A model stand-in that only fills templates from what each request supplies: no forecast, no
    inference; for a REASON proposal, one PREPARE of the first goal that restates the observed report
    (key and value) of the report that triggered it. Records every request."""

    def __init__(self):
        self.requests = []

    def __call__(self, endpoint, key, payload, *rest):
        self.requests.append(payload)
        task = json.loads(payload["messages"][-1]["content"])
        if "confirm" in task:
            return completion({"confirmed": list(range(len(task["confirm"])))})
        if "utterance" in task:
            return completion({"reply": "好的，我记下了。", "statements": []})
        inner = json.loads(task["task"]) if isinstance(task.get("task"), str) else {}
        if "observable_contracts" in inner:
            return completion({"hypotheses": [], "wait_reason": "template provider: no forecast", "inferences": []})
        if "choices" in inner:
            goals = [g["node_id"] for g in inner.get("goals", [])][:1]
            if not goals or "PREPARE" not in inner["choices"]:
                return completion({"candidates": [], "wait_reason": "template provider: nothing to prepare"})
            goal = next(g for g in inner["goals"] if g["node_id"] == goals[0])
            beliefs = [e for e in inner.get("evidence", []) if e.get("node_type") == "Belief"]
            seen = set(inner.get("observed_evidence_ids") or [])
            # the report that triggered this round (else the most recently observed one), not every report offered
            reports = [e for e in beliefs if e["node_id"] in seen] or sorted(beliefs, key=lambda e: str(e["content"].get("observed_at")))[-1:]
            text = "更新：" + goal["content"].get("text", "") + "。" + "；".join(f"{e['content'].get('key')}：{e['content'].get('value')}" for e in reports)
            return completion({"candidates": [{"kind": "PREPARE", "title": "更新后的材料", "content": text, "rationale": "template",
                                               "goal_ids": goals, "prediction_ids": [],
                                               "evidence_ids": goals + [e["node_id"] for e in inner.get("evidence", [])],
                                               "expected_benefit": 0.7, "expected_usefulness_probability": 0.6, "uncertainty_reduction": 0.1,
                                               "cost": 0.05, "risk": 0.05, "source_id": None, "calculations": []}], "wait_reason": ""})
        return completion({"facts": []})


class Paraphraser:
    """The user's words: a paraphrase of one intent, by the default model at temperature 0, cached."""
    SYSTEM = "你在扮演一位普通用户。把下面这句话换一种自然的口语说法，意思不变，不添加任何新信息。只输出改写后的那一句话。"

    def __init__(self, cache_path):
        self.path, self.lock = Path(cache_path), threading.Lock()
        self.cache = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        self.calls, self.prompt_tokens, self.completion_tokens, self.failures = 0, 0, 0, 0

    def __call__(self, intent):
        with self.lock:
            if intent in self.cache:
                return self.cache[intent]
        try:
            r = httpx.post(os.environ["DEEPSEEK_BASE_URL"].rstrip("/") + "/chat/completions", timeout=60,
                           headers={"Authorization": "Bearer " + os.environ["DEEPSEEK_API_KEY"]},
                           json={"model": MODEL, "temperature": 0, "max_tokens": 120,
                                 "messages": [{"role": "system", "content": self.SYSTEM}, {"role": "user", "content": intent}]})
            r.raise_for_status()
            body = r.json()
            text = body["choices"][0]["message"]["content"].strip() or intent
            usage = body.get("usage") or {}
            with self.lock:
                self.calls += 1
                self.prompt_tokens += int(usage.get("prompt_tokens") or 0)
                self.completion_tokens += int(usage.get("completion_tokens") or 0)
        except (httpx.HTTPError, KeyError, ValueError, IndexError):
            with self.lock:
                self.failures += 1
            return intent
        with self.lock:
            self.cache[intent] = text
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.cache, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, self.path)
        return text


__all__ = ["offline_config", "real_config", "TemplateProvider", "Paraphraser", "MODEL"]
