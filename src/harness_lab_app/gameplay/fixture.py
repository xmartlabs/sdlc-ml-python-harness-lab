"""Tiny integer simulation for instrument checks, not a gameplay benchmark."""

from pydantic import JsonValue

from harness_lab_app.gameplay.contracts import ControlBounds


class LineWorld:
    """Reach a seeded target on a line; one cell per tick, no hidden state."""

    environment_id = "line-world"
    environment_version = "1"
    instructions = "Reach goal. Positive move increases position one cell per tick; negative decreases it; zero waits."

    def reset(self, seed: int) -> None:
        self.position = 0
        self.goal = 3 + seed % 3

    def state(self) -> dict[str, JsonValue]:
        return {"position": self.position, "goal": self.goal}

    def controls(self) -> dict[str, ControlBounds]:
        return {"move": ControlBounds(minimum=-1.0, maximum=1.0)}

    def step(self, controls: dict[str, float], ticks: int) -> list[str]:
        events = []
        direction = (controls["move"] > 0) - (controls["move"] < 0)
        for _ in range(ticks):
            if not self.terminal():
                self.position += direction
                if self.terminal():
                    events.append("goal_reached")
        return events

    def terminal(self) -> bool:
        return self.position == self.goal

    def metrics(self) -> dict[str, float]:
        return {
            "reached_goal": float(self.terminal()),
            "distance_remaining": float(abs(self.goal - self.position)),
        }
