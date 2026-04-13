"""
Password authentication for the ERP app.
Passwords are stored as SHA-256 hashes in st.secrets.
"""
import streamlit as st
import hashlib


def hash_password(password):
    """Return SHA-256 hash of a password."""
    return hashlib.sha256(password.encode()).hexdigest()


def check_auth(app_name="management"):
    """Show login form and verify password. Returns True if authenticated."""
    if st.session_state.get("authenticated"):
        return True

    st.markdown("""
    <div style="max-width:400px;margin:80px auto;text-align:center">
        <div style="font-size:3rem;margin-bottom:8px">🏭</div>
        <div style="font-size:1.5rem;font-weight:700;color:#0f172a">FabriFlow ERP</div>
        <div style="font-size:0.85rem;color:#64748b;margin-bottom:24px">
            {} Interface — Enter password to continue
        </div>
    </div>
    """.format("Management" if app_name == "management" else "Production"), unsafe_allow_html=True)

    with st.form("login_form"):
        password = st.text_input("Password", type="password", placeholder="Enter your password")
        submitted = st.form_submit_button("🔐 Login", use_container_width=True)

        if submitted:
            secret_key = f"{app_name}_password_hash"
            stored_hash = st.secrets.get("auth", {}).get(secret_key, "")

            if not stored_hash:
                st.error("No password configured. Add auth section to secrets.toml")
                return False

            if hash_password(password) == stored_hash:
                st.session_state["authenticated"] = True
                st.rerun()
            else:
                st.error("Incorrect password")
                return False

    return False


def logout():
    """Clear authentication state."""
    st.session_state["authenticated"] = False
    st.rerun()
