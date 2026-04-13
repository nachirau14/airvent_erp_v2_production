"""
FabriFlow ERP — Management Interface
Password-protected. Deployed as a separate Streamlit app.
"""
import sys
from pathlib import Path
APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import streamlit as st
import importlib

st.set_page_config(page_title="FabriFlow ERP — Management", page_icon="🏭", layout="wide", initial_sidebar_state="expanded")

# ─── Auth Gate ────────────────────────────────────────────────────
from auth import check_auth, logout
if not check_auth("management"):
    st.stop()

# ─── CSS ──────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@300;400;500;600;700&display=swap');
html, body, [class*="css"] { font-family: 'DM Sans', sans-serif; color: #1e293b; }
[data-testid="stSidebar"] { background: #f8fafc; border-right: 1px solid #e2e8f0; }
[data-testid="stSidebar"] .stMarkdown p, [data-testid="stSidebar"] label,
[data-testid="stSidebar"] .stSelectbox label, [data-testid="stSidebar"] .stRadio label
{ color: #334155 !important; }
h1 { color: #0f172a !important; font-weight: 700 !important; }
h2 { color: #1e293b !important; font-weight: 600 !important; }
h3 { color: #334155 !important; font-weight: 600 !important; }
.stButton > button { border-radius: 8px; font-weight: 600; padding: 0.5rem 1.2rem; transition: all 0.2s; }
.stButton > button:hover { transform: translateY(-1px); box-shadow: 0 4px 12px rgba(0,0,0,0.1); }
.stTabs [data-baseweb="tab"] { border-radius: 8px 8px 0 0; padding: 8px 20px; font-weight: 600; }
[data-testid="stForm"] { border: 1px solid #e2e8f0; border-radius: 12px; padding: 20px; background: #fafbfc; }
.streamlit-expanderHeader { font-weight: 600; font-size: 1rem; color: #1e293b !important; }
details { border: 1px solid #e2e8f0 !important; border-radius: 8px !important; }
hr { border-color: #e2e8f0 !important; }
#MainMenu {visibility: hidden;} footer {visibility: hidden;}
</style>
""", unsafe_allow_html=True)

# ─── Sidebar ──────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("""
    <div style="text-align:center;padding:16px 0 8px 0">
        <div style="font-size:2.2rem">🏭</div>
        <div style="font-size:1.2rem;font-weight:700;color:#0f172a">FabriFlow ERP</div>
        <div style="font-size:0.7rem;color:#64748b">Management Interface</div>
    </div><hr style="border-color:#e2e8f0;margin:8px 0 16px 0">
    """, unsafe_allow_html=True)

    page = st.selectbox("Navigate", [
        "📊 Dashboard",
        "📦 Master Items",
        "📋 Projects & BOQ",
        "🚀 Order Staging",
        "👥 Vendors",
        "🔧 Service Vendors",
        "📦 Purchase Orders",
        "🛠️ Service POs",
        "🏗️ Production Tracking",
        "📦 Inventory",
        "✅ Finished Goods",
        "🚚 Dispatch",
        "📤 Bulk Upload",
    ])

    st.markdown("<hr style='border-color:#e2e8f0;margin:16px 0'>", unsafe_allow_html=True)
    if st.button("🔓 Logout", use_container_width=True):
        logout()

PAGE_MAP = {
    "📊 Dashboard": "views.dashboard",
    "📦 Master Items": "views.master_items",
    "📋 Projects & BOQ": "views.projects",
    "🚀 Order Staging": "views.order_staging",
    "👥 Vendors": "views.vendors",
    "🔧 Service Vendors": "views.service_vendors",
    "📦 Purchase Orders": "views.raw_material_po",
    "🛠️ Service POs": "views.service_po",
    "🏗️ Production Tracking": "views.production",
    "📦 Inventory": "views.inventory",
    "✅ Finished Goods": "views.finished_goods",
    "🚚 Dispatch": "views.dispatch",
    "📤 Bulk Upload": "views.bulk_upload",
}
mod = importlib.import_module(PAGE_MAP[page])
mod.render()
