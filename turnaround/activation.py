from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .advanced_models import (
    ExecutionState,
    LogicalGroup,
    TriggerCondition,
    TurnaroundProject,
    TurnaroundTask,
)


class ActivationState(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    PENDING = "pending"


@dataclass
class ActivationResult:
    states: dict[str, ActivationState]
    reasons: dict[str, str]
    group_states: dict[str, str] = field(default_factory=dict)

    @property
    def active_ids(self) -> set[str]:
        return {tid for tid, state in self.states.items() if state == ActivationState.ACTIVE}

    @property
    def pending_ids(self) -> set[str]:
        return {tid for tid, state in self.states.items() if state == ActivationState.PENDING}

    @property
    def inactive_ids(self) -> set[str]:
        return {tid for tid, state in self.states.items() if state == ActivationState.INACTIVE}


def _condition_state(condition: TriggerCondition, state: ExecutionState) -> bool | None:
    execution = state.executions.get(condition.source_task_id)
    if condition.require_completed:
        if execution is None or execution.status not in {"completed", "skipped"}:
            return None
        if execution.status == "skipped":
            return False

    observed = set(state.events.get(condition.source_task_id, []))
    expected = set(condition.events)
    if condition.event_logic == "all":
        return expected.issubset(observed)
    return bool(expected & observed)


def _combine(values: list[bool | None], logic: str) -> bool | None:
    if logic == "all":
        if any(v is False for v in values):
            return False
        if any(v is None for v in values):
            return None
        return True
    if any(v is True for v in values):
        return True
    if any(v is None for v in values):
        return None
    return False


def _base_state(task: TurnaroundTask, state: ExecutionState) -> tuple[ActivationState, str]:
    kind = task.activation.kind
    if kind == "mandatory":
        return ActivationState.ACTIVE, "atividade obrigatória"
    if kind == "optional":
        if task.id in set(state.selected_optional_ids):
            return ActivationState.ACTIVE, "atividade opcional selecionada"
        return ActivationState.INACTIVE, "atividade opcional não selecionada"

    values = [_condition_state(c, state) for c in task.activation.conditions]
    result = _combine(values, task.activation.condition_logic)
    if result is True:
        return ActivationState.ACTIVE, "condição de ativação satisfeita"
    if result is None:
        return ActivationState.PENDING, "aguardando atividade/evento gatilho"
    return ActivationState.INACTIVE, "condição de ativação não satisfeita"


def _apply_group(
    group: LogicalGroup,
    states: dict[str, ActivationState],
    reasons: dict[str, str],
    state: ExecutionState,
) -> str:
    when = True if group.when is None else _condition_state(group.when, state)
    if when is None:
        return "pending_trigger"
    if when is False:
        return "inactive_trigger"

    members = set(group.member_task_ids)
    if group.operator == "and":
        for tid in members:
            states[tid] = ActivationState.ACTIVE
            reasons[tid] = f"ativada pelo grupo AND {group.id}"
        return "resolved"

    selected = set(state.group_selections.get(group.id, []))
    unknown = selected - members
    if unknown:
        raise ValueError(f"Grupo {group.id}: seleção contém membros inválidos {sorted(unknown)}")

    if group.operator == "xor":
        if not selected:
            for tid in members:
                if states[tid] != ActivationState.ACTIVE:
                    states[tid] = ActivationState.PENDING
                    reasons[tid] = f"aguardando seleção XOR do grupo {group.id}"
            return "pending_selection"
        if len(selected) != 1:
            raise ValueError(f"Grupo XOR {group.id} exige exatamente uma seleção")
    elif group.operator == "or":
        if not selected:
            for tid in members:
                if states[tid] != ActivationState.ACTIVE:
                    states[tid] = ActivationState.PENDING
                    reasons[tid] = f"aguardando uma ou mais seleções OR do grupo {group.id}"
            return "pending_selection"

    for tid in members:
        if tid in selected:
            states[tid] = ActivationState.ACTIVE
            reasons[tid] = f"selecionada pelo grupo {group.operator.upper()} {group.id}"
        else:
            states[tid] = ActivationState.INACTIVE
            reasons[tid] = f"não selecionada pelo grupo {group.operator.upper()} {group.id}"
    return "resolved"


def resolve_activation(project: TurnaroundProject, state: ExecutionState) -> ActivationResult:
    states: dict[str, ActivationState] = {}
    reasons: dict[str, str] = {}
    for task in project.tasks:
        states[task.id], reasons[task.id] = _base_state(task, state)

    group_states: dict[str, str] = {}
    for group in project.logical_groups:
        group_states[group.id] = _apply_group(group, states, reasons, state)

    for tid, execution in state.executions.items():
        if tid in states and execution.status in {"in_progress", "completed"}:
            states[tid] = ActivationState.ACTIVE
            reasons[tid] = f"atividade já {execution.status}"

    changed = True
    task_by_id = {t.id: t for t in project.tasks}
    while changed:
        changed = False
        for tid, current in list(states.items()):
            if current != ActivationState.PENDING:
                continue
            task = task_by_id[tid]
            if task.activation.kind != "conditional":
                continue
            values: list[bool | None] = []
            for condition in task.activation.conditions:
                source_execution = state.executions.get(condition.source_task_id)
                if (
                    states.get(condition.source_task_id) == ActivationState.INACTIVE
                    and source_execution is None
                ):
                    values.append(False)
                else:
                    values.append(_condition_state(condition, state))
            resolved = _combine(values, task.activation.condition_logic)
            if resolved is False:
                states[tid] = ActivationState.INACTIVE
                reasons[tid] = "gatilho inativo ou condição não satisfeita"
                changed = True
            elif resolved is True:
                states[tid] = ActivationState.ACTIVE
                reasons[tid] = "condição de ativação satisfeita"
                changed = True

    return ActivationResult(states=states, reasons=reasons, group_states=group_states)
