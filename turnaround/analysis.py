from __future__ import annotations

from collections import defaultdict
from typing import Dict, List

import pandas as pd

from .cpm import cpm_metrics, earliest_precedence_schedule
from .models import Task, TurnaroundResult


def tasks_dataframe(tasks: List[Task]) -> pd.DataFrame:
    rows = []
    for t in tasks:
        rows.append({
            "ID": t.id,
            "Nome": t.name,
            "Duracao_h": t.duration_h,
            "Predecessoras": "; ".join(f"{l.predecessor_id}{l.relation}{l.lag_h:+d}h" for l in t.predecessors),
            "Recursos": "; ".join(f"{r}:{q}" for r, q in t.resources.items()),
            "WBS": t.wbs or "",
        })
    return pd.DataFrame(rows)


def schedule_dataframe(result: TurnaroundResult, hours_per_day: int = 8) -> pd.DataFrame:
    rows = []
    for s in result.schedule:
        rows.append({
            "ID": s.id,
            "Atividade": s.name,
            "Inicio_h": s.start_h,
            "Fim_h": s.finish_h,
            "Duracao_h": s.duration_h,
            "Inicio_dia": round(s.start_h / hours_per_day, 2),
            "Fim_dia": round(s.finish_h / hours_per_day, 2),
            "Recursos": "; ".join(f"{r}:{q}" for r, q in s.resources.items()),
            "WBS": s.wbs or "",
        })
    return pd.DataFrame(rows)


def compare_baseline(tasks: List[Task], optimized: TurnaroundResult) -> Dict[str, float]:
    precedence = earliest_precedence_schedule(tasks)
    unconstrained = max(f for _, f in precedence.values())
    return {
        "unconstrained_makespan_h": unconstrained,
        "optimized_makespan_h": optimized.makespan_h,
        "resource_penalty_h": optimized.makespan_h - unconstrained,
    }


def criticality_dataframe(tasks: List[Task]) -> pd.DataFrame:
    cpm = cpm_metrics(tasks)
    rows = []
    by_id = {t.id: t for t in tasks}
    for tid, m in cpm.items():
        rows.append({
            "ID": tid,
            "Atividade": by_id[tid].name,
            **m,
        })
    return pd.DataFrame(rows).sort_values(["Critical", "Float_h", "ES"], ascending=[False, True, True])


def resource_profile(result: TurnaroundResult) -> pd.DataFrame:
    usage = defaultdict(lambda: [0] * result.makespan_h)
    for s in result.schedule:
        for r, q in s.resources.items():
            for h in range(s.start_h, s.finish_h):
                usage[r][h] += q
    rows = []
    for r, values in usage.items():
        for h, q in enumerate(values):
            rows.append({"Hora": h, "Recurso": r, "Uso": q})
    return pd.DataFrame(rows)
