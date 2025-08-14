# modules/utils.py
import streamlit as st
import json

def debug_log(msg: str, data=None):
    if st.session_state.get("DEBUG"):
        st.info(msg)
        if data is not None:
            try:
                st.code(json.dumps(data, indent=2, ensure_ascii=False))
            except Exception:
                st.code(str(data))

# ✅ Store en memoria del servidor (sobrevive a recargas mientras viva el worker)
@st.cache_resource(show_spinner=False)
def token_store():
    return {"dest": {}, "src": {}}
