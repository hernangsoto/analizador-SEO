# --- Selección de cuenta para Google Analytics 4 (igual que SC) ---
st.subheader("Selecciona la cuenta con acceso a Google Analytics 4")
ga4_account_options = ["Acceso", "Acceso Medios", "Acceso en cuenta personal de Nomadic"]
_default_ga4_label = st.session_state.get("ga4_account_choice", "Acceso en cuenta personal de Nomadic")
ga4_default_idx = ga4_account_options.index(_default_ga4_label) if _default_ga4_label in ga4_account_options else 2

ga4_choice = st.selectbox(
    "Elegí la cuenta para consultar datos de Google Analytics (GA4)",
    ga4_account_options, index=ga4_default_idx, key="ga4_account_choice"
)

def _ga4_choice_to_key(label: str) -> str | None:
    if norm(label) == norm("Acceso"): return "ACCESO"
    if norm(label) == norm("Acceso Medios"): return "ACCESO_MEDIOS"
    return None  # personal usa creds_dest

ga4_admin = None
ga4_data  = None

if ga4_choice == "Acceso en cuenta personal de Nomadic":
    # Reusar credenciales del Paso 0
    ga4_creds_dict = st.session_state.get("creds_dest") or token_store.load("creds_dest")
    if not ga4_creds_dict:
        st.error("No encuentro la sesión personal. Volvé a iniciar sesión en el Paso 0."); st.stop()

    if not has_ga4_scope(ga4_creds_dict.get("scopes")):
        st.warning("Tu cuenta personal no tiene permisos de Google Analytics todavía.")
        st.caption("Volvé a realizar el Paso 0 solicitando también el permiso de Analytics.")
        st.stop()

    try:
        if ensure_admin_client is None or ensure_data_client is None:
            raise RuntimeError("Módulos GA4 no disponibles. Agregá `modules/ga4_admin.py` y `modules/ga4.py` y `google-analytics-*-` a requirements.")
        creds = Credentials(**ga4_creds_dict)
        ga4_admin = ensure_admin_client(creds)
        ga4_data  = ensure_data_client(creds)
        st.session_state["creds_ga4"] = ga4_creds_dict
        st.session_state["ga4_account_label"] = "Acceso en cuenta personal de Nomadic"
        st.markdown(
            f'''
            <div class="success-inline">
                Cuenta de acceso (GA4): <strong>Acceso en cuenta personal de Nomadic</strong>
                <a href="{APP_HOME}?action=change_ga4" target="_self" rel="nofollow">(Cambiar cuenta GA4)</a>
            </div>
            ''',
            unsafe_allow_html=True
        )
    except Exception as e:
        st.error(f"No pude inicializar Analytics con la cuenta personal: {e}")
        st.stop()

else:
    # Installed app flow (igual que SC) pero con scopes de GA4
    wanted_key = _ga4_choice_to_key(ga4_choice)  # "ACCESO" o "ACCESO_MEDIOS"
    need_new_auth = (
        not st.session_state.get("ga4_step_done") or
        (st.session_state.get("ga4_account_label") != ga4_choice)
    )
    if need_new_auth:
        flow = _build_flow_installed_or_local(wanted_key, GA4_SCOPES)
        auth_url, state = flow.authorization_url(prompt="consent select_account", access_type="offline")
        st.markdown(f"🔗 **Autorizar acceso a Google Analytics** → {auth_url}")
        with st.expander("Ver/copiar URL de autorización (GA4)"):
            st.code(auth_url)

        url = st.text_input(
            "Pegá la URL de redirección (http://localhost/?code=...&state=...)",
            key=f"auth_response_url_ga4_{wanted_key}",
            placeholder="http://localhost/?code=...&state=...",
        )

        c1, c2 = st.columns([1,1])
        with c1:
            if st.button("Conectar Google Analytics", key=f"btn_connect_ga4_{wanted_key}", type="secondary"):
                if not url.strip():
                    st.error("Pegá la URL completa de redirección (incluye code y state)."); st.stop()
                from urllib.parse import urlsplit, parse_qs
                try:
                    qs = parse_qs(urlsplit(url.strip()).query)
                    returned_state = (qs.get("state") or [""])[0]
                except Exception:
                    returned_state = ""
                if not returned_state or returned_state != state:
                    st.error("CSRF Warning: el 'state' devuelto no coincide con el generado."); st.stop()
                try:
                    flow.fetch_token(authorization_response=url.strip())
                    creds = flow.credentials
                    ga4_creds_dict = {
                        "token": creds.token,
                        "refresh_token": getattr(creds, "refresh_token", None),
                        "token_uri": getattr(creds, "token_uri", "https://oauth2.googleapis.com/token"),
                        "client_id": getattr(creds, "client_id", None),
                        "client_secret": getattr(creds, "client_secret", None),
                        "scopes": list(getattr(creds, "scopes", GA4_SCOPES)),
                    }
                    st.session_state["creds_ga4"] = ga4_creds_dict
                    st.session_state["ga4_account_label"] = ga4_choice
                    st.session_state["ga4_step_done"] = True
                    token_store.save("creds_ga4", ga4_creds_dict)  # opcional, por consistencia
                    st.rerun()
                except Exception as e:
                    st.error("No se pudo conectar Google Analytics. Reintentá la autorización.")
                    st.caption(f"Detalle técnico: {e}")
        with c2:
            if st.button("Reiniciar conexión (GA4)", key=f"btn_reset_ga4_{wanted_key}"):
                for k in ("creds_ga4","ga4_step_done","ga4_account_label","ga4_property_choice","ga4_property_id","ga4_property_label"):
                    st.session_state.pop(k, None)
                clear_qp(); st.rerun()

    # Si ya tenemos tokens guardados en sesión, instanciamos clientes
    if st.session_state.get("creds_ga4"):
        try:
            if ensure_admin_client is None or ensure_data_client is None:
                raise RuntimeError("Módulos GA4 no disponibles. Agregá `modules/ga4_admin.py` y `modules/ga4.py` y `google-analytics-*-` a requirements.")
            creds = Credentials(**st.session_state["creds_ga4"])
            ga4_admin = ensure_admin_client(creds)
            ga4_data  = ensure_data_client(creds)
            st.markdown(
                f'''
                <div class="success-inline">
                    Cuenta de acceso (GA4): <strong>{st.session_state.get('ga4_account_label','(desconocida)')}</strong>
                    <a href="{APP_HOME}?action=change_ga4" target="_self" rel="nofollow">(Cambiar cuenta GA4)</a>
                </div>
                ''',
                unsafe_allow_html=True
            )
        except Exception as e:
            st.error(f"No pude inicializar el cliente de GA4: {e}")
            st.stop()

# --- Elegir MEDIO (Propiedad GA4) ---
if ga4_admin:
    props = list_account_property_summaries(ga4_admin)  # [{account_name, property_display_name, property_id, ...}, ...]
    if not props:
        st.warning("No se encontraron propiedades GA4 con esta cuenta.")
    else:
        labels = [
            f"{p['account_name']}  /  {p['property_display_name']}  —  {p['property_id']}"
            for p in props
        ]
        default_label = st.session_state.get("ga4_property_choice")
        default_idx = labels.index(default_label) if default_label in labels else 0

        choice = st.selectbox("Elegí el medio (Propiedad GA4)", labels, index=default_idx, key="ga4_property_choice")
        sel = props[labels.index(choice)]
        st.session_state["ga4_property_id"] = sel["property_id"]
        st.session_state["ga4_property_label"] = f"{sel['property_display_name']} (prop {sel['property_id']})"

        st.markdown(
            f"""
            <div class="success-inline">
                Medio seleccionado (GA4): <strong>{st.session_state['ga4_property_label']}</strong>
            </div>
            """,
            unsafe_allow_html=True
        )
