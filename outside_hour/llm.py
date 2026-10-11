"""Talk to a local llama.cpp server (llama-server) running an open-weight Gemma model.

The output is constrained by a JSON schema (llama.cpp turns it into a grammar),
so the model can only pick a place_id that we actually fetched. Its wording is
then checked again in validate.py.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

SYSTEM = (
    "You help someone spend a short time outdoors. You get JSON facts: the time they have, the weather, "
    "warnings, and a few nearby candidate places from OpenStreetMap with walking times. "
    "Pick the ONE candidate that best suits the weather and time, and write a short, warm plan card.\n"
    "Rules:\n"
    "- Only use places and numbers that appear in the facts. Never invent a place, street, shop or number.\n"
    "- Mention only the chosen place by name.\n"
    "- headline: under 60 characters. why: one or two short, complete sentences on why this place fits now.\n"
    "- Write plain English only. Speak to the reader as 'you'; never say we, us, me or let's.\n"
    "- notice: exactly three small, specific things to look, listen or feel for at the chosen place, in this "
    "weather and at this time of day. Choose and rephrase from notice_ideas for the chosen place's kind as "
    "full sentences, without repeating the place name; do not assume water, views or animals the facts don't mention. Each must be doable without a phone.\n"
    "- phone_down_line: one complete sentence of under 15 words, kind, encouraging them to put the phone away. Do not put a time in it; "
    "the app adds the turn-around time itself.\n"
    "- If there is a warning, respect it in your wording."
)


class ModelUnavailable(RuntimeError):
    pass


def schema_for(candidate_ids: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "place_id": {"type": "string", "enum": candidate_ids},
            "headline": {"type": "string", "minLength": 5, "maxLength": 70},
            "why": {"type": "string", "minLength": 10, "maxLength": 220},
            "notice": {
                "type": "array",
                "items": {"type": "string", "minLength": 5, "maxLength": 110},
                "minItems": 3,
                "maxItems": 3,
            },
            "phone_down_line": {"type": "string", "minLength": 5, "maxLength": 140},
        },
        "required": ["place_id", "headline", "why", "notice", "phone_down_line"],
    }


class LlamaServer:
    def __init__(self, url: str | None = None, timeout: float = 180):
        self.url = (url or os.environ.get("OUTSIDE_HOUR_LLM_URL", "http://127.0.0.1:8088")).rstrip("/")
        self.timeout = timeout
        self._model = None

    @property
    def label(self) -> str:
        return f"{self.model_name()} (local, llama.cpp)"

    def model_name(self) -> str:
        if self._model is None:
            try:
                with urllib.request.urlopen(self.url + "/v1/models", timeout=5) as r:
                    d = json.loads(r.read().decode())
                mid = d["data"][0]["id"]
                self._model = os.path.basename(mid).removesuffix(".gguf")
            except Exception:
                self._model = "local model"
        return self._model

    def _post(self, path: str, body: dict) -> dict:
        req = urllib.request.Request(self.url + path, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read().decode())
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            raise ModelUnavailable(str(e)) from e

    def write_plan(self, facts: dict) -> tuple[dict, dict]:
        ids = [c["id"] for c in facts["candidates"]]
        messages = [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": json.dumps(facts, ensure_ascii=False, separators=(",", ":"))},
        ]
        t = time.perf_counter()
        # 1) Let llama-server format the conversation with the model's own chat template.
        prompt = self._post("/apply-template", {"messages": messages,
                                                "chat_template_kwargs": {"enable_thinking": False}})["prompt"]
        # 2) Complete it under a JSON-schema grammar that applies from the very first token. (Through the
        #    chat endpoint, Gemma 4 sometimes starts a long "thinking" channel first, which costs ~45 s on
        #    a CPU and bypasses the grammar.) The enum means place_id can only be a place we fetched.
        d = self._post("/completion", {
            "prompt": prompt,
            "json_schema": schema_for(ids),
            "n_predict": 400,
            "temperature": 0.4,
            "cache_prompt": True,
        })
        wall = time.perf_counter() - t
        text = d.get("content", "")
        tm = d.get("timings", {})
        stats = {
            "model": self.model_name(),
            "wall_s": round(wall, 2),
            "prompt_tokens": tm.get("prompt_n"),
            "cached_prompt_tokens": tm.get("cache_n"),
            "prompt_tok_per_s": round(tm["prompt_per_second"], 1) if tm.get("prompt_per_second") else None,
            "output_tokens": tm.get("predicted_n"),
            "output_tok_per_s": round(tm["predicted_per_second"], 1) if tm.get("predicted_per_second") else None,
        }
        try:
            return json.loads(text), stats
        except json.JSONDecodeError:
            return {"_raw": text[:300]}, stats
