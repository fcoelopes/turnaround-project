from __future__ import annotations

import io
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from turnaround import (
    ExecutionState,
    TaskExecution,
    apply_scope_config,
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

section(
    "2",
    "Capacidade de recursos",
    "Teste cenários de equipe e recursos compartilhados antes e durante a parada.",
)
cols = st.columns(min(4, max(1, len(project.capacities))))
new_caps: dict[str, float] = {}
for i, (resource, capacity) in enumerate(sorted(project.capacities.items())):
    upper = max(2.0, float(capacity) * 2.5)
    step = 1.0 if float(capacity).is_integer() else 0.5
    with cols[i % len(cols)]:
        new_caps[resource] = st.slider(
            resource,
            min_value=0.0,
            max_value=float(upper),
            value=float(capacity),
            step=step,
            key=f"advanced_cap_{i}",
        )
project = project.model_copy(update={"capacities": new_caps})

empty_state = ExecutionState(current_time=0)
baseline_activation = resolve_activation(project, empty_state)
baseline_tasks = [
    task
    for task in project.tasks
    if task.id in baseline_activation.active_ids
]

try:
    baseline = solve_mrcpsp(
        baseline_tasks,
        project.capacities,
        deadline=project.deadline,
    )
except ValueError as exc:
    st.error(str(exc))
    st.stop()

m1, m2, m3, m4 = st.columns(4)
m1.metric("Makespan base", f"{baseline.makespan:.1f} h")
m2.metric(
    "Deadline",
    "—" if project.deadline is None else f"{project.deadline:.1f} h",
)
m3.metric("Tarefas ativas na base", len(baseline.tasks))
m4.metric("Escopo potencial", len(project.tasks) - len(baseline.tasks))

with st.expander("Modos disponíveis por atividade"):
    mode_rows = []
    for task in project.tasks:
        for mode in task.modes:
            mode_rows.append(
                {
                    "ID": task.id,
                    "Atividade": task.name,
                    "Tipo": task.activation.kind,
                    "Modo": mode.name,
                    "Duração (h)": mode.duration,
                    "Recursos": ", ".join(
                        f"{key}:{value:g}"
                        for key, value in mode.resources.items()
                    ),
                    "Custo": mode.cost,
                }
            )
    st.dataframe(
        pd.DataFrame(mode_rows),
        use_container_width=True,
        hide_index=True,
    )

section(
    "3",
    "Estado da parada e achados",
    "Avance a hora corrente, registre achados e resolva decisões lógicas de escopo.",
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
        event_catalog.setdefault(
            group.when.source_task_id,
            set(),
        ).update(group.when.events)

name_by_id = {task.id: task.name for task in project.tasks}
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

group_selections: dict[str, list[str]] = {}
for group in project.logical_groups:
    labels = {
        task_id: f"{task_id} · {name_by_id.get(task_id, task_id)}"
        for task_id in group.member_task_ids
    }

    if group.operator == "xor":
        chosen = st.selectbox(
            f"Grupo XOR · {group.id}",
            options=[None] + group.member_task_ids,
            format_func=lambda task_id: (
                "— selecionar —"
                if task_id is None
                else labels[task_id]
            ),
        )
        if chosen:
            group_selections[group.id] = [chosen]

    elif group.operator == "or":
        chosen = st.multiselect(
            f"Grupo OR · {group.id}",
            options=group.member_task_ids,
            format_func=lambda task_id: labels[task_id],
        )
        if chosen:
            group_selections[group.id] = chosen

state = ExecutionState(
    current_time=current_time,
    events=events,
    selected_optional_ids=selected_optional_ids,
    group_selections=group_selections,
    executions=executions,
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
            "Há decisão lógica pendente nos grupos: "
            + ", ".join(pending_groups)
            + ". O cronograma não inclui os ramos ainda não escolhidos."
        ),
        tone="warn",
        title="Decisão de escopo pendente.",
    )

try:
    result = reschedule_from_state(project, state)
except ValueError as exc:
    st.error(str(exc))
    st.stop()

active_now = result.activation.active_ids
new_scope = active_now - baseline_activation.active_ids

activation_df = pd.DataFrame(
    [
        {
            "ID": task.id,
            "Atividade": task.name,
            "Tipo": task.activation.kind,
            "Estado": result.activation.states[task.id].value,
            "Motivo": result.activation.reasons[task.id],
        }
        for task in project.tasks
    ]
)

all_items = result.frozen_tasks + result.schedule.tasks
schedule_df = pd.DataFrame(
    [
        {
            "ID": item.task_id,
            "Atividade": item.task_name,
            "Modo": item.mode_name,
            "Início (h)": item.start,
            "Fim (h)": item.finish,
            "Duração (h)": item.duration,
            "Congelada": item.fixed,
            "Recursos": ", ".join(
                f"{key}:{value:g}"
                for key, value in item.resources.items()
            ),
        }
        for item in sorted(
            all_items,
            key=lambda scheduled: (
                scheduled.start,
                scheduled.finish,
            ),
        )
    ]
)

section(
    "4",
    "Impacto do scope discovery",
    "Compare baseline, novo escopo, atraso e custo de modos em uma leitura gerencial.",
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

    if all_items:
        fig = go.Figure()
        ordered = sorted(
            all_items,
            key=lambda item: (item.start, item.finish),
            reverse=True,
        )
        fig.add_trace(
            go.Bar(
                y=[
                    f"{item.task_id} · {item.task_name}"
                    for item in ordered
                ],
                x=[item.duration for item in ordered],
                base=[item.start for item in ordered],
                orientation="h",
                text=[
                    item.mode_name
                    + (" · congelada" if item.fixed else "")
                    for item in ordered
                ],
                hovertemplate=(
                    "%{y}<br>Início=%{base:.1f}h"
                    "<br>Duração=%{x:.1f}h<extra></extra>"
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
        schedule_df,
        use_container_width=True,
        hide_index=True,
    )
    st.caption(
        f"Solver: {result.schedule.strategy} · "
        f"combinações de modos={result.schedule.mode_combinations} · "
        f"avaliações SSGS={result.schedule.evaluated_combinations}."
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
