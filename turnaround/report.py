from __future__ import annotations

from io import BytesIO
from html import escape
from typing import Any

import pandas as pd
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Flowable,
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


class _MetricCards(Flowable):
    def __init__(
        self,
        metrics: list[tuple[str, str]],
        *,
        columns: int | None = None,
    ):
        super().__init__()
        self.metrics = metrics
        if columns is None:
            columns = 3 if len(metrics) == 6 else 4 if len(metrics) >= 8 else min(3, len(metrics))
        self.columns = max(1, columns)
        self.gap = 3 * mm
        self.card_height = 22 * mm
        self.row_gap = 3 * mm
        self.rows = (len(metrics) + self.columns - 1) // self.columns
        self.height = (
            self.rows * self.card_height
            + max(0, self.rows - 1) * self.row_gap
        )

    def wrap(self, avail_width, avail_height):
        self.width = avail_width
        return avail_width, self.height

    def _fit_text(self, text: str, max_width: float, start_size: float) -> float:
        size = start_size
        while size > 8 and stringWidth(text, "Helvetica-Bold", size) > max_width:
            size -= 0.5
        return size

    def draw(self):
        canvas = self.canv
        card_width = (
            self.width - (self.columns - 1) * self.gap
        ) / self.columns

        for index, (label, value) in enumerate(self.metrics):
            row = index // self.columns
            col = index % self.columns
            x = col * (card_width + self.gap)
            y = self.height - (row + 1) * self.card_height - row * self.row_gap

            canvas.saveState()
            canvas.setFillColor(colors.HexColor("#FAFCFD"))
            canvas.setStrokeColor(LINE)
            canvas.setLineWidth(0.7)
            canvas.roundRect(
                x,
                y,
                card_width,
                self.card_height,
                5,
                fill=1,
                stroke=1,
            )

            canvas.setFillColor(ACCENT)
            canvas.roundRect(
                x,
                y,
                3.2,
                self.card_height,
                3,
                fill=1,
                stroke=0,
            )

            left = x + 5 * mm
            label_y = y + self.card_height - 6.2 * mm
            value_y = y + 5.2 * mm

            canvas.setFillColor(MUTED)
            canvas.setFont("Helvetica-Bold", 7.2)
            canvas.drawString(left, label_y, str(label).upper())

            value_text = str(value)
            max_value_width = card_width - 9 * mm
            value_size = self._fit_text(
                value_text,
                max_value_width,
                14,
            )
            canvas.setFillColor(INK)
            canvas.setFont("Helvetica-Bold", value_size)
            canvas.drawString(left, value_y, value_text)
            canvas.restoreState()


def _metric_table(metrics: list[tuple[str, str]]) -> Flowable:
    return _MetricCards(metrics)


def _truncate_canvas_text(
    text: str,
    *,
    max_width: float,
    font_name: str = "Helvetica",
    font_size: float = 7.2,
) -> str:
    text = str(text)
    if stringWidth(text, font_name, font_size) <= max_width:
        return text
    suffix = "..."
    available = max_width - stringWidth(suffix, font_name, font_size)
    if available <= 0:
        return suffix
    out = text
    while out and stringWidth(out, font_name, font_size) > available:
        out = out[:-1]
    return out.rstrip() + suffix


class _GanttFlowable(Flowable):
    def __init__(
        self,
        schedule_df: pd.DataFrame,
        *,
        critical_ids: set[str] | None = None,
        deadline: float | None = None,
        current_time: float | None = None,
        max_rows: int = 18,
        frozen_column: str | None = None,
    ):
        super().__init__()
        self.schedule_df = schedule_df.copy()
        self.critical_ids = {str(x) for x in (critical_ids or set())}
        self.deadline = deadline
        self.current_time = current_time
        self.max_rows = max_rows
        self.frozen_column = frozen_column
        self.row_height = 6.4 * mm
        self.axis_height = 12 * mm
        self.legend_height = 7 * mm

        self.start_col = (
            "Inicio_h"
            if "Inicio_h" in self.schedule_df.columns
            else "Início (h)"
        )
        self.finish_col = (
            "Fim_h"
            if "Fim_h" in self.schedule_df.columns
            else "Fim (h)"
        )
        self.name_col = "Atividade"
        self.id_col = "ID"

        if not self.schedule_df.empty:
            self.schedule_df = self.schedule_df.sort_values(
                [self.start_col, self.finish_col]
            ).head(self.max_rows)

        self.rows = len(self.schedule_df)
        self.height = (
            self.axis_height
            + self.rows * self.row_height
            + self.legend_height
        )

    def wrap(self, avail_width, avail_height):
        self.width = avail_width
        return avail_width, self.height

    def _time_bounds(self):
        if self.schedule_df.empty:
            return 0.0, 1.0

        start = float(self.schedule_df[self.start_col].min())
        finish = float(self.schedule_df[self.finish_col].max())
        start = min(0.0, start)

        if self.deadline is not None:
            finish = max(finish, float(self.deadline))
        if self.current_time is not None:
            finish = max(finish, float(self.current_time))

        if finish <= start:
            finish = start + 1.0
        return start, finish

    def draw(self):
        canvas = self.canv
        if self.schedule_df.empty:
            canvas.setFillColor(MUTED)
            canvas.setFont("Helvetica", 8)
            canvas.drawString(0, self.height / 2, "Sem atividades para exibir no Gantt.")
            return

        label_width = min(57 * mm, self.width * 0.36)
        chart_x = label_width + 4 * mm
        chart_width = self.width - chart_x
        top = self.height - self.axis_height
        start_t, finish_t = self._time_bounds()
        span = finish_t - start_t

        def tx(value: float) -> float:
            return chart_x + (float(value) - start_t) / span * chart_width

        # Axis/grid
        ticks = 6
        canvas.saveState()
        canvas.setStrokeColor(LINE)
        canvas.setFillColor(MUTED)
        canvas.setFont("Helvetica", 6.8)
        for i in range(ticks + 1):
            value = start_t + span * i / ticks
            x = tx(value)
            canvas.setDash(1, 2)
            canvas.line(
                x,
                self.legend_height,
                x,
                top + 1.5 * mm,
            )
            canvas.setDash()
            label = f"{value:.0f} h"
            canvas.drawCentredString(
                x,
                top + 3.2 * mm,
                label,
            )

        # Rows and bars
        for row_index, (_, row) in enumerate(self.schedule_df.iterrows()):
            y = top - (row_index + 1) * self.row_height
            center_y = y + self.row_height / 2
            task_id = str(row[self.id_col])
            task_name = _truncate_canvas_text(
                row[self.name_col],
                max_width=label_width - 6 * mm,
                font_size=7.1,
            )

            if row_index % 2 == 1:
                canvas.setFillColor(colors.HexColor("#F7F9FB"))
                canvas.rect(
                    0,
                    y,
                    self.width,
                    self.row_height,
                    fill=1,
                    stroke=0,
                )

            canvas.setStrokeColor(LINE)
            canvas.setLineWidth(0.35)
            canvas.line(0, y, self.width, y)

            canvas.setFillColor(INK)
            canvas.setFont("Helvetica", 7.1)
            canvas.drawString(
                0,
                center_y - 2.2,
                f"{task_id} · {task_name}",
            )

            bar_start = tx(float(row[self.start_col]))
            bar_finish = tx(float(row[self.finish_col]))
            bar_width = max(2.5, bar_finish - bar_start)
            frozen = (
                self.frozen_column is not None
                and self.frozen_column in row.index
                and bool(row[self.frozen_column])
            )

            if frozen:
                fill = colors.HexColor("#AAB4C0")
            elif task_id in self.critical_ids:
                fill = ACCENT
            else:
                fill = colors.HexColor("#73AFA9")

            canvas.setFillColor(fill)
            canvas.roundRect(
                bar_start,
                y + 1.4 * mm,
                bar_width,
                self.row_height - 2.8 * mm,
                2.2,
                fill=1,
                stroke=0,
            )

            duration = float(row[self.finish_col]) - float(row[self.start_col])
            if bar_width >= 16 * mm:
                canvas.setFillColor(WHITE)
                canvas.setFont("Helvetica-Bold", 6.4)
                canvas.drawCentredString(
                    bar_start + bar_width / 2,
                    center_y - 2.0,
                    f"{duration:g} h",
                )

        canvas.setStrokeColor(LINE)
        canvas.line(
            0,
            top - self.rows * self.row_height,
            self.width,
            top - self.rows * self.row_height,
        )

        # Deadline/current time markers
        if self.deadline is not None:
            x = tx(float(self.deadline))
            canvas.setStrokeColor(DANGER)
            canvas.setLineWidth(1.2)
            canvas.setDash(3, 2)
            canvas.line(
                x,
                self.legend_height,
                x,
                top + 1.5 * mm,
            )
            canvas.setDash()
            canvas.setFillColor(DANGER)
            canvas.setFont("Helvetica-Bold", 6.5)
            canvas.drawRightString(
                min(self.width, x - 1.5 * mm),
                self.legend_height - 1.5 * mm,
                "deadline",
            )

        if self.current_time is not None:
            x = tx(float(self.current_time))
            canvas.setStrokeColor(colors.HexColor("#2563EB"))
            canvas.setLineWidth(1.2)
            canvas.setDash(2, 2)
            canvas.line(
                x,
                self.legend_height,
                x,
                top + 1.5 * mm,
            )
            canvas.setDash()
            canvas.setFillColor(colors.HexColor("#2563EB"))
            canvas.setFont("Helvetica-Bold", 6.5)
            canvas.drawString(
                min(self.width - 18 * mm, x + 1.5 * mm),
                self.legend_height - 1.5 * mm,
                "agora",
            )

        # Legend
        legend_y = 1.8 * mm
        legend_items = [
            (ACCENT, "crítica"),
            (colors.HexColor("#73AFA9"), "programada"),
        ]
        if self.frozen_column:
            legend_items.append((colors.HexColor("#AAB4C0"), "congelada"))

        x = 0
        canvas.setFont("Helvetica", 6.6)
        for color, label in legend_items:
            canvas.setFillColor(color)
            canvas.roundRect(
                x,
                legend_y,
                5 * mm,
                2.6 * mm,
                1.2,
                fill=1,
                stroke=0,
            )
            canvas.setFillColor(MUTED)
            canvas.drawString(
                x + 6.2 * mm,
                legend_y + 0.2 * mm,
                label,
            )
            x += 31 * mm

        total_rows = len(self.schedule_df)
        canvas.restoreState()


def _gantt_chart(
    schedule_df: pd.DataFrame,
    *,
    critical_ids: set[str] | None = None,
    deadline: float | None = None,
    current_time: float | None = None,
    frozen_column: str | None = None,
) -> Flowable:
    return _GanttFlowable(
        schedule_df,
        critical_ids=critical_ids,
        deadline=deadline,
        current_time=current_time,
        frozen_column=frozen_column,
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

    critical_ids = set()
    if not criticality_df.empty and "Critical" in criticality_df.columns:
        critical_ids = {
            str(value)
            for value in criticality_df.loc[
                criticality_df["Critical"] == True,  # noqa: E712
                "ID",
            ].tolist()
        }

    story.extend(
        [
            PageBreak(),
            _p("CRONOGRAMA", s["kicker"]),
            _p("Cronograma otimizado", s["title"]),
            _p(
                "Visão temporal das frentes de trabalho; atividades críticas aparecem em destaque.",
                s["muted"],
            ),
            Spacer(1, 3 * mm),
            _gantt_chart(
                schedule_df,
                critical_ids=critical_ids,
                deadline=deadline_h,
            ),
            Spacer(1, 5 * mm),
            _p("Detalhamento do cronograma", s["h2"]),
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
    critical_ids: set[str] | None = None,
    critical_path_label: str | None = None,
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
        _p("Cadeia controladora atual", s["h2"]),
        _p(
            (
                critical_path_label
                if critical_path_label and critical_path_label != "—"
                else "Sem cadeia controladora identificada para o estado atual."
            ),
            s["body"],
        ),
        _p(
            "A criticidade efetiva considera precedências ativas, gates criados pelo "
            "scope discovery e liberações de recursos que controlam o término. "
            "Não equivale ao CPM clássico do planejamento-base.",
            s["muted"],
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
        _gantt_chart(
            schedule_df,
            critical_ids=critical_ids,
            deadline=deadline,
            current_time=current_time,
            frozen_column="Congelada",
        ),
        Spacer(1, 5 * mm),
        _p("Detalhamento do cronograma", s["h2"]),
        _dataframe_table(
            schedule_df,
            [
                "ID",
                "Atividade",
                "Modo",
                "Início (h)",
                "Fim (h)",
                "Duração (h)",
                "Crítica atual",
                "Controla por",
            ],
            max_rows=60,
            widths=[
                10 * mm,
                48 * mm,
                20 * mm,
                16 * mm,
                16 * mm,
                18 * mm,
                20 * mm,
                26 * mm,
            ],
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
