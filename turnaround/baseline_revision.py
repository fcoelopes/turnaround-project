from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field

from .mrcpsp import AdvancedScheduledTask


class BaselineRevisionTaskSnapshot(BaseModel):
    task_id: str
    task_name: str
    start_h: float = Field(ge=0)
    finish_h: float = Field(ge=0)
    duration_h: float = Field(ge=0)
    mode_name: str
    resources: dict[str, float] = Field(default_factory=dict)
    cost: float = Field(default=0.0, ge=0)
    wbs: str | None = None
    skill_assignments: dict[str, list[str]] = Field(default_factory=dict)

    @classmethod
    def from_scheduled_task(
        cls,
        item: AdvancedScheduledTask,
        *,
        wbs: str | None = None,
    ) -> "BaselineRevisionTaskSnapshot":
        return cls(
            task_id=str(item.task_id),
            task_name=item.task_name,
            start_h=float(item.start),
            finish_h=float(item.finish),
            duration_h=float(item.duration),
            mode_name=item.mode_name,
            resources={
                str(resource): float(quantity)
                for resource, quantity in item.resources.items()
            },
            cost=float(item.cost),
            wbs=wbs,
            skill_assignments={
                str(skill): list(person_ids)
                for skill, person_ids in item.skill_assignments.items()
            },
        )

    def to_scheduled_task(self) -> AdvancedScheduledTask:
        return AdvancedScheduledTask(
            task_id=self.task_id,
            task_name=self.task_name,
            mode_name=self.mode_name,
            start=float(self.start_h),
            finish=float(self.finish_h),
            duration=float(self.duration_h),
            resources={
                str(resource): float(quantity)
                for resource, quantity in self.resources.items()
            },
            cost=float(self.cost),
            fixed=False,
            skill_assignments={
                str(skill): tuple(person_ids)
                for skill, person_ids in self.skill_assignments.items()
            },
        )


class BaselineRevision(BaseModel):
    session_id: str
    project_key: str
    revision_number: int = Field(ge=1, le=10)
    name: str
    reason: str
    approved_by: str | None = None
    notes: str | None = None
    snapshot_id: str
    current_time_h: float = Field(ge=0)
    makespan_h: float = Field(gt=0)
    deadline_h: float | None = Field(default=None, gt=0)
    total_cost: float = Field(default=0.0, ge=0)
    schedule: list[BaselineRevisionTaskSnapshot]
    approved_at: datetime

    @property
    def label(self) -> str:
        return f"Rev.{self.revision_number}"

    def reference_start_times(self) -> dict[str, float]:
        return {
            item.task_id: float(item.start_h)
            for item in self.schedule
        }

    def schedule_items(self) -> list[AdvancedScheduledTask]:
        return [
            item.to_scheduled_task()
            for item in self.schedule
        ]


def build_baseline_revision(
    *,
    session_id: str,
    project_key: str,
    revision_number: int,
    name: str,
    reason: str,
    snapshot_id: str,
    current_time_h: float,
    makespan_h: float,
    deadline_h: float | None,
    total_cost: float,
    schedule_items: list[AdvancedScheduledTask],
    wbs_by_id: dict[str, str | None] | None = None,
    approved_by: str | None = None,
    notes: str | None = None,
) -> BaselineRevision:
    name = name.strip()
    reason = reason.strip()
    if not name:
        raise ValueError("A revisão exige um nome.")
    if not reason:
        raise ValueError("A revisão exige um motivo de aprovação.")

    wbs_by_id = wbs_by_id or {}
    return BaselineRevision(
        session_id=session_id,
        project_key=project_key,
        revision_number=int(revision_number),
        name=name,
        reason=reason,
        approved_by=approved_by.strip() if approved_by and approved_by.strip() else None,
        notes=notes.strip() if notes and notes.strip() else None,
        snapshot_id=snapshot_id,
        current_time_h=float(current_time_h),
        makespan_h=float(makespan_h),
        deadline_h=None if deadline_h is None else float(deadline_h),
        total_cost=float(total_cost),
        schedule=[
            BaselineRevisionTaskSnapshot.from_scheduled_task(
                item,
                wbs=wbs_by_id.get(str(item.task_id)),
            )
            for item in schedule_items
        ],
        approved_at=datetime.now(timezone.utc),
    )
