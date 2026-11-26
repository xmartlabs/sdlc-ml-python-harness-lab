"""Direct text-only LM Studio adapter; never loads, switches or downloads models."""

from __future__ import annotations

import urllib.error
from dataclasses import dataclass
from typing import TYPE_CHECKING

from harness_lab_app.direct_hosts import DirectHostUnreachable, get_json, host_root
from harness_lab_app.gameplay.contracts import PolicyReply
from harness_lab_app.lmstudio_direct import bare_model_id
from harness_lab_app.local_tuning.servers import LMStudioServer

if TYPE_CHECKING:
    from pydantic import JsonValue

    from harness_lab_app.gameplay.contracts import Observation


@dataclass
class LMStudioPolicy:
    server: LMStudioServer
    model_id: str
    instance_id: str
    served_config: dict[str, JsonValue]
    model_facts: dict[str, JsonValue]

    @classmethod
    def connect(cls, base_url: str, model_id: str) -> LMStudioPolicy:
        """Require an already loaded instance, using existing native discovery."""
        server = LMStudioServer(host_root(base_url))
        bare = bare_model_id(model_id)
        model = next((m for m in server.list_models() if m.id == bare), None)
        if model is None:
            raise ValueError("Unknown LM Studio model; use its exact inventory id")
        if not model.loaded or not model.instance_ids:
            raise ValueError("Model must already be loaded in LM Studio")
        served = model.served.model_dump(mode="json") if model.served else {}
        return cls(
            server,
            bare,
            model.instance_ids[0],
            served,
            {
                "quantization": model.quantization,
                "arch": model.arch,
                "size_bytes": model.size_bytes,
                "max_context": model.max_context,
            },
        )

    @property
    def provenance(self) -> dict[str, JsonValue]:
        return {
            "provider": "lmstudio-direct",
            "requested_model": self.model_id,
            "instance_id": self.instance_id,
            "served_config": self.served_config,
            "model_facts": self.model_facts,
            "effective_model": None,
            "effective_temperature": None,
            "requested_temperature": 0,
            "reasoning_effort": "none",
            "response_format": "json_schema",
            "policy_version": "structured-action.v2",
            "max_output_tokens": 512,
        }

    def __call__(self, observation: Observation, timeout: float) -> PolicyReply:
        control_schema = {
            name: {
                "type": "integer" if bounds.integer else "number",
                "minimum": bounds.minimum,
                "maximum": bounds.maximum,
            }
            for name, bounds in observation.controls.items()
        }
        schema = {
            "type": "object",
            "properties": {
                "version": {"type": "string", "const": "gameplay.v1"},
                "observation_id": {
                    "type": "string",
                    "const": observation.observation_id,
                },
                "controls": {
                    "type": "object",
                    "properties": control_schema,
                    "required": list(control_schema),
                    "additionalProperties": False,
                },
            },
            "required": ["version", "observation_id", "controls"],
            "additionalProperties": False,
        }
        prompt = (
            "Play the fixed environment using only its visible state. "
            "Follow observation.instructions. Choose every advertised control. "
            "Controls last ticks_to_advance simulation ticks; game time pauses "
            "during inference. Return only the requested JSON action, no explanation.\n"
            "OBSERVATION\n" + observation.model_dump_json()
        )
        try:
            payload = get_json(
                f"{self.server.base_url}/v1/chat/completions",
                timeout,
                {
                    "model": self.instance_id,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 512,
                    "temperature": 0,
                    "reasoning_effort": "none",
                    "stream": False,
                    "response_format": {
                        "type": "json_schema",
                        "json_schema": {
                            "name": "game_action",
                            "strict": True,
                            "schema": schema,
                        },
                    },
                },
            )
        except (TimeoutError, urllib.error.URLError) as exc:
            if isinstance(exc, TimeoutError) or isinstance(
                getattr(exc, "reason", None), TimeoutError
            ):
                raise TimeoutError("LM Studio decision timed out") from exc
            raise DirectHostUnreachable("LM Studio completion unavailable") from exc
        if payload.get("error") or not payload.get("choices"):
            raise RuntimeError("LM Studio completion failed")
        choice = payload["choices"][0]
        content = choice.get("message", {}).get("content") or ""
        usage = payload.get("usage") or {}
        details = usage.get("completion_tokens_details") or {}
        return PolicyReply(
            action_json=content,
            provenance={
                **self.provenance,
                "prompt_tokens": usage.get("prompt_tokens"),
                "completion_tokens": usage.get("completion_tokens"),
                "reasoning_tokens": details.get("reasoning_tokens"),
                "finish_reason": choice.get("finish_reason"),
                "response_model": payload.get("model"),
            },
        )
