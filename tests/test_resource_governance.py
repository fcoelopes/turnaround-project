from __future__ import annotations

from turnaround.resource_governance import (
    inferred_capacity_resources,
    resolve_capacity_origins,
)


def test_capacity_origins_distinguish_project_informed_and_inferred():
    origins = resolve_capacity_origins(
        base_capacities={
            "Mecânica": 4,
            "Elétrica": 2,
            "Guindaste": 1,
        },
        scenario_capacities={
            "Mecânica": 4,
            "Elétrica": 3,
            "Guindaste": 1,
        },
        project_resources={"Mecânica", "Elétrica"},
    )

    assert origins == {
        "Mecânica": "PROJECT",
        "Elétrica": "INFORMADA",
        "Guindaste": "INFERIDA",
    }
    assert inferred_capacity_resources(origins) == ["Guindaste"]


def test_capacity_without_base_is_informed_when_planner_provides_it():
    origins = resolve_capacity_origins(
        base_capacities={},
        scenario_capacities={"Soldagem": 2},
        project_resources=set(),
    )

    assert origins == {"Soldagem": "INFORMADA"}
    assert inferred_capacity_resources(origins) == []
