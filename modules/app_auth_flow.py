# modules/app_auth_flow.py
from __future__ import annotations

import os
from urllib.parse import urlencode
import streamlit as st

from .auth import (
    build_flow_web,
    fetch_userinfo,
    SCOPES_PERSONAL_FULL,   # OIDC + Drive/Sheets + GSC
    is_redirect_ready,      # verifica que [auth] tenga client_id/secret/redirect_uri
)
from .utils import token_store

# ---------------------------
# Helpers: query params
# ---------------------------
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

# ---------------------------
# Paso 0: Login Web con botón Google
# ---------------------------
def step0_google_identity():
    """
    Pide login con Google usando el cliente Web de [auth] y solicita:
    - OIDC (openid, email, profile)
    - Drive/Sheets
    - Search Console (readonly)
    Guardamos:
      - st.session_state['creds_dest'] (dict)
      - st.session_state['_google_identity'] ({name,email,picture})
    UI mínima: solo un botón.
    """

    # Preflight: ¿está listo el cliente Web?
    if not is_redirect_ready():
        st.error(
            "Falta configurar el cliente OAuth Web o el redirect_uri.\n\n"
            "En `secrets.toml` asegurate de tener en [auth]:\n"
            "- client_id\n- client_secret\n- redirect_uri (exacto, igual al autorizado en Google Cloud)"
        )
        return None

    st.subheader("Inicia sesión con tu cuenta personal de Nomadic")

    # Crear flujo + URL de autorización (una sola vez)
    if "oauth_oidc" not in st.session_state:
        flow = build_flow_web(SCOPES_PERSONAL_FULL)
        # Nota: include_granted_scopes DEBE ser string "true"
        auth_url, state = flow.authorization_url(
            prompt="consent select_account",
            access_type="offline",
            include_granted_scopes="true",
        )
        st.session_state["oauth_oidc"] = {
            "flow": flow,
            "auth_url": auth_url,
            "state": state,
        }

    oo = st.session_state["oauth_oidc"]

    # Si venimos redirigidos desde Google (code + state ya en la URL)
    qp = _get_qp()
    code = qp.get("code", [None])[0] if isinstance(qp.get("code"), list) else qp.get("code")
    returned_state = qp.get("state", [None])[0] if isinstance(qp.get("state"), list) else qp.get("state")

    # Mostrar SOLO el botón (abre en la misma pestaña)
    st.markdown(
        f'<a href="{oo["auth_url"]}" target="_self">'
        f'<button type="button" class="st-btn st-btn-primary">Iniciar sesión con Google</button>'
        f'</a>',
        unsafe_allow_html=True
    )

    # Si todavía no volvimos de Google, no hay nada más que hacer
    if not (code and returned_state):
        return st.session_state.get("_google_identity")

    # Validar state (solo advertir en DEBUG, no bloquear)
    expected_state = oo.get("state")
    if returned_state != expected_state and st.session_state.get("DEBUG"):
        st.info("Aviso (DEBUG): el 'state' recibido no coincide; rehidratando flujo con el estado actual…")

    # Reconstruir la URL EXACTA a la que Google redirigió (redirect_uri + query actual)
    qp_flat = {k: (v[0] if isinstance(v, list) else v) for k, v in qp.items()}
    redirect_uri = (oo["flow"].redirect_uri or "")
    current_url = f"{redirect_uri}?{urlencode(qp_flat, doseq=True)}"

    # Intercambiar code→tokens
    try:
        flow = oo["flow"]
        flow.fetch_token(authorization_response=current_url)
        creds = flow.credentials

        # Guardar credenciales personales (creds_dest)
        data = {
            "token": creds.token,
            "refresh_token": getattr(creds, "refresh_token", None),
            "token_uri": creds.token_uri,
            "client_id": creds.client_id,
            "client_secret": creds.client_secret,
            "scopes": list(creds.scopes) if getattr(creds, "scopes", None) else None,
        }
        st.session_state["creds_dest"] = data
        token_store.save("creds_dest", data)

        # Obtener identidad (OIDC /userinfo)
        ident = fetch_userinfo(creds)
        st.session_state["_google_identity"] = ident

        # Limpiar code/state de la URL
        _clear_qp()

        # Feedback suave
        st.success(f"Sesión iniciada: {ident.get('email','')}")

        return ident

    except Exception as e:
        # Error típico: redirect_uri_mismatch por configuración en Google Cloud
        st.error("No se pudo completar el login. Volvé a intentarlo (un solo click).")
        if st.session_state.get("DEBUG"):
            st.caption(f"(DEBUG) Detalle técnico: {e}")
        return None


# ---------------------------
# Pantalla de LOGOUT
# ---------------------------
def _revoke_google_token(token: str | None) -> None:
    if not token:
        return
    import requests
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

    revoke = st.checkbox("Revocar permisos de Google", value=True)

    c1, c2 = st.columns(2)
    with c1:
        if st.button("🔒 Cerrar sesión y limpiar", type="primary"):
            if revoke:
                for key in ("creds_dest", "creds_src"):
                    data = st.session_state.get(key)
                    if isinstance(data, dict):
                        _revoke_google_token(data.get("token") or data.get("refresh_token"))

            # Limpiar caches y sesión
            try: st.cache_data.clear()
            except Exception: pass
            try: st.cache_resource.clear()
            except Exception: pass

            for k in [
                "oauth_oidc","_google_identity",
                "creds_dest","creds_src",
                "step1_done","step2_done","step3_done",
                "dest_folder_id","src_account_label",
                "site_url_choice","last_file_id","last_file_kind",
                "DEBUG",
            ]:
                st.session_state.pop(k, None)

            # Limpiar token_store
            try:
                token_store.clear("creds_dest")
                token_store.clear("creds_src")
            except Exception:
                pass

            st.success("Sesión cerrada.")
            st.markdown(f"➡️ Volver al inicio: [{app_home}]({app_home})")
            st.stop()

    with c2:
        if st.button("Cancelar"):
            _clear_qp()
            st.rerun()