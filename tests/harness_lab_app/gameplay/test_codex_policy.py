"""Codex transport tests use an executable fixture, never a provider request."""

import json
import os
import sys
import time
from pathlib import Path

import pytest

from harness_lab_app.gameplay.contracts import ControlBounds, Observation


def observation():
    return Observation(
        episode_id="codex-test",
        observation_id="codex-test:12",
        tick=12,
        ticks_to_advance=60,
        tick_rate=60,
        instructions="Complete one lap using the visible state.",
        state={"speed": 4.0},
        controls={"steer": ControlBounds(minimum=-1.0, maximum=1.0)},
        terminal=False,
    )


def action():
    return json.dumps(
        {
            "version": "gameplay.v1",
            "observation_id": "codex-test:12",
            "controls": {"steer": 0.2},
        }
    )


def executable(tmp_path, body):
    path = tmp_path / "fake-codex"
    path.write_text(f"#!{sys.executable}\n" + body)
    path.chmod(0o700)
    return str(path)


def policy(tmp_path, body):
    from harness_lab_app.gameplay.codex_policy import CodexGameplayPolicy

    auth = tmp_path / "auth"
    auth.mkdir(exist_ok=True)
    (auth / "auth.json").write_text("{}")
    return CodexGameplayPolicy(
        auth_directory=auth, executable=executable(tmp_path, body)
    )


def test_isolated_json_action_and_token_provenance(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "not-a-real-key")
    monkeypatch.setenv("CODEX_API_KEY", "not-a-real-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://wrong-provider.invalid")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "not-a-real-token")
    receipt = tmp_path / "request.json"
    body = f"""
import json,os,sys
from pathlib import Path
args=sys.argv[1:]
schema=json.loads(Path(args[args.index('--output-schema')+1]).read_text())
prompt=sys.stdin.read()
Path({str(receipt)!r}).write_text(json.dumps({{
 'args':args,'cwd':os.getcwd(),'home':os.environ['HOME'],
 'codex_home':os.environ['CODEX_HOME'],
 'billing_keys':[k for k in ['OPENAI_API_KEY','CODEX_API_KEY','OPENAI_BASE_URL','ANTHROPIC_AUTH_TOKEN'] if k in os.environ],
 'schema':schema,'prompt':prompt,'files':sorted(p.name for p in Path.cwd().iterdir())
}}))
print(json.dumps({{'type':'thread.started','thread_id':'fake-thread'}}))
print(json.dumps({{'type':'item.completed','item':{{'type':'agent_message','text':{action()!r}}}}}))
print(json.dumps({{'type':'turn.completed','usage':{{'input_tokens':100,'cached_input_tokens':20,'output_tokens':30}}}}))
"""
    adapter = policy(tmp_path, body)
    reply = adapter(observation(), 3.0)
    request = json.loads(receipt.read_text())
    assert json.loads(reply.action_json) == json.loads(action())
    assert request["billing_keys"] == []
    assert request["cwd"] != os.getcwd()
    assert request["home"] != os.environ["HOME"]
    assert not Path(request["cwd"]).exists(), "Per-decision workspace must be removed"
    assert request["codex_home"] != str(tmp_path / "auth")
    assert not Path(request["codex_home"]).exists()
    assert "--ignore-user-config" in request["args"]
    assert "--ignore-rules" in request["args"]
    assert "--ephemeral" in request["args"]
    assert request["args"][request["args"].index("-m") + 1] == "gpt-6.1-sol"
    assert 'model_reasoning_effort="low"' in request["args"]
    for feature in [
        "shell_tool",
        "unified_exec",
        "apps",
        "multi_agent",
        "multi_agent_v2",
    ]:
        assert feature in request["args"]
    assert (
        request["schema"]["properties"]["observation_id"]["const"]
        == observation().observation_id
    )
    assert request["schema"]["properties"]["controls"]["additionalProperties"] is False
    assert json.loads(
        request["prompt"].split("OBSERVATION\n")[1]
    ) == observation().model_dump(mode="json")
    assert reply.provenance["prompt_tokens"] == 100
    assert reply.provenance["cached_prompt_tokens"] == 20
    assert reply.provenance["completion_tokens"] == 30
    assert reply.provenance["effective_model"] is None
    assert reply.provenance["response_model"] is None
    assert reply.provenance["billing_mode"] == "subscription"
    assert reply.provenance["max_output_tokens"] is None


@pytest.mark.parametrize(
    "kind",
    [
        "command_execution",
        "file_change",
        "mcp_tool_call",
        "web_search",
        "collab_tool_call",
    ],
)
def test_rejects_tool_use_even_when_final_action_exists(tmp_path, kind):
    body = f"""import json
print(json.dumps({{'type':'item.started','item':{{'type':{kind!r}}}}}))
print(json.dumps({{'type':'item.completed','item':{{'type':'agent_message','text':{action()!r}}}}}))
print(json.dumps({{'type':'turn.completed','usage':{{}}}}))
"""
    with pytest.raises(RuntimeError, match="tool"):
        policy(tmp_path, body)(observation(), 3.0)


@pytest.mark.parametrize(
    "body,match",
    [
        ("print('not json')", "malformed"),
        ('print(\'{"type":"turn.completed","usage":{}}\')', "action"),
        ('print(\'{"type":"turn.failed","error":{"message":"denied"}}\')', "failed"),
        ("import sys;sys.exit(2)", "failed"),
    ],
)
def test_rejects_failed_or_missing_response(tmp_path, body, match):
    with pytest.raises(RuntimeError, match=match):
        policy(tmp_path, body)(observation(), 3.0)


def test_timeout_terminates_process_group(tmp_path):
    child_pid = tmp_path / "child.pid"
    survived = tmp_path / "child-survived"
    body = f"""import subprocess,sys,time
from pathlib import Path
child_code="import time;from pathlib import Path;time.sleep(0.8);Path("+repr({str(survived)!r})+").write_text('survived')"
p=subprocess.Popen([sys.executable,'-c',child_code])
Path({str(child_pid)!r}).write_text(str(p.pid))
time.sleep(60)
"""
    with pytest.raises(TimeoutError, match="timed out"):
        policy(tmp_path, body)(observation(), 0.3)
    assert int(child_pid.read_text()) > 0
    time.sleep(0.9)
    assert not survived.exists(), "The child must not survive its process group timeout"


def test_excessive_output_stops_transport(tmp_path):
    with pytest.raises(RuntimeError, match="output"):
        policy(tmp_path, "import sys;sys.stdout.write('x'*3000000);sys.stdout.flush()")(
            observation(), 3.0
        )


def test_refuses_personal_configuration_and_missing_auth(tmp_path):
    from harness_lab_app.gameplay.codex_policy import CodexGameplayPolicy

    with pytest.raises(ValueError, match=r"auth\.json"):
        CodexGameplayPolicy(
            auth_directory=tmp_path / "missing", executable=sys.executable
        )
    (tmp_path / "auth.json").write_text("{}")
    (tmp_path / "AGENTS.md").write_text("Do not leak this.")
    with pytest.raises(ValueError, match="auth-only"):
        CodexGameplayPolicy(auth_directory=tmp_path, executable=sys.executable)


def test_invalid_action_is_not_silently_repaired(tmp_path):
    body = """import json
print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'not an action'}}))
print(json.dumps({'type':'turn.completed','usage':{}}))
"""
    reply = policy(tmp_path, body)(observation(), 3.0)
    assert reply.action_json == "not an action"


def test_request_matches_direct_local_policy_without_additional_game_hints(
    tmp_path, monkeypatch
):
    from harness_lab_app.gameplay.lmstudio import LMStudioPolicy
    from harness_lab_app.local_tuning.servers import LMStudioServer

    captured = []

    def completion(url, timeout, payload):
        captured.append(payload)
        return {"choices": [{"message": {"content": action()}}]}

    monkeypatch.setattr("harness_lab_app.gameplay.lmstudio.get_json", completion)
    direct = LMStudioPolicy(
        LMStudioServer("http://unused.invalid"), "fake", "fake", {}, {}
    )
    direct(observation(), 3.0)
    receipt = tmp_path / "payload.json"
    body = f"""import json,sys
from pathlib import Path
args=sys.argv[1:]
Path({str(receipt)!r}).write_text(json.dumps({{'prompt':sys.stdin.read(),'schema':json.loads(Path(args[args.index('--output-schema')+1]).read_text())}}))
print(json.dumps({{'type':'item.completed','item':{{'type':'agent_message','text':{action()!r}}}}}))
print(json.dumps({{'type':'turn.completed','usage':{{}}}}))
"""
    policy(tmp_path, body)(observation(), 3.0)
    codex = json.loads(receipt.read_text())
    assert codex["prompt"] == captured[0]["messages"][0]["content"]
    assert codex["schema"] == captured[0]["response_format"]["json_schema"]["schema"]


def test_cli_generated_configuration_never_leaks_into_later_decisions(tmp_path):
    receipt = tmp_path / "auth-inputs.jsonl"
    body = f"""import json,os
from pathlib import Path
home=Path(os.environ['CODEX_HOME'])
with Path({str(receipt)!r}).open('a') as f:
 f.write(json.dumps(sorted(p.name for p in home.iterdir()))+'\\n')
(home/'skills').mkdir()
(home/'plugins').mkdir()
(home/'AGENTS.md').write_text('Do not inherit this generated instruction.')
print(json.dumps({{'type':'item.completed','item':{{'type':'agent_message','text':{action()!r}}}}}))
print(json.dumps({{'type':'turn.completed','usage':{{}}}}))
"""
    adapter = policy(tmp_path, body)
    adapter(observation(), 3.0)
    adapter(observation(), 3.0)
    assert [json.loads(line) for line in receipt.read_text().splitlines()] == [
        ["auth.json"],
        ["auth.json"],
    ]
    assert sorted(p.name for p in (tmp_path / "auth").iterdir()) == ["auth.json"]


def test_subscription_refresh_returns_only_to_explicit_task_auth_directory(tmp_path):
    refreshed = {"auth_mode": "chatgpt", "tokens": {"refresh_token": "fixture-only"}}
    body = f"""import json,os
from pathlib import Path
home=Path(os.environ['CODEX_HOME'])
(home/'auth.json').write_text({json.dumps(refreshed)!r})
print(json.dumps({{'type':'item.completed','item':{{'type':'agent_message','text':{action()!r}}}}}))
print(json.dumps({{'type':'turn.completed','usage':{{}}}}))
"""
    adapter = policy(tmp_path, body)
    adapter(observation(), 3.0)
    source = tmp_path / "auth" / "auth.json"
    assert json.loads(source.read_text()) == refreshed
    assert source.stat().st_mode & 0o777 == 0o600
