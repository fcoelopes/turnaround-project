from __future__ import annotations

from dataclasses import dataclass, field

from .activation import ActivationResult, ActivationState, resolve_activation
from .advanced_models import ExecutionState, Precedence, TurnaroundProject, TurnaroundTask
from .workforce import WorkforceProfile, skill_requirements
from .mrcpsp import (
    AdvancedScheduleResult,
    AdvancedScheduledTask,
    FixedInterval,
    solve_mrcpsp,
)


@dataclass
class RescheduleResult:
    activation: ActivationResult
    schedule: AdvancedScheduleResult
    frozen_tasks: list[AdvancedScheduledTask]
    newly_scheduled_ids: set[str]
    effective_tasks: list[TurnaroundTask] = field(default_factory=list)


def _propagate_late_predecessors_across_frozen_tasks(
    project: TurnaroundProject,
    activation: ActivationResult,
    frozen_ids: set[str],
    schedulable: list[TurnaroundTask],
) -> list[TurnaroundTask]:
    """Protect future work when late scope appears behind frozen work.

    Frozen history is not rewritten. If a newly active, unfinished task should
    have preceded a frozen task, that unfinished task becomes a conservative
    finish-to-start gate for the frozen task's future successors.
    """

    task_by_id = {task.id: task for task in project.tasks}
    active_ids = activation.active_ids
    cache: dict[str, set[str]] = {}

    def unresolved_before_frozen(
        task_id: str,
        trail: set[str] | None = None,
    ) -> set[str]:
        if task_id in cache:
            return set(cache[task_id])

        trail = set(trail or set())
        if task_id in trail:
            return set()
        trail.add(task_id)

        blockers: set[str] = set()
        task = task_by_id[task_id]
        for precedence in task.precedences:
            predecessor_id = precedence.predecessor_id
            if predecessor_id not in active_ids:
                continue

            if predecessor_id in frozen_ids:
                blockers.update(
                    unresolved_before_frozen(predecessor_id, trail)
                )
            else:
                blockers.add(predecessor_id)

        cache[task_id] = set(blockers)
        return blockers

    repaired: list[TurnaroundTask] = []
    for task in schedulable:
        existing_predecessors = {
            precedence.predecessor_id
            for precedence in task.precedences
        }
        extra_predecessors: set[str] = set()

        for precedence in task.precedences:
            if precedence.predecessor_id not in frozen_ids:
                continue
            extra_predecessors.update(
                unresolved_before_frozen(precedence.predecessor_id)
            )

        extra_predecessors.difference_update(existing_predecessors)
        extra_predecessors.discard(task.id)

        if not extra_predecessors:
            repaired.append(task)
            continue

        repaired.append(
            task.model_copy(
                update={
                    "precedences": [
                        *task.precedences,
                        *[
                            Precedence(
                                predecessor_id=predecessor_id,
                                relation="FS",
                                lag=0.0,
                            )
                            for predecessor_id in sorted(extra_predecessors)
                        ],
                    ]
                }
            )
        )

    return repaired


def reschedule_from_state(
    project: TurnaroundProject,
    state: ExecutionState,
    max_mode_combinations: int = 2000,
    reference_start_times: dict[str, float] | None = None,
    stability_weight: float = 0.0,
    workforce: WorkforceProfile | None = None,
) -> RescheduleResult:
    activation = resolve_activation(project, state)
    task_by_id = {t.id: t for t in project.tasks}

    fixed_intervals: list[FixedInterval] = []
    fixed_task_times: dict[str, tuple[float, float]] = {}
    frozen: list[AdvancedScheduledTask] = []

    for tid, execution in state.executions.items():
        if tid not in task_by_id or execution.status not in {"completed", "in_progress"}:
            continue
        assert execution.start is not None and execution.finish is not None
        task = task_by_id[tid]
        mode = next(
            (m for m in task.modes if m.name == execution.mode_name),
            task.modes[0],
        )
        assignments = {
            skill: tuple(person_ids)
            for skill, person_ids in execution.skill_assignments.items()
        }
        human_requirements = skill_requirements(mode.resources, workforce)
        if (
            execution.status == "in_progress"
            and execution.finish > state.current_time
            and human_requirements
            and not assignments
        ):
            raise ValueError(
                f"Atividade {tid} está em andamento e exige habilidades "
                "multi-skill, mas não possui pessoas congeladas no estado atual"
            )

        fixed_task_times[tid] = (execution.start, execution.finish)
        frozen.append(
            AdvancedScheduledTask(
                task_id=tid,
                task_name=task.name,
                mode_name=mode.name,
                start=execution.start,
                finish=execution.finish,
                duration=max(0.0, execution.finish - execution.start),
                resources=dict(mode.resources),
                cost=mode.cost,
                fixed=True,
                skill_assignments=assignments,
            )
        )
        if execution.status == "in_progress" and execution.finish > state.current_time:
            fixed_intervals.append(
                FixedInterval(
                    task_id=tid,
                    start=state.current_time,
                    finish=execution.finish,
                    resources=dict(mode.resources),
                    skill_assignments=assignments,
                )
            )

    frozen_ids = set(fixed_task_times)
    schedulable = []
    for task in project.tasks:
        if activation.states[task.id] != ActivationState.ACTIVE:
            continue
        execution = state.executions.get(task.id)
        if execution and execution.status in {"completed", "in_progress"}:
            continue
        schedulable.append(task)

    schedulable = _propagate_late_predecessors_across_frozen_tasks(
        project,
        activation,
        frozen_ids,
        schedulable,
    )

    schedule = solve_mrcpsp(
        schedulable,
        project.capacities,
        deadline=project.deadline,
        max_mode_combinations=max_mode_combinations,
        earliest_start=state.current_time,
        fixed_intervals=fixed_intervals,
        fixed_task_times=fixed_task_times,
        reference_start_times=reference_start_times,
        stability_weight=stability_weight,
        workforce=workforce,
    )

    combined_finish = max(
        [schedule.makespan] + [t.finish for t in frozen],
        default=state.current_time,
    )
    schedule.makespan = combined_finish
    if project.deadline is not None:
        schedule.tardiness = max(0.0, combined_finish - project.deadline)
    schedule.total_cost += sum(t.cost for t in frozen)

    return RescheduleResult(
        activation=activation,
        schedule=schedule,
        frozen_tasks=sorted(frozen, key=lambda x: (x.start, x.task_id)),
        newly_scheduled_ids={t.task_id for t in schedule.tasks},
        effective_tasks=schedulable,
    )
