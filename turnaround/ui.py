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

        .ta-hero {
            border: 1px solid var(--ta-line);
            border-radius: 20px;
            padding: 1.6rem 1.8rem;
            margin-bottom: 1.25rem;
            background: linear-gradient(135deg, #ffffff 0%, #f2f8f7 100%);
            box-shadow: 0 10px 30px rgba(19, 34, 56, 0.05);
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
            font-size: clamp(2rem, 4vw, 3.3rem);
            line-height: 1.03;
            letter-spacing: -.035em;
            margin: 0 0 .65rem 0;
        }

        .ta-hero p {
            color: var(--ta-muted);
            font-size: 1.02rem;
            line-height: 1.55;
            margin: 0;
            max-width: 72rem;
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
            min-height: 116px;
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
        </style>
        """,
        unsafe_allow_html=True,
    )


def hero(title: str, subtitle: str, kicker: str = "TURNAROUND") -> None:
    st.markdown(
        f"""
        <div class="ta-hero">
            <div class="ta-kicker">{escape(kicker)}</div>
            <h1>{escape(title)}</h1>
            <p>{escape(subtitle)}</p>
        </div>
        """,
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
