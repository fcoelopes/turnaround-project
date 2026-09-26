from __future__ import annotations

from datetime import datetime, timedelta, timezone
from xml.etree import ElementTree as ET

from .advanced_models import ExecutionState, TurnaroundProject, TurnaroundTask
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


def _calendar(root: ET.Element) -> None:
    calendars = ET.SubElement(root, _tag("Calendars"))
    calendar = ET.SubElement(calendars, _tag("Calendar"))
    _text(calendar, "UID", 1)
    _text(calendar, "Name", "Turnaround 24x7")
    _text(calendar, "IsBaseCalendar", 1)
    weekdays = ET.SubElement(calendar, _tag("WeekDays"))

    for day_type in range(1, 8):
        weekday = ET.SubElement(weekdays, _tag("WeekDay"))
        _text(weekday, "DayType", day_type)
        _text(weekday, "DayWorking", 1)
        working_times = ET.SubElement(weekday, _tag("WorkingTimes"))
        working_time = ET.SubElement(working_times, _tag("WorkingTime"))
        _text(working_time, "FromTime", "00:00:00")
        _text(working_time, "ToTime", "23:59:00")


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

    _calendar(root)

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
        _text(task_node, "Notes", "\n".join(notes))

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
