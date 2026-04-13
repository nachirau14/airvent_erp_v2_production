"""
FabriFlow ERP — Production Floor Interface
"""
import sys
from pathlib import Path
APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import streamlit as st
import importlib

st.set_page_config(page_title="FabriFlow ERP — Production", page_icon="🔧", layout="wide", initial_sidebar_state="expanded")

from auth import check_auth, logout
if not check_auth("production"):
    st.stop()

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@300;400;500;600;700&display=swap');
html, body, [class*="css"] { font-family: 'DM Sans', sans-serif; color: #1e293b; }
[data-testid="stSidebar"] { background: #f8fafc; border-right: 1px solid #e2e8f0; }
[data-testid="stSidebar"] .stMarkdown p, [data-testid="stSidebar"] label,
[data-testid="stSidebar"] .stRadio label { color: #334155 !important; }
[data-testid="stSidebar"] [data-baseweb="radio"] label { font-size: 0.95rem !important; padding: 2px 0 !important; }
h1 { color: #0f172a !important; font-weight: 700 !important; }
h2 { color: #1e293b !important; } h3 { color: #334155 !important; }
.stButton > button { border-radius: 8px; font-weight: 600; }
[data-testid="stForm"] { border: 1px solid #e2e8f0; border-radius: 12px; padding: 20px; background: #fafbfc; }
hr { border-color: #e2e8f0 !important; }
#MainMenu {visibility: hidden;} footer {visibility: hidden;}
</style>
""", unsafe_allow_html=True)

with st.sidebar:
    st.markdown("""
    <div style="text-align:center;padding:16px 0 8px 0">
        <div style="font-size:2.2rem">🔧</div>
        <div style="font-size:1.2rem;font-weight:700;color:#0f172a">FabriFlow ERP</div>
        <div style="font-size:0.7rem;color:#64748b">Production Floor</div>
    </div><hr style="border-color:#e2e8f0;margin:8px 0 16px 0">
    """, unsafe_allow_html=True)

    page = st.radio(
        "Navigation",
        [
            "📦 Issue Material",
            "🔄 Return Material",
            "📋 View Inventory",
            "🏗️ Update Production",
        ],
        label_visibility="collapsed",
    )

    st.markdown("<hr style='border-color:#e2e8f0;margin:16px 0'>", unsafe_allow_html=True)
    if st.button("🔓 Logout", use_container_width=True):
        logout()

PAGE_MAP = {
    "📦 Issue Material": "views.issue_material",
    "🔄 Return Material": "views.return_material",
    "📋 View Inventory": "views.view_inventory",
    "🏗️ Update Production": "views.update_production",
}
mod = importlib.import_module(PAGE_MAP[page])
mod.render()
