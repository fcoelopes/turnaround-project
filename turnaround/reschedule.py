from __future__ import annotations

from dataclasses import dataclass

from .activation import ActivationResult, ActivationState, resolve_activation
from .advanced_models import ExecutionState, TurnaroundProject
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


def reschedule_from_state(
    project: TurnaroundProject,
    state: ExecutionState,
    max_mode_combinations: int = 2000,
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
            )
        )
        if execution.status == "in_progress" and execution.finish > state.current_time:
            fixed_intervals.append(
                FixedInterval(
                    task_id=tid,
                    start=state.current_time,
                    finish=execution.finish,
                    resources=dict(mode.resources),
                )
            )

    schedulable = []
    for task in project.tasks:
        if activation.states[task.id] != ActivationState.ACTIVE:
            continue
        execution = state.executions.get(task.id)
        if execution and execution.status in {"completed", "in_progress"}:
            continue
        schedulable.append(task)

    schedule = solve_mrcpsp(
        schedulable,
        project.capacities,
        deadline=project.deadline,
        max_mode_combinations=max_mode_combinations,
        earliest_start=state.current_time,
        fixed_intervals=fixed_intervals,
        fixed_task_times=fixed_task_times,
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
    )
