from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, BinaryIO, TextIO

from .advanced_models import LogicalGroup, TurnaroundProject, TurnaroundTask


def _load_json(
    source: str | Path | BinaryIO | TextIO | dict[str, Any],
) -> dict[str, Any]:
    if isinstance(source, dict):
        return deepcopy(source)
    if hasattr(source, "read"):
        raw = source.read()
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return json.loads(raw)
    return json.loads(Path(source).read_text(encoding="utf-8"))


def _uid_index(project: TurnaroundProject) -> dict[str, str]:
    return {
        str(task.project_uid): task.id
        for task in project.tasks
        if task.project_uid is not None
    }


def _resolve_project_uid(
    project_uid: str,
    uid_to_id: dict[str, str],
    *,
    context: str,
) -> str:
    key = str(project_uid)
    try:
        return uid_to_id[key]
    except KeyError as exc:
        raise ValueError(
            f"{context}: UID do Microsoft Project desconhecido {key}"
        ) from exc


def _normalize_trigger(
    raw: dict[str, Any],
    uid_to_id: dict[str, str],
    *,
    context: str,
) -> dict[str, Any]:
    data = deepcopy(raw)
    source_uid = data.pop("source_task_uid", None)
    source_id = data.get("source_task_id")

    if source_uid is not None:
        resolved_id = _resolve_project_uid(
            str(source_uid),
            uid_to_id,
            context=context,
        )
        if source_id is not None and str(source_id) != resolved_id:
            raise ValueError(
                f"{context}: source_task_id={source_id} conflita com "
                f"source_task_uid={source_uid} (ID atual {resolved_id})"
            )
        data["source_task_id"] = resolved_id

    if "source_task_id" not in data:
        raise ValueError(
            f"{context}: informe source_task_uid (recomendado para XML) "
            "ou source_task_id"
        )
    return data


def _normalize_activation(
    raw: dict[str, Any],
    uid_to_id: dict[str, str],
    *,
    context: str,
) -> dict[str, Any]:
    data = deepcopy(raw)
    conditions = []
    for index, condition in enumerate(data.get("conditions", [])):
        conditions.append(
            _normalize_trigger(
                condition,
                uid_to_id,
                context=f"{context}.conditions[{index}]",
            )
        )
    if "conditions" in data:
        data["conditions"] = conditions
    return data


def _normalize_precedences(
    raw: list[dict[str, Any]],
    uid_to_id: dict[str, str],
    *,
    context: str,
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        data = deepcopy(item)
        predecessor_uid = data.pop("predecessor_uid", None)
        predecessor_id = data.get("predecessor_id")
        if predecessor_uid is not None:
            resolved_id = _resolve_project_uid(
                str(predecessor_uid),
                uid_to_id,
                context=f"{context}[{index}]",
            )
            if predecessor_id is not None and str(predecessor_id) != resolved_id:
                raise ValueError(
                    f"{context}[{index}]: predecessor_id={predecessor_id} "
                    f"conflita com predecessor_uid={predecessor_uid} "
                    f"(ID atual {resolved_id})"
                )
            data["predecessor_id"] = resolved_id
        normalized.append(data)
    return normalized


def _normalize_task_patch(
    raw: dict[str, Any],
    uid_to_id: dict[str, str],
    *,
    context: str,
) -> dict[str, Any]:
    data = deepcopy(raw)
    if "activation" in data:
        data["activation"] = _normalize_activation(
            data["activation"],
            uid_to_id,
            context=f"{context}.activation",
        )
    if "precedences" in data:
        data["precedences"] = _normalize_precedences(
            data["precedences"],
            uid_to_id,
            context=f"{context}.precedences",
        )
    return data


def _normalize_group(
    raw: dict[str, Any],
    uid_to_id: dict[str, str],
    *,
    context: str,
) -> dict[str, Any]:
    data = deepcopy(raw)

    member_uids = data.pop("member_task_uids", None)
    member_ids = data.get("member_task_ids")
    if member_uids is not None:
        resolved_ids = [
            _resolve_project_uid(
                str(uid),
                uid_to_id,
                context=f"{context}.member_task_uids",
            )
            for uid in member_uids
        ]
        if member_ids is not None and [str(x) for x in member_ids] != resolved_ids:
            raise ValueError(
                f"{context}: member_task_ids conflita com member_task_uids"
            )
        data["member_task_ids"] = resolved_ids

    if "member_task_ids" not in data:
        raise ValueError(
            f"{context}: informe member_task_uids (recomendado para XML) "
            "ou member_task_ids"
        )

    if data.get("when") is not None:
        data["when"] = _normalize_trigger(
            data["when"],
            uid_to_id,
            context=f"{context}.when",
        )
    return data


def apply_scope_config(
    project: TurnaroundProject,
    source: str | Path | BinaryIO | TextIO | dict[str, Any],
) -> TurnaroundProject:
    """Aplica metadados de escopo potencial sem alterar o cronograma-base.

    Referências estáveis recomendadas para XML do Microsoft Project:
      - task_uid_overrides: {"<UID>": {...}}
      - TriggerCondition: {"source_task_uid": "<UID>", ...}
      - LogicalGroup: {"member_task_uids": ["<UID>", ...], ...}
      - Precedence em override/added_task: {"predecessor_uid": "<UID>", ...}

    As formas legadas baseadas em task ID continuam aceitas para compatibilidade.
    O UID é resolvido para o ID atual a cada importação, então inserir/reordenar
    tarefas no Microsoft Project não invalida as regras do sidecar.
    """
    config = _load_json(source)
    uid_to_id = _uid_index(project)

    # Permite que tarefas adicionadas pelo sidecar também exponham project_uid
    # para referências internas no mesmo arquivo.
    for raw in config.get("added_tasks", []):
        project_uid = raw.get("project_uid")
        task_id = raw.get("id")
        if project_uid is None or task_id is None:
            continue
        key = str(project_uid)
        if key in uid_to_id and uid_to_id[key] != str(task_id):
            raise ValueError(
                f"added_tasks: project_uid duplicado {key} "
                f"para IDs {uid_to_id[key]} e {task_id}"
            )
        uid_to_id[key] = str(task_id)

    legacy_overrides = config.get("task_overrides", {})
    uid_overrides = config.get("task_uid_overrides", {})

    resolved_overrides: dict[str, dict[str, Any]] = {}

    for task_id, patch in legacy_overrides.items():
        resolved_overrides[str(task_id)] = _normalize_task_patch(
            patch,
            uid_to_id,
            context=f"task_overrides[{task_id}]",
        )

    for project_uid, patch in uid_overrides.items():
        task_id = _resolve_project_uid(
            str(project_uid),
            uid_to_id,
            context=f"task_uid_overrides[{project_uid}]",
        )
        if task_id in resolved_overrides:
            raise ValueError(
                f"Atividade {task_id} recebeu override por ID e por UID; "
                "use apenas uma forma de referência"
            )
        resolved_overrides[task_id] = _normalize_task_patch(
            patch,
            uid_to_id,
            context=f"task_uid_overrides[{project_uid}]",
        )

    tasks: list[TurnaroundTask] = []
    seen: set[str] = set()

    for task in project.tasks:
        data = task.model_dump()
        if task.id in resolved_overrides:
            patch = resolved_overrides[task.id]
            unknown = set(patch) - {
                "name",
                "wbs",
                "modes",
                "precedences",
                "activation",
            }
            if unknown:
                raise ValueError(
                    f"Override {task.id}: campos não suportados {sorted(unknown)}"
                )
            data.update(deepcopy(patch))
        updated = TurnaroundTask.model_validate(data)
        tasks.append(updated)
        seen.add(updated.id)

    missing_overrides = set(resolved_overrides) - seen
    if missing_overrides:
        raise ValueError(
            f"Overrides para atividades inexistentes: {sorted(missing_overrides)}"
        )

    for index, raw in enumerate(config.get("added_tasks", [])):
        data = deepcopy(raw)
        if "activation" in data:
            data["activation"] = _normalize_activation(
                data["activation"],
                uid_to_id,
                context=f"added_tasks[{index}].activation",
            )
        if "precedences" in data:
            data["precedences"] = _normalize_precedences(
                data["precedences"],
                uid_to_id,
                context=f"added_tasks[{index}].precedences",
            )
        added = TurnaroundTask.model_validate(data)
        if added.id in seen:
            raise ValueError(f"added_tasks contém ID já existente: {added.id}")
        tasks.append(added)
        seen.add(added.id)

    groups = [
        LogicalGroup.model_validate(
            _normalize_group(
                raw,
                uid_to_id,
                context=f"logical_groups[{index}]",
            )
        )
        for index, raw in enumerate(config.get("logical_groups", []))
    ]

    deadline = config.get("deadline", project.deadline)
    capacities = dict(project.capacities)
    capacities.update(config.get("capacity_overrides", {}))

    return TurnaroundProject(
        tasks=tasks,
        capacities=capacities,
        deadline=deadline,
        logical_groups=groups,
    )
