"""Decision clock: how much of the engine's budget is left and which mode fits (see the plan)."""

import math
import time

RETURN_MARGIN = 2.0  # validate + encode + pipe after we return
WAVE_GUARD = 90  # the engine closes 90 s before a wave's cap


class Budget:
    def __init__(self, observation, params, now=None):
        self.t0 = time.monotonic() if now is None else now
        ready = observation.get("ready_suppliers") or []
        seconds = observation.get("seconds_left") or {}
        thread_left = min((seconds.get(s, 60) for s in ready), default=60)
        wave = observation.get("wave_seconds_left")
        self.wave_left = wave
        self.total = min(
            params["decision_seconds"],
            thread_left - 5,
            (wave - WAVE_GUARD) if wave is not None else math.inf,
        )
        self.deadline = self.t0 + self.total - RETURN_MARGIN
        self.call = params["model_timeout"] + 1.0

    def remaining(self):
        return self.deadline - time.monotonic()

    def mode(self, llm_ok, refresh_due, two_call_ok):
        """reflex | det | merged | two"""
        if self.total <= 4:
            return "reflex"
        if not llm_ok:
            return "det"
        r = self.remaining()
        if two_call_ok and refresh_due and r >= 2 * self.call + 1 and self.call <= 14:
            return "two"
        if r >= self.call + 1:
            return "merged"
        return "det"

    def can_call(self):
        return self.remaining() >= self.call + 0.5

    def last_round(self, max_latency):
        """Wave pre-close: no time for another reply before the engine's guard."""
        if self.wave_left is None:
            return False
        return self.wave_left - WAVE_GUARD < max(max_latency, 30) + 25

    def horizon(self, sent, moves_left, latencies):
        available = moves_left
        if self.wave_left is not None:
            round_seconds = max(latencies, default=60) + max(1, self.total)
            available = min(available, max(1, int((self.wave_left - WAVE_GUARD) / round_seconds)))
        return max(2, sent + available)
