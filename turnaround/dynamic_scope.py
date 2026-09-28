from __future__ import annotations

from dataclasses import dataclass

from .advanced_models import (
    ActivationRule,
    DiscoveredTask,
    ExecutionState,
    Precedence,
    TurnaroundProject,
    TurnaroundTask,
)
from .reschedule import reschedule_from_state
from .workforce import WorkforceProfile


@dataclass(frozen=True)
class DynamicScopeMaterialization:
    project: TurnaroundProject
    discovered_ids: set[str]


def evaluate_dynamic_scope_impacts(
    *,
    planned_project: TurnaroundProject,
    discovered_tasks: list[DiscoveredTask],
    state: ExecutionState,
    capacities: dict[str, float],
    current_makespan: float,
    critical_ids: set[str] | None = None,
    reference_start_times: dict[str, float] | None = None,
    stability_weight: float = 0.0,
    workforce: WorkforceProfile | None = None,
) -> list[dict[str, object]]:
    """Estima o impacto marginal de DS-* relevantes por contrafactual individual.

    Para cada descoberta relevante, recompõe o projeto sem aquela atividade e
    reprograma o mesmo estado operacional. Se a remoção quebrar dependências
    entre atividades descobertas, o impacto é marcado como não isolável.
    """
    critical_ids = set(critical_ids or set())
    rows: list[dict[str, object]] = []

    for item in discovered_tasks:
        should_evaluate = (
            item.id in critical_ids
            or bool(item.successor_task_ids)
        )
        impact_h: float | None = None
        diagnostic = ""

        if should_evaluate:
            counterfactual_tasks = [
                candidate
                for candidate in discovered_tasks
                if candidate.id != item.id
            ]
            try:
                counterfactual = materialize_dynamic_scope(
                    planned_project,
                    counterfactual_tasks,
                ).project.model_copy(
                    update={"capacities": dict(capacities)}
                )
                counterfactual_result = reschedule_from_state(
                    counterfactual,
                    state,
                    reference_start_times=reference_start_times,
                    stability_weight=stability_weight,
                    workforce=workforce,
                )
                impact_h = (
                    float(current_makespan)
                    - float(counterfactual_result.schedule.makespan)
                )
            except ValueError as exc:
                diagnostic = str(exc)

        relevant = (
            (impact_h is not None and impact_h > 1e-9)
            or item.id in critical_ids
            or bool(item.successor_task_ids)
        )
        if not relevant:
            continue

        rows.append(
            {
                "id": item.id,
                "name": item.name,
                "discovered_at": float(item.discovered_at),
                "impact_h": impact_h,
                "successor_task_ids": list(item.successor_task_ids),
                "critical": item.id in critical_ids,
                "diagnostic": diagnostic,
            }
        )

    rows.sort(
        key=lambda row: (
            -(
                float(row["impact_h"])
                if row["impact_h"] is not None
                else float("-inf")
            ),
            str(row["id"]),
        )
    )
    return rows


def next_discovered_task_id(
    project: TurnaroundProject,
    discovered_tasks: list[DiscoveredTask],
    *,
    prefix: str = "DS",
) -> str:
    existing = {task.id for task in project.tasks}
    existing.update(task.id for task in discovered_tasks)
    index = 1
    while True:
        candidate = f"{prefix}-{index:03d}"
        if candidate not in existing:
            return candidate
        index += 1


def _validate_acyclic(tasks: list[TurnaroundTask]) -> None:
    predecessors = {
        task.id: [p.predecessor_id for p in task.precedences]
        for task in tasks
    }
    state: dict[str, int] = {}
    stack: list[str] = []

    def visit(task_id: str) -> None:
        marker = state.get(task_id, 0)
        if marker == 2:
            return
        if marker == 1:
            try:
                start = stack.index(task_id)
                cycle = [*stack[start:], task_id]
            except ValueError:
                cycle = [task_id, task_id]
            raise ValueError(
                "Dynamic scope criou ciclo de precedência: "
                + " -> ".join(cycle)
            )

        state[task_id] = 1
        stack.append(task_id)
        for predecessor_id in predecessors.get(task_id, []):
            visit(predecessor_id)
        stack.pop()
        state[task_id] = 2

    for task_id in predecessors:
        visit(task_id)


def materialize_dynamic_scope(
    project: TurnaroundProject,
    discovered_tasks: list[DiscoveredTask],
) -> DynamicScopeMaterialization:
    """Injeta atividades realmente descobertas durante a execução.

    As atividades descobertas não fazem parte do baseline. Quando materializadas:
    - tornam-se mandatory no projeto efetivo;
    - podem depender de tarefas existentes ou de outras atividades descobertas;
    - podem bloquear atividades futuras existentes por successor_task_ids;
    - preservam o projeto de origem: um novo TurnaroundProject é retornado.
    """
    if not discovered_tasks:
        return DynamicScopeMaterialization(
            project=project,
            discovered_ids=set(),
        )

    base_ids = {task.id for task in project.tasks}
    discovered_ids = [task.id for task in discovered_tasks]
    if len(discovered_ids) != len(set(discovered_ids)):
        duplicates = sorted(
            task_id
            for task_id in set(discovered_ids)
            if discovered_ids.count(task_id) > 1
        )
        raise ValueError(
            "IDs de atividades descobertas devem ser únicos: "
            + ", ".join(duplicates)
        )

    collisions = base_ids & set(discovered_ids)
    if collisions:
        raise ValueError(
            "Atividades descobertas colidem com IDs do cronograma-base: "
            + ", ".join(sorted(collisions))
        )

    all_ids = base_ids | set(discovered_ids)

    for item in discovered_tasks:
        referenced = {p.predecessor_id for p in item.precedences}
        referenced.update(item.successor_task_ids)
        if item.source_task_id:
            referenced.add(item.source_task_id)
        missing = referenced - all_ids
        if missing:
            raise ValueError(
                f"Atividade descoberta {item.id}: referências desconhecidas "
                f"{sorted(missing)}"
            )

    discovered_as_tasks = [
        TurnaroundTask(
            id=item.id,
            name=item.name,
            wbs=item.wbs,
            project_uid=None,
            release_time=item.discovered_at,
            modes=item.modes,
            precedences=item.precedences,
            activation=ActivationRule(kind="mandatory"),
        )
        for item in discovered_tasks
    ]

    successor_gates: dict[str, list[str]] = {}
    for item in discovered_tasks:
        for successor_id in item.successor_task_ids:
            successor_gates.setdefault(successor_id, []).append(item.id)

    tasks: list[TurnaroundTask] = []
    for task in [*project.tasks, *discovered_as_tasks]:
        gates = successor_gates.get(task.id, [])
        if not gates:
            tasks.append(task)
            continue

        existing = {p.predecessor_id for p in task.precedences}
        extra = [
            Precedence(
                predecessor_id=gate_id,
                relation="FS",
                lag=0.0,
            )
            for gate_id in gates
            if gate_id not in existing
        ]
        tasks.append(
            task.model_copy(
                update={"precedences": [*task.precedences, *extra]}
            )
        )

    _validate_acyclic(tasks)

    effective = TurnaroundProject(
        tasks=tasks,
        capacities=dict(project.capacities),
        deadline=project.deadline,
        logical_groups=project.logical_groups,
        resource_calendars=dict(project.resource_calendars),
    )
    return DynamicScopeMaterialization(
        project=effective,
        discovered_ids=set(discovered_ids),
    )
