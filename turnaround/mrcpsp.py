from __future__ import annotations

from dataclasses import dataclass, field
from itertools import product
from math import prod
import re

from .advanced_models import ExecutionMode, Precedence, TurnaroundProject, TurnaroundTask
from .workforce import (
    WorkforceProfile,
    assign_people_to_skills,
    assigned_people,
    effective_capacities,
    skill_requirements,
)


@dataclass(frozen=True)
class FixedInterval:
    task_id: str
    start: float
    finish: float
    resources: dict[str, float]
    skill_assignments: dict[str, tuple[str, ...]] = field(default_factory=dict)


@dataclass
class AdvancedScheduledTask:
    task_id: str
    task_name: str
    mode_name: str
    start: float
    finish: float
    duration: float
    resources: dict[str, float]
    cost: float
    fixed: bool = False
    skill_assignments: dict[str, tuple[str, ...]] = field(default_factory=dict)


@dataclass
class AdvancedScheduleResult:
    tasks: list[AdvancedScheduledTask]
    makespan: float
    total_cost: float
    tardiness: float
    strategy: str
    mode_combinations: int
    evaluated_combinations: int
    total_start_deviation: float = 0.0
    max_start_deviation: float = 0.0
    stability_compared_tasks: int = 0
    stability_weight: float = 0.0


@dataclass(frozen=True)
class ResourceCatalogEntry:
    name: str
    base_capacity: float | None
    max_demand: float
    task_ids: tuple[str, ...]

    @property
    def capacity_defined(self) -> bool:
        return self.base_capacity is not None


def discover_resource_catalog(
    project: TurnaroundProject,
) -> dict[str, ResourceCatalogEntry]:
    """Descobre recursos por capacidade declarada OU por demanda em qualquer modo."""
    max_demand: dict[str, float] = {}
    task_ids: dict[str, set[str]] = {}

    for task in project.tasks:
        for mode in task.modes:
            for resource, demand in mode.resources.items():
                if demand <= 0:
                    continue
                max_demand[resource] = max(
                    max_demand.get(resource, 0.0),
                    float(demand),
                )
                task_ids.setdefault(resource, set()).add(task.id)

    names = set(project.capacities) | set(max_demand)
    return {
        name: ResourceCatalogEntry(
            name=name,
            base_capacity=(
                float(project.capacities[name])
                if name in project.capacities
                else None
            ),
            max_demand=float(max_demand.get(name, 0.0)),
            task_ids=tuple(sorted(task_ids.get(name, set()), key=_natural_id_key)),
        )
        for name in sorted(names, key=str.casefold)
    }


PRIORITIES = ("most_successors", "shortest_duration", "longest_duration", "id")


def _natural_id_key(value: str) -> tuple[tuple[int, int | str], ...]:
    """Natural ordering for task IDs: 2 comes before 10, while mixed IDs stay stable."""
    parts = re.split(r"(\d+)", str(value))
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part.casefold())
        for part in parts
        if part
    )


def _mode_feasible(
    mode: ExecutionMode,
    capacities: dict[str, float],
    workforce: WorkforceProfile | None = None,
) -> bool:
    human_requirements = skill_requirements(mode.resources, workforce)
    for skill, count in human_requirements.items():
        if count > capacities.get(skill, 0.0) + 1e-9:
            return False
    if human_requirements and assign_people_to_skills(
        human_requirements,
        workforce,
    ) is None:
        return False
    return all(
        req <= capacities.get(resource, 0.0) + 1e-9
        for resource, req in mode.resources.items()
    )


def _successor_counts(tasks: list[TurnaroundTask], active_ids: set[str]) -> dict[str, int]:
    counts = {t.id: 0 for t in tasks}
    for task in tasks:
        for p in task.precedences:
            if p.predecessor_id in active_ids:
                counts[p.predecessor_id] = counts.get(p.predecessor_id, 0) + 1
    return counts


def _precedence_earliest(
    precedence: Precedence,
    duration: float,
    starts: dict[str, float],
    finishes: dict[str, float],
) -> float:
    pred_start = starts[precedence.predecessor_id]
    pred_finish = finishes[precedence.predecessor_id]
    lag = precedence.lag
    if precedence.relation == "FS":
        return pred_finish + lag
    if precedence.relation == "SS":
        return pred_start + lag
    if precedence.relation == "FF":
        return pred_finish + lag - duration
    return pred_start + lag - duration


def _resource_ok(
    start: float,
    finish: float,
    demand: dict[str, float],
    capacities: dict[str, float],
    intervals: list[FixedInterval | AdvancedScheduledTask],
) -> bool:
    for resource, req in demand.items():
        if req <= 0:
            continue
        relevant = [
            iv
            for iv in intervals
            if iv.resources.get(resource, 0.0) > 0
            and start < iv.finish - 1e-9
            and finish > iv.start + 1e-9
        ]
        points = {start, finish}
        for iv in relevant:
            points.add(max(start, iv.start))
            points.add(min(finish, iv.finish))
        ordered = sorted(points)
        for a, b in zip(ordered, ordered[1:]):
            if b <= a + 1e-9:
                continue
            mid = (a + b) / 2
            usage = sum(
                iv.resources.get(resource, 0.0)
                for iv in relevant
                if iv.start < mid < iv.finish
            )
            if usage + req > capacities.get(resource, 0.0) + 1e-9:
                return False
    return True


def _busy_people(
    start: float,
    finish: float,
    intervals: list[FixedInterval | AdvancedScheduledTask],
) -> set[str]:
    busy: set[str] = set()
    for interval in intervals:
        if (
            start < interval.finish - 1e-9
            and finish > interval.start + 1e-9
        ):
            busy.update(assigned_people(interval.skill_assignments))
    return busy


def _people_assignment(
    start: float,
    finish: float,
    demand: dict[str, float],
    workforce: WorkforceProfile | None,
    intervals: list[FixedInterval | AdvancedScheduledTask],
) -> dict[str, tuple[str, ...]] | None:
    requirements = skill_requirements(demand, workforce)
    if not requirements:
        return {}
    return assign_people_to_skills(
        requirements,
        workforce,
        busy_person_ids=_busy_people(start, finish, intervals),
    )


def _earliest_resource_start(
    earliest: float,
    duration: float,
    demand: dict[str, float],
    capacities: dict[str, float],
    intervals: list[FixedInterval | AdvancedScheduledTask],
    workforce: WorkforceProfile | None = None,
) -> tuple[float, dict[str, tuple[str, ...]]]:
    candidates = {max(0.0, earliest)}
    for iv in intervals:
        if iv.finish >= earliest - 1e-9:
            candidates.add(max(earliest, iv.finish))
    for start in sorted(candidates):
        finish = start + duration
        if not _resource_ok(start, finish, demand, capacities, intervals):
            continue
        assignments = _people_assignment(
            start,
            finish,
            demand,
            workforce,
            intervals,
        )
        if assignments is not None:
            return start, assignments

    fallback = max([earliest] + [iv.finish for iv in intervals])
    assignments = _people_assignment(
        fallback,
        fallback + duration,
        demand,
        workforce,
        intervals,
    )
    if assignments is None:
        raise ValueError(
            "Não existe alocação multi-skill factível para a atividade "
            "nas capacidades/pessoas atuais"
        )
    return fallback, assignments


def _schedule_assignment(
    tasks: list[TurnaroundTask],
    modes: dict[str, ExecutionMode],
    capacities: dict[str, float],
    priority: str,
    earliest_start: float = 0.0,
    fixed_intervals: list[FixedInterval] | None = None,
    fixed_task_times: dict[str, tuple[float, float]] | None = None,
    workforce: WorkforceProfile | None = None,
) -> list[AdvancedScheduledTask]:
    fixed_intervals = list(fixed_intervals or [])
    fixed_task_times = dict(fixed_task_times or {})
    active_ids = {t.id for t in tasks}
    successor_counts = _successor_counts(tasks, active_ids)
    scheduled: dict[str, AdvancedScheduledTask] = {}
    starts = {tid: times[0] for tid, times in fixed_task_times.items()}
    finishes = {tid: times[1] for tid, times in fixed_task_times.items()}
    intervals: list[FixedInterval | AdvancedScheduledTask] = list(fixed_intervals)

    while len(scheduled) < len(tasks):
        ready: list[TurnaroundTask] = []
        for task in tasks:
            if task.id in scheduled:
                continue
            active_preds = [
                p.predecessor_id
                for p in task.precedences
                if p.predecessor_id in active_ids or p.predecessor_id in fixed_task_times
            ]
            if all(pid in finishes for pid in active_preds):
                ready.append(task)

        if not ready:
            unresolved = [t.id for t in tasks if t.id not in scheduled]
            raise ValueError(f"Rede com ciclo ou predecessor não resolvido: {unresolved}")

        def key(task: TurnaroundTask):
            mode = modes[task.id]
            if priority == "most_successors":
                return (-successor_counts.get(task.id, 0), mode.duration, _natural_id_key(task.id))
            if priority == "shortest_duration":
                return (mode.duration, _natural_id_key(task.id))
            if priority == "longest_duration":
                return (-mode.duration, _natural_id_key(task.id))
            return (_natural_id_key(task.id),)

        task = sorted(ready, key=key)[0]
        mode = modes[task.id]
        earliest = max(earliest_start, float(task.release_time))
        for p in task.precedences:
            if p.predecessor_id not in finishes:
                continue
            earliest = max(
                earliest,
                _precedence_earliest(p, mode.duration, starts, finishes),
            )
        start, skill_assignments = _earliest_resource_start(
            earliest,
            mode.duration,
            mode.resources,
            capacities,
            intervals,
            workforce=workforce,
        )
        item = AdvancedScheduledTask(
            task_id=task.id,
            task_name=task.name,
            mode_name=mode.name,
            start=start,
            finish=start + mode.duration,
            duration=mode.duration,
            resources=dict(mode.resources),
            cost=mode.cost,
            skill_assignments=skill_assignments,
        )
        scheduled[task.id] = item
        starts[task.id] = item.start
        finishes[task.id] = item.finish
        intervals.append(item)

    return list(scheduled.values())


def _stability_metrics(
    items: list[AdvancedScheduledTask],
    reference_start_times: dict[str, float] | None,
) -> tuple[float, float, int]:
    if not reference_start_times:
        return 0.0, 0.0, 0

    deviations = [
        abs(item.start - reference_start_times[item.task_id])
        for item in items
        if item.task_id in reference_start_times
    ]
    if not deviations:
        return 0.0, 0.0, 0
    return sum(deviations), max(deviations), len(deviations)


def _score(
    items: list[AdvancedScheduledTask],
    deadline: float | None,
    reference_start_times: dict[str, float] | None = None,
    stability_weight: float = 0.0,
) -> tuple[float, float, float, float, float]:
    makespan = max((t.finish for t in items), default=0.0)
    cost = sum(t.cost for t in items)
    tardiness = max(0.0, makespan - deadline) if deadline is not None else 0.0
    total_deviation, max_deviation, _ = _stability_metrics(
        items,
        reference_start_times,
    )

    if stability_weight <= 0:
        # Preserva exatamente a ordenação histórica quando estabilidade está
        # desligada: atraso -> makespan -> custo.
        return tardiness, makespan, cost, 0.0, 0.0

    # Formulação stability-aware inspirada na literatura de rescheduling:
    # minimiza makespan + lambda * soma dos deslocamentos de início.
    # A deadline continua tendo precedência lexicográfica.
    stability_objective = makespan + stability_weight * total_deviation
    return (
        tardiness,
        stability_objective,
        max_deviation,
        cost,
        makespan,
    )


def solve_mrcpsp(
    tasks: list[TurnaroundTask],
    capacities: dict[str, float],
    deadline: float | None = None,
    max_mode_combinations: int = 2000,
    earliest_start: float = 0.0,
    fixed_intervals: list[FixedInterval] | None = None,
    fixed_task_times: dict[str, tuple[float, float]] | None = None,
    reference_start_times: dict[str, float] | None = None,
    stability_weight: float = 0.0,
    workforce: WorkforceProfile | None = None,
) -> AdvancedScheduleResult:
    if not tasks:
        return AdvancedScheduleResult([], earliest_start, 0.0, 0.0, "empty", 0, 0)

    capacities = effective_capacities(capacities, workforce)

    feasible_modes: dict[str, list[ExecutionMode]] = {}
    for task in tasks:
        feasible = [
            m
            for m in task.modes
            if _mode_feasible(m, capacities, workforce)
        ]
        if not feasible:
            raise ValueError(
                f"Atividade {task.id} não possui modo factível para as capacidades atuais"
            )
        feasible_modes[task.id] = feasible

    combination_count = prod(len(feasible_modes[t.id]) for t in tasks)
    best_items: list[AdvancedScheduledTask] | None = None
    best_score: tuple[float, float, float, float, float] | None = None
    evaluated = 0

    def evaluate(assignment: dict[str, ExecutionMode]):
        nonlocal best_items, best_score, evaluated
        for priority in PRIORITIES:
            items = _schedule_assignment(
                tasks,
                assignment,
                capacities,
                priority,
                earliest_start=earliest_start,
                fixed_intervals=fixed_intervals,
                fixed_task_times=fixed_task_times,
                workforce=workforce,
            )
            evaluated += 1
            score = _score(
                items,
                deadline,
                reference_start_times=reference_start_times,
                stability_weight=stability_weight,
            )
            if best_score is None or score < best_score:
                best_score = score
                best_items = items

    if combination_count <= max_mode_combinations:
        mode_lists = [feasible_modes[t.id] for t in tasks]
        for combo in product(*mode_lists):
            evaluate({task.id: mode for task, mode in zip(tasks, combo)})
        strategy = "enumeration+ssgs"
    else:
        seeds: list[dict[str, ExecutionMode]] = []
        seeds.append(
            {
                t.id: min(feasible_modes[t.id], key=lambda m: (m.duration, m.cost))
                for t in tasks
            }
        )
        seeds.append(
            {
                t.id: min(feasible_modes[t.id], key=lambda m: (m.cost, m.duration))
                for t in tasks
            }
        )
        seeds.append(
            {
                t.id: min(
                    feasible_modes[t.id],
                    key=lambda m: (
                        sum(m.resources.values()) * m.duration,
                        m.duration,
                        m.cost,
                    ),
                )
                for t in tasks
            }
        )
        seen = set()
        for seed in seeds:
            key = tuple((tid, seed[tid].name) for tid in sorted(seed, key=_natural_id_key))
            if key not in seen:
                seen.add(key)
                evaluate(seed)

        assert best_items is not None
        current_names = {item.task_id: item.mode_name for item in best_items}
        current = {
            t.id: next(m for m in feasible_modes[t.id] if m.name == current_names[t.id])
            for t in tasks
        }
        improved = True
        passes = 0
        while improved and passes < 3:
            improved = False
            passes += 1
            baseline = best_score
            for task in tasks:
                original = current[task.id]
                for mode in feasible_modes[task.id]:
                    if mode.name == original.name:
                        continue
                    candidate = dict(current)
                    candidate[task.id] = mode
                    before = best_score
                    evaluate(candidate)
                    if best_score is not None and before is not None and best_score < before:
                        current = candidate
                        improved = True
                        break
                if improved:
                    break
            if baseline == best_score:
                improved = False
        strategy = "multistart-local+ssgs"

    assert best_items is not None and best_score is not None

    makespan = max((item.finish for item in best_items), default=earliest_start)
    cost = sum(item.cost for item in best_items)
    tardiness = (
        max(0.0, makespan - deadline)
        if deadline is not None
        else 0.0
    )
    total_deviation, max_deviation, compared_tasks = _stability_metrics(
        best_items,
        reference_start_times,
    )
    if stability_weight > 0 and reference_start_times:
        strategy = f"{strategy}+stability"

    return AdvancedScheduleResult(
        tasks=sorted(best_items, key=lambda x: (x.start, x.finish, _natural_id_key(x.task_id))),
        makespan=makespan,
        total_cost=cost,
        tardiness=tardiness,
        strategy=strategy,
        mode_combinations=combination_count,
        evaluated_combinations=evaluated,
        total_start_deviation=total_deviation,
        max_start_deviation=max_deviation,
        stability_compared_tasks=compared_tasks,
        stability_weight=float(stability_weight),
    )
