# modules/app_auth_flow.py
from __future__ import annotations

import requests
import streamlit as st
from urllib.parse import urlencode

from .auth import (
    build_flow_web,
    SCOPES_OIDC,
    SCOPES_PERSONAL_FULL,   # ← para pedir Drive + GSC también en Paso 0 si se desea
    fetch_userinfo,
    is_redirect_ready,
)
from .utils import token_store, debug_log


# -------------------------------
# Helpers de query params (compat)
# -------------------------------
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


# ============================
# PASO 0: Login con Google
# ============================
def step0_google_identity():
    """
    Paso 0: Iniciar sesión con Google para obtener identidad (name/email/picture).

    - Usa cliente OAuth Web de [auth] en secrets.
    - Evita el problema de iframes con dos opciones:
        a) botón que abre en pestaña nueva (target=_blank)
        b) forzar navegación del top-level window (misma pestaña)
    - Canjea el token con `code=...` (no con la URL entera), para ser tolerante
      a pequeñas variaciones del redirect.

    Scopes:
      * Por defecto solo OIDC (identidad).
      * Si pones `auth.step0_full = true` en secrets pedirá también Drive/Sheets y GSC.
    """
    st.subheader("0) Iniciar sesión con Google")

    if not is_redirect_ready():
        st.error(
            "Falta configurar el redirect_uri o el cliente OAuth Web.\n\n"
            "Definí en secrets [auth] `client_id`, `client_secret` y `redirect_uri`."
        )
        return None

    # ¿Querés también Drive + GSC en el Paso 0?
    use_full_scopes = bool((st.secrets.get("auth") or {}).get("step0_full", False))
    scopes = SCOPES_PERSONAL_FULL if use_full_scopes else SCOPES_OIDC

    # Creamos el flow y auth_url una sola vez
    if "oauth_oidc" not in st.session_state:
        flow = build_flow_web(scopes)
        auth_url, state = flow.authorization_url(
            prompt="select_account",
            access_type="offline" if use_full_scopes else "online",
        )
        st.session_state["oauth_oidc"] = {
            "flow": flow,
            "auth_url": auth_url,
            "state": state,
            "use_full_scopes": use_full_scopes,
        }

    oo = st.session_state["oauth_oidc"]

    # Si volvemos de Google con ?code=...&state=...
    qp = _get_qp()
    code = qp.get("code", [None])[0] if isinstance(qp.get("code"), list) else qp.get("code")
    state_in = qp.get("state", [None])[0] if isinstance(qp.get("state"), list) else qp.get("state")

    if code:
        # Nota: sólo mostramos el aviso de state mismatch en modo DEBUG
        expected_state = oo.get("state")
        if state_in and state_in != expected_state and st.session_state.get("DEBUG"):
            st.warning("Aviso: el 'state' no coincide (posible nueva pestaña). "
                       "Se continuará con el código recibido…")

        try:
            flow = oo["flow"]
            # 👉 canje robusto con code (sin depender de la URL exacta)
            flow.fetch_token(code=code)
            creds = flow.credentials

            # Userinfo (OIDC)
            ident = fetch_userinfo(creds) or {"name": "Invitado", "email": "—", "picture": None}
            st.session_state["_google_identity"] = ident

            # Limpiamos code/state para dejar la URL prolija
            _clear_qp()

            st.success(f"Identidad verificada: {ident.get('email', '—')}")
            return ident
        except Exception as e:
            debug_log("[step0_google_identity] fetch_token fallo", str(e))
            st.error("No se pudo completar el login. Volvé a intentarlo (un solo click).")
            if st.button("Reiniciar Paso 0", key="btn_reset_oidc_after_error"):
                st.session_state.pop("oauth_oidc", None)
                st.session_state.pop("_google_identity", None)
                _clear_qp()
                st.rerun()
            return None

    # --- UI inicial (evita iframes) ---
    c1, c2 = st.columns([1, 1])

    with c1:
        # Opción preferida: abrir en pestaña nueva (evita sandbox)
        if hasattr(st, "link_button"):
            st.link_button("Continuar con Google (pestaña nueva)", oo["auth_url"])
        else:
            st.markdown(
                f'<a href="{oo["auth_url"]}" target="_blank" rel="noopener">'
                f'<button type="button">Continuar con Google (pestaña nueva)</button></a>',
                unsafe_allow_html=True
            )

    with c2:
        # Alternativa: forzar navegación del top-level window
        if st.button("Continuar aquí (si la otra falla)"):
            st.session_state["_do_oidc_redirect"] = True

    if st.session_state.get("_do_oidc_redirect"):
        st.session_state.pop("_do_oidc_redirect", None)
        st.markdown(
            f"""
            <script>
            window.top.location.assign("{oo['auth_url']}");
            </script>
            """,
            unsafe_allow_html=True,
        )

    # Botón para reiniciar manualmente
    if st.button("Reiniciar Paso 0", key="btn_reset_oidc_manual"):
        st.session_state.pop("oauth_oidc", None)
        st.session_state.pop("_google_identity", None)
        _clear_qp()
        st.rerun()

    return st.session_state.get("_google_identity")


# ============================
# Pantalla de LOGOUT
# ============================
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
        pass  # sin ruido si falla

def logout_screen(home_url: str = "?"):
    st.header("Cerrar sesión")
    ident = st.session_state.get("_google_identity") or {}
    current_email = ident.get("email") or "—"
    st.write(f"Usuario actual: **{current_email}**")

    revoke = st.checkbox("Revocar permisos de Google (Drive/Sheets y Search Console)", value=True)
    wipe_pkg = st.checkbox("Borrar caché del paquete externo (.ext_pkgs/)", value=False)

    c1, c2 = st.columns([1, 1])
    with c1:
        if st.button("🔒 Cerrar sesión y limpiar", type="primary"):
            if revoke:
                for key in ("creds_dest", "creds_src"):
                    data = st.session_state.get(key) or token_store.load(key)
                    if isinstance(data, dict):
                        _revoke_google_token(data.get("token") or data.get("refresh_token"))

            try: st.cache_data.clear()
            except Exception: pass
            try: st.cache_resource.clear()
            except Exception: pass

            if wipe_pkg:
                import shutil
                shutil.rmtree(".ext_pkgs", ignore_errors=True)

            for k in [
                "_auth_bypass", "_google_identity",
                "oauth_oidc", "oauth_dest", "oauth_src",
                "creds_dest", "creds_src",
                "step1_done", "step2_done", "step3_done",
                "dest_folder_id", "src_account_label",
                "site_url_choice", "last_file_id", "last_file_kind",
                "DEBUG", "_do_oidc_redirect",
            ]:
                st.session_state.pop(k, None)

            try:
                token_store.clear("creds_dest")
                token_store.clear("creds_src")
            except Exception:
                pass

            st.success("Sesión cerrada y caché limpiada.")
            st.markdown(f"➡️ Volver a la app: [Inicio]({home_url})")
            st.stop()

    with c2:
        if st.button("Cancelar"):
            _clear_qp()
            st.rerun()