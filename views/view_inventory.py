"""View Inventory — read-only stock view for production floor."""
import streamlit as st
import pandas as pd
from utils.db import get_all_inventory, get_all_scrap
from utils.ui_helpers import section_header, empty_state, styled_metric


def render():
    st.markdown("# 📋 View Inventory")
    st.markdown("---")

    tab_raw, tab_scrap = st.tabs(["📦 Raw Material", "♻️ Scrap Store"])

    with tab_scrap:
        scrap = [s for s in get_all_scrap() if s.get("quantity", 0) > 0]
        if not scrap:
            empty_state("♻️", "Scrap store is empty")
        else:
            styled_metric("Scrap Items", len(scrap), color="#d97706")
            ss = st.text_input("🔍 Search scrap", key="vi_ss")
            sf = scrap
            if ss:
                q = ss.lower()
                sf = [i for i in scrap if q in i.get("item_name", "").lower()
                      or q in i.get("specification", "").lower()
                      or q in i.get("notes", "").lower()]
            if sf:
                sdf = pd.DataFrame(sorted(sf, key=lambda x: x.get("added_at", ""), reverse=True))
                scols = ["item_name", "specification", "quantity", "unit", "source_po", "notes", "added_at"]
                savail = [c for c in scols if c in sdf.columns]
                if "added_at" in sdf.columns:
                    sdf["added_at"] = sdf["added_at"].str[:16].str.replace("T", " ")
                st.dataframe(sdf[savail], use_container_width=True, hide_index=True, height=400)
                st.caption(f"{len(sf)} scrap item(s) — includes material returned from production")

    with tab_raw:
        inventory = get_all_inventory()
        if not inventory:
            empty_state("📦", "No inventory"); return

        c1, c2, c3 = st.columns(3)
        with c1: styled_metric("Total Items", len(inventory), color="#1e40af")
        with c2: styled_metric("In Stock", len([i for i in inventory if i.get("quantity",0) > 0]), color="#16a34a")
        with c3: styled_metric("Out of Stock", len([i for i in inventory if i.get("quantity",0) <= 0]), color="#dc2626")

        st.markdown("")
        sc1, sc2 = st.columns([2, 1])
        with sc1: search = st.text_input("🔍 Search", key="vi_s")
        with sc2: cat = st.selectbox("Category", ["All"] + sorted(set(i.get("category","") for i in inventory)), key="vi_c")

        filtered = inventory
        if search:
            s = search.lower()
            filtered = [i for i in filtered if s in i.get("item_name","").lower() or s in i.get("specification","").lower() or s in i.get("category","").lower()]
        if cat != "All":
            filtered = [i for i in filtered if i.get("category") == cat]

        if filtered:
            df = pd.DataFrame(filtered)
            cols = ["item_name", "category", "specification", "quantity", "unit", "location"]
            available = [c for c in cols if c in df.columns]
            display_df = df[available].copy()
            display_df.columns = [c.replace("_"," ").title() for c in available]

            def hl(row):
                q = row.get("Quantity", 0)
                if q <= 0: return ["background-color:#fee2e2;color:#991b1b"]*len(row)
                elif q < 5: return ["background-color:#fef3c7;color:#92400e"]*len(row)
                return [""]*len(row)

            st.dataframe(display_df.style.apply(hl, axis=1), use_container_width=True, hide_index=True, height=500)
            st.caption(f"Showing {len(filtered)} of {len(inventory)}")
