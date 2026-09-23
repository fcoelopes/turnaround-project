from __future__ import annotations

from collections import defaultdict
from typing import Dict, Iterable, List, Tuple

from .cpm import cpm_metrics, topological_order, validate_tasks
from .models import ScheduledTask, Task, TurnaroundResult


def _successors(tasks: List[Task]) -> Dict[str, List[str]]:
    out = defaultdict(list)
    for t in tasks:
        for link in t.predecessors:
            out[link.predecessor_id].append(t.id)
    return out


def _transitive_successor_count(tasks: List[Task]) -> Dict[str, int]:
    succ = _successors(tasks)
    memo: Dict[str, set] = {}

    def visit(tid: str) -> set:
        if tid in memo:
            return memo[tid]
        ans = set()
        for s in succ.get(tid, []):
            ans.add(s)
            ans.update(visit(s))
        memo[tid] = ans
        return ans

    return {t.id: len(visit(t.id)) for t in tasks}


def infer_capacities(tasks: List[Task]) -> Dict[str, int]:
    peak = defaultdict(int)
    for t in tasks:
        for r, q in t.resources.items():
            peak[r] = max(peak[r], q)
    return dict(peak)


def _precedence_bound(task: Task, scheduled: Dict[str, ScheduledTask]) -> int:
    lb = 0
    for link in task.predecessors:
        pred = scheduled[link.predecessor_id]
        if link.relation == "FS":
            bound = pred.finish_h + link.lag_h
        elif link.relation == "SS":
            bound = pred.start_h + link.lag_h
        elif link.relation == "FF":
            bound = pred.finish_h + link.lag_h - task.duration_h
        else:  # SF
            bound = pred.start_h + link.lag_h - task.duration_h
        lb = max(lb, bound)
    return max(0, lb)


def _fits(task: Task, start: int, usage: Dict[str, List[int]], capacities: Dict[str, int]) -> bool:
    finish = start + task.duration_h
    for resource, demand in task.resources.items():
        cap = capacities.get(resource, 0)
        if demand > cap:
            return False
        timeline = usage.setdefault(resource, [])
        if len(timeline) < finish:
            timeline.extend([0] * (finish - len(timeline)))
        if any(timeline[t] + demand > cap for t in range(start, finish)):
            return False
    return True


def _reserve(task: Task, start: int, usage: Dict[str, List[int]]) -> None:
    finish = start + task.duration_h
    for resource, demand in task.resources.items():
        timeline = usage.setdefault(resource, [])
        if len(timeline) < finish:
            timeline.extend([0] * (finish - len(timeline)))
        for t in range(start, finish):
            timeline[t] += demand


def _priority_keys(tasks: List[Task], rule: str):
    cpm = cpm_metrics(tasks)
    succ_count = _transitive_successor_count(tasks)
    by_id = {t.id: t for t in tasks}

    def key(tid: str):
        t = by_id[tid]
        if rule == "minimum_float":
            return (cpm[tid]["Float_h"], -succ_count[tid], -t.duration_h, str(tid))
        if rule == "most_successors":
            return (-succ_count[tid], cpm[tid]["Float_h"], -t.duration_h, str(tid))
        if rule == "longest_duration":
            return (-t.duration_h, cpm[tid]["Float_h"], -succ_count[tid], str(tid))
        if rule == "shortest_duration":
            return (t.duration_h, cpm[tid]["Float_h"], -succ_count[tid], str(tid))
        return (str(tid),)

    return key


def serial_schedule_generation(
    tasks: List[Task], capacities: Dict[str, int], priority_rule: str = "minimum_float"
) -> TurnaroundResult:
    validate_tasks(tasks)
    by_id = {t.id: t for t in tasks}
    remaining = set(by_id)
    scheduled: Dict[str, ScheduledTask] = {}
    usage: Dict[str, List[int]] = {}
    key = _priority_keys(tasks, priority_rule)

    for t in tasks:
        for resource, demand in t.resources.items():
            if capacities.get(resource, 0) < demand:
                raise ValueError(
                    f"Capacidade insuficiente para '{resource}': atividade {t.id} exige {demand}, "
                    f"mas a capacidade configurada é {capacities.get(resource, 0)}."
                )

    while remaining:
        eligible = [
            tid for tid in remaining
            if all(link.predecessor_id in scheduled for link in by_id[tid].predecessors)
        ]
        if not eligible:
            raise ValueError("Não há atividade elegível. Verifique ciclos ou predecessoras inválidas.")
        tid = sorted(eligible, key=key)[0]
        task = by_id[tid]
        start = _precedence_bound(task, scheduled)
        while not _fits(task, start, usage, capacities):
            start += 1
            if start > 1_000_000:
                raise RuntimeError("Busca de janela viável excedeu o limite de segurança.")
        _reserve(task, start, usage)
        scheduled[tid] = ScheduledTask(
            id=task.id,
            name=task.name,
            start_h=start,
            finish_h=start + task.duration_h,
            duration_h=task.duration_h,
            resources=dict(task.resources),
            wbs=task.wbs,
        )
        remaining.remove(tid)

    schedule = sorted(scheduled.values(), key=lambda x: (x.start_h, x.finish_h, str(x.id)))
    makespan = max(x.finish_h for x in schedule)
    peaks = {r: max(v) if v else 0 for r, v in usage.items()}
    utilization = {}
    for r, timeline in usage.items():
        cap = capacities.get(r, 0)
        utilization[r] = 0.0 if cap <= 0 or makespan <= 0 else sum(timeline[:makespan]) / (cap * makespan)

    return TurnaroundResult(
        schedule=schedule,
        makespan_h=makespan,
        resource_peak=peaks,
        resource_utilization=utilization,
        tardiness_h=0,
        priority_rule=priority_rule,
    )


def optimize_turnaround(
    tasks: List[Task], capacities: Dict[str, int], deadline_h: int | None = None
) -> Tuple[TurnaroundResult, List[TurnaroundResult]]:
    rules = ["minimum_float", "most_successors", "longest_duration", "shortest_duration"]
    candidates = [serial_schedule_generation(tasks, capacities, r) for r in rules]
    for c in candidates:
        c.tardiness_h = max(0, c.makespan_h - deadline_h) if deadline_h else 0
    best = min(candidates, key=lambda c: (c.tardiness_h, c.makespan_h))
    return best, candidates
