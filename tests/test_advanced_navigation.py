from __future__ import annotations

from pathlib import Path


ADVANCED_PAGE = (
    Path(__file__).resolve().parents[1]
    / "pages"
    / "2_Escopo_e_Replanejamento.py"
)


def test_advanced_navigation_uses_contextual_help_instead_of_manual_tab():
    source = ADVANCED_PAGE.read_text(encoding="utf-8")

    assert '["Operação", "Configuração", "Pessoas", "Governança"]' in source
    assert '"Manual"' not in source
    assert "manual_tab" not in source
    assert "render_manual" not in source

    assert "Ajuda · fluxo operacional" in source
    assert "Ajuda · conditional, XOR/OR/AND e resolução" in source
    assert "Ajuda · como funciona o multi-skill" in source
    assert "Ajuda · forecast, baseline vigente e rebaseline" in source
