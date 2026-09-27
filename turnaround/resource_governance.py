from __future__ import annotations

from typing import Literal


CapacityOrigin = Literal["PROJECT", "INFORMADA", "INFERIDA"]


def resolve_capacity_origins(
    *,
    base_capacities: dict[str, int | float],
    scenario_capacities: dict[str, int | float],
    project_resources: set[str],
) -> dict[str, CapacityOrigin]:
    """Classify the provenance of capacities used by a planning scenario.

    PROJECT: unchanged explicit capacity imported from Microsoft Project.
    INFORMADA: the planner changed/provided the scenario capacity.
    INFERIDA: unchanged fallback inferred from task demand.
    """

    origins: dict[str, CapacityOrigin] = {}
    for resource, scenario_value in scenario_capacities.items():
        base_value = base_capacities.get(resource)
        if (
            base_value is None
            or abs(float(scenario_value) - float(base_value)) > 1e-9
        ):
            origins[resource] = "INFORMADA"
        elif resource in project_resources:
            origins[resource] = "PROJECT"
        else:
            origins[resource] = "INFERIDA"
    return origins


def inferred_capacity_resources(
    origins: dict[str, CapacityOrigin],
) -> list[str]:
    return sorted(
        resource
        for resource, origin in origins.items()
        if origin == "INFERIDA"
    )
