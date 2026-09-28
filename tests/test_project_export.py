from __future__ import annotations

from datetime import datetime
from xml.etree import ElementTree as ET

from turnaround import (
    ActivationRule,
    CalendarBlock,
    DailyShift,
    ExecutionMode,
    ExecutionState,
    OvertimeWindow,
    Precedence,
    TaskExecution,
    TriggerCondition,
    TurnaroundProject,
    TurnaroundTask,
    WorkingCalendar,
    build_baseline_revision,
)
from turnaround.io import project_xml_to_tasks
from turnaround.mrcpsp import AdvancedScheduledTask
from turnaround.project_export import build_project_xml


def test_project_xml_export_materializes_conditional_and_dynamic_scope():
    project = TurnaroundProject(
        tasks=[
            TurnaroundTask(
                id="1",
                project_uid="101",
                name="Inspecionar",
                wbs="1.1",
                modes=[
                    ExecutionMode(
                        name="base",
                        duration=2,
                        resources={"Inspeção": 1},
                    )
                ],
            ),
            TurnaroundTask(
                id="2",
                project_uid="102",
                name="Reparar após achado",
                wbs="1.2",
                modes=[
                    ExecutionMode(
                        name="base",
                        duration=4,
                        resources={"Mecânica": 2},
                    )
                ],
                precedences=[Precedence(predecessor_id="1")],
                activation=ActivationRule(
                    kind="conditional",
                    conditions=[
                        TriggerCondition(
                            source_task_id="1",
                            events=["damage"],
                        )
                    ],
                ),
            ),
            TurnaroundTask(
                id="DS-001",
                name="Reparar trinca descoberta",
                wbs="DS.1",
                modes=[
                    ExecutionMode(
                        name="campo",
                        duration=3,
                        resources={"Soldagem": 1},
                    )
                ],
                precedences=[Precedence(predecessor_id="2")],
            ),
            TurnaroundTask(
                id="3",
                project_uid="103",
                name="Condicional ainda dormente",
                wbs="1.3",
                modes=[ExecutionMode(name="base", duration=2)],
                activation=ActivationRule(
                    kind="conditional",
                    conditions=[
                        TriggerCondition(
                            source_task_id="1",
                            events=["other_damage"],
                        )
                    ],
                ),
            ),
        ],
        capacities={
            "Inspeção": 1,
            "Mecânica": 2,
            "Soldagem": 1,
        },
    )

    items = [
        AdvancedScheduledTask(
            task_id="1",
            task_name="Inspecionar",
            mode_name="base",
            start=0,
            finish=2,
            duration=2,
            resources={"Inspeção": 1},
            cost=0,
            fixed=True,
        ),
        AdvancedScheduledTask(
            task_id="2",
            task_name="Reparar após achado",
            mode_name="base",
            start=2,
            finish=6,
            duration=4,
            resources={"Mecânica": 2},
            cost=0,
        ),
        AdvancedScheduledTask(
            task_id="DS-001",
            task_name="Reparar trinca descoberta",
            mode_name="campo",
            start=6,
            finish=9,
            duration=3,
            resources={"Soldagem": 1},
            cost=0,
        ),
    ]

    state = ExecutionState(
        current_time=2,
        events={"1": ["damage"]},
        executions={
            "1": TaskExecution(
                status="completed",
                start=0,
                finish=2,
                mode_name="base",
            )
        },
    )

    xml_bytes = build_project_xml(
        project_name="Parada teste",
        project=project,
        active_task_ids={"1", "2", "DS-001"},
        schedule_items=items,
        effective_tasks=project.tasks,
        state=state,
        calendar_origin=datetime(2026, 10, 6, 6, 0),
        dynamic_scope_ids={"DS-001"},
        activation_reasons={
            "2": "evento damage observado em 1",
            "DS-001": "dynamic scope",
        },
        snapshot_id="abc123def456",
    )

    parsed_tasks, capacities = project_xml_to_tasks(xml_bytes)

    assert [task.name for task in parsed_tasks] == [
        "Inspecionar",
        "Reparar após achado",
        "Reparar trinca descoberta",
    ]
    assert "Condicional ainda dormente" not in {
        task.name for task in parsed_tasks
    }
    assert capacities["Mecânica"] == 2
    assert capacities["Soldagem"] == 1

    by_name = {task.name: task for task in parsed_tasks}
    assert by_name["Reparar após achado"].predecessors[0].predecessor_id == "1"
    assert (
        by_name["Reparar trinca descoberta"].predecessors[0].predecessor_id
        == "2"
    )
    assert by_name["Reparar trinca descoberta"].resources == {"Soldagem": 1}

    root = ET.fromstring(xml_bytes)
    ns = {"p": "http://schemas.microsoft.com/project"}
    notes = [
        node.text or ""
        for node in root.findall(".//p:Task/p:Notes", ns)
    ]
    assert any("Origem: conditional / selective" in note for note in notes)
    assert any("Origem: dynamic discovery" in note for note in notes)
    assert any("Snapshot: abc123def456" in note for note in notes)


def test_project_xml_export_preserves_replanned_dates():
    project = TurnaroundProject(
        tasks=[
            TurnaroundTask(
                id="A",
                name="Atividade A",
                modes=[ExecutionMode(name="base", duration=2)],
            )
        ],
        capacities={},
    )
    item = AdvancedScheduledTask(
        task_id="A",
        task_name="Atividade A",
        mode_name="base",
        start=5,
        finish=7,
        duration=2,
        resources={},
        cost=0,
    )

    xml_bytes = build_project_xml(
        project_name="Parada",
        project=project,
        active_task_ids={"A"},
        schedule_items=[item],
        state=ExecutionState(current_time=5),
        calendar_origin=datetime(2026, 10, 6, 6, 0),
    )

    root = ET.fromstring(xml_bytes)
    ns = {"p": "http://schemas.microsoft.com/project"}
    task = root.find(".//p:Tasks/p:Task", ns)

    assert task is not None
    assert task.findtext("p:Start", namespaces=ns) == "2026-10-06T11:00:00"
    assert task.findtext("p:Finish", namespaces=ns) == "2026-10-06T13:00:00"


def test_project_xml_export_writes_original_and_formal_baselines():
    project = TurnaroundProject(
        tasks=[
            TurnaroundTask(
                id="A",
                project_uid="1001",
                name="Atividade A",
                modes=[ExecutionMode(name="base", duration=5)],
            )
        ],
        capacities={},
    )

    original = AdvancedScheduledTask(
        task_id="A",
        task_name="Atividade A",
        mode_name="base",
        start=0,
        finish=5,
        duration=5,
        resources={},
        cost=0,
    )
    revision = build_baseline_revision(
        session_id="session-1",
        project_key="project-key",
        revision_number=1,
        name="Rev.1",
        reason="Nova janela aprovada",
        approved_by="Planejamento",
        snapshot_id="snap-rev1",
        current_time_h=2,
        makespan_h=6,
        deadline_h=8,
        total_cost=0,
        schedule_items=[
            AdvancedScheduledTask(
                task_id="A",
                task_name="Atividade A",
                mode_name="base",
                start=1,
                finish=6,
                duration=5,
                resources={},
                cost=0,
            )
        ],
    )
    current = AdvancedScheduledTask(
        task_id="A",
        task_name="Atividade A",
        mode_name="base",
        start=2,
        finish=7,
        duration=5,
        resources={},
        cost=0,
    )

    xml_bytes = build_project_xml(
        project_name="Parada",
        project=project,
        active_task_ids={"A"},
        schedule_items=[current],
        state=ExecutionState(current_time=2),
        calendar_origin=datetime(2026, 10, 6, 6, 0),
        original_baseline_items=[original],
        baseline_revisions=[revision],
    )

    root = ET.fromstring(xml_bytes)
    ns = {"p": "http://schemas.microsoft.com/project"}
    task = root.find(".//p:Tasks/p:Task", ns)
    assert task is not None

    baselines = task.findall("p:Baseline", ns)
    assert len(baselines) == 2

    values = {
        int(item.findtext("p:Number", namespaces=ns)): (
            item.findtext("p:Start", namespaces=ns),
            item.findtext("p:Finish", namespaces=ns),
        )
        for item in baselines
    }
    assert values[0] == (
        "2026-10-06T06:00:00",
        "2026-10-06T11:00:00",
    )
    assert values[1] == (
        "2026-10-06T07:00:00",
        "2026-10-06T12:00:00",
    )
    assert task.findtext("p:Start", namespaces=ns) == "2026-10-06T08:00:00"
    assert task.findtext("p:Finish", namespaces=ns) == "2026-10-06T13:00:00"


def test_project_xml_export_assigns_recurring_calendar_to_resource():
    project = TurnaroundProject(
        tasks=[
            TurnaroundTask(
                id="A",
                name="Serviço mecânico",
                modes=[
                    ExecutionMode(
                        name="base",
                        duration=4,
                        resources={"Equipe": 1},
                    )
                ],
            )
        ],
        capacities={"Equipe": 1},
        resource_calendars={
            "Equipe": WorkingCalendar(
                name="Equipe",
                shifts=(DailyShift(7, 19),),
                origin_hour=6,
            )
        },
    )
    item = AdvancedScheduledTask(
        task_id="A",
        task_name="Serviço mecânico",
        mode_name="base",
        start=1,
        finish=5,
        duration=4,
        resources={"Equipe": 1},
        cost=0,
    )

    xml_bytes = build_project_xml(
        project_name="Parada calendário",
        project=project,
        active_task_ids={"A"},
        schedule_items=[item],
        state=ExecutionState(current_time=0),
        calendar_origin=datetime(2026, 10, 6, 6, 0),
    )

    root = ET.fromstring(xml_bytes)
    ns = {"p": "http://schemas.microsoft.com/project"}

    calendars = root.findall(".//p:Calendars/p:Calendar", ns)
    assert len(calendars) == 2

    custom = next(
        node
        for node in calendars
        if node.findtext("p:Name", namespaces=ns) == "TDS · Equipe"
    )
    custom_uid = int(custom.findtext("p:UID", namespaces=ns))
    weekdays = custom.findall("p:WeekDays/p:WeekDay", ns)
    assert len(weekdays) == 7
    for weekday in weekdays:
        assert weekday.findtext("p:DayWorking", namespaces=ns) == "1"
        times = weekday.findall(
            "p:WorkingTimes/p:WorkingTime",
            ns,
        )
        assert len(times) == 1
        assert times[0].findtext("p:FromTime", namespaces=ns) == "07:00:00"
        assert times[0].findtext("p:ToTime", namespaces=ns) == "19:00:00"

    resource = root.find(".//p:Resources/p:Resource", ns)
    assert resource is not None
    assert int(resource.findtext("p:CalendarUID", namespaces=ns)) == custom_uid

    notes = root.findtext(".//p:Tasks/p:Task/p:Notes", namespaces=ns) or ""
    assert "Calendários de recurso: Equipe" in notes
    assert "atividades não preemptivas" in notes


def test_project_xml_export_splits_overnight_recurring_shift():
    project = TurnaroundProject(
        tasks=[
            TurnaroundTask(
                id="N",
                name="Serviço noturno",
                modes=[
                    ExecutionMode(
                        name="base",
                        duration=4,
                        resources={"Noturno": 1},
                    )
                ],
            )
        ],
        capacities={"Noturno": 1},
        resource_calendars={
            "Noturno": WorkingCalendar(
                name="Noturno",
                shifts=(DailyShift(19, 7),),
                origin_hour=6,
            )
        },
    )
    item = AdvancedScheduledTask(
        task_id="N",
        task_name="Serviço noturno",
        mode_name="base",
        start=13,
        finish=17,
        duration=4,
        resources={"Noturno": 1},
        cost=0,
    )

    xml_bytes = build_project_xml(
        project_name="Parada noite",
        project=project,
        active_task_ids={"N"},
        schedule_items=[item],
        state=ExecutionState(current_time=0),
        calendar_origin=datetime(2026, 10, 6, 6, 0),
    )

    root = ET.fromstring(xml_bytes)
    ns = {"p": "http://schemas.microsoft.com/project"}
    custom = next(
        node
        for node in root.findall(".//p:Calendars/p:Calendar", ns)
        if node.findtext("p:Name", namespaces=ns) == "TDS · Noturno"
    )
    first_day = custom.find("p:WeekDays/p:WeekDay", ns)
    assert first_day is not None
    times = first_day.findall("p:WorkingTimes/p:WorkingTime", ns)

    assert [
        (
            item.findtext("p:FromTime", namespaces=ns),
            item.findtext("p:ToTime", namespaces=ns),
        )
        for item in times
    ] == [
        ("00:00:00", "07:00:00"),
        ("19:00:00", "23:59:59"),
    ]


def test_project_xml_export_materializes_blocks_and_overtime_as_exceptions():
    project = TurnaroundProject(
        tasks=[
            TurnaroundTask(
                id="A",
                name="Serviço",
                modes=[
                    ExecutionMode(
                        name="base",
                        duration=4,
                        resources={"Equipe": 1},
                    )
                ],
            )
        ],
        capacities={"Equipe": 1},
        resource_calendars={
            "Equipe": WorkingCalendar(
                name="Equipe",
                shifts=(DailyShift(7, 19),),
                origin_hour=6,
                blocks=(
                    CalendarBlock(
                        start_h=6,
                        end_h=8,
                        reason="indisponibilidade",
                    ),
                ),
                overtime_windows=(
                    OvertimeWindow(
                        start_h=13,
                        end_h=17,
                        reason="hora extra",
                    ),
                ),
            )
        },
    )
    item = AdvancedScheduledTask(
        task_id="A",
        task_name="Serviço",
        mode_name="base",
        start=8,
        finish=12,
        duration=4,
        resources={"Equipe": 1},
        cost=0,
    )

    xml_bytes = build_project_xml(
        project_name="Parada exceção",
        project=project,
        active_task_ids={"A"},
        schedule_items=[item],
        state=ExecutionState(current_time=0),
        calendar_origin=datetime(2026, 10, 6, 6, 0),
    )

    root = ET.fromstring(xml_bytes)
    ns = {"p": "http://schemas.microsoft.com/project"}
    custom = next(
        node
        for node in root.findall(".//p:Calendars/p:Calendar", ns)
        if node.findtext("p:Name", namespaces=ns) == "TDS · Equipe"
    )
    exceptions = custom.findall("p:Exceptions/p:Exception", ns)
    assert len(exceptions) == 1
    exception = exceptions[0]
    assert exception.findtext("p:DayWorking", namespaces=ns) == "1"

    times = exception.findall("p:WorkingTimes/p:WorkingTime", ns)
    assert [
        (
            wt.findtext("p:FromTime", namespaces=ns),
            wt.findtext("p:ToTime", namespaces=ns),
        )
        for wt in times
    ] == [
        ("07:00:00", "12:00:00"),
        ("14:00:00", "23:00:00"),
    ]
    assert (
        exception.findtext(
            "p:TimePeriod/p:FromDate",
            namespaces=ns,
        )
        == "2026-10-06T00:00:00"
    )


def test_project_xml_export_keeps_24x7_for_resources_without_custom_calendar():
    project = TurnaroundProject(
        tasks=[
            TurnaroundTask(
                id="A",
                name="Serviço",
                modes=[
                    ExecutionMode(
                        name="base",
                        duration=2,
                        resources={"Equipe": 1},
                    )
                ],
            )
        ],
        capacities={"Equipe": 1},
    )
    item = AdvancedScheduledTask(
        task_id="A",
        task_name="Serviço",
        mode_name="base",
        start=0,
        finish=2,
        duration=2,
        resources={"Equipe": 1},
        cost=0,
    )

    xml_bytes = build_project_xml(
        project_name="Parada 24h",
        project=project,
        active_task_ids={"A"},
        schedule_items=[item],
        state=ExecutionState(current_time=0),
        calendar_origin=datetime(2026, 10, 6, 6, 0),
    )

    root = ET.fromstring(xml_bytes)
    ns = {"p": "http://schemas.microsoft.com/project"}
    resource = root.find(".//p:Resources/p:Resource", ns)
    assert resource is not None
    assert resource.findtext("p:CalendarUID", namespaces=ns) == "1"
    calendars = root.findall(".//p:Calendars/p:Calendar", ns)
    assert len(calendars) == 1
