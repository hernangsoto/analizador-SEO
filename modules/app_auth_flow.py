# modules/app_auth_flow.py
from __future__ import annotations
import time
import requests
import streamlit as st
from google.oauth2.credentials import Credentials

from modules.app_utils import oauth_flow_store, get_qp, clear_qp
from modules.app_constants import SCOPES_GSC  # sólo para tenerlo a mano si hiciera falta
from modules.auth import build_flow, SCOPES_DRIVE  # reuso de helper y scopes

# -----------------------------
# Helpers
# -----------------------------
def _append_hd(auth_url: str, domain: str = "nomadic.agency") -> str:
    sep = "&" if "?" in auth_url else "?"
    return f"{auth_url}{sep}hd={domain}"

def _email_is_nomadic(email: str | None) -> bool:
    return bool(email and email.lower().endswith("@nomadic.agency"))

def _fetch_userinfo_json_with_retry(access_token: str) -> dict:
    url = "https://openidconnect.googleapis.com/v1/userinfo"
    headers = {"Authorization": f"Bearer {access_token}"}
    for attempt in range(4):
        try:
            r = requests.get(url, headers=headers, timeout=10)
            if r.status_code in (500, 502, 503, 504):
                time.sleep(1.2 * (attempt + 1))
                continue
            return r.json() if r.status_code == 200 else {}
        except requests.RequestException:
            time.sleep(1.2 * (attempt + 1))
    return {}

def _force_flow_state(flow, incoming_state: str | None):
    """Fuerza el state en el OAuth2Session y su client interno, antes de fetch_token."""
    if not incoming_state or not flow:
        return
    try:
        sess = getattr(flow, "oauth2session", None)
        if sess is not None:
            # requests-oauthlib guarda el estado en _state y también en el client
            try: setattr(sess, "_state", incoming_state)
            except Exception: pass
            try:
                # algunos builds exponen .client, otros ._client
                if hasattr(sess, "client") and getattr(sess, "client") is not None:
                    setattr(sess.client, "state", incoming_state)
            except Exception: pass
            try:
                if hasattr(sess, "_client") and getattr(sess, "_client") is not None:
                    setattr(sess._client, "state", incoming_state)
            except Exception: pass
    except Exception:
        pass

def _csrf_mismatch_hint(step_label: str = "Paso 0"):
    st.error("CSRF Warning: el 'state' devuelto no coincide con el generado.")
    st.info(f"Hacé clic en **Reiniciar {step_label}** y repetí la autorización (un solo click).")
    if st.button(f"Reiniciar {step_label}", key=f"btn_restart_{step_label.replace(' ', '_').lower()}"):
        for k in ("oauth_oidc", "_google_identity", "creds_dest", "step1_done"):
            st.session_state.pop(k, None)
        clear_qp()
        st.rerun()

# -----------------------------
# Paso 0: Identidad Google (OIDC + Drive + GSC scopes base)
# -----------------------------
def step0_google_identity():
    st.subheader("¡Bienvenido! Para comenzar, inicia sesión con tu mail personal de Nomadic")
    auth_sec = st.secrets.get("auth", {}) or {}
    has_web = bool(auth_sec.get("client_id") and auth_sec.get("client_secret") and auth_sec.get("redirect_uri"))
    redirect_uri = auth_sec.get("redirect_uri")
    store = oauth_flow_store()
    scopes_step0 = ["openid", "email", "profile"] + SCOPES_DRIVE + SCOPES_GSC  # incluye GSC de una vez

    # 1) Preparar flujo inicial / reusar
    if "oauth_oidc" not in st.session_state:
        if has_web:
            client_secrets = {"web": {
                "client_id": auth_sec["client_id"],
                "client_secret": auth_sec["client_secret"],
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
                "redirect_uris": [redirect_uri],
            }}
            from google_auth_oauthlib.flow import Flow
            flow = Flow.from_client_config(client_secrets, scopes=scopes_step0)
            flow.redirect_uri = redirect_uri
            auth_url, state = flow.authorization_url(
                prompt="consent select_account", access_type="offline", include_granted_scopes="true",
            )
            auth_url = _append_hd(auth_url)
            st.session_state["oauth_oidc"] = {
                "flow_state": state, "use_redirect": True, "redirect_uri": redirect_uri, "mode": "web", "auth_url": auth_url,
            }
            store[state] = {"flow": flow, "created": time.time(), "mode": "web"}
        else:
            acct_for_dest = st.secrets.get("oauth_app_key", "ACCESO")
            flow = build_flow(acct_for_dest, scopes_step0)
            auth_url, state = flow.authorization_url(
                prompt="consent select_account", access_type="offline", include_granted_scopes="true",
            )
            auth_url = _append_hd(auth_url)
            st.session_state["oauth_oidc"] = {
                "flow_state": state, "use_redirect": False, "redirect_uri": "http://localhost", "mode": "installed", "auth_url": auth_url,
            }
            store[state] = {"flow": flow, "created": time.time(), "mode": "installed"}
    else:
        oo = st.session_state["oauth_oidc"]
        if has_web and oo.get("mode") != "web":
            st.session_state.pop("oauth_oidc", None); return step0_google_identity()
        if (not has_web) and oo.get("mode") != "installed":
            st.session_state.pop("oauth_oidc", None); return step0_google_identity()

    oo = st.session_state["oauth_oidc"]
    auth_url = oo["auth_url"]
    qp = get_qp()
    code = qp.get("code", [None])[0] if isinstance(qp.get("code"), list) else qp.get("code")
    state_in = qp.get("state", [None])[0] if isinstance(qp.get("state"), list) else qp.get("state")

    # -----------------------------
    # Helper interno: finalizar identidad OK
    # -----------------------------
    def _finalize_identity(creds, info):
        ident = {"name": info.get("name") or info.get("email") or "Invitado", "email": info.get("email") or "—", "picture": info.get("picture")}
        hd_ok = (info.get("hd") == "nomadic.agency") if info.get("hd") else False
        if not (_email_is_nomadic(ident["email"]) or hd_ok):
            st.error("Debes iniciar sesión con un correo **@nomadic.agency**.")
            st.session_state.pop("_google_identity", None)
            st.session_state.pop("creds_dest", None)
            st.session_state.pop("step1_done", None)
            clear_qp()
            st.stop()

        st.session_state["_google_identity"] = ident
        st.session_state["creds_dest"] = {
            "token": creds.token,
            "refresh_token": getattr(creds, "refresh_token", None),
            "token_uri": creds.token_uri,
            "client_id": creds.client_id,
            "client_secret": creds.client_secret,
            "scopes": creds.scopes,
        }
        st.session_state["step1_done"] = True  # omite Paso 1
        clear_qp()
        st.success(f"Identidad verificada y permisos listos: {ident['email']}")
        return ident

    # -----------------------------
    # Rehidratación y canje (modo redirect)
    # -----------------------------
    if oo.get("use_redirect") and code:
        expected_state = oo.get("flow_state")
        flow = None

        # 1) Intentar recuperar el Flow usando el state recibido
        if state_in and state_in in store:
            flow = store.pop(state_in)["flow"]

        # 2) Si no está en memoria, recrear y forzar estado entrante
        if not flow:
            if st.secrets.get("auth", {}).get("client_id"):
                from google_auth_oauthlib.flow import Flow
                client_secrets = {"web": {
                    "client_id": st.secrets["auth"]["client_id"],
                    "client_secret": st.secrets["auth"]["client_secret"],
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
                    "redirect_uris": [st.secrets["auth"]["redirect_uri"]],
                }}
                flow = Flow.from_client_config(client_secrets, scopes=["openid","email","profile"] + SCOPES_DRIVE + SCOPES_GSC, state=state_in)
                flow.redirect_uri = st.secrets["auth"]["redirect_uri"]
            else:
                acct_for_dest = st.secrets.get("oauth_app_key", "ACCESO")
                flow = build_flow(acct_for_dest, ["openid","email","profile"] + SCOPES_DRIVE + SCOPES_GSC)
                try:
                    setattr(flow, "_state", state_in)
                except Exception:
                    pass

        # 3) Forzar state entrante en el session/client del flow
        _force_flow_state(flow, state_in)

        # 4) Aviso si el state no coincide, pero continuar
        if expected_state and state_in and state_in != expected_state:
            st.info("Aviso: el 'state' no coincide (posible nueva pestaña). Usando el flujo rehidratado con el state recibido…")

        # 5) Intercambiar código por tokens
        from urllib.parse import urlencode
        current_url = f"{oo['redirect_uri']}?{urlencode({k: (v[0] if isinstance(v, list) else v) for k, v in qp.items()}, doseq=True)}"
        try:
            flow.fetch_token(authorization_response=current_url)
            creds = flow.credentials
            info = _fetch_userinfo_json_with_retry(creds.token)
            return _finalize_identity(creds, info)
        except Exception as e:
            msg = str(e)
            if "csrf" in msg.lower() or "state" in msg.lower():
                _csrf_mismatch_hint("Paso 0")
                st.stop()
            st.error(f"No se pudo verificar identidad: {e}")
            st.stop()

    # -----------------------------
    # UI de inicio (todavía no volvimos de Google)
    # -----------------------------
    if oo.get("use_redirect"):
        try:
            st.link_button("Continuar con Google", auth_url)
        except Exception:
            st.markdown(
                f'<a href="{auth_url}" target="_self" rel="noopener" '
                f'style="display:inline-block;padding:.6rem 1rem;border-radius:8px;'
                f'background:#8e7cc3;color:#fff;text-decoration:none;font-weight:600;">'
                f'Continuar con Google</a>',
                unsafe_allow_html=True
            )
        st.caption("Serás redirigido a esta app automáticamente después de otorgar permisos.")
    else:
        st.info("Modo manual activo (no hay credenciales WEB en [auth]).")
        st.markdown(f"🔗 **Paso A (identidad):** [Iniciar sesión con Google]({auth_url})")
        with st.expander("Ver/copiar URL de autorización (identidad)"):
            st.code(auth_url)
        url = st.text_input(
            "🔑 Paso B (identidad): pegá la URL completa (http://localhost/?code=...&state=...)",
            key="auth_response_url_oidc",
            placeholder="http://localhost/?code=...&state=..."
        )
        c1, c2 = st.columns(2)
        with c1:
            if st.button("Verificar identidad", type="primary", key="btn_oidc_connect"):
                raw = (url or "").strip()
                if not raw:
                    st.error("Pegá la URL completa de redirección (incluye code y state).")
                    st.stop()
                try:
                    from urllib.parse import urlparse, parse_qs
                    q = parse_qs(urlparse(raw).query)
                    state_in_manual = q.get("state", [None])[0]

                    flow = None
                    # 1) Priorizar el Flow guardado para el state que vino en la URL
                    if state_in_manual and state_in_manual in store:
                        flow = store.pop(state_in_manual)["flow"]

                    # 2) Si no existe, recrear y forzar state
                    if not flow:
                        acct_for_dest = st.secrets.get("oauth_app_key", "ACCESO")
                        flow = build_flow(acct_for_dest, ["openid","email","profile"] + SCOPES_DRIVE + SCOPES_GSC)
                        try:
                            setattr(flow, "_state", state_in_manual)
                        except Exception:
                            pass
                    _force_flow_state(flow, state_in_manual)

                    # 3) Intercambiar código por tokens usando la URL pegada
                    flow.fetch_token(authorization_response=raw)
                    creds = flow.credentials
                    info = _fetch_userinfo_json_with_retry(creds.token)
                    return _finalize_identity(creds, info)
                except Exception as e:
                    msg = str(e)
                    if "csrf" in msg.lower() or "state" in msg.lower():
                        _csrf_mismatch_hint("Paso 0")
                        st.stop()
                    st.error(f"No se pudo verificar identidad: {e}")
                    st.stop()
        with c2:
            if st.button("Reiniciar Paso 0", key="btn_reset_oidc"):
                st.session_state.pop("oauth_oidc", None)
                st.session_state.pop("_google_identity", None)
                clear_qp()
                st.rerun()

    return st.session_state.get("_google_identity")

# ------------------------------------------------------------
# Pantalla de LOGOUT
# ------------------------------------------------------------
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

def logout_screen(app_home: str):
    st.header("Cerrar sesión")
    ident = st.session_state.get("_google_identity") or {}
    current_email = ident.get("email") or "—"
    st.write(f"Usuario actual: **{current_email}**")

    revoke = st.checkbox("Revocar permisos de Google (Drive/Sheets y Search Console)", value=True)
    wipe_pkg = st.checkbox("Borrar caché del paquete externo (.ext_pkgs/)", value=False)

    col1, col2 = st.columns([1,1])
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
                "sc_account_choice",
                "DEBUG",
            ]:
                st.session_state.pop(k, None)
            st.markdown(f"<meta http-equiv='refresh' content='0; url={app_home}'>", unsafe_allow_html=True)
            st.stop()
    with col2:
        if st.button("Cancelar"):
            clear_qp()
            st.rerun()