# modules/app_auth_flow.py
from __future__ import annotations

import os
os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

from typing import Optional, Dict
from urllib.parse import urlsplit, parse_qs, urlencode
import requests
import streamlit as st
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials

# Intentar importar el flujo "web" (redirect a la app). Si no existe, caemos al flujo "installed".
try:
    from .auth import build_flow_web as _build_flow_oidc, SCOPES_OIDC
    _HAS_BUILD_FLOW_WEB = True
except Exception:
    from .auth import build_flow as _build_flow_oidc  # type: ignore
    # SCOPES_OIDC podría no existir en auth antiguos: definimos aquí si hace falta
    try:
        from .auth import SCOPES_OIDC  # type: ignore
    except Exception:
        SCOPES_OIDC = ["openid", "email", "profile"]  # fallback
    _HAS_BUILD_FLOW_WEB = False

from .utils import token_store, debug_log

# -----------------------
# Helpers URL (locales)
# -----------------------
def _get_qp() -> Dict[str, str]:
    try:
        qp = dict(st.query_params)
    except Exception:
        qp = st.experimental_get_query_params()
    # normalizar para leer 1er valor
    return {k: (v[0] if isinstance(v, list) else v) for k, v in qp.items()}

def _clear_qp():
    try:
        st.query_params.clear()
    except Exception:
        st.experimental_set_query_params()

# -----------------------
# Paso 0: identidad OIDC
# -----------------------
def step0_google_identity() -> Optional[dict]:
    """
    Login de identidad con Google:
    - Si hay [auth].redirect_uri y existe build_flow_web => botón (sin pegar URL).
    - Sino => modo manual (pegar URL http://localhost/?code=&state=).
    El warning de 'state mismatch' solo aparece en modo DEBUG.
    """
    st.subheader("0) Iniciar sesión con Google (identidad)")

    acct_for_dest = st.secrets.get("oauth_app_key", "ACCESO")
    redirect_uri = (st.secrets.get("auth", {}) or {}).get("redirect_uri")
    use_redirect = bool(redirect_uri and _HAS_BUILD_FLOW_WEB)

    # (1) Preparar flow + auth_url una sola vez
    if "oauth_oidc" not in st.session_state:
        flow = _build_flow_oidc(acct_for_dest, SCOPES_OIDC)
        if use_redirect:
            # En build_flow_web el redirect ya viene seteado, pero por las dudas:
            try:
                flow.redirect_uri = redirect_uri  # type: ignore[attr-defined]
            except Exception:
                pass
        auth_url, state = flow.authorization_url(
            prompt="select_account",
            access_type="online",
            # no usar include_granted_scopes para evitar 400s por True/False capitalizado
        )
        st.session_state["oauth_oidc"] = {
            "flow": flow,
            "auth_url": auth_url,
            "state": state,
            "use_redirect": use_redirect,
            "redirect_uri": redirect_uri,
            "account_key": acct_for_dest,
        }

    oo = st.session_state["oauth_oidc"]

    # (2) ¿Venimos redirigidos desde Google?
    qp = _get_qp()
    code = qp.get("code")
    state_in = qp.get("state")

    if oo["use_redirect"] and code:
        expected_state = oo.get("state")
        state_ok = (state_in == expected_state)

        try:
            if state_ok:
                # Reconstruimos la URL exacta de redirección para fetch_token
                current_url = oo["redirect_uri"]
                if "?" in current_url:
                    current_url = current_url.split("?", 1)[0]
                qs = {k: v for k, v in _get_qp().items()}
                auth_response = f"{current_url}?{urlencode(qs)}"
                flow: Flow = oo["flow"]
                flow.fetch_token(authorization_response=auth_response)
                creds = flow.credentials
            else:
                # State mismatch (nueva pestaña o refresh). En DEBUG avisamos; fuera de DEBUG, silencio.
                if st.session_state.get("DEBUG"):
                    st.info("Aviso: el 'state' no coincide (posible nueva pestaña). Usando flujo rehidratado.")
                # Rehidratar: crear flow y usar el CODE directamente (sin validar state)
                flow2 = _build_flow_oidc(acct_for_dest, SCOPES_OIDC)
                try:
                    flow2.redirect_uri = oo.get("redirect_uri") or redirect_uri  # type: ignore[attr-defined]
                except Exception:
                    pass
                flow2.fetch_token(code=code)
                creds = flow2.credentials

            # Obtener userinfo
            resp = requests.get(
                "https://openidconnect.googleapis.com/v1/userinfo",
                headers={"Authorization": f"Bearer {creds.token}"},
                timeout=10,
            )
            info = resp.json() if resp.status_code == 200 else {}
            ident = {
                "name": info.get("name") or info.get("email") or "Invitado",
                "email": info.get("email") or "—",
                "picture": info.get("picture"),
            }
            st.session_state["_google_identity"] = ident
            _clear_qp()
            st.success(f"Identidad verificada: {ident['email']}")
            return ident
        except Exception as e:
            st.error(f"No se pudo verificar identidad: {e}")
            st.stop()

    # (3) UI cuando aún no se autorizó
    if oo["use_redirect"]:
        st.markdown(
            f'<a href="{oo["auth_url"]}" target="_self"><button type="button">Continuar con Google</button></a>',
            unsafe_allow_html=True
        )
        st.caption("Serás redirigido automáticamente de vuelta a esta app.")
    else:
        # Modo manual (installed / http://localhost)
        st.markdown(f"🔗 **Paso A (identidad):** [Iniciar sesión con Google]({oo['auth_url']})")
        with st.expander("Ver/copiar URL de autorización (identidad)"):
            st.code(oo["auth_url"])

        url = st.text_input(
            "🔑 Paso B (identidad): pegá la URL completa (http://localhost/?code=...&state=...)",
            key="auth_response_url_oidc",
            placeholder="http://localhost/?code=...&state=...",
        )

        c1, c2 = st.columns(2)
        with c1:
            if st.button("Verificar identidad", type="primary", key="btn_oidc_connect"):
                if not url.strip():
                    st.error("Pegá la URL completa de redirección (incluye code y state).")
                    st.stop()
                # Validar state (estricto en modo manual)
                try:
                    qs = parse_qs(urlsplit(url.strip()).query)
                    returned_state = (qs.get("state") or [""])[0]
                except Exception:
                    returned_state = ""
                expected_state = oo.get("state")
                if returned_state != expected_state:
                    st.error("CSRF Warning: el 'state' devuelto no coincide con el generado.")
                    st.stop()

                try:
                    flow: Flow = oo["flow"]
                    flow.fetch_token(authorization_response=url.strip())
                    creds = flow.credentials
                    resp = requests.get(
                        "https://openidconnect.googleapis.com/v1/userinfo",
                        headers={"Authorization": f"Bearer {creds.token}"},
                        timeout=10,
                    )
                    info = resp.json() if resp.status_code == 200 else {}
                    ident = {
                        "name": info.get("name") or info.get("email") or "Invitado",
                        "email": info.get("email") or "—",
                        "picture": info.get("picture"),
                    }
                    st.session_state["_google_identity"] = ident
                    st.success(f"Identidad verificada: {ident['email']}")
                    return ident
                except Exception as e:
                    st.error(f"No se pudo verificar identidad: {e}")
                    st.stop()

        with c2:
            if st.button("Reiniciar Paso 0", key="btn_reset_oidc"):
                st.session_state.pop("oauth_oidc", None)
                st.session_state.pop("_google_identity", None)
                st.rerun()

    return st.session_state.get("_google_identity")


# -----------------------
# Pantalla de LOGOUT
# -----------------------
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

def logout_screen(home_url: str | None = None):
    st.header("Cerrar sesión")
    ident = st.session_state.get("_google_identity") or {}
    current_email = ident.get("email") or "—"
    st.write(f"Usuario actual: **{current_email}**")

    revoke = st.checkbox("Revocar permisos de Google (Drive/Sheets y Search Console)", value=True)
    wipe_pkg = st.checkbox("Borrar caché del paquete externo (.ext_pkgs/)", value=False)

    c1, c2 = st.columns([1, 1])
    with c1:
        if st.button("🔒 Cerrar sesión y limpiar", type="primary"):
            # Revocación opcional
            if revoke:
                for key in ("creds_dest", "creds_src"):
                    data = st.session_state.get(key)
                    if isinstance(data, dict):
                        _revoke_google_token(data.get("token") or data.get("refresh_token"))

            # Limpiar caches
            try: st.cache_data.clear()
            except Exception: pass
            try: st.cache_resource.clear()
            except Exception: pass

            # Limpiar session_state principal
            for k in [
                "_auth_bypass", "_google_identity",
                "oauth_oidc", "oauth_dest", "oauth_src",
                "creds_dest", "creds_src",
                "step1_done", "step2_done", "step3_done",
                "dest_folder_id", "src_account_label",
                "site_url_choice", "last_file_id", "last_file_kind",
                "DEBUG",
            ]:
                st.session_state.pop(k, None)

            # Limpiar token_store
            try:
                token_store.clear("creds_dest")
                token_store.clear("creds_src")
            except Exception:
                pass

            # Cerrar sesión de Streamlit si existe
            try:
                if hasattr(st, "logout"):
                    st.logout()
            except Exception:
                pass

            st.success("Sesión cerrada y caché limpiada.")
            home = home_url or "?"
            st.markdown(f"➡️ Volver a la app: [Inicio]({home})")
            st.stop()

    with c2:
        if st.button("Cancelar"):
            _clear_qp()
            st.rerun()