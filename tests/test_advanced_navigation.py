from __future__ import annotations

from pathlib import Path


ADVANCED_PAGE = (
    Path(__file__).resolve().parents[1]
    / "pages"
    / "2_Escopo_e_Replanejamento.py"
)


def test_advanced_navigation_uses_contextual_help_instead_of_manual_tab():
    source = ADVANCED_PAGE.read_text(encoding="utf-8")

    assert '["Visão", "Operação", "Escopo", "Recursos", "Configuração", "Governança"]' in source
    assert '"Manual"' not in source
    assert "manual_tab" not in source
    assert "render_manual" not in source

    assert "Ajuda · fluxo operacional" in source
    assert "Ajuda · conditional, XOR/OR/AND e resolução" in source
    assert "Ajuda · como funciona o multi-skill" in source
    assert "### Recursos da parada" in source
    assert "#### Capacidades e equipamentos" in source
    assert "#### Resultado do cenário" in source
    assert '"Utilização (%)"' in source
    assert "with people_tab:" not in source
    assert "Ajuda · forecast, baseline vigente e rebaseline" in source
    assert "### Visão executiva da parada" in source
    assert "### Escopo da parada" in source
    assert "Ajuda · domínio de escopo" in source
    assert '"Decisões pendentes"' in source
    assert '"#### Novo escopo relevante"' in source
    assert '"#### Cadeia controladora"' in source
