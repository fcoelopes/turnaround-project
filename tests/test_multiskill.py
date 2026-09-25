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
    analyze_effective_criticality,
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



def test_multiskill_filters_impossible_mode_and_keeps_feasible_alternative():
    task = TurnaroundTask(
        id="A",
        name="Escolha de modo",
        modes=[
            ExecutionMode(
                name="rapido",
                duration=2,
                resources={"Mecânica": 1, "Soldagem": 1},
            ),
            ExecutionMode(
                name="lento",
                duration=5,
                resources={"Mecânica": 1},
            ),
        ],
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

    result = solve_mrcpsp(
        [task],
        capacities={"Mecânica": 1, "Soldagem": 1},
        workforce=workforce,
    )

    assert result.makespan == 5.0
    assert result.tasks[0].mode_name == "lento"
    assert result.tasks[0].skill_assignments == {"Mecânica": ("P1",)}



def test_multiskill_person_release_is_visible_as_criticality_driver():
    tasks = [
        _task("A", "Primeira", duration=4, resources={"Mecânica": 1}),
        _task("B", "Segunda", duration=3, resources={"Soldagem": 1}),
    ]
    project = TurnaroundProject(
        tasks=tasks,
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

    schedule = solve_mrcpsp(
        tasks,
        project.capacities,
        workforce=workforce,
    )
    criticality = analyze_effective_criticality(
        project=project,
        effective_tasks=tasks,
        items=schedule.tasks,
        capacities={
            "Mecânica": 1,
            "Soldagem": 1,
        },
        current_time=0,
        makespan=schedule.makespan,
    )

    ordered = sorted(schedule.tasks, key=lambda item: item.start)
    predecessor_id = ordered[0].task_id
    successor_id = ordered[1].task_id

    assert criticality.path_ids == [predecessor_id, successor_id]
    assert any(
        driver.kind == "person"
        and driver.predecessor_id == predecessor_id
        and driver.successor_id == successor_id
        and driver.detail == "P1"
        for driver in criticality.drivers
    )
