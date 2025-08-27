# modules/app_auth_flow.py
from __future__ import annotations

import shutil
from urllib.parse import urlencode, urlsplit, parse_qs

import requests
import streamlit as st

from modules.app_utils import get_qp, clear_qp
from modules.auth import build_flow

# Scopes de identidad (solo para el Paso 0)
OIDC_SCOPES = ["openid", "email", "profile"]


def step0_google_identity():
    """
    Paso 0: Login con Google (OIDC) para obtener identidad (nombre, email, foto).
    - Si hay `auth.redirect_uri` en secrets, usa redirección automática (sin copy/paste).
    - Si no hay redirect_uri, muestra el flujo manual (pegar URL http://localhost/?code=...).
    - El aviso de 'state' no coincide solo se muestra en modo DEBUG.
    """
    st.subheader("0) Iniciar sesión con Google (identidad)")

    acct_key = st.secrets.get("oauth_app_key", "ACCESO")
    redirect_uri = (st.secrets.get("auth", {}) or {}).get("redirect_uri")
    use_redirect = bool(redirect_uri)

    # Inicializar flow y auth_url
    if "oauth_oidc" not in st.session_state:
        flow = build_flow(acct_key, OIDC_SCOPES)
        if use_redirect:
            flow.redirect_uri = redirect_uri
        auth_url, state = flow.authorization_url(
            prompt="select_account",
            access_type="online",
            include_granted_scopes="true",
        )
        st.session_state["oauth_oidc"] = {
            "flow": flow,
            "auth_url": auth_url,
            "state": state,
            "use_redirect": use_redirect,
            "redirect_uri": redirect_uri,
        }

    oo = st.session_state["oauth_oidc"]

    # ¿Volvimos de Google con code/state en la URL?
    qp = get_qp()
    code = qp.get("code", [None])[0] if isinstance(qp.get("code"), list) else qp.get("code")
    state_in = qp.get("state", [None])[0] if isinstance(qp.get("state"), list) else qp.get("state")

    if oo["use_redirect"] and code and state_in:
        expected_state = oo.get("state")
        if state_in != expected_state and st.session_state.get("DEBUG"):
            st.info("Aviso: el 'state' no coincide (posible nueva pestaña). Usando el state recibido…")

        # Reconstruir la URL exacta de redirección (redirect_uri + query actual)
        current_url = f"{oo['redirect_uri']}?" + urlencode(
            {k: v[0] if isinstance(v, list) else v for k, v in qp.items()},
            doseq=True,
        )

        try:
            flow = oo["flow"]
            flow.fetch_token(authorization_response=current_url)
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

            clear_qp()  # limpiar code/state de la URL
            st.success(f"Identidad verificada: {ident['email']}")
            return ident
        except Exception as e:
            st.error(f"No se pudo verificar identidad: {e}")
            st.stop()

    # UI de inicio + fallback manual
    st.markdown(f"🔗 **Paso A (identidad):** [Iniciar sesión con Google]({oo['auth_url']})")
    with st.expander("Ver/copiar URL de autorización (identidad)"):
        st.code(oo["auth_url"])

    if not oo["use_redirect"]:
        url = st.text_input(
            "🔑 Paso B (identidad): pegá la URL completa (http://localhost/?code=...&state=...)",
            key="auth_response_url_oidc",
            placeholder="http://localhost/?code=...&state=...",
        )

        c1, c2 = st.columns(2)
        with c1:
            if st.button("Verificar identidad", type="primary", key="btn_oidc_connect"):
                if not (url or "").strip():
                    st.error("Pegá la URL completa de redirección (incluye code y state).")
                    st.stop()

                # Validar state (aviso solo en DEBUG)
                try:
                    qs = parse_qs(urlsplit(url.strip()).query)
                    returned_state = (qs.get("state") or [""])[0]
                except Exception:
                    returned_state = ""
                expected_state = oo.get("state")
                if returned_state != expected_state and st.session_state.get("DEBUG"):
                    st.info("Aviso: el 'state' no coincide (posible nueva pestaña). Continuando…")

                try:
                    flow = oo["flow"]
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
                clear_qp()
                st.rerun()
    else:
        # Modo redirect: abrir en la MISMA pestaña
        st.markdown(
            f'<a href="{oo["auth_url"]}" target="_self"><button type="button">Continuar con Google</button></a>',
            unsafe_allow_html=True,
        )
        st.caption("Serás redirigido a esta app automáticamente después de otorgar permisos.")

    return st.session_state.get("_google_identity")


# -------- Pantalla de logout (reutilizable desde app.py) --------

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


def logout_screen(app_home: str = "/"):
    st.header("Cerrar sesión")
    ident = st.session_state.get("_google_identity") or {}
    current_email = ident.get("email") or "—"
    st.write(f"Usuario actual: **{current_email}**")

    revoke = st.checkbox("Revocar permisos de Google (Drive/Sheets y Search Console)", value=True)
    wipe_pkg = st.checkbox("Borrar caché del paquete externo (.ext_pkgs/)", value=False)

    c1, c2 = st.columns(2)
    with c1:
        if st.button("🔒 Cerrar sesión y limpiar", type="primary"):
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

            if wipe_pkg:
                shutil.rmtree(".ext_pkgs", ignore_errors=True)

            # Limpiar session_state
            for k in [
                "_auth_bypass", "_google_identity",
                "oauth_oidc", "oauth_dest", "oauth_src",
                "creds_dest", "creds_src",
                "step1_done", "step2_done", "step3_done",
                "dest_folder_id", "src_account_label",
                "site_url_choice", "last_file_id", "last_file_kind",
                "DEBUG", "sc_account_choice", "_src_oauth_in_progress", "_src_target_label",
            ]:
                st.session_state.pop(k, None)

            # Cerrar sesión de Streamlit si existe
            try:
                if hasattr(st, "logout"):
                    st.logout()
            except Exception:
                pass

            st.success("Sesión cerrada y caché limpiada.")
            st.markdown(f"➡️ Volver a la app: [Inicio]({app_home})")
            st.stop()

    with c2:
        if st.button("Cancelar"):
            clear_qp()
            st.rerun()