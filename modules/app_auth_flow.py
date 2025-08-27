# modules/app_auth_flow.py
from __future__ import annotations

import requests
import streamlit as st
from urllib.parse import urlencode

from .auth import (
    build_flow_web,           # cliente OAuth Web
    SCOPES_OIDC,              # openid email profile
    fetch_userinfo,
    is_redirect_ready,        # verifica que [auth] tenga client_id/secret/redirect_uri
)
from .utils import token_store

# -----------------------------
# Helpers de query params
# -----------------------------
def _get_qp() -> dict:
    try:
        return dict(st.query_params)
    except Exception:
        return st.experimental_get_query_params()

def _clear_qp():
    try:
        st.query_params.clear()
    except Exception:
        st.experimental_set_query_params()

# -----------------------------
# Paso 0: Login con Google (Web OAuth, sin copy/paste)
# -----------------------------
def step0_google_identity():
    """
    Inicia sesión con Google usando el flujo WEB (redirect a tu app).
    - No requiere pegar ninguna URL.
    - Pide scopes: openid, email, profile (identidad).
    - Si el 'state' no coincide o se perdió la sesión, rehidrata el flujo automáticamente.
    Guarda en session_state['_google_identity'] => {name, email, picture}
    """
    st.subheader("0) Iniciar sesión con Google")

    # Validación mínima de secrets
    if not is_redirect_ready():
        st.error(
            "Falta configurar el redirect_uri o el cliente OAuth Web.\n\n"
            "En `[auth]` definí: client_id, client_secret y redirect_uri "
            "(por ejemplo: https://<tu-app>.streamlit.app)."
        )
        st.stop()

    # Construir el flujo una sola vez (primer render)
    if "oauth_oidc_web" not in st.session_state:
        flow = build_flow_web(SCOPES_OIDC)
        auth_url, state = flow.authorization_url(
            prompt="select_account",
            access_type="online",
            include_granted_scopes="true",  # como string
        )
        st.session_state["oauth_oidc_web"] = {
            "flow": flow,
            "auth_url": auth_url,
            "state": state,
            "redirect_uri": flow.redirect_uri,
        }

    oo = st.session_state["oauth_oidc_web"]
    qp = _get_qp()
    code = qp.get("code", [None])[0] if isinstance(qp.get("code"), list) else qp.get("code")
    state_in = qp.get("state", [None])[0] if isinstance(qp.get("state"), list) else qp.get("state")

    # 1) ¿Volvimos de Google con ?code&state?
    if code and state_in:
        # Reconstruir la URL exacta de redirección
        current_url = f"{oo['redirect_uri']}?{urlencode({k: (v[0] if isinstance(v, list) else v) for k, v in qp.items()}, doseq=True)}"

        # Preferir flujo existente; si falta o hay mismatch, rehidratar y forzar state
        flow = oo.get("flow")
        expected_state = oo.get("state")

        # Si no hay flow (sesión perdida) o el state no coincide -> rehidratar
        if (flow is None) or (expected_state != state_in):
            if st.session_state.get("DEBUG"):
                st.info("Aviso (DEBUG): state no coincide o sesión perdida. Rehidratando flujo con el state recibido…")
            # reconstruir flujo SIN generar un state nuevo (no llamamos authorization_url)
            flow = build_flow_web(SCOPES_OIDC)
            # forzar el state entrante en el objeto flow (propiedad privada/segura)
            try:
                setattr(flow, "state", state_in)
            except Exception:
                pass
            try:
                setattr(flow, "_state", state_in)
            except Exception:
                pass
            # guardarlo de nuevo por si lo necesitamos luego
            st.session_state["oauth_oidc_web"]["flow"] = flow

        # Intercambiar el code por tokens
        try:
            flow.fetch_token(authorization_response=current_url)
            creds = flow.credentials
            info = fetch_userinfo(creds)
            ident = {
                "name": info.get("name") or "Invitado",
                "email": info.get("email") or "—",
                "picture": info.get("picture"),
            }
            st.session_state["_google_identity"] = ident
            # Limpiar la query ?code=&state= para dejar la URL prolija
            _clear_qp()
            st.success(f"Sesión iniciada como {ident['email']}")
            return ident
        except Exception as e:
            # Mensaje corto para usuario; detalle técnico solo en DEBUG
            st.error("No se pudo completar el login. Volvé a intentarlo (un solo click).")
            if st.session_state.get("DEBUG"):
                st.caption(f"Detalle técnico: {e}")
            # botón para reiniciar flujo
            if st.button("Reiniciar Paso 0"):
                st.session_state.pop("oauth_oidc_web", None)
                st.session_state.pop("_google_identity", None)
                _clear_qp()
                st.rerun()
            st.stop()

    # 2) Primera carga (sin code/state): mostrar botón que abre Google en la MISMA pestaña
    col1, col2 = st.columns([1, 1])
    with col1:
        if st.button("Iniciar sesión con Google", type="primary", key="btn_oidc_launch"):
            st.session_state["_oidc_launch_url"] = oo["auth_url"]
            st.rerun()
    with col2:
        st.link_button("Abrir en pestaña nueva", oo["auth_url"])

    # Redirección en top window (evita bloqueo por iframe)
    if st.session_state.get("_oidc_launch_url") == oo["auth_url"]:
        st.markdown(f'<script>window.top.location.href="{oo["auth_url"]}";</script>', unsafe_allow_html=True)
        st.stop()

    # Mostrar identidad si ya existe en sesión (por ejemplo tras un refresh)
    return st.session_state.get("_google_identity")


# -----------------------------
# Pantalla de LOGOUT
# -----------------------------
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

def logout_screen(home_url: str = "?"):
    st.header("Cerrar sesión")
    ident = st.session_state.get("_google_identity") or {}
    st.write(f"Usuario actual: **{ident.get('email') or '—'}**")

    revoke = st.checkbox("Revocar permisos de Google (si corresponde)", value=True)

    c1, c2 = st.columns([1, 1])
    with c1:
        if st.button("🔒 Cerrar sesión y limpiar", type="primary"):
            # Revocar lo que tengamos guardado
            if revoke:
                for key in ("creds_dest", "creds_src"):
                    data = st.session_state.get(key)
                    if isinstance(data, dict):
                        _revoke_google_token(data.get("token") or data.get("refresh_token"))
                for key in ("creds_dest", "creds_src"):
                    data = token_store.load(key)
                    if isinstance(data, dict):
                        _revoke_google_token(data.get("token") or data.get("refresh_token"))

            # Limpiar caches
            try: st.cache_data.clear()
            except Exception: pass
            try: st.cache_resource.clear()
            except Exception: pass

            # Borrar session_state y token_store
            for k in [
                "_google_identity",
                "oauth_oidc_web", "_oidc_launch_url",
                "oauth_dest", "oauth_src",
                "creds_dest", "creds_src",
                "step1_done", "step2_done", "step3_done",
                "dest_folder_id", "src_account_label",
                "site_url_choice", "last_file_id", "last_file_kind",
                "_oauth_launch_url_dest", "_oauth_launch_url_src",
                "_auth_bypass",
            ]:
                st.session_state.pop(k, None)
            try:
                token_store.clear("creds_dest")
                token_store.clear("creds_src")
            except Exception:
                pass

            st.success("Sesión cerrada.")
            st.markdown(f"➡️ Volver a la app: [{home_url}]({home_url})")
            st.stop()

    with c2:
        if st.button("Cancelar"):
            st.rerun()