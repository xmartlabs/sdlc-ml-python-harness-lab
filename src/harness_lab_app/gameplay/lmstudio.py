"""Direct text-only LM Studio adapter; never loads, switches or downloads models."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from harness_lab_app.direct_hosts import DirectHostUnreachable, host_root
from harness_lab_app.gameplay.contracts import Action, PolicyReply
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
            "max_output_tokens": 512,
        }

    def __call__(self, observation: Observation, timeout: float) -> PolicyReply:
        prompt = (
            "Play the fixed environment using only its visible state. "
            "Follow observation.instructions. Controls are held for "
            "ticks_to_advance simulation ticks; simulation pauses during inference. "
            "Return ONLY one JSON object matching ACTION_SCHEMA, with the exact "
            "observation_id and every advertised control. No markdown or explanation.\n"
            "ACTION_SCHEMA\n" + json.dumps(Action.model_json_schema()) + "\n"
            "OBSERVATION\n" + observation.model_dump_json()
        )
        try:
            completion = self.server.complete(
                self.instance_id, prompt, max_tokens=512, timeout=timeout
            )
        except DirectHostUnreachable as exc:
            cause = exc.__cause__
            if isinstance(cause, TimeoutError) or isinstance(
                getattr(cause, "reason", None), TimeoutError
            ):
                raise TimeoutError("LM Studio decision timed out") from exc
            raise
        if completion.error:
            raise RuntimeError("LM Studio completion failed")
        return PolicyReply(
            action_json=completion.text,
            provenance={
                **self.provenance,
                "prompt_tokens": completion.prompt_tokens,
                "completion_tokens": completion.completion_tokens,
            },
        )
