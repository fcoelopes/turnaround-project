from __future__ import annotations

from datetime import datetime, timedelta, timezone
from xml.etree import ElementTree as ET

from .advanced_models import ExecutionState, TurnaroundProject, TurnaroundTask
from .baseline_revision import BaselineRevision
from .calendar import WorkingCalendar
from .mrcpsp import AdvancedScheduledTask


MSP_NS = "http://schemas.microsoft.com/project"
ET.register_namespace("", MSP_NS)

RELATION_TO_PROJECT_TYPE = {
    "FF": 0,
    "FS": 1,
    "SF": 2,
    "SS": 3,
}


def _tag(name: str) -> str:
    return f"{{{MSP_NS}}}{name}"


def _text(parent: ET.Element, name: str, value: object) -> ET.Element:
    node = ET.SubElement(parent, _tag(name))
    node.text = str(value)
    return node


def _iso_duration(hours: float) -> str:
    total_seconds = max(0, int(round(float(hours) * 3600)))
    h, remainder = divmod(total_seconds, 3600)
    m, s = divmod(remainder, 60)
    return f"PT{h}H{m}M{s}S"


def _format_dt(value: datetime) -> str:
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value.replace(microsecond=0).isoformat()


def _numeric_uid(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _uid_map(tasks: list[TurnaroundTask]) -> dict[str, int]:
    used: set[int] = set()
    mapping: dict[str, int] = {}

    for task in tasks:
        candidate = _numeric_uid(task.project_uid)
        if candidate is None or candidate in used:
            continue
        mapping[task.id] = candidate
        used.add(candidate)

    next_uid = max(used, default=0) + 1
    for task in tasks:
        if task.id in mapping:
            continue
        while next_uid in used:
            next_uid += 1
        mapping[task.id] = next_uid
        used.add(next_uid)
        next_uid += 1

    return mapping


def _append_baseline(
    task_node: ET.Element,
    *,
    number: int,
    start_h: float,
    finish_h: float,
    duration_h: float,
    calendar_origin: datetime,
    cost: float = 0.0,
) -> None:
    baseline = ET.SubElement(task_node, _tag("Baseline"))
    _text(baseline, "Number", int(number))
    _text(
        baseline,
        "Start",
        _format_dt(calendar_origin + timedelta(hours=float(start_h))),
    )
    _text(
        baseline,
        "Finish",
        _format_dt(calendar_origin + timedelta(hours=float(finish_h))),
    )
    _text(baseline, "Duration", _iso_duration(duration_h))
    _text(baseline, "DurationFormat", 7)
    _text(baseline, "Cost", float(cost))


def _clock_text(hour: float, *, end: bool = False) -> str:
    value = float(hour)
    if value < 0 or value > 24:
        raise ValueError(f"Hora de calendário inválida: {value:g}")
    if abs(value - 24.0) <= 1e-9:
        # MSPDI usa horário de relógio; evitamos 24:00:00 por compatibilidade.
        return "23:59:59"

    total_seconds = int(round(value * 3600))
    total_seconds = min(total_seconds, 24 * 3600 - 1)
    hh, remainder = divmod(total_seconds, 3600)
    mm, ss = divmod(remainder, 60)
    return f"{hh:02d}:{mm:02d}:{ss:02d}"


def _merge_clock_intervals(
    intervals: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    cleaned = sorted(
        (max(0.0, float(start)), min(24.0, float(end)))
        for start, end in intervals
        if float(end) > float(start) + 1e-9
    )
    merged: list[list[float]] = []
    for start, end in cleaned:
        if not merged or start > merged[-1][1] + 1e-9:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return [(start, end) for start, end in merged]


def _recurring_shift_intervals(
    calendar: WorkingCalendar,
) -> list[tuple[float, float]]:
    intervals: list[tuple[float, float]] = []
    for shift in calendar.shifts:
        start = float(shift.start_hour)
        end = float(shift.end_hour)
        if shift.crosses_midnight:
            intervals.append((0.0, end))
            intervals.append((start, 24.0))
        else:
            intervals.append((start, end))
    merged = _merge_clock_intervals(intervals)
    if len(merged) > 5:
        raise ValueError(
            f"Calendário {calendar.name!r} possui {len(merged)} janelas diárias; "
            "MSPDI aceita no máximo cinco WorkingTime por dia."
        )
    return merged


def _append_working_times(
    parent: ET.Element,
    intervals: list[tuple[float, float]],
) -> None:
    if not intervals:
        return
    if len(intervals) > 5:
        raise ValueError(
            "MSPDI aceita no máximo cinco WorkingTime por dia."
        )
    working_times = ET.SubElement(parent, _tag("WorkingTimes"))
    for start, end in intervals:
        working_time = ET.SubElement(working_times, _tag("WorkingTime"))
        _text(working_time, "FromTime", _clock_text(start))
        _text(working_time, "ToTime", _clock_text(end, end=True))


def _append_weekdays(
    calendar_node: ET.Element,
    calendar: WorkingCalendar | None,
) -> None:
    weekdays = ET.SubElement(calendar_node, _tag("WeekDays"))
    intervals = (
        [(0.0, 24.0)]
        if calendar is None
        else _recurring_shift_intervals(calendar)
    )
    for day_type in range(1, 8):
        weekday = ET.SubElement(weekdays, _tag("WeekDay"))
        _text(weekday, "DayType", day_type)
        _text(weekday, "DayWorking", 1 if intervals else 0)
        _append_working_times(weekday, intervals)


def _affected_exception_dates(
    calendar: WorkingCalendar,
    calendar_origin: datetime,
) -> list[datetime]:
    dates: set = set()
    for item in [*calendar.blocks, *calendar.overtime_windows]:
        start_dt = calendar_origin + timedelta(hours=float(item.start_h))
        end_dt = calendar_origin + timedelta(hours=float(item.end_h))
        current = start_dt.date()
        last = end_dt.date()
        while current <= last:
            dates.add(current)
            current += timedelta(days=1)
    return [
        datetime.combine(
            day,
            datetime.min.time(),
            tzinfo=calendar_origin.tzinfo,
        )
        for day in sorted(dates)
    ]


def _effective_day_intervals(
    calendar: WorkingCalendar,
    *,
    calendar_origin: datetime,
    day_start: datetime,
) -> list[tuple[float, float]]:
    day_end = day_start + timedelta(days=1)
    start_h = (day_start - calendar_origin).total_seconds() / 3600
    end_h = (day_end - calendar_origin).total_seconds() / 3600
    intervals = calendar.working_intervals(start_h, end_h)

    clock_intervals: list[tuple[float, float]] = []
    for start, end in intervals:
        start_dt = calendar_origin + timedelta(hours=float(start))
        end_dt = calendar_origin + timedelta(hours=float(end))
        start_clock = (
            start_dt.hour
            + start_dt.minute / 60
            + start_dt.second / 3600
        )
        if end_dt >= day_end - timedelta(microseconds=1):
            end_clock = 24.0
        else:
            end_clock = (
                end_dt.hour
                + end_dt.minute / 60
                + end_dt.second / 3600
            )
        clock_intervals.append((start_clock, end_clock))

    merged = _merge_clock_intervals(clock_intervals)
    if len(merged) > 5:
        raise ValueError(
            f"Calendário {calendar.name!r} gera {len(merged)} janelas no dia "
            f"{day_start:%Y-%m-%d}; MSPDI aceita no máximo cinco."
        )
    return merged


def _append_exceptions(
    calendar_node: ET.Element,
    calendar: WorkingCalendar,
    *,
    calendar_origin: datetime,
) -> None:
    exception_days = _affected_exception_dates(calendar, calendar_origin)
    if not exception_days:
        return

    exceptions = ET.SubElement(calendar_node, _tag("Exceptions"))
    for day_start in exception_days:
        intervals = _effective_day_intervals(
            calendar,
            calendar_origin=calendar_origin,
            day_start=day_start,
        )
        exception = ET.SubElement(exceptions, _tag("Exception"))
        _text(exception, "EnteredByOccurrences", 0)
        period = ET.SubElement(exception, _tag("TimePeriod"))
        _text(period, "FromDate", _format_dt(day_start))
        _text(
            period,
            "ToDate",
            _format_dt(day_start + timedelta(hours=23, minutes=59)),
        )
        _text(exception, "Occurrences", 1)
        _text(exception, "Name", f"TDS · {day_start:%Y-%m-%d}")
        _text(exception, "Type", 1)
        _text(exception, "DayWorking", 1 if intervals else 0)
        _append_working_times(exception, intervals)


def _append_calendars(
    root: ET.Element,
    *,
    resource_calendars: dict[str, WorkingCalendar],
    calendar_origin: datetime,
) -> dict[str, int]:
    calendars = ET.SubElement(root, _tag("Calendars"))

    base = ET.SubElement(calendars, _tag("Calendar"))
    _text(base, "UID", 1)
    _text(base, "Name", "Turnaround 24x7")
    _text(base, "IsBaseCalendar", 1)
    _text(base, "BaseCalendarUID", -1)
    _append_weekdays(base, None)

    uid_by_resource: dict[str, int] = {}
    for uid, resource in enumerate(sorted(resource_calendars), start=2):
        calendar = resource_calendars[resource]
        node = ET.SubElement(calendars, _tag("Calendar"))
        _text(node, "UID", uid)
        _text(node, "Name", f"TDS · {resource}")
        _text(node, "IsBaseCalendar", 1)
        _text(node, "BaseCalendarUID", -1)
        _append_weekdays(node, calendar)
        _append_exceptions(
            node,
            calendar,
            calendar_origin=calendar_origin,
        )
        uid_by_resource[resource] = uid

    return uid_by_resource


def build_project_xml(
    *,
    project_name: str,
    project: TurnaroundProject,
    active_task_ids: set[str],
    schedule_items: list[AdvancedScheduledTask],
    effective_tasks: list[TurnaroundTask] | None = None,
    state: ExecutionState,
    calendar_origin: datetime,
    dynamic_scope_ids: set[str] | None = None,
    activation_reasons: dict[str, str] | None = None,
    snapshot_id: str | None = None,
    original_baseline_items: list[AdvancedScheduledTask] | None = None,
    baseline_revisions: list[BaselineRevision] | None = None,
) -> bytes:
    """Build a Microsoft Project XML (MSPDI) for the current materialized plan.

    Only tasks active in the current execution snapshot are exported. Conditional
    or selective tasks that remain dormant/pending are intentionally excluded:
    Microsoft Project has no native representation of the decision rules used by
    the turnaround engine. Activated conditional work and dynamic DS-* work are
    exported as normal operational tasks with audit metadata in Notes.
    """

    dynamic_scope_ids = set(dynamic_scope_ids or set())
    activation_reasons = dict(activation_reasons or {})
    baseline_revisions = sorted(
        list(baseline_revisions or []),
        key=lambda item: item.revision_number,
    )
    original_baseline_by_id = {
        str(item.task_id): item
        for item in (original_baseline_items or [])
    }
    revision_schedule_by_number = {
        revision.revision_number: {
            item.task_id: item
            for item in revision.schedule
        }
        for revision in baseline_revisions
    }
    schedule_by_id = {
        str(item.task_id): item
        for item in schedule_items
        if str(item.task_id) in active_task_ids
    }

    task_by_id = {
        task.id: task
        for task in project.tasks
    }
    effective_by_id = {
        task.id: task
        for task in (effective_tasks or [])
    }

    ordered_tasks = [
        task
        for task in project.tasks
        if task.id in active_task_ids and task.id in schedule_by_id
    ]
    if not ordered_tasks:
        raise ValueError("Não há atividades ativas programadas para exportar.")

    uid_by_id = _uid_map(ordered_tasks)
    export_id_by_id = {
        task.id: index
        for index, task in enumerate(ordered_tasks, start=1)
    }

    root = ET.Element(_tag("Project"))
    _text(root, "SaveVersion", 14)
    _text(root, "Name", f"{project_name}_replanejado.xml")
    _text(root, "Title", f"{project_name} · cronograma replanejado")
    _text(root, "Subject", "Snapshot operacional exportado pelo Turnaround Decision Support")
    _text(root, "Author", "Turnaround Decision Support")
    _text(root, "CreationDate", _format_dt(datetime.now(timezone.utc)))
    _text(root, "LastSaved", _format_dt(datetime.now(timezone.utc)))
    _text(root, "ScheduleFromStart", 1)
    _text(root, "StartDate", _format_dt(calendar_origin))
    _text(
        root,
        "FinishDate",
        _format_dt(
            calendar_origin
            + timedelta(
                hours=max(schedule_by_id[task.id].finish for task in ordered_tasks)
            )
        ),
    )
    _text(
        root,
        "CurrentDate",
        _format_dt(calendar_origin + timedelta(hours=float(state.current_time))),
    )
    _text(root, "MinutesPerDay", 1440)
    _text(root, "MinutesPerWeek", 10080)
    _text(root, "DaysPerMonth", 30)
    _text(root, "DefaultStartTime", "00:00:00")
    _text(root, "DefaultFinishTime", "23:59:00")
    _text(root, "CalendarUID", 1)

    resource_calendar_uid = _append_calendars(
        root,
        resource_calendars=project.resource_calendars,
        calendar_origin=calendar_origin,
    )

    tasks_node = ET.SubElement(root, _tag("Tasks"))

    for task in ordered_tasks:
        item = schedule_by_id[task.id]
        effective_task = effective_by_id.get(task.id, task)
        task_node = ET.SubElement(tasks_node, _tag("Task"))

        _text(task_node, "UID", uid_by_id[task.id])
        _text(task_node, "ID", export_id_by_id[task.id])
        _text(task_node, "Name", task.name)
        _text(task_node, "Type", 1)
        _text(task_node, "IsNull", 0)
        _text(task_node, "CreateDate", _format_dt(datetime.now(timezone.utc)))
        _text(task_node, "WBS", task.wbs or task.id)
        _text(task_node, "OutlineNumber", task.wbs or str(export_id_by_id[task.id]))
        _text(task_node, "OutlineLevel", max(1, (task.wbs or "").count(".") + 1))
        _text(
            task_node,
            "Start",
            _format_dt(calendar_origin + timedelta(hours=float(item.start))),
        )
        _text(
            task_node,
            "Finish",
            _format_dt(calendar_origin + timedelta(hours=float(item.finish))),
        )
        _text(task_node, "Duration", _iso_duration(item.duration))
        _text(task_node, "DurationFormat", 7)
        _text(task_node, "Work", _iso_duration(item.duration))
        _text(task_node, "Summary", 0)
        _text(task_node, "Milestone", 0)
        _text(task_node, "Active", 1)

        execution = state.executions.get(task.id)
        if execution is not None and execution.status == "completed":
            _text(task_node, "PercentComplete", 100)
            if execution.start is not None:
                _text(
                    task_node,
                    "ActualStart",
                    _format_dt(
                        calendar_origin
                        + timedelta(hours=float(execution.start))
                    ),
                )
            if execution.finish is not None:
                _text(
                    task_node,
                    "ActualFinish",
                    _format_dt(
                        calendar_origin
                        + timedelta(hours=float(execution.finish))
                    ),
                )
        elif execution is not None and execution.status == "in_progress":
            elapsed = max(0.0, float(state.current_time) - float(item.start))
            pct = min(
                99,
                max(
                    1,
                    int(round(100 * elapsed / max(float(item.duration), 1e-9))),
                ),
            )
            _text(task_node, "PercentComplete", pct)
            _text(
                task_node,
                "ActualStart",
                _format_dt(calendar_origin + timedelta(hours=float(item.start))),
            )
        else:
            _text(task_node, "PercentComplete", 0)

        origin = (
            "dynamic discovery"
            if task.id in dynamic_scope_ids
            else (
                "conditional / selective"
                if task.activation.kind != "mandatory"
                else "baseline"
            )
        )
        notes = [
            "Turnaround Decision Support",
            f"ID original: {task.id}",
            f"Origem: {origin}",
            f"Modo: {item.mode_name}",
            f"Estado: {state.executions.get(task.id).status if task.id in state.executions else 'future'}",
        ]
        if task.id in activation_reasons:
            notes.append(f"Ativação: {activation_reasons[task.id]}")
        if snapshot_id:
            notes.append(f"Snapshot: {snapshot_id}")
        if baseline_revisions:
            notes.append(
                f"Baseline vigente: Rev.{baseline_revisions[-1].revision_number}"
            )
        else:
            notes.append("Baseline vigente: Original")
        task_calendar_resources = [
            resource
            for resource, demand in item.resources.items()
            if float(demand) > 0 and resource in project.resource_calendars
        ]
        if task_calendar_resources:
            notes.append(
                "Calendários de recurso: "
                + ", ".join(sorted(task_calendar_resources))
            )
        notes.append(
            "Hipótese de scheduling: atividades não preemptivas no TDS."
        )
        _text(task_node, "Notes", "\n".join(notes))

        original_item = original_baseline_by_id.get(task.id)
        if original_item is not None:
            _append_baseline(
                task_node,
                number=0,
                start_h=float(original_item.start),
                finish_h=float(original_item.finish),
                duration_h=float(original_item.duration),
                calendar_origin=calendar_origin,
                cost=float(original_item.cost),
            )

        for revision in baseline_revisions:
            revision_item = revision_schedule_by_number[
                revision.revision_number
            ].get(task.id)
            if revision_item is None:
                continue
            _append_baseline(
                task_node,
                number=revision.revision_number,
                start_h=float(revision_item.start_h),
                finish_h=float(revision_item.finish_h),
                duration_h=float(revision_item.duration_h),
                calendar_origin=calendar_origin,
                cost=float(revision_item.cost),
            )

        for precedence in effective_task.precedences:
            predecessor_id = precedence.predecessor_id
            if predecessor_id not in uid_by_id:
                continue
            link = ET.SubElement(task_node, _tag("PredecessorLink"))
            _text(link, "PredecessorUID", uid_by_id[predecessor_id])
            _text(
                link,
                "Type",
                RELATION_TO_PROJECT_TYPE.get(precedence.relation, 1),
            )
            _text(link, "CrossProject", 0)
            _text(link, "LinkLag", int(round(float(precedence.lag) * 600)))
            _text(link, "LagFormat", 7)

    resource_names = sorted(
        {
            resource
            for item in schedule_by_id.values()
            for resource, demand in item.resources.items()
            if float(demand) > 0
        },
        key=str.casefold,
    )
    resource_uid = {
        name: index
        for index, name in enumerate(resource_names, start=1)
    }

    resources_node = ET.SubElement(root, _tag("Resources"))
    for name in resource_names:
        resource = ET.SubElement(resources_node, _tag("Resource"))
        _text(resource, "UID", resource_uid[name])
        _text(resource, "ID", resource_uid[name])
        _text(resource, "Name", name)
        _text(resource, "Type", 1)
        _text(resource, "IsNull", 0)
        _text(
            resource,
            "MaxUnits",
            float(project.capacities.get(name, 1.0)),
        )
        _text(
            resource,
            "CalendarUID",
            resource_calendar_uid.get(name, 1),
        )

    assignments_node = ET.SubElement(root, _tag("Assignments"))
    assignment_uid = 1
    for task in ordered_tasks:
        item = schedule_by_id[task.id]
        for resource_name, demand in sorted(item.resources.items()):
            if float(demand) <= 0 or resource_name not in resource_uid:
                continue
            assignment = ET.SubElement(assignments_node, _tag("Assignment"))
            _text(assignment, "UID", assignment_uid)
            _text(assignment, "TaskUID", uid_by_id[task.id])
            _text(assignment, "ResourceUID", resource_uid[resource_name])
            _text(assignment, "Units", float(demand))
            _text(
                assignment,
                "Start",
                _format_dt(
                    calendar_origin + timedelta(hours=float(item.start))
                ),
            )
            _text(
                assignment,
                "Finish",
                _format_dt(
                    calendar_origin + timedelta(hours=float(item.finish))
                ),
            )
            _text(
                assignment,
                "Work",
                _iso_duration(float(item.duration) * float(demand)),
            )
            assignment_uid += 1

    return ET.tostring(
        root,
        encoding="utf-8",
        xml_declaration=True,
    )
