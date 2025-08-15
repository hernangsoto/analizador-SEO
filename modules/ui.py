# modules/ui.py
from __future__ import annotations

import base64
import shutil
from pathlib import Path
from urllib.parse import quote

import requests
import streamlit as st


# =============================
# Estilos / branding
# =============================

def apply_page_style(
    page_bg: str = "#ffffff",
    use_gradient: bool = False,
    band_height_px: int = 110,
    header_bg: str = "#5c417c",
    header_height_px: int = 64,   # ← alto real del header (ajustable)
) -> None:
    """
    Fondo + header nativo con tu color. Define --app-header-height para que
    el banner use exactamente la misma altura de offset.
    """
    if use_gradient:
        css_bg = (
            f"linear-gradient(180deg, {header_bg} 0, {header_bg} {band_height_px}px, "
            f"{page_bg} {band_height_px}px)"
        )
    else:
        css_bg = page_bg

    st.markdown(
        f"""
        <style>
        .stApp {{
            --app-header-height: {header_height_px}px;   /* ← clave */
            background: {css_bg} !important;
        }}
        header[data-testid="stHeader"] {{
            background: {header_bg} !important;
            color: #fff !important;
            min-height: var(--app-header-height);
            height: var(--app-header-height);
            box-shadow: none !important;
            z-index: 1000;  /* por si tu tema lo cambia */
        }}
        header [data-testid="stToolbar"] * {{
            color: #fff !important;
            fill: #fff !important;
        }}
        .block-container {{
            padding-top: 0.75rem !important;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def _inline_logo_src(logo_url: str) -> str:
    """
    Devuelve un data:URI para el logo (desde ruta local o URL remota).
    Si falla, retorna la URL original.
    """
    try:
        p = Path(logo_url)
        if p.exists() and p.is_file():
            if p.suffix.lower() == ".svg":
                return f"data:image/svg+xml;utf8,{quote(p.read_text(encoding='utf-8'))}"
            data = p.read_bytes()
            mime = "image/png"
            if p.suffix.lower() in {".jpg", ".jpeg"}:
                mime = "image/jpeg"
            elif p.suffix.lower() == ".webp":
                mime = "image/webp"
            b64 = base64.b64encode(data).decode("ascii")
            return f"data:{mime};base64,{b64}"

        if logo_url.startswith("http"):
            r = requests.get(logo_url, timeout=10)
            if r.status_code == 200:
                ct = r.headers.get("Content-Type", "")
                if "svg" in ct or logo_url.lower().endswith(".svg"):
                    return f"data:image/svg+xml;utf8,{quote(r.text)}"
                mime = "image/png"
                if "jpeg" in ct or logo_url.lower().endswith((".jpg", ".jpeg")):
                    mime = "image/jpeg"
                elif "webp" in ct or logo_url.lower().endswith(".webp"):
                    mime = "image/webp"
                b64 = base64.b64encode(r.content).decode("ascii")
                return f"data:{mime};base64,{b64}"
    except Exception:
        pass
    return logo_url


def render_brand_header(
    logo_url: str,
    width_px: int | None = None,
    height_px: int = 27,
    band_bg: str = "transparent",
    top_offset_px: int | None = None,   # None => usa --app-header-height
    pinned: bool = True,                # fijo al hacer scroll
    nudge_px: int = -30,                  # ↑↓  (+ baja, − sube)
    z_index: int = 3000,                # por delante del header
    x_align: str = "left",              # "left" | "center" | "right"
    x_offset_px: int = 50,               # →←  (si left: positivo mueve a la derecha; si right: positivo mueve a la izquierda)
    container_max_px: int = 1200,       # ancho del contenido para alinear
) -> None:
    src = _inline_logo_src(logo_url)
    dim_css = f"height:{height_px}px !important; width:auto !important; max-width:100% !important;"

    # top calculado (base + nudge)
    base = "var(--app-header-height)" if top_offset_px is None else f"{top_offset_px}px"
    if nudge_px == 0:
        top_css = base
    elif nudge_px > 0:
        top_css = f"calc({base} + {nudge_px}px)"
    else:
        top_css = f"calc({base} - {abs(nudge_px)}px)"

    # alineación horizontal
    justify = {"left": "flex-start", "center": "center", "right": "flex-end"}.get(x_align, "flex-start")
    if x_align == "left":
        img_margin = f"margin-left:{x_offset_px}px;"
    elif x_align == "right":
        img_margin = f"margin-right:{x_offset_px}px;"
    else:
        img_margin = ""

    if pinned:
        # capa fija full-width
        st.markdown(
            f"""
            <style>
            .brand-fixed {{
              position: fixed;
              top: {top_css};
              left: 0;
              width: 100%;
              z-index: {z_index};
              background: transparent !important;
              pointer-events: none;
            }}
            .brand-fixed .brand-inner {{
              max-width: {container_max_px}px;
              margin: 0 auto;
              padding: 0 16px;
              display: flex; align-items: center; justify-content: {justify};
            }}
            .brand-fixed img.brand-logo {{
              {dim_css}
              {img_margin}
              image-rendering: -webkit-optimize-contrast;
              object-fit: contain;
              display: inline-block !important;
              pointer-events: none;
            }}
            </style>
            <div class="brand-fixed">
              <div class="brand-inner">
                <img class="brand-logo" src="{src}" alt="Brand" />
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    else:
        # variante sticky
        st.markdown(
            f"""
            <style>
            .brand-banner {{
              background: transparent !important;
              margin: 0 0 8px 0 !important;
              padding: 0 !important;
              position: -webkit-sticky; position: sticky;
              top: {top_css};
              z-index: {z_index};
              display: flex; align-items: center; justify-content: {justify};
            }}
            .brand-banner img.brand-logo {{
              {dim_css}
              {img_margin}
              image-rendering: -webkit-optimize-contrast;
              object-fit: contain;
              display: inline-block !important;
            }}
            </style>
            <div class="brand-banner">
              <img class="brand-logo" src="{src}" alt="Brand" />
            </div>
            """,
            unsafe_allow_html=True,
        )


def render_brand_header_once(
    logo_url: str,
    width_px: int | None = None,
    height_px: int = 27,
    band_bg: str = "transparent",
    top_offset_px: int | None = None,
    pinned: bool = True,
    nudge_px: int = 0,
    z_index: int = 3000,
    x_align: str = "left",
    x_offset_px: int = 0,
    container_max_px: int = 1200,
) -> None:
    if st.session_state.get("_brand_rendered"):
        return
    st.session_state["_brand_rendered"] = True
    render_brand_header(
        logo_url=logo_url,
        width_px=width_px,
        height_px=height_px,
        band_bg=band_bg,
        top_offset_px=top_offset_px,
        pinned=pinned,
        nudge_px=nudge_px,
        z_index=z_index,
        x_align=x_align,
        x_offset_px=x_offset_px,
        container_max_px=container_max_px,
    )



def hide_old_logo_instances(logo_url: str) -> None:
    """
    Oculta el mismo logo si aparecía en otros lugares, menos dentro del banner.
    """
    st.markdown(
        f"""
        <style>
        img[src*="{logo_url}"]:not(.brand-banner img) {{
          display:none !important;
        }}
        .brand-banner img {{
          display:inline-block !important;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


# =============================
# User helpers
# =============================

def get_user():
    """Devuelve st.user (o experimental_user)."""
    return getattr(st, "user", getattr(st, "experimental_user", None))


def get_first_name(full_name: str | None) -> str:
    if not full_name:
        return "👋"
    return full_name.split()[0]


def sidebar_user_info(user) -> None:
    """Sidebar con avatar, nombre, email y mantenimiento."""
    with st.sidebar:
        with st.container():
            c1, c2 = st.columns([1, 3])
            with c1:
                if getattr(user, "picture", None):
                    try:
                        r = requests.get(user.picture, timeout=5)
                        if r.status_code == 200:
                            st.image(r.content, width=64)
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


def login_screen() -> None:
    st.header("Esta aplicación es privada.")
    st.subheader("Por favor, inicia sesión.")
    st.button(":material/login: Iniciar sesión con Google", on_click=st.login)


# (Opcional) declara lo exportado para evitar confusiones en imports con __all__
__all__ = [
    "apply_page_style",
    "render_brand_header_once",
    "hide_old_logo_instances",
    "get_user",
    "get_first_name",
    "sidebar_user_info",
    "login_screen",
]
