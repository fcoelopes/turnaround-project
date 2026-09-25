from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations

from .activation import resolve_activation
from .advanced_models import ExecutionState, LogicalGroup, TurnaroundProject
from .reschedule import RescheduleResult, reschedule_from_state


@dataclass(frozen=True)
class DecisionImpact:
    selection: tuple[str, ...]
    feasible: bool
    task_names: tuple[str, ...]
    makespan: float | None = None
    tardiness: float | None = None
    total_cost: float | None = None
    error: str | None = None

    @property
    def score(self) -> tuple[float, float, float]:
        if not self.feasible:
            return (float("inf"), float("inf"), float("inf"))
        assert self.tardiness is not None
        assert self.makespan is not None
        assert self.total_cost is not None
        return (self.tardiness, self.makespan, self.total_cost)


@dataclass
class DecisionEvaluation:
    group_id: str
    operator: str
    resolution_mode: str
    impacts: list[DecisionImpact] = field(default_factory=list)
    recommended_selection: tuple[str, ...] | None = None
    applied_selection: tuple[str, ...] | None = None
    exhaustive: bool = True
    status: str = "pending"


@dataclass
class DecisionEngineResult:
    state: ExecutionState
    decisions: list[DecisionEvaluation]
    auto_resolved: dict[str, list[str]]
    unresolved_event_groups: list[str]

    @property
    def pending_human(self) -> list[DecisionEvaluation]:
        return [
            decision
            for decision in self.decisions
            if decision.resolution_mode == "human"
            and decision.status == "pending"
        ]


def _copy_state_with_selections(
    state: ExecutionState,
    selections: dict[str, list[str]],
) -> ExecutionState:
    return state.model_copy(
        update={"group_selections": selections},
        deep=True,
    )


def _candidate_selections(
    group: LogicalGroup,
    *,
    max_or_candidates: int,
) -> tuple[list[tuple[str, ...]], bool]:
    members = list(group.member_task_ids)
    if group.operator == "xor":
        return [(task_id,) for task_id in members], True

    if group.operator != "or":
        return [], True

    total = (2 ** len(members)) - 1
    if total <= max_or_candidates:
        result: list[tuple[str, ...]] = []
        for size in range(1, len(members) + 1):
            result.extend(combinations(members, size))
        return result, True

    # OR grande: evita 2^n. Avalia primeiro escolhas simples e depois pares,
    # reservando uma vaga para "todos". Isso produz boa visibilidade marginal
    # sem explodir o número de cenários sombra.
    result = [(task_id,) for task_id in members[:max_or_candidates]]
    if len(result) < max_or_candidates - 1:
        for pair in combinations(members, 2):
            if pair in result:
                continue
            result.append(pair)
            if len(result) >= max_or_candidates - 1:
                break

    all_members = tuple(members)
    if all_members not in result:
        if len(result) >= max_or_candidates:
            result[-1] = all_members
        else:
            result.append(all_members)
    return result, False


def _evaluate_selection(
    project: TurnaroundProject,
    state: ExecutionState,
    group: LogicalGroup,
    selection: tuple[str, ...],
    *,
    max_mode_combinations: int,
) -> DecisionImpact:
    selections = {
        key: list(value)
        for key, value in state.group_selections.items()
    }
    selections[group.id] = list(selection)
    candidate_state = _copy_state_with_selections(state, selections)
    task_by_id = {task.id: task for task in project.tasks}

    try:
        result: RescheduleResult = reschedule_from_state(
            project,
            candidate_state,
            max_mode_combinations=max_mode_combinations,
        )
    except ValueError as exc:
        return DecisionImpact(
            selection=selection,
            feasible=False,
            task_names=tuple(
                task_by_id[task_id].name
                for task_id in selection
            ),
            error=str(exc),
        )

    return DecisionImpact(
        selection=selection,
        feasible=True,
        task_names=tuple(
            task_by_id[task_id].name
            for task_id in selection
        ),
        makespan=float(result.schedule.makespan),
        tardiness=float(result.schedule.tardiness),
        total_cost=float(result.schedule.total_cost),
    )


def _event_selection(
    group: LogicalGroup,
    state: ExecutionState,
) -> tuple[str, ...] | None:
    assert group.when is not None
    observed = state.events.get(group.when.source_task_id, [])
    selected: list[str] = []
    for event_name in observed:
        for task_id in group.event_routes.get(event_name, []):
            if task_id not in selected:
                selected.append(task_id)

    if not selected:
        return None
    if group.operator == "xor" and len(selected) != 1:
        raise ValueError(
            f"Grupo XOR {group.id}: eventos observados selecionaram "
            f"{len(selected)} ramos; esperado exatamente 1"
        )
    return tuple(selected)


def evaluate_scope_decisions(
    project: TurnaroundProject,
    state: ExecutionState,
    *,
    max_or_candidates: int = 32,
    max_mode_combinations: int = 2000,
) -> DecisionEngineResult:
    """Resolve decisões de escopo sob demanda e avalia cenários sombra.

    - event: o evento observado seleciona o ramo via event_routes;
    - optimize: avalia os ramos e aplica o melhor score
      (tardiness, makespan, custo), um grupo por vez;
    - human: avalia os ramos, mas não aplica nenhum. A UI recebe apenas as
      decisões cujo gatilho já ocorreu.

    Grupos ainda sem gatilho não geram cenários nem controles.
    """
    working_state = state.model_copy(deep=True)
    decisions: list[DecisionEvaluation] = []
    auto_resolved: dict[str, list[str]] = {}
    unresolved_event_groups: list[str] = []

    # Resolve automaticamente event/optimize em rolling horizon. Cada escolha
    # aplicada altera o estado usado para avaliar a próxima decisão.
    while True:
        activation = resolve_activation(project, working_state)
        pending_ids = {
            group_id
            for group_id, group_state in activation.group_states.items()
            if group_state == "pending_selection"
        }
        progressed = False

        for group in project.logical_groups:
            if group.id not in pending_ids:
                continue
            if group.id in working_state.group_selections:
                continue

            if group.resolution_mode == "event":
                selection = _event_selection(group, working_state)
                if selection is None:
                    if group.id not in unresolved_event_groups:
                        unresolved_event_groups.append(group.id)
                    continue
                impact = _evaluate_selection(
                    project,
                    working_state,
                    group,
                    selection,
                    max_mode_combinations=max_mode_combinations,
                )
                selections = {
                    key: list(value)
                    for key, value in working_state.group_selections.items()
                }
                selections[group.id] = list(selection)
                working_state = _copy_state_with_selections(
                    working_state,
                    selections,
                )
                auto_resolved[group.id] = list(selection)
                decisions.append(
                    DecisionEvaluation(
                        group_id=group.id,
                        operator=group.operator,
                        resolution_mode=group.resolution_mode,
                        impacts=[impact],
                        recommended_selection=selection,
                        applied_selection=selection,
                        status=(
                            "auto_resolved"
                            if impact.feasible
                            else "auto_resolved_infeasible"
                        ),
                    )
                )
                progressed = True
                break

            if group.resolution_mode == "optimize":
                candidates, exhaustive = _candidate_selections(
                    group,
                    max_or_candidates=max_or_candidates,
                )
                impacts = [
                    _evaluate_selection(
                        project,
                        working_state,
                        group,
                        selection,
                        max_mode_combinations=max_mode_combinations,
                    )
                    for selection in candidates
                ]
                feasible = [impact for impact in impacts if impact.feasible]
                if not feasible:
                    decisions.append(
                        DecisionEvaluation(
                            group_id=group.id,
                            operator=group.operator,
                            resolution_mode=group.resolution_mode,
                            impacts=impacts,
                            exhaustive=exhaustive,
                            status="infeasible",
                        )
                    )
                    continue

                best = min(feasible, key=lambda impact: impact.score)
                selections = {
                    key: list(value)
                    for key, value in working_state.group_selections.items()
                }
                selections[group.id] = list(best.selection)
                working_state = _copy_state_with_selections(
                    working_state,
                    selections,
                )
                auto_resolved[group.id] = list(best.selection)
                decisions.append(
                    DecisionEvaluation(
                        group_id=group.id,
                        operator=group.operator,
                        resolution_mode=group.resolution_mode,
                        impacts=impacts,
                        recommended_selection=best.selection,
                        applied_selection=best.selection,
                        exhaustive=exhaustive,
                        status="auto_resolved",
                    )
                )
                progressed = True
                break

        if not progressed:
            break

    # Só agora materializa decisões humanas. Grupos dormentes nunca aparecem.
    activation = resolve_activation(project, working_state)
    pending_ids = {
        group_id
        for group_id, group_state in activation.group_states.items()
        if group_state == "pending_selection"
    }

    for group in project.logical_groups:
        if (
            group.id not in pending_ids
            or group.resolution_mode != "human"
            or group.id in working_state.group_selections
        ):
            continue

        candidates, exhaustive = _candidate_selections(
            group,
            max_or_candidates=max_or_candidates,
        )
        impacts = [
            _evaluate_selection(
                project,
                working_state,
                group,
                selection,
                max_mode_combinations=max_mode_combinations,
            )
            for selection in candidates
        ]
        feasible = [impact for impact in impacts if impact.feasible]
        recommended = (
            min(feasible, key=lambda impact: impact.score).selection
            if feasible
            else None
        )
        decisions.append(
            DecisionEvaluation(
                group_id=group.id,
                operator=group.operator,
                resolution_mode=group.resolution_mode,
                impacts=impacts,
                recommended_selection=recommended,
                exhaustive=exhaustive,
                status="pending",
            )
        )

    return DecisionEngineResult(
        state=working_state,
        decisions=decisions,
        auto_resolved=auto_resolved,
        unresolved_event_groups=unresolved_event_groups,
    )
