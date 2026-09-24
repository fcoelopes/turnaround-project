import pytest

from turnaround import (
    ActivationRule,
    ExecutionMode,
    Precedence,
    TurnaroundProject,
    TurnaroundTask,
    analyze_effective_criticality,
)
from turnaround.mrcpsp import AdvancedScheduledTask


def task(tid, name, duration, predecessors=None, resources=None):
    return TurnaroundTask(
        id=tid,
        name=name,
        modes=[
            ExecutionMode(
                name="base",
                duration=duration,
                resources=resources or {},
            )
        ],
        precedences=[
            Precedence(predecessor_id=predecessor_id)
            for predecessor_id in (predecessors or [])
        ],
        activation=ActivationRule(kind="mandatory"),
    )


def scheduled(
    tid,
    name,
    start,
    finish,
    resources=None,
    *,
    fixed=False,
):
    return AdvancedScheduledTask(
        task_id=tid,
        task_name=name,
        mode_name="base",
        start=start,
        finish=finish,
        duration=finish - start,
        resources=resources or {},
        cost=0,
        fixed=fixed,
    )


def test_effective_criticality_follows_binding_precedence():
    a = task("A", "Abrir", 2)
    b = task("B", "Fechar", 3, predecessors=["A"])
    project = TurnaroundProject(tasks=[a, b], capacities={})
    items = [
        scheduled("A", "Abrir", 0, 2),
        scheduled("B", "Fechar", 2, 5),
    ]

    result = analyze_effective_criticality(
        project=project,
        effective_tasks=project.tasks,
        items=items,
        capacities=project.capacities,
        current_time=0,
        makespan=5,
    )

    assert result.critical_ids == {"A", "B"}
    assert result.path_ids == ["A", "B"]
    assert "precedência FS" in result.reasons["A"]
    assert "término do cronograma" in result.reasons["B"]


def test_effective_criticality_detects_resource_release_driver():
    a = task("A", "Frente A", 2, resources={"Mec": 1})
    b = task("B", "Frente B", 3, resources={"Mec": 1})
    project = TurnaroundProject(
        tasks=[a, b],
        capacities={"Mec": 1},
    )
    items = [
        scheduled("A", "Frente A", 0, 2, {"Mec": 1}),
        scheduled("B", "Frente B", 2, 5, {"Mec": 1}),
    ]

    result = analyze_effective_criticality(
        project=project,
        effective_tasks=project.tasks,
        items=items,
        capacities=project.capacities,
        current_time=0,
        makespan=5,
    )

    assert result.path_ids == ["A", "B"]
    assert "recurso Mec" in result.reasons["A"]
    assert any(
        driver.kind == "resource"
        and driver.predecessor_id == "A"
        and driver.successor_id == "B"
        for driver in result.drivers
    )


def test_effective_criticality_marks_late_scope_gate():
    inspection = task("I", "Inspecionar", 2)
    repair = task("R", "Reparo descoberto", 2, predecessors=["I"])
    close = task("C", "Fechar", 2, predecessors=["I", "R"])
    startup = task("S", "Partida", 1, predecessors=["C"])
    project = TurnaroundProject(
        tasks=[inspection, repair, close, startup],
        capacities={},
    )

    effective_startup = startup.model_copy(
        update={
            "precedences": [
                *startup.precedences,
                Precedence(predecessor_id="R"),
            ]
        }
    )
    items = [
        scheduled("C", "Fechar", 2, 4, fixed=True),
        scheduled("R", "Reparo descoberto", 3, 5),
        scheduled("S", "Partida", 5, 6),
    ]

    result = analyze_effective_criticality(
        project=project,
        effective_tasks=[repair, effective_startup],
        items=items,
        capacities=project.capacities,
        current_time=3,
        makespan=6,
    )

    assert result.critical_ids == {"R", "S"}
    assert result.path_ids == ["R", "S"]
    assert "gate de escopo" in result.reasons["R"]
    assert any(
        driver.kind == "scope_gate"
        and driver.predecessor_id == "R"
        and driver.successor_id == "S"
        for driver in result.drivers
    )
