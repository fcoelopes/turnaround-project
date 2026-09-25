from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Literal

from .advanced_models import Precedence, TurnaroundProject, TurnaroundTask
from .mrcpsp import AdvancedScheduledTask

DriverKind = Literal["precedence", "scope_gate", "resource", "person"]


@dataclass(frozen=True)
class EffectiveDriver:
    predecessor_id: str
    successor_id: str
    kind: DriverKind
    detail: str


@dataclass
class EffectiveCriticalityResult:
    critical_ids: set[str]
    path_ids: list[str]
    drivers: list[EffectiveDriver]
    reasons: dict[str, str]


def _precedence_bound(
    precedence: Precedence,
    predecessor: AdvancedScheduledTask,
    successor: AdvancedScheduledTask,
) -> float:
    if precedence.relation == "FS":
        return predecessor.finish + precedence.lag
    if precedence.relation == "SS":
        return predecessor.start + precedence.lag
    if precedence.relation == "FF":
        return predecessor.finish + precedence.lag - successor.duration
    return predecessor.start + precedence.lag - successor.duration


def analyze_effective_criticality(
    *,
    project: TurnaroundProject,
    effective_tasks: list[TurnaroundTask],
    items: list[AdvancedScheduledTask],
    capacities: dict[str, float],
    current_time: float,
    makespan: float,
    tolerance: float = 1e-6,
) -> EffectiveCriticalityResult:
    """Explain what controls the current rescheduled finish.

    This is intentionally not a classical CPM calculation. It analyzes the
    realized MRCPSP schedule and builds a graph of *binding* drivers:

    - original precedence relationships;
    - precedence gates injected by late scope discovery;
    - resource releases that make a delayed task feasible.

    Critical activities are the live activities connected through those
    binding drivers to the task(s) that currently define the makespan.
    """

    if not items:
        return EffectiveCriticalityResult(set(), [], [], {})

    item_by_id = {str(item.task_id): item for item in items}
    project_task_by_id = {task.id: task for task in project.tasks}
    effective_task_by_id = {task.id: task for task in effective_tasks}

    live_ids = {
        task_id
        for task_id, item in item_by_id.items()
        if item.finish > current_time + tolerance
    }
    if not live_ids:
        return EffectiveCriticalityResult(set(), [], [], {})

    drivers: list[EffectiveDriver] = []
    seen: set[tuple[str, str, str, str]] = set()

    def add_driver(
        predecessor_id: str,
        successor_id: str,
        kind: DriverKind,
        detail: str,
    ) -> None:
        if predecessor_id == successor_id:
            return
        key = (predecessor_id, successor_id, kind, detail)
        if key in seen:
            return
        seen.add(key)
        drivers.append(
            EffectiveDriver(
                predecessor_id=predecessor_id,
                successor_id=successor_id,
                kind=kind,
                detail=detail,
            )
        )

    # Binding logical / discovery precedences.
    for successor_id in live_ids:
        successor = item_by_id[successor_id]
        task = effective_task_by_id.get(successor_id)
        if task is None:
            continue

        original = project_task_by_id.get(successor_id)
        original_pred_ids = {
            p.predecessor_id
            for p in (original.precedences if original is not None else [])
        }

        for precedence in task.precedences:
            predecessor = item_by_id.get(precedence.predecessor_id)
            if predecessor is None:
                continue
            bound = _precedence_bound(precedence, predecessor, successor)
            if abs(successor.start - bound) > tolerance:
                continue

            if precedence.predecessor_id in original_pred_ids:
                add_driver(
                    precedence.predecessor_id,
                    successor_id,
                    "precedence",
                    precedence.relation,
                )
            else:
                add_driver(
                    precedence.predecessor_id,
                    successor_id,
                    "scope_gate",
                    "gate de escopo",
                )

    # Binding resource releases. The task is delayed by resource contention
    # when it could not start an instant before its realized start, but becomes
    # feasible exactly when one or more resource-consuming tasks finish.
    probe_epsilon = max(1e-5, tolerance * 10)
    for successor_id in live_ids:
        successor = item_by_id[successor_id]
        if successor.fixed:
            continue
        if successor.start <= current_time + tolerance:
            continue

        probe = successor.start - probe_epsilon
        for resource, demand in successor.resources.items():
            if demand <= tolerance:
                continue
            capacity = capacities.get(resource, 0.0)

            active_before = [
                item
                for item in items
                if str(item.task_id) != successor_id
                and item.resources.get(resource, 0.0) > tolerance
                and item.start < probe + tolerance
                and item.finish > probe + tolerance
            ]
            usage = sum(
                item.resources.get(resource, 0.0)
                for item in active_before
            )
            if usage + demand <= capacity + tolerance:
                continue

            for blocker in active_before:
                if abs(blocker.finish - successor.start) <= probe_epsilon * 2:
                    add_driver(
                        str(blocker.task_id),
                        successor_id,
                        "resource",
                        resource,
                    )

    # Releases de pessoas multi-skill também podem controlar o início mesmo
    # quando as capacidades agregadas por habilidade parecem suficientes.
    for successor_id in live_ids:
        successor = item_by_id[successor_id]
        if successor.fixed or not successor.skill_assignments:
            continue
        if successor.start <= current_time + tolerance:
            continue

        successor_people = {
            person_id
            for values in successor.skill_assignments.values()
            for person_id in values
        }
        for blocker in items:
            blocker_id = str(blocker.task_id)
            if blocker_id == successor_id:
                continue
            blocker_people = {
                person_id
                for values in blocker.skill_assignments.values()
                for person_id in values
            }
            shared = successor_people & blocker_people
            if not shared:
                continue
            if abs(blocker.finish - successor.start) > probe_epsilon * 2:
                continue
            for person_id in sorted(shared):
                add_driver(
                    blocker_id,
                    successor_id,
                    "person",
                    person_id,
                )

    terminal_ids = {
        task_id
        for task_id in live_ids
        if abs(item_by_id[task_id].finish - makespan) <= tolerance
    }
    if not terminal_ids:
        max_finish = max(item_by_id[task_id].finish for task_id in live_ids)
        terminal_ids = {
            task_id
            for task_id in live_ids
            if abs(item_by_id[task_id].finish - max_finish) <= tolerance
        }

    incoming: dict[str, list[EffectiveDriver]] = defaultdict(list)
    outgoing: dict[str, list[EffectiveDriver]] = defaultdict(list)
    for driver in drivers:
        if driver.predecessor_id in live_ids and driver.successor_id in live_ids:
            incoming[driver.successor_id].append(driver)
            outgoing[driver.predecessor_id].append(driver)

    critical_ids = set(terminal_ids)
    stack = list(terminal_ids)
    while stack:
        successor_id = stack.pop()
        for driver in incoming.get(successor_id, []):
            predecessor_id = driver.predecessor_id
            if predecessor_id not in critical_ids:
                critical_ids.add(predecessor_id)
                stack.append(predecessor_id)

    def path_score(path: list[str]) -> tuple[float, int]:
        duration = sum(item_by_id[task_id].duration for task_id in path)
        return duration, len(path)

    memo: dict[str, list[str]] = {}

    def best_path_to(task_id: str, visiting: set[str] | None = None) -> list[str]:
        if task_id in memo:
            return memo[task_id]

        visiting = set(visiting or set())
        if task_id in visiting:
            return [task_id]
        visiting.add(task_id)

        predecessors = [
            driver.predecessor_id
            for driver in incoming.get(task_id, [])
            if driver.predecessor_id in critical_ids
        ]
        if not predecessors:
            memo[task_id] = [task_id]
            return memo[task_id]

        candidates = [
            best_path_to(predecessor_id, visiting) + [task_id]
            for predecessor_id in predecessors
        ]
        memo[task_id] = max(candidates, key=path_score)
        return memo[task_id]

    path_ids = max(
        (best_path_to(task_id) for task_id in terminal_ids),
        key=path_score,
        default=[],
    )

    reasons: dict[str, str] = {}
    for task_id in critical_ids:
        labels: list[str] = []
        for driver in outgoing.get(task_id, []):
            if driver.successor_id not in critical_ids:
                continue
            if driver.kind == "resource":
                label = f"recurso {driver.detail}"
            elif driver.kind == "person":
                label = f"pessoa {driver.detail}"
            elif driver.kind == "scope_gate":
                label = "gate de escopo"
            else:
                label = f"precedência {driver.detail}"
            if label not in labels:
                labels.append(label)

        if task_id in terminal_ids:
            labels.append("término do cronograma")
        reasons[task_id] = ", ".join(labels) if labels else "cadeia controladora"

    return EffectiveCriticalityResult(
        critical_ids=critical_ids,
        path_ids=path_ids,
        drivers=drivers,
        reasons=reasons,
    )
