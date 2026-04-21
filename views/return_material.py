"""Return Material — return unused material to inventory."""
import streamlit as st
from utils.db import get_all_inventory, update_inventory_qty
from utils.ui_helpers import section_header, empty_state


def render():
    st.markdown("# 🔄 Return Material")
    st.markdown("---")
    inventory = get_all_inventory()
    if not inventory:
        empty_state("📦", "No inventory items"); return

    search = st.text_input("🔍 Search", key="ret_s")
    filtered = inventory
    if search:
        s = search.lower()
        filtered = [i for i in inventory if s in i.get("item_name","").lower() or s in i.get("specification","").lower()]

    item_opts = {f"{i['item_name']} | {i.get('specification','')} (Stock: {i.get('quantity',0)} {i.get('unit','')})": i for i in filtered}
    sel = st.selectbox("Select Item *", list(item_opts.keys()), key="ret_sel")
    item = item_opts[sel]

    st.markdown(f"""
    <div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:10px;padding:16px;margin:12px 0">
        <div style="font-weight:700;color:#166534">{item['item_name']}</div>
        <div style="color:#15803d;font-size:0.85rem;margin-top:4px">
            Stock: <strong>{item.get('quantity',0)} {item.get('unit','')}</strong> • {item.get('category','')} • {item.get('location','')}
        </div>
    </div>""", unsafe_allow_html=True)

    with st.form("return_form", clear_on_submit=True):
        c1, c2 = st.columns(2)
        with c1: qty = st.number_input("Return Qty *", min_value=0.1, step=0.5, key="ret_q")
        with c2: by = st.text_input("Returned By *", key="ret_by")
        reason = st.text_area("Reason", key="ret_r")
        if st.form_submit_button("✅ Return to Store", type="primary", use_container_width=True):
            if qty > 0 and by:
                update_inventory_qty(item["item_id"], qty)
                st.success(f"Returned **{qty} {item.get('unit','')}** of **{item['item_name']}**")
                st.rerun()
            else: st.error("Quantity and Returned By are required.")
