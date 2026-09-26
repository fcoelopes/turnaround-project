from __future__ import annotations

from html import escape

import streamlit as st


def apply_app_style() -> None:
    st.markdown(
        """
        <style>
        :root {
            --ta-ink: #132238;
            --ta-muted: #607086;
            --ta-line: #dfe6ee;
            --ta-surface: #ffffff;
            --ta-soft: #f5f7fa;
            --ta-accent: #0f766e;
            --ta-accent-soft: #e6f5f2;
            --ta-warn: #9a6700;
            --ta-danger: #b42318;
        }

        .stApp {
            background:
                radial-gradient(circle at 100% 0%, rgba(15,118,110,.08), transparent 30rem),
                #f7f9fb;
        }

        .block-container {
            max-width: 1440px;
            padding-top: 2rem;
            padding-bottom: 4rem;
        }

        .ta-app-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 1.25rem;
            background: #1f2b4d;
            color: #ffffff;
            border-radius: 20px;
            padding: 1rem 1.35rem;
            margin: 0 0 1rem 0;
            box-shadow: 0 8px 24px rgba(19,34,56,.08);
        }

        .ta-app-header-left {
            display: flex;
            align-items: center;
            gap: 1rem;
            min-width: 0;
        }

        .ta-app-badge {
            width: 3.5rem;
            height: 3.5rem;
            border-radius: 14px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            flex: 0 0 auto;
            background: #8ea2ff;
            color: #15223d;
            font-size: 1rem;
            font-weight: 850;
            letter-spacing: -.02em;
        }

        .ta-app-copy {
            min-width: 0;
        }

        .ta-app-title {
            margin: 0;
            color: #ffffff;
            font-size: .92rem;
            font-weight: 850;
            letter-spacing: .11em;
            text-transform: uppercase;
            line-height: 1.2;
        }

        .ta-app-subtitle {
            margin: .35rem 0 0 0;
            color: #dbe4ff;
            font-size: .88rem;
            line-height: 1.35;
        }

        .ta-app-context {
            color: #e7ecff;
            font-size: .84rem;
            line-height: 1.35;
            text-align: right;
            white-space: nowrap;
            flex: 0 0 auto;
        }

        .ta-hero {
            display: grid;
            grid-template-columns: minmax(0, 1.7fr) minmax(240px, .7fr);
            gap: 2rem;
            align-items: end;
            border-bottom: 1px solid var(--ta-line);
            padding: .5rem 0 1.35rem;
            margin-bottom: 1rem;
        }

        .ta-kicker {
            color: var(--ta-accent);
            font-size: .72rem;
            font-weight: 800;
            letter-spacing: .12em;
            text-transform: uppercase;
            margin-bottom: .4rem;
        }

        .ta-hero h1 {
            color: var(--ta-ink);
            font-size: clamp(1.9rem, 3.4vw, 3rem);
            line-height: 1.04;
            letter-spacing: -.035em;
            margin: 0 0 .55rem 0;
        }

        .ta-hero p {
            color: var(--ta-muted);
            font-size: .98rem;
            line-height: 1.55;
            margin: 0;
            max-width: 60rem;
        }

        .ta-hero-note {
            border-left: 3px solid var(--ta-accent);
            padding: .25rem 0 .25rem 1rem;
        }

        .ta-hero-note strong {
            display: block;
            color: var(--ta-ink);
            font-size: 1rem;
            margin-bottom: .25rem;
        }

        .ta-hero-note span {
            color: var(--ta-muted);
            font-size: .82rem;
            line-height: 1.4;
        }

        .ta-flow {
            display: grid;
            grid-template-columns: repeat(5, minmax(0, 1fr));
            border-top: 1px solid var(--ta-line);
            border-bottom: 1px solid var(--ta-line);
            margin: 0 0 1.2rem;
        }

        .ta-flow-step {
            padding: .7rem .8rem;
            border-right: 1px solid var(--ta-line);
        }

        .ta-flow-step:last-child {
            border-right: 0;
        }

        .ta-flow-step b {
            display: block;
            color: var(--ta-accent);
            font-size: .67rem;
            letter-spacing: .08em;
            margin-bottom: .2rem;
        }

        .ta-flow-step span {
            color: var(--ta-ink);
            font-size: .82rem;
            font-weight: 750;
        }

        .ta-flow-step small {
            display: block;
            color: var(--ta-muted);
            font-size: .72rem;
            margin-top: .15rem;
        }

        .ta-section {
            display: flex;
            align-items: baseline;
            gap: .65rem;
            margin: 1.8rem 0 .8rem 0;
        }

        .ta-section-index {
            display: inline-flex;
            width: 1.8rem;
            height: 1.8rem;
            align-items: center;
            justify-content: center;
            border-radius: .6rem;
            color: var(--ta-accent);
            background: var(--ta-accent-soft);
            font-weight: 800;
            font-size: .85rem;
        }

        .ta-section-title {
            color: var(--ta-ink);
            font-size: 1.25rem;
            font-weight: 750;
            letter-spacing: -.015em;
        }

        .ta-section-subtitle {
            color: var(--ta-muted);
            font-size: .9rem;
            margin-top: .12rem;
        }

        div[data-testid="stMetric"] {
            background: var(--ta-surface);
            border: 1px solid var(--ta-line);
            border-radius: 16px;
            padding: 1rem 1.05rem;
            box-shadow: 0 5px 18px rgba(19,34,56,.035);
            min-height: 96px;
        }

        div[data-testid="stMetric"] label {
            color: var(--ta-muted);
            font-weight: 650;
        }

        div[data-testid="stMetricValue"] {
            color: var(--ta-ink);
            font-weight: 800;
        }

        div[data-testid="stFileUploader"] {
            background: rgba(255,255,255,.75);
            border: 1px solid var(--ta-line);
            border-radius: 16px;
            padding: .45rem .75rem;
        }

        div[data-testid="stDataFrame"] {
            border: 1px solid var(--ta-line);
            border-radius: 14px;
            overflow: hidden;
        }

        div[data-testid="stPlotlyChart"] {
            border: 1px solid var(--ta-line);
            border-radius: 16px;
            background: #fff;
            padding: .35rem;
            box-shadow: 0 5px 18px rgba(19,34,56,.035);
        }

        div[data-testid="stExpander"] {
            border: 1px solid var(--ta-line);
            border-radius: 14px;
            background: rgba(255,255,255,.78);
        }

        .ta-status {
            border-radius: 14px;
            padding: .9rem 1rem;
            border: 1px solid var(--ta-line);
            background: var(--ta-surface);
            margin: .35rem 0 .9rem 0;
        }

        .ta-status strong {
            color: var(--ta-ink);
        }

        .ta-status.ok {
            border-left: 5px solid #16803a;
            background: #f0faf3;
        }

        .ta-status.warn {
            border-left: 5px solid #d69200;
            background: #fff8e5;
        }

        .ta-status.danger {
            border-left: 5px solid #d92d20;
            background: #fff3f2;
        }

        .ta-note {
            color: var(--ta-muted);
            font-size: .83rem;
            line-height: 1.45;
        }

        button[kind="primary"] {
            border-radius: 12px !important;
            font-weight: 750 !important;
        }

        .stDownloadButton button {
            border-radius: 12px;
            font-weight: 700;
        }

        [data-testid="stSidebar"] {
            border-right: 1px solid var(--ta-line);
        }

        .stTabs [data-baseweb="tab-list"] {
            gap: .35rem;
            border-bottom: 1px solid var(--ta-line);
            padding-bottom: .35rem;
            overflow-x: auto;
        }

        .stTabs [data-baseweb="tab"] {
            height: 40px;
            border-radius: 8px;
            padding: 0 .8rem;
            color: var(--ta-muted);
        }

        .stTabs [aria-selected="true"] {
            background: var(--ta-accent-soft) !important;
            color: var(--ta-accent) !important;
        }

        @media (max-width: 900px) {
            .ta-app-header {
                align-items: flex-start;
                flex-direction: column;
                gap: .8rem;
                border-radius: 16px;
            }

            .ta-app-context {
                text-align: left;
                white-space: normal;
                padding-left: 4.5rem;
            }

            .ta-hero {
                grid-template-columns: 1fr;
                gap: 1rem;
            }
            .ta-flow {
                grid-template-columns: 1fr 1fr;
            }
            .ta-flow-step:nth-child(2n) {
                border-right: 0;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def app_header(
    title: str,
    subtitle: str,
    *,
    badge: str = "T/A",
    context: str | None = None,
) -> None:
    """Compact product identity header.

    This is intentionally low-height: identity and context only, no marketing hero.
    """
    context_html = (
        f'<div class="ta-app-context">{escape(context)}</div>'
        if context
        else ""
    )
    st.markdown(
        f"""
        <div class="ta-app-header">
            <div class="ta-app-header-left">
                <div class="ta-app-badge">{escape(badge)}</div>
                <div class="ta-app-copy">
                    <div class="ta-app-title">{escape(title)}</div>
                    <div class="ta-app-subtitle">{escape(subtitle)}</div>
                </div>
            </div>
            {context_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def hero(
    title: str,
    subtitle: str,
    kicker: str = "TURNAROUND",
    *,
    note_title: str | None = None,
    note_body: str | None = None,
) -> None:
    note_html = ""
    if note_title or note_body:
        note_html = (
            '<aside class="ta-hero-note">'
            f'<strong>{escape(note_title or "")}</strong>'
            f'<span>{escape(note_body or "")}</span>'
            '</aside>'
        )

    st.markdown(
        f"""
        <div class="ta-hero">
            <div>
                <div class="ta-kicker">{escape(kicker)}</div>
                <h1>{escape(title)}</h1>
                <p>{escape(subtitle)}</p>
            </div>
            {note_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def workflow_strip(steps: list[tuple[str, str, str]]) -> None:
    """Compact decision workflow; navigation cue, not decoration."""
    cells = []
    for index, (title, label, detail) in enumerate(steps, start=1):
        cells.append(
            '<div class="ta-flow-step">'
            f'<b>{index:02d} · {escape(title.upper())}</b>'
            f'<span>{escape(label)}</span>'
            f'<small>{escape(detail)}</small>'
            '</div>'
        )
    st.markdown(
        '<div class="ta-flow">' + "".join(cells) + '</div>',
        unsafe_allow_html=True,
    )


def section(index: str, title: str, subtitle: str | None = None) -> None:
    subtitle_html = (
        f'<div class="ta-section-subtitle">{escape(subtitle)}</div>'
        if subtitle
        else ""
    )
    st.markdown(
        f"""
        <div class="ta-section">
            <div class="ta-section-index">{escape(index)}</div>
            <div>
                <div class="ta-section-title">{escape(title)}</div>
                {subtitle_html}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def status(message: str, tone: str = "ok", title: str | None = None) -> None:
    tone = tone if tone in {"ok", "warn", "danger"} else "ok"
    title_html = f"<strong>{escape(title)}</strong> " if title else ""
    st.markdown(
        f'<div class="ta-status {tone}">{title_html}{escape(message)}</div>',
        unsafe_allow_html=True,
    )
