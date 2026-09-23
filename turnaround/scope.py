from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, BinaryIO, TextIO

from .advanced_models import LogicalGroup, TurnaroundProject, TurnaroundTask


def _load_json(source: str | Path | BinaryIO | TextIO | dict[str, Any]) -> dict[str, Any]:
    if isinstance(source, dict):
        return deepcopy(source)
    if hasattr(source, "read"):
        raw = source.read()
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return json.loads(raw)
    return json.loads(Path(source).read_text(encoding="utf-8"))


def apply_scope_config(
    project: TurnaroundProject,
    source: str | Path | BinaryIO | TextIO | dict[str, Any],
) -> TurnaroundProject:
    """Aplica metadados de escopo potencial sem alterar o cronograma-base."""
    config = _load_json(source)
    overrides = config.get("task_overrides", {})
    tasks: list[TurnaroundTask] = []
    seen: set[str] = set()

    for task in project.tasks:
        data = task.model_dump()
        if task.id in overrides:
            patch = overrides[task.id]
            unknown = set(patch) - {"name", "wbs", "modes", "precedences", "activation"}
            if unknown:
                raise ValueError(f"Override {task.id}: campos não suportados {sorted(unknown)}")
            data.update(deepcopy(patch))
        updated = TurnaroundTask.model_validate(data)
        tasks.append(updated)
        seen.add(updated.id)

    missing_overrides = set(overrides) - seen
    if missing_overrides:
        raise ValueError(f"Overrides para atividades inexistentes: {sorted(missing_overrides)}")

    for raw in config.get("added_tasks", []):
        added = TurnaroundTask.model_validate(raw)
        if added.id in seen:
            raise ValueError(f"added_tasks contém ID já existente: {added.id}")
        tasks.append(added)
        seen.add(added.id)

    groups = [LogicalGroup.model_validate(raw) for raw in config.get("logical_groups", [])]
    deadline = config.get("deadline", project.deadline)
    capacities = dict(project.capacities)
    capacities.update(config.get("capacity_overrides", {}))
    return TurnaroundProject(
        tasks=tasks,
        capacities=capacities,
        deadline=deadline,
        logical_groups=groups,
    )
