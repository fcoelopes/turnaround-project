from __future__ import annotations

from pathlib import Path


ADVANCED_PAGE = (
    Path(__file__).resolve().parents[1]
    / "pages"
    / "2_Escopo_e_Replanejamento.py"
)


def test_advanced_navigation_uses_contextual_help_instead_of_manual_tab():
    source = ADVANCED_PAGE.read_text(encoding="utf-8")

    assert '["Visão", "Operação", "Escopo", "Recursos", "Governança"]' in source
    assert '"Manual"' not in source
    assert "manual_tab" not in source
    assert "render_manual" not in source

    assert "Ajuda · fluxo operacional" in source
    assert "Ajuda · conditional, XOR/OR/AND e resolução" in source
    assert "Ajuda · como funciona o multi-skill" in source
    assert "### Recursos da parada" in source
    assert "#### Capacidades e equipamentos" in source
    assert "#### Resultado do cenário" in source
    assert '"Preservação do plano"' in source
    assert '"Baixa": 0.3' in source
    assert '"Balanceada": 1.0' in source
    assert '"Alta": 1.7' in source
    assert "Mostrar configuração avançada de estabilidade" in source
    assert '"Utilização (%)"' in source
    assert "with people_tab:" not in source
    assert "config_tab" not in source
    assert '"Preferências do replanejamento"' in source
    assert '"Histórico da execução"' in source
    assert '"Sessão de execução persistida"' in source
    assert "Ajuda · forecast, baseline vigente e rebaseline" in source
    assert "### Visão executiva da parada" in source
    assert "### Escopo da parada" in source
    assert "Ajuda · domínio de escopo" in source
    assert '"Decisões pendentes"' in source
    assert '"#### Novo escopo relevante"' in source
    assert '"#### Cadeia controladora"' in source


def test_execution_revalidates_persisted_baseline_before_project_build():
    source = ADVANCED_PAGE.read_text(encoding="utf-8")

    assert "validate_planning_baseline_for_execution(" in source
    assert "allow_dangling_repair=True" in source
    assert "require_formal_approval=True" in source
    assert '"Baseline bloqueado para execução."' in source


def test_execution_supports_audited_progress_import_without_faking_actual_times():
    source = ADVANCED_PAGE.read_text(encoding="utf-8")

    assert '"Importar progresso em lote"' in source
    assert "parse_progress_file(progress_file)" in source
    assert "reconcile_progress(" in source
    assert "record_progress_import(" in source
    assert '"Actual Start/Actual Finish"' in source
    assert "latest_progress_import(" in source
