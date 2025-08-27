# modules/app_auth_flow.py
from __future__ import annotations
from urllib.parse import urlencode, urlsplit, parse_qs
import streamlit as st
from .auth import (
    SCOPES_PERSONAL_FULL,
    build_flow_web,
    fetch_userinfo,
    is_redirect_ready,
)

def step0_google_identity():
    """
    Paso 0: Login con Google (OIDC + Drive + GSC) SIN copy/paste.
    - Usa cliente OAuth Web (redirect_uri a la propia app).
    - Redirige en la MISMA pestaña (top window). Fallback: abrir en pestaña nueva.
    """
    st.subheader("0) Iniciar sesión con Google (identidad)")
    acct_for_dest = st.secrets.get("oauth_app_key", "ACCESO")

    # Verificar que haya client_id/secret/redirect_uri web configurados
    if not is_redirect_ready(acct_for_dest):
        st.error(
            "Falta configurar el redirect_uri o el cliente OAuth Web.\n"
            "Revisá secrets [auth] o [accounts].<key> (web_client_id/web_client_secret/web_redirect_uri)."
        )
        st.stop()

    # Crear flow y auth_url (una vez)
    if "oauth_oidc" not in st.session_state:
        flow = build_flow_web(SCOPES_PERSONAL_FULL)
        auth_url, state = flow.authorization_url(
            prompt="select_account",
            access_type="online",
            include_granted_scopes="true",
        )
        st.session_state["oauth_oidc"] = {
            "flow": flow,
            "auth_url": auth_url,
            "state": state,
            "redirect_uri": flow.redirect_uri,
        }

    oo = st.session_state["oauth_oidc"]

    # ¿Volvimos de Google con ?code&state en la URL?
    qp = dict(st.query_params) if hasattr(st, "query_params") else st.experimental_get_query_params()
    code = qp.get("code", [None])[0] if isinstance(qp.get("code"), list) else qp.get("code")
    state_in = qp.get("state", [None])[0] if isinstance(qp.get("state"), list) else qp.get("state")

    if code and state_in:
        if state_in != oo["state"]:
            # Solo mostrar el aviso en DEBUG
            if st.session_state.get("DEBUG"):
                st.warning("Aviso: el 'state' no coincide (posible nueva pestaña). Reintentá el login.")
            # Forzar reinicio del paso 0
            st.session_state.pop("oauth_oidc", None)
            st.stop()

        # Reconstruir la URL de retorno exacta y canjear el code
        from urllib.parse import urlencode
        auth_response = f"{oo['redirect_uri']}?{urlencode({k: v[0] if isinstance(v, list) else v for k, v in qp.items()}, doseq=True)}"
        try:
            flow = oo["flow"]
            flow.fetch_token(authorization_response=auth_response)
            creds = flow.credentials
            info = fetch_userinfo(creds)
            st.session_state["_google_identity"] = info
            # Limpiar query params (sacá code/state de la barra)
            if hasattr(st, "query_params"):
                st.query_params.clear()
            else:
                st.experimental_set_query_params()
            st.success(f"Identidad verificada: {info.get('email')}")
            return info
        except Exception as e:
            st.error(f"No se pudo verificar identidad: {e}")
            st.stop()

    # Lanzadores: MISMA pestaña (JS) + pestaña nueva
    c1, c2 = st.columns([1, 1])
    with c1:
        if st.button("Iniciar sesión con Google", type="primary", key="btn_oidc_launch"):
            st.session_state["_oidc_launch_url"] = oo["auth_url"]
            st.rerun()
    with c2:
        st.link_button("Abrir en pestaña nueva", oo["auth_url"])

    # Si se pulsó el botón, redirigimos el top window (evita iframes)
    if st.session_state.get("_oidc_launch_url") == oo["auth_url"]:
        st.markdown(
            f'<script>window.top.location.href="{oo["auth_url"]}";</script>',
            unsafe_allow_html=True,
        )
        st.stop()

    return st.session_state.get("_google_identity")