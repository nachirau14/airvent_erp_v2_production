"""Issued Material — everything issued in the last 15 days, grouped by project."""
import streamlit as st
import pandas as pd
from utils.db import get_all_issued_material, get_all_projects
from utils.ui_helpers import section_header, empty_state, styled_metric


def render():
    st.markdown("# 📤 Issued Material")
    st.markdown("*All material issued to production, by project — log auto-clears 15 days after issue*")
    st.markdown("---")

    issued = get_all_issued_material()
    if not issued:
        empty_state("📤", "No material issued in the last 15 days")
        return

    # Map project_id → project name for readable display
    projects = get_all_projects()
    proj_names = {p["project_id"]: p.get("name", p["project_id"]) for p in projects}
    for rec in issued:
        rec["project"] = proj_names.get(rec.get("project_id", ""), rec.get("project_id", "Unknown"))

    # Metrics
    c1, c2, c3 = st.columns(3)
    with c1:
        styled_metric("Records", len(issued), color="#1e40af")
    with c2:
        styled_metric("Projects", len(set(r["project"] for r in issued)), color="#0e7490")
    with c3:
        styled_metric("Issuers", len(set(r.get("issued_by", "") for r in issued)), color="#7c3aed")

    # Filters
    fc1, fc2 = st.columns([1, 2])
    with fc1:
        proj_filter = st.selectbox("Project", ["All"] + sorted(set(r["project"] for r in issued)), key="il_proj")
    with fc2:
        search = st.text_input("🔍 Search item / spec / issued by", key="il_search")

    filtered = issued
    if proj_filter != "All":
        filtered = [r for r in filtered if r["project"] == proj_filter]
    if search:
        s = search.lower()
        filtered = [r for r in filtered if s in r.get("item_name", "").lower()
                    or s in r.get("specification", "").lower()
                    or s in r.get("issued_by", "").lower()]

    if not filtered:
        st.caption("No records match the filters.")
        return

    st.markdown("---")

    # Grouped by project
    by_project = {}
    for rec in filtered:
        by_project.setdefault(rec["project"], []).append(rec)

    for project_name in sorted(by_project.keys()):
        recs = sorted(by_project[project_name], key=lambda x: x.get("issued_at", ""), reverse=True)
        st.markdown(f"### 📋 {project_name} — {len(recs)} item(s)")
        df = pd.DataFrame(recs)
        cols = ["item_name", "specification", "quantity", "unit", "issued_by", "issued_at", "product_id"]
        available = [c for c in cols if c in df.columns]
        if "issued_at" in df.columns:
            df["issued_at"] = df["issued_at"].str[:16].str.replace("T", " ")
        st.dataframe(df[available], use_container_width=True, hide_index=True)
        st.markdown("")
