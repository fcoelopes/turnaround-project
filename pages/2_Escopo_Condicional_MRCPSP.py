from __future__ import annotations

import io
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from turnaround import (
    ExecutionState,
    TaskExecution,
    analyze_effective_criticality,
    apply_scope_config,
    discover_resource_catalog,
    evaluate_scope_decisions,
    project_from_tasks,
    resolve_activation,
    reschedule_from_state,
    solve_mrcpsp,
)
from turnaround.io import project_xml_to_tasks
from turnaround.report import build_conditional_management_pdf
from turnaround.ui import apply_app_style, hero, section, status

ROOT = Path(__file__).resolve().parents[1]
DEMO_XML = ROOT / "sample_data" / "turnaround_conditional_model.xml"
DEMO_SCOPE = ROOT / "sample_data" / "turnaround_conditional_scope.json"

st.set_page_config(
    page_title="Escopo condicional · MRCPSP",
    page_icon="🧩",
    layout="wide",
    initial_sidebar_state="expanded",
)
apply_app_style()
hero(
    "Escopo condicional + MRCPSP",
    "Transforme achados de inspeção em novo escopo e veja o impacto operacional antes de comprometer a janela da parada.",
    "SCOPE DISCOVERY · REPLANEJAMENTO",
)


def load_project():
    section(
        "1",
        "Planejamento-base e regras de escopo",
        "O XML continua sendo a fonte do cronograma; o JSON acrescenta modos, gatilhos e decisões condicionais.",
    )

    c1, c2 = st.columns(2)
    with c1:
        xml_upload = st.file_uploader(
            "Microsoft Project XML",
            type=["xml"],
            key="advanced_xml",
        )
    with c2:
        scope_upload = st.file_uploader(
            "Regras de escopo / modos (JSON)",
            type=["json"],
            key="advanced_scope",
        )

    use_demo = st.checkbox(
        "Usar cenário demonstrativo 'Kinder Ovo'",
        value=xml_upload is None,
    )

    if xml_upload is not None:
        tasks, xml_caps = project_xml_to_tasks(xml_upload.getvalue())
        project_name = Path(xml_upload.name).stem.replace("_", " ")
    elif use_demo:
        tasks, xml_caps = project_xml_to_tasks(DEMO_XML.read_bytes())
        project_name = "Turnaround Kinder Ovo"
    else:
        st.info("Envie um XML do Project ou habilite o cenário demonstrativo.")
        st.stop()

    project = project_from_tasks(tasks, xml_caps)

    if scope_upload is not None:
        project = apply_scope_config(
            project,
            io.BytesIO(scope_upload.getvalue()),
        )
    elif use_demo:
        project = apply_scope_config(project, DEMO_SCOPE)

    return project, project_name


project, project_name = load_project()
base_project = project
resource_catalog = discover_resource_catalog(base_project)
base_capacities = dict(base_project.capacities)
unknown_resources = [
    entry
    for entry in resource_catalog.values()
    if not entry.capacity_defined
]

section(
    "2",
    "Cenário MRCPSP de recursos",
    (
        "Os controles são gerados automaticamente pela união entre Resource Sheet, "
        "recursos das tarefas e recursos de todos os modos MRCPSP."
    ),
)

scenario_name = st.text_input(
    "Nome do cenário",
    value="Cenário de recursos A",
    key="mrcpsp_scenario_name",
)

if project_name == "Turnaround Kinder Ovo":
    st.info(
        "Teste guiado: avance até 7 h. Você terá dois discoveries independentes: "
        "na inspeção mecânica, marque bearing_damage; no ensaio do motor, marque "
        "motor_replacement_required. O primeiro testa mudança de modo com Mecânica=4→5; "
        "o segundo adiciona a substituição do motor (6 h, Elétrica:2, Mecânica:2, Guindaste:1)."
    )

if unknown_resources:
    status(
        (
            "Foram encontrados recursos usados por tarefas/modos sem capacidade-base "
            "declarada no Project/sidecar: "
            + ", ".join(entry.name for entry in unknown_resources)
            + ". Eles aparecem abaixo com base não informada e cenário inicial 0."
        ),
        tone="warn",
        title="Capacidades de recursos precisam ser informadas.",
    )

cols = st.columns(min(4, max(1, len(resource_catalog))))
scenario_capacities: dict[str, float] = {}
for i, (resource, entry) in enumerate(resource_catalog.items()):
    base_capacity = entry.base_capacity
    default_value = float(base_capacity) if base_capacity is not None else 0.0
    upper = max(
        2.0,
        default_value * 2.5,
        float(entry.max_demand) * 2.0,
        float(entry.max_demand) + 2.0,
    )
    values_for_step = [float(entry.max_demand)]
    if base_capacity is not None:
        values_for_step.append(float(base_capacity))
    step = (
        1.0
        if all(float(value).is_integer() for value in values_for_step)
        else 0.5
    )
    help_text = (
        f"Capacidade-base: {base_capacity:g}. "
        if base_capacity is not None
        else "Capacidade-base não informada. "
    )
    help_text += (
        f"Maior demanda individual observada: {entry.max_demand:g}. "
        f"Usado em {len(entry.task_ids)} atividade(s)."
    )
    with cols[i % len(cols)]:
        scenario_capacities[resource] = st.slider(
            resource,
            min_value=0.0,
            max_value=float(upper),
            value=default_value,
            step=step,
            key=f"advanced_cap_{i}_{resource}",
            help=help_text,
        )

resource_scenario_df = pd.DataFrame(
    [
        {
            "Recurso": resource,
            "Base": (
                float(entry.base_capacity)
                if entry.base_capacity is not None
                else "não informada"
            ),
            "Maior demanda": float(entry.max_demand),
            "Cenário": float(scenario_capacities[resource]),
            "Δ vs base": (
                float(scenario_capacities[resource]) - float(entry.base_capacity)
                if entry.base_capacity is not None
                else "—"
            ),
            "Atividades que usam": len(entry.task_ids),
        }
        for resource, entry in resource_catalog.items()
    ]
)
st.dataframe(
    resource_scenario_df,
    use_container_width=True,
    hide_index=True,
)

project = base_project.model_copy(
    update={"capacities": scenario_capacities}
)

with st.expander("Modos disponíveis por atividade", expanded=bool(unknown_resources)):
    mode_rows = []
    for task in base_project.tasks:
        for mode in task.modes:
            feasible_base = all(
                demand <= base_capacities.get(resource, 0.0) + 1e-9
                for resource, demand in mode.resources.items()
            )
            feasible_scenario = all(
                demand <= scenario_capacities.get(resource, 0.0) + 1e-9
                for resource, demand in mode.resources.items()
            )
            missing_base = [
                resource
                for resource in mode.resources
                if resource not in base_capacities
            ]
            mode_rows.append(
                {
                    "ID": task.id,
                    "UID Project": task.project_uid or "—",
                    "Atividade": task.name,
                    "Tipo": task.activation.kind,
                    "Modo": mode.name,
                    "Duração (h)": mode.duration,
                    "Recursos": ", ".join(
                        f"{key}:{value:g}"
                        for key, value in mode.resources.items()
                    ),
                    "Custo": mode.cost,
                    "Capacidade ausente": ", ".join(missing_base) or "—",
                    "Factível na base": "sim" if feasible_base else "não",
                    "Factível no cenário": "sim" if feasible_scenario else "não",
                    "Novo modo liberado": (
                        "SIM"
                        if (not feasible_base and feasible_scenario)
                        else "não"
                    ),
                }
            )
    st.dataframe(
        pd.DataFrame(mode_rows),
        use_container_width=True,
        hide_index=True,
    )

empty_state = ExecutionState(current_time=0)
baseline_activation = resolve_activation(base_project, empty_state)
baseline_tasks = [
    task
    for task in base_project.tasks
    if task.id in baseline_activation.active_ids
]

base_baseline = None
base_baseline_error = None
try:
    base_baseline = solve_mrcpsp(
        baseline_tasks,
        base_project.capacities,
        deadline=base_project.deadline,
    )
except ValueError as exc:
    base_baseline_error = str(exc)

scenario_baseline = None
scenario_baseline_error = None
try:
    scenario_baseline = solve_mrcpsp(
        baseline_tasks,
        project.capacities,
        deadline=project.deadline,
    )
except ValueError as exc:
    scenario_baseline_error = str(exc)

if scenario_baseline is None:
    st.error(
        "O cenário ainda não consegue programar o escopo-base. "
        f"Ajuste as capacidades acima. Diagnóstico: {scenario_baseline_error}"
    )
    st.stop()

# O estado da parada usa o planejamento-base quando ele é factível; caso a
# capacidade-base esteja incompleta, usa o cenário informado como referência.
baseline = base_baseline or scenario_baseline

m1, m2, m3, m4 = st.columns(4)
m1.metric(
    "Makespan planejado",
    "—" if base_baseline is None else f"{base_baseline.makespan:.1f} h",
)
m2.metric(
    "Makespan · cenário-base",
    f"{scenario_baseline.makespan:.1f} h",
)
m3.metric(
    "Tarefas ativas na base",
    len(scenario_baseline.tasks),
)
m4.metric(
    "Escopo potencial",
    len(base_project.tasks) - len(scenario_baseline.tasks),
)

if base_baseline is None:
    status(
        (
            "O planejamento não pode ser resolvido apenas com as capacidades-base "
            "declaradas. O estado da parada abaixo usa as capacidades do cenário. "
            f"Diagnóstico base: {base_baseline_error}"
        ),
        tone="warn",
        title="Baseline de recursos incompleto.",
    )

section(
    "3",
    "Estado da parada e achados",
    "Avance a hora corrente, registre achados e resolva decisões lógicas de escopo.",
)

reference_start_times = {
    item.task_id: float(item.start)
    for item in baseline.tasks
}
stability_weight = st.slider(
    "Peso de estabilidade do replanejamento (λ)",
    min_value=0.0,
    max_value=2.0,
    value=1.0,
    step=0.1,
    help=(
        "Objetivo do rescheduling: makespan + λ × soma dos deslocamentos de início. "
        "Atraso à deadline continua sendo prioridade. λ=0 reproduz o comportamento anterior; "
        "λ=1 trata 1 h acumulada de mudança de início como 1 h no objetivo."
    ),
)
st.caption(
    "A estabilidade compara apenas atividades futuras que já existiam no plano anterior. "
    "Novo escopo descoberto não recebe penalidade por não possuir início de referência."
)

current_time = st.number_input(
    "Hora corrente desde o início da parada",
    min_value=0.0,
    value=min(7.0, float(baseline.makespan)),
    step=0.5,
)

executions: dict[str, TaskExecution] = {}
for item in baseline.tasks:
    if item.finish <= current_time + 1e-9:
        executions[item.task_id] = TaskExecution(
            status="completed",
            start=item.start,
            finish=item.finish,
            mode_name=item.mode_name,
        )
    elif item.start < current_time < item.finish:
        executions[item.task_id] = TaskExecution(
            status="in_progress",
            start=item.start,
            finish=item.finish,
            mode_name=item.mode_name,
        )

event_catalog: dict[str, set[str]] = {}
for task in project.tasks:
    for condition in task.activation.conditions:
        event_catalog.setdefault(
            condition.source_task_id,
            set(),
        ).update(condition.events)

for group in project.logical_groups:
    if group.when:
        group_events = set(group.when.events)
        group_events.update(group.event_routes)
        event_catalog.setdefault(
            group.when.source_task_id,
            set(),
        ).update(group_events)

name_by_id = {task.id: task.name for task in project.tasks}
project_order = {task.id: index for index, task in enumerate(project.tasks)}
wbs_by_id = {task.id: task.wbs for task in project.tasks}
events: dict[str, list[str]] = {}

if event_catalog:
    st.markdown("#### Resultados observados nas atividades gatilho")
    for source_id, options in sorted(event_catalog.items()):
        execution = executions.get(source_id)
        completed = (
            execution is not None
            and execution.status == "completed"
        )
        label = (
            f"{name_by_id.get(source_id, source_id)} · "
            f"{'concluída' if completed else 'ainda não concluída'}"
        )
        selected = st.multiselect(
            label,
            options=sorted(options),
            default=[],
            disabled=not completed,
            key=f"events_{source_id}",
        )
        if selected:
            events[source_id] = selected

group_members = {
    task_id
    for group in project.logical_groups
    for task_id in group.member_task_ids
}
independent_optional = [
    task
    for task in project.tasks
    if task.activation.kind == "optional"
    and task.id not in group_members
]

selected_optional_ids: list[str] = []
if independent_optional:
    labels = {
        task.id: f"{task.id} · {task.name}"
        for task in independent_optional
    }
    selected_optional_ids = st.multiselect(
        "Atividades opcionais selecionadas",
        options=list(labels),
        format_func=lambda task_id: labels[task_id],
    )

known_groups = {group.id: group for group in project.logical_groups}
stored_human_selections = st.session_state.setdefault(
    "scope_decision_selections",
    {},
)
stored_human_selections = {
    group_id: [
        task_id
        for task_id in selection
        if (
            group_id in known_groups
            and task_id in known_groups[group_id].member_task_ids
        )
    ]
    for group_id, selection in stored_human_selections.items()
    if group_id in known_groups
}
stored_human_selections = {
    group_id: selection
    for group_id, selection in stored_human_selections.items()
    if selection
}
st.session_state.scope_decision_selections = stored_human_selections

state = ExecutionState(
    current_time=current_time,
    events=events,
    selected_optional_ids=selected_optional_ids,
    group_selections=stored_human_selections,
    executions=executions,
)

decision_engine = evaluate_scope_decisions(
    project,
    state,
    reference_start_times=reference_start_times,
    stability_weight=stability_weight,
)

st.markdown("#### Decisões de escopo ativas")
if not decision_engine.pending_human:
    st.caption(
        "Nenhuma decisão humana pendente. Regras sem gatilho permanecem dormentes "
        "e não geram controles."
    )

human_choices: dict[str, list[str]] = {}
pending_human = decision_engine.pending_human
focused_decision = None

if pending_human:
    pending_ids = [decision.group_id for decision in pending_human]
    if len(pending_human) > 1:
        st.caption(
            f"{len(pending_human)} decisões humanas estão ativas. "
            "Apenas uma é detalhada por vez."
        )
        focused_group_id = st.selectbox(
            "Decisão em foco",
            options=pending_ids,
            key="scope_decision_focus",
        )
        focused_decision = next(
            decision
            for decision in pending_human
            if decision.group_id == focused_group_id
        )
    else:
        focused_decision = pending_human[0]

if focused_decision is not None:
    decision = focused_decision
    group = known_groups[decision.group_id]
    st.markdown(f"**{decision.group_id} · {group.operator.upper()}**")

    impact_rows = []
    feasible_impacts = []
    for index, impact in enumerate(decision.impacts):
        if impact.feasible:
            feasible_impacts.append((index, impact))
        impact_rows.append(
            {
                "Alternativa": " + ".join(impact.task_names),
                "Factível": "sim" if impact.feasible else "não",
                "Makespan (h)": impact.makespan if impact.feasible else None,
                "Atraso (h)": impact.tardiness if impact.feasible else None,
                "Custo": impact.total_cost if impact.feasible else None,
                "Δ início total (h)": (
                    impact.total_start_deviation if impact.feasible else None
                ),
                "Maior Δ início (h)": (
                    impact.max_start_deviation if impact.feasible else None
                ),
                "Gargalo de recursos": (
                    ", ".join(
                        f"{resource}: faltam {shortage:g}"
                        for resource, shortage in sorted(impact.resource_gaps.items())
                    )
                    or "—"
                ),
                "Diagnóstico": impact.error or "",
            }
        )
    st.dataframe(
        pd.DataFrame(impact_rows),
        use_container_width=True,
        hide_index=True,
    )

    option_indices = [index for index, impact in feasible_impacts]
    chosen_index = st.selectbox(
        "Resolver decisão",
        options=[None] + option_indices,
        key=f"scope_decision_{decision.group_id}",
        format_func=lambda index: (
            "— manter pendente —"
            if index is None
            else (
                " + ".join(decision.impacts[index].task_names)
            )
        ),
    )
    if chosen_index is not None:
        human_choices[decision.group_id] = list(
            decision.impacts[chosen_index].selection
        )

    if not decision.exhaustive:
        st.caption(
            "Grupo OR grande: a avaliação usa um conjunto limitado de candidatos "
            "para evitar explosão combinatória."
        )

if human_choices:
    stored_human_selections.update(human_choices)
    st.session_state.scope_decision_selections = stored_human_selections

if stored_human_selections:
    if st.button("Reabrir decisões humanas desta sessão"):
        st.session_state.scope_decision_selections = {}
        for key in list(st.session_state):
            if key.startswith("scope_decision_") and key != "scope_decision_selections":
                del st.session_state[key]
        st.rerun()

# Reexecuta o motor após eventuais escolhas humanas. Ele pode resolver grupos
# event/optimize adicionais que tenham sido liberados pela decisão recém tomada.
state = ExecutionState(
    current_time=current_time,
    events=events,
    selected_optional_ids=selected_optional_ids,
    group_selections=stored_human_selections,
    executions=executions,
)
decision_engine = evaluate_scope_decisions(
    project,
    state,
    reference_start_times=reference_start_times,
    stability_weight=stability_weight,
)
state = decision_engine.state

if decision_engine.auto_resolved:
    auto_rows = []
    for group_id, selection in decision_engine.auto_resolved.items():
        group = known_groups[group_id]
        decision = next(
            (
                item
                for item in decision_engine.decisions
                if item.group_id == group_id
                and item.applied_selection is not None
            ),
            None,
        )
        applied_impact = None
        if decision is not None:
            applied_impact = next(
                (
                    impact
                    for impact in decision.impacts
                    if impact.selection == decision.applied_selection
                ),
                None,
            )
        auto_rows.append(
            {
                "Regra": group_id,
                "Modo": group.resolution_mode,
                "Ramo aplicado": " + ".join(
                    name_by_id.get(task_id, task_id)
                    for task_id in selection
                ),
                "Factível": (
                    "sim"
                    if applied_impact is not None and applied_impact.feasible
                    else "não"
                ),
                "Makespan (h)": (
                    applied_impact.makespan
                    if applied_impact is not None and applied_impact.feasible
                    else None
                ),
                "Atraso (h)": (
                    applied_impact.tardiness
                    if applied_impact is not None and applied_impact.feasible
                    else None
                ),
                "Custo": (
                    applied_impact.total_cost
                    if applied_impact is not None and applied_impact.feasible
                    else None
                ),
                "Δ início total (h)": (
                    applied_impact.total_start_deviation
                    if applied_impact is not None and applied_impact.feasible
                    else None
                ),
                "Maior Δ início (h)": (
                    applied_impact.max_start_deviation
                    if applied_impact is not None and applied_impact.feasible
                    else None
                ),
                "Gargalo de recursos": (
                    ", ".join(
                        f"{resource}: faltam {shortage:g}"
                        for resource, shortage in sorted(
                            (applied_impact.resource_gaps if applied_impact is not None else {}).items()
                        )
                    )
                    or "—"
                ),
                "Diagnóstico": (
                    applied_impact.error
                    if applied_impact is not None and not applied_impact.feasible
                    else ""
                ),
            }
        )
    with st.expander("Regras determinísticas aplicadas", expanded=False):
        st.dataframe(
            pd.DataFrame(auto_rows),
            use_container_width=True,
            hide_index=True,
        )
        for decision in decision_engine.decisions:
            if (
                decision.applied_selection is None
                or len(decision.impacts) <= 1
            ):
                continue
            st.markdown(f"**Cenários sombra · {decision.group_id}**")
            shadow_rows = []
            for impact in decision.impacts:
                shadow_rows.append(
                    {
                        "Alternativa": " + ".join(impact.task_names),
                        "Aplicada": (
                            "SIM"
                            if impact.selection == decision.applied_selection
                            else "não"
                        ),
                        "Factível": "sim" if impact.feasible else "não",
                        "Makespan (h)": (
                            impact.makespan if impact.feasible else None
                        ),
                        "Atraso (h)": (
                            impact.tardiness if impact.feasible else None
                        ),
                        "Custo": (
                            impact.total_cost if impact.feasible else None
                        ),
                        "Δ início total (h)": (
                            impact.total_start_deviation if impact.feasible else None
                        ),
                        "Maior Δ início (h)": (
                            impact.max_start_deviation if impact.feasible else None
                        ),
                        "Gargalo de recursos": (
                            ", ".join(
                                f"{resource}: faltam {shortage:g}"
                                for resource, shortage in sorted(impact.resource_gaps.items())
                            )
                            or "—"
                        ),
                        "Diagnóstico": impact.error or "",
                    }
                )
            st.dataframe(
                pd.DataFrame(shadow_rows),
                use_container_width=True,
                hide_index=True,
            )

if decision_engine.unresolved_event_groups:
    status(
        (
            "Há regra(s) event-driven com gatilho ativo, mas nenhum event_route "
            "corresponde aos eventos observados: "
            + ", ".join(decision_engine.unresolved_event_groups)
        ),
        tone="warn",
        title="Rota automática ainda não determinada.",
    )

activation = resolve_activation(project, state)
pending_groups = [
    group_id
    for group_id, group_status in activation.group_states.items()
    if group_status == "pending_selection"
]
if pending_groups:
    status(
        (
            f"{len(pending_groups)} decisão(ões) de escopo continuam pendentes. "
            "Somente grupos cujo gatilho ocorreu entram nesta fila."
        ),
        tone="warn",
        title="Rolling horizon de decisões.",
    )

base_result = None
base_result_error = None
try:
    base_result = reschedule_from_state(
        base_project,
        state,
        reference_start_times=reference_start_times,
        stability_weight=stability_weight,
    )
except ValueError as exc:
    base_result_error = str(exc)

try:
    result = reschedule_from_state(
        project,
        state,
        reference_start_times=reference_start_times,
        stability_weight=stability_weight,
    )
except ValueError as exc:
    st.error(f"Cenário de recursos inviável: {exc}")
    if base_result_error:
        st.caption(f"Com os recursos-base também é inviável: {base_result_error}")
    st.stop()

active_now = result.activation.active_ids
new_scope = active_now - baseline_activation.active_ids

activation_df = pd.DataFrame(
    [
        {
            "ID": task.id,
            "UID Project": task.project_uid or "—",
            "Atividade": task.name,
            "Tipo": task.activation.kind,
            "Estado": result.activation.states[task.id].value,
            "Motivo": result.activation.reasons[task.id],
        }
        for task in project.tasks
    ]
)

all_items = result.frozen_tasks + result.schedule.tasks
criticality = analyze_effective_criticality(
    project=project,
    effective_tasks=result.effective_tasks,
    items=all_items,
    capacities=project.capacities,
    current_time=float(current_time),
    makespan=float(result.schedule.makespan),
)
critical_path_label = (
    " → ".join(criticality.path_ids)
    if criticality.path_ids
    else "—"
)

schedule_df = pd.DataFrame(
    [
        {
            "_Ordem": project_order.get(str(item.task_id), len(project.tasks)),
            "ID": item.task_id,
            "WBS": wbs_by_id.get(str(item.task_id), ""),
            "Atividade": item.task_name,
            "Modo": item.mode_name,
            "Início (h)": item.start,
            "Fim (h)": item.finish,
            "Duração (h)": item.duration,
            "Congelada": item.fixed,
            "Crítica atual": str(item.task_id) in criticality.critical_ids,
            "Controla por": criticality.reasons.get(str(item.task_id), ""),
            "Recursos": ", ".join(
                f"{key}:{value:g}"
                for key, value in item.resources.items()
            ),
        }
        for item in sorted(
            all_items,
            key=lambda scheduled: project_order.get(
                str(scheduled.task_id),
                len(project.tasks),
            ),
        )
    ]
)

section(
    "4",
    "Comparação do cenário MRCPSP",
    "Isole o efeito dos recursos: o escopo descoberto e o estado da parada são os mesmos; muda apenas a capacidade.",
)

if base_result is None:
    status(
        (
            "O escopo descoberto é inviável com os recursos-base. "
            f"Diagnóstico: {base_result_error}"
        ),
        tone="danger",
        title="Recursos-base insuficientes.",
    )
else:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric(
        "Makespan · recursos-base",
        f"{base_result.schedule.makespan:.1f} h",
    )
    c2.metric(
        "Makespan · cenário",
        f"{result.schedule.makespan:.1f} h",
        delta=f"{result.schedule.makespan - base_result.schedule.makespan:+.1f} h",
    )
    c3.metric(
        "Horas recuperadas",
        f"{max(0.0, base_result.schedule.makespan - result.schedule.makespan):.1f} h",
    )
    c4.metric(
        "Δ custo de modos",
        f"{result.schedule.total_cost - base_result.schedule.total_cost:+,.0f}",
    )

    s1, s2, s3, s4 = st.columns(4)
    s1.metric(
        "Δ início acumulado · base",
        f"{base_result.schedule.total_start_deviation:.1f} h",
    )
    s2.metric(
        "Δ início acumulado · cenário",
        f"{result.schedule.total_start_deviation:.1f} h",
        delta=(
            f"{result.schedule.total_start_deviation - base_result.schedule.total_start_deviation:+.1f} h"
        ),
    )
    s3.metric(
        "Maior deslocamento de início",
        f"{result.schedule.max_start_deviation:.1f} h",
    )
    s4.metric(
        "Atividades comparadas",
        result.schedule.stability_compared_tasks,
    )

    base_future = {item.task_id: item for item in base_result.schedule.tasks}
    scenario_future = {item.task_id: item for item in result.schedule.tasks}
    mode_comparison_rows = []
    for task_id in sorted(set(base_future) & set(scenario_future)):
        before = base_future[task_id]
        after = scenario_future[task_id]
        mode_comparison_rows.append(
            {
                "ID": task_id,
                "Atividade": after.task_name,
                "Modo · base": before.mode_name,
                "Modo · cenário": after.mode_name,
                "Mudou modo?": "SIM" if before.mode_name != after.mode_name else "não",
                "Duração base (h)": before.duration,
                "Duração cenário (h)": after.duration,
                "Fim base (h)": before.finish,
                "Fim cenário (h)": after.finish,
                "Δ fim (h)": after.finish - before.finish,
            }
        )

    mode_comparison_df = pd.DataFrame(mode_comparison_rows)
    changed_modes_df = (
        mode_comparison_df[mode_comparison_df["Mudou modo?"] == "SIM"]
        if not mode_comparison_df.empty
        else mode_comparison_df
    )

    if not changed_modes_df.empty:
        status(
            f"O MRCPSP trocou o modo de {len(changed_modes_df)} atividade(s) neste cenário.",
            tone="ok",
            title="Mudança de estratégia de execução detectada.",
        )
        st.dataframe(
            changed_modes_df,
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info(
            "Nenhuma atividade trocou de modo. O cenário ainda pode alterar o makespan "
            "por permitir ou restringir paralelismo."
        )

    with st.expander("Comparação completa das atividades futuras", expanded=True):
        st.dataframe(
            mode_comparison_df,
            use_container_width=True,
            hide_index=True,
        )

    st.session_state.setdefault("mrcpsp_saved_scenarios", [])
    save_col, clear_col = st.columns(2)
    with save_col:
        if st.button("Salvar cenário na comparação", type="primary"):
            st.session_state.mrcpsp_saved_scenarios.append(
                {
                    "Cenário": scenario_name,
                    "Makespan (h)": result.schedule.makespan,
                    "Atraso (h)": result.schedule.tardiness,
                    "Custo modos": result.schedule.total_cost,
                    "Horas recuperadas": base_result.schedule.makespan - result.schedule.makespan,
                    "Modos alterados": int(
                        sum(
                            row["Mudou modo?"] == "SIM"
                            for row in mode_comparison_rows
                        )
                    ),
                    "Recursos": "; ".join(
                        f"{resource}={scenario_capacities[resource]:g}"
                        for resource in sorted(scenario_capacities)
                    ),
                }
            )
            st.success(f"{scenario_name} salvo.")
    with clear_col:
        if st.button("Limpar cenários salvos"):
            st.session_state.mrcpsp_saved_scenarios = []

    if st.session_state.mrcpsp_saved_scenarios:
        st.markdown("#### Cenários salvos nesta sessão")
        st.dataframe(
            pd.DataFrame(st.session_state.mrcpsp_saved_scenarios),
            use_container_width=True,
            hide_index=True,
        )

section(
    "5",
    "Impacto do scope discovery",
    "Compare planejamento original, novo escopo, atraso e custo de modos em uma leitura gerencial.",
)

tab_exec, tab_activation, tab_schedule, tab_export = st.tabs(
    [
        "Visão executiva",
        "Mapa de ativação",
        "Cronograma",
        "Exportação",
    ]
)

with tab_exec:
    r1, r2, r3, r4 = st.columns(4)
    r1.metric(
        "Novo makespan",
        f"{result.schedule.makespan:.1f} h",
        delta=f"{result.schedule.makespan - baseline.makespan:+.1f} h",
    )
    r2.metric("Atraso", f"{result.schedule.tardiness:.1f} h")
    r3.metric("Novas tarefas ativas", len(new_scope))
    r4.metric("Custo dos modos", f"{result.schedule.total_cost:,.0f}")

    if project.deadline is None:
        status(
            "O projeto não possui deadline configurado para avaliar atraso.",
            tone="warn",
            title="Janela sem limite formal.",
        )
    elif result.schedule.makespan <= project.deadline:
        status(
            (
                f"O cronograma reprogramado permanece dentro da janela: "
                f"{result.schedule.makespan:.1f} h para "
                f"{project.deadline:.1f} h disponíveis."
            ),
            tone="ok",
            title="Scope discovery absorvido.",
        )
    else:
        status(
            (
                f"O novo escopo excede a janela em "
                f"{result.schedule.makespan - project.deadline:.1f} h."
            ),
            tone="danger",
            title="Intervenção gerencial necessária.",
        )

    if criticality.path_ids:
        path_names = " → ".join(
            f"{task_id} · {name_by_id.get(task_id, task_id)}"
            for task_id in criticality.path_ids
        )
        st.markdown(f"**Cadeia controladora atual:** {path_names}")
        branch_count = len(criticality.critical_ids - set(criticality.path_ids))
        if branch_count:
            st.caption(
                f"Há mais {branch_count} atividade(s) crítica(s) em ramificações "
                "que também alimentam o término atual."
            )
        st.caption(
            "Criticidade efetiva: considera precedências ativas, gates criados "
            "pelo scope discovery e liberações de recursos que controlam o cronograma."
        )

    if all_items:
        fig = go.Figure()
        ordered = sorted(
            all_items,
            key=lambda item: project_order.get(
                str(item.task_id),
                len(project.tasks),
            ),
        )
        gantt_labels = [
            f"{item.task_id} · {item.task_name}"
            for item in ordered
        ]
        bar_colors = []
        bar_text = []
        hover_reasons = []
        for item in ordered:
            task_id = str(item.task_id)
            is_critical = task_id in criticality.critical_ids
            if item.fixed:
                bar_colors.append("#94A3B8")
            elif is_critical:
                bar_colors.append("#D92D20")
            else:
                bar_colors.append("#0F766E")

            label = item.mode_name + (" · congelada" if item.fixed else "")
            if is_critical:
                label += " · crítica"
            bar_text.append(label)
            hover_reasons.append(
                criticality.reasons.get(task_id, "fora da cadeia controladora")
            )

        fig.add_trace(
            go.Bar(
                y=gantt_labels,
                x=[item.duration for item in ordered],
                base=[item.start for item in ordered],
                orientation="h",
                text=bar_text,
                marker_color=bar_colors,
                customdata=hover_reasons,
                hovertemplate=(
                    "%{y}<br>Início=%{base:.1f}h"
                    "<br>Duração=%{x:.1f}h"
                    "<br>Driver=%{customdata}<extra></extra>"
                ),
            )
        )
        fig.add_vline(
            x=current_time,
            line_dash="dash",
            annotation_text="agora",
        )
        if project.deadline is not None:
            fig.add_vline(
                x=project.deadline,
                line_dash="dot",
                annotation_text="deadline",
            )
        fig.update_yaxes(
            categoryorder="array",
            categoryarray=gantt_labels,
            autorange="reversed",
        )
        fig.update_layout(
            title="Cronograma reprogramado",
            xaxis_title="Horas desde o início da parada",
            yaxis_title="",
            barmode="overlay",
            height=max(450, 32 * len(ordered)),
            margin=dict(l=15, r=15, t=55, b=15),
            paper_bgcolor="white",
            plot_bgcolor="white",
            showlegend=False,
        )
        st.plotly_chart(fig, use_container_width=True)

with tab_activation:
    st.dataframe(
        activation_df,
        use_container_width=True,
        hide_index=True,
    )

    state_counts = activation_df["Estado"].value_counts()
    state_chart = pd.DataFrame(
        {
            "Estado": state_counts.index,
            "Quantidade": state_counts.values,
        }
    )
    st.bar_chart(
        state_chart,
        x="Estado",
        y="Quantidade",
        use_container_width=True,
    )

with tab_schedule:
    st.dataframe(
        schedule_df.drop(columns=["_Ordem"], errors="ignore"),
        use_container_width=True,
        hide_index=True,
    )
    st.caption(
        f"Solver: {result.schedule.strategy} · "
        f"combinações de modos={result.schedule.mode_combinations} · "
        f"avaliações SSGS={result.schedule.evaluated_combinations}."
    )
    if criticality.critical_ids:
        st.caption(
            "Crítica atual = atividade pertencente à cadeia efetiva que controla "
            "o término do cronograma reprogramado; não equivale ao CPM clássico."
        )

with tab_export:
    st.markdown("#### Relatório gerencial do replanejamento")
    st.caption(
        "PDF executivo com baseline, impacto do novo escopo, mapa de ativação "
        "e cronograma reprogramado."
    )

    pdf_bytes = build_conditional_management_pdf(
        project_name=project_name,
        baseline_makespan=float(baseline.makespan),
        current_makespan=float(result.schedule.makespan),
        deadline=(
            None
            if project.deadline is None
            else float(project.deadline)
        ),
        current_time=float(current_time),
        total_cost=float(result.schedule.total_cost),
        new_scope_count=len(new_scope),
        strategy=result.schedule.strategy,
        activation_df=activation_df,
        schedule_df=schedule_df,
        critical_ids=criticality.critical_ids,
        critical_path_label=critical_path_label,
    )

    st.download_button(
        "⬇ Baixar relatório gerencial em PDF",
        data=pdf_bytes,
        file_name=(
            f"{project_name.lower().replace(' ', '_')}"
            "_scope_discovery.pdf"
        ),
        mime="application/pdf",
        type="primary",
        use_container_width=True,
    )

    st.markdown(
        """
        <div class="ta-note">
        O relatório registra a fotografia atual do replanejamento.
        Atividades já iniciadas ou concluídas permanecem congeladas;
        apenas o trabalho futuro é reprogramado.
        </div>
        """,
        unsafe_allow_html=True,
    )
