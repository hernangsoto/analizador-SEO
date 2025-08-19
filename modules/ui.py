# modules/ui.py
from __future__ import annotations

import base64
import shutil
from urllib.parse import quote
from types import SimpleNamespace

import requests
import streamlit as st
import streamlit.components.v1 as components
from streamlit.errors import StreamlitAuthError


# =============================
# Branding / utilidades
# =============================

def _inline_logo_src(logo_url: str) -> str:
    """
    Descarga el logo y devuelve un data: URI (SVG utf8 o binario base64).
    Si falla, retorna la URL original.
    """
    try:
        if logo_url.startswith(("http://", "https://")):
            r = requests.get(logo_url, timeout=10)
            if r.status_code == 200:
                content_type = r.headers.get("Content-Type", "")
                # SVG inline utf8
                if "svg" in content_type or logo_url.lower().endswith(".svg"):
                    return f"data:image/svg+xml;utf8,{quote(r.text)}"
                # Otros formatos → base64
                mime = "image/png"
                if "jpeg" in content_type or logo_url.lower().endswith((".jpg", ".jpeg")):
                    mime = "image/jpeg"
                elif "webp" in content_type or logo_url.lower().endswith(".webp"):
                    mime = "image/webp"
                b64 = base64.b64encode(r.content).decode("ascii")
                return f"data:{mime};base64,{b64}"
    except Exception:
        pass
    return logo_url


def apply_page_style(
    page_bg: str = "#ffffff",
    use_gradient: bool = False,
    band_height_px: int = 110,
    header_bg: str = "#5c417c",
    header_height_px: int = 64,
) -> None:
    """
    Aplica estilos globales:
    - Define --app-header-height para sincronizar el offset del banner.
    - Pinta el header nativo con `header_bg`.
    - Opcionalmente aplica una banda/gradiente superior en el body.
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
            --app-header-height: {header_height_px}px;
            background: {css_bg} !important;
        }}
        header[data-testid="stHeader"] {{
            background: {header_bg} !important;
            color: #fff !important;
            min-height: var(--app-header-height);
            height: var(--app-header-height);
            box-shadow: none !important;
            z-index: 1000 !important;
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


def render_brand_header(
    logo_url: str,
    width_px: int | None = None,
    height_px: int = 27,
    band_bg: str = "transparent",
    top_offset_px: int | None = None,   # None => usa --app-header-height
    pinned: bool = True,                # True = fixed, False = sticky
    nudge_px: int = 0,                  # ↑↓  (+ baja, − sube)
    z_index: int = 3000,                # por delante del header
    x_align: str = "left",              # "left" | "center" | "right"
    x_offset_px: int = 0,               # →←
    container_max_px: int = 1200,
) -> None:
    """Dibuja el logo como capa limpia, sin recuadro ni sombras."""
    src = _inline_logo_src(logo_url)
    dim_css = f"height:{height_px}px !important; width:auto !important; max-width:100% !important;"

    base = "var(--app-header-height)" if top_offset_px is None else f"{top_offset_px}px"
    if nudge_px == 0:
        top_css = base
    elif nudge_px > 0:
        top_css = f"calc({base} + {nudge_px}px)"
    else:
        top_css = f"calc({base} - {abs(nudge_px)}px)"

    justify = {"left": "flex-start", "center": "center", "right": "flex-end"}.get(x_align, "flex-start")
    if x_align == "left":
        img_margin = f"margin-left:{x_offset_px}px;"
    elif x_align == "right":
        img_margin = f"margin-right:{x_offset_px}px;"
    else:
        img_margin = ""

    if pinned:
        st.markdown(
            f"""
            <style>
            .brand-fixed {{
              position: fixed;
              top: {top_css};
              left: var(--brand-left, 0px);
              width: var(--brand-width, 100%);
              z-index: {z_index};
              background: transparent !important;
              pointer-events: none;
              transition: left .18s ease, width .18s ease;
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
        st.markdown(
            f"""
            <style>
            .brand-banner {{
              background: {band_bg} !important;
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
    """Renderiza el banner una sola vez por 'firma' de parámetros."""
    sig = (
        logo_url, width_px, height_px, band_bg, top_offset_px,
        pinned, nudge_px, z_index, x_align, x_offset_px, container_max_px
    )
    if st.session_state.get("_brand_sig") == sig:
        return
    st.session_state["_brand_sig"] = sig

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


def reset_brand_banner() -> None:
    st.session_state.pop("_brand_sig", None)


def hide_old_logo_instances() -> None:
    st.markdown(
        """
        <style>
        header [data-testid="stLogo"] { display:none !important; }
        header svg[height][width] { opacity:0 !important; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def enable_brand_auto_align() -> None:
    """Actualiza --brand-left / --brand-width con el ancho del contenedor."""
    st.markdown(
        """
        <style>
        .brand-fixed { left: var(--brand-left, 0px) !important; width: var(--brand-width, 100%) !important; }
        iframe.stIFrame[srcdoc*="--brand-left"] { width:0 !important; height:0 !important; opacity:0 !important; pointer-events:none !important; border:0 !important; display:block !important; margin:0 !important; padding:0 !important; }
        iframe.stIFrame[srcdoc*="--brand-left"] + div { display:none !important; }
        </style>
        """,
        unsafe_allow_html=True,
    )
    components.html(
        """
        <script>
        (function() {
          const doc = window.parent.document;
          function updateVars() {
            const bc = doc.querySelector('.block-container');
            if (!bc) return;
            const r = bc.getBoundingClientRect();
            doc.documentElement.style.setProperty('--brand-left',  r.left + 'px');
            doc.documentElement.style.setProperty('--brand-width', r.width + 'px');
          }
          const obs = new MutationObserver(() => updateVars());
          obs.observe(doc.body, { attributes:true, childList:true, subtree:true });
          window.addEventListener('resize', updateVars);
          updateVars();
        })();
        </script>
        """,
        height=0,
    )


# =============================
# Helpers de usuario / Sidebar
# =============================

def _auth_mode() -> str:
    """
    Devuelve el modo de autenticación configurado por Secrets.
    Valores: "streamlit" (recomendado en Cloud) o "bypass".
    """
    auth = st.secrets.get("auth", {})
    return str(auth.get("mode", "streamlit")).lower().strip()

def _allow_bypass() -> bool:
    return bool(st.secrets.get("auth", {}).get("allow_bypass", True))


def get_user():
    """
    Devuelve el usuario autenticado (st.user o experimental_user).
    Si no hay auth habilitada y está activado el bypass, devuelve un usuario dummy.
    """
    u = getattr(st, "user", getattr(st, "experimental_user", None))
    if u:
        return u
    if st.session_state.get("_auth_bypass"):
        return SimpleNamespace(is_logged_in=True, name="Invitado", email="—", picture=None)
    return None


def get_first_name(full_name: str | None) -> str:
    if not full_name:
        return "👋"
    return full_name.split()[0]


def _merge_identity(user):
    """
    Combina datos de st.user y de la identidad de Drive guardada en sesión
    (st.session_state['_google_identity']) para mostrar nombre, email y foto.
    """
    gd = st.session_state.get("_google_identity") or {}
    name = (
        getattr(user, "name", None)
        | gd.get("displayName") if False else (getattr(user, "name", None) or gd.get("displayName"))
    )
    if not name:
        name = gd.get("emailAddress") or "Invitado"

    email = getattr(user, "email", None) or gd.get("emailAddress") or "—"
    picture = getattr(user, "picture", None) or gd.get("photoLink") or None
    return name, email, picture


def sidebar_user_info(user, maintenance_extra=None):
    """
    Sidebar con avatar, nombre, email y utilidades de mantenimiento.
    Usa fallbacks de identidad si Streamlit no aporta name/picture.
    """
    name, email, picture = _merge_identity(user)

    with st.sidebar:
        with st.container():
            c1, c2 = st.columns([1, 3])
            with c1:
                if picture:
                    try:
                        r = requests.get(picture, timeout=10)
                        if r.status_code == 200:
                            st.image(r.content, width=64)
                        else:
                            st.warning("No se pudo cargar la imagen.")
                    except Exception as e:
                        st.warning(f"Error al cargar la imagen: {e}")
                else:
                    # Avatar neutro
                    st.markdown(
                        '<div style="width:64px;height:64px;border-radius:50%;background:#eee;display:flex;align-items:center;justify-content:center;font-size:22px">🙂</div>',
                        unsafe_allow_html=True
                    )
            with c2:
                st.header("Información del usuario", anchor=False)
                st.write(f"**Nombre:** {name}")
                st.write(f"**Correo:** {email}")

        st.divider()
        st.markdown("**🧹 Mantenimiento**")

        if callable(maintenance_extra):
            maintenance_extra()

        if st.button("Borrar caché del paquete externo (.ext_pkgs/)", key="btn_clean_extpkgs", use_container_width=True):
            try:
                shutil.rmtree(".ext_pkgs", ignore_errors=True)
                st.success("✅ Caché borrada. Hacé *Rerun* para reinstalar el paquete externo.")
            except Exception as e:
                st.error(f"No pude borrar .ext_pkgs: {e}")

        st.divider()
        # Cerrar sesión (solo si el entorno lo soporta)
        if _auth_mode() == "streamlit":
            if st.button(":material/logout: Cerrar sesión", key="btn_logout", use_container_width=True):
                try:
                    st.logout()
                except StreamlitAuthError:
                    st.error("No fue posible cerrar sesión en este despliegue.")
                st.rerun()
        else:
            if st.button("Salir del modo de pruebas", key="btn_exit_bypass", use_container_width=True):
                st.session_state.pop("_auth_bypass", None)
                st.rerun()


def login_screen():
    """
    Pantalla de login:
    - En Cloud con “Only specific people can view this app”, el login ocurre ANTES de cargar el script.
      Por eso NO llamamos st.login() aquí. Solo damos instrucciones y un botón de reintento.
    - Si permitís bypass (auth.allow_bypass=true), mostramos un botón para modo pruebas.
    """
    st.header("Esta aplicación es privada.")
    st.subheader("Iniciá sesión para continuar.")

    mode = _auth_mode()
    allow_bypass = _allow_bypass()

    if mode == "streamlit":
        st.error("El inicio de sesión de Streamlit no está habilitado o falló en este despliegue.")
        st.markdown(
            """
- Verificá **Manage app → Sharing → Who can view this app = Only specific people** y agregá tus emails.
- Guardá (Save changes) y hacé **Reboot app**.
- Probá en **ventana privada** o limpiando cookies.
            """
        )
        if st.button("🔄 Reintentar", key="btn_retry_login"):
            st.rerun()

        if allow_bypass:
            st.caption("¿Solo querés probar la UI ahora mismo?")
            if st.button("Continuar sin login (modo pruebas)", key="btn_bypass"):
                st.session_state["_auth_bypass"] = True
                st.rerun()

    else:
        st.info("Autenticación desactivada en este despliegue. Podés continuar en modo pruebas.")
        if st.button("Continuar (modo pruebas)", key="btn_bypass_only"):
            st.session_state["_auth_bypass"] = True
            st.rerun()
