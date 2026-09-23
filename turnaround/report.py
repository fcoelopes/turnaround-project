from __future__ import annotations

from io import BytesIO
from html import escape
from typing import Any

import pandas as pd
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

INK = colors.HexColor("#132238")
MUTED = colors.HexColor("#607086")
ACCENT = colors.HexColor("#0F766E")
ACCENT_SOFT = colors.HexColor("#E6F5F2")
LINE = colors.HexColor("#DFE6EE")
SOFT = colors.HexColor("#F5F7FA")
GOOD = colors.HexColor("#16803A")
WARN = colors.HexColor("#D69200")
DANGER = colors.HexColor("#D92D20")
WHITE = colors.white


def _styles():
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "ReportTitle",
            parent=base["Title"],
            fontName="Helvetica-Bold",
            fontSize=22,
            leading=25,
            textColor=INK,
            alignment=TA_LEFT,
            spaceAfter=5 * mm,
        ),
        "kicker": ParagraphStyle(
            "Kicker",
            parent=base["Normal"],
            fontName="Helvetica-Bold",
            fontSize=8,
            leading=10,
            textColor=ACCENT,
            spaceAfter=1.5 * mm,
        ),
        "h2": ParagraphStyle(
            "H2",
            parent=base["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=13,
            leading=16,
            textColor=INK,
            spaceBefore=4 * mm,
            spaceAfter=2.5 * mm,
        ),
        "body": ParagraphStyle(
            "Body",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=9,
            leading=13,
            textColor=INK,
        ),
        "muted": ParagraphStyle(
            "Muted",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=8,
            leading=11,
            textColor=MUTED,
        ),
        "metric_label": ParagraphStyle(
            "MetricLabel",
            parent=base["BodyText"],
            fontName="Helvetica-Bold",
            fontSize=7.5,
            leading=9,
            textColor=MUTED,
        ),
        "metric_value": ParagraphStyle(
            "MetricValue",
            parent=base["BodyText"],
            fontName="Helvetica-Bold",
            fontSize=13,
            leading=15,
            textColor=INK,
        ),
        "table": ParagraphStyle(
            "Table",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=7.5,
            leading=9,
            textColor=INK,
        ),
        "table_header": ParagraphStyle(
            "TableHeader",
            parent=base["BodyText"],
            fontName="Helvetica-Bold",
            fontSize=7.5,
            leading=9,
            textColor=WHITE,
        ),
    }


def _p(value: Any, style) -> Paragraph:
    if value is None:
        value = "-"
    return Paragraph(escape(str(value)), style)


def _page(canvas, doc):
    canvas.saveState()
    canvas.setStrokeColor(LINE)
    canvas.line(18 * mm, 13 * mm, A4[0] - 18 * mm, 13 * mm)
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(18 * mm, 8.5 * mm, "Turnaround Scheduler - relatório gerencial")
    canvas.drawRightString(
        A4[0] - 18 * mm,
        8.5 * mm,
        f"Página {doc.page}",
    )
    canvas.restoreState()


def _metric_table(metrics: list[tuple[str, str]]) -> Table:
    styles = _styles()
    cells = []
    for label, value in metrics:
        cells.append(
            [
                _p(label.upper(), styles["metric_label"]),
                _p(value, styles["metric_value"]),
            ]
        )

    cards = []
    for label_value in cells:
        cards.append(
            Table(
                [[label_value[0]], [label_value[1]]],
                colWidths=[39 * mm],
                rowHeights=[8 * mm, 11 * mm],
                style=TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, -1), colors.white),
                        ("BOX", (0, 0), (-1, -1), 0.6, LINE),
                        ("ROUNDEDCORNERS", [4]),
                        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 4 * mm),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 3 * mm),
                        ("TOPPADDING", (0, 0), (-1, -1), 1 * mm),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 1 * mm),
                    ]
                ),
            )
        )

    rows = []
    for i in range(0, len(cards), 4):
        row = cards[i : i + 4]
        while len(row) < 4:
            row.append("")
        rows.append(row)

    return Table(
        rows,
        colWidths=[42 * mm] * 4,
        hAlign="LEFT",
        style=TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3 * mm),
                ("TOPPADDING", (0, 0), (-1, -1), 1 * mm),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2 * mm),
            ]
        ),
    )


def _dataframe_table(
    df: pd.DataFrame | None,
    columns: list[str],
    *,
    max_rows: int = 12,
    widths: list[float] | None = None,
) -> Table | Paragraph:
    styles = _styles()
    if df is None or df.empty:
        return _p("Sem dados disponíveis.", styles["muted"])

    existing = [c for c in columns if c in df.columns]
    if not existing:
        return _p("Sem dados disponíveis.", styles["muted"])

    subset = df.loc[:, existing].head(max_rows)
    data = [[_p(c, styles["table_header"]) for c in existing]]
    for _, row in subset.iterrows():
        data.append([_p(row[c], styles["table"]) for c in existing])

    if widths is None:
        usable = 174 * mm
        widths = [usable / len(existing)] * len(existing)

    table = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), INK),
                ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
                ("GRID", (0, 0), (-1, -1), 0.4, LINE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, SOFT]),
                ("LEFTPADDING", (0, 0), (-1, -1), 2 * mm),
                ("RIGHTPADDING", (0, 0), (-1, -1), 2 * mm),
                ("TOPPADDING", (0, 0), (-1, -1), 1.4 * mm),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1.4 * mm),
            ]
        )
    )
    return table


def _status_box(text: str, tone: str = "good") -> Table:
    styles = _styles()
    tone_color = {"good": GOOD, "warn": WARN, "danger": DANGER}.get(tone, ACCENT)
    table = Table(
        [[_p(text, styles["body"])]],
        colWidths=[174 * mm],
        style=TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.white),
                ("BOX", (0, 0), (-1, -1), 0.7, LINE),
                ("LINEBEFORE", (0, 0), (0, -1), 4, tone_color),
                ("LEFTPADDING", (0, 0), (-1, -1), 4 * mm),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4 * mm),
                ("TOPPADDING", (0, 0), (-1, -1), 3 * mm),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3 * mm),
            ]
        ),
    )
    return table


def _build_pdf(story: list) -> bytes:
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title="Relatório gerencial de turnaround",
        author="Turnaround Scheduler",
    )
    doc.build(story, onFirstPage=_page, onLaterPages=_page)
    return buffer.getvalue()


def build_base_management_pdf(
    *,
    project_name: str,
    hours_per_day: int,
    makespan_h: float,
    deadline_h: float | None,
    priority_rule: str,
    comparison: dict[str, float],
    schedule_df: pd.DataFrame,
    criticality_df: pd.DataFrame,
    resource_df: pd.DataFrame | None,
    risk: dict | None,
) -> bytes:
    s = _styles()
    story: list = [
        _p("RELATÓRIO GERENCIAL", s["kicker"]),
        _p(project_name or "Turnaround", s["title"]),
        _p(
            "Síntese executiva do cronograma, pressão de recursos e risco de prazo.",
            s["muted"],
        ),
        Spacer(1, 3 * mm),
    ]

    if deadline_h is None:
        tone = "warn"
        status_text = "Janela-alvo não definida. O relatório apresenta duração e risco sem avaliação de aderência ao prazo."
    elif makespan_h <= deadline_h:
        tone = "good"
        status_text = (
            f"Cronograma determinístico dentro da janela: {makespan_h:.1f} h "
            f"para um limite de {deadline_h:.1f} h."
        )
    else:
        tone = "danger"
        status_text = (
            f"Cronograma determinístico excede a janela em {makespan_h - deadline_h:.1f} h "
            f"({makespan_h:.1f} h planejadas para {deadline_h:.1f} h disponíveis)."
        )
    story.extend([_status_box(status_text, tone), Spacer(1, 4 * mm)])

    p80 = "-"
    probability = "-"
    if risk:
        p80 = f"{risk['p80_h'] / hours_per_day:.2f} d"
        if risk.get("probability_meet_deadline") is not None:
            probability = f"{100 * risk['probability_meet_deadline']:.1f}%"

    story.extend(
        [
            _metric_table(
                [
                    ("Makespan", f"{makespan_h / hours_per_day:.2f} d"),
                    (
                        "CPM sem recursos",
                        f"{comparison['unconstrained_makespan_h'] / hours_per_day:.2f} d",
                    ),
                    (
                        "Penalidade recursos",
                        f"{comparison['resource_penalty_h']:.1f} h",
                    ),
                    ("P80", p80),
                    ("P(cumprir janela)", probability),
                    ("Regra SSGS", priority_rule.replace("_", " ")),
                ]
            ),
            _p("Leitura executiva", s["h2"]),
        ]
    )

    insights = []
    if comparison["resource_penalty_h"] > 0:
        insights.append(
            f"A restrição de recursos adiciona {comparison['resource_penalty_h']:.1f} h "
            "sobre a referência sem limitação de capacidade."
        )
    else:
        insights.append(
            "As capacidades configuradas não acrescentam atraso sobre a referência CPM."
        )
    if risk:
        insights.append(
            f"A simulação aponta P50={risk['p50_h'] / hours_per_day:.2f} d, "
            f"P80={risk['p80_h'] / hours_per_day:.2f} d e "
            f"P90={risk['p90_h'] / hours_per_day:.2f} d."
        )
    if resource_df is not None and not resource_df.empty:
        top = resource_df.sort_values("Utilizacao_%", ascending=False).iloc[0]
        insights.append(
            f"O recurso com maior utilização média é {top['Recurso']} "
            f"({float(top['Utilizacao_%']):.1f}%)."
        )
    for item in insights:
        story.append(_p(f"- {item}", s["body"]))

    story.extend(
        [
            _p("Gargalos de recurso", s["h2"]),
            _dataframe_table(
                resource_df,
                ["Recurso", "Capacidade", "Pico", "Utilizacao_%"],
                max_rows=8,
                widths=[65 * mm, 30 * mm, 28 * mm, 38 * mm],
            ),
            _p("Atividades críticas / menor folga", s["h2"]),
        ]
    )

    crit = criticality_df.copy()
    if not crit.empty and "Critical" in crit.columns:
        critical_only = crit[crit["Critical"] == True]  # noqa: E712
        if not critical_only.empty:
            crit = critical_only
    story.append(
        _dataframe_table(
            crit,
            ["ID", "Atividade", "ES", "EF", "Float_h", "Critical"],
            max_rows=12,
            widths=[13 * mm, 74 * mm, 20 * mm, 20 * mm, 23 * mm, 24 * mm],
        )
    )

    story.extend(
        [
            PageBreak(),
            _p("CRONOGRAMA", s["kicker"]),
            _p("Cronograma otimizado", s["title"]),
            _p(
                "Lista gerencial das atividades programadas. Para manipulação detalhada, mantenha a exportação Excel.",
                s["muted"],
            ),
            Spacer(1, 3 * mm),
            _dataframe_table(
                schedule_df,
                ["ID", "Atividade", "Inicio_h", "Fim_h", "Duracao_h", "WBS"],
                max_rows=60,
                widths=[12 * mm, 82 * mm, 19 * mm, 19 * mm, 21 * mm, 21 * mm],
            ),
            Spacer(1, 4 * mm),
            _p(
                "Nota metodológica: o cronograma-base usa SSGS/RCPSP heurístico; "
                "a simulação de risco perturba as durações por distribuição triangular. "
                "O relatório não representa prova de ótimo global.",
                s["muted"],
            ),
        ]
    )
    return _build_pdf(story)


def build_conditional_management_pdf(
    *,
    project_name: str,
    baseline_makespan: float,
    current_makespan: float,
    deadline: float | None,
    current_time: float,
    total_cost: float,
    new_scope_count: int,
    strategy: str,
    activation_df: pd.DataFrame,
    schedule_df: pd.DataFrame,
) -> bytes:
    s = _styles()
    delta = current_makespan - baseline_makespan

    if deadline is None:
        tone = "warn"
        status_text = (
            f"O escopo atual leva o makespan a {current_makespan:.1f} h "
            "e não há deadline configurado para avaliar atraso."
        )
    elif current_makespan <= deadline:
        tone = "good"
        status_text = (
            f"Após o scope discovery, o cronograma permanece dentro da janela: "
            f"{current_makespan:.1f} h para {deadline:.1f} h disponíveis."
        )
    else:
        tone = "danger"
        status_text = (
            f"O scope discovery leva o cronograma a exceder a janela em "
            f"{current_makespan - deadline:.1f} h."
        )

    active_counts = {}
    if not activation_df.empty and "Estado" in activation_df.columns:
        active_counts = activation_df["Estado"].value_counts().to_dict()

    story: list = [
        _p("RELATÓRIO GERENCIAL - SCOPE DISCOVERY", s["kicker"]),
        _p(project_name or "Turnaround", s["title"]),
        _p(
            "Impacto de achados de inspeção, decisões de escopo e modos de execução sobre o plano da parada.",
            s["muted"],
        ),
        Spacer(1, 3 * mm),
        _status_box(status_text, tone),
        Spacer(1, 4 * mm),
        _metric_table(
            [
                ("Baseline", f"{baseline_makespan:.1f} h"),
                ("Reprogramado", f"{current_makespan:.1f} h"),
                ("Impacto", f"{delta:+.1f} h"),
                ("Hora corrente", f"{current_time:.1f} h"),
                ("Novo escopo", str(new_scope_count)),
                ("Custo modos", f"{total_cost:,.0f}"),
                ("Ativas", str(active_counts.get("active", 0))),
                ("Pendentes", str(active_counts.get("pending", 0))),
            ]
        ),
        _p("Mapa de ativação", s["h2"]),
        _dataframe_table(
            activation_df,
            ["ID", "Atividade", "Tipo", "Estado", "Motivo"],
            max_rows=25,
            widths=[12 * mm, 58 * mm, 24 * mm, 24 * mm, 56 * mm],
        ),
        PageBreak(),
        _p("CRONOGRAMA REPROGRAMADO", s["kicker"]),
        _p("Plano após scope discovery", s["title"]),
        _p(
            f"Estratégia do solver: {strategy}. Atividades concluídas ou em andamento permanecem congeladas.",
            s["muted"],
        ),
        Spacer(1, 3 * mm),
        _dataframe_table(
            schedule_df,
            ["ID", "Atividade", "Modo", "Início (h)", "Fim (h)", "Duração (h)", "Congelada"],
            max_rows=60,
            widths=[12 * mm, 63 * mm, 27 * mm, 19 * mm, 19 * mm, 19 * mm, 20 * mm],
        ),
        Spacer(1, 4 * mm),
        _p(
            "Nota metodológica: o MRCPSP atual combina enumeração de modos em espaços pequenos "
            "e busca heurística em espaços maiores, sempre com SSGS. O resultado é um plano factível, "
            "não uma prova de ótimo global para instâncias grandes.",
            s["muted"],
        ),
    ]
    return _build_pdf(story)
