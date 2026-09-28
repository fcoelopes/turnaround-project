from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations

from .activation import resolve_activation
from .advanced_models import ExecutionState, LogicalGroup, TurnaroundProject
from .reschedule import RescheduleResult, reschedule_from_state
from .workforce import WorkforceProfile, effective_capacities


@dataclass(frozen=True)
class DecisionImpact:
    selection: tuple[str, ...]
    feasible: bool
    task_names: tuple[str, ...]
    makespan: float | None = None
    tardiness: float | None = None
    total_cost: float | None = None
    total_start_deviation: float | None = None
    max_start_deviation: float | None = None
    resource_gaps: dict[str, float] = field(default_factory=dict)
    error: str | None = None


@dataclass
class DecisionEvaluation:
    group_id: str
    operator: str
    resolution_mode: str
    impacts: list[DecisionImpact] = field(default_factory=list)
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


def summarize_pending_decisions(
    decisions: list[DecisionEvaluation],
    *,
    current_makespan: float,
    current_cost: float,
) -> list[dict[str, str]]:
    """Resume alternativas pendentes sem escolher entre elas."""
    rows: list[dict[str, str]] = []
    for decision in decisions:
        feasible = [impact for impact in decision.impacts if impact.feasible]
        all_names = []
        for impact in decision.impacts:
            label = " + ".join(impact.task_names)
            if label and label not in all_names:
                all_names.append(label)

        makespan_deltas = [
            float(impact.makespan) - float(current_makespan)
            for impact in feasible
            if impact.makespan is not None
        ]
        cost_deltas = [
            float(impact.total_cost) - float(current_cost)
            for impact in feasible
            if impact.total_cost is not None
        ]

        def format_range(values: list[float], suffix: str) -> str:
            if not values:
                return "sem alternativa factível"
            low = min(values)
            high = max(values)
            if abs(low - high) <= 1e-9:
                return f"{low:+.1f} {suffix}"
            return f"{low:+.1f} a {high:+.1f} {suffix}"

        resource_gaps: dict[str, float] = {}
        for impact in decision.impacts:
            for resource, shortage in impact.resource_gaps.items():
                resource_gaps[resource] = max(
                    resource_gaps.get(resource, 0.0),
                    float(shortage),
                )

        rows.append(
            {
                "Decisão": decision.group_id,
                "Alternativas": " | ".join(all_names) or "—",
                "Impacto prazo": format_range(makespan_deltas, "h"),
                "Impacto custo": format_range(cost_deltas, ""),
                "Recurso crítico": (
                    ", ".join(
                        f"{resource} (faltam {shortage:g})"
                        for resource, shortage in sorted(resource_gaps.items())
                    )
                    if resource_gaps
                    else "sem déficit estático"
                ),
                "Factibilidade": (
                    f"{len(feasible)}/{len(decision.impacts)} alternativa(s) factível(is)"
                ),
            }
        )
    return rows


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

    # OR grande: evita 2^n. A ferramenta avalia apenas um conjunto de
    # cenários sombra para informar impacto; ela não escolhe nenhum deles.
    result = [(task_id,) for task_id in members[:max_or_candidates]]
    if len(result) < max_or_candidates - 1:
        for pair in combinations(members, 2):
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


def _resource_gaps_for_selection(
    project: TurnaroundProject,
    selection: tuple[str, ...],
    workforce: WorkforceProfile | None = None,
) -> dict[str, float]:
    """Retorna faltas estáticas de capacidade para os ramos selecionados.

    Para cada atividade alternativa, se ao menos um modo cabe nas capacidades
    atuais, não há gap estático. Caso nenhum modo caiba, escolhemos o modo com
    menor falta total apenas para explicar o gargalo ao usuário.
    """
    task_by_id = {task.id: task for task in project.tasks}
    capacities = effective_capacities(project.capacities, workforce)
    gaps: dict[str, float] = {}

    for task_id in selection:
        task = task_by_id[task_id]
        feasible_mode_exists = any(
            all(
                demand <= capacities.get(resource, 0.0) + 1e-9
                for resource, demand in mode.resources.items()
            )
            for mode in task.modes
        )
        if feasible_mode_exists:
            continue

        mode_shortages: list[tuple[float, dict[str, float]]] = []
        for mode in task.modes:
            shortages = {
                resource: demand - capacities.get(resource, 0.0)
                for resource, demand in mode.resources.items()
                if demand > capacities.get(resource, 0.0) + 1e-9
            }
            mode_shortages.append((sum(shortages.values()), shortages))

        if not mode_shortages:
            continue

        _, best_explanation = min(mode_shortages, key=lambda item: item[0])
        for resource, shortage in best_explanation.items():
            gaps[resource] = max(gaps.get(resource, 0.0), float(shortage))

    return gaps


def _evaluate_selection(
    project: TurnaroundProject,
    state: ExecutionState,
    group: LogicalGroup,
    selection: tuple[str, ...],
    *,
    max_mode_combinations: int,
    reference_start_times: dict[str, float] | None,
    stability_weight: float,
    workforce: WorkforceProfile | None,
) -> DecisionImpact:
    selections = {
        key: list(value)
        for key, value in state.group_selections.items()
    }
    selections[group.id] = list(selection)
    candidate_state = _copy_state_with_selections(state, selections)
    task_by_id = {task.id: task for task in project.tasks}
    resource_gaps = _resource_gaps_for_selection(
        project,
        selection,
        workforce=workforce,
    )

    try:
        result: RescheduleResult = reschedule_from_state(
            project,
            candidate_state,
            max_mode_combinations=max_mode_combinations,
            reference_start_times=reference_start_times,
            stability_weight=stability_weight,
            workforce=workforce,
        )
    except ValueError as exc:
        return DecisionImpact(
            selection=selection,
            feasible=False,
            task_names=tuple(
                task_by_id[task_id].name
                for task_id in selection
            ),
            resource_gaps=resource_gaps,
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
        total_start_deviation=float(result.schedule.total_start_deviation),
        max_start_deviation=float(result.schedule.max_start_deviation),
        resource_gaps=resource_gaps,
    )


def _event_selection(
    group: LogicalGroup,
    state: ExecutionState,
) -> tuple[str, ...] | None:
    """Aplica apenas uma regra determinística previamente configurada.

    Isto não é uma decisão do scheduler. O evento já codifica qual escopo deve
    entrar; o motor apenas materializa a regra definida pelo planejador.
    """
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
    reference_start_times: dict[str, float] | None = None,
    stability_weight: float = 0.0,
    workforce: WorkforceProfile | None = None,
) -> DecisionEngineResult:
    """Avalia decisões de escopo sob demanda sem decidir a ação técnica.

    - human: quando o gatilho ocorre, simula alternativas e deixa a decisão
      pendente para a pessoa responsável;
    - event: somente para regras determinísticas previamente definidas, nas
      quais o próprio evento já determina qual tarefa deve entrar.

    Não existe modo de resolução por otimização. Makespan, atraso, custo e
    factibilidade são informação de apoio à decisão, nunca critério para o
    scheduler escolher entre reparar/substituir/etc.
    """
    working_state = state.model_copy(deep=True)
    decisions: list[DecisionEvaluation] = []
    auto_resolved: dict[str, list[str]] = {}
    unresolved_event_groups: list[str] = []

    # Apenas regras determinísticas event-driven podem alterar o estado sem
    # intervenção humana. Isso pode liberar outras regras event-driven em cadeia.
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
            if group.resolution_mode != "event":
                continue

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
                reference_start_times=reference_start_times,
                stability_weight=stability_weight,
                workforce=workforce,
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
                    applied_selection=selection,
                    status=(
                        "rule_applied"
                        if impact.feasible
                        else "rule_applied_infeasible"
                    ),
                )
            )
            progressed = True
            break

        if not progressed:
            break

    # Decisões reais são sempre humanas e só aparecem depois do gatilho.
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
                reference_start_times=reference_start_times,
                stability_weight=stability_weight,
                workforce=workforce,
            )
            for selection in candidates
        ]
        decisions.append(
            DecisionEvaluation(
                group_id=group.id,
                operator=group.operator,
                resolution_mode=group.resolution_mode,
                impacts=impacts,
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
