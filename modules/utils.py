# modules/utils.py
from __future__ import annotations

import json
import streamlit as st


def debug_log(msg: str, data=None) -> None:
    """
    Muestra mensajes de depuración solo cuando st.session_state["DEBUG"] es True.
    - msg: título o descripción corta.
    - data: cualquier objeto extra; si es serializable a JSON lo imprime bonito, si no, str().
    """
    if st.session_state.get("DEBUG"):
        st.info(msg)
        if data is not None:
            try:
                st.code(json.dumps(data, indent=2, ensure_ascii=False))
            except Exception:
                st.code(str(data))


@st.cache_resource(show_spinner=False)
def token_store() -> dict:
    """
    Pequeño almacén en memoria del proceso (sobrevive a recargas mientras viva el worker).
    Se usa para guardar credenciales OAuth y poder recuperarlas sin pedir reautorización.

    Estructura:
    {
      "dest": { "<email_streamlit>": { ...creds_dict... } },  # Drive/Sheets (cuenta PERSONAL)
      "src":  { "<ACCESO|ACCESO_MEDIOS>": { ...creds_dict... } }  # Search Console (fuente)
    }
    """
    return {"dest": {}, "src": {}}
