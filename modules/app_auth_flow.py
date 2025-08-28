# modules/app_auth_flow.py
from __future__ import annotations

import streamlit as st
import requests
from urllib.parse import urlencode
from typing import Optional, Dict

from .app_utils import get_qp, clear_qp
from .auth import build_flow_web, SCOPES_PERSONAL_FULL, fetch_userinfo, creds_to_dict
from .utils import token_store

CARD_CSS = """
<style>
.login-card {
  border:1px solid #e5e7eb; border-radius:16px; padding:20px 18px; margin:8px 0 18px 0;
  background:#fff; box-shadow:0 8px 24px rgba(17,24,39,.06);
}
.login-card h3 { margin:0 0 6px 0; font-size:1.15rem; }
.login-card p  { margin:0 0 12px 0; color:#374151; }
.login-btn {
  display:inline-flex; align-items:center; gap:.6rem; padding:10px 14px; border-radius:10px;
  border:1px solid #d1d5db; text-decoration:none; font-weight:600;
}
.login-btn:hover { background:#f3f4f6; }
.login-btn img { width:18px; height:18px; }
</style>
"""

def _full_current_redirect_url(redirect_uri: str) -> str:
    qp = get_qp()
    flat = {k: (v[0] if isinstance(v, list) else v) for k, v in qp.items()}
    qs = urlencode(flat, doseq=False)
    return f"{redirect_uri}?{qs}" if qs else redirect_uri

def step0_google_identity() -> Optional[Dict[str, str]]:
    st.subheader("Inicia sesión con tu cuenta personal de Nomadic")

    # Construir flow web una sola vez
    if "oauth_oidc_web" not in st.session_state:
        flow = build_flow_web(SCOPES_PERSONAL_FULL)
        # Para refresh_token consistente pedimos prompt=consent
        auth_url, state = flow.authorization_url(
            prompt="consent select_account",
            access_type="offline",
            include_granted_scopes=True,  # bool OK para google-auth
        )
        st.session_state["oauth_oidc_web"] = {
            "flow": flow,
            "auth_url": auth_url,
            "state": state,
            "redirect_uri": flow.redirect_uri,
        }

    oo = st.session_state["oauth_oidc_web"]

    # Si volvimos del redirect con code+state, hacemos el intercambio
    qp = get_qp()
    code = qp.get("code", [None])[0] if isinstance(qp.get("code"), list) else qp.get("code")
    state_in = qp.get("state", [None])[0] if isinstance(qp.get("state"), list) else qp.get("state")

    if code and state_in:
        try:
            if state_in != oo.get("state") and st.session_state.get("DEBUG"):
                st.warning("Aviso (DEBUG): 'state' no coincide; google-auth también valida internamente.")
            flow = oo["flow"]
            auth_response = _full_current_redirect_url(oo["redirect_uri"])
            flow.fetch_token(authorization_response=auth_response)
            creds = flow.credentials
            info = fetch_userinfo(creds)

            # Guardar identidad y credenciales
            st.session_state["_google_identity"] = info
            data = creds_to_dict(creds)
            st.session_state["creds_dest"] = data
            token_store.save("creds_dest", data)

            clear_qp()

            # Tarjeta compacta de éxito
            with st.container():
                c1, c2 = st.columns([0.12, 0.88])
                with c1:
                    if info.get("picture"):
                        st.image(info["picture"], width=48)
                with c2:
                    st.success(f"Sesión iniciada como {info.get('email','—')}")
            return info

        except Exception as e:
            msg = str(e)
            if "redirect_uri_mismatch" in msg or "redirect_uri_mismatch" in getattr(e, "args", [""])[0]:
                st.error("No se pudo completar el inicio de sesión: *redirect_uri_mismatch*.")
                if st.session_state.get("DEBUG"):
                    st.info(
                        "Verificá en Google Cloud **(OAuth 2.0 Client ID – Web)** que tengas **ambas** URIs:\n"
                        "• https://hernangsoto.streamlit.app\n"
                        "• https://hernangsoto.streamlit.app/\n"
                        f"Usando redirect_uri: `{oo.get('redirect_uri')}`"
                    )
                st.stop()
            elif "invalid_client" in msg or "unauthorized_client" in msg:
                st.error("Cliente OAuth Web inválido. Revisá client_id/client_secret en [auth].")
                if st.session_state.get("DEBUG"):
                    st.caption(msg)
                st.stop()
            else:
                st.error("No se pudo completar el inicio de sesión. Verificá el cliente web y el redirect_uri.")
                if st.session_state.get("DEBUG"):
                    st.caption(msg)
                st.stop()

    # UI inicial: solo una tarjeta con el botón
    st.markdown(CARD_CSS, unsafe_allow_html=True)
    with st.container():
        st.markdown('<div class="login-card">', unsafe_allow_html=True)
        st.markdown("<h3>Tu cuenta personal</h3>", unsafe_allow_html=True)
        st.markdown("<p>Usaremos esta cuenta para guardar reportes en tu Drive y consultar Search Console.</p>", unsafe_allow_html=True)
        st.markdown(
            f'<a class="login-btn" href="{oo["auth_url"]}" target="_blank" rel="nofollow">'
            f'<img alt="G" src="https://www.gstatic.com/images/branding/product/1x/googleg_32dp.png"/> Iniciar sesión con Google</a>',
            unsafe_allow_html=True
        )
        if st.session_state.get("DEBUG"):
            st.caption(f"(DEBUG) redirect_uri: {oo.get('redirect_uri')}")
        st.markdown("</div>", unsafe_allow_html=True)

    return st.session_state.get("_google_identity")

# --- Pantalla de logout (igual que antes) ---
def _revoke_google_token(token: str | None) -> None:
    if not token:
        return
    try:
        requests.post(
            "https://oauth2.googleapis.com/revoke",
            params={"token": token},
            headers={"content-type": "application/x-www-form-urlencoded"},
            timeout=10,
        )
    except Exception:
        pass

def logout_screen(app_home: str = "?"):
    st.header("Cerrar sesión")
    ident = st.session_state.get("_google_identity") or {}
    current_email = ident.get("email") or "—"
    st.write(f"Usuario actual: **{current_email}**")

    revoke = st.checkbox("Revocar permisos de Google (Drive/Sheets y Search Console)", value=True)

    col1, col2 = st.columns(2)
    with col1:
        if st.button("🔒 Cerrar sesión y limpiar", type="primary"):
            if revoke:
                for key in ("creds_dest", "creds_src"):
                    data = st.session_state.get(key)
                    if isinstance(data, dict):
                        _revoke_google_token(data.get("token") or data.get("refresh_token"))
            try: st.cache_data.clear()
            except Exception: pass
            try: st.cache_resource.clear()
            except Exception: pass
            for k in [
                "_auth_bypass", "_google_identity",
                "oauth_oidc_web", "oauth_dest", "oauth_src",
                "creds_dest", "creds_src",
                "step1_done", "step2_done", "step3_done",
                "dest_folder_id", "src_account_label",
                "site_url_choice", "last_file_id", "last_file_kind",
                "DEBUG",
            ]:
                st.session_state.pop(k, None)
            token_store.clear("creds_dest"); token_store.clear("creds_src")
            st.success("Sesión cerrada y caché limpiada.")
            st.markdown(f"➡️ Volver a la app: [{app_home}]({app_home})")
            st.stop()
    with col2:
        if st.button("Cancelar"):
            st.stop()