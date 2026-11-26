"""The fixed Harbor Kitchen uses the exact browser simulation through Node/HTTP."""

from harness_lab_app.gameplay.contracts import Action, ControlBounds, PolicyReply
from harness_lab_app.gameplay.racing import RacingEnvironment


class CookingEnvironment(RacingEnvironment):
    environment_id = "harbor-kitchen"
    environment_version = "1"
    bridge_script = "scripts/cooking_bridge.mjs"
    engine_module = "frontend/src/gameplay/cooking.mjs"
    instructions = (
        "Serve tomato soup during a 90-second kitchen shift. target is an integer: "
        "-1 stays put; 0..5 selects a station listed in state.stations. Your chef "
        "walks there under the same rules as a human. interact=1 uses the station "
        "while held, interact=0 releases. Take tomato from pantry, chop at prep "
        "for 2 seconds, put chopped tomato in pot and cook for 3 seconds, retrieve "
        "cooked soup, plate it, then deliver. Read your held item and station "
        "progress before changing targets. Inputs last one second; the countdown "
        "is non-interactive. Return target and interact every turn."
    )

    @staticmethod
    def controls() -> dict[str, ControlBounds]:
        return {
            "target": ControlBounds(minimum=-1.0, maximum=5.0, integer=True),
            "interact": ControlBounds(minimum=0.0, maximum=1.0),
        }


def after_cooking_countdown(policy):
    """Avoid model calls during the fixed start sequence."""

    def decide(observation, timeout):
        if observation.state.get("phase") == "countdown":
            return PolicyReply(
                action_json=Action(
                    observation_id=observation.observation_id,
                    controls={"target": -1.0, "interact": 0.0},
                ).model_dump_json(),
                provenance={"controller": "fixed-countdown", "model_call": False},
            )
        return policy(observation, timeout)

    return decide
