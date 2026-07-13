"""Return Material — return items from the ISSUED MATERIAL log back to inventory."""
import streamlit as st
from utils.db import get_all_issued_material, return_issued_material
from utils.ui_helpers import section_header, empty_state


def render():
    st.markdown("# 🔄 Return Material")
    st.markdown("*Select from material issued in the last 15 days — returns go back to raw inventory*")
    st.markdown("---")

    issued = get_all_issued_material()
    if not issued:
        empty_state("📋", "No issued material to return (log auto-clears after 15 days)")
        return

    search = st.text_input("🔍 Search issued material", key="ret_s")
    filtered = sorted(issued, key=lambda x: x.get("issued_at", ""), reverse=True)
    if search:
        s = search.lower()
        filtered = [i for i in filtered if s in i.get("item_name", "").lower()
                    or s in i.get("specification", "").lower()
                    or s in i.get("issued_by", "").lower()]

    rec_opts = {
        f"{i['item_name']} | {i.get('specification','')} | Issued: {i.get('quantity',0)} {i.get('unit','')} "
        f"on {i.get('issued_at','')[:10]} by {i.get('issued_by','')} ({i['record_id']})": i
        for i in filtered}
    if not rec_opts:
        st.caption("No records match your search.")
        return

    sel = st.selectbox("Select Issued Record *", list(rec_opts.keys()), key="ret_sel")
    rec = rec_opts[sel]
    issued_qty = float(rec.get("quantity", 0))

    st.markdown(f"""
    <div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:10px;padding:16px;margin:12px 0">
        <div style="font-weight:700;color:#166534">{rec['item_name']}</div>
        <div style="color:#15803d;font-size:0.85rem;margin-top:4px">
            Issued: <strong>{issued_qty} {rec.get('unit','')}</strong> •
            {rec.get('specification','')} • Project: {rec.get('project_id','')} •
            By {rec.get('issued_by','')} on {rec.get('issued_at','')[:10]}
        </div>
    </div>""", unsafe_allow_html=True)

    with st.form("return_form", clear_on_submit=True):
        c1, c2 = st.columns(2)
        with c1:
            qty = st.number_input("Return Qty *", min_value=0.0, max_value=issued_qty,
                                   step=1.0, key="ret_q")
        with c2:
            by = st.text_input("Returned By *", key="ret_by")
        reason = st.text_area("Reason", key="ret_r")
        if st.form_submit_button("✅ Return to Store", type="primary", use_container_width=True):
            if qty > 0 and by:
                return_issued_material(rec, qty, by, reason)
                remaining = issued_qty - qty
                if remaining <= 0:
                    st.success(f"Returned **{qty} {rec.get('unit','')}** of **{rec['item_name']}** — record fully returned and closed.")
                else:
                    st.success(f"Returned **{qty} {rec.get('unit','')}** of **{rec['item_name']}** — {remaining} still issued.")
                st.rerun()
            else:
                st.error("Quantity and Returned By are required.")
