from __future__ import annotations

from dataclasses import dataclass

from .advanced_models import (
    ActivationRule,
    DiscoveredTask,
    Precedence,
    TurnaroundProject,
    TurnaroundTask,
)


@dataclass(frozen=True)
class DynamicScopeMaterialization:
    project: TurnaroundProject
    discovered_ids: set[str]


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

    effective = TurnaroundProject(
        tasks=tasks,
        capacities=dict(project.capacities),
        deadline=project.deadline,
        logical_groups=project.logical_groups,
    )
    return DynamicScopeMaterialization(
        project=effective,
        discovered_ids=set(discovered_ids),
    )
