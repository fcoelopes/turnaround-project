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

from .planning_status import classify_planning_status

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
    canvas.drawString(18 * mm, 8.5 * mm, "Turnaround Decision Support - relatório gerencial")
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
        max_rows: int | None = 18,
        frozen_column: str | None = None,
        order_column: str | None = None,
        time_bounds: tuple[float, float] | None = None,
    ):
        super().__init__()
        self.schedule_df = schedule_df.copy()
        self.critical_ids = {str(x) for x in (critical_ids or set())}
        self.deadline = deadline
        self.current_time = current_time
        self.max_rows = max_rows
        self.frozen_column = frozen_column
        self.order_column = order_column
        self.time_bounds = time_bounds
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
            if (
                self.order_column
                and self.order_column in self.schedule_df.columns
            ):
                self.schedule_df = self.schedule_df.sort_values(
                    [self.order_column],
                    kind="stable",
                )
            else:
                self.schedule_df = self.schedule_df.sort_values(
                    [self.start_col, self.finish_col],
                    kind="stable",
                )
            if self.max_rows is not None:
                self.schedule_df = self.schedule_df.head(self.max_rows)

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
        if self.time_bounds is not None:
            return self.time_bounds
        return _schedule_time_bounds(
            self.schedule_df,
            deadline=self.deadline,
            current_time=self.current_time,
        )

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


def _schedule_time_bounds(
    schedule_df: pd.DataFrame,
    *,
    deadline: float | None = None,
    current_time: float | None = None,
) -> tuple[float, float]:
    if schedule_df.empty:
        return 0.0, 1.0

    start_col = (
        "Inicio_h"
        if "Inicio_h" in schedule_df.columns
        else "Início (h)"
    )
    finish_col = (
        "Fim_h"
        if "Fim_h" in schedule_df.columns
        else "Fim (h)"
    )

    start = min(0.0, float(schedule_df[start_col].min()))
    finish = float(schedule_df[finish_col].max())

    if deadline is not None:
        finish = max(finish, float(deadline))
    if current_time is not None:
        finish = max(finish, float(current_time))

    if finish <= start:
        finish = start + 1.0
    return start, finish


def _gantt_chart_pages(
    schedule_df: pd.DataFrame,
    *,
    critical_ids: set[str] | None = None,
    deadline: float | None = None,
    current_time: float | None = None,
    frozen_column: str | None = None,
    order_column: str | None = None,
    max_rows: int = 18,
) -> list[Flowable]:
    """Paginate a Gantt without dropping rows.

    Sorting is applied once to the full schedule, then the ordered dataframe is
    split into pages. Every page shares the same time bounds so the horizontal
    scale remains comparable throughout the report.
    """
    if max_rows <= 0:
        raise ValueError("max_rows deve ser maior que zero")

    ordered = schedule_df.copy()
    if not ordered.empty:
        start_col = (
            "Inicio_h"
            if "Inicio_h" in ordered.columns
            else "Início (h)"
        )
        finish_col = (
            "Fim_h"
            if "Fim_h" in ordered.columns
            else "Fim (h)"
        )
        if order_column and order_column in ordered.columns:
            ordered = ordered.sort_values([order_column], kind="stable")
        else:
            ordered = ordered.sort_values(
                [start_col, finish_col],
                kind="stable",
            )

    bounds = _schedule_time_bounds(
        ordered,
        deadline=deadline,
        current_time=current_time,
    )

    if ordered.empty:
        chunks = [ordered]
    else:
        chunks = [
            ordered.iloc[start:start + max_rows].copy()
            for start in range(0, len(ordered), max_rows)
        ]

    return [
        _GanttFlowable(
            chunk,
            critical_ids=critical_ids,
            deadline=deadline,
            current_time=current_time,
            max_rows=None,
            frozen_column=frozen_column,
            order_column=order_column,
            time_bounds=bounds,
        )
        for chunk in chunks
    ]


def _gantt_chart(
    schedule_df: pd.DataFrame,
    *,
    critical_ids: set[str] | None = None,
    deadline: float | None = None,
    current_time: float | None = None,
    frozen_column: str | None = None,
    order_column: str | None = None,
    max_rows: int | None = 18,
    time_bounds: tuple[float, float] | None = None,
) -> Flowable:
    return _GanttFlowable(
        schedule_df,
        critical_ids=critical_ids,
        deadline=deadline,
        current_time=current_time,
        max_rows=max_rows,
        frozen_column=frozen_column,
        order_column=order_column,
        time_bounds=time_bounds,
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


def _planning_front_page_metrics(
    *,
    makespan_h: float,
    deadline_h: float | None,
    hours_per_day: int,
    risk: dict | None,
    overall_status: str,
) -> list[tuple[str, str]]:
    """Métricas que a primeira página deve responder sem leitura técnica."""
    day = float(hours_per_day)
    deadline = (
        "sem janela"
        if deadline_h is None
        else f"{float(deadline_h) / day:.2f} d"
    )
    p80_h = None if not risk else risk.get("p80_h")
    probability = (
        None
        if not risk
        else risk.get("probability_meet_deadline")
    )
    p80 = "não simulado" if p80_h is None else f"{float(p80_h) / day:.2f} d"
    probability_label = (
        "—"
        if probability is None
        else f"{100 * float(probability):.1f}%"
    )
    reserve = (
        "não simulado"
        if p80_h is None
        else f"{(float(p80_h) - float(makespan_h)) / day:+.2f} d"
    )
    return [
        ("Janela", deadline),
        ("Makespan base", f"{float(makespan_h) / day:.2f} d"),
        ("P80", p80),
        ("P(cumprir janela)", probability_label),
        ("Reserva até P80", reserve),
        ("Status geral", overall_status),
    ]


def _planning_risk_driver_frame(
    *,
    risk: dict | None,
    resource_df: pd.DataFrame | None,
    max_rows: int = 5,
) -> pd.DataFrame:
    """Resume sinais que mais pressionam o prazo sem prescrever decisão."""
    rows: list[dict[str, str | float]] = []

    if risk:
        for event in risk.get("scope_events", []):
            impact = event.get("marginal_impact_h")
            if impact is None or float(impact) <= 0:
                continue
            rows.append(
                {
                    "Tipo": "Evento de escopo",
                    "Direcionador": str(event.get("event_name") or "evento"),
                    "Sinal": f"+{float(impact):.1f} h",
                    "Prioridade": float(impact),
                    "Evidência": (
                        f"P={float(event.get('probability_configured', 0.0)) * 100:.1f}% · "
                        f"freq. simulada={float(event.get('frequency_simulated', 0.0)) * 100:.1f}%"
                    ),
                }
            )

    if resource_df is not None and not resource_df.empty:
        ranked = resource_df.copy()
        if "Utilizacao_%" in ranked.columns:
            ranked = ranked.sort_values(
                "Utilizacao_%",
                ascending=False,
                kind="stable",
            )
        top = ranked.iloc[0]
        resource = str(top.get("Recurso", "Recurso"))
        utilization = float(top.get("Utilizacao_%", 0.0) or 0.0)
        peak = top.get("Pico")
        capacity = top.get("Capacidade cenário", top.get("Capacidade"))
        evidence_parts = [f"utilização média={utilization:.1f}%"]
        signal = "maior utilização"
        if peak is not None and capacity is not None:
            peak_value = float(peak)
            capacity_value = float(capacity)
            evidence_parts.insert(
                0,
                f"pico={peak_value:g} / capacidade={capacity_value:g}",
            )
            if peak_value >= capacity_value - 1e-9:
                signal = "atinge capacidade no pico"
        rows.append(
            {
                "Tipo": "Recurso",
                "Direcionador": resource,
                "Sinal": signal,
                "Prioridade": utilization / 100.0,
                "Evidência": " · ".join(evidence_parts),
            }
        )

    if not rows:
        return pd.DataFrame(
            columns=["Rank", "Tipo", "Direcionador", "Sinal", "Evidência"]
        )

    scope_rows = [row for row in rows if row["Tipo"] == "Evento de escopo"]
    resource_rows = [row for row in rows if row["Tipo"] == "Recurso"]
    scope_rows.sort(key=lambda row: float(row["Prioridade"]), reverse=True)
    ordered = scope_rows + resource_rows
    ordered = ordered[:max_rows]

    return pd.DataFrame(
        [
            {
                "Rank": index,
                "Tipo": row["Tipo"],
                "Direcionador": row["Direcionador"],
                "Sinal": row["Sinal"],
                "Evidência": row["Evidência"],
            }
            for index, row in enumerate(ordered, start=1)
        ]
    )


def _planning_assumption_frames(
    assumptions: dict | None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Normaliza premissas do cenário para rastreabilidade no PDF."""
    assumptions = dict(assumptions or {})
    hours_per_day = int(assumptions.get("hours_per_day") or 0)
    deadline_h = assumptions.get("deadline_h")
    deadline_label = "sem janela"
    if deadline_h is not None and hours_per_day > 0:
        deadline_label = (
            f"{float(deadline_h) / float(hours_per_day):.2f} d "
            f"({float(deadline_h):.1f} h)"
        )

    distribution = assumptions.get("duration_distribution") or {}
    distribution_label = "não informada"
    if distribution:
        distribution_label = (
            f"Triangular · otimista {float(distribution.get('optimistic_pct', 0)):+.0f}% · "
            f"mais provável {float(distribution.get('most_likely_pct', 0)):+.0f}% · "
            f"pessimista {float(distribution.get('pessimistic_pct', 0)):+.0f}%"
        )

    summary_df = pd.DataFrame(
        [
            {"Premissa": "Arquivo de origem", "Valor": assumptions.get("source_name") or "—"},
            {"Premissa": "Janela", "Valor": deadline_label},
            {
                "Premissa": "Horas/dia para conversão",
                "Valor": str(hours_per_day) if hours_per_day > 0 else "—",
            },
            {
                "Premissa": "Simulações Monte Carlo",
                "Valor": str(assumptions.get("simulations") or "—"),
            },
            {"Premissa": "Distribuição de duração", "Valor": distribution_label},
            {"Premissa": "Heurística escolhida", "Valor": assumptions.get("heuristic") or "—"},
            {"Premissa": "Data/hora do cálculo", "Valor": assumptions.get("calculated_at") or "—"},
        ]
    )

    capacities = assumptions.get("capacities") or {}
    origins = assumptions.get("capacity_origins") or {}
    resource_df = pd.DataFrame(
        [
            {
                "Recurso": resource,
                "Capacidade": float(capacity),
                "Origem": origins.get(resource, "—"),
            }
            for resource, capacity in sorted(capacities.items())
        ],
        columns=["Recurso", "Capacidade", "Origem"],
    )

    event_df = pd.DataFrame(
        [
            {
                "Gatilho": item.get("trigger") or item.get("trigger_task_id") or "—",
                "Evento": item.get("event") or item.get("event_name") or "—",
                "Probabilidade": (
                    f"{float(item.get('probability', 0.0)) * 100:.1f}%"
                ),
            }
            for item in assumptions.get("scope_events", [])
        ],
        columns=["Gatilho", "Evento", "Probabilidade"],
    )
    return summary_df, resource_df, event_df


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
    baseline_scenario_name: str | None = None,
    baseline_approved_by: str | None = None,
    baseline_approved_at: str | None = None,
    baseline_approval_reason: str | None = None,
    scenario_assumptions: dict | None = None,
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

    executive_status = classify_planning_status(
        makespan_h=float(makespan_h),
        deadline_h=None if deadline_h is None else float(deadline_h),
        p80_h=None if not risk else float(risk["p80_h"]),
        probability_meet_deadline=(
            None
            if not risk or risk.get("probability_meet_deadline") is None
            else float(risk["probability_meet_deadline"])
        ),
    )
    tone = "good" if executive_status.tone == "ok" else executive_status.tone
    status_text = (
        f"{executive_status.overall}. "
        f"Determinístico: {executive_status.deterministic}; "
        f"P80: {executive_status.p80}; "
        f"P(janela): {executive_status.probability}. "
        f"{executive_status.detail}"
    )
    story.extend([_status_box(status_text, tone), Spacer(1, 4 * mm)])

    story.extend(
        [
            _metric_table(
                _planning_front_page_metrics(
                    makespan_h=float(makespan_h),
                    deadline_h=(
                        None if deadline_h is None else float(deadline_h)
                    ),
                    hours_per_day=hours_per_day,
                    risk=risk,
                    overall_status=executive_status.overall,
                )
            ),
            Spacer(1, 3 * mm),
        ]
    )

    risk_driver_df = _planning_risk_driver_frame(
        risk=risk,
        resource_df=resource_df,
    )
    story.extend(
        [
            _p("Principais direcionadores", s["h2"]),
            (
                _dataframe_table(
                    risk_driver_df,
                    ["Rank", "Tipo", "Direcionador", "Sinal", "Evidência"],
                    max_rows=5,
                    widths=[12 * mm, 30 * mm, 42 * mm, 34 * mm, 56 * mm],
                )
                if not risk_driver_df.empty
                else _p(
                    "Nenhum direcionador adicional foi identificado neste cenário.",
                    s["muted"],
                )
            ),
            _p(
                "Ordenação informativa para apoiar a análise; a ferramenta não seleciona a decisão técnica.",
                s["muted"],
            ),
            Spacer(1, 3 * mm),
        ]
    )

    if (
        baseline_scenario_name
        and baseline_approved_by
        and baseline_approved_at
    ):
        story.extend(
            [
                _p("BASELINE 0 APROVADA", s["h2"]),
                _metric_table(
                    [
                        ("Cenário", baseline_scenario_name),
                        ("Aprovado por", baseline_approved_by),
                        ("Aprovada em", baseline_approved_at),
                    ]
                ),
                *(
                    [
                        _p(
                            f"Motivo / observação: {baseline_approval_reason}",
                            s["body"],
                        )
                    ]
                    if baseline_approval_reason
                    else []
                ),
                Spacer(1, 3 * mm),
            ]
        )
    else:
        story.extend(
            [
                _p(
                    "Documento de análise: a Baseline 0 ainda não possui aprovação formal registrada.",
                    s["muted"],
                ),
                Spacer(1, 3 * mm),
            ]
        )

    story.extend(
        [
            _p("Leitura executiva", s["h2"]),
            _metric_table(
                [
                    ("Determinístico", executive_status.deterministic),
                    ("Risco probabilístico", executive_status.risk),
                    (
                        "CPM sem recursos",
                        f"{comparison['unconstrained_makespan_h'] / hours_per_day:.2f} d",
                    ),
                    (
                        "Penalidade recursos",
                        f"{comparison['resource_penalty_h']:.1f} h",
                    ),
                ]
            ),
            _p(
                f"Regra SSGS selecionada: {priority_rule.replace('_', ' ')}.",
                s["muted"],
            ),
        ]
    )

    assumption_summary_df, assumption_resource_df, assumption_event_df = (
        _planning_assumption_frames(scenario_assumptions)
    )
    story.extend(
        [
            PageBreak(),
            _p("PREMISSAS DO CENÁRIO", s["kicker"]),
            _p("Rastreabilidade do cálculo", s["title"]),
            _p(
                "Valores efetivamente usados para gerar este cenário e sua análise de risco.",
                s["muted"],
            ),
            Spacer(1, 3 * mm),
            _dataframe_table(
                assumption_summary_df,
                ["Premissa", "Valor"],
                max_rows=12,
                widths=[58 * mm, 116 * mm],
            ),
            Spacer(1, 4 * mm),
            _p("Capacidades e origem", s["h2"]),
            _dataframe_table(
                assumption_resource_df,
                ["Recurso", "Capacidade", "Origem"],
                max_rows=30,
                widths=[80 * mm, 44 * mm, 50 * mm],
            ),
            Spacer(1, 4 * mm),
            _p("Probabilidades de eventos de escopo", s["h2"]),
            (
                _dataframe_table(
                    assumption_event_df,
                    ["Gatilho", "Evento", "Probabilidade"],
                    max_rows=30,
                    widths=[72 * mm, 72 * mm, 30 * mm],
                )
                if not assumption_event_df.empty
                else _p(
                    "Nenhum evento probabilístico de ampliação de escopo configurado.",
                    s["muted"],
                )
            ),
            PageBreak(),
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
        if risk.get("scope_enabled"):
            insights.append(
                (
                    f"Escopo adicional apareceu em "
                    f"{risk['probability_any_scope_simulated'] * 100:.1f}% "
                    f"das simulações, com impacto médio incremental de "
                    f"{risk['mean_scope_impact_h']:.1f} h e P80 do impacto "
                    f"de {risk['p80_scope_impact_h']:.1f} h."
                )
            )
            insights.append(
                (
                    f"O P80 somente com incerteza de duração seria "
                    f"{risk['duration_only_p80_h'] / hours_per_day:.2f} d; "
                    f"com duração + ampliação probabilística de escopo, "
                    f"o P80 passa a {risk['p80_h'] / hours_per_day:.2f} d."
                )
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
                [
                    "Recurso",
                    "Origem",
                    "Capacidade cenário",
                    "Pico",
                    "Utilizacao_%",
                ],
                max_rows=8,
                widths=[48 * mm, 30 * mm, 34 * mm, 24 * mm, 34 * mm],
            ),
        ]
    )

    if risk and risk.get("scope_enabled") and risk.get("scope_events"):
        scope_event_df = pd.DataFrame(
            [
                {
                    "Gatilho": item["trigger_task_id"],
                    "Evento": item["event_name"],
                    "Atividades": ", ".join(item["task_ids"]),
                    "Prob.": f"{item['probability_configured'] * 100:.1f}%",
                    "Freq.": f"{item['frequency_simulated'] * 100:.1f}%",
                    "Impacto h": (
                        "—"
                        if item["marginal_impact_h"] is None
                        else f"{item['marginal_impact_h']:.1f}"
                    ),
                }
                for item in risk["scope_events"]
            ]
        )
        story.extend(
            [
                _p("Risco de ampliação de escopo", s["h2"]),
                _dataframe_table(
                    scope_event_df,
                    ["Gatilho", "Evento", "Atividades", "Prob.", "Freq.", "Impacto h"],
                    max_rows=12,
                    widths=[20 * mm, 38 * mm, 50 * mm, 20 * mm, 20 * mm, 23 * mm],
                ),
            ]
        )

    story.append(_p("Atividades críticas / menor folga", s["h2"]))

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
            _p("Cronograma factível por recursos", s["title"]),
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
                "Nota metodológica: o cronograma-base usa SSGS/RCPSP heurístico. "
                "A simulação de risco perturba durações por distribuição triangular e, "
                "quando configurado, sorteia eventos Bernoulli de ampliação de escopo antes "
                "de materializar o projeto de cada iteração. "
                f"A conversão de horas em dias usa {hours_per_day} h/d e não representa "
                "calendário real de turnos. O relatório não representa prova de ótimo global.",
                s["muted"],
            ),
        ]
    )
    return _build_pdf(story)


def _stability_profile_label(stability_weight: float) -> str:
    presets = {
        "Baixa": 0.3,
        "Balanceada": 1.0,
        "Alta": 1.7,
    }
    for label, weight in presets.items():
        if abs(float(stability_weight) - weight) <= 1e-9:
            return label
    return "Avançada"


def build_conditional_management_pdf(
    *,
    project_name: str,
    baseline_makespan: float,
    current_makespan: float,
    deadline: float | None,
    governing_baseline_makespan: float | None = None,
    governing_baseline_label: str | None = None,
    current_time: float,
    total_cost: float,
    new_scope_count: int,
    dynamic_scope_count: int = 0,
    strategy: str,
    activation_df: pd.DataFrame,
    total_start_deviation: float = 0.0,
    max_start_deviation: float = 0.0,
    stability_compared_tasks: int = 0,
    stability_weight: float = 0.0,
    schedule_df: pd.DataFrame,
    critical_ids: set[str] | None = None,
    critical_path_label: str | None = None,
    snapshot_id: str | None = None,
    session_id: str | None = None,
    resource_scenario_df: pd.DataFrame | None = None,
    execution_state_df: pd.DataFrame | None = None,
    dynamic_scope_df: pd.DataFrame | None = None,
    baseline_revisions_df: pd.DataFrame | None = None,
    pending_decisions_df: pd.DataFrame | None = None,
) -> bytes:
    s = _styles()
    delta = current_makespan - baseline_makespan
    governing_makespan = (
        float(governing_baseline_makespan)
        if governing_baseline_makespan is not None
        else float(baseline_makespan)
    )
    governing_label = governing_baseline_label or "Original"
    delta_vs_governing = current_makespan - governing_makespan
    formalized_delta = governing_makespan - baseline_makespan

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

    gantt_pages = _gantt_chart_pages(
        schedule_df,
        critical_ids=critical_ids,
        deadline=deadline,
        current_time=current_time,
        frozen_column="Congelada",
        order_column="_Ordem",
        max_rows=18,
    )

    if deadline is None:
        window_metric = ("Janela", "sem deadline")
    elif current_makespan <= deadline:
        window_metric = (
            "Folga da janela",
            f"{deadline - current_makespan:.1f} h",
        )
    else:
        window_metric = (
            "Excesso da janela",
            f"{current_makespan - deadline:.1f} h",
        )

    scope_changes_df = activation_df.copy()
    if not scope_changes_df.empty:
        state_series = scope_changes_df.get(
            "Estado",
            pd.Series(index=scope_changes_df.index, dtype=str),
        ).astype(str)
        type_series = scope_changes_df.get(
            "Tipo",
            pd.Series(index=scope_changes_df.index, dtype=str),
        ).astype(str)
        origin_series = scope_changes_df.get(
            "Origem",
            pd.Series(index=scope_changes_df.index, dtype=str),
        ).astype(str)
        scope_changes_df = scope_changes_df[
            (
                origin_series.eq("dynamic discovery")
                | (
                    type_series.ne("mandatory")
                    & state_series.isin(["active", "pending_selection"])
                )
            )
        ]

    changed_resources_df = pd.DataFrame()
    if resource_scenario_df is not None and not resource_scenario_df.empty:
        changed_mask = []
        for _, row in resource_scenario_df.iterrows():
            delta_value = row.get("Δ vs base")
            base_value = row.get("Base")
            scenario_value = row.get("Cenário")
            changed = False
            try:
                changed = abs(float(delta_value)) > 1e-9
            except (TypeError, ValueError):
                try:
                    changed = (
                        str(base_value).strip().lower() == "não informada"
                        and float(scenario_value) > 0
                    )
                except (TypeError, ValueError):
                    changed = False
            changed_mask.append(changed)
        changed_resources_df = resource_scenario_df.loc[changed_mask].copy()

    story: list = [
        _p("RELATÓRIO GERENCIAL - SCOPE DISCOVERY", s["kicker"]),
        _p(project_name or "Turnaround", s["title"]),
        _p(
            "Leitura executiva do impacto do escopo descoberto sobre a janela da parada.",
            s["muted"],
        ),
        _p(
            (
                "Snapshot: "
                + (snapshot_id or "não informado")
                + f" · hora corrente {current_time:.1f} h"
            ),
            s["muted"],
        ),
        Spacer(1, 3 * mm),
        _status_box(status_text, tone),
        Spacer(1, 4 * mm),
        _metric_table(
            [
                ("Baseline original", f"{baseline_makespan:.1f} h"),
                ("Baseline vigente", f"{governing_makespan:.1f} h · {governing_label}"),
                ("Forecast atual", f"{current_makespan:.1f} h"),
                ("Δ vs original", f"{delta:+.1f} h"),
                ("Δ vs vigente", f"{delta_vs_governing:+.1f} h"),
                window_metric,
            ]
        ),
        *(
            [
                _p("DECISÕES PENDENTES", s["h2"]),
                _dataframe_table(
                    pending_decisions_df,
                    [
                        "Decisão",
                        "Alternativas",
                        "Impacto prazo",
                        "Impacto custo",
                        "Recurso crítico",
                    ],
                    max_rows=8,
                    widths=[25 * mm, 55 * mm, 28 * mm, 28 * mm, 38 * mm],
                ),
                _p(
                    "Impactos comparados contra o forecast atual. O relatório não seleciona a alternativa técnica.",
                    s["muted"],
                ),
            ]
            if pending_decisions_df is not None
            and not pending_decisions_df.empty
            else []
        ),
        *(
            [
                _p("NOVO ESCOPO RELEVANTE", s["h2"]),
                _dataframe_table(
                    dynamic_scope_df,
                    [
                        "ID",
                        "Atividade",
                        "Descoberta em (h)",
                        "Impacto no término",
                        "Bloqueia",
                    ],
                    max_rows=8,
                    widths=[18 * mm, 58 * mm, 28 * mm, 34 * mm, 36 * mm],
                ),
                _p(
                    "Impacto estimado por contrafactual individual no mesmo estado operacional; dependências entre DS-* podem impedir isolamento.",
                    s["muted"],
                ),
            ]
            if dynamic_scope_df is not None
            and not dynamic_scope_df.empty
            else []
        ),
        _p("Leitura executiva", s["h2"]),
        _p(
            (
                f"O estado atual ativou {new_scope_count} atividade(s) além do baseline. "
                f"Dessas, {dynamic_scope_count} foram criadas durante a execução. "
                f"O replanejamento deslocou {stability_compared_tasks} atividade(s) já planejadas, "
                f"somando {total_start_deviation:.1f} h de mudança de início."
            ),
            s["body"],
        ),
        _p(
            (
                f"Desde a baseline original, o forecast acumula {delta:+.1f} h. "
                f"A baseline vigente ({governing_label}) incorporou formalmente "
                f"{formalized_delta:+.1f} h em relação ao plano original; "
                f"restam {delta_vs_governing:+.1f} h de desvio contra a referência vigente."
            ),
            s["body"],
        ),
        *(
            [
                _p(
                    f"Há {active_counts.get('pending', 0)} decisão(ões) de escopo ainda pendentes.",
                    s["body"],
                )
            ]
            if active_counts.get("pending", 0)
            else []
        ),
        _p("O que controla o término", s["h2"]),
        _p(
            (
                critical_path_label
                if critical_path_label and critical_path_label != "—"
                else "Sem cadeia controladora identificada para o estado atual."
            ),
            s["body"],
        ),
        _p(
            "A cadeia considera precedências ativas, gates do scope discovery, contenção de recursos e, quando habilitado, disputa por pessoas multi-skill.",
            s["muted"],
        ),
        PageBreak(),
        _p("O QUE MUDOU", s["kicker"]),
        _p("Escopo e recursos que explicam o cenário", s["title"]),
        _p(
            "Mostramos somente alterações relevantes para a leitura gerencial; o restante permanece disponível na aplicação.",
            s["muted"],
        ),
        Spacer(1, 3 * mm),
        _p("Escopo ativado ou pendente", s["h2"]),
        *(
            [
                _dataframe_table(
                    scope_changes_df,
                    ["ID", "Atividade", "Tipo", "Estado", "Motivo"],
                    max_rows=20,
                    widths=[12 * mm, 64 * mm, 24 * mm, 24 * mm, 50 * mm],
                )
            ]
            if not scope_changes_df.empty
            else [_p("Nenhuma alteração de escopo relevante neste snapshot.", s["muted"])]
        ),
        Spacer(1, 4 * mm),
        _p("Recursos alterados", s["h2"]),
        *(
            [
                _dataframe_table(
                    changed_resources_df,
                    ["Recurso", "Base", "Cenário", "Δ vs base"],
                    max_rows=20,
                    widths=[62 * mm, 35 * mm, 35 * mm, 35 * mm],
                )
            ]
            if not changed_resources_df.empty
            else [_p("As capacidades do cenário coincidem com a referência informada.", s["muted"])]
        ),
        *(
            [
                Spacer(1, 4 * mm),
                _p("Trabalho criado durante a execução", s["h2"]),
                _dataframe_table(
                    dynamic_scope_df,
                    [
                        "ID",
                        "Atividade",
                        "Descoberta em (h)",
                        "Impacto no término",
                        "Recursos",
                        "Bloqueia",
                    ],
                    max_rows=20,
                    widths=[16 * mm, 48 * mm, 24 * mm, 30 * mm, 28 * mm, 28 * mm],
                ),
            ]
            if dynamic_scope_df is not None and not dynamic_scope_df.empty
            else []
        ),
        *(
            [
                Spacer(1, 4 * mm),
                _p("Histórico de linhas de base", s["h2"]),
                _dataframe_table(
                    baseline_revisions_df,
                    [
                        "Linha de base",
                        "Anterior",
                        "Makespan (h)",
                        "Janela (h)",
                        "Δ vs anterior (h)",
                        "Δ vs original (h)",
                        "Aprovada em",
                    ],
                    max_rows=10,
                    widths=[
                        22 * mm,
                        20 * mm,
                        22 * mm,
                        20 * mm,
                        24 * mm,
                        24 * mm,
                        38 * mm,
                    ],
                ),
                _p("Auditoria das aprovações", s["h2"]),
                _dataframe_table(
                    baseline_revisions_df,
                    [
                        "Linha de base",
                        "Aprovado por",
                        "Snapshot",
                        "Motivo",
                        "Observação",
                    ],
                    max_rows=10,
                    widths=[
                        24 * mm,
                        32 * mm,
                        26 * mm,
                        47 * mm,
                        45 * mm,
                    ],
                ),
            ]
            if baseline_revisions_df is not None
            and not baseline_revisions_df.empty
            else []
        ),
        Spacer(1, 4 * mm),
        _p("Rastreabilidade", s["h2"]),
        _p(
            (
                f"Snapshot {snapshot_id or 'não informado'} · solver {strategy} · "
                f"preservação do plano {_stability_profile_label(stability_weight)} · "
                f"maior deslocamento individual {max_start_deviation:.1f} h."
            ),
            s["muted"],
        ),
        PageBreak(),
        _p("CRONOGRAMA REPROGRAMADO", s["kicker"]),
        _p("Plano após scope discovery", s["title"]),
        _p(
            "O Gantt é a principal leitura operacional: concluídas/em andamento ficam congeladas e o trabalho futuro é reprogramado.",
            s["muted"],
        ),
        Spacer(1, 3 * mm),
        gantt_pages[0],
    ]

    for page_number, gantt_page in enumerate(gantt_pages[1:], start=2):
        story.extend(
            [
                PageBreak(),
                _p("CRONOGRAMA REPROGRAMADO", s["kicker"]),
                _p(
                    f"Plano após scope discovery · continuação {page_number}",
                    s["title"],
                ),
                _p(
                    "Continuação do Gantt na mesma escala temporal e na ordem original do Project.",
                    s["muted"],
                ),
                Spacer(1, 3 * mm),
                gantt_page,
            ]
        )

    detail_columns = [
        "ID",
        "Atividade",
        "Modo",
        "Início (h)",
        "Fim (h)",
        "Crítica atual",
        "Controla por",
    ]
    detail_widths = [
        10 * mm,
        43 * mm,
        18 * mm,
        15 * mm,
        15 * mm,
        18 * mm,
        25 * mm,
    ]
    if (
        "Pessoas" in schedule_df.columns
        and schedule_df["Pessoas"].fillna("—").astype(str).ne("—").any()
    ):
        detail_columns.append("Pessoas")
        detail_widths.append(45 * mm)

    story.extend(
        [
            Spacer(1, 5 * mm),
            _p("Detalhamento do cronograma", s["h2"]),
            _dataframe_table(
                schedule_df,
                detail_columns,
                max_rows=60,
                widths=detail_widths,
            ),
            Spacer(1, 4 * mm),
            _p(
                "Nota metodológica: o MRCPSP atual combina enumeração de modos em espaços pequenos "
                "e busca heurística em espaços maiores, sempre com SSGS. Quando multi-skill está habilitado, "
                "cada vaga de habilidade recebe uma pessoa e a mesma pessoa não pode ocupar duas atividades "
                "sobrepostas. O resultado é um plano factível, não uma prova de ótimo global para instâncias grandes.",
                s["muted"],
            ),
        ]
    )
    return _build_pdf(story)
