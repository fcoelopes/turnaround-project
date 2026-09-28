from __future__ import annotations

from dataclasses import dataclass

from .advanced_adapter import project_from_tasks
from .cpm import topological_order
from .models import Link, Task
from .planning_baseline import ApprovedPlanningBaseline
from .planning_scope_risk import (
    apply_planning_scope_risks_to_project,
    materialize_planning_scope,
)


@dataclass(frozen=True)
class ExecutionReadinessReport:
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    checks: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return not self.errors


def _preview(items: list[str], limit: int = 8) -> str:
    shown = items[:limit]
    suffix = "" if len(items) <= limit else f" · +{len(items) - limit}"
    return ", ".join(shown) + suffix


def _precedence_satisfied(
    link: Link,
    *,
    task_start: float,
    task_finish: float,
    pred_start: float,
    pred_finish: float,
) -> bool:
    lag = float(link.lag_h)
    if link.relation == "FS":
        return task_start >= pred_finish + lag - 1e-9
    if link.relation == "SS":
        return task_start >= pred_start + lag - 1e-9
    if link.relation == "FF":
        return task_finish >= pred_finish + lag - 1e-9
    return task_finish >= pred_start + lag - 1e-9


def validate_planning_baseline_for_execution(
    baseline: ApprovedPlanningBaseline,
    *,
    allow_dangling_repair: bool = False,
    require_formal_approval: bool = False,
) -> ExecutionReadinessReport:
    """Valida se uma Baseline 0 pode atravessar a fronteira para Execução.

    O objetivo não é reprovar premissas gerenciais, e sim impedir que a página de
    replanejamento receba um snapshot estruturalmente inconsistente.
    """
    errors: list[str] = []
    warnings: list[str] = []
    checks: list[str] = []

    if not baseline.tasks:
        errors.append("O baseline não possui atividades.")
        return ExecutionReadinessReport(tuple(errors), tuple(warnings), tuple(checks))

    if require_formal_approval:
        missing_governance = []
        if not (baseline.scenario_name or "").strip():
            missing_governance.append("nome do cenário")
        if not (baseline.approved_by or "").strip():
            missing_governance.append("aprovador")
        if not (baseline.approval_reason or "").strip():
            missing_governance.append("motivo da aprovação")
        if missing_governance:
            errors.append(
                "Baseline sem governança formal completa: "
                + ", ".join(missing_governance)
                + "."
            )
        else:
            checks.append("Governança formal da Baseline 0 registrada")

    task_ids = [item.id for item in baseline.tasks]
    duplicates = sorted(
        task_id
        for task_id in set(task_ids)
        if task_ids.count(task_id) > 1
    )
    if duplicates:
        errors.append(
            "IDs de atividade duplicados: " + _preview(duplicates) + "."
        )
    else:
        checks.append("IDs de atividade únicos")

    dangling = baseline.dangling_predecessor_links()
    if dangling:
        details = [
            f"{task_id} → {predecessor_id}"
            for task_id, predecessor_id in dangling
        ]
        if allow_dangling_repair:
            warnings.append(
                "Vínculos órfãos legados serão removidos na ponte para Execução: "
                + _preview(details)
                + "."
            )
        else:
            errors.append(
                "Existem predecessoras fora do snapshot: "
                + _preview(details)
                + "."
            )
    else:
        checks.append("Predecessoras pertencem ao snapshot")

    tasks = baseline.to_tasks(
        drop_dangling_predecessors=allow_dangling_repair and bool(dangling)
    )

    self_links = [
        f"{task.id} → {link.predecessor_id}"
        for task in tasks
        for link in task.predecessors
        if link.predecessor_id == task.id
    ]
    if self_links:
        errors.append(
            "Existem auto-precedências: " + _preview(self_links) + "."
        )

    if not duplicates and (not dangling or allow_dangling_repair):
        try:
            topological_order(tasks)
        except ValueError as exc:
            errors.append(f"Rede de precedências inválida: {exc}")
        else:
            checks.append("Rede de precedências acíclica")

    try:
        project = project_from_tasks(
            tasks,
            baseline.capacities,
            deadline=baseline.deadline_h,
            resource_calendars=baseline.to_resource_calendars(),
        )
        apply_planning_scope_risks_to_project(
            project,
            baseline.scope_risks,
        )
    except ValueError as exc:
        errors.append(f"Domínio de execução inválido: {exc}")
    else:
        checks.append("Escopo potencial e gatilhos compatíveis com a execução")

    if not baseline.capacities_validated:
        errors.append(
            "As capacidades do baseline não estão formalmente validadas."
        )
    else:
        checks.append("Capacidades formalmente validadas")

    capacity_errors: list[str] = []
    for task in tasks:
        for resource, demand in task.resources.items():
            demand_value = float(demand)
            if demand_value <= 0:
                continue
            capacity = baseline.capacities.get(resource)
            if capacity is None:
                capacity_errors.append(
                    f"{task.id}: {resource} sem capacidade"
                )
            elif float(capacity) + 1e-9 < demand_value:
                capacity_errors.append(
                    f"{task.id}: {resource} exige {demand_value:g} > "
                    f"{float(capacity):g}"
                )
    if capacity_errors:
        errors.append(
            "Há demandas sem capacidade executável: "
            + _preview(capacity_errors)
            + "."
        )
    else:
        checks.append("Recursos de todas as atividades possuem capacidade suficiente")

    schedule_ids = [item.task_id for item in baseline.schedule]
    duplicate_schedule_ids = sorted(
        task_id
        for task_id in set(schedule_ids)
        if schedule_ids.count(task_id) > 1
    )
    if duplicate_schedule_ids:
        errors.append(
            "O cronograma aprovado possui atividades duplicadas: "
            + _preview(duplicate_schedule_ids)
            + "."
        )

    try:
        base_tasks = materialize_planning_scope(
            tasks,
            baseline.scope_risks,
            active_scope_task_ids=set(),
        )
    except ValueError as exc:
        errors.append(f"Escopo-base inválido: {exc}")
        base_tasks = []

    expected_base_ids = {task.id for task in base_tasks}
    scheduled_ids = set(schedule_ids)
    missing_schedule = sorted(expected_base_ids - scheduled_ids)
    extra_schedule = sorted(scheduled_ids - expected_base_ids)
    if missing_schedule:
        errors.append(
            "Atividades do escopo-base ausentes no cronograma aprovado: "
            + _preview(missing_schedule)
            + "."
        )
    if extra_schedule:
        errors.append(
            "O cronograma aprovado contém atividades fora do escopo-base: "
            + _preview(extra_schedule)
            + "."
        )
    if not missing_schedule and not extra_schedule and not duplicate_schedule_ids:
        checks.append("Cronograma aprovado cobre exatamente o escopo-base")

    schedule_by_id = {
        item.task_id: item
        for item in baseline.schedule
    }
    task_by_id = {task.id: task for task in base_tasks}

    timing_errors: list[str] = []
    for task_id in expected_base_ids & scheduled_ids:
        item = schedule_by_id[task_id]
        task = task_by_id[task_id]
        if item.finish_h < item.start_h - 1e-9:
            timing_errors.append(f"{task_id}: término anterior ao início")
            continue
        elapsed = float(item.finish_h) - float(item.start_h)
        if abs(elapsed - float(item.duration_h)) > 1e-9:
            timing_errors.append(
                f"{task_id}: intervalo {elapsed:g} h != duração {item.duration_h:g} h"
            )
        if abs(float(item.duration_h) - float(task.duration_h)) > 1e-9:
            timing_errors.append(
                f"{task_id}: duração aprovada {item.duration_h:g} h != "
                f"atividade {task.duration_h:g} h"
            )
        for link in task.predecessors:
            predecessor = schedule_by_id.get(link.predecessor_id)
            if predecessor is None:
                continue
            if not _precedence_satisfied(
                link,
                task_start=float(item.start_h),
                task_finish=float(item.finish_h),
                pred_start=float(predecessor.start_h),
                pred_finish=float(predecessor.finish_h),
            ):
                timing_errors.append(
                    f"{task_id}: vínculo {link.predecessor_id}{link.relation}"
                    f"{link.lag_h:+d}h violado"
                )

    if timing_errors:
        errors.append(
            "Há inconsistências de tempo no cronograma aprovado: "
            + _preview(timing_errors)
            + "."
        )
    elif expected_base_ids:
        checks.append("Horários aprovados respeitam duração e precedências")

    calendar_errors: list[str] = []
    calendars = baseline.to_resource_calendars()
    for item in baseline.schedule:
        for resource, demand in item.resources.items():
            if float(demand) <= 0:
                continue
            calendar = calendars.get(resource)
            if calendar is None:
                continue
            if not calendar.is_working_interval(
                float(item.start_h),
                float(item.duration_h),
            ):
                calendar_errors.append(
                    f"{item.task_id}: {resource} fora da janela de trabalho"
                )
    if calendar_errors:
        errors.append(
            "O cronograma aprovado viola calendário de recurso: "
            + _preview(calendar_errors)
            + "."
        )
    elif calendars:
        checks.append("Cronograma aprovado respeita calendários dos recursos")

    resource_events: dict[str, list[tuple[float, int, float, str]]] = {}
    for item in baseline.schedule:
        for resource, demand in item.resources.items():
            amount = float(demand)
            if amount <= 0:
                continue
            resource_events.setdefault(resource, []).extend(
                [
                    (float(item.start_h), 1, amount, item.task_id),
                    (float(item.finish_h), 0, -amount, item.task_id),
                ]
            )

    overloads: list[str] = []
    for resource, events in resource_events.items():
        capacity = float(baseline.capacities.get(resource, 0.0))
        usage = 0.0
        for time_h, _kind, delta, task_id in sorted(
            events,
            key=lambda event: (event[0], event[1]),
        ):
            usage += delta
            if usage > capacity + 1e-9:
                overloads.append(
                    f"{resource} em H+{time_h:g}: uso {usage:g} > {capacity:g}"
                )
                break
    if overloads:
        errors.append(
            "O cronograma aprovado excede capacidade: "
            + _preview(overloads)
            + "."
        )
    elif resource_events:
        checks.append("Cronograma aprovado respeita capacidades simultâneas")

    if baseline.schedule:
        expected_makespan = max(float(item.finish_h) for item in baseline.schedule)
        if abs(expected_makespan - float(baseline.makespan_h)) > 1e-9:
            errors.append(
                f"Makespan persistido ({baseline.makespan_h:g} h) diverge do "
                f"cronograma ({expected_makespan:g} h)."
            )
        else:
            checks.append("Makespan consistente com o cronograma aprovado")

    if baseline.deadline_h is None:
        warnings.append(
            "Baseline sem janela-alvo: a execução pode prosseguir, mas não haverá "
            "referência formal de prazo."
        )

    return ExecutionReadinessReport(
        errors=tuple(dict.fromkeys(errors)),
        warnings=tuple(dict.fromkeys(warnings)),
        checks=tuple(dict.fromkeys(checks)),
    )
