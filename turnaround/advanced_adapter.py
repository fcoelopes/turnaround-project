from __future__ import annotations

from .advanced_models import ExecutionMode, Precedence, TurnaroundProject, TurnaroundTask
from .models import Task


def project_from_tasks(
    tasks: list[Task],
    capacities: dict[str, int | float],
    *,
    deadline: float | None = None,
) -> TurnaroundProject:
    """Converte o domínio existente do MVP para o domínio MRCPSP/conditional.

    O parser atual continua sendo a única fonte de verdade para XML/CSV/Excel.
    Cada Task entra inicialmente com um único modo "base"; o sidecar JSON pode
    substituir esse modo por alternativas e alterar a regra de ativação.
    """
    converted = []
    for task in tasks:
        converted.append(
            TurnaroundTask(
                id=task.id,
                name=task.name,
                wbs=task.wbs,
                modes=[
                    ExecutionMode(
                        name="base",
                        duration=float(task.duration_h),
                        resources={k: float(v) for k, v in task.resources.items()},
                    )
                ],
                precedences=[
                    Precedence(
                        predecessor_id=link.predecessor_id,
                        relation=link.relation,
                        lag=float(link.lag_h),
                    )
                    for link in task.predecessors
                ],
            )
        )
    return TurnaroundProject(
        tasks=converted,
        capacities={k: float(v) for k, v in capacities.items()},
        deadline=deadline,
    )
