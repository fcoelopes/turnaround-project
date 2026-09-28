from __future__ import annotations

import hashlib
import io
import json
from datetime import datetime
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from turnaround import (
    DiscoveredTask,
    ExecutionMode,
    ExecutionState,
    ExecutionStore,
    Person,
    Precedence,
    ScopeRuleRow,
    TaskExecution,
    WorkforceProfile,
    analyze_effective_criticality,
    approved_schedule_matches_project,
    approved_schedule_to_advanced,
    apply_scope_config,
    apply_planning_scope_risks_to_project,
    build_baseline_revision,
    assign_people_to_skills,
    apply_scope_rule_rows,
    discover_resource_catalog,
    effective_capacities,
    evaluate_dynamic_scope_impacts,
    evaluate_scope_decisions,
    forecast_remaining_finish,
    materialize_dynamic_scope,
    next_discovered_task_id,
    parse_progress_file,
    reconcile_progress,
    project_from_tasks,
    resolve_activation,
    reschedule_from_state,
    solve_mrcpsp,
    summarize_pending_decisions,
    skill_capacities,
    skill_requirements,
    task_reference_catalog,
    upgrade_database,
    validate_planning_baseline_for_execution,
)
from turnaround.io import project_xml_to_tasks
from turnaround.project_export import build_project_xml
from turnaround.report import build_conditional_management_pdf
from turnaround.ui import app_header, apply_app_style, section, status, workflow_strip

ROOT = Path(__file__).resolve().parents[1]
DEMO_XML = ROOT / "sample_data" / "turnaround_conditional_model.xml"
DEMO_SCOPE = ROOT / "sample_data" / "turnaround_conditional_scope.json"

st.set_page_config(
    page_title="Escopo e Replanejamento",
    page_icon="🧩",
    layout="wide",
    initial_sidebar_state="collapsed",
)
apply_app_style()
app_header(
    "TURNAROUND DECISION SUPPORT",
    "Escopo, recursos e incerteza sobre o baseline.",
    badge="T/A",
    context="Scope discovery · MRCPSP · Multi-skill",
)
workflow_strip(
    [
        ("Plano", "Baseline", "Aprovado ou Project"),
        ("Estado", "Execução", "Congele o realizado"),
        ("Escopo", "Achados", "Ative ou descubra"),
        ("Capacidade", "Recursos", "Pessoas + equipamentos"),
        ("Decisão", "Replanejar", "Leia impacto e janela"),
    ]
)


@st.cache_resource
def _get_execution_store() -> ExecutionStore:
    upgrade_database()
    return ExecutionStore()


def _split_scope_values(raw: object) -> list[str]:
    if raw is None or pd.isna(raw):
        return []
    text = str(raw).strip()
    if not text:
        return []
    normalized = text.replace(",", ";")
    return [
        item.strip()
        for item in normalized.split(";")
        if item.strip()
    ]


def _parse_scope_event_routes(raw: object) -> dict[str, list[str]]:
    if raw is None or pd.isna(raw):
        return {}
    text = str(raw).strip()
    if not text:
        return {}

    routes: dict[str, list[str]] = {}
    for part in text.split("|"):
        item = part.strip()
        if not item:
            continue
        if "=>" not in item:
            raise ValueError(
                "Rotas devem usar 'evento=>atividade' e ser separadas por |"
            )
        event_name, refs_raw = item.split("=>", 1)
        event_name = event_name.strip()
        if not event_name:
            raise ValueError("Rota de evento exige nome do evento")
        refs = [
            ref.strip()
            for ref in refs_raw.split(",")
            if ref.strip()
        ]
        if not refs:
            raise ValueError(
                f"Rota {event_name} exige ao menos uma atividade"
            )
        routes[event_name] = refs
    return routes


def _format_scope_event_routes(routes: dict[str, list[str]]) -> str:
    return " | ".join(
        f"{event_name}=>{','.join(refs)}"
        for event_name, refs in sorted(routes.items())
    )


def _next_person_id(rows: list[dict]) -> str:
    existing = {
        str(row.get("ID", "")).strip()
        for row in rows
    }
    index = 1
    while True:
        candidate = f"P-{index:03d}"
        if candidate not in existing:
            return candidate
        index += 1


def _render_workforce_section(
    store: ExecutionStore,
    project=None,
) -> WorkforceProfile:
    profile = store.load_workforce_profile()
    st.markdown("### Pessoas e habilidades")
    st.caption(
        "Cadastre pessoas reais e marque quais recursos do cronograma representam "
        "habilidades humanas. Uma pessoa multi-skill só pode ocupar uma vaga por vez."
    )
    with st.expander("Ajuda · como funciona o multi-skill", expanded=False):
        st.markdown(
            """
- **Habilidade** é uma competência humana, como Mecânica, Elétrica ou Soldagem.
- **Pessoa multi-skill** pode possuir várias habilidades, mas não ocupa duas vagas simultaneamente.
- Recursos físicos, como guindaste ou ferramenta especial, continuam como recursos agregados e não devem ser classificados como habilidade humana.
- O scheduler só usa o roster quando **Aplicar multi-skill ao scheduling** está habilitado.
- **Cobertura de habilidades** mostra se existem pessoas ativas suficientes e se há combinação viável de pessoas para preencher todas as vagas de um modo.
            """
        )

    enabled = st.checkbox(
        "Aplicar multi-skill ao scheduling",
        value=bool(profile.enabled),
        key="workforce_enabled",
    )

    project_resources: list[str] = []
    if project is not None:
        project_resources = list(discover_resource_catalog(project))
        selected_skills = st.multiselect(
            "Recursos do plano tratados como habilidades humanas",
            options=project_resources,
            default=[
                skill
                for skill in profile.skills
                if skill in project_resources
            ],
            help=(
                "Ex.: Mecânica, Elétrica, Soldagem. Equipamentos como Guindaste "
                "continuam como recursos agregados e não devem ser selecionados."
            ),
            key="workforce_project_skills",
        )
        custom_default = "; ".join(
            skill
            for skill in profile.skills
            if skill not in project_resources
        )
    else:
        selected_skills = []
        custom_default = "; ".join(profile.skills)
        st.info(
            "A equipe pode ser cadastrada sem um projeto. Quando um XML for carregado, "
            "você poderá classificar os recursos do plano que representam habilidades humanas."
        )

    custom_skills = st.text_input(
        "Outras habilidades",
        value=custom_default,
        key="workforce_custom_skills",
        placeholder="Instrumentação; Andaime; Caldeiraria",
        help="Separe por ;. Também serve para manter uma skill mesmo sem pessoa qualificada ativa.",
    )
    configured_skills = []
    for skill in [*selected_skills, *_split_scope_values(custom_skills)]:
        if skill and skill not in configured_skills:
            configured_skills.append(skill)

    stored_rows = [
        {
            "Excluir": False,
            "ID": person.id,
            "Nome": person.name,
            "Ativo": person.active,
            "Habilidades": "; ".join(person.skills),
            "Observação": person.notes or "",
        }
        for person in profile.people
    ]
    draft_key = "workforce_people_draft"
    editor_key = "workforce_people_editor"
    if draft_key not in st.session_state:
        st.session_state[draft_key] = stored_rows

    add_col, save_col, discard_col = st.columns([1, 1, 1])
    with add_col:
        if st.button("Adicionar pessoa", type="primary", key="add_workforce_person"):
            draft = list(st.session_state.get(draft_key, []))
            draft.append(
                {
                    "Excluir": False,
                    "ID": _next_person_id(draft),
                    "Nome": "",
                    "Ativo": True,
                    "Habilidades": "",
                    "Observação": "",
                }
            )
            st.session_state[draft_key] = draft
            st.rerun()

    people_df = pd.DataFrame(st.session_state[draft_key])
    if people_df.empty:
        people_df = pd.DataFrame(
            columns=[
                "Excluir",
                "ID",
                "Nome",
                "Ativo",
                "Habilidades",
                "Observação",
            ]
        )

    edited_people_df = st.data_editor(
        people_df,
        key=editor_key,
        use_container_width=True,
        hide_index=True,
        num_rows="fixed",
        column_config={
            "Excluir": st.column_config.CheckboxColumn("Excluir"),
            "ID": st.column_config.TextColumn(
                "ID",
                help="Identificador estável da pessoa no roster.",
            ),
            "Nome": st.column_config.TextColumn("Nome"),
            "Ativo": st.column_config.CheckboxColumn(
                "Ativo",
                help="Pessoa disponível para este pool de planejamento.",
            ),
            "Habilidades": st.column_config.TextColumn(
                "Habilidades",
                help="Separe por ;. Ex.: Mecânica; Soldagem",
            ),
            "Observação": st.column_config.TextColumn("Observação"),
        },
    )
    st.session_state[draft_key] = edited_people_df.to_dict("records")

    with save_col:
        if st.button("Salvar equipe", key="save_workforce"):
            try:
                people: list[Person] = []
                for row_number, raw in enumerate(
                    edited_people_df.to_dict("records"),
                    start=1,
                ):
                    if bool(raw.get("Excluir", False)):
                        continue
                    person_id = str(raw.get("ID", "") or "").strip()
                    name = str(raw.get("Nome", "") or "").strip()
                    if not person_id and not name:
                        continue
                    people.append(
                        Person(
                            id=person_id,
                            name=name,
                            active=bool(raw.get("Ativo", True)),
                            skills=_split_scope_values(raw.get("Habilidades", "")),
                            notes=(
                                str(raw.get("Observação", "") or "").strip()
                                or None
                            ),
                        )
                    )

                saved = WorkforceProfile(
                    enabled=enabled,
                    skills=configured_skills,
                    people=people,
                )
                store.save_workforce_profile(saved)
                st.session_state.pop(draft_key, None)
                st.session_state.pop(editor_key, None)
                st.success(
                    f"Equipe salva: {len(saved.people)} pessoa(s), "
                    f"{len(saved.skills)} habilidade(s)."
                )
                st.rerun()
            except (ValueError, TypeError) as exc:
                st.error(f"Não foi possível salvar a equipe: {exc}")

    with discard_col:
        if st.button("Descartar alterações", key="discard_workforce"):
            st.session_state.pop(draft_key, None)
            st.session_state.pop(editor_key, None)
            st.rerun()

    current = store.load_workforce_profile()
    if current.people:
        st.markdown("#### Cobertura de habilidades")
        capacities = skill_capacities(current)
        resource_catalog = (
            discover_resource_catalog(project)
            if project is not None
            else {}
        )
        coverage_rows = []
        for skill in current.skills:
            entry = resource_catalog.get(skill)
            coverage_rows.append(
                {
                    "Habilidade": skill,
                    "Pessoas ativas qualificadas": int(capacities.get(skill, 0)),
                    "Pessoas qualificadas no cadastro": sum(
                        1
                        for person in current.people
                        if skill in person.skills
                    ),
                    "Maior demanda de um modo": (
                        float(entry.max_demand)
                        if entry is not None
                        else "—"
                    ),
                    "Atividades/modos usam": (
                        len(entry.task_ids)
                        if entry is not None
                        else 0
                    ),
                }
            )
        with st.expander("Cobertura de habilidades", expanded=False):
            st.dataframe(
                pd.DataFrame(coverage_rows),
                use_container_width=True,
                hide_index=True,
            )

        if current.enabled and project is not None:
            uncovered = [
                row
                for row in coverage_rows
                if row["Maior demanda de um modo"] != "—"
                and float(row["Maior demanda de um modo"])
                > float(row["Pessoas ativas qualificadas"])
            ]
            if uncovered:
                status(
                    (
                        "Há habilidades cuja maior demanda individual supera o número "
                        "de pessoas ativas qualificadas: "
                        + ", ".join(row["Habilidade"] for row in uncovered)
                    ),
                    tone="warn",
                    title="Cobertura multi-skill insuficiente.",
                )

            impossible_modes = []
            for task in project.tasks:
                for mode in task.modes:
                    requirements = skill_requirements(
                        mode.resources,
                        current,
                    )
                    if not requirements:
                        continue
                    if assign_people_to_skills(requirements, current) is None:
                        impossible_modes.append(
                            {
                                "ID": task.id,
                                "Atividade": task.name,
                                "Modo": mode.name,
                                "Habilidades exigidas": "; ".join(
                                    f"{skill}={count}"
                                    for skill, count in requirements.items()
                                ),
                            }
                        )
            if impossible_modes:
                status(
                    (
                        f"{len(impossible_modes)} modo(s) têm quantidade por skill aparentemente "
                        "suficiente, mas não existe matching de pessoas que preencha todas as vagas."
                    ),
                    tone="warn",
                    title="Composição de equipe impossível.",
                )
                with st.expander("Ver modos com conflito de composição", expanded=False):
                    st.dataframe(
                        pd.DataFrame(impossible_modes),
                        use_container_width=True,
                        hide_index=True,
                    )

    st.caption(
        "Nesta primeira versão, habilidade é binária: a pessoa possui ou não possui. "
        "Níveis de proficiência e produtividade entram em uma evolução posterior."
    )
    return current


def _next_scope_rule_id(rows: list[dict]) -> str:
    existing = {
        str(row.get("Regra", "")).strip()
        for row in rows
    }
    index = 1
    while True:
        candidate = f"RULE-{index:03d}"
        if candidate not in existing:
            return candidate
        index += 1


def _parse_resource_demands(raw: str) -> dict[str, float]:
    demands: dict[str, float] = {}
    if not raw.strip():
        return demands

    for part in raw.split(";"):
        item = part.strip()
        if not item:
            continue
        if "=" not in item:
            raise ValueError(
                f"Recurso inválido '{item}'. Use Nome=quantidade; Nome=quantidade."
            )
        name, raw_value = item.split("=", 1)
        resource = name.strip()
        if not resource:
            raise ValueError("Nome do recurso não pode ser vazio")
        try:
            value = float(raw_value.strip().replace(",", "."))
        except ValueError as exc:
            raise ValueError(
                f"Quantidade inválida para {resource}: {raw_value.strip()}"
            ) from exc
        if value < 0:
            raise ValueError(f"Demanda de {resource} deve ser >= 0")
        demands[resource] = value
    return demands


def _resource_usage_snapshot(
    schedule_items: list,
    capacities: dict[str, float],
    makespan: float,
) -> dict[str, dict[str, float]]:
    """Calcula pico e utilização média do snapshot MRCPSP exibido."""
    metrics: dict[str, dict[str, float]] = {}
    resource_names = set(capacities)
    for item in schedule_items:
        resource_names.update(item.resources)

    for resource in sorted(resource_names, key=str.casefold):
        events: list[tuple[float, int, float]] = []
        load_hours = 0.0
        for item in schedule_items:
            demand = float(item.resources.get(resource, 0.0))
            if demand <= 0:
                continue
            start = float(item.start)
            finish = float(item.finish)
            duration = max(0.0, finish - start)
            load_hours += demand * duration
            # Em instantes coincidentes, liberações são processadas antes de inícios.
            events.append((start, 1, demand))
            events.append((finish, 0, -demand))

        current = 0.0
        peak = 0.0
        for _, _, delta in sorted(events, key=lambda row: (row[0], row[1])):
            current += delta
            peak = max(peak, current)

        capacity = float(capacities.get(resource, 0.0))
        utilization = (
            0.0
            if capacity <= 0 or makespan <= 0
            else load_hours / (capacity * float(makespan))
        )
        metrics[resource] = {
            "peak": peak,
            "utilization": utilization,
            "peak_slack": capacity - peak,
        }
    return metrics


def load_project(store: ExecutionStore):
    section(
        "1",
        "Carregar baseline",
        (
            "Continue a partir de um plano aprovado na guia Planejamento ou, "
            "quando necessário, carregue diretamente um XML do Microsoft Project."
        ),
    )

    approved_baselines = store.list_planning_baselines(limit=20)
    source_options = []
    if approved_baselines:
        source_options.append("Baseline aprovado")
    source_options.extend(["Importar XML", "Cenário demonstrativo"])

    source_mode = st.radio(
        "Origem do baseline",
        options=source_options,
        horizontal=True,
        key="advanced_baseline_source",
    )

    approved_baseline = None
    source_kind = None
    tasks = None
    xml_caps = None
    project_name = None

    if source_mode == "Baseline aprovado":
        preferred_key = st.session_state.get("execution_baseline_key")
        keys = [item.key for item in approved_baselines]
        preferred_index = (
            keys.index(preferred_key)
            if preferred_key in keys
            else 0
        )
        selected_key = st.selectbox(
            "Plano aprovado para execução",
            options=keys,
            index=preferred_index,
            format_func=lambda key: next(
                (
                    (
                        f"{item.project_name} · {item.makespan_h:.1f} h · "
                        f"{item.approved_at.astimezone().strftime('%d/%m/%Y %H:%M')}"
                    )
                    for item in approved_baselines
                    if item.key == key
                ),
                key[:12],
            ),
            key="approved_baseline_selector",
        )
        approved_baseline = next(
            item
            for item in approved_baselines
            if item.key == selected_key
        )
        st.session_state["execution_baseline_key"] = approved_baseline.key
        readiness = validate_planning_baseline_for_execution(
            approved_baseline,
            allow_dangling_repair=True,
            require_formal_approval=True,
        )
        if not readiness.ready:
            status(
                (
                    "Este baseline foi persistido, mas falhou na validação "
                    "defensiva antes de entrar no replanejamento."
                ),
                tone="danger",
                title="Baseline bloqueado para execução.",
            )
            for message in readiness.errors:
                st.error(message)
            for message in readiness.warnings:
                st.warning(message)
            return None, None, None, None, None

        for message in readiness.warnings:
            status(
                message,
                tone="warn",
                title="Ajuste defensivo aplicado ao baseline.",
            )

        dangling_links = approved_baseline.dangling_predecessor_links()
        tasks = approved_baseline.to_tasks(
            drop_dangling_predecessors=bool(dangling_links)
        )
        xml_caps = {
            resource: float(quantity)
            for resource, quantity in approved_baseline.capacities.items()
        }
        project_name = approved_baseline.project_name
        source_kind = "approved"

        status(
            (
                f"{len(approved_baseline.tasks)} atividades · "
                f"makespan aprovado {approved_baseline.makespan_h:.1f} h · "
                f"regra {approved_baseline.priority_rule} · "
                f"ID {approved_baseline.key[:12]}"
            ),
            tone="ok",
            title="Baseline herdado do Planejamento.",
        )
        st.caption(
            "As capacidades, a janela, os calendários de recurso e os horários "
            "RCPSP aprovados acompanham o baseline. O replanejamento passa a "
            "medir mudanças contra essa referência."
        )

    elif source_mode == "Importar XML":
        xml_upload = st.file_uploader(
            "Microsoft Project XML",
            type=["xml"],
            key="advanced_xml",
            help=(
                "Use esta opção quando a execução não nasceu da guia Planejamento. "
                "A planilha de regras continua disponível mesmo sem JSON."
            ),
        )
        if xml_upload is None:
            st.info("Envie um XML do Microsoft Project para continuar.")
            return None, None, None, None, None

        tasks, xml_caps = project_xml_to_tasks(xml_upload.getvalue())
        project_name = Path(xml_upload.name).stem.replace("_", " ")
        source_kind = "real"

    else:
        tasks, xml_caps = project_xml_to_tasks(DEMO_XML.read_bytes())
        project_name = "Turnaround Kinder Ovo"
        source_kind = "demo"

    baseline_start_values = [
        task.baseline_start
        for task in tasks
        if task.baseline_start
    ]
    parsed_starts = pd.to_datetime(
        pd.Series(baseline_start_values, dtype="object"),
        errors="coerce",
    ).dropna()
    calendar_origin = (
        parsed_starts.min().to_pydatetime()
        if not parsed_starts.empty
        else None
    )

    project = project_from_tasks(
        tasks,
        xml_caps,
        deadline=(
            approved_baseline.deadline_h
            if approved_baseline is not None
            else None
        ),
        resource_calendars=(
            approved_baseline.to_resource_calendars()
            if approved_baseline is not None
            else None
        ),
    )
    if approved_baseline is not None and approved_baseline.scope_risks:
        project = apply_planning_scope_risks_to_project(
            project,
            approved_baseline.scope_risks,
        )

    with st.expander(
        "Importar regras existentes por JSON (opcional)",
        expanded=False,
    ):
        scope_upload = st.file_uploader(
            "Regras de escopo / modos (JSON)",
            type=["json"],
            key="advanced_scope",
            help=(
                "Opcional. As regras podem ser cadastradas diretamente na "
                "planilha de configuração."
            ),
        )

    if scope_upload is not None:
        project = apply_scope_config(
            project,
            io.BytesIO(scope_upload.getvalue()),
        )
    elif source_kind == "demo":
        project = apply_scope_config(project, DEMO_SCOPE)

    if source_kind == "real":
        st.caption(f"{project_name} · baseline carregado diretamente do XML")

    return (
        project,
        project_name,
        source_kind,
        approved_baseline,
        calendar_origin,
    )


execution_store = _get_execution_store()

vision_tab, operation_tab, scope_tab, resources_tab, governance_tab = st.tabs(
    ["Visão", "Operação", "Escopo", "Recursos", "Governança"]
)

with operation_tab:
    (
        project,
        project_name,
        project_source_kind,
        approved_planning_baseline,
        calendar_origin,
    ) = load_project(execution_store)

    with st.expander("Ajuda · fluxo operacional", expanded=False):
        st.markdown(
            """
**Use Operação para registrar o que realmente aconteceu na parada.**

1. Avance a **hora corrente** para representar onde a execução está.
2. Registre **achados/eventos** quando uma inspeção ou atividade produzir uma condição observada.
3. Se o trabalho não existia no plano, crie uma atividade **DS-***.
4. Quando uma regra `XOR/OR` usa resolução **human**, o evento abre **Decisão de escopo necessária**; compare os cenários e escolha em **Resolver decisão**.
5. Quando a resolução é **event**, a rota previamente configurada é aplicada automaticamente.
6. Leia o efeito no makespan, janela, novo escopo e cadeia controladora antes de formalizar qualquer rebaseline.

**Importante:** registrar um evento e decidir o que fazer com ele não são necessariamente a mesma coisa.
            """
        )
        st.caption(
            "O manual técnico completo permanece em docs/manual-escopo-condicional.md."
        )

workforce = execution_store.load_workforce_profile()

if project is None:
    with vision_tab:
        st.markdown("### Visão executiva da parada")
        st.info(
            "Selecione um baseline aprovado, carregue um XML na aba Operação "
            "ou use o cenário demonstrativo para montar a visão executiva."
        )
    with scope_tab:
        st.markdown("### Escopo")
        st.info(
            "Selecione um baseline aprovado, carregue um XML na aba Operação "
            "ou use o cenário demonstrativo para configurar regras de escopo."
        )
    with resources_tab:
        st.markdown("### Recursos da parada")
        st.info(
            "Sem um baseline carregado, ainda é possível preparar o cadastro de "
            "pessoas e habilidades. Capacidades de equipamentos aparecem quando "
            "um projeto estiver disponível."
        )
        _render_workforce_section(execution_store, project)
    st.stop()

base_planned_project = project

# O fingerprint identifica o baseline + sidecar importado. Regras adicionadas
# pela planilha são persistidas contra esse baseline e podem evoluir sem criar
# uma nova identidade de projeto a cada edição.
project_payload = {
    "project_name": project_name,
    "project": base_planned_project.model_dump(mode="json"),
}
if approved_planning_baseline is not None:
    project_payload["approved_planning_baseline"] = (
        approved_planning_baseline.key
    )

project_key = hashlib.sha256(
    json.dumps(
        project_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
).hexdigest()

with scope_tab:
    st.markdown("### Escopo da parada")
    st.caption(
        "Defina aqui a lógica de ampliação e seleção de escopo. "
        "O JSON continua opcional; o editor tabular é o mecanismo principal."
    )
    with st.expander("Ajuda · domínio de escopo", expanded=False):
        st.markdown(
            """
Use esta aba somente para regras que alteram **o que entra ou não entra na parada**.

- **conditional**: uma atividade entra quando um gatilho/evento ocorre.
- **XOR**: exatamente uma alternativa deve ser escolhida.
- **OR**: uma ou mais alternativas podem ser escolhidas.
- **AND**: todos os membros entram quando a condição é satisfeita.
- **Gatilhos e eventos** representam a evidência que libera a regra.
- **Resolução human** mantém a decisão com o planejador.
- **Resolução event** aplica uma rota determinística já configurada.

Capacidades, pessoas e estabilidade do replanejamento pertencem a outros domínios e não são configuradas aqui.
            """
        )

    with st.expander("Ajuda · conditional, XOR/OR/AND e resolução", expanded=False):
        st.markdown(
            """
- **conditional** não representa escolha: se o gatilho/evento ocorrer, a atividade-alvo entra.
- **XOR** exige exatamente uma alternativa.
- **OR** permite uma ou mais alternativas.
- **AND** ativa todos os membros quando a condição é satisfeita.
- **Resolução = human:** o evento abre uma decisão na aba Operação; o sistema calcula impactos, mas não escolhe a alternativa técnica.
- **Resolução = event:** o próprio evento determina o ramo por meio de **Rotas event**.
- **Rotas event** usam o formato `evento=>atividade | outro_evento=>outra_atividade`.

Exemplo: `replacement_required=>uid:10 | repairable=>uid:9`.
            """
        )

    st.markdown("#### Regras de escopo em planilha")
    if project_source_kind == "approved":
        st.caption(
            "Baseline aprovado: as regras abaixo são uma camada adicional sobre "
            "o plano liberado na guia Planejamento."
        )
    elif project_source_kind == "real":
        st.caption(
            "Plano carregado por XML: a planilha abaixo é a entrada principal "
            "para cadastrar regras; o JSON acima é opcional."
        )
    else:
        st.caption(
            "Cenário demonstrativo: a planilha adiciona regras sobre o sidecar Kinder Ovo."
        )

    inherited_rule_rows = []
    for task in base_planned_project.tasks:
        if task.activation.kind != "conditional":
            continue
        for index, condition in enumerate(task.activation.conditions, start=1):
            inherited_rule_rows.append(
                {
                    "Origem": (
                        "Planejamento"
                        if approved_planning_baseline is not None
                        and approved_planning_baseline.scope_risks
                        else "JSON / sidecar"
                    ),
                    "Regra": f"activation:{task.id}:{index}",
                    "Tipo": "conditional",
                    "Alvo / membros": f"{task.id} · {task.name}",
                    "Gatilho": condition.source_task_id,
                    "Eventos": "; ".join(condition.events),
                    "Resolução": "—",
                }
            )

    for group in base_planned_project.logical_groups:
        inherited_rule_rows.append(
            {
                "Origem": (
                        "Planejamento"
                        if approved_planning_baseline is not None
                        and approved_planning_baseline.scope_risks
                        else "JSON / sidecar"
                    ),
                "Regra": group.id,
                "Tipo": group.operator,
                "Alvo / membros": "; ".join(group.member_task_ids),
                "Gatilho": (
                    group.when.source_task_id
                    if group.when is not None
                    else "—"
                ),
                "Eventos": (
                    "; ".join(group.when.events)
                    if group.when is not None
                    else "—"
                ),
                "Resolução": group.resolution_mode,
            }
        )

    if inherited_rule_rows:
        with st.expander(
            f"Regras herdadas do JSON / sidecar ({len(inherited_rule_rows)})",
            expanded=False,
        ):
            st.dataframe(
                pd.DataFrame(inherited_rule_rows),
                use_container_width=True,
                hide_index=True,
            )
            st.caption(
                "Estas regras continuam válidas e não são editadas pela planilha. "
                "A grade abaixo adiciona uma camada complementar."
            )

    stored_scope_rules = execution_store.load_scope_rules(project_key)
    scope_ref_catalog = task_reference_catalog(base_planned_project)
    scope_label_to_ref = {
        label: reference
        for reference, label in scope_ref_catalog.items()
    }
    scope_ref_to_label = dict(scope_ref_catalog)

    scope_rule_rows = [
        {
            "Excluir": False,
            "Regra": row.id,
            "Ativa": row.enabled,
            "Tipo": row.rule_type,
            "Atividade alvo": (
                scope_ref_to_label.get(row.target_task_ref, row.target_task_ref)
                if row.target_task_ref
                else ""
            ),
            "Gatilho": (
                scope_ref_to_label.get(row.trigger_task_ref, row.trigger_task_ref)
                if row.trigger_task_ref
                else ""
            ),
            "Eventos": "; ".join(row.events),
            "Lógica eventos": row.event_logic,
            "Membros": "; ".join(row.member_task_refs),
            "Resolução": row.resolution_mode,
            "Rotas event": _format_scope_event_routes(row.event_routes),
            "Observação": row.notes or "",
        }
        for row in stored_scope_rules
    ]

    scope_draft_key = f"scope_rule_draft_{project_key[:12]}"
    scope_editor_key = f"scope_rule_editor_{project_key[:12]}"
    if scope_draft_key not in st.session_state:
        st.session_state[scope_draft_key] = scope_rule_rows

    add_rule_col, save_rule_col, discard_rule_col = st.columns([1, 1, 1])
    with add_rule_col:
        if st.button(
            "Adicionar regra",
            type="primary",
            key=f"add_scope_rule_{project_key[:12]}",
        ):
            draft = list(st.session_state.get(scope_draft_key, []))
            draft.append(
                {
                    "Excluir": False,
                    "Regra": _next_scope_rule_id(draft),
                    "Ativa": True,
                    "Tipo": "conditional",
                    "Atividade alvo": "",
                    "Gatilho": "",
                    "Eventos": "",
                    "Lógica eventos": "any",
                    "Membros": "",
                    "Resolução": "human",
                    "Rotas event": "",
                    "Observação": "",
                }
            )
            st.session_state[scope_draft_key] = draft
            st.rerun()

    scope_editor_df = pd.DataFrame(st.session_state[scope_draft_key])
    if scope_editor_df.empty:
        scope_editor_df = pd.DataFrame(
            columns=[
                "Excluir",
                "Regra",
                "Ativa",
                "Tipo",
                "Atividade alvo",
                "Gatilho",
                "Eventos",
                "Lógica eventos",
                "Membros",
                "Resolução",
                "Rotas event",
                "Observação",
            ]
        )

    edited_scope_df = st.data_editor(
        scope_editor_df,
        key=scope_editor_key,
        use_container_width=True,
        hide_index=True,
        num_rows="fixed",
        column_config={
            "Excluir": st.column_config.CheckboxColumn(
                "Excluir",
                help="Marque e clique em Salvar regras para remover a linha.",
            ),
            "Regra": st.column_config.TextColumn(
                "Regra",
                help="ID único da regra, por exemplo RULE-001 ou P101_impeller.",
            ),
            "Ativa": st.column_config.CheckboxColumn("Ativa"),
            "Tipo": st.column_config.SelectboxColumn(
                "Tipo",
                options=["conditional", "xor", "or", "and"],
                required=True,
            ),
            "Atividade alvo": st.column_config.SelectboxColumn(
                "Atividade alvo",
                options=["", *scope_ref_catalog.values()],
                help="Usada por regras conditional.",
            ),
            "Gatilho": st.column_config.SelectboxColumn(
                "Gatilho",
                options=["", *scope_ref_catalog.values()],
                help="Atividade cuja conclusão/evento libera a regra.",
            ),
            "Eventos": st.column_config.TextColumn(
                "Eventos",
                help="Um ou mais eventos separados por ;. Ex.: crack_detected; wear_high",
            ),
            "Lógica eventos": st.column_config.SelectboxColumn(
                "Lógica eventos",
                options=["any", "all"],
            ),
            "Membros": st.column_config.TextColumn(
                "Membros",
                help="Para XOR/OR/AND: referências separadas por ;. Ex.: uid:9009; uid:9010",
            ),
            "Resolução": st.column_config.SelectboxColumn(
                "Resolução",
                options=["human", "event"],
                help="human mantém a decisão com o planejador; event apenas aplica uma regra determinística previamente definida.",
            ),
            "Rotas event": st.column_config.TextColumn(
                "Rotas event",
                help="Somente resolution=event. Ex.: repairable=>uid:9 | replacement_required=>uid:10",
            ),
            "Observação": st.column_config.TextColumn("Observação"),
        },
    )
    st.session_state[scope_draft_key] = edited_scope_df.to_dict("records")

    with st.expander("Referências para preencher Membros / Rotas", expanded=False):
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Referência": reference,
                        "ID atual": task.id,
                        "UID Project": task.project_uid or "—",
                        "Atividade": task.name,
                    }
                    for task in base_planned_project.tasks
                    for reference in [
                        (
                            f"uid:{task.project_uid}"
                            if task.project_uid is not None
                            else f"id:{task.id}"
                        )
                    ]
                ]
            ),
            use_container_width=True,
            hide_index=True,
        )

    with save_rule_col:
        if st.button(
            "Salvar regras",
            key=f"save_scope_rules_{project_key[:12]}",
        ):
            try:
                parsed_rows: list[ScopeRuleRow] = []
                for index, raw in enumerate(
                    edited_scope_df.to_dict("records"),
                    start=1,
                ):
                    if bool(raw.get("Excluir", False)):
                        continue

                    rule_id = str(raw.get("Regra", "") or "").strip()
                    if not rule_id:
                        raise ValueError(
                            f"Linha {index}: informe o ID da regra"
                        )

                    target_label = str(
                        raw.get("Atividade alvo", "") or ""
                    ).strip()
                    trigger_label = str(
                        raw.get("Gatilho", "") or ""
                    ).strip()

                    parsed_rows.append(
                        ScopeRuleRow(
                            id=rule_id,
                            enabled=bool(raw.get("Ativa", True)),
                            rule_type=str(raw.get("Tipo", "conditional")),
                            target_task_ref=(
                                scope_label_to_ref.get(target_label, target_label)
                                if target_label
                                else None
                            ),
                            trigger_task_ref=(
                                scope_label_to_ref.get(trigger_label, trigger_label)
                                if trigger_label
                                else None
                            ),
                            events=_split_scope_values(raw.get("Eventos", "")),
                            event_logic=str(
                                raw.get("Lógica eventos", "any") or "any"
                            ),
                            member_task_refs=_split_scope_values(
                                raw.get("Membros", "")
                            ),
                            resolution_mode=str(
                                raw.get("Resolução", "human") or "human"
                            ),
                            event_routes=_parse_scope_event_routes(
                                raw.get("Rotas event", "")
                            ),
                            notes=(
                                str(raw.get("Observação", "") or "").strip()
                                or None
                            ),
                        )
                    )

                # Valida a composição inteira antes de escrever no banco.
                apply_scope_rule_rows(base_planned_project, parsed_rows)
                execution_store.replace_scope_rules(
                    project_key,
                    parsed_rows,
                )
                st.session_state.pop(scope_draft_key, None)
                st.session_state.pop(scope_editor_key, None)
                st.success(f"{len(parsed_rows)} regra(s) salva(s).")
                st.rerun()
            except (ValueError, TypeError) as exc:
                st.error(f"Não foi possível salvar as regras: {exc}")

    with discard_rule_col:
        if st.button(
            "Descartar alterações",
            key=f"discard_scope_rules_{project_key[:12]}",
        ):
            st.session_state.pop(scope_draft_key, None)
            st.session_state.pop(scope_editor_key, None)
            st.rerun()

    if stored_scope_rules:
        st.caption(
            f"{len(stored_scope_rules)} regra(s) adicionais persistida(s) neste baseline."
        )


planned_project = apply_scope_rule_rows(
    base_planned_project,
    stored_scope_rules,
)

execution_session = execution_store.get_or_create_active_session(
    project_key=project_key,
    project_name=project_name,
)
baseline_revisions = execution_store.list_baseline_revisions(
    execution_session.id
)
governing_baseline_revision = (
    baseline_revisions[-1]
    if baseline_revisions
    else None
)
original_deadline = (
    approved_planning_baseline.deadline_h
    if approved_planning_baseline is not None
    else planned_project.deadline
)
if governing_baseline_revision is not None:
    planned_project = planned_project.model_copy(
        update={"deadline": governing_baseline_revision.deadline_h}
    )

discovered_tasks = execution_store.load_discovered_tasks(
    execution_session.id
)

with operation_tab:
    st.caption(
        f"Sessão persistida: {execution_session.id[:8]} · SQLite · "
        f"{len(discovered_tasks)} atividade(s) dinâmica(s) armazenada(s) · "
        f"{len(baseline_revisions)} rebaseline(s) formal(is)"
    )

    with st.expander("Sessão de execução persistida", expanded=False):
        st.write(
            f"**Sessão:** `{execution_session.id}`  \\n"
            f"**Baseline:** `{project_key[:12]}`  \\n"
            "Uma nova sessão arquiva esta execução e começa sem achados, DS-* ou decisões."
        )
        confirm_new_session = st.checkbox(
            "Confirmo que quero iniciar uma nova sessão limpa para este baseline",
            key=f"confirm_new_execution_{execution_session.id[:8]}",
        )
        if st.button(
            "Iniciar nova sessão",
            disabled=not confirm_new_session,
            key=f"new_execution_{execution_session.id[:8]}",
        ):
            execution_store.start_new_session(
                project_key=project_key,
                project_name=project_name,
                initial_current_time=0.0,
            )
            for key in list(st.session_state):
                if key.startswith("scope_decision_"):
                    del st.session_state[key]
            st.rerun()

with operation_tab:
    with st.expander("Histórico da execução", expanded=False):
        persisted_events = execution_store.list_events(
            execution_session.id,
            limit=100,
        )
        if persisted_events:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "ID": item.id,
                            "Quando": item.occurred_at,
                            "Evento": item.event_type,
                            "Atividade": item.task_id or "—",
                            "Detalhes": json.dumps(
                                item.payload,
                                ensure_ascii=False,
                                sort_keys=True,
                            ),
                        }
                        for item in persisted_events
                    ]
                ),
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.caption("Ainda não há eventos persistidos nesta sessão.")


dynamic_materialization = materialize_dynamic_scope(
    planned_project,
    discovered_tasks,
)
base_project = dynamic_materialization.project
dynamic_scope_ids = dynamic_materialization.discovered_ids

resource_catalog = discover_resource_catalog(base_project)
base_capacities = dict(base_project.capacities)
unknown_resources = [
    entry
    for entry in resource_catalog.values()
    if (
        not entry.capacity_defined
        and not (
            workforce.enabled
            and entry.name in workforce.skills
        )
    )
]

with resources_tab:
    st.markdown("### Recursos da parada")
    st.caption(
        "Concentre aqui a capacidade operacional do cenário: equipamentos, "
        "recursos agregados, pessoas, habilidades e multi-skill."
    )
    st.markdown("#### Capacidades e equipamentos")
    st.caption(
        "Ajuste capacidades somente quando quiser testar outro cenário. "
        "Os controles são gerados a partir dos recursos usados pelo plano e pelos modos MRCPSP."
    )

    scenario_name = st.session_state.get(
        "mrcpsp_scenario_name",
        "Cenário de recursos A",
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

    workforce_skill_capacities = (
        skill_capacities(workforce)
        if workforce.enabled
        else {}
    )
    cols = st.columns(min(4, max(1, len(resource_catalog))))
    scenario_capacities: dict[str, float] = {}
    for i, (resource, entry) in enumerate(resource_catalog.items()):
        base_capacity = entry.base_capacity
        persisted_capacity = execution_session.scenario_capacities.get(resource)
        default_value = (
            float(persisted_capacity)
            if persisted_capacity is not None
            else (float(base_capacity) if base_capacity is not None else 0.0)
        )
        upper = max(
            2.0,
            default_value * 2.5,
            float(entry.max_demand) * 2.0,
            float(entry.max_demand) + 2.0,
        )
        values_for_step = [float(entry.max_demand), default_value]
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
            if workforce.enabled and resource in workforce.skills:
                scenario_capacities[resource] = float(
                    workforce_skill_capacities.get(resource, 0.0)
                )
                st.metric(
                    resource,
                    f"{scenario_capacities[resource]:g} pessoa(s)",
                    help=(
                        "Capacidade derivada das pessoas ativas qualificadas. "
                        "Para esta habilidade, o slider agregado é substituído "
                        "pela alocação individual multi-skill."
                    ),
                )
            else:
                scenario_capacities[resource] = st.slider(
                    resource,
                    min_value=0.0,
                    max_value=float(upper),
                    value=default_value,
                    step=step,
                    key=f"advanced_cap_{execution_session.id[:8]}_{i}_{resource}",
                    help=help_text,
                )

    effective_base_capacities = effective_capacities(
        base_capacities,
        workforce,
    )
    effective_scenario_capacities = effective_capacities(
        scenario_capacities,
        workforce,
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
                "Fonte da capacidade": (
                    "Pessoas / multi-skill"
                    if workforce.enabled and resource in workforce.skills
                    else "Capacidade agregada"
                ),
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
    with st.expander("Resumo das capacidades", expanded=False):
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
                human_requirements = skill_requirements(
                    mode.resources,
                    workforce,
                )
                workforce_match = (
                    assign_people_to_skills(
                        human_requirements,
                        workforce,
                    )
                    if human_requirements
                    else {}
                )
                feasible_base = (
                    all(
                        demand <= effective_base_capacities.get(resource, 0.0) + 1e-9
                        for resource, demand in mode.resources.items()
                    )
                    and workforce_match is not None
                )
                feasible_scenario = (
                    all(
                        demand <= effective_scenario_capacities.get(resource, 0.0) + 1e-9
                        for resource, demand in mode.resources.items()
                    )
                    and workforce_match is not None
                )
                missing_base = [
                    resource
                    for resource in mode.resources
                    if (
                        resource not in base_capacities
                        and not (
                            workforce.enabled
                            and resource in workforce.skills
                        )
                    )
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


with resources_tab:
    st.divider()
    _render_workforce_section(execution_store, project)

with operation_tab:
    with st.expander("Preferências do replanejamento", expanded=False):
        st.caption(
            "Escolha quanto o replanejamento deve preservar os horários já "
            "combinados para atividades futuras. Atraso à janela continua sendo prioridade."
        )

        stability_presets = {
            "Baixa": 0.3,
            "Balanceada": 1.0,
            "Alta": 1.7,
        }
        persisted_stability_weight = min(
            2.0,
            max(0.0, float(execution_session.stability_weight)),
        )
        matching_level = next(
            (
                level
                for level, weight in stability_presets.items()
                if abs(weight - persisted_stability_weight) <= 1e-9
            ),
            None,
        )
        nearest_level = min(
            stability_presets,
            key=lambda level: abs(
                stability_presets[level] - persisted_stability_weight
            ),
        )
        selected_stability_level = st.radio(
            "Preservação do plano",
            options=list(stability_presets),
            index=list(stability_presets).index(
                matching_level or nearest_level
            ),
            horizontal=True,
            key=f"stability_level_{execution_session.id[:8]}",
            help=(
                "Baixa permite maior rearranjo do trabalho futuro; Balanceada busca "
                "compromisso entre prazo e estabilidade; Alta penaliza mais mudanças "
                "nos horários já planejados."
            ),
        )

        show_advanced_stability = st.checkbox(
            "Mostrar configuração avançada de estabilidade",
            value=matching_level is None,
            key=f"stability_advanced_visible_{execution_session.id[:8]}",
            help="Exibe o λ numérico somente para estudos ou calibração.",
        )
        use_manual_stability_weight = matching_level is None
        manual_stability_weight = persisted_stability_weight
        if show_advanced_stability:
            use_manual_stability_weight = st.checkbox(
                "Usar valor λ manual",
                value=matching_level is None,
                key=f"stability_manual_enabled_{execution_session.id[:8]}",
                help=(
                    "Quando ativo, este valor substitui o nível operacional "
                    "selecionado acima."
                ),
            )
            manual_stability_weight = st.slider(
                "λ de estabilidade",
                min_value=0.0,
                max_value=2.0,
                value=persisted_stability_weight,
                step=0.1,
                disabled=not use_manual_stability_weight,
                key=f"stability_weight_{execution_session.id[:8]}",
                help=(
                    "Objetivo do rescheduling: makespan + λ × soma dos deslocamentos "
                    "de início. λ=0 ignora estabilidade; valores maiores penalizam "
                    "mais mudanças de início."
                ),
            )
            if use_manual_stability_weight:
                st.caption(
                    "Valor avançado ativo: o λ manual substitui o nível "
                    f"{selected_stability_level}."
                )
            else:
                st.caption(
                    "Mapeamento operacional: "
                    + " · ".join(
                        f"{level} = λ {weight:.1f}"
                        for level, weight in stability_presets.items()
                    )
                )

        stability_weight = (
            float(manual_stability_weight)
            if use_manual_stability_weight
            else float(stability_presets[selected_stability_level])
        )
        stability_profile_label = (
            "Avançada"
            if use_manual_stability_weight
            else selected_stability_level
        )
        st.caption(
            "A estabilidade compara apenas atividades futuras que já existiam no "
            "plano anterior. Novo escopo descoberto não recebe penalidade por não "
            "possuir início de referência."
        )

empty_state = ExecutionState(current_time=0)
baseline_activation = resolve_activation(planned_project, empty_state)
baseline_tasks = [
    task
    for task in planned_project.tasks
    if task.id in baseline_activation.active_ids
]

base_baseline = None
base_baseline_error = None
approved_schedule_in_use = False

baseline_active_ids = {
    task.id
    for task in baseline_tasks
}

if (
    approved_planning_baseline is not None
    and not workforce.enabled
    and approved_schedule_matches_project(
        approved_planning_baseline,
        planned_project,
        baseline_active_ids,
    )
):
    base_baseline = approved_schedule_to_advanced(
        approved_planning_baseline,
        planned_project,
        baseline_active_ids,
    )
    approved_schedule_in_use = True
else:
    try:
        base_baseline = solve_mrcpsp(
            baseline_tasks,
            planned_project.capacities,
            deadline=planned_project.deadline,
            workforce=workforce,
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
        workforce=workforce,
    )
except ValueError as exc:
    scenario_baseline_error = str(exc)

if scenario_baseline is None:
    st.error(
        "O cenário ainda não consegue programar o escopo-base. "
        f"Ajuste as capacidades acima. Diagnóstico: {scenario_baseline_error}"
    )
    st.stop()

# O estado da parada parte do cronograma aprovado quando ele continua
# compatível com o escopo-base. Se regras/modos ou multi-skill exigirem uma
# reconstrução, o MRCPSP gera a linha de execução, mas a estabilidade continua
# comparada contra os horários aprovados no Planejamento.
baseline = base_baseline or scenario_baseline

original_baseline_makespan = (
    float(approved_planning_baseline.makespan_h)
    if approved_planning_baseline is not None
    else float(baseline.makespan)
)
governing_baseline_makespan = (
    float(governing_baseline_revision.makespan_h)
    if governing_baseline_revision is not None
    else original_baseline_makespan
)
governing_baseline_label = (
    governing_baseline_revision.label
    if governing_baseline_revision is not None
    else "Original"
)
formalized_baseline_change = (
    governing_baseline_makespan - original_baseline_makespan
)

with operation_tab:
    st.markdown("### Acompanhar a execução")
    st.caption(
        "Avance a parada, registre apenas o que mudou e acompanhe o efeito sobre a janela."
    )

    if approved_planning_baseline is not None:
        if approved_schedule_in_use:
            status(
                (
                    "A execução está ancorada exatamente no cronograma RCPSP "
                    f"aprovado no Planejamento ({approved_planning_baseline.key[:12]})."
                ),
                tone="ok",
                title="Continuidade do baseline preservada.",
            )
        else:
            status(
                (
                    "O baseline aprovado continua sendo a referência de estabilidade, "
                    "mas a linha operacional precisou ser reconstruída pelo MRCPSP "
                    "porque regras/modos ou o multi-skill alteraram o modelo executável."
                ),
                tone="warn",
                title="Baseline aprovado adaptado ao modelo avançado.",
            )
    elif base_baseline is None:
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
        "2",
        "Onde estamos na parada",
        "Informe a hora corrente; atividades concluídas ou em andamento ficam congeladas no replanejamento.",
    )

    if governing_baseline_revision is not None:
        reference_start_times = (
            governing_baseline_revision.reference_start_times()
        )
    elif approved_planning_baseline is not None:
        approved_reference_start_times = (
            approved_planning_baseline.reference_start_times()
        )
        reference_start_times = {
            task_id: start
            for task_id, start in approved_reference_start_times.items()
            if task_id in baseline_active_ids
        }
    else:
        reference_start_times = {
            item.task_id: float(item.start)
            for item in baseline.tasks
        }
    persisted_current_time = float(execution_session.current_time)
    if persisted_current_time > 0:
        default_current_time = persisted_current_time
    elif project_source_kind == "demo":
        default_current_time = min(7.0, float(baseline.makespan))
    else:
        default_current_time = 0.0
    latest_progress_import = execution_store.latest_progress_import(
        execution_session.id
    )
    with st.expander(
        "Importar progresso em lote",
        expanded=False,
    ):
        st.caption(
            "Aceita Microsoft Project XML, Excel ou CSV. Actual Start/Actual Finish "
            "em data/hora são convertidos para H+ usando a origem do baseline; "
            "também são aceitas colunas H+ diretas. Remaining Duration representa "
            "horas de trabalho restantes da atividade em andamento e alimenta seu "
            "forecast operacional sem usar % concluído como duração."
        )
        progress_file = st.file_uploader(
            "Arquivo de progresso",
            type=["xml", "xlsx", "xls", "csv"],
            key=f"progress_import_{execution_session.id[:8]}",
        )
        progress_result = None
        if progress_file is not None:
            try:
                imported_rows = parse_progress_file(
                    progress_file,
                    calendar_origin=calendar_origin,
                )
                progress_result = reconcile_progress(
                    imported_rows,
                    project,
                )
                preview_df = pd.DataFrame(
                    [
                        {
                            "ID": row.task_id,
                            "UID Project": row.project_uid or "—",
                            "Atividade": row.task_name,
                            "% concluído": row.percent_complete,
                            "Status": row.status,
                            "Actual Start (H+)": row.actual_start_h,
                            "Actual Finish (H+)": row.actual_finish_h,
                            "Remaining Duration (h)": row.remaining_duration_h,
                            "Referência Remaining (H+)": row.remaining_as_of_h,
                            "Conciliação": row.source_reference,
                        }
                        for row in progress_result.rows
                    ]
                )
                st.dataframe(
                    preview_df,
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "% concluído": st.column_config.NumberColumn(
                            "% concluído",
                            format="%.1f",
                        )
                    },
                )
                if progress_result.warnings:
                    for warning in progress_result.warnings:
                        st.warning(warning)
                st.caption(
                    f"{progress_result.matched_rows} de "
                    f"{progress_result.source_rows} linha(s) conciliada(s)."
                )

                if st.button(
                    "Registrar importação de progresso",
                    type="primary",
                    key=f"save_progress_import_{execution_session.id[:8]}",
                ):
                    execution_store.record_progress_import(
                        execution_session.id,
                        source_name=progress_file.name,
                        rows=[
                            {
                                "task_id": row.task_id,
                                "project_uid": row.project_uid,
                                "task_name": row.task_name,
                                "percent_complete": row.percent_complete,
                                "status": row.status,
                                "actual_start_h": row.actual_start_h,
                                "actual_finish_h": row.actual_finish_h,
                                "remaining_duration_h": row.remaining_duration_h,
                                "remaining_as_of_h": row.remaining_as_of_h,
                                "source_reference": row.source_reference,
                            }
                            for row in progress_result.rows
                        ],
                        warnings=list(progress_result.warnings),
                    )
                    st.success(
                        "Progresso importado e registrado no histórico da sessão."
                    )
                    st.rerun()
            except (TypeError, ValueError) as exc:
                st.error(f"Não foi possível importar o progresso: {exc}")

        if latest_progress_import is not None:
            latest_payload = latest_progress_import.payload
            st.divider()
            st.caption(
                "Última importação registrada: "
                f"{latest_payload.get('source_name', 'arquivo')} · "
                f"{latest_payload.get('matched_rows', 0)} atividade(s) conciliada(s)."
            )
            latest_rows = latest_payload.get("rows", [])
            if latest_rows:
                st.dataframe(
                    pd.DataFrame(
                        [
                            {
                                "ID": row.get("task_id"),
                                "Atividade": row.get("task_name"),
                                "% concluído": row.get("percent_complete"),
                                "Status": row.get("status"),
                                "Actual Start (H+)": row.get("actual_start_h"),
                                "Actual Finish (H+)": row.get("actual_finish_h"),
                                "Remaining Duration (h)": row.get("remaining_duration_h"),
                                "Referência Remaining (H+)": row.get("remaining_as_of_h"),
                            }
                            for row in latest_rows
                        ]
                    ),
                    use_container_width=True,
                    hide_index=True,
                )
            actual_completed = sum(
                1
                for row in latest_rows
                if (
                    row.get("status") == "completed"
                    and row.get("actual_start_h") is not None
                    and row.get("actual_finish_h") is not None
                )
            )
            actual_in_progress = sum(
                1
                for row in latest_rows
                if (
                    row.get("status") == "in_progress"
                    and row.get("actual_start_h") is not None
                )
            )
            in_progress_with_remaining = sum(
                1
                for row in latest_rows
                if (
                    row.get("status") == "in_progress"
                    and row.get("actual_start_h") is not None
                    and row.get("remaining_duration_h") is not None
                )
            )
            if actual_completed:
                st.success(
                    f"{actual_completed} atividade(s) concluída(s) possuem Actual "
                    "Start/Finish e serão congeladas pelo realizado."
                )
            if in_progress_with_remaining:
                st.success(
                    f"{in_progress_with_remaining} atividade(s) em andamento possuem "
                    "Actual Start + Remaining Duration e terão forecast congelado "
                    "a partir da referência informada."
                )
            elif actual_in_progress:
                st.info(
                    f"{actual_in_progress} atividade(s) em andamento possuem Actual "
                    "Start, mas ainda não têm Remaining Duration; ficam auditadas "
                    "sem término previsto inventado."
                )

    latest_progress_rows = (
        latest_progress_import.payload.get("rows", [])
        if latest_progress_import is not None
        else []
    )
    actual_time_values = [
        float(value)
        for row in latest_progress_rows
        for value in (
            row.get("actual_start_h"),
            row.get("actual_finish_h"),
            row.get("remaining_as_of_h"),
        )
        if value is not None
    ]
    actual_progress_authoritative = bool(actual_time_values)
    latest_actual_h = max(actual_time_values, default=0.0)
    default_current_time = max(
        float(default_current_time),
        float(latest_actual_h),
    )

    current_time = st.number_input(
        "Hora corrente desde o início da parada",
        min_value=float(latest_actual_h if actual_progress_authoritative else 0.0),
        value=float(default_current_time),
        step=0.5,
        key=f"current_time_{execution_session.id[:8]}",
        help=(
            "Quando existem Actuals importados, a hora corrente não pode anteceder "
            "o último registro real."
        ),
    )

    execution_reference_items = (
        governing_baseline_revision.schedule_items()
        if governing_baseline_revision is not None
        else baseline.tasks
    )
    reference_item_by_id = {
        item.task_id: item
        for item in execution_reference_items
    }
    executions: dict[str, TaskExecution] = {}

    if actual_progress_authoritative:
        progress_state_errors: list[str] = []
        for row in latest_progress_rows:
            task_id = str(row.get("task_id"))
            status_value = row.get("status")
            actual_start = row.get("actual_start_h")
            actual_finish = row.get("actual_finish_h")
            remaining_duration = row.get("remaining_duration_h")
            remaining_as_of = row.get("remaining_as_of_h")
            reference_item = reference_item_by_id.get(task_id)
            mode_name = (
                reference_item.mode_name
                if reference_item is not None
                else None
            )
            assignments = (
                {
                    skill: list(person_ids)
                    for skill, person_ids
                    in reference_item.skill_assignments.items()
                }
                if reference_item is not None
                else {}
            )

            if status_value == "completed":
                if actual_start is None or actual_finish is None:
                    continue
                executions[task_id] = TaskExecution(
                    status="completed",
                    start=float(actual_start),
                    finish=float(actual_finish),
                    mode_name=mode_name,
                    skill_assignments=assignments,
                )
                continue

            if status_value != "in_progress":
                continue
            if actual_start is None or remaining_duration is None:
                continue

            forecast_anchor = (
                float(remaining_as_of)
                if remaining_as_of is not None
                else float(current_time)
            )
            try:
                forecast_finish = forecast_remaining_finish(
                    project,
                    task_id=task_id,
                    remaining_duration_h=float(remaining_duration),
                    from_h=forecast_anchor,
                    mode_name=mode_name,
                )
                if forecast_finish <= current_time + 1e-9:
                    raise ValueError(
                        f"forecast H+{forecast_finish:g} não é posterior à hora "
                        f"corrente H+{current_time:g}; atualize o progresso."
                    )
                executions[task_id] = TaskExecution(
                    status="in_progress",
                    start=float(actual_start),
                    finish=float(forecast_finish),
                    mode_name=mode_name,
                    skill_assignments=assignments,
                )
            except ValueError as exc:
                progress_state_errors.append(
                    f"{task_id} · {row.get('task_name', 'atividade')}: {exc}"
                )

        if progress_state_errors:
            status(
                (
                    "O progresso importado não forma um estado operacional "
                    "calendar-aware válido. Corrija os dados ou ajuste "
                    "calendário/overtime antes de replanejar."
                ),
                tone="danger",
                title="Remaining Duration inconsistente.",
            )
            for message in progress_state_errors:
                st.error(message)
            st.stop()

        st.caption(
            "Estado operacional orientado pelos Actuals importados. Atividades "
            "concluídas usam Actual Start/Finish; atividades em andamento usam "
            "Actual Start + Remaining Duration. Itens sem dados reais suficientes "
            "não são inferidos como realizados apenas pelo relógio."
        )
    else:
        for item in execution_reference_items:
            if item.finish <= current_time + 1e-9:
                executions[item.task_id] = TaskExecution(
                    status="completed",
                    start=item.start,
                    finish=item.finish,
                    mode_name=item.mode_name,
                    skill_assignments={
                        skill: list(person_ids)
                        for skill, person_ids in item.skill_assignments.items()
                    },
                )
            elif item.start < current_time < item.finish:
                executions[item.task_id] = TaskExecution(
                    status="in_progress",
                    start=item.start,
                    finish=item.finish,
                    mode_name=item.mode_name,
                    skill_assignments={
                        skill: list(person_ids)
                        for skill, person_ids in item.skill_assignments.items()
                    },
                )
        st.caption(
            "Sem Actuals importados, a tela mantém o modo de simulação legado: "
            "o estado é inferido pela posição da hora corrente no cronograma."
        )

    st.markdown("#### Mudanças de escopo")

    completed_ids = [
        task_id
        for task_id, execution in executions.items()
        if execution.status == "completed"
    ]
    task_labels = {
        task.id: f"{task.id} · {task.name}"
        for task in project.tasks
    }
    future_task_ids = [
        task.id
        for task in project.tasks
        if task.id not in completed_ids
    ]

    with st.expander(
        "Registrar atividade não prevista no cronograma",
        expanded=False,
    ):
        st.caption(
            "Use isto quando o trabalho descoberto não existia no XML nem no sidecar. "
            "A atividade entra como escopo obrigatório e pode bloquear atividades futuras."
        )

        next_dynamic_id = next_discovered_task_id(
            planned_project,
            discovered_tasks,
        )
        d1, d2, d3 = st.columns([2, 1, 1])
        with d1:
            dynamic_name = st.text_input(
                "Nova atividade",
                key="dynamic_scope_name",
                placeholder="Ex.: Reparar trinca encontrada na carcaça",
            )
        with d2:
            dynamic_duration = st.number_input(
                "Duração (h)",
                min_value=0.5,
                value=4.0,
                step=0.5,
                key="dynamic_scope_duration",
            )
        with d3:
            dynamic_cost = st.number_input(
                "Custo do modo",
                min_value=0.0,
                value=0.0,
                step=100.0,
                key="dynamic_scope_cost",
            )

        dynamic_resources = st.text_input(
            "Recursos demandados",
            key="dynamic_scope_resources",
            placeholder="Soldador=1; Mecânica=2; Guindaste=1",
            help=(
                "Recursos novos são aceitos. Após salvar, a página recarrega e cria "
                "automaticamente o slider de capacidade correspondente."
            ),
        )

        source_options = [None] + completed_ids
        dynamic_source_task = st.selectbox(
            "Atividade onde o achado foi identificado",
            options=source_options,
            format_func=lambda task_id: (
                "— origem não informada —"
                if task_id is None
                else task_labels.get(task_id, task_id)
            ),
            key="dynamic_scope_source",
        )
        dynamic_source_event = st.text_input(
            "Achado / evento de origem",
            key="dynamic_scope_source_event",
            placeholder="Ex.: crack_detected",
            disabled=dynamic_source_task is None,
        )

        dynamic_predecessors = st.multiselect(
            "Predecessoras adicionais",
            options=list(task_labels),
            format_func=lambda task_id: task_labels[task_id],
            key="dynamic_scope_predecessors",
            help=(
                "A atividade de origem, quando informada, é incluída automaticamente "
                "como predecessora FS."
            ),
        )
        dynamic_successors = st.multiselect(
            "Atividades futuras que esta descoberta deve bloquear",
            options=future_task_ids,
            format_func=lambda task_id: task_labels[task_id],
            key="dynamic_scope_successors",
            help=(
                "Será injetada uma precedência FS da nova atividade para cada sucessora. "
                "Ex.: o fechamento só começa após o reparo descoberto."
            ),
        )
        dynamic_notes = st.text_area(
            "Observação",
            key="dynamic_scope_notes",
            placeholder="Contexto técnico do achado, evidência ou decisão que criou o novo trabalho.",
        )

        if st.button(
            f"Adicionar {next_dynamic_id} ao escopo",
            type="primary",
            key="dynamic_scope_add",
        ):
            try:
                if not dynamic_name.strip():
                    raise ValueError("Informe o nome da nova atividade")

                resources = _parse_resource_demands(dynamic_resources)
                predecessor_ids = list(dynamic_predecessors)
                if (
                    dynamic_source_task is not None
                    and dynamic_source_task not in predecessor_ids
                ):
                    predecessor_ids.append(dynamic_source_task)

                discovered = DiscoveredTask(
                    id=next_dynamic_id,
                    name=dynamic_name.strip(),
                    discovered_at=float(current_time),
                    source_task_id=dynamic_source_task,
                    source_event=(
                        dynamic_source_event.strip()
                        if dynamic_source_task is not None
                        and dynamic_source_event.strip()
                        else None
                    ),
                    wbs=f"DS.{len(discovered_tasks) + 1}",
                    modes=[
                        ExecutionMode(
                            name="campo",
                            duration=float(dynamic_duration),
                            resources=resources,
                            cost=float(dynamic_cost),
                        )
                    ],
                    precedences=[
                        Precedence(
                            predecessor_id=task_id,
                            relation="FS",
                            lag=0.0,
                        )
                        for task_id in predecessor_ids
                    ],
                    successor_task_ids=list(dynamic_successors),
                    notes=dynamic_notes.strip() or None,
                )

                candidate = [*discovered_tasks, discovered]
                # Valida colisões, referências e gates antes de persistir.
                materialize_dynamic_scope(planned_project, candidate)
                execution_store.add_discovered_task(
                    execution_session.id,
                    discovered,
                )
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))

    if discovered_tasks:
        st.markdown("##### Escopo descoberto em execução")
        discovered_rows = []
        for item in discovered_tasks:
            discovered_rows.append(
                {
                    "ID": item.id,
                    "Atividade": item.name,
                    "Descoberta em (h)": item.discovered_at,
                    "Origem": (
                        task_labels.get(item.source_task_id, item.source_task_id)
                        if item.source_task_id
                        else "—"
                    ),
                    "Achado": item.source_event or "—",
                    "Duração (h)": item.modes[0].duration,
                    "Recursos": ", ".join(
                        f"{resource}:{value:g}"
                        for resource, value in item.modes[0].resources.items()
                    ) or "—",
                    "Bloqueia": ", ".join(
                        task_labels.get(task_id, task_id)
                        for task_id in item.successor_task_ids
                    ) or "—",
                }
            )
        st.dataframe(
            pd.DataFrame(discovered_rows),
            use_container_width=True,
            hide_index=True,
        )

        remove_id = st.selectbox(
            "Reverter descoberta ainda não iniciada",
            options=[None] + [item.id for item in discovered_tasks],
            format_func=lambda task_id: (
                "— selecionar —"
                if task_id is None
                else task_labels.get(task_id, task_id)
            ),
            key="dynamic_scope_remove_id",
        )
        if remove_id and st.button(
            "Remover do escopo dinâmico",
            key="dynamic_scope_remove",
        ):
            candidate = [
                item
                for item in discovered_tasks
                if item.id != remove_id
            ]
            try:
                materialize_dynamic_scope(planned_project, candidate)
                execution_store.remove_discovered_task(
                    execution_session.id,
                    remove_id,
                )
                st.rerun()
            except ValueError as exc:
                st.error(
                    "Não é possível remover esta descoberta porque outra atividade "
                    f"dinâmica ainda depende dela: {exc}"
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

    # Eventos já registrados permanecem no estado mesmo quando o controle não
    # precisa ser exibido. A UI mostra somente gatilhos que podem ser usados agora.
    events: dict[str, list[str]] = {
        source_id: [
            event_name
            for event_name in execution_session.observed_events.get(source_id, [])
            if event_name in options
        ]
        for source_id, options in event_catalog.items()
        if any(
            event_name in options
            for event_name in execution_session.observed_events.get(source_id, [])
        )
    }
    actionable_event_sources = [
        source_id
        for source_id in event_catalog
        if (
            (
                source_id in executions
                and executions[source_id].status == "completed"
            )
            or source_id in events
        )
    ]

    if actionable_event_sources:
        with st.expander(
            f"Registrar achados ({len(actionable_event_sources)} gatilho(s) disponível(is))",
            expanded=True,
        ):
            for source_id in sorted(actionable_event_sources):
                options = event_catalog[source_id]
                completed = (
                    source_id in executions
                    and executions[source_id].status == "completed"
                )
                selected = st.multiselect(
                    name_by_id.get(source_id, source_id),
                    options=sorted(options),
                    default=events.get(source_id, []),
                    disabled=not completed,
                    key=f"events_{execution_session.id[:8]}_{source_id}",
                )
                if selected:
                    events[source_id] = selected
                else:
                    events.pop(source_id, None)

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
        with st.expander("Escopo opcional manual", expanded=False):
            selected_optional_ids = st.multiselect(
                "Atividades opcionais selecionadas",
                options=list(labels),
                default=[
                    task_id
                    for task_id in execution_session.selected_optional_ids
                    if task_id in labels
                ],
                format_func=lambda task_id: labels[task_id],
                key=f"optional_{execution_session.id[:8]}",
            )

    known_groups = {group.id: group for group in project.logical_groups}
    stored_human_selections = {
        group_id: [
            task_id
            for task_id in selection
            if (
                group_id in known_groups
                and task_id in known_groups[group_id].member_task_ids
            )
        ]
        for group_id, selection in execution_session.human_selections.items()
        if group_id in known_groups
    }
    stored_human_selections = {
        group_id: selection
        for group_id, selection in stored_human_selections.items()
        if selection
    }
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
        workforce=workforce,
    )

    if decision_engine.pending_human:
        st.markdown("#### Decisão de escopo necessária")
        st.caption(
            "Compare as consequências abaixo. A ferramenta informa impacto e factibilidade; a escolha técnica continua humana."
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

    if stored_human_selections:
        if st.button("Reabrir decisões humanas desta sessão"):
            execution_store.update_execution_state(
                execution_session.id,
                current_time=float(current_time),
                observed_events=events,
                human_selections={},
                selected_optional_ids=selected_optional_ids,
                scenario_capacities=scenario_capacities,
                stability_weight=float(stability_weight),
            )
            for key in list(st.session_state):
                if key.startswith("scope_decision_"):
                    del st.session_state[key]
            st.rerun()

    # Persiste o estado operacional da sessão antes do replanejamento.
    execution_session = execution_store.update_execution_state(
        execution_session.id,
        current_time=float(current_time),
        observed_events=events,
        human_selections=stored_human_selections,
        selected_optional_ids=selected_optional_ids,
        scenario_capacities=scenario_capacities,
        stability_weight=float(stability_weight),
    )

    # Reexecuta o motor após eventuais escolhas humanas. Ele pode aplicar regras
    # event-driven determinísticas que tenham sido liberadas pela decisão recém tomada.
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
        workforce=workforce,
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
            workforce=workforce,
        )
    except ValueError as exc:
        base_result_error = str(exc)

    try:
        result = reschedule_from_state(
            project,
            state,
            reference_start_times=reference_start_times,
            stability_weight=stability_weight,
            workforce=workforce,
        )
    except ValueError as exc:
        st.error(f"Cenário de recursos inviável: {exc}")
        if base_result_error:
            st.caption(f"Com os recursos-base também é inviável: {base_result_error}")
        st.stop()

    pending_decision_rows = summarize_pending_decisions(
        decision_engine.pending_human,
        current_makespan=float(result.schedule.makespan),
        current_cost=float(result.schedule.total_cost),
    )
    pending_decisions_df = pd.DataFrame(pending_decision_rows)

    active_now = result.activation.active_ids
    new_scope = active_now - baseline_activation.active_ids

    activation_df = pd.DataFrame(
        [
            {
                "ID": task.id,
                "UID Project": task.project_uid or "—",
                "Atividade": task.name,
                "Origem": (
                    "dynamic discovery"
                    if task.id in dynamic_scope_ids
                    else "planejamento / regra"
                ),
                "Tipo": task.activation.kind,
                "Estado": result.activation.states[task.id].value,
                "Motivo": result.activation.reasons[task.id],
            }
            for task in project.tasks
        ]
    )

    all_items = result.frozen_tasks + result.schedule.tasks
    resource_usage = _resource_usage_snapshot(
        all_items,
        project.capacities,
        float(result.schedule.makespan),
    )
    if not resource_scenario_df.empty:
        resource_scenario_df = resource_scenario_df.copy()
        resource_scenario_df["Pico"] = resource_scenario_df["Recurso"].map(
            lambda resource: resource_usage.get(resource, {}).get("peak", 0.0)
        )
        resource_scenario_df["Utilizacao_%"] = resource_scenario_df["Recurso"].map(
            lambda resource: round(
                resource_usage.get(resource, {}).get("utilization", 0.0) * 100,
                1,
            )
        )
        resource_scenario_df["Folga no pico"] = resource_scenario_df["Recurso"].map(
            lambda resource: resource_usage.get(resource, {}).get(
                "peak_slack",
                float(project.capacities.get(resource, 0.0)),
            )
        )

    with resources_tab:
        st.markdown("#### Resultado do cenário")
        st.caption(
            "Pico e utilização são calculados sobre o mesmo snapshot MRCPSP "
            "mostrado em Operação e no relatório."
        )
        st.dataframe(
            resource_scenario_df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Utilizacao_%": st.column_config.NumberColumn(
                    "Utilização (%)",
                    format="%.1f",
                ),
                "Pico": st.column_config.NumberColumn("Pico", format="%.1f"),
                "Folga no pico": st.column_config.NumberColumn(
                    "Folga no pico",
                    format="%.1f",
                ),
            },
        )
        constrained_resources = resource_scenario_df[
            resource_scenario_df["Folga no pico"] <= 1e-9
        ] if not resource_scenario_df.empty else resource_scenario_df
        if not constrained_resources.empty:
            st.caption(
                "Recursos que atingem a capacidade no pico: "
                + ", ".join(constrained_resources["Recurso"].astype(str).tolist())
            )

    person_name_by_id = {
        person.id: person.name
        for person in workforce.people
    }
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
                "Origem": (
                    "dynamic discovery"
                    if str(item.task_id) in dynamic_scope_ids
                    else "planejamento / regra"
                ),
                "Modo": item.mode_name,
                "Início (h)": item.start,
                "Fim (h)": item.finish,
                "Duração (h)": item.duration,
                "Δ início vs plano (h)": (
                    item.start - reference_start_times[item.task_id]
                    if item.task_id in reference_start_times
                    else None
                ),
                "Congelada": item.fixed,
                "Crítica atual": str(item.task_id) in criticality.critical_ids,
                "Controla por": criticality.reasons.get(str(item.task_id), ""),
                "Recursos": ", ".join(
                    f"{key}:{value:g}"
                    for key, value in item.resources.items()
                ),
                "Pessoas": "; ".join(
                    (
                        f"{skill}: "
                        + ", ".join(
                            person_name_by_id.get(person_id, person_id)
                            for person_id in person_ids
                        )
                    )
                    for skill, person_ids in item.skill_assignments.items()
                ) or "—",
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

    # Snapshot único usado pela tela e pelo relatório gerencial.
    # Se este invariante falhar, o PDF não é gerado: ele nunca deve representar
    # um estado diferente do cronograma que o usuário está vendo.
    snapshot_schedule_finish = max(
        [float(item.finish) for item in all_items],
        default=float(current_time),
    )
    if abs(snapshot_schedule_finish - float(result.schedule.makespan)) > 1e-6:
        st.error(
            "Inconsistência interna: o makespan exibido não coincide com o maior "
            "fim do cronograma atual. O relatório foi bloqueado para evitar um "
            "snapshot incorreto."
        )
        st.stop()

    dynamic_scope_impacts = evaluate_dynamic_scope_impacts(
        planned_project=planned_project,
        discovered_tasks=discovered_tasks,
        state=state,
        capacities=project.capacities,
        current_makespan=float(result.schedule.makespan),
        critical_ids=criticality.critical_ids,
        reference_start_times=reference_start_times,
        stability_weight=stability_weight,
        workforce=workforce,
    )
    dynamic_scope_impact_by_id = {
        str(row["id"]): row
        for row in dynamic_scope_impacts
    }
    dynamic_scope_report_df = pd.DataFrame(
        [
            {
                "ID": item.id,
                "Atividade": item.name,
                "Descoberta em (h)": float(item.discovered_at),
                "Impacto no término": (
                    "não isolável"
                    if dynamic_scope_impact_by_id[item.id]["impact_h"] is None
                    else f"{float(dynamic_scope_impact_by_id[item.id]['impact_h']):+.1f} h"
                ),
                "Recursos": "; ".join(
                    f"{resource}={demand:g}"
                    for mode in item.modes
                    for resource, demand in sorted(mode.resources.items())
                ) or "—",
                "Bloqueia": ", ".join(
                    name_by_id.get(task_id, task_id)
                    for task_id in item.successor_task_ids
                ) or "—",
                "Crítica atual": (
                    "sim"
                    if item.id in criticality.critical_ids
                    else "não"
                ),
            }
            for item in discovered_tasks
            if item.id in dynamic_scope_impact_by_id
        ]
    )

    observed_events_label = "; ".join(
        f"{task_id}: {', '.join(values)}"
        for task_id, values in sorted(events.items())
        if values
    ) or "—"
    human_decisions_label = "; ".join(
        f"{group_id}: {', '.join(selection)}"
        for group_id, selection in sorted(stored_human_selections.items())
        if selection
    ) or "—"
    auto_rules_label = "; ".join(
        f"{group_id}: {', '.join(selection)}"
        for group_id, selection in sorted(decision_engine.auto_resolved.items())
        if selection
    ) or "—"
    optional_label = ", ".join(sorted(selected_optional_ids)) or "—"

    snapshot_payload = {
        "session_id": execution_session.id,
        "project_key": project_key,
        "current_time": float(current_time),
        "scenario_capacities": {
            key: float(value)
            for key, value in sorted(scenario_capacities.items())
        },
        "stability_weight": float(stability_weight),
        "workforce": workforce.model_dump(mode="json"),
        "events": {
            key: sorted(value)
            for key, value in sorted(events.items())
            if value
        },
        "selected_optional_ids": sorted(selected_optional_ids),
        "human_selections": {
            key: sorted(value)
            for key, value in sorted(stored_human_selections.items())
            if value
        },
        "auto_resolved": {
            key: sorted(value)
            for key, value in sorted(decision_engine.auto_resolved.items())
            if value
        },
        "pending_decisions": pending_decision_rows,
        "dynamic_scope": [
            item.model_dump(mode="json")
            for item in discovered_tasks
        ],
        "spreadsheet_scope_rules": [
            row.model_dump(mode="json")
            for row in stored_scope_rules
        ],
        "result": {
            "makespan": float(result.schedule.makespan),
            "tardiness": float(result.schedule.tardiness),
            "total_cost": float(result.schedule.total_cost),
            "strategy": result.schedule.strategy,
            "tasks": [
                {
                    "id": str(item.task_id),
                    "mode": item.mode_name,
                    "start": float(item.start),
                    "finish": float(item.finish),
                    "fixed": bool(item.fixed),
                    "skill_assignments": {
                        skill: list(person_ids)
                        for skill, person_ids in item.skill_assignments.items()
                    },
                }
                for item in all_items
            ],
        },
    }
    report_snapshot_id = hashlib.sha256(
        json.dumps(
            snapshot_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()[:12]

    baseline_history_rows = [
        {
            "Linha de base": "Original",
            "Anterior": "—",
            "Makespan (h)": original_baseline_makespan,
            "Janela (h)": (
                original_deadline
                if original_deadline is not None
                else "—"
            ),
            "Δ vs anterior (h)": 0.0,
            "Δ vs original (h)": 0.0,
            "Aprovada em": (
                approved_planning_baseline.approved_at.astimezone().strftime(
                    "%d/%m/%Y %H:%M"
                )
                if approved_planning_baseline is not None
                else "importação"
            ),
            "Aprovado por": (
                approved_planning_baseline.approved_by
                if approved_planning_baseline is not None
                and approved_planning_baseline.approved_by
                else "—"
            ),
            "Motivo": (
                approved_planning_baseline.approval_reason
                if approved_planning_baseline is not None
                and approved_planning_baseline.approval_reason
                else (
                    "Plano aprovado na guia Planejamento"
                    if approved_planning_baseline is not None
                    else "Baseline importado diretamente"
                )
            ),
            "Snapshot": (
                approved_planning_baseline.key[:12]
                if approved_planning_baseline is not None
                else "—"
            ),
            "Observação": "—",
        }
    ]
    previous_label_for_history = "Original"
    previous_makespan_for_history = original_baseline_makespan
    previous_deadline_for_history = original_deadline
    for revision in baseline_revisions:
        previous_label = (
            revision.previous_baseline_label
            or previous_label_for_history
        )
        previous_makespan = (
            float(revision.previous_makespan_h)
            if revision.previous_makespan_h is not None
            else float(previous_makespan_for_history)
        )
        delta_vs_previous = (
            float(revision.delta_vs_previous_h)
            if revision.delta_vs_previous_h is not None
            else float(revision.makespan_h) - previous_makespan
        )
        delta_vs_original = (
            float(revision.delta_vs_original_h)
            if revision.delta_vs_original_h is not None
            else float(revision.makespan_h) - original_baseline_makespan
        )
        baseline_history_rows.append(
            {
                "Linha de base": revision.label,
                "Anterior": previous_label,
                "Makespan (h)": float(revision.makespan_h),
                "Janela (h)": (
                    float(revision.deadline_h)
                    if revision.deadline_h is not None
                    else "—"
                ),
                "Δ vs anterior (h)": delta_vs_previous,
                "Δ vs original (h)": delta_vs_original,
                "Aprovada em": revision.approved_at.astimezone().strftime(
                    "%d/%m/%Y %H:%M"
                ),
                "Aprovado por": revision.approved_by or "—",
                "Motivo": revision.reason,
                "Snapshot": revision.snapshot_id,
                "Observação": revision.notes or "—",
            }
        )
        previous_label_for_history = revision.label
        previous_makespan_for_history = float(revision.makespan_h)
        previous_deadline_for_history = revision.deadline_h

    baseline_revisions_df = pd.DataFrame(baseline_history_rows)

    execution_state_report_df = pd.DataFrame(
        [
            {"Parâmetro": "Snapshot", "Valor": report_snapshot_id},
            {"Parâmetro": "Sessão", "Valor": execution_session.id},
            {"Parâmetro": "Hora corrente", "Valor": f"{current_time:.1f} h"},
            {
                "Parâmetro": "Baseline original",
                "Valor": f"{original_baseline_makespan:.1f} h",
            },
            {
                "Parâmetro": "Baseline vigente",
                "Valor": (
                    f"{governing_baseline_label} · "
                    f"{governing_baseline_makespan:.1f} h"
                ),
            },
            {
                "Parâmetro": "Makespan reprogramado",
                "Valor": f"{result.schedule.makespan:.1f} h",
            },
            {
                "Parâmetro": "Δ vs original",
                "Valor": (
                    f"{result.schedule.makespan - original_baseline_makespan:+.1f} h"
                ),
            },
            {
                "Parâmetro": "Δ vs vigente",
                "Valor": (
                    f"{result.schedule.makespan - governing_baseline_makespan:+.1f} h"
                ),
            },
            {"Parâmetro": "Atraso", "Valor": f"{result.schedule.tardiness:.1f} h"},
            {"Parâmetro": "Preservação do plano", "Valor": stability_profile_label},
            {"Parâmetro": "Achados observados", "Valor": observed_events_label},
            {"Parâmetro": "Opcionais selecionadas", "Valor": optional_label},
            {"Parâmetro": "Decisões humanas", "Valor": human_decisions_label},
            {"Parâmetro": "Regras determinísticas", "Valor": auto_rules_label},
            {
                "Parâmetro": "Dynamic scope",
                "Valor": f"{len(dynamic_scope_ids)} atividade(s)",
            },
            {
                "Parâmetro": "Regras da planilha",
                "Valor": (
                    ", ".join(row.id for row in stored_scope_rules if row.enabled)
                    or "—"
                ),
            },
            {
                "Parâmetro": "Multi-skill",
                "Valor": (
                    f"{len(workforce.active_people)} pessoa(s) ativa(s) · "
                    f"{len(workforce.skills)} habilidade(s)"
                    if workforce.enabled
                    else "desativado"
                ),
            },
            {"Parâmetro": "Estratégia solver", "Valor": result.schedule.strategy},
        ]
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
        with st.expander(
            "Comparar recursos-base x cenário",
            expanded=False,
        ):
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

            with st.expander("Comparação completa das atividades futuras", expanded=False):
                st.dataframe(
                    mode_comparison_df,
                    use_container_width=True,
                    hide_index=True,
                )

            st.session_state.setdefault("mrcpsp_saved_scenarios", [])
            scenario_name = st.text_input(
                "Nome para salvar esta comparação",
                value=str(scenario_name),
                key="mrcpsp_scenario_name",
            )
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
                            "Δ início acumulado (h)": result.schedule.total_start_deviation,
                            "Maior Δ início (h)": result.schedule.max_start_deviation,
                            "Preservação do plano": stability_profile_label,
                            "Dynamic scope": len(dynamic_scope_ids),
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

    # A aba Visão é deliberadamente somente leitura. Ela reutiliza o mesmo
    # snapshot calculado pela Operação para evitar divergência entre dashboard,
    # relatório e cronograma replanejado.
    vision_tab.markdown("### Visão executiva da parada")
    vision_tab.caption(
        "Leitura gerencial do snapshot corrente: compromisso formal, forecast, "
        "janela, decisões e cadeia que controla o término. Sem controles técnicos."
    )

    vision_impact_vs_original = (
        float(result.schedule.makespan) - float(original_baseline_makespan)
    )
    vision_impact_vs_governing = (
        float(result.schedule.makespan) - float(governing_baseline_makespan)
    )
    v1, v2, v3 = vision_tab.columns(3)
    v1.metric(
        "Baseline original",
        f"{original_baseline_makespan:.1f} h",
    )
    v2.metric(
        "Baseline vigente",
        f"{governing_baseline_makespan:.1f} h",
        delta=governing_baseline_label,
        help="Baseline 0 ou a última Rev.n formalmente aprovada.",
    )
    v3.metric(
        "Forecast atual",
        f"{result.schedule.makespan:.1f} h",
    )

    v4, v5, v6, v7 = vision_tab.columns(4)
    v4.metric(
        "Δ vs original",
        f"{vision_impact_vs_original:+.1f} h",
    )
    v5.metric(
        "Δ vs vigente",
        f"{vision_impact_vs_governing:+.1f} h",
    )
    if project.deadline is None:
        vision_window_value = "sem deadline"
    elif result.schedule.makespan <= project.deadline:
        vision_window_value = (
            f"{project.deadline - result.schedule.makespan:.1f} h de folga"
        )
    else:
        vision_window_value = (
            f"{result.schedule.makespan - project.deadline:.1f} h acima"
        )
    v6.metric("Janela", vision_window_value)
    v7.metric(
        "Decisões pendentes",
        len(decision_engine.pending_human),
    )

    if project.deadline is None:
        vision_tab.warning(
            "A execução ainda não possui uma janela formal para comparar com o forecast."
        )
    elif result.schedule.makespan <= project.deadline:
        vision_tab.success(
            f"Forecast dentro da janela: {result.schedule.makespan:.1f} h "
            f"para {project.deadline:.1f} h disponíveis."
        )
    else:
        vision_tab.error(
            f"Forecast excede a janela em "
            f"{result.schedule.makespan - project.deadline:.1f} h."
        )

    if not pending_decisions_df.empty:
        vision_tab.markdown("#### Decisões pendentes")
        vision_tab.caption(
            "Impactos comparados contra o forecast atual. A ferramenta informa "
            "as consequências; a decisão técnica permanece humana."
        )
        vision_tab.dataframe(
            pending_decisions_df[
                [
                    "Decisão",
                    "Alternativas",
                    "Impacto prazo",
                    "Impacto custo",
                    "Recurso crítico",
                ]
            ],
            use_container_width=True,
            hide_index=True,
        )

    scope_col, chain_col = vision_tab.columns(2)
    active_change_names = [
        name_by_id.get(task_id, task_id)
        for task_id in sorted(new_scope)
    ]
    with scope_col:
        st.markdown("#### Novo escopo relevante")
        if not dynamic_scope_report_df.empty:
            st.dataframe(
                dynamic_scope_report_df[
                    [
                        "ID",
                        "Atividade",
                        "Descoberta em (h)",
                        "Impacto no término",
                        "Bloqueia",
                    ]
                ],
                use_container_width=True,
                hide_index=True,
            )
            st.caption(
                "Impacto marginal estimado por contrafactual individual no mesmo "
                "estado operacional. Dependências entre DS-* podem impedir isolamento."
            )
        elif active_change_names:
            st.write(
                f"{len(active_change_names)} atividade(s) adicional(is) ativa(s) "
                "neste snapshot, sem DS-* com impacto gerencial destacado."
            )
        else:
            st.caption("Nenhuma atividade adicional está ativa neste snapshot.")
        if dynamic_scope_ids:
            st.caption(
                f"{len(dynamic_scope_ids)} atividade(s) DS-* registrada(s) na sessão."
            )

    with chain_col:
        st.markdown("#### Cadeia controladora")
        if criticality.path_ids:
            st.write(
                " → ".join(
                    f"{task_id} · {name_by_id.get(task_id, task_id)}"
                    for task_id in criticality.path_ids
                )
            )
            branch_count = len(
                criticality.critical_ids - set(criticality.path_ids)
            )
            if branch_count:
                st.caption(
                    f"+ {branch_count} atividade(s) crítica(s) em ramificações "
                    "que também alimentam o término."
                )
        else:
            st.caption("Nenhuma cadeia controladora foi identificada neste snapshot.")

    vision_tab.caption(
        f"Hora corrente: {current_time:.1f} h · "
        f"snapshot {report_snapshot_id} · "
        "rebaseline formal continua exclusivo da aba Governança."
    )

    section(
        "3",
        "Impacto e decisão",
        "Situação da janela, mudança de escopo e cadeia que controla o término.",
    )

    tab_exec, tab_schedule, tab_export = st.tabs(
        [
            "Decisão",
            "Cronograma",
            "Relatório",
        ]
    )

    with tab_exec:
        impact_vs_original = (
            result.schedule.makespan - original_baseline_makespan
        )
        impact_vs_governing = (
            result.schedule.makespan - governing_baseline_makespan
        )
        if project.deadline is None:
            window_title = "Janela"
            window_value = "sem deadline"
        elif result.schedule.makespan <= project.deadline:
            window_title = "Folga da janela"
            window_value = f"{project.deadline - result.schedule.makespan:.1f} h"
        else:
            window_title = "Excesso da janela"
            window_value = f"{result.schedule.makespan - project.deadline:.1f} h"

        r1, r2, r3, r4 = st.columns(4)
        r1.metric(window_title, window_value)
        r2.metric(
            "Novo escopo ativo",
            len(new_scope),
            delta=(
                f"{len(dynamic_scope_ids)} dinâmico(s)"
                if dynamic_scope_ids
                else None
            ),
        )
        r3.metric(
            "Decisões pendentes",
            len(decision_engine.pending_human),
        )
        r4.metric(
            "Hora corrente",
            f"{current_time:.1f} h",
        )
        st.caption(
            "Baseline original, baseline vigente, forecast, deltas e rebaseline "
            "formal ficam concentrados na aba Governança."
        )

        with st.expander("Estabilidade, custo e diagnóstico do solver", expanded=False):
            sr1, sr2, sr3, sr4 = st.columns(4)
            sr1.metric(
                "Δ início acumulado",
                f"{result.schedule.total_start_deviation:.1f} h",
            )
            sr2.metric(
                "Maior Δ início",
                f"{result.schedule.max_start_deviation:.1f} h",
            )
            sr3.metric("Custo dos modos", f"{result.schedule.total_cost:,.0f}")
            sr4.metric(
                "Preservação do plano",
                stability_profile_label,
            )
            st.caption(
                f"{result.schedule.stability_compared_tasks} atividade(s) comparadas · "
                f"solver {result.schedule.strategy}."
            )

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

        st.caption(
            "A promoção formal de uma nova linha de base está disponível em Governança."
        )

        active_change_names = [
            name_by_id.get(task_id, task_id)
            for task_id in sorted(new_scope)
        ]
        if active_change_names:
            st.markdown("**O que mudou neste snapshot**")
            st.write(
                f"{len(active_change_names)} atividade(s) entraram no escopo ativo: "
                + ", ".join(active_change_names[:6])
                + ("…" if len(active_change_names) > 6 else "")
            )
        elif not decision_engine.pending_human:
            st.caption(
                "Nenhuma nova atividade foi ativada neste snapshot; o impacto atual vem do estado, recursos e modos de execução."
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

    with tab_schedule:
        st.dataframe(
            schedule_df.drop(columns=["_Ordem"], errors="ignore"),
            use_container_width=True,
            hide_index=True,
        )

        with st.expander("Rastrear ativação do escopo", expanded=False):
            st.dataframe(
                activation_df,
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
            baseline_makespan=original_baseline_makespan,
            current_makespan=float(result.schedule.makespan),
            governing_baseline_makespan=governing_baseline_makespan,
            governing_baseline_label=governing_baseline_label,
            deadline=(
                None
                if project.deadline is None
                else float(project.deadline)
            ),
            current_time=float(current_time),
            total_cost=float(result.schedule.total_cost),
            new_scope_count=len(new_scope),
            dynamic_scope_count=len(dynamic_scope_ids),
            strategy=result.schedule.strategy,
            activation_df=activation_df,
            schedule_df=schedule_df,
            total_start_deviation=float(result.schedule.total_start_deviation),
            max_start_deviation=float(result.schedule.max_start_deviation),
            stability_compared_tasks=int(result.schedule.stability_compared_tasks),
            stability_weight=float(stability_weight),
            critical_ids=criticality.critical_ids,
            critical_path_label=critical_path_label,
            snapshot_id=report_snapshot_id,
            session_id=execution_session.id,
            resource_scenario_df=resource_scenario_df,
            execution_state_df=execution_state_report_df,
            dynamic_scope_df=dynamic_scope_report_df,
            baseline_revisions_df=baseline_revisions_df,
            pending_decisions_df=pending_decisions_df,
        )

        st.info(
            f"PDF preparado com o snapshot **{report_snapshot_id}** · "
            f"makespan **{result.schedule.makespan:.1f} h**."
        )

        st.download_button(
            "Baixar relatório gerencial em PDF",
            data=pdf_bytes,
            file_name=(
                f"{project_name.lower().replace(' ', '_')}"
                f"_ms{result.schedule.makespan:.1f}".replace(".", "_")
                + f"_{report_snapshot_id}_scope_discovery.pdf"
            ),
            mime="application/pdf",
            type="primary",
            use_container_width=True,
            key=f"download_management_report_{report_snapshot_id}",
            on_click="ignore",
        )

        st.markdown("#### Cronograma operacional para Microsoft Project")
        st.caption(
            "Exporta o snapshot materializado em Microsoft Project XML (MSPDI). "
            "Entram atividades ativas, condicionais já disparadas, escopo DS-*, "
            "precedências efetivas, recursos, horários e calendários por recurso "
            "(turnos, indisponibilidades e overtime). Regras ainda dormentes não "
            "são materializadas."
        )
        if project.resource_calendars:
            st.info(
                "O XML leva os calendários usados pelo TDS e os horários calculados. "
                "A hipótese TDS de atividade não preemptiva é registrada nas Notas, "
                "mas não existe como regra nativa equivalente no MSPDI; portanto, "
                "alterações e novo recálculo dentro do Project podem produzir datas "
                "diferentes do snapshot exportado."
            )

        export_origin = calendar_origin
        if export_origin is None:
            e1, e2 = st.columns(2)
            with e1:
                export_date = st.date_input(
                    "Data de início para o arquivo Project",
                    key=f"project_export_date_{report_snapshot_id}",
                )
            with e2:
                export_time = st.time_input(
                    "Hora de início para o arquivo Project",
                    key=f"project_export_time_{report_snapshot_id}",
                )
            export_origin = datetime.combine(export_date, export_time)
            st.caption(
                "O arquivo de origem não trouxe uma data-base; esta data/hora "
                "será usada como âncora para converter as horas relativas."
            )
        else:
            st.caption(
                "Âncora de calendário herdada do planejamento: "
                f"{export_origin:%d/%m/%Y %H:%M}."
            )

        project_xml_bytes = build_project_xml(
            project_name=project_name,
            project=project,
            active_task_ids=set(result.activation.active_ids),
            schedule_items=all_items,
            effective_tasks=result.effective_tasks,
            state=state,
            calendar_origin=export_origin,
            dynamic_scope_ids=set(dynamic_scope_ids),
            activation_reasons=result.activation.reasons,
            snapshot_id=report_snapshot_id,
            original_baseline_items=list(baseline.tasks),
            baseline_revisions=baseline_revisions,
        )

        st.download_button(
            "Baixar cronograma replanejado para Microsoft Project (.xml)",
            data=project_xml_bytes,
            file_name=(
                f"{project_name.lower().replace(' ', '_')}"
                f"_{report_snapshot_id}_replanejado.xml"
            ),
            mime="application/xml",
            use_container_width=True,
            key=f"download_project_xml_{report_snapshot_id}",
            on_click="ignore",
        )

        st.markdown(
            """
            <div class="ta-note">
            O relatório registra a fotografia atual do replanejamento e inclui
            o identificador do snapshot, capacidades de recursos, achados e decisões
            que produziram o makespan exibido. Atividades já iniciadas ou concluídas
            permanecem congeladas; apenas o trabalho futuro é reprogramado.
            </div>
            """,
            unsafe_allow_html=True,
        )


with governance_tab:
    st.markdown("### Governança do cronograma")
    st.caption(
        "Separe o forecast operacional da referência formal. "
        "Replanejar não altera a linha de base até que uma Rev.n seja aprovada."
    )
    with st.expander("Ajuda · forecast, baseline vigente e rebaseline", expanded=False):
        st.markdown(
            """
- **Baseline original / Baseline 0:** compromisso formal inicial; não é apagado.
- **Baseline vigente:** Baseline 0 ou a última `Rev.n` formalmente aprovada.
- **Forecast atual:** melhor previsão com o estado, escopo e recursos atuais; pode mudar a cada replanejamento.
- **Replanejar não é rebaseline:** um forecast só vira `Rev.n` quando a mudança de compromisso é formalmente aprovada.
- Antes de promover, compare **Δ vs vigente** e **Δ vs Original** e registre aprovador, motivo e nova janela.
- Use **Auditar uma revisão** para reconstruir a cadeia de aprovação e o snapshot que originou cada compromisso.
            """
        )

    impact_vs_original = (
        result.schedule.makespan - original_baseline_makespan
    )
    impact_vs_governing = (
        result.schedule.makespan - governing_baseline_makespan
    )

    if project.deadline is None:
        governance_window_title = "Janela vigente"
        governance_window_value = "sem deadline"
    elif result.schedule.makespan <= project.deadline:
        governance_window_title = "Folga vs janela vigente"
        governance_window_value = (
            f"{project.deadline - result.schedule.makespan:.1f} h"
        )
    else:
        governance_window_title = "Excesso vs janela vigente"
        governance_window_value = (
            f"{result.schedule.makespan - project.deadline:.1f} h"
        )

    g1, g2, g3 = st.columns(3)
    g1.metric(
        "Baseline original",
        f"{original_baseline_makespan:.1f} h",
    )
    g2.metric(
        "Baseline vigente",
        f"{governing_baseline_makespan:.1f} h",
        delta=governing_baseline_label,
        help="Original ou a última Rev.n formalmente aprovada.",
    )
    g3.metric(
        "Forecast atual",
        f"{result.schedule.makespan:.1f} h",
    )

    g4, g5, g6 = st.columns(3)
    g4.metric(
        "Δ vs original",
        f"{impact_vs_original:+.1f} h",
    )
    g5.metric(
        "Δ vs vigente",
        f"{impact_vs_governing:+.1f} h",
    )
    g6.metric(
        governance_window_title,
        governance_window_value,
    )

    if governing_baseline_revision is None:
        status(
            (
                "A Baseline 0 permanece como referência formal vigente. "
                "O forecast atual ainda não foi promovido a uma revisão."
            ),
            tone="ok",
            title="Baseline vigente: Original.",
        )
    else:
        status(
            (
                f"{governing_baseline_revision.label} · "
                f"{governing_baseline_revision.makespan_h:.1f} h"
                + (
                    f" · janela {governing_baseline_revision.deadline_h:.1f} h."
                    if governing_baseline_revision.deadline_h is not None
                    else " · sem deadline formal."
                )
            ),
            tone="ok",
            title=f"Baseline vigente: {governing_baseline_revision.label}.",
        )

    if approved_planning_baseline is not None:
        with st.expander("Baseline 0 · aprovação original", expanded=False):
            b0_rows = [
                {
                    "Campo": "Cenário",
                    "Valor": approved_planning_baseline.scenario_name or project_name,
                },
                {
                    "Campo": "Aprovado por",
                    "Valor": approved_planning_baseline.approved_by or "—",
                },
                {
                    "Campo": "Aprovada em",
                    "Valor": approved_planning_baseline.approved_at.astimezone().strftime(
                        "%d/%m/%Y %H:%M"
                    ),
                },
                {
                    "Campo": "Motivo / observação",
                    "Valor": approved_planning_baseline.approval_reason or "—",
                },
                {
                    "Campo": "Makespan",
                    "Valor": f"{approved_planning_baseline.makespan_h:.1f} h",
                },
                {
                    "Campo": "Janela",
                    "Valor": (
                        f"{approved_planning_baseline.deadline_h:.1f} h"
                        if approved_planning_baseline.deadline_h is not None
                        else "—"
                    ),
                },
            ]
            st.dataframe(
                pd.DataFrame(b0_rows),
                use_container_width=True,
                hide_index=True,
            )

    st.markdown("#### Histórico formal")
    governance_chain = " → ".join(
        ["Original", *[revision.label for revision in baseline_revisions]]
    )
    st.caption(f"Cadeia de aprovação: {governance_chain}")
    st.dataframe(
        baseline_revisions_df,
        use_container_width=True,
        hide_index=True,
    )
    st.caption(
        f"Mudança já formalizada vs Baseline 0: "
        f"{formalized_baseline_change:+.1f} h."
    )

    if baseline_revisions:
        with st.expander("Auditar uma revisão", expanded=False):
            audit_revision_number = st.selectbox(
                "Revisão",
                options=[revision.revision_number for revision in baseline_revisions],
                format_func=lambda number: f"Rev.{number}",
                key=f"governance_revision_audit_{execution_session.id[:8]}",
            )
            audit_revision = next(
                revision
                for revision in baseline_revisions
                if revision.revision_number == audit_revision_number
            )
            audit_index = next(
                index
                for index, revision in enumerate(baseline_revisions)
                if revision.revision_number == audit_revision_number
            )
            fallback_previous_revision = (
                baseline_revisions[audit_index - 1]
                if audit_index > 0
                else None
            )
            audit_previous_label = (
                audit_revision.previous_baseline_label
                or (
                    fallback_previous_revision.label
                    if fallback_previous_revision is not None
                    else "Original"
                )
            )
            audit_previous_makespan = (
                float(audit_revision.previous_makespan_h)
                if audit_revision.previous_makespan_h is not None
                else (
                    float(fallback_previous_revision.makespan_h)
                    if fallback_previous_revision is not None
                    else float(original_baseline_makespan)
                )
            )
            audit_previous_deadline = (
                audit_revision.previous_deadline_h
                if audit_revision.previous_deadline_h is not None
                else (
                    fallback_previous_revision.deadline_h
                    if fallback_previous_revision is not None
                    else original_deadline
                )
            )
            audit_delta_previous = (
                float(audit_revision.delta_vs_previous_h)
                if audit_revision.delta_vs_previous_h is not None
                else float(audit_revision.makespan_h) - audit_previous_makespan
            )
            audit_delta_original = (
                float(audit_revision.delta_vs_original_h)
                if audit_revision.delta_vs_original_h is not None
                else float(audit_revision.makespan_h) - original_baseline_makespan
            )

            a1, a2, a3, a4 = st.columns(4)
            a1.metric("Anterior", audit_previous_label)
            a2.metric(
                "Makespan anterior",
                f"{audit_previous_makespan:.1f} h",
            )
            a3.metric(
                "Makespan aprovado",
                f"{audit_revision.makespan_h:.1f} h",
                delta=f"{audit_delta_previous:+.1f} h",
            )
            a4.metric(
                "Δ vs Original",
                f"{audit_delta_original:+.1f} h",
            )

            audit_rows = [
                {
                    "Campo": "Janela anterior",
                    "Valor": (
                        f"{float(audit_previous_deadline):.1f} h"
                        if audit_previous_deadline is not None
                        else "—"
                    ),
                },
                {
                    "Campo": "Nova janela aprovada",
                    "Valor": (
                        f"{float(audit_revision.deadline_h):.1f} h"
                        if audit_revision.deadline_h is not None
                        else "—"
                    ),
                },
                {
                    "Campo": "Aprovado por",
                    "Valor": audit_revision.approved_by or "—",
                },
                {
                    "Campo": "Aprovada em",
                    "Valor": audit_revision.approved_at.astimezone().strftime(
                        "%d/%m/%Y %H:%M"
                    ),
                },
                {
                    "Campo": "Snapshot promovido",
                    "Valor": audit_revision.snapshot_id,
                },
                {
                    "Campo": "Motivo",
                    "Valor": audit_revision.reason,
                },
                {
                    "Campo": "Observação",
                    "Valor": audit_revision.notes or "—",
                },
            ]
            st.dataframe(
                pd.DataFrame(audit_rows),
                use_container_width=True,
                hide_index=True,
            )

    with st.expander(
        "Promover este replanejamento a nova linha de base",
        expanded=False,
    ):
        next_revision_number = (
            execution_store.next_baseline_revision_number(
                execution_session.id
            )
        )
        if next_revision_number is None:
            st.warning(
                "Esta sessão já possui Rev.1 até Rev.10. O Microsoft Project "
                "suporta dez linhas de base adicionais além da Baseline original."
            )
        else:
            st.caption(
                "Use somente quando houver aprovação formal de uma nova referência "
                "de compromisso. O forecast corrente, por si só, não cria baseline."
            )
            p1, p2, p3, p4 = st.columns(4)
            p1.metric(
                "Referência anterior",
                governing_baseline_label,
                help="Baseline formal vigente antes desta promoção.",
            )
            p2.metric(
                "Makespan anterior",
                f"{governing_baseline_makespan:.1f} h",
            )
            p3.metric(
                "Forecast a promover",
                f"{result.schedule.makespan:.1f} h",
                delta=f"{result.schedule.makespan - governing_baseline_makespan:+.1f} h",
            )
            p4.metric(
                "Δ vs Original",
                f"{result.schedule.makespan - original_baseline_makespan:+.1f} h",
            )
            revision_name = st.text_input(
                "Nome da revisão",
                value=f"Rev.{next_revision_number} · {project_name}",
                key=f"baseline_revision_name_{next_revision_number}",
            )
            revision_reason = st.text_area(
                "Motivo da revisão",
                placeholder=(
                    "Ex.: ampliação de escopo após inspeção do V-101 "
                    "e nova janela aprovada."
                ),
                key=f"baseline_revision_reason_{next_revision_number}",
            )
            rb1, rb2 = st.columns(2)
            with rb1:
                revision_approved_by = st.text_input(
                    "Aprovado por",
                    key=f"baseline_revision_approver_{next_revision_number}",
                    help="Obrigatório para manter a trilha formal de aprovação.",
                )
            with rb2:
                revision_deadline = st.number_input(
                    "Janela aprovada da revisão (h)",
                    min_value=0.5,
                    value=float(
                        project.deadline
                        if project.deadline is not None
                        else result.schedule.makespan
                    ),
                    step=0.5,
                    key=f"baseline_revision_deadline_{next_revision_number}",
                )
            revision_notes = st.text_area(
                "Observação (opcional)",
                key=f"baseline_revision_notes_{next_revision_number}",
            )
            confirm_revision = st.checkbox(
                (
                    f"Confirmo que este snapshot deve se tornar a "
                    f"Baseline Rev.{next_revision_number}"
                ),
                key=f"confirm_baseline_revision_{next_revision_number}",
            )
            if st.button(
                f"Promover para Baseline Rev.{next_revision_number}",
                type="primary",
                disabled=(
                    not confirm_revision
                    or not revision_name.strip()
                    or not revision_reason.strip()
                    or not revision_approved_by.strip()
                ),
                use_container_width=True,
                key=f"promote_baseline_revision_{next_revision_number}",
            ):
                revision = build_baseline_revision(
                    session_id=execution_session.id,
                    project_key=project_key,
                    revision_number=next_revision_number,
                    name=revision_name,
                    reason=revision_reason,
                    approved_by=revision_approved_by,
                    notes=revision_notes,
                    snapshot_id=report_snapshot_id,
                    previous_baseline_label=governing_baseline_label,
                    previous_makespan_h=float(governing_baseline_makespan),
                    previous_deadline_h=(
                        None
                        if project.deadline is None
                        else float(project.deadline)
                    ),
                    original_makespan_h=float(original_baseline_makespan),
                    current_time_h=float(current_time),
                    makespan_h=float(result.schedule.makespan),
                    deadline_h=float(revision_deadline),
                    total_cost=float(result.schedule.total_cost),
                    schedule_items=all_items,
                    wbs_by_id=wbs_by_id,
                )
                execution_store.save_baseline_revision(revision)
                st.session_state[
                    "baseline_revision_notice"
                ] = revision.label
                st.rerun()

    if st.session_state.get("baseline_revision_notice"):
        st.success(
            f"{st.session_state.pop('baseline_revision_notice')} passou a ser "
            "a baseline vigente para métricas, estabilidade e exportação ao Project."
        )
