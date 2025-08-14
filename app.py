import streamlit as st

st.title ("Autenticación 22")
if st.button ("Authenticate"):
    st.login ("google")
