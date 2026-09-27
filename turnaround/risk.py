from __future__ import annotations

from dataclasses import replace
from typing import Dict, List

import numpy as np

from .models import Task
from .planning_scope_risk import PlanningScopeRisk, materialize_planning_scope
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
    scope_risks: list[PlanningScopeRisk] | None = None,
) -> dict:
    if n <= 0:
        raise ValueError("Número de simulações deve ser positivo.")
    if not (0 < optimistic_factor <= most_likely_factor <= pessimistic_factor):
        raise ValueError(
            "Use fatores triangularmente ordenados: "
            "otimista <= provável <= pessimista."
        )

    scope_risks = list(scope_risks or [])
    potential_ids = {item.task_id for item in scope_risks}

    # Uma ocorrência física deve ser sorteada uma única vez, ainda que ative
    # mais de uma atividade. Por isso agrupamos por gatilho + nome do evento.
    grouped_events: dict[tuple[str, str], dict] = {}
    for item in scope_risks:
        entry = grouped_events.setdefault(
            item.event_key,
            {
                "trigger_task_id": item.trigger_task_id,
                "event_name": item.event_name,
                "probability": float(item.probability),
                "task_ids": [],
            },
        )
        if abs(float(entry["probability"]) - float(item.probability)) > 1e-9:
            raise ValueError(
                "O mesmo evento de escopo não pode ter probabilidades diferentes: "
                f"{item.trigger_task_id} / {item.event_name}."
            )
        entry["task_ids"].append(item.task_id)

    rng = np.random.default_rng(seed)
    makespans: list[float] = []
    duration_only_makespans: list[float] = []
    scope_impacts: list[float] = []
    any_scope_count = 0

    event_stats: dict[tuple[str, str], dict] = {
        key: {
            **value,
            "active_count": 0,
            "active_makespans": [],
            "inactive_makespans": [],
        }
        for key, value in grouped_events.items()
    }

    task_active_count = {task_id: 0 for task_id in potential_ids}

    base_scope_tasks = materialize_planning_scope(
        tasks,
        scope_risks,
        active_scope_task_ids=set(),
    )

    for _ in range(n):
        duration_factors = {
            task.id: float(
                rng.triangular(
                    optimistic_factor,
                    most_likely_factor,
                    pessimistic_factor,
                )
            )
            for task in tasks
        }

        active_scope_task_ids: set[str] = set()
        event_outcomes: dict[tuple[str, str], bool] = {}
        for key, event in grouped_events.items():
            active = bool(rng.random() < float(event["probability"]))
            event_outcomes[key] = active
            if active:
                active_scope_task_ids.update(event["task_ids"])

        if active_scope_task_ids:
            any_scope_count += 1
            for task_id in active_scope_task_ids:
                task_active_count[task_id] += 1

        scenario_tasks = materialize_planning_scope(
            tasks,
            scope_risks,
            active_scope_task_ids=active_scope_task_ids,
        )
        sampled_scenario = [
            replace(
                task,
                duration_h=max(
                    1,
                    int(
                        round(
                            task.duration_h
                            * duration_factors[task.id]
                        )
                    ),
                ),
            )
            for task in scenario_tasks
        ]
        scenario_result = serial_schedule_generation(
            sampled_scenario,
            capacities,
            priority_rule,
        )
        makespans.append(float(scenario_result.makespan_h))

        # Paired base-scope run using the exact same duration factors. This
        # isolates the incremental effect of uncertain scope from duration noise.
        sampled_base = [
            replace(
                task,
                duration_h=max(
                    1,
                    int(
                        round(
                            task.duration_h
                            * duration_factors[task.id]
                        )
                    ),
                ),
            )
            for task in base_scope_tasks
        ]
        base_result = serial_schedule_generation(
            sampled_base,
            capacities,
            priority_rule,
        )
        duration_only_makespans.append(float(base_result.makespan_h))
        scope_impacts.append(
            float(scenario_result.makespan_h - base_result.makespan_h)
        )

        for key, active in event_outcomes.items():
            stats = event_stats[key]
            if active:
                stats["active_count"] += 1
                stats["active_makespans"].append(
                    float(scenario_result.makespan_h)
                )
            else:
                stats["inactive_makespans"].append(
                    float(scenario_result.makespan_h)
                )

    arr = np.asarray(makespans, dtype=float)
    base_arr = np.asarray(duration_only_makespans, dtype=float)
    scope_arr = np.asarray(scope_impacts, dtype=float)

    event_rows = []
    for key, stats in event_stats.items():
        active_values = stats.pop("active_makespans")
        inactive_values = stats.pop("inactive_makespans")
        active_mean = (
            float(np.mean(active_values))
            if active_values
            else None
        )
        inactive_mean = (
            float(np.mean(inactive_values))
            if inactive_values
            else None
        )
        event_rows.append(
            {
                "trigger_task_id": stats["trigger_task_id"],
                "event_name": stats["event_name"],
                "task_ids": list(stats["task_ids"]),
                "probability_configured": float(stats["probability"]),
                "frequency_simulated": float(stats["active_count"] / n),
                "mean_makespan_when_active_h": active_mean,
                "mean_makespan_when_inactive_h": inactive_mean,
                "marginal_impact_h": (
                    None
                    if active_mean is None or inactive_mean is None
                    else active_mean - inactive_mean
                ),
            }
        )

    event_rows.sort(
        key=lambda row: (
            -(
                row["marginal_impact_h"]
                if row["marginal_impact_h"] is not None
                else float("-inf")
            ),
            row["event_name"],
        )
    )

    return {
        "n": n,
        "p50_h": float(np.percentile(arr, 50)),
        "p80_h": float(np.percentile(arr, 80)),
        "p90_h": float(np.percentile(arr, 90)),
        "mean_h": float(arr.mean()),
        "std_h": float(arr.std(ddof=1)) if len(arr) > 1 else 0.0,
        "probability_meet_deadline": (
            None
            if not deadline_h
            else float(np.mean(arr <= deadline_h))
        ),
        "samples": makespans,
        "duration_only_samples": duration_only_makespans,
        "scope_impact_samples": scope_impacts,
        "scope_enabled": bool(scope_risks),
        "probability_any_scope_simulated": float(any_scope_count / n),
        "mean_scope_impact_h": (
            float(scope_arr.mean())
            if len(scope_arr)
            else 0.0
        ),
        "p80_scope_impact_h": (
            float(np.percentile(scope_arr, 80))
            if len(scope_arr)
            else 0.0
        ),
        "duration_only_p80_h": float(np.percentile(base_arr, 80)),
        "scope_events": event_rows,
        "task_activation_frequency": {
            task_id: float(count / n)
            for task_id, count in task_active_count.items()
        },
    }
