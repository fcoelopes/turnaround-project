from __future__ import annotations

import io

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
from turnaround.io import load_schedule
from turnaround.rcpsp import infer_capacities, optimize_turnaround
from turnaround.risk import simulate_deadline_risk


st.set_page_config(page_title="Turnaround Scheduler", page_icon="🛠️", layout="wide")
st.title("🛠️ Turnaround Scheduler")
st.caption("Cronograma do Microsoft Project → CPM → RCPSP → risco de prazo")

with st.sidebar:
    st.header("Configuração")
    hours_per_day = st.number_input("Horas por dia de parada", min_value=1, max_value=24, value=8, step=1)
    deadline_days = st.number_input(
        "Janela-alvo da parada (dias)", min_value=0.0, value=0.0, step=0.5,
        help="0 = sem deadline. O prazo é convertido em horas usando as horas/dia acima."
    )
    deadline_h = int(round(deadline_days * hours_per_day)) if deadline_days > 0 else None

    st.divider()
    st.subheader("Risco de duração")
    simulations = st.slider("Simulações", 50, 1000, 300, 50)
    optimistic_pct = st.slider("Otimista (%)", -30, 0, -10, 5)
    pessimistic_pct = st.slider("Pessimista (%)", 0, 100, 30, 5)

uploaded = st.file_uploader(
    "Envie o cronograma exportado do Microsoft Project",
    type=["xml", "xlsx", "xls", "csv"],
    help="Recomendado: XML do Microsoft Project. Também aceita Excel/CSV com ID, Nome, Duração, Predecessoras e Recursos.",
)

with st.expander("Formato esperado para Excel/CSV"):
    st.markdown(
        """
        Colunas mínimas: **ID**, **Nome** e **Duracao_h** (ou nomes equivalentes em inglês/português).

        Recomendadas:
        - **Predecessoras**: `10FS;12SS+4h`
        - **Recursos**: `Mecânica;Guindaste`
        - **Demandas**: `Mecânica:3;Guindaste:1`
        - **WBS/EDT**, **Início**, **Término** (opcionais)

        Para XML, o app lê tarefas, vínculos, recursos e assignments do Project.
        """
    )

if not uploaded:
    st.info("Carregue um cronograma para iniciar. Há um arquivo de exemplo incluído no pacote do projeto.")
    st.stop()

try:
    tasks, xml_caps = load_schedule(uploaded, hours_per_day=int(hours_per_day))
except Exception as exc:
    st.error(f"Falha ao ler o cronograma: {exc}")
    st.stop()

st.success(f"{len(tasks)} atividades executáveis carregadas.")

with st.expander("1. Atividades normalizadas", expanded=False):
    st.dataframe(tasks_dataframe(tasks), use_container_width=True, hide_index=True)

base_caps = infer_capacities(tasks)
for r, q in xml_caps.items():
    base_caps[r] = max(base_caps.get(r, 0), q)

st.subheader("2. Capacidade de recursos")
resources = sorted(base_caps)
if not resources:
    st.warning(
        "O arquivo não possui recursos atribuídos. O modelo ainda calcula precedências, mas não haverá restrição de capacidade."
    )
    capacities = {}
else:
    st.caption("Ajuste quantas equipes/unidades de cada recurso estarão disponíveis durante a parada.")
    cols = st.columns(min(4, max(1, len(resources))))
    capacities = {}
    for idx, resource in enumerate(resources):
        default = max(1, int(base_caps.get(resource, 1)))
        capacities[resource] = int(
            cols[idx % len(cols)].number_input(resource, min_value=1, value=default, step=1, key=f"cap_{idx}")
        )

run = st.button("▶ Aplicar modelo de turnaround", type="primary", use_container_width=True)
if not run:
    st.stop()

try:
    best, candidates = optimize_turnaround(tasks, capacities, deadline_h=deadline_h)
    comparison = compare_baseline(tasks, best)
except Exception as exc:
    st.error(f"Não foi possível gerar um cronograma factível: {exc}")
    st.stop()

st.subheader("3. Resultado do turnaround")
metric_cols = st.columns(5)
metric_cols[0].metric("Makespan", f"{best.makespan_h / hours_per_day:.2f} dias")
metric_cols[1].metric("Horas", f"{best.makespan_h} h")
metric_cols[2].metric("Regra escolhida", best.priority_rule.replace("_", " "))
metric_cols[3].metric("CPM sem recursos", f"{comparison['unconstrained_makespan_h'] / hours_per_day:.2f} dias")
metric_cols[4].metric("Penalidade por recursos", f"{comparison['resource_penalty_h']} h")

if deadline_h:
    if best.makespan_h <= deadline_h:
        st.success(f"Cronograma determinístico cabe na janela de {deadline_days:.2f} dias.")
    else:
        st.error(
            f"Cronograma determinístico excede a janela em {(best.makespan_h - deadline_h) / hours_per_day:.2f} dias."
        )

schedule_df = schedule_dataframe(best, int(hours_per_day))

left, right = st.columns([2, 1])
with left:
    fig = px.timeline(
        schedule_df,
        x_start="Inicio_h",
        x_end="Fim_h",
        y="Atividade",
        hover_data=["ID", "Duracao_h", "Recursos", "WBS"],
        title="Gantt otimizado em horas relativas ao início da parada",
    )
    fig.update_yaxes(autorange="reversed")
    st.plotly_chart(fig, use_container_width=True)
with right:
    st.markdown("**Candidatos heurísticos**")
    cand_df = pd.DataFrame([
        {
            "Regra": c.priority_rule,
            "Makespan_h": c.makespan_h,
            "Makespan_dias": round(c.makespan_h / hours_per_day, 2),
            "Atraso_h": c.tardiness_h,
        }
        for c in candidates
    ]).sort_values(["Atraso_h", "Makespan_h"])
    st.dataframe(cand_df, use_container_width=True, hide_index=True)

st.dataframe(schedule_df, use_container_width=True, hide_index=True)

st.subheader("4. Gargalos de recurso")
if resources:
    profile = resource_profile(best)
    if not profile.empty:
        fig2 = px.line(profile, x="Hora", y="Uso", color="Recurso", title="Perfil de utilização ao longo da parada")
        st.plotly_chart(fig2, use_container_width=True)

    util_df = pd.DataFrame([
        {
            "Recurso": r,
            "Capacidade": capacities[r],
            "Pico": best.resource_peak.get(r, 0),
            "Utilizacao_%": round(best.resource_utilization.get(r, 0) * 100, 1),
        }
        for r in resources
    ]).sort_values("Utilizacao_%", ascending=False)
    st.dataframe(util_df, use_container_width=True, hide_index=True)
else:
    st.info("Sem recursos atribuídos, não há perfil de capacidade a avaliar.")

st.subheader("5. Caminho/folga por precedência")
crit_df = criticality_dataframe(tasks)
st.dataframe(crit_df, use_container_width=True, hide_index=True)

st.subheader("6. Risco de cumprir a janela")
with st.spinner("Simulando incerteza de duração..."):
    try:
        risk = simulate_deadline_risk(
            tasks,
            capacities,
            priority_rule=best.priority_rule,
            deadline_h=deadline_h,
            n=int(simulations),
            optimistic_factor=1 + optimistic_pct / 100,
            most_likely_factor=1.0,
            pessimistic_factor=1 + pessimistic_pct / 100,
        )
    except Exception as exc:
        st.warning(f"A simulação de risco não pôde ser concluída: {exc}")
        risk = None

if risk:
    risk_cols = st.columns(5)
    risk_cols[0].metric("P50", f"{risk['p50_h'] / hours_per_day:.2f} dias")
    risk_cols[1].metric("P80", f"{risk['p80_h'] / hours_per_day:.2f} dias")
    risk_cols[2].metric("P90", f"{risk['p90_h'] / hours_per_day:.2f} dias")
    risk_cols[3].metric("Média", f"{risk['mean_h'] / hours_per_day:.2f} dias")
    if deadline_h:
        risk_cols[4].metric("P(cumprir janela)", f"{risk['probability_meet_deadline'] * 100:.1f}%")
    else:
        risk_cols[4].metric("P(cumprir janela)", "defina deadline")

    hist_df = pd.DataFrame({"Makespan_dias": [x / hours_per_day for x in risk["samples"]]})
    fig3 = px.histogram(hist_df, x="Makespan_dias", nbins=30, title="Distribuição simulada da duração da parada")
    st.plotly_chart(fig3, use_container_width=True)

st.subheader("7. Exportação")
buffer = io.BytesIO()
with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
    schedule_df.to_excel(writer, index=False, sheet_name="Cronograma Otimizado")
    crit_df.to_excel(writer, index=False, sheet_name="CPM")
    if resources:
        util_df.to_excel(writer, index=False, sheet_name="Recursos")
    cand_df.to_excel(writer, index=False, sheet_name="Heuristicas")

st.download_button(
    "⬇ Baixar resultado em Excel",
    data=buffer.getvalue(),
    file_name="turnaround_resultado.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    use_container_width=True,
)

st.caption(
    "MVP: SSGS/RCPSP heurístico com quatro regras de prioridade. Não altera o arquivo original do Project. "
    "Próxima evolução natural: CP-SAT/MILP, calendários/turnos, multi-skill, custos e reimportação para Project."
)
