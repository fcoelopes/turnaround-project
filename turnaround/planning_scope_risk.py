from __future__ import annotations

import io
import math
import re
from dataclasses import replace
from typing import Iterable

import pandas as pd
from pydantic import BaseModel, Field, model_validator

from .advanced_models import ActivationRule, TriggerCondition, TurnaroundProject
from .models import Task


class PlanningScopeRisk(BaseModel):
    task_id: str
    trigger_task_id: str
    event_name: str
    probability: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_ids(self):
        if self.task_id == self.trigger_task_id:
            raise ValueError("A atividade potencial não pode ser seu próprio gatilho.")
        if not self.event_name.strip():
            raise ValueError("Informe o evento que representa a ampliação de escopo.")
        return self

    @property
    def event_key(self) -> tuple[str, str]:
        return (self.trigger_task_id, self.event_name.strip())


def _norm(value: object) -> str:
    text = str(value).replace("\ufeff", "").strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text


def _find_column(columns: Iterable[str], aliases: list[str]) -> str | None:
    normalized = {_norm(column): column for column in columns}
    for alias in aliases:
        if _norm(alias) in normalized:
            return normalized[_norm(alias)]
    return None


def _normalize_id(value: object) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    text = str(value).strip()
    return text[:-2] if text.endswith(".0") else text


def extract_scope_risk_candidates(uploaded_file) -> list[PlanningScopeRisk]:
    """Read optional planning-only scope-risk metadata from CSV/Excel.

    Supported columns:
      Tipo_escopo | scope_type
      Gatilho_ID | trigger_id
      Evento_sugerido | Evento | event
      Probabilidade_% | Probabilidade | probability

    Missing probability defaults to zero so the planner must explicitly choose it.
    """
    name = uploaded_file.name.lower()
    content = uploaded_file.getvalue()
    if name.endswith(".csv"):
        try:
            frame = pd.read_csv(
                io.BytesIO(content),
                sep=None,
                engine="python",
                encoding="utf-8-sig",
            )
        except UnicodeDecodeError:
            frame = pd.read_csv(
                io.BytesIO(content),
                sep=None,
                engine="python",
                encoding="latin1",
            )
    elif name.endswith((".xlsx", ".xls")):
        frame = pd.read_excel(io.BytesIO(content))
    else:
        return []

    id_col = _find_column(frame.columns, ["ID", "task id"])
    type_col = _find_column(
        frame.columns,
        ["Tipo_escopo", "Tipo escopo", "scope_type", "scope type"],
    )
    trigger_col = _find_column(
        frame.columns,
        ["Gatilho_ID", "Gatilho ID", "trigger_id", "trigger id"],
    )
    event_col = _find_column(
        frame.columns,
        ["Evento_sugerido", "Evento sugerido", "Evento", "event"],
    )
    probability_col = _find_column(
        frame.columns,
        [
            "Probabilidade_%",
            "Probabilidade %",
            "Probabilidade",
            "probability",
            "probability_pct",
        ],
    )

    if not id_col or not type_col or not trigger_col or not event_col:
        return []

    result: list[PlanningScopeRisk] = []
    for _, row in frame.iterrows():
        scope_type = _norm(row[type_col])
        if scope_type not in {"conditional", "potential", "probabilistic"}:
            continue

        task_id = _normalize_id(row[id_col])
        trigger_id = _normalize_id(row[trigger_col])
        event_name = "" if pd.isna(row[event_col]) else str(row[event_col]).strip()
        if not task_id or not trigger_id or not event_name:
            continue

        probability = 0.0
        if probability_col and not pd.isna(row[probability_col]):
            raw_probability = float(row[probability_col])
            probability = (
                raw_probability / 100.0
                if raw_probability > 1.0
                else raw_probability
            )
            probability = min(1.0, max(0.0, probability))

        result.append(
            PlanningScopeRisk(
                task_id=task_id,
                trigger_task_id=trigger_id,
                event_name=event_name,
                probability=probability,
            )
        )
    return result


def materialize_planning_scope(
    tasks: list[Task],
    scope_risks: list[PlanningScopeRisk],
    active_scope_task_ids: set[str] | None = None,
) -> list[Task]:
    """Materialize the project scope for one planning scenario.

    Tasks listed in scope_risks are potential scope. They are excluded unless
    explicitly activated. Precedence links pointing to inactive potential tasks
    are removed, so dormant gates do not block mandatory work.
    """
    active_scope_task_ids = set(active_scope_task_ids or set())
    potential_ids = {item.task_id for item in scope_risks}
    all_ids = {task.id for task in tasks}

    unknown = potential_ids - all_ids
    if unknown:
        raise ValueError(
            "Atividades de escopo potencial inexistentes: "
            + ", ".join(sorted(unknown))
        )

    active_ids = (all_ids - potential_ids) | active_scope_task_ids
    materialized: list[Task] = []
    for task in tasks:
        if task.id not in active_ids:
            continue
        materialized.append(
            replace(
                task,
                predecessors=[
                    link
                    for link in task.predecessors
                    if link.predecessor_id in active_ids
                ],
            )
        )
    return materialized


def apply_planning_scope_risks_to_project(
    project: TurnaroundProject,
    scope_risks: list[PlanningScopeRisk],
) -> TurnaroundProject:
    """Translate planning uncertainty into execution-time conditional rules."""
    if not scope_risks:
        return project

    risk_by_task: dict[str, PlanningScopeRisk] = {}
    for item in scope_risks:
        if item.task_id in risk_by_task:
            raise ValueError(
                f"Atividade {item.task_id} possui mais de uma regra de risco de escopo."
            )
        risk_by_task[item.task_id] = item

    project_ids = {task.id for task in project.tasks}
    for item in scope_risks:
        if item.task_id not in project_ids:
            raise ValueError(
                f"Atividade potencial {item.task_id} não existe no projeto aprovado."
            )
        if item.trigger_task_id not in project_ids:
            raise ValueError(
                f"Gatilho {item.trigger_task_id} não existe no projeto aprovado."
            )

    tasks = []
    for task in project.tasks:
        item = risk_by_task.get(task.id)
        if item is None:
            tasks.append(task)
            continue
        tasks.append(
            task.model_copy(
                update={
                    "activation": ActivationRule(
                        kind="conditional",
                        conditions=[
                            TriggerCondition(
                                source_task_id=item.trigger_task_id,
                                events=[item.event_name],
                                event_logic="any",
                                require_completed=True,
                            )
                        ],
                        condition_logic="all",
                    )
                }
            )
        )

    return project.model_copy(update={"tasks": tasks})
