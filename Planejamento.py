from __future__ import annotations

import hashlib
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.express as px
import streamlit as st

from turnaround.analysis import (
    compare_baseline,
    criticality_dataframe,
    resource_profile,
    schedule_dataframe,
    tasks_dataframe,
)
from turnaround import (
    DailyShift,
    ExecutionStore,
    PlanningScopeRisk,
    WorkingCalendar,
    build_planning_baseline,
    extract_scope_risk_candidates,
    materialize_planning_scope,
    upgrade_database,
    validate_planning_baseline_for_execution,
)
from turnaround.io import load_schedule
from turnaround.rcpsp import infer_capacities, optimize_turnaround
from turnaround.planning_status import classify_planning_status
from turnaround.report import build_base_management_pdf
from turnaround.resource_governance import (
    inferred_capacity_resources,
    resolve_capacity_origins,
)
from turnaround.risk import simulate_deadline_risk
from turnaround.ui import app_header, apply_app_style, section, status, workflow_strip


st.set_page_config(
    page_title="Turnaround Decision Support",
    page_icon="🛠️",
    layout="wide",
    initial_sidebar_state="expanded",
)


@st.cache_resource
def _get_execution_store() -> ExecutionStore:
    upgrade_database()
    return ExecutionStore()


execution_store = _get_execution_store()
apply_app_style()
app_header(
    "TURNAROUND DECISION SUPPORT",
    "Planejamento, capacidade, criticidade e risco de prazo.",
    badge="T/A",
    context="RCPSP · recursos · janela",
)
workflow_strip(
    [
        ("Plano", "Importar", "Project / Excel / CSV"),
        ("Capacidade", "Recursos", "Equipes e compartilhados"),
        ("Sequência", "RCPSP", "Cronograma factível"),
        ("Risco", "Prazo", "P80 e janela"),
        ("Decisão", "Comunicar", "Plano e relatório"),
    ]
)

with st.sidebar:
    st.markdown("### Premissas globais")
    hours_per_day = st.number_input(
        "Horas consideradas por dia para conversão de prazo",
        min_value=1,
        max_value=24,
        value=8,
        step=1,
        help=(
            "Sem calendário real, converte horas de trabalho em dias para os "
            "indicadores e para a janela. Quando um calendário de recurso é "
            "ativado, os indicadores passam a usar dias corridos de 24 h."
        ),
    )
    deadline_days = st.number_input(
        "Janela-alvo (dias)",
        min_value=0.0,
        value=0.0,
        step=0.5,
        help=(
            "0 = sem deadline. Sem calendário real, usa o fator horas/dia acima; "
            "com calendário de recurso, a janela passa a representar dias corridos de 24 h."
        ),
    )
    st.caption(
        "Capacidades de recursos e premissas de risco são configuradas "
        "no corpo da página, no contexto correspondente."
    )

section(
    "1",
    "Carregar planejamento",
    "XML do Microsoft Project é o formato recomendado; Excel e CSV continuam disponíveis.",
)
uploaded = st.file_uploader(
    "Cronograma da parada",
    type=["xml", "xlsx", "xls", "csv"],
    help=(
        "Recomendado: XML do Microsoft Project. Também aceita Excel/CSV "
        "com ID, Nome, Duração, Predecessoras e Recursos."
    ),
)

with st.expander("Formato esperado para Excel/CSV"):
    st.markdown(
        """
        Colunas mínimas: **ID**, **Nome** e **Duracao_h**.

        Recomendadas:
        - **Predecessoras**: 10FS;12SS+4h
        - **Recursos**: Mecânica;Guindaste
        - **Demandas**: Mecânica:3;Guindaste:1
        - **WBS/EDT**, **Início**, **Término** (opcionais)

        Para XML, a aplicação lê tarefas, vínculos, recursos e assignments do Project.
        """
    )

if not uploaded:
    st.info(
        "Carregue um cronograma para iniciar. "
        "O repositório também possui arquivos demonstrativos em sample_data/."
    )
    st.stop()

project_name = Path(uploaded.name).stem.replace("_", " ").strip() or "Turnaround"

try:
    tasks, xml_caps = load_schedule(
        uploaded,
        hours_per_day=int(hours_per_day),
    )
except Exception as exc:
    st.error(f"Falha ao ler o cronograma: {exc}")
    st.stop()

st.caption(f"{project_name} · {len(tasks)} atividades carregadas")

try:
    imported_scope_risks = extract_scope_risk_candidates(uploaded)
except Exception as exc:
    imported_scope_risks = []
    st.warning(
        "Metadados opcionais de risco de escopo não puderam ser lidos: "
        f"{exc}"
    )

with st.expander("Atividades normalizadas", expanded=False):
    st.dataframe(
        tasks_dataframe(tasks),
        use_container_width=True,
        hide_index=True,
    )

base_caps = infer_capacities(tasks)
for resource, quantity in xml_caps.items():
    base_caps[resource] = max(base_caps.get(resource, 0), quantity)

resources = sorted(base_caps)
source_key = hashlib.sha256(uploaded.getvalue()).hexdigest()[:12]
task_by_id = {str(task.id): task for task in tasks}
task_label_by_id = {
    str(task.id): f"{task.id} · {task.name}"
    for task in tasks
}
task_id_by_label = {
    label: task_id
    for task_id, label in task_label_by_id.items()
}
imported_scope_by_task = {
    item.task_id: item
    for item in imported_scope_risks
    if item.task_id in task_by_id
}

baseline_start_values = [
    task.baseline_start
    for task in tasks
    if task.baseline_start
]
parsed_calendar_starts = pd.to_datetime(
    pd.Series(baseline_start_values, dtype="object"),
    errors="coerce",
).dropna()
planning_calendar_origin = (
    parsed_calendar_starts.min().to_pydatetime()
    if not parsed_calendar_starts.empty
    else None
)
calendar_origin_hour = (
    float(
        planning_calendar_origin.hour
        + planning_calendar_origin.minute / 60
        + planning_calendar_origin.second / 3600
    )
    if planning_calendar_origin is not None
    else 0.0
)

section(
    "2",
    "Configurar cenário",
    "Capacidade e calendário em Recursos; incertezas de duração e escopo em Risco.",
)
config_resources, config_calendar, config_risk = st.tabs(
    ["Recursos", "Calendários", "Risco"]
)

base_capacity_origin = {
    resource: ("PROJECT" if resource in xml_caps else "INFERIDA")
    for resource in resources
}
capacities: dict[str, int] = {}
capacity_origins: dict[str, str] = {}
inferred_resources: list[str] = []
capacity_validation_ok = True
with config_resources:
    st.markdown("#### Capacidade do cenário")
    if not resources:
        st.info(
            "O arquivo não possui recursos atribuídos. "
            "O modelo calcula precedências sem restrição de capacidade."
        )
    else:
        st.caption(
            "Edite a capacidade do cenário aqui. Capacidade-base e origem "
            "são referências de leitura; a validação formal das capacidades "
            "será tratada na etapa de governança."
        )
        capacity_input_df = pd.DataFrame(
            [
                {
                    "Recurso": resource,
                    "Capacidade-base": int(base_caps.get(resource, 0)),
                    "Origem-base": base_capacity_origin[resource],
                    "Capacidade cenário": int(base_caps.get(resource, 0)),
                }
                for resource in resources
            ]
        )
        edited_capacity_df = st.data_editor(
            capacity_input_df,
            use_container_width=True,
            hide_index=True,
            num_rows="fixed",
            key=f"resource_capacity_editor_{source_key}",
            disabled=["Recurso", "Capacidade-base", "Origem-base"],
            column_config={
                "Recurso": st.column_config.TextColumn("Recurso"),
                "Capacidade-base": st.column_config.NumberColumn(
                    "Capacidade-base",
                    min_value=0,
                    step=1,
                ),
                "Origem-base": st.column_config.TextColumn(
                    "Origem-base",
                    help=(
                        "Origem da referência inicial. A tabela de governança abaixo "
                        "mostra a origem efetiva do cenário após qualquer edição."
                    ),
                ),
                "Capacidade cenário": st.column_config.NumberColumn(
                    "Capacidade cenário",
                    min_value=1,
                    step=1,
                    required=True,
                    help="Capacidade que será usada no cenário RCPSP.",
                ),
            },
        )
        capacities = {
            str(row["Recurso"]): int(row["Capacidade cenário"])
            for _, row in edited_capacity_df.iterrows()
        }

        capacity_origins = resolve_capacity_origins(
            base_capacities=base_caps,
            scenario_capacities=capacities,
            project_resources=set(xml_caps),
        )
        inferred_resources = inferred_capacity_resources(capacity_origins)

        governance_df = pd.DataFrame(
            [
                {
                    "Recurso": resource,
                    "Capacidade cenário": capacities[resource],
                    "Origem": capacity_origins[resource],
                    "Validação": (
                        "requer confirmação"
                        if capacity_origins[resource] == "INFERIDA"
                        else "validada pela origem"
                    ),
                }
                for resource in resources
            ]
        )
        st.markdown("##### Governança das capacidades")
        st.dataframe(
            governance_df,
            use_container_width=True,
            hide_index=True,
        )

        if inferred_resources:
            status(
                (
                    "As capacidades a seguir foram inferidas a partir das demandas "
                    "das atividades e ainda não representam disponibilidade operacional "
                    "confirmada: "
                    + ", ".join(inferred_resources)
                    + "."
                ),
                tone="warn",
                title="Capacidades inferidas exigem validação.",
            )
            confirm_inferred_capacities = st.checkbox(
                "Confirmo que revisei e aceito as capacidades inferidas deste cenário",
                key=f"confirm_inferred_capacities_{source_key}",
                help=(
                    "Esta confirmação é obrigatória para aprovar o baseline. "
                    "Você também pode editar a capacidade; nesse caso a origem passa "
                    "a ser INFORMADA."
                ),
            )
        else:
            confirm_inferred_capacities = True

        capacity_validation_ok = bool(confirm_inferred_capacities)

resource_calendars: dict[str, WorkingCalendar] = {}
calendar_config_errors: list[str] = []
with config_calendar:
    st.markdown("#### Turno recorrente por recurso")
    st.caption(
        "Por padrão, recursos permanecem 24 h disponíveis. Desative 24 h somente "
        "quando quiser que o RCPSP respeite um turno real recorrente. Nesta etapa "
        "o Planejamento configura um turno por recurso; indisponibilidades e overtime "
        "continuam disponíveis no domínio avançado e ganharão entrada própria depois."
    )
    if planning_calendar_origin is not None:
        st.caption(
            "H+0 corresponde a "
            f"{planning_calendar_origin.strftime('%d/%m/%Y %H:%M')}; "
            "os horários abaixo são horas do relógio e serão alinhados a essa origem."
        )
    else:
        st.warning(
            "O arquivo não traz uma data/hora inicial utilizável. Para calendários "
            "recorrentes, H+0 será tratado como 00:00."
        )

    calendar_input_df = pd.DataFrame(
        [
            {
                "Recurso": resource,
                "24 h": True,
                "Início turno": 7,
                "Fim turno": 19,
            }
            for resource in resources
        ]
    )
    if calendar_input_df.empty:
        st.info("Não há recursos para configurar calendário.")
        edited_calendar_df = calendar_input_df
    else:
        edited_calendar_df = st.data_editor(
            calendar_input_df,
            use_container_width=True,
            hide_index=True,
            num_rows="fixed",
            key=f"resource_calendar_editor_{source_key}",
            disabled=["Recurso"],
            column_config={
                "Recurso": st.column_config.TextColumn("Recurso"),
                "24 h": st.column_config.CheckboxColumn(
                    "24 h",
                    help="Marcado = recurso disponível continuamente.",
                ),
                "Início turno": st.column_config.NumberColumn(
                    "Início turno",
                    min_value=0,
                    max_value=23,
                    step=1,
                    format="%d:00",
                ),
                "Fim turno": st.column_config.NumberColumn(
                    "Fim turno",
                    min_value=1,
                    max_value=24,
                    step=1,
                    format="%d:00",
                ),
            },
        )

    for _, row in edited_calendar_df.iterrows():
        resource = str(row["Recurso"])
        if bool(row["24 h"]):
            continue
        try:
            shift = DailyShift(
                float(row["Início turno"]),
                float(row["Fim turno"]),
            )
            resource_calendars[resource] = WorkingCalendar(
                name=resource,
                shifts=(shift,),
                origin_hour=calendar_origin_hour,
            )
        except ValueError as exc:
            calendar_config_errors.append(f"{resource}: {exc}")

    if (
        resource_calendars
        and abs(calendar_origin_hour - round(calendar_origin_hour)) > 1e-9
    ):
        calendar_config_errors.append(
            "O RCPSP de Planejamento usa grade horária inteira nesta etapa, "
            "mas H+0 possui minutos diferentes de 00. Ajuste a origem ou mantenha "
            "os recursos em 24 h até suportarmos resolução sub-horária."
        )

    for message in calendar_config_errors:
        st.error(message)

calendar_mode_active = bool(resource_calendars)
timeline_hours_per_day = 24 if calendar_mode_active else int(hours_per_day)
deadline_h = (
    int(round(deadline_days * timeline_hours_per_day))
    if deadline_days > 0
    else None
)

with config_risk:
    if calendar_mode_active:
        status(
            (
                "O RCPSP determinístico já usa os calendários configurados. "
                "O Monte Carlo ainda não: para não misturar bases temporais, "
                "P50/P80/P90 ficam indisponíveis neste cenário até a integração "
                "calendar-aware da próxima etapa."
            ),
            tone="warn",
            title="Risco probabilístico temporariamente não comparável.",
        )
    st.markdown("#### Incerteza de duração")
    st.caption(
        "Configure a distribuição triangular usada em cada iteração do Monte Carlo. "
        "Os percentuais são variações sobre a duração-base."
    )
    risk_control_cols = st.columns(4)
    with risk_control_cols[0]:
        simulations = st.slider(
            "Simulações Monte Carlo",
            50,
            1000,
            300,
            50,
            key=f"simulations_{source_key}",
        )
    with risk_control_cols[1]:
        optimistic_pct = st.slider(
            "Otimista (%)",
            -50,
            0,
            -10,
            5,
            key=f"optimistic_{source_key}",
        )
    with risk_control_cols[2]:
        most_likely_pct = st.slider(
            "Mais provável (%)",
            -30,
            50,
            0,
            5,
            key=f"most_likely_{source_key}",
        )
    with risk_control_cols[3]:
        pessimistic_pct = st.slider(
            "Pessimista (%)",
            0,
            100,
            30,
            5,
            key=f"pessimistic_{source_key}",
        )

    duration_config_error = not (
        optimistic_pct <= most_likely_pct <= pessimistic_pct
    )
    if duration_config_error:
        st.error(
            "A distribuição triangular deve respeitar: "
            "otimista ≤ mais provável ≤ pessimista."
        )
    else:
        st.caption(
            f"Triangular: {100 + optimistic_pct:.0f}% / "
            f"{100 + most_likely_pct:.0f}% / {100 + pessimistic_pct:.0f}% "
            "da duração-base."
        )
    st.divider()
    st.markdown("#### Ampliação probabilística de escopo")
    st.caption(
        "Use esta seção para trabalhos que ainda não pertencem ao escopo-base, "
        "mas podem entrar se um evento ocorrer. A probabilidade é usada somente "
        "no planejamento Monte Carlo; na execução, o evento observado continua "
        "sendo lançado em Escopo e Replanejamento. Eventos distintos são tratados "
        "como independentes nesta versão; o mesmo gatilho+evento é sorteado uma única vez."
    )

    default_potential_ids = list(imported_scope_by_task)
    selected_scope_ids = st.multiselect(
        "Atividades de escopo potencial",
        options=list(task_label_by_id),
        default=[
            task_id
            for task_id in default_potential_ids
            if task_id in task_label_by_id
        ],
        format_func=lambda task_id: task_label_by_id[task_id],
        key=f"planning_scope_ids_{source_key}",
        help=(
            "Essas atividades ficam fora do plano-base determinístico e entram "
            "nas simulações conforme a probabilidade do evento."
        ),
    )

    scope_rows = []
    for task_id in selected_scope_ids:
        imported = imported_scope_by_task.get(task_id)
        trigger_id = imported.trigger_task_id if imported else ""
        scope_rows.append(
            {
                "ID": task_id,
                "Atividade": task_by_id[task_id].name,
                "Gatilho": (
                    task_label_by_id.get(trigger_id, "")
                    if trigger_id
                    else ""
                ),
                "Evento": imported.event_name if imported else "",
                "Probabilidade_%": (
                    round(float(imported.probability) * 100, 2)
                    if imported
                    else 0.0
                ),
            }
        )

    scope_risk_df = pd.DataFrame(
        scope_rows,
        columns=[
            "ID",
            "Atividade",
            "Gatilho",
            "Evento",
            "Probabilidade_%",
        ],
    )
    edited_scope_risk_df = st.data_editor(
        scope_risk_df,
        use_container_width=True,
        hide_index=True,
        num_rows="fixed",
        key=f"planning_scope_editor_{source_key}",
        disabled=["ID", "Atividade"],
        column_config={
            "ID": st.column_config.TextColumn("ID"),
            "Atividade": st.column_config.TextColumn("Atividade"),
            "Gatilho": st.column_config.SelectboxColumn(
                "Gatilho",
                options=["", *task_label_by_id.values()],
                help="Atividade cuja inspeção/checagem pode revelar o novo escopo.",
            ),
            "Evento": st.column_config.TextColumn(
                "Evento",
                help="Ex.: bearing_damage, nozzle_crack, calibration_failed.",
            ),
            "Probabilidade_%": st.column_config.NumberColumn(
                "Probabilidade_%",
                min_value=0.0,
                max_value=100.0,
                step=1.0,
                format="%.1f",
            ),
        },
    )

scope_risks: list[PlanningScopeRisk] = []
scope_config_errors: list[str] = []
for _, row in edited_scope_risk_df.iterrows():
    task_id = str(row["ID"]).strip()
    trigger_label = str(row["Gatilho"]).strip()
    event_name = str(row["Evento"]).strip()
    probability_pct = float(row["Probabilidade_%"] or 0.0)

    trigger_id = task_id_by_label.get(trigger_label, "")
    if not trigger_id:
        scope_config_errors.append(
            f"{task_label_by_id.get(task_id, task_id)}: informe o gatilho."
        )
        continue
    if not event_name:
        scope_config_errors.append(
            f"{task_label_by_id.get(task_id, task_id)}: informe o evento."
        )
        continue

    try:
        scope_risks.append(
            PlanningScopeRisk(
                task_id=task_id,
                trigger_task_id=trigger_id,
                event_name=event_name,
                probability=probability_pct / 100.0,
            )
        )
    except ValueError as exc:
        scope_config_errors.append(str(exc))

event_probability_by_key: dict[tuple[str, str], float] = {}
potential_task_ids = {item.task_id for item in scope_risks}
for item in scope_risks:
    if item.trigger_task_id in potential_task_ids:
        scope_config_errors.append(
            f"{task_label_by_id.get(item.task_id, item.task_id)}: "
            "o gatilho deve pertencer ao escopo-base, não ao escopo potencial."
        )
    previous_probability = event_probability_by_key.get(item.event_key)
    if (
        previous_probability is not None
        and abs(previous_probability - item.probability) > 1e-9
    ):
        scope_config_errors.append(
            f"Evento {item.event_name} no gatilho "
            f"{task_label_by_id.get(item.trigger_task_id, item.trigger_task_id)} "
            "aparece com probabilidades diferentes."
        )
    event_probability_by_key[item.event_key] = item.probability

if scope_config_errors:
    with config_risk:
        for message in dict.fromkeys(scope_config_errors):
            st.error(message)

base_tasks = materialize_planning_scope(
    tasks,
    scope_risks,
    active_scope_task_ids=set(),
)

planning_signature = hashlib.sha256(
    json.dumps(
        {
            "source_sha256": hashlib.sha256(
                uploaded.getvalue()
            ).hexdigest(),
            "import_hours_per_day": int(hours_per_day),
            "timeline_hours_per_day": int(timeline_hours_per_day),
            "deadline_h": deadline_h,
            "capacities": capacities,
            "capacity_origins": capacity_origins,
            "resource_calendars": {
                resource: {
                    "origin_hour": float(calendar.origin_hour),
                    "shifts": [
                        [float(shift.start_hour), float(shift.end_hour)]
                        for shift in calendar.shifts
                    ],
                    "blocks": [
                        [float(block.start_h), float(block.end_h), block.reason]
                        for block in calendar.blocks
                    ],
                    "overtime_windows": [
                        [float(window.start_h), float(window.end_h), window.reason]
                        for window in calendar.overtime_windows
                    ],
                }
                for resource, calendar in sorted(resource_calendars.items())
            },
            "simulations": int(simulations),
            "optimistic_pct": int(optimistic_pct),
            "most_likely_pct": int(most_likely_pct),
            "pessimistic_pct": int(pessimistic_pct),
            "scope_risks": [
                item.model_dump(mode="json")
                for item in scope_risks
            ],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
).hexdigest()

run = st.button(
    "▶ Gerar cenário factível",
    type="primary",
    use_container_width=True,
    disabled=(
        bool(scope_config_errors)
        or duration_config_error
        or bool(calendar_config_errors)
    ),
)

cached_result = st.session_state.get("planning_result")
scenario_changed = bool(
    cached_result
    and cached_result.get("signature") != planning_signature
)
if scenario_changed and not run:
    st.info(
        "As premissas do cenário foram alteradas. "
        "O resultado anterior foi invalidado; gere o cenário novamente."
    )

if run:
    try:
        best, candidates = optimize_turnaround(
            base_tasks,
            capacities,
            deadline_h=deadline_h,
            resource_calendars=resource_calendars,
        )
        comparison = compare_baseline(base_tasks, best)
    except Exception as exc:
        st.session_state.pop("planning_result", None)
        st.error(f"Não foi possível gerar um cronograma factível: {exc}")
        st.stop()

    risk_error = None
    if calendar_mode_active:
        risk = None
        risk_error = (
            "Monte Carlo não executado: este cenário usa calendário real de recurso "
            "e a integração probabilística calendar-aware ainda não foi concluída."
        )
    else:
        with st.spinner("Simulando duração + ampliação probabilística de escopo..."):
            try:
                risk = simulate_deadline_risk(
                    tasks,
                    capacities,
                    priority_rule=best.priority_rule,
                    deadline_h=deadline_h,
                    n=int(simulations),
                    optimistic_factor=1 + optimistic_pct / 100,
                    most_likely_factor=1 + most_likely_pct / 100,
                    pessimistic_factor=1 + pessimistic_pct / 100,
                    scope_risks=scope_risks,
                )
            except Exception as exc:
                risk = None
                risk_error = str(exc)

    cached_result = {
        "signature": planning_signature,
        "best": best,
        "candidates": candidates,
        "comparison": comparison,
        "risk": risk,
        "risk_error": risk_error,
        "calculated_at": datetime.now(timezone.utc).isoformat(timespec="minutes"),
    }
    st.session_state["planning_result"] = cached_result
elif (
    not cached_result
    or cached_result.get("signature") != planning_signature
):
    st.stop()

best = cached_result["best"]
candidates = cached_result["candidates"]
comparison = cached_result["comparison"]
risk = cached_result["risk"]
risk_error = cached_result.get("risk_error")
if risk_error:
    with config_risk:
        st.warning(
            f"A simulação de risco não pôde ser concluída: {risk_error}"
        )

schedule_df = schedule_dataframe(best, int(timeline_hours_per_day))
crit_df = criticality_dataframe(base_tasks)

# Consolida RCPSP e CPM na mesma visão. O cronograma factível continua vindo
# do solver; ES/EF/LS/LF/folga são métricas da rede de precedências.
schedule_df["ID"] = schedule_df["ID"].astype(str)
cpm_view_df = crit_df[
    ["ID", "ES", "EF", "LS", "LF", "Float_h", "Critical"]
].copy()
cpm_view_df["ID"] = cpm_view_df["ID"].astype(str)
schedule_df = schedule_df.merge(
    cpm_view_df,
    on="ID",
    how="left",
    validate="one_to_one",
)
schedule_df = schedule_df.rename(
    columns={
        "Float_h": "Folga_h",
        "Critical": "Crítica_CPM",
    }
)

# O solver pode devolver as atividades em ordem de execução. Para leitura gerencial,
# preservamos a ordem estrutural do cronograma importado (Project/Excel/CSV).
project_order = {str(task.id): index for index, task in enumerate(tasks)}
schedule_df["_Ordem"] = (
    schedule_df["ID"]
    .map(project_order)
    .fillna(len(project_order))
    .astype(int)
)
schedule_df = schedule_df.sort_values(
    ["_Ordem", "Inicio_h", "Fim_h"],
    kind="stable",
).reset_index(drop=True)

critical_ids = set(
    schedule_df.loc[
        schedule_df["Crítica_CPM"].fillna(False),
        "ID",
    ].astype(str)
)
schedule_df["Criticidade"] = schedule_df["Crítica_CPM"].fillna(False).map(
    lambda is_critical: (
        "Caminho crítico CPM"
        if bool(is_critical)
        else "Não crítica"
    )
)
schedule_df["Rótulo"] = schedule_df.apply(
    lambda row: f"{row['ID']} · {row['Atividade']}",
    axis=1,
)
gantt_labels = schedule_df["Rótulo"].tolist()

# Quando o arquivo traz datas de baseline, usamos a primeira data como âncora
# de calendário para o cronograma factível produzido pelo solver. A lógica do
# RCPSP continua em horas; apenas a visualização passa a mostrar data/hora real.
calendar_anchor = (
    pd.Timestamp(planning_calendar_origin).tz_localize(None)
    if planning_calendar_origin is not None
    and pd.Timestamp(planning_calendar_origin).tzinfo is not None
    else planning_calendar_origin
)

use_calendar_axis = calendar_anchor is not None
if use_calendar_axis:
    schedule_df["Início calendário"] = (
        calendar_anchor
        + pd.to_timedelta(schedule_df["Inicio_h"], unit="h")
    )
    schedule_df["Término calendário"] = (
        calendar_anchor
        + pd.to_timedelta(schedule_df["Fim_h"], unit="h")
    )
    gantt_x_start = "Início calendário"
    gantt_x_end = "Término calendário"
    gantt_xaxis_title = "Data / hora"
else:
    gantt_x_start = "Inicio_h"
    gantt_x_end = "Fim_h"
    gantt_xaxis_title = "Horas desde o início da parada"

schedule_view_columns = [
    "ID",
    "Atividade",
    "WBS",
    "Inicio_h",
    "Fim_h",
    "Duracao_h",
    "Inicio_dia",
    "Fim_dia",
    "ES",
    "EF",
    "LS",
    "LF",
    "Folga_h",
    "Criticidade",
    "Recursos",
]
if use_calendar_axis:
    schedule_view_columns.extend(
        ["Início calendário", "Término calendário"]
    )

schedule_view_df = schedule_df[
    [
        column
        for column in schedule_view_columns
        if column in schedule_df.columns
    ]
].copy()

if resources:
    util_df = pd.DataFrame(
        [
            {
                "Recurso": resource,
                "Capacidade-base": int(base_caps.get(resource, 0)),
                "Origem": capacity_origins[resource],
                "Capacidade cenário": capacities[resource],
                "Capacidade": capacities[resource],
                "Pico": best.resource_peak.get(resource, 0),
                "Utilizacao_%": round(
                    best.resource_utilization.get(resource, 0) * 100,
                    1,
                ),
            }
            for resource in resources
        ]
    ).sort_values("Utilizacao_%", ascending=False)
else:
    util_df = pd.DataFrame()

with config_resources:
    if resources:
        st.divider()
        st.markdown("#### Resultado do cenário")
        st.caption(
            "Capacidade, pico e utilização permanecem no mesmo contexto "
            "em que o cenário de recursos foi configurado."
        )
        st.dataframe(
            util_df[
                [
                    "Recurso",
                    "Capacidade-base",
                    "Origem",
                    "Capacidade cenário",
                    "Pico",
                    "Utilizacao_%",
                ]
            ],
            use_container_width=True,
            hide_index=True,
            column_config={
                "Utilizacao_%": st.column_config.NumberColumn(
                    "Utilização (%)",
                    format="%.1f",
                ),
            },
        )

        profile = resource_profile(best)
        if not profile.empty:
            fig2 = px.area(
                profile,
                x="Hora",
                y="Uso",
                color="Recurso",
                title="Perfil de utilização de recursos",
            )
            fig2.update_layout(
                margin=dict(l=15, r=15, t=55, b=15),
                paper_bgcolor="white",
                plot_bgcolor="white",
                legend_title_text="",
            )
            st.plotly_chart(fig2, use_container_width=True)

        if not util_df.empty:
            bottleneck = util_df.iloc[0]
            status(
                (
                    f"{bottleneck['Recurso']} apresenta a maior utilização média "
                    f"({float(bottleneck['Utilizacao_%']):.1f}%)."
                ),
                tone="warn",
                title="Recurso mais pressionado.",
            )

with config_risk:
    st.divider()
    st.markdown("#### Resultado da simulação")

    if not risk:
        st.info("A simulação de risco não está disponível para este cenário.")
    else:
        risk_cols = st.columns(5)
        risk_cols[0].metric(
            "Média",
            f"{risk['mean_h'] / timeline_hours_per_day:.2f} d",
            help="Média das durações finais observadas nas simulações.",
        )
        risk_cols[1].metric(
            "P50",
            f"{risk['p50_h'] / timeline_hours_per_day:.2f} d",
            help="50% das simulações terminaram até este prazo.",
        )
        risk_cols[2].metric(
            "P80",
            f"{risk['p80_h'] / timeline_hours_per_day:.2f} d",
            help="80% das simulações terminaram até este prazo.",
        )
        risk_cols[3].metric(
            "P90",
            f"{risk['p90_h'] / timeline_hours_per_day:.2f} d",
            help="90% das simulações terminaram até este prazo.",
        )
        risk_cols[4].metric(
            "P(cumprir janela)",
            (
                "—"
                if risk.get("probability_meet_deadline") is None
                else f"{risk['probability_meet_deadline'] * 100:.1f}%"
            ),
            help=(
                "Percentual de simulações cujo makespan ficou dentro "
                "da janela configurada."
            ),
        )

        hist_df = pd.DataFrame(
            {
                "Makespan_dias": [
                    value / timeline_hours_per_day
                    for value in risk["samples"]
                ]
            }
        )
        fig3 = px.histogram(
            hist_df,
            x="Makespan_dias",
            nbins=30,
            title="Distribuição simulada da duração da parada",
        )
        fig3.update_layout(
            xaxis_title="Duração da parada (dias)",
            yaxis_title="Frequência",
            margin=dict(l=15, r=15, t=55, b=15),
            paper_bgcolor="white",
            plot_bgcolor="white",
        )
        st.plotly_chart(fig3, use_container_width=True)

        if risk.get("scope_enabled"):
            st.markdown("#### Decomposição duração × escopo")
            decomposition_cols = st.columns(4)
            decomposition_cols[0].metric(
                "P80 somente duração",
                f"{risk['duration_only_p80_h'] / timeline_hours_per_day:.2f} d",
            )
            decomposition_cols[1].metric(
                "P80 combinado",
                f"{risk['p80_h'] / timeline_hours_per_day:.2f} d",
            )
            decomposition_cols[2].metric(
                "Impacto médio do escopo",
                f"{risk['mean_scope_impact_h']:.1f} h",
            )
            decomposition_cols[3].metric(
                "P80 impacto do escopo",
                f"{risk['p80_scope_impact_h']:.1f} h",
            )
            st.caption(
                f"Escopo adicional apareceu em "
                f"{risk['probability_any_scope_simulated'] * 100:.1f}% das simulações."
            )

            scope_event_rows = []
            for event in risk.get("scope_events", []):
                scope_event_rows.append(
                    {
                        "Gatilho": task_label_by_id.get(
                            event["trigger_task_id"],
                            event["trigger_task_id"],
                        ),
                        "Evento": event["event_name"],
                        "Atividades ativadas": ", ".join(
                            task_label_by_id.get(task_id, task_id)
                            for task_id in event["task_ids"]
                        ),
                        "Probabilidade": (
                            f"{event['probability_configured'] * 100:.1f}%"
                        ),
                        "Freq. simulada": (
                            f"{event['frequency_simulated'] * 100:.1f}%"
                        ),
                        "Impacto marginal médio (h)": (
                            None
                            if event["marginal_impact_h"] is None
                            else round(event["marginal_impact_h"], 1)
                        ),
                    }
                )

            if scope_event_rows:
                scope_driver_df = pd.DataFrame(scope_event_rows).sort_values(
                    "Impacto marginal médio (h)",
                    ascending=False,
                    na_position="last",
                )
                st.markdown("##### Direcionadores de risco de escopo")
                st.caption(
                    "Ordenação pelo impacto marginal médio observado nas simulações; "
                    "serve como apoio à análise, não como decisão técnica automática."
                )
                st.dataframe(
                    scope_driver_df,
                    use_container_width=True,
                    hide_index=True,
                )

        if deadline_h and risk.get("probability_meet_deadline") is not None:
            st.caption(
                "A classificação gerencial de prazo combina P80 e "
                "P(cumprir janela); nenhum desses sinais é interpretado isoladamente."
            )

executive_status = classify_planning_status(
    makespan_h=float(best.makespan_h),
    deadline_h=None if deadline_h is None else float(deadline_h),
    p80_h=None if not risk else float(risk["p80_h"]),
    probability_meet_deadline=(
        None
        if not risk or risk.get("probability_meet_deadline") is None
        else float(risk["probability_meet_deadline"])
    ),
)

cand_df = pd.DataFrame(
    [
        {
            "Regra": candidate.priority_rule,
            "Makespan_h": candidate.makespan_h,
            "Makespan_dias": round(
                candidate.makespan_h / timeline_hours_per_day,
                2,
            ),
            "Atraso_h": candidate.tardiness_h,
        }
        for candidate in candidates
    ]
).sort_values(["Atraso_h", "Makespan_h"])

section(
    "3",
    "Leitura para decisão",
    "Prazo, restrição de recursos e risco primeiro; detalhes técnicos ficam recolhidos.",
)

tab_exec, tab_schedule, tab_export = st.tabs(
    [
        "Decisão",
        "Cronograma",
        "Relatório",
    ]
)

with tab_exec:
    metric_cols = st.columns(4)
    metric_cols[0].metric(
        "Makespan base" if scope_risks else "Makespan",
        f"{best.makespan_h / timeline_hours_per_day:.2f} d",
        help=(
            "Cronograma determinístico do escopo-base; atividades potenciais "
            "entram apenas na análise probabilística."
            if scope_risks
            else None
        ),
    )
    metric_cols[1].metric(
        (
            "Penalidade recursos/calendário"
            if calendar_mode_active
            else "Penalidade de recursos"
        ),
        f"{comparison['resource_penalty_h']:.1f} h",
    )
    metric_cols[2].metric(
        "P80",
        "—" if not risk else f"{risk['p80_h'] / timeline_hours_per_day:.2f} d",
    )
    metric_cols[3].metric(
        "P(cumprir janela)",
        (
            "—"
            if not risk or risk.get("probability_meet_deadline") is None
            else f"{risk['probability_meet_deadline'] * 100:.1f}%"
        ),
    )

    st.markdown("#### Status executivo")
    status_cols = st.columns(4)
    status_cols[0].metric(
        "Determinístico",
        executive_status.deterministic,
        help="Indica se o cronograma-base cabe na janela-alvo.",
    )
    status_cols[1].metric(
        "P80",
        executive_status.p80,
        help="Indica se o percentil P80 do prazo cabe na janela-alvo.",
    )
    status_cols[2].metric(
        "P(janela)",
        executive_status.probability,
        help="Compara a probabilidade simulada de cumprir a janela com o limiar de 80%.",
    )
    status_cols[3].metric(
        "Risco",
        executive_status.risk,
        help="CONTROLADO somente quando P80 cabe na janela e P(janela) ≥ 80%.",
    )

    status(
        executive_status.detail,
        tone=executive_status.tone,
        title=f"Status geral: {executive_status.overall}.",
    )

    if scope_risks:
        st.caption(
            f"{len(scope_risks)} atividade(s) estão tratadas como escopo potencial "
            "e, por isso, não aparecem no Gantt determinístico abaixo. Elas são "
            "materializadas nas simulações conforme os eventos de risco."
        )

    if use_calendar_axis:
        st.caption(
            "A ordem vertical preserva o cronograma importado. "
            "O eixo usa a data inicial do baseline como âncora para o plano "
            "factível; atividades destacadas possuem folga total zero no CPM."
        )
    else:
        st.caption(
            "A ordem vertical preserva o cronograma importado. "
            "As atividades destacadas possuem folga total zero no CPM do baseline."
        )

    gantt_hover = {
        "Rótulo": False,
        "ID": True,
        "Duracao_h": True,
        "Recursos": True,
        "WBS": True,
        "Criticidade": True,
    }
    if use_calendar_axis:
        gantt_hover["Início calendário"] = "|%d/%m/%Y %H:%M"
        gantt_hover["Término calendário"] = "|%d/%m/%Y %H:%M"

    fig = px.timeline(
        schedule_df,
        x_start=gantt_x_start,
        x_end=gantt_x_end,
        y="Rótulo",
        color="Criticidade",
        category_orders={
            "Rótulo": gantt_labels,
            "Criticidade": ["Não crítica", "Caminho crítico CPM"],
        },
        color_discrete_map={
            "Não crítica": "#64748B",
            "Caminho crítico CPM": "#DC2626",
        },
        hover_name="Atividade",
        hover_data=gantt_hover,
        title=(
            (
                "Cronograma factível · escopo-base · calendário"
                if scope_risks
                else "Cronograma factível · calendário"
            )
            if use_calendar_axis
            else (
                "Cronograma factível · escopo-base"
                if scope_risks
                else "Cronograma factível"
            )
        ),
    )
    fig.update_yaxes(
        autorange="reversed",
        title="",
        categoryorder="array",
        categoryarray=gantt_labels,
    )
    fig.update_layout(
        xaxis_title=gantt_xaxis_title,
        height=max(440, min(1600, 34 * len(schedule_df) + 140)),
        margin=dict(l=15, r=15, t=55, b=15),
        paper_bgcolor="white",
        plot_bgcolor="white",
        legend_title_text="",
    )
    fig.update_xaxes(
        showgrid=True,
        gridcolor="#E5E7EB",
        tickformat=("%d/%m<br>%H:%M" if use_calendar_axis else None),
    )
    st.plotly_chart(fig, use_container_width=True)

    st.divider()
    st.markdown("#### Liberar plano para execução")
    st.caption(
        "Ao aprovar, este cenário passa a ser a referência persistida de "
        "Escopo e Replanejamento: tarefas, capacidades, janela e horários "
        "RCPSP deixam de ser recalculados como um baseline independente."
    )

    approval_candidate = build_planning_baseline(
        project_name=project_name,
        source_name=uploaded.name,
        hours_per_day=int(timeline_hours_per_day),
        deadline_h=(
            None
            if deadline_h is None
            else float(deadline_h)
        ),
        capacities=capacities,
        capacity_origins=capacity_origins,
        capacities_validated=capacity_validation_ok,
        tasks=tasks,
        result=best,
        risk=risk,
        risk_assumptions={
            "simulations": int(simulations),
            "optimistic_pct": float(optimistic_pct),
            "most_likely_pct": float(most_likely_pct),
            "pessimistic_pct": float(pessimistic_pct),
        },
        scope_risks=scope_risks,
        resource_calendars=resource_calendars,
    )
    approved_baseline = execution_store.load_planning_baseline(
        approval_candidate.key
    )
    baseline_formal = bool(
        approved_baseline
        and approved_baseline.scenario_name
        and approved_baseline.approved_by
        and approved_baseline.approval_reason
    )
    readiness_subject = (
        approved_baseline
        if baseline_formal and approved_baseline is not None
        else approval_candidate
    )
    execution_readiness = validate_planning_baseline_for_execution(
        readiness_subject,
        require_formal_approval=baseline_formal,
    )
    with st.expander(
        "Validação para execução",
        expanded=not execution_readiness.ready,
    ):
        if execution_readiness.ready:
            st.success(
                f"Baseline apta à execução · "
                f"{len(execution_readiness.checks)} verificações estruturais aprovadas."
            )
        else:
            st.error(
                "A liberação para Escopo e Replanejamento está bloqueada "
                "até corrigir os erros estruturais abaixo."
            )
        for message in execution_readiness.errors:
            st.markdown(f"- ❌ {message}")
        for message in execution_readiness.warnings:
            st.markdown(f"- ⚠️ {message}")
        if execution_readiness.checks:
            st.caption(
                "Verificado: " + " · ".join(execution_readiness.checks)
            )

    if not capacity_validation_ok and not baseline_formal:
        status(
            (
                "Revise as capacidades marcadas como INFERIDA em Recursos e "
                "confirme explicitamente a validação antes de aprovar a Baseline 0."
            ),
            tone="warn",
            title="Aprovação bloqueada por capacidade não validada.",
        )
    elif baseline_formal:
        if execution_readiness.ready:
            st.session_state["execution_baseline_key"] = approved_baseline.key
            st.session_state["advanced_baseline_source"] = "Baseline aprovado"
        status(
            (
                f"{approved_baseline.scenario_name} · "
                f"{approved_baseline.makespan_h:.1f} h · "
                f"{len(approved_baseline.schedule)} atividades · "
                f"ID {approved_baseline.key[:12]}."
            ),
            tone="ok" if execution_readiness.ready else "danger",
            title=(
                "Baseline 0 formalmente aprovada e apta à execução."
                if execution_readiness.ready
                else "Baseline 0 aprovada, mas bloqueada para execução."
            ),
        )
        approval_cols = st.columns(3)
        approved_at_local = approved_baseline.approved_at.astimezone(
            ZoneInfo("America/Fortaleza")
        )
        with approval_cols[0]:
            st.caption("Aprovado por")
            st.markdown(f"**{approved_baseline.approved_by}**")
        with approval_cols[1]:
            st.caption("Aprovada em")
            st.markdown(
                f"**{approved_at_local.strftime('%d/%m/%Y %H:%M')}**"
            )
            st.caption("Horário local · America/Fortaleza")
        with approval_cols[2]:
            st.caption("Janela aprovada")
            st.markdown(
                "**"
                + (
                    "sem deadline"
                    if approved_baseline.deadline_h is None
                    else f"{approved_baseline.deadline_h / approved_baseline.hours_per_day:.2f} d"
                )
                + "**"
            )
        st.caption(
            f"Motivo / observação: {approved_baseline.approval_reason}"
        )
    else:
        if approved_baseline is not None:
            status(
                (
                    "Este snapshot foi criado antes da governança formal da "
                    "Baseline 0. Preencha os dados abaixo para formalizá-lo."
                ),
                tone="warn",
                title="Baseline legado requer formalização.",
            )
        else:
            status(
                "Este cenário ainda é apenas uma análise de planejamento.",
                tone="warn",
                title="Baseline 0 ainda não aprovada.",
            )

        st.markdown("##### Aprovação formal da Baseline 0")
        st.caption(
            "Nome, aprovador e motivo ficam persistidos com o snapshot. "
            "A data/hora da aprovação é registrada automaticamente."
        )
        with st.form(
            key=f"baseline0_approval_{approval_candidate.key[:12]}",
            clear_on_submit=False,
        ):
            scenario_name = st.text_input(
                "Nome do cenário",
                value=f"{project_name} · Baseline 0",
            )
            approved_by = st.text_input(
                "Aprovado por",
                placeholder="Nome ou identificação do responsável",
            )
            approval_reason = st.text_area(
                "Motivo / observação da aprovação",
                placeholder=(
                    "Ex.: cenário validado na reunião de congelamento do escopo "
                    "e liberado para execução."
                ),
            )
            approve_submitted = st.form_submit_button(
                "✓ Aprovar formalmente a Baseline 0",
                type="primary",
                use_container_width=True,
                disabled=not execution_readiness.ready,
            )

        if approve_submitted:
            missing_fields = []
            if not scenario_name.strip():
                missing_fields.append("nome do cenário")
            if not approved_by.strip():
                missing_fields.append("aprovador")
            if not approval_reason.strip():
                missing_fields.append("motivo / observação")

            if missing_fields:
                st.error(
                    "Preencha os campos obrigatórios: "
                    + ", ".join(missing_fields)
                    + "."
                )
            else:
                formal_candidate = approval_candidate.model_copy(
                    update={
                        "scenario_name": scenario_name.strip(),
                        "approved_by": approved_by.strip(),
                        "approval_reason": approval_reason.strip(),
                    }
                )
                approved_baseline = execution_store.save_planning_baseline(
                    formal_candidate
                )
                st.session_state["execution_baseline_key"] = approved_baseline.key
                st.session_state["advanced_baseline_source"] = "Baseline aprovado"
                st.session_state["planning_approval_notice"] = approved_baseline.key
                st.rerun()

    if (
        st.session_state.get("planning_approval_notice")
        == approval_candidate.key
    ):
        st.success(
            "Baseline 0 formalmente aprovada e persistida. "
            "Escopo e Replanejamento já pode continuar a partir deste plano."
        )
        st.session_state.pop("planning_approval_notice", None)

    if baseline_formal and execution_readiness.ready:
        st.page_link(
            "pages/2_Escopo_e_Replanejamento.py",
            label="Abrir Escopo e Replanejamento",
            icon="➡️",
            use_container_width=True,
        )

with tab_schedule:
    st.markdown("#### Cronograma completo")
    st.caption(
        "Cronograma factível por recursos e métricas CPM consolidados na mesma visão. "
        "ES/EF/LS/LF e Folga_h vêm da rede de precedências; Início/Fim vêm do RCPSP."
    )
    st.dataframe(
        schedule_view_df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Folga_h": st.column_config.NumberColumn(
                "Folga_h",
                format="%.1f",
            ),
            "Criticidade": st.column_config.TextColumn(
                "Criticidade",
                help="Folga total zero no CPM da rede de precedências.",
            ),
        },
    )

    with st.expander("Diagnóstico do heurístico", expanded=False):
        d1, d2 = st.columns(2)
        d1.metric(
            "CPM sem recursos",
            f"{comparison['unconstrained_makespan_h'] / timeline_hours_per_day:.2f} d",
        )
        d2.metric("Regra selecionada", best.priority_rule)
        st.dataframe(
            cand_df,
            use_container_width=True,
            hide_index=True,
        )

scenario_assumptions = {
    "source_name": uploaded.name,
    "deadline_h": None if deadline_h is None else float(deadline_h),
    "hours_per_day": int(timeline_hours_per_day),
    "import_hours_per_day": int(hours_per_day),
    "capacities": {
        resource: float(value)
        for resource, value in capacities.items()
    },
    "capacity_origins": dict(capacity_origins),
    "resource_calendars": {
        resource: {
            "origin_hour": float(calendar.origin_hour),
            "shifts": [
                {
                    "start_hour": float(shift.start_hour),
                    "end_hour": float(shift.end_hour),
                }
                for shift in calendar.shifts
            ],
        }
        for resource, calendar in sorted(resource_calendars.items())
    },
    "simulations": int(simulations),
    "duration_distribution": {
        "optimistic_pct": float(optimistic_pct),
        "most_likely_pct": float(most_likely_pct),
        "pessimistic_pct": float(pessimistic_pct),
    },
    "scope_events": [
        {
            "trigger": task_label_by_id.get(
                item.trigger_task_id,
                item.trigger_task_id,
            ),
            "event": item.event_name,
            "probability": float(item.probability),
        }
        for item in scope_risks
    ],
    "heuristic": f"SSGS · {best.priority_rule}",
    "calculated_at": cached_result.get("calculated_at", "não registrado"),
}

with tab_export:
    st.markdown("#### Relatório gerencial")
    st.caption(
        "PDF executivo com status da janela, KPIs, gargalos, atividades críticas "
        "e cronograma resumido."
    )

    pdf_bytes = build_base_management_pdf(
        project_name=project_name,
        hours_per_day=int(timeline_hours_per_day),
        makespan_h=float(best.makespan_h),
        deadline_h=None if deadline_h is None else float(deadline_h),
        priority_rule=best.priority_rule,
        comparison=comparison,
        schedule_df=schedule_view_df,
        criticality_df=crit_df,
        resource_df=util_df,
        risk=risk,
        baseline_scenario_name=(
            approved_baseline.scenario_name
            if baseline_formal and approved_baseline is not None
            else None
        ),
        baseline_approved_by=(
            approved_baseline.approved_by
            if baseline_formal and approved_baseline is not None
            else None
        ),
        baseline_approved_at=(
            approved_baseline.approved_at.isoformat(timespec="minutes")
            if baseline_formal and approved_baseline is not None
            else None
        ),
        baseline_approval_reason=(
            approved_baseline.approval_reason
            if baseline_formal and approved_baseline is not None
            else None
        ),
        scenario_assumptions=scenario_assumptions,
    )

    st.download_button(
        "Baixar relatório gerencial em PDF",
        data=pdf_bytes,
        file_name=f"{Path(uploaded.name).stem}_relatorio_gerencial.pdf",
        mime="application/pdf",
        use_container_width=True,
        type="primary",
    )

    with st.expander("Dados técnicos", expanded=False):
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            schedule_view_df.to_excel(
                writer,
                index=False,
                sheet_name="Cronograma Factível",
            )
            if resources:
                util_df.to_excel(
                    writer,
                    index=False,
                    sheet_name="Recursos",
                )
            cand_df.to_excel(
                writer,
                index=False,
                sheet_name="Heuristicas",
            )

        st.download_button(
            "Baixar Excel para auditoria",
            data=buffer.getvalue(),
            file_name=f"{Path(uploaded.name).stem}_dados_tecnicos.xlsx",
            mime=(
                "application/"
                "vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
            use_container_width=True,
        )
