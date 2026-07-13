"""Issue Material — take material from inventory for production."""
import streamlit as st
import pandas as pd
from utils.db import (get_all_projects, get_production_trackers, get_all_inventory,
                       create_material_issue, get_all_issued_material)
from utils.ui_helpers import section_header, empty_state


def render():
    st.markdown("# 📦 Issue Material for Production")
    st.markdown("---")
    projects = get_all_projects()
    if not projects:
        st.warning("No projects."); return

    proj_opts = {f"{p['name']} ({p['project_id']})": p for p in projects}
    sel_proj = st.selectbox("Project", list(proj_opts.keys()), key="iss_proj")
    project = proj_opts[sel_proj]
    trackers = get_production_trackers(project["project_id"])
    product_id = ""
    if trackers:
        t_opts = {f"{t['product_name']} ({t['product_id']})": t for t in trackers}
        product_id = t_opts[st.selectbox("Product", list(t_opts.keys()), key="iss_t")]["product_id"]
    issued_by = st.text_input("Issued By *", key="iss_by")
    st.markdown("---")

    inventory = get_all_inventory()
    if not inventory:
        st.warning("No inventory."); return

    if "issue_items" not in st.session_state:
        st.session_state.issue_items = []

    search = st.text_input("🔍 Search", key="iss_s")
    filtered = [i for i in inventory if i.get("quantity", 0) > 0]
    if search:
        s = search.lower()
        filtered = [i for i in filtered if s in i.get("item_name", "").lower() or s in i.get("specification", "").lower()]

    for item in filtered[:20]:
        ic1, ic2, ic3, ic4 = st.columns([3, 1, 1, 1])
        with ic1:
            st.markdown(f"**{item['item_name']}**")
            st.caption(f"{item.get('category','')} | {item.get('specification','')}")
        with ic2: st.caption(f"Avail: {item.get('quantity',0)} {item.get('unit','')}")
        with ic3:
            qty = st.number_input("Qty", min_value=0.0, max_value=float(item.get("quantity",0)),
                                   step=1.0, key=f"iq_{item['item_id']}", label_visibility="collapsed")
        with ic4:
            if st.button("Add", key=f"ia_{item['item_id']}"):
                if qty > 0:
                    st.session_state.issue_items.append({"item_id": item["item_id"], "item_name": item["item_name"],
                        "specification": item.get("specification", ""), "category": item.get("category", ""),
                        "sub_category": item.get("sub_category", ""),
                        "quantity": qty, "unit": item.get("unit",""), "inventory_type": "raw"})
                    st.rerun()

    if st.session_state.issue_items:
        st.markdown("---")
        for idx, it in enumerate(st.session_state.issue_items):
            c1, c2, c3 = st.columns([4,1,1])
            with c1: st.markdown(f"**{it['item_name']}**")
            with c2: st.caption(f"{it['quantity']} {it['unit']}")
            with c3:
                if st.button("🗑️", key=f"ir_{idx}"):
                    st.session_state.issue_items.pop(idx); st.rerun()
        if st.button("✅ Confirm Issue", type="primary", use_container_width=True):
            if issued_by:
                r = create_material_issue(project["project_id"], product_id, st.session_state.issue_items, issued_by)
                st.success(f"Issued! ID: `{r['issue_id']}`"); st.session_state.issue_items = []; st.rerun()
            else: st.error("'Issued By' required.")

    # ─── Issued Material Log (auto-purged after 15 days) ─────────
    st.markdown("---")
    st.markdown("### 📋 Issued Material Log")
    st.caption("Entries auto-delete 15 days after issue (DynamoDB TTL).")
    issued = get_all_issued_material()
    if issued:
        df = pd.DataFrame(sorted(issued, key=lambda x: x.get("issued_at", ""), reverse=True))
        cols = ["item_name", "specification", "quantity", "unit", "project_id", "issued_by", "issued_at"]
        available = [c for c in cols if c in df.columns]
        if "issued_at" in df.columns:
            df["issued_at"] = df["issued_at"].str[:16].str.replace("T", " ")
        st.dataframe(df[available], use_container_width=True, hide_index=True, height=300)
        st.caption(f"{len(issued)} record(s) currently in the log")
    else:
        st.info("No material issued in the last 15 days.")
