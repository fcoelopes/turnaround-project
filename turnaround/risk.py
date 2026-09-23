from __future__ import annotations

from dataclasses import replace
from typing import Dict, List

import numpy as np

from .models import Task
from .rcpsp import serial_schedule_generation


def simulate_deadline_risk(
    tasks: List[Task],
    capacities: Dict[str, int],
    priority_rule: str,
    deadline_h: int | None,
    n: int = 300,
    optimistic_factor: float = 0.90,
    most_likely_factor: float = 1.00,
    pessimistic_factor: float = 1.30,
    seed: int = 42,
) -> dict:
    if n <= 0:
        raise ValueError("Número de simulações deve ser positivo.")
    if not (0 < optimistic_factor <= most_likely_factor <= pessimistic_factor):
        raise ValueError("Use fatores triangularmente ordenados: otimista <= provável <= pessimista.")

    rng = np.random.default_rng(seed)
    makespans = []
    for _ in range(n):
        sampled = []
        for t in tasks:
            factor = float(rng.triangular(optimistic_factor, most_likely_factor, pessimistic_factor))
            sampled.append(replace(t, duration_h=max(1, int(round(t.duration_h * factor)))))
        result = serial_schedule_generation(sampled, capacities, priority_rule)
        makespans.append(result.makespan_h)

    arr = np.asarray(makespans, dtype=float)
    return {
        "n": n,
        "p50_h": float(np.percentile(arr, 50)),
        "p80_h": float(np.percentile(arr, 80)),
        "p90_h": float(np.percentile(arr, 90)),
        "mean_h": float(arr.mean()),
        "std_h": float(arr.std(ddof=1)) if len(arr) > 1 else 0.0,
        "probability_meet_deadline": None if not deadline_h else float(np.mean(arr <= deadline_h)),
        "samples": makespans,
    }
