from __future__ import annotations

from collections import defaultdict, deque
from typing import Dict, List, Tuple

from .models import Task


def validate_tasks(tasks: List[Task]) -> None:
    ids = {t.id for t in tasks}
    if len(ids) != len(tasks):
        raise ValueError("Existem IDs de atividades duplicados.")
    missing = sorted({l.predecessor_id for t in tasks for l in t.predecessors if l.predecessor_id not in ids})
    if missing:
        raise ValueError(f"Há predecessoras que não existem no cronograma: {', '.join(missing[:10])}")


def topological_order(tasks: List[Task]) -> List[str]:
    validate_tasks(tasks)
    indeg = {t.id: 0 for t in tasks}
    succ = defaultdict(list)
    for t in tasks:
        for link in t.predecessors:
            indeg[t.id] += 1
            succ[link.predecessor_id].append(t.id)
    q = deque(sorted([tid for tid, d in indeg.items() if d == 0], key=str))
    out = []
    while q:
        u = q.popleft()
        out.append(u)
        for v in succ[u]:
            indeg[v] -= 1
            if indeg[v] == 0:
                q.append(v)
    if len(out) != len(tasks):
        raise ValueError("O cronograma contém ciclo de precedência.")
    return out


def earliest_precedence_schedule(tasks: List[Task]) -> Dict[str, Tuple[int, int]]:
    """Earliest schedule honoring logical links only, ignoring resources."""
    by_id = {t.id: t for t in tasks}
    order = topological_order(tasks)
    result: Dict[str, Tuple[int, int]] = {}
    for tid in order:
        task = by_id[tid]
        start_lb = 0
        for link in task.predecessors:
            ps, pf = result[link.predecessor_id]
            if link.relation == "FS":
                bound = pf + link.lag_h
            elif link.relation == "SS":
                bound = ps + link.lag_h
            elif link.relation == "FF":
                bound = pf + link.lag_h - task.duration_h
            else:  # SF
                bound = ps + link.lag_h - task.duration_h
            start_lb = max(start_lb, bound)
        start = max(0, start_lb)
        result[tid] = (start, start + task.duration_h)
    return result


def cpm_metrics(tasks: List[Task]) -> Dict[str, dict]:
    """CPM approximation using FS projection for float; link-aware earliest dates are preserved."""
    by_id = {t.id: t for t in tasks}
    order = topological_order(tasks)
    esef = earliest_precedence_schedule(tasks)
    project_finish = max(f for _, f in esef.values())

    succ = defaultdict(list)
    for t in tasks:
        for link in t.predecessors:
            succ[link.predecessor_id].append(t.id)

    lf = {tid: project_finish for tid in order}
    ls = {tid: project_finish - by_id[tid].duration_h for tid in order}
    for tid in reversed(order):
        if succ[tid]:
            lf[tid] = min(ls[s] for s in succ[tid])
            ls[tid] = lf[tid] - by_id[tid].duration_h

    metrics = {}
    for tid in order:
        es, ef = esef[tid]
        float_h = max(0, ls[tid] - es)
        metrics[tid] = {
            "ES": es,
            "EF": ef,
            "LS": ls[tid],
            "LF": lf[tid],
            "Float_h": float_h,
            "Critical": float_h == 0,
        }
    return metrics
