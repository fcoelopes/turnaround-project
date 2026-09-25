from __future__ import annotations

import pytest

from turnaround import (
    ExecutionMode,
    ExecutionState,
    Person,
    TaskExecution,
    TurnaroundProject,
    TurnaroundTask,
    WorkforceProfile,
    reschedule_from_state,
    solve_mrcpsp,
)


def _task(
    task_id: str,
    name: str,
    *,
    duration: float = 4.0,
    resources: dict[str, float] | None = None,
) -> TurnaroundTask:
    return TurnaroundTask(
        id=task_id,
        name=name,
        modes=[
            ExecutionMode(
                name="base",
                duration=duration,
                resources=resources or {},
            )
        ],
    )


def test_multiskill_prevents_double_counting_same_person_across_skills():
    tasks = [
        _task("A", "Mecânica", resources={"Mecânica": 1}),
        _task("B", "Soldagem", resources={"Soldagem": 1}),
    ]
    workforce = WorkforceProfile(
        skills=["Mecânica", "Soldagem"],
        people=[
            Person(
                id="P1",
                name="Ana",
                skills=["Mecânica", "Soldagem"],
            )
        ],
    )

    result = solve_mrcpsp(
        tasks,
        capacities={"Mecânica": 99, "Soldagem": 99},
        workforce=workforce,
    )

    # A capacidade agregada por skill pareceria permitir paralelismo, mas a
    # mesma pessoa não pode ocupar Mecânica e Soldagem ao mesmo tempo.
    assert result.makespan == 8.0
    assert {
        person_id
        for item in result.tasks
        for values in item.skill_assignments.values()
        for person_id in values
    } == {"P1"}


def test_multiskill_allows_parallelism_with_distinct_qualified_people():
    tasks = [
        _task("A", "Mecânica", resources={"Mecânica": 1}),
        _task("B", "Soldagem", resources={"Soldagem": 1}),
    ]
    workforce = WorkforceProfile(
        skills=["Mecânica", "Soldagem"],
        people=[
            Person(id="P1", name="Ana", skills=["Mecânica"]),
            Person(id="P2", name="Bruno", skills=["Soldagem"]),
        ],
    )

    result = solve_mrcpsp(
        tasks,
        capacities={"Mecânica": 1, "Soldagem": 1},
        workforce=workforce,
    )

    assert result.makespan == 4.0
    assignments = {
        item.task_id: item.skill_assignments
        for item in result.tasks
    }
    assert assignments["A"] == {"Mecânica": ("P1",)}
    assert assignments["B"] == {"Soldagem": ("P2",)}


def test_multiskill_chooses_matching_without_wasting_rare_skill():
    task = _task(
        "A",
        "Trabalho combinado",
        resources={"Mecânica": 1, "Soldagem": 1},
    )
    workforce = WorkforceProfile(
        skills=["Mecânica", "Soldagem"],
        people=[
            Person(id="P1", name="Ana", skills=["Mecânica", "Soldagem"]),
            Person(id="P2", name="Bruno", skills=["Mecânica"]),
        ],
    )

    result = solve_mrcpsp(
        [task],
        capacities={"Mecânica": 2, "Soldagem": 1},
        workforce=workforce,
    )

    item = result.tasks[0]
    assert item.skill_assignments["Soldagem"] == ("P1",)
    assert item.skill_assignments["Mecânica"] == ("P2",)


def test_multiskill_rejects_fractional_people_demand():
    task = _task(
        "A",
        "Demanda fracionária",
        resources={"Mecânica": 1.5},
    )
    workforce = WorkforceProfile(
        skills=["Mecânica"],
        people=[
            Person(id="P1", name="Ana", skills=["Mecânica"]),
            Person(id="P2", name="Bruno", skills=["Mecânica"]),
        ],
    )

    with pytest.raises(ValueError, match="demanda inteira"):
        solve_mrcpsp(
            [task],
            capacities={"Mecânica": 2},
            workforce=workforce,
        )


def test_multiskill_freezes_people_of_in_progress_task():
    project = TurnaroundProject(
        tasks=[
            _task("A", "Em andamento", duration=4, resources={"Mecânica": 1}),
            _task("B", "Trabalho futuro", duration=2, resources={"Soldagem": 1}),
        ],
        capacities={"Mecânica": 1, "Soldagem": 1},
    )
    workforce = WorkforceProfile(
        skills=["Mecânica", "Soldagem"],
        people=[
            Person(
                id="P1",
                name="Ana",
                skills=["Mecânica", "Soldagem"],
            )
        ],
    )
    state = ExecutionState(
        current_time=2,
        executions={
            "A": TaskExecution(
                status="in_progress",
                start=0,
                finish=4,
                mode_name="base",
                skill_assignments={"Mecânica": ["P1"]},
            )
        },
    )

    result = reschedule_from_state(
        project,
        state,
        workforce=workforce,
    )

    future = next(item for item in result.schedule.tasks if item.task_id == "B")
    assert future.start == 4.0
    assert future.skill_assignments == {"Soldagem": ("P1",)}


def test_multiskill_requires_frozen_assignment_for_in_progress_human_task():
    project = TurnaroundProject(
        tasks=[
            _task("A", "Em andamento", duration=4, resources={"Mecânica": 1}),
        ],
        capacities={"Mecânica": 1},
    )
    workforce = WorkforceProfile(
        skills=["Mecânica"],
        people=[Person(id="P1", name="Ana", skills=["Mecânica"])],
    )
    state = ExecutionState(
        current_time=2,
        executions={
            "A": TaskExecution(
                status="in_progress",
                start=0,
                finish=4,
                mode_name="base",
            )
        },
    )

    with pytest.raises(ValueError, match="não possui pessoas congeladas"):
        reschedule_from_state(
            project,
            state,
            workforce=workforce,
        )
