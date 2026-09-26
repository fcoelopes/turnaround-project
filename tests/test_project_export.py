from __future__ import annotations

from datetime import datetime
from xml.etree import ElementTree as ET

from turnaround import (
    ActivationRule,
    ExecutionMode,
    ExecutionState,
    Precedence,
    TaskExecution,
    TriggerCondition,
    TurnaroundProject,
    TurnaroundTask,
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
