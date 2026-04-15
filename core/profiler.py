"""Stopwatch mínimo para instrumentar pasos dentro de una función.

Uso típico:

    sw = Stopwatch("vista_aum")
    sw.step("mongo: find ultimo")
    docs = list(col.find(...))
    sw.step("mongo: agg fci")
    rows = list(col.aggregate(...))
    sw.step("pandas: merge")
    df = ...
    trace = sw.done()
    # trace = {"label": "vista_aum", "total_ms": 1234.5,
    #          "steps": [{"name": "mongo: find ultimo", "ms": 180.1}, ...]}

No maneja pasos anidados — es deliberado. Si necesitás jerarquía, usá dos
stopwatches y componé los resultados afuera.
"""
from __future__ import annotations

import time


class Stopwatch:
    def __init__(self, label: str):
        self.label = label
        self.steps: list[dict] = []
        self._name: str | None = None
        self._start: float = 0.0
        self._t0 = time.perf_counter()

    def step(self, name: str) -> None:
        now = time.perf_counter()
        if self._name is not None:
            self.steps.append({"name": self._name, "ms": (now - self._start) * 1000})
        self._name = name
        self._start = now

    def done(self) -> dict:
        now = time.perf_counter()
        if self._name is not None:
            self.steps.append({"name": self._name, "ms": (now - self._start) * 1000})
            self._name = None
        return {
            "label":    self.label,
            "total_ms": (now - self._t0) * 1000,
            "steps":    self.steps,
        }
