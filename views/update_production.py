"""Update Production — end-of-day stage updates."""
import streamlit as st
from utils.db import get_all_projects, get_production_trackers, update_production_stage
from utils.ui_helpers import section_header, empty_state, production_stage_color, render_production_progress
from config import PRODUCTION_STAGES


def render():
    st.markdown("# 🏗️ Update Production Status")
    st.markdown("---")
    projects = get_all_projects()
    if not projects:
        empty_state("🏗️", "No projects"); return

    active = [p for p in projects if p.get("status") in ("In Production", "Procurement", "BOQ Ready", "Planning")]
    proj_opts = {f"{p['name']} ({p['project_id']})": p for p in (active or projects)}
    sel = st.selectbox("Project", list(proj_opts.keys()), key="up_proj")
    project = proj_opts[sel]

    trackers = get_production_trackers(project["project_id"])
    if not trackers:
        empty_state("🏗️", "No products tracked", "Add via Management → Production Tracking"); return

    for tracker in trackers:
        pt = tracker.get("product_type", "Custom")
        stages = PRODUCTION_STAGES.get(pt, PRODUCTION_STAGES["Custom"])
        sd = tracker.get("stages", {})

        st.markdown(f"""
        <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:12px;padding:16px 20px;margin-bottom:8px">
            <div style="font-size:1.1rem;font-weight:700;color:#0f172a">🔩 {tracker['product_name']}</div>
            <div style="font-size:0.8rem;color:#64748b">Type: {pt} • Qty: {tracker.get('quantity',1)}</div>
        </div>""", unsafe_allow_html=True)

        render_production_progress(sd, stages)
        changes = False
        for row_start in range(0, len(stages), 3):
            cols = st.columns(3)
            for ci, (sn, ss) in enumerate(stages[row_start:row_start+3]):
                with cols[ci]:
                    cur = sd.get(sn, ss[0])
                    color = production_stage_color(cur)
                    st.markdown(f"""
                    <div style="border-left:4px solid {color};padding:8px 10px;margin:4px 0;background:#f8fafc;border-radius:0 6px 6px 0">
                        <div style="font-size:0.75rem;font-weight:600;color:#334155;text-transform:uppercase">{sn}</div>
                        <div style="font-size:0.8rem;color:{color};font-weight:700">● {cur}</div>
                    </div>""", unsafe_allow_html=True)
                    new = st.selectbox(f"Update", ss, index=ss.index(cur) if cur in ss else 0,
                                       key=f"p_{tracker['product_id']}_{sn}", label_visibility="collapsed")
                    if new != cur: changes = True

        if changes:
            if st.button(f"💾 Save — {tracker['product_name']}", key=f"sv_{tracker['product_id']}",
                         type="primary", use_container_width=True):
                for sn, ss in stages:
                    cur = sd.get(sn, ss[0])
                    nv = st.session_state.get(f"p_{tracker['product_id']}_{sn}", cur)
                    if nv != cur:
                        update_production_stage(project["project_id"], tracker["product_id"], sn, nv)
                st.success(f"Updated **{tracker['product_name']}**!")
                st.rerun()
        st.markdown("---")
