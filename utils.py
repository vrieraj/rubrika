"""
utils.py — Lógica de firma digital y renderizado de PDF
  · Listar certificados del DNIe (PKCS#11)
  · Ejecutar firma incremental en múltiples páginas
  · Renderizar páginas PDF como PNG bytes (pymupdf)
"""

import asyncio
import concurrent.futures
import io
import os
from datetime import datetime

DEFAULT_PKCS11_LIB = '/usr/lib/opensc-pkcs11.so'


# ─────────────────────────────────────────────────────────────────────────────
# PKCS#11 / DNIe
# ─────────────────────────────────────────────────────────────────────────────

def listar_certificados(lib_path=DEFAULT_PKCS11_LIB):
    """
    Devuelve lista de dicts con los certificados del token.
    No requiere PIN — abre sesión de solo lectura.
    """
    import pkcs11
    from pkcs11 import ObjectClass, Attribute
    from asn1crypto import x509 as _x509

    lib   = pkcs11.lib(lib_path)
    slots = lib.get_slots(token_present=True)
    if not slots:
        raise RuntimeError("No se detecta ninguna tarjeta. ¿Está conectado el DNIe?")

    certs = []
    for slot in slots:
        token = slot.get_token()
        try:
            with token.open() as session:
                for obj in session.get_objects({Attribute.CLASS: ObjectClass.CERTIFICATE}):
                    try:
                        label = obj[Attribute.LABEL]
                    except Exception:
                        label = "(sin etiqueta)"

                    subject   = label
                    nombre_cn = None
                    try:
                        raw  = bytes(obj[Attribute.VALUE])
                        cert = _x509.Certificate.load(raw)
                        subject = cert.subject.human_friendly
                        for rdn in cert.subject.chosen:
                            for attr in rdn:
                                if attr['type'].native == 'common_name':
                                    nombre_cn = attr['value'].native
                    except Exception:
                        pass

                    certs.append({
                        'label':     label,
                        'key_label': 'Kpriv' + label.replace('Cert', ''),
                        'subject':   subject,
                        'nombre':    nombre_cn,
                        'slot':      slot.slot_id,
                    })
        except Exception as e:
            certs.append({
                'label':   f'Error slot {slot.slot_id}: {e}',
                'subject': '', 'nombre': None, 'slot': slot.slot_id,
            })
    return certs


# ─────────────────────────────────────────────────────────────────────────────
# RENDERIZADO (pymupdf → PNG bytes)
# ─────────────────────────────────────────────────────────────────────────────

def renderizar_pagina(pdf_bytes: bytes, numero: int, dpi: int = 150) -> bytes:
    """
    Renderiza la página `numero` (1-based) y devuelve PNG en bytes.
    El llamador convierte a QPixmap con QPixmap.loadFromData().
    """
    import fitz
    doc  = fitz.open(stream=pdf_bytes, filetype='pdf')
    page = doc[numero - 1]
    mat  = fitz.Matrix(dpi / 72, dpi / 72)
    pix  = page.get_pixmap(matrix=mat)
    png  = pix.tobytes('png')
    doc.close()
    return png


def info_paginas(pdf_bytes: bytes):
    """
    Devuelve (total_paginas, [(ancho_pts, alto_pts), ...]).
    Los puntos PDF son la unidad nativa para las coordenadas de firma.
    """
    import fitz
    doc  = fitz.open(stream=pdf_bytes, filetype='pdf')
    info = [(p.rect.width, p.rect.height) for p in doc]
    doc.close()
    return len(info), info


# ─────────────────────────────────────────────────────────────────────────────
# FIRMA
# ─────────────────────────────────────────────────────────────────────────────

def _construir_sello(texto_firma: str, font_size: int, rubrica_bytes: bytes | None):
    """
    Construye el TextStampStyle de pyhanko.
    rubrica_bytes: PNG con fondo transparente y trazos azul marino,
                   ya procesado por CanvasRubrica.exportar_png().
    """
    from pyhanko.stamp.text import TextStampStyle, TextBoxStyle

    if rubrica_bytes:
        from PIL import Image as PILImage
        from pyhanko.pdf_utils.images import PdfImage
        from pyhanko.pdf_utils.layout import (
            SimpleBoxLayoutRule, AxisAlignment, Margins, InnerScaling
        )
        pil_img = PILImage.open(io.BytesIO(rubrica_bytes)).convert('RGBA')
        pdf_img = PdfImage(pil_img, opacity=1.0)
        return TextStampStyle(
            stamp_text=texto_firma,
            border_width=0,
            background=pdf_img,
            background_opacity=0.0,
            background_layout=SimpleBoxLayoutRule(
                x_align=AxisAlignment.ALIGN_MID,
                y_align=AxisAlignment.ALIGN_MAX,
                margins=Margins(left=2, right=2, top=2, bottom=0),
                inner_content_scaling=InnerScaling.SHRINK_TO_FIT,
            ),
            text_box_style=TextBoxStyle(
                font_size=font_size,
                text_color=(0.1, 0.23, 0.42),
            ),
        )
    else:
        return TextStampStyle(
            stamp_text=texto_firma,
            border_width=0,
            background_opacity=0.0,
            text_box_style=TextBoxStyle(font_size=font_size),
        )


def _firmar_pagina(writer, signer, nombre_campo, sello):
    """
    Firma una página con un event loop propio y executor de 1 hilo.
    Necesario porque la sesión PKCS#11 no es thread-safe y
    pyhanko usa asyncio internamente.
    """
    from pyhanko.sign import signers
    pdf_signer = signers.PdfSigner(
        signature_meta=signers.PdfSignatureMetadata(field_name=nombre_campo),
        signer=signer,
        stamp_style=sello,
    )
    out_buf  = io.BytesIO()
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    loop     = asyncio.new_event_loop()
    loop.set_default_executor(executor)
    try:
        loop.run_until_complete(pdf_signer.async_sign_pdf(writer, output=out_buf))
    finally:
        executor.shutdown(wait=False)
        loop.close()
    out_buf.seek(0)
    return out_buf


def firmar_pdf(
    pdf_bytes:     bytes,
    output_path:   str,
    coords:        dict,
    texto_firma:   str,
    font_size:     int         = 9,
    rubrica_bytes: bytes | None = None,
    dnie:          bool        = False,
    pkcs11_lib:    str         = DEFAULT_PKCS11_LIB,
    slot:          int         = 0,
    pin:           str         = '',
    cert_label:    str         = 'CertFirmaDigital',
    key_label:     str | None  = None,
    p12_path:      str | None  = None,
    p12_pass:      str | None  = None,
):
    """
    Firma el PDF en `pdf_bytes` en las páginas indicadas por `coords`.
    coords = {num_pagina: (x1, y1, x2, y2)} en puntos PDF.
    Escribe el resultado en `output_path`. Firmas incrementales.
    """
    from pyhanko.sign import signers, fields
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
    from pyhanko.sign.fields import SigFieldSpec

    if not coords:
        raise ValueError("No hay páginas con firma posicionada.")

    _cert_label = cert_label
    _key_label  = key_label or ('Kpriv' + _cert_label.replace('Cert', ''))

    session_ = None
    if dnie:
        import pkcs11 as _pkcs11
        from pyhanko.sign.pkcs11 import PKCS11Signer
        lib_    = _pkcs11.lib(pkcs11_lib)
        slots_  = lib_.get_slots(token_present=True)
        if not slots_:
            raise RuntimeError("DNIe no detectado. ¿Está insertado el lector?")
        token_   = slots_[slot].get_token()
        session_ = token_.open(user_pin=pin, rw=True)
        signer   = PKCS11Signer(
            pkcs11_session=session_,
            cert_label=_cert_label,
            key_label=_key_label,
            prefer_pss=False,
        )
    else:
        with open(p12_path, 'rb') as f:
            signer = signers.SimpleSigner.load_pkcs12(
                pfx_file=f,
                passphrase=p12_pass.encode() if p12_pass else None,
            )

    sello = _construir_sello(texto_firma, font_size, rubrica_bytes)
    buf   = io.BytesIO(pdf_bytes)

    try:
        for num_pagina in sorted(coords.keys()):
            x1, y1, x2, y2 = coords[num_pagina]
            x1, y1, x2, y2 = round(x1, 2), round(y1, 2), round(x2, 2), round(y2, 2)
            nombre_campo = f'Firma_pag{num_pagina}'

            buf.seek(0)
            writer = IncrementalPdfFileWriter(buf)
            fields.append_signature_field(writer, SigFieldSpec(
                sig_field_name=nombre_campo,
                on_page=num_pagina - 1,
                box=(x1, y1, x2, y2),
            ))

            out_buf = _firmar_pagina(writer, signer, nombre_campo, sello)
            datos   = out_buf.read()
            with open(output_path, 'wb') as f:
                f.write(datos)
            buf = io.BytesIO(datos)

    finally:
        if session_:
            try:
                session_.close()
            except Exception:
                pass


# ─────────────────────────────────────────────────────────────────────────────
# UTILIDADES
# ─────────────────────────────────────────────────────────────────────────────

def nombre_salida(input_path: str) -> str:
    base, ext = os.path.splitext(input_path)
    return base + '_firmado' + (ext or '.pdf')


def texto_firma_default(nombre: str = 'Firmante') -> str:
    fecha = datetime.now().strftime('%d/%m/%Y %H:%M')
    return f'Documento firmado digitalmente por:\n{nombre}\nFecha: {fecha}'
