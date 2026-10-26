"""
Validación del CONTENIDO de los adjuntos (no solo de la extensión).

El navegador envía el tipo MIME que quiera y el nombre lo elige el usuario:
se aceptaba un ejecutable renombrado a .pdf o un HTML con script guardado como
.jpg, y se servía con el MIME del cliente. Ahora:
  1. La firma del archivo (magic bytes) debe corresponder a su extensión.
  2. El MIME se deriva en el servidor a partir de la extensión validada.
  3. El nombre original se sanea (sin rutas ni caracteres de control, con la
     extensión conservada al truncar).
"""
from __future__ import annotations

import os
import unicodedata

# Extensión → MIME fijo del servidor.
MIME_POR_EXTENSION: dict[str, str] = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".csv": "text/csv; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
}

_ZIP = (b"PK\x03\x04",)
_FIRMAS: dict[str, tuple[bytes, ...]] = {
    ".pdf": (b"%PDF-",),
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".docx": _ZIP,
    ".xlsx": _ZIP,
}


def _es_texto(contenido: bytes) -> bool:
    if b"\x00" in contenido[:8192]:
        return False
    muestra = contenido[:8192]
    try:
        texto = muestra.decode("utf-8")
    except UnicodeDecodeError:
        # Puede cortarse un carácter multibyte al final de la muestra.
        try:
            texto = muestra[:-3].decode("utf-8")
        except UnicodeDecodeError:
            return False
    # Un .txt/.csv no es una página web: se rechaza HTML/SVG/script disfrazado.
    cabeza = texto.lstrip().lower()[:200]
    return not cabeza.startswith(("<!doctype", "<html", "<svg", "<script", "<?xml"))


def contenido_coincide(extension: str, contenido: bytes) -> bool:
    """¿La firma del archivo corresponde a su extensión?"""
    ext = extension.lower()
    if ext == ".webp":
        return contenido[:4] == b"RIFF" and contenido[8:12] == b"WEBP"
    if ext in (".csv", ".txt"):
        return _es_texto(contenido)
    firmas = _FIRMAS.get(ext)
    return bool(firmas) and contenido.startswith(firmas)


def mime_de(extension: str) -> str:
    return MIME_POR_EXTENSION.get(extension.lower(), "application/octet-stream")


def sanear_nombre(nombre: str | None, maximo: int = 255) -> str:
    """'../../etc/x.pdf' → 'x.pdf'; quita controles y conserva la extensión al truncar."""
    base = os.path.basename((nombre or "").replace("\\", "/")) or "archivo"
    base = "".join(ch for ch in base if unicodedata.category(ch)[0] != "C").strip(" .") or "archivo"
    raiz, ext = os.path.splitext(base)
    if len(base) > maximo:
        base = raiz[: maximo - len(ext)] + ext
    return base
