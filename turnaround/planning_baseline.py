from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from .advanced_models import TurnaroundProject
from .calendar import CalendarBlock, DailyShift, OvertimeWindow, WorkingCalendar
from .models import Link, Task, TurnaroundResult
from .mrcpsp import AdvancedScheduleResult, AdvancedScheduledTask
from .planning_scope_risk import PlanningScopeRisk


class PlanningLinkSnapshot(BaseModel):
    predecessor_id: str
    relation: str = "FS"
    lag_h: int = 0

    @classmethod
    def from_link(cls, link: Link) -> "PlanningLinkSnapshot":
        return cls(
            predecessor_id=link.predecessor_id,
            relation=link.relation,
            lag_h=int(link.lag_h),
        )

    def to_link(self) -> Link:
        return Link(
            predecessor_id=self.predecessor_id,
            relation=self.relation,
            lag_h=int(self.lag_h),
        )


class PlanningTaskSnapshot(BaseModel):
    id: str
    name: str
    duration_h: int = Field(gt=0)
    predecessors: list[PlanningLinkSnapshot] = Field(default_factory=list)
    resources: dict[str, int] = Field(default_factory=dict)
    baseline_start: str | None = None
    baseline_finish: str | None = None
    wbs: str | None = None
    project_uid: str | None = None

    @classmethod
    def from_task(cls, task: Task) -> "PlanningTaskSnapshot":
        return cls(
            id=task.id,
            name=task.name,
            duration_h=int(task.duration_h),
            predecessors=[
                PlanningLinkSnapshot.from_link(link)
                for link in task.predecessors
            ],
            resources={
                str(resource): int(quantity)
                for resource, quantity in task.resources.items()
            },
            baseline_start=task.baseline_start,
            baseline_finish=task.baseline_finish,
            wbs=task.wbs,
            project_uid=task.project_uid,
        )

    def to_task(self) -> Task:
        return Task(
            id=self.id,
            name=self.name,
            duration_h=int(self.duration_h),
            predecessors=[item.to_link() for item in self.predecessors],
            resources={
                str(resource): int(quantity)
                for resource, quantity in self.resources.items()
            },
            baseline_start=self.baseline_start,
            baseline_finish=self.baseline_finish,
            wbs=self.wbs,
            project_uid=self.project_uid,
        )


class PlanningScheduleItemSnapshot(BaseModel):
    task_id: str
    task_name: str
    start_h: float = Field(ge=0)
    finish_h: float = Field(ge=0)
    duration_h: float = Field(gt=0)
    resources: dict[str, float] = Field(default_factory=dict)
    wbs: str | None = None


class PlanningShiftSnapshot(BaseModel):
    start_hour: float
    end_hour: float


class PlanningCalendarBlockSnapshot(BaseModel):
    start_h: float
    end_h: float
    reason: str | None = None


class PlanningOvertimeSnapshot(BaseModel):
    start_h: float
    end_h: float
    reason: str | None = None


class PlanningResourceCalendarSnapshot(BaseModel):
    name: str
    origin_hour: float = 0.0
    shifts: list[PlanningShiftSnapshot]
    blocks: list[PlanningCalendarBlockSnapshot] = Field(default_factory=list)
    overtime_windows: list[PlanningOvertimeSnapshot] = Field(default_factory=list)

    @classmethod
    def from_calendar(
        cls,
        calendar: WorkingCalendar,
    ) -> "PlanningResourceCalendarSnapshot":
        return cls(
            name=calendar.name,
            origin_hour=float(calendar.origin_hour),
            shifts=[
                PlanningShiftSnapshot(
                    start_hour=float(shift.start_hour),
                    end_hour=float(shift.end_hour),
                )
                for shift in calendar.shifts
            ],
            blocks=[
                PlanningCalendarBlockSnapshot(
                    start_h=float(block.start_h),
                    end_h=float(block.end_h),
                    reason=block.reason,
                )
                for block in calendar.blocks
            ],
            overtime_windows=[
                PlanningOvertimeSnapshot(
                    start_h=float(window.start_h),
                    end_h=float(window.end_h),
                    reason=window.reason,
                )
                for window in calendar.overtime_windows
            ],
        )

    def to_calendar(self) -> WorkingCalendar:
        return WorkingCalendar(
            name=self.name,
            origin_hour=float(self.origin_hour),
            shifts=tuple(
                DailyShift(
                    float(shift.start_hour),
                    float(shift.end_hour),
                )
                for shift in self.shifts
            ),
            blocks=tuple(
                CalendarBlock(
                    start_h=float(block.start_h),
                    end_h=float(block.end_h),
                    reason=block.reason,
                )
                for block in self.blocks
            ),
            overtime_windows=tuple(
                OvertimeWindow(
                    start_h=float(window.start_h),
                    end_h=float(window.end_h),
                    reason=window.reason,
                )
                for window in self.overtime_windows
            ),
        )


class PlanningRiskAssumptions(BaseModel):
    simulations: int = Field(ge=1)
    optimistic_pct: float
    most_likely_pct: float
    pessimistic_pct: float


class ApprovedPlanningBaseline(BaseModel):
    key: str = Field(min_length=64, max_length=64)
    project_name: str
    source_name: str
    scenario_name: str | None = None
    approved_by: str | None = None
    approval_reason: str | None = None
    hours_per_day: int = Field(ge=1, le=24)
    deadline_h: float | None = Field(default=None, gt=0)
    capacities: dict[str, float]
    capacity_origins: dict[str, str] = Field(default_factory=dict)
    capacities_validated: bool = False
    tasks: list[PlanningTaskSnapshot]
    schedule: list[PlanningScheduleItemSnapshot]
    makespan_h: float = Field(gt=0)
    priority_rule: str
    risk_p80_h: float | None = None
    probability_meet_deadline: float | None = None
    risk_assumptions: PlanningRiskAssumptions | None = None
    scope_risks: list[PlanningScopeRisk] = Field(default_factory=list)
    resource_calendars: dict[str, PlanningResourceCalendarSnapshot] = Field(
        default_factory=dict
    )
    approved_at: datetime

    def dangling_predecessor_links(self) -> list[tuple[str, str]]:
        known_ids = {item.id for item in self.tasks}
        return [
            (item.id, link.predecessor_id)
            for item in self.tasks
            for link in item.predecessors
            if link.predecessor_id not in known_ids
        ]

    def to_tasks(
        self,
        *,
        drop_dangling_predecessors: bool = False,
    ) -> list[Task]:
        tasks = [item.to_task() for item in self.tasks]
        if not drop_dangling_predecessors:
            return tasks

        known_ids = {task.id for task in tasks}
        return [
            replace(
                task,
                predecessors=[
                    link
                    for link in task.predecessors
                    if link.predecessor_id in known_ids
                ],
            )
            for task in tasks
        ]

    def to_resource_calendars(self) -> dict[str, WorkingCalendar]:
        return {
            resource: snapshot.to_calendar()
            for resource, snapshot in self.resource_calendars.items()
        }

    def reference_start_times(self) -> dict[str, float]:
        return {
            item.task_id: float(item.start_h)
            for item in self.schedule
        }


def _core_payload(
    *,
    project_name: str,
    hours_per_day: int,
    deadline_h: float | None,
    capacities: dict[str, float],
    capacity_origins: dict[str, str],
    tasks: list[PlanningTaskSnapshot],
    schedule: list[PlanningScheduleItemSnapshot],
    makespan_h: float,
    priority_rule: str,
    risk_assumptions: PlanningRiskAssumptions | None,
    scope_risks: list[PlanningScopeRisk],
    resource_calendars: dict[str, PlanningResourceCalendarSnapshot],
) -> dict:
    return {
        "project_name": project_name,
        "hours_per_day": int(hours_per_day),
        "deadline_h": (
            None
            if deadline_h is None
            else float(deadline_h)
        ),
        "capacities": {
            str(key): float(value)
            for key, value in sorted(capacities.items())
        },
        "capacity_origins": {
            str(key): str(value)
            for key, value in sorted(capacity_origins.items())
        },
        "tasks": [
            item.model_dump(mode="json")
            for item in tasks
        ],
        "schedule": [
            item.model_dump(mode="json")
            for item in schedule
        ],
        "makespan_h": float(makespan_h),
        "priority_rule": priority_rule,
        "risk_assumptions": (
            None
            if risk_assumptions is None
            else risk_assumptions.model_dump(mode="json")
        ),
        "scope_risks": [
            item.model_dump(mode="json")
            for item in scope_risks
        ],
        "resource_calendars": {
            resource: snapshot.model_dump(mode="json")
            for resource, snapshot in sorted(resource_calendars.items())
        },
    }


def build_planning_baseline(
    *,
    project_name: str,
    source_name: str,
    hours_per_day: int,
    deadline_h: float | None,
    capacities: dict[str, int | float],
    capacity_origins: dict[str, str] | None = None,
    capacities_validated: bool = False,
    tasks: list[Task],
    result: TurnaroundResult,
    risk: dict | None = None,
    risk_assumptions: PlanningRiskAssumptions | dict | None = None,
    scope_risks: list[PlanningScopeRisk] | None = None,
    resource_calendars: dict[str, WorkingCalendar] | None = None,
    scenario_name: str | None = None,
    approved_by: str | None = None,
    approval_reason: str | None = None,
) -> ApprovedPlanningBaseline:
    task_snapshots = [
        PlanningTaskSnapshot.from_task(task)
        for task in tasks
    ]
    schedule_snapshots = [
        PlanningScheduleItemSnapshot(
            task_id=item.id,
            task_name=item.name,
            start_h=float(item.start_h),
            finish_h=float(item.finish_h),
            duration_h=float(item.duration_h),
            resources={
                str(resource): float(quantity)
                for resource, quantity in item.resources.items()
            },
            wbs=item.wbs,
        )
        for item in result.schedule
    ]
    normalized_capacities = {
        str(resource): float(quantity)
        for resource, quantity in capacities.items()
    }
    scope_risks = list(scope_risks or [])
    normalized_risk_assumptions = (
        None
        if risk_assumptions is None
        else PlanningRiskAssumptions.model_validate(risk_assumptions)
    )
    normalized_capacity_origins = {
        str(resource): str(origin)
        for resource, origin in (capacity_origins or {}).items()
    }
    normalized_resource_calendars = {
        str(resource): PlanningResourceCalendarSnapshot.from_calendar(calendar)
        for resource, calendar in (resource_calendars or {}).items()
    }

    core = _core_payload(
        project_name=project_name,
        hours_per_day=hours_per_day,
        deadline_h=deadline_h,
        capacities=normalized_capacities,
        capacity_origins=normalized_capacity_origins,
        tasks=task_snapshots,
        schedule=schedule_snapshots,
        makespan_h=float(result.makespan_h),
        priority_rule=result.priority_rule,
        risk_assumptions=normalized_risk_assumptions,
        scope_risks=scope_risks,
        resource_calendars=normalized_resource_calendars,
    )
    key = hashlib.sha256(
        json.dumps(
            core,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()

    return ApprovedPlanningBaseline(
        key=key,
        project_name=project_name,
        source_name=source_name,
        scenario_name=(scenario_name.strip() if scenario_name else None),
        approved_by=(approved_by.strip() if approved_by else None),
        approval_reason=(approval_reason.strip() if approval_reason else None),
        hours_per_day=int(hours_per_day),
        deadline_h=(
            None
            if deadline_h is None
            else float(deadline_h)
        ),
        capacities=normalized_capacities,
        capacity_origins=normalized_capacity_origins,
        capacities_validated=bool(capacities_validated),
        tasks=task_snapshots,
        schedule=schedule_snapshots,
        makespan_h=float(result.makespan_h),
        priority_rule=result.priority_rule,
        risk_p80_h=(
            None
            if not risk
            else float(risk["p80_h"])
        ),
        probability_meet_deadline=(
            None
            if not risk
            or risk.get("probability_meet_deadline") is None
            else float(risk["probability_meet_deadline"])
        ),
        risk_assumptions=normalized_risk_assumptions,
        scope_risks=scope_risks,
        resource_calendars=normalized_resource_calendars,
        approved_at=datetime.now(timezone.utc),
    )


def approved_schedule_matches_project(
    baseline: ApprovedPlanningBaseline,
    project: TurnaroundProject,
    active_ids: set[str],
) -> bool:
    schedule_by_id = {
        item.task_id: item
        for item in baseline.schedule
    }
    task_by_id = {
        task.id: task
        for task in project.tasks
    }

    if not active_ids.issubset(schedule_by_id):
        return False
    if not active_ids.issubset(task_by_id):
        return False

    for task_id in active_ids:
        scheduled = schedule_by_id[task_id]
        task = task_by_id[task_id]
        base_mode = next(
            (mode for mode in task.modes if mode.name == "base"),
            None,
        )
        if base_mode is None:
            return False
        if abs(float(base_mode.duration) - float(scheduled.duration_h)) > 1e-9:
            return False

        expected_resources = {
            str(key): float(value)
            for key, value in scheduled.resources.items()
        }
        actual_resources = {
            str(key): float(value)
            for key, value in base_mode.resources.items()
        }
        if actual_resources != expected_resources:
            return False

    return True


def approved_schedule_to_advanced(
    baseline: ApprovedPlanningBaseline,
    project: TurnaroundProject,
    active_ids: set[str],
) -> AdvancedScheduleResult:
    if not approved_schedule_matches_project(
        baseline,
        project,
        active_ids,
    ):
        raise ValueError(
            "O baseline aprovado não é compatível com o escopo-base atual."
        )

    task_by_id = {
        task.id: task
        for task in project.tasks
    }
    items: list[AdvancedScheduledTask] = []

    for scheduled in baseline.schedule:
        if scheduled.task_id not in active_ids:
            continue
        task = task_by_id[scheduled.task_id]
        base_mode = next(
            mode
            for mode in task.modes
            if mode.name == "base"
        )
        items.append(
            AdvancedScheduledTask(
                task_id=scheduled.task_id,
                task_name=scheduled.task_name,
                mode_name="base",
                start=float(scheduled.start_h),
                finish=float(scheduled.finish_h),
                duration=float(scheduled.duration_h),
                resources={
                    str(resource): float(quantity)
                    for resource, quantity in scheduled.resources.items()
                },
                cost=float(base_mode.cost),
            )
        )

    makespan = max(
        (item.finish for item in items),
        default=0.0,
    )
    deadline = project.deadline
    return AdvancedScheduleResult(
        tasks=items,
        makespan=makespan,
        total_cost=sum(item.cost for item in items),
        tardiness=(
            max(0.0, makespan - deadline)
            if deadline is not None
            else 0.0
        ),
        strategy=f"approved-rcpsp:{baseline.priority_rule}",
        mode_combinations=1,
        evaluated_combinations=1,
    )
