# modules/ui.py
from __future__ import annotations

import os
import shutil
import requests
import streamlit as st


# =============================
# Branding / estilos
# =============================

def apply_page_style(page_bg: str = "#0f172a", use_gradient: bool = True):
    """
    Aplica estilos globales.
    - use_gradient=True: banda superior oscura de ~220px y resto claro.
    - use_gradient=False: toda la página del color `page_bg`.
    """
    if use_gradient:
        css_bg = f"linear-gradient(180deg, {page_bg} 0, {page_bg} 220px, #ffffff 220px)"
    else:
        css_bg = page_bg

    st.markdown(
        f"""
        <style>
        .stApp {{
            background: {css_bg} !important;
        }}
        .block-container {{
            padding-top: 0.75rem !important;
        }}
        header[data-testid="stHeader"] {{
            background: transparent !important;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def apply_page_style(page_bg: str = "#5c417c", use_gradient: bool = True, band_height_px: int = 110):
    """
    Aplica estilos globales.
    - use_gradient=True: banda superior de `band_height_px` y resto claro.
    - page_bg: color de la banda (#5c417c por pedido).
    """
    if use_gradient:
        css_bg = (
            f"linear-gradient(180deg, {page_bg} 0, {page_bg} {band_height_px}px, "
            f"#ffffff {band_height_px}px)"
        )
    else:
        css_bg = page_bg

    st.markdown(
        f"""
        <style>
        .stApp {{
            background: {css_bg} !important;
        }}
        .block-container {{
            padding-top: 0.75rem !important;
        }}
        header[data-testid="stHeader"] {{
            background: transparent !important;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_brand_header(
    logo_url: str,
    width_px: int = 153,
    height_px: int = 27,
    band_bg: str = "#5c417c",
):
    """Franja superior con logo (153x27 por defecto), STICKY al hacer scroll."""
    st.markdown(
        f"""
        <div class="brand-banner" style="
            background:{band_bg};
            border-radius: 10px;
            margin: 0 0 12px 0;
            padding: 8px 16px;
            display: flex; align-items: center;
            position: -webkit-sticky;   /* Safari */
            position: sticky;
            top: 0;
            z-index: 999;
            box-shadow: 0 4px 14px rgba(0,0,0,0.25);
        ">
            <img src="{logo_url}" alt="Brand"
                 width="{width_px}" height="{height_px}"
                 style="width:{width_px}px;height:{height_px}px;display:block;" />
        </div>
        """,
        unsafe_allow_html=True,
    )


# =============================
# User helpers
# =============================

def get_user():
    # Compatibilidad con st.user o st.experimental_user
    return getattr(st, "user", getattr(st, "experimental_user", None))


def get_first_name(full_name: str | None) -> str:
    if not full_name:
        return "👋"
    return full_name.split()[0]


def sidebar_user_info(user):
    with st.sidebar:
        with st.container():
            c1, c2 = st.columns([1, 3])
            with c1:
                if getattr(user, "picture", None):
                    try:
                        r = requests.get(user.picture, timeout=5)
                        if r.status_code == 200:
                            st.image(r.content, width=96)
                        else:
                            st.warning("No se pudo cargar la imagen.")
                    except Exception as e:
                        st.warning(f"Error al cargar la imagen: {e}")
                else:
                    st.info("Sin imagen de perfil.")
            with c2:
                st.header("Información del usuario", anchor=False)
                st.write(f"**Nombre:** {getattr(user, 'name', '—')}")
                st.write(f"**Correo:** {getattr(user, 'email', '—')}")

        # Bloque mantenimiento (arriba del cerrar sesión)
        st.divider()
        st.markdown("**🧹 Mantenimiento**")
        if st.button(
            "Borrar caché del paquete externo (.ext_pkgs/)",
            key="btn_clean_extpkgs",
            use_container_width=True,
        ):
            try:
                shutil.rmtree(".ext_pkgs", ignore_errors=True)
                st.success("✅ Caché borrada. Hacé *Rerun* para reinstalar el paquete externo.")
            except Exception as e:
                st.error(f"No pude borrar .ext_pkgs: {e}")

        st.divider()
        st.button(":material/logout: Cerrar sesión", on_click=st.logout, use_container_width=True)


def login_screen():
    st.header("Esta aplicación es privada.")
    st.subheader("Por favor, inicia sesión.")
    st.button(":material/login: Iniciar sesión con Google", on_click=st.login)
