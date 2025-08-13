import streamlit as st

st.title("Mi primera app en Streamlit 🚀")
nombre = st.text_input("Escribe tu nombre")
if nombre:
    st.write(f"¡Hola, {nombre}!")
