"""
UI helper utilities for the Streamlit ERP application.
Light theme — all elements designed for white/light backgrounds with high contrast text.
"""
import streamlit as st
import pandas as pd


def styled_metric(label, value, delta=None, color="#2563eb"):
    """Render a styled metric card on white background."""
    delta_html = ""
    if delta is not None:
        d_color = "#16a34a" if delta >= 0 else "#dc2626"
        d_icon = "▲" if delta >= 0 else "▼"
        delta_html = f'<div style="color:{d_color};font-size:0.85rem;margin-top:2px">{d_icon} {abs(delta)}</div>'
    st.markdown(f"""
    <div style="background:#ffffff;border:1px solid #e2e8f0;box-shadow:0 1px 3px rgba(0,0,0,0.06);
         border-radius:12px;padding:16px 20px;text-align:center;border-top:3px solid {color}">
        <div style="color:#64748b;font-size:0.75rem;text-transform:uppercase;letter-spacing:1px;font-weight:600">{label}</div>
        <div style="color:#0f172a;font-size:1.8rem;font-weight:700;margin-top:4px">{value}</div>
        {delta_html}
    </div>""", unsafe_allow_html=True)


def po_status_badge(status):
    """Return colored HTML badge for PO status — readable on white."""
    colors = {
        "Draft": ("#475569", "#f1f5f9", "#94a3b8"),
        "Placed": ("#1e40af", "#dbeafe", "#60a5fa"),
        "Partially Received": ("#92400e", "#fef3c7", "#fbbf24"),
        "In Progress": ("#92400e", "#fef3c7", "#fbbf24"),
        "Complete": ("#166534", "#dcfce7", "#4ade80"),
        "Cancelled": ("#991b1b", "#fee2e2", "#f87171"),
    }
    fg, bg, border = colors.get(status, ("#475569", "#f1f5f9", "#94a3b8"))
    return f'<span style="background:{bg};color:{fg};padding:4px 12px;border-radius:20px;font-size:0.8rem;font-weight:600;border:1px solid {border}40;display:inline-block">{status}</span>'


def project_status_badge(status):
    """Return colored HTML badge for project status — readable on white."""
    colors = {
        "Planning": ("#475569", "#f1f5f9", "#94a3b8"),
        "BOQ Ready": ("#1e40af", "#dbeafe", "#60a5fa"),
        "Procurement": ("#5b21b6", "#ede9fe", "#a78bfa"),
        "In Production": ("#92400e", "#fef3c7", "#fbbf24"),
        "Complete": ("#166534", "#dcfce7", "#4ade80"),
        "Dispatched": ("#065f46", "#d1fae5", "#34d399"),
    }
    fg, bg, border = colors.get(status, ("#475569", "#f1f5f9", "#94a3b8"))
    return f'<span style="background:{bg};color:{fg};padding:4px 12px;border-radius:20px;font-size:0.8rem;font-weight:600;border:1px solid {border}40;display:inline-block">{status}</span>'


def production_stage_color(status):
    """Return color for production stage status — bright on white."""
    colors = {
        "Pending": "#dc2626",
        "Ordered": "#d97706",
        "Issued": "#2563eb",
        "In Progress": "#d97706",
        "Received": "#16a34a",
        "Complete": "#16a34a",
    }
    return colors.get(status, "#64748b")


def render_production_progress(stages_dict, stage_definitions):
    """Render a visual progress bar on white background."""
    total = len(stage_definitions)
    completed = sum(1 for name, _ in stage_definitions if stages_dict.get(name) in ("Complete", "Received"))
    pct = int((completed / total) * 100) if total > 0 else 0

    bar_color = "#16a34a" if pct == 100 else "#2563eb" if pct > 50 else "#d97706" if pct > 0 else "#e2e8f0"

    html = f"""
    <div style="margin:8px 0;padding:12px 16px;background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px">
        <div style="display:flex;justify-content:space-between;margin-bottom:6px">
            <span style="font-size:0.8rem;color:#475569;font-weight:500">Production Progress</span>
            <span style="font-size:0.8rem;font-weight:700;color:#0f172a">{completed}/{total} stages — {pct}%</span>
        </div>
        <div style="background:#e2e8f0;border-radius:8px;height:10px;overflow:hidden">
            <div style="background:{bar_color};width:{pct}%;height:100%;border-radius:8px;
                 transition:width 0.5s ease"></div>
        </div>
    </div>"""
    st.markdown(html, unsafe_allow_html=True)


def format_currency(amount):
    """Format amount as INR."""
    if amount is None:
        return "₹0.00"
    return f"₹{amount:,.2f}"


def empty_state(icon, message, sub_message=""):
    """Render an empty state placeholder — visible on white."""
    st.markdown(f"""
    <div style="text-align:center;padding:48px 20px;background:#f8fafc;border:1px dashed #cbd5e1;border-radius:12px;margin:12px 0">
        <div style="font-size:3rem;margin-bottom:12px">{icon}</div>
        <div style="font-size:1.1rem;font-weight:600;color:#334155">{message}</div>
        <div style="font-size:0.85rem;margin-top:6px;color:#64748b">{sub_message}</div>
    </div>""", unsafe_allow_html=True)


def section_header(title, icon=""):
    """Render a styled section header — dark text on white."""
    st.markdown(f"""
    <div style="display:flex;align-items:center;gap:10px;margin:24px 0 16px 0;padding-bottom:10px;
         border-bottom:2px solid #e2e8f0">
        <span style="font-size:1.4rem">{icon}</span>
        <span style="font-size:1.2rem;font-weight:700;color:#0f172a">{title}</span>
    </div>""", unsafe_allow_html=True)
