#!/usr/bin/env python3
"""
rubrika — Firma digital de PDFs con DNIe o .p12
Interfaz nativa Qt/KDE con PySide6.
Uso: python main.py [--p12 cert.p12] [--pkcs11-lib /ruta/lib.so]
"""

import argparse
import io
import os
import sys

import numpy as np
from PIL import Image as PILImage
from PySide6.QtCore import (QPoint, QRect, QSize, QThread, Qt, Signal)
from PySide6.QtGui import (QColor, QCursor, QPainter, QPen, QPixmap)
from PySide6.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QFileDialog,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMainWindow, QMessageBox, QPushButton, QRubberBand,
    QScrollArea, QSizePolicy, QStatusBar, QVBoxLayout, QWidget,
)

import utils


# ─────────────────────────────────────────────────────────────────────────────
# HILO DE FIRMA (no bloquea la UI)
# ─────────────────────────────────────────────────────────────────────────────

class HiloFirma(QThread):
    terminado = Signal(bool, str)   # (ok, mensaje)

    def __init__(self, **kwargs):
        super().__init__()
        self.kwargs = kwargs

    def run(self):
        try:
            utils.firmar_pdf(**self.kwargs)
            n      = len(self.kwargs['coords'])
            paginas = f'{n} página{"s" if n != 1 else ""}'
            self.terminado.emit(True, f'PDF firmado en {paginas}.')
        except Exception as e:
            self.terminado.emit(False, str(e))


# ─────────────────────────────────────────────────────────────────────────────
# VISOR DE PÁGINA con rubber band
# ─────────────────────────────────────────────────────────────────────────────

class VisorPagina(QLabel):
    """
    QLabel que muestra una página PDF y permite dibujar
    un rectángulo de selección con el ratón (rubber band nativo de Qt).
    Emite rectSeleccionado con las coordenadas en píxeles del label.
    """
    rectSeleccionado = Signal(QRect)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.setCursor(QCursor(Qt.CursorShape.CrossCursor))
        self.rubberband = QRubberBand(QRubberBand.Shape.Rectangle, self)
        self._origen    = QPoint()
        self._dibujando = False

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._origen    = event.pos()
            self._dibujando = True
            self.rubberband.setGeometry(QRect(self._origen, QSize()))
            self.rubberband.show()

    def mouseMoveEvent(self, event):
        if self._dibujando:
            self.rubberband.setGeometry(
                QRect(self._origen, event.pos()).normalized()
            )

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._dibujando:
            self._dibujando = False
            rect = QRect(self._origen, event.pos()).normalized()
            if rect.width() > 10 and rect.height() > 10:
                self.rectSeleccionado.emit(rect)
            else:
                self.rubberband.hide()

    def limpiar(self):
        self.rubberband.hide()

    def restaurar_rect(self, rect: QRect | None):
        """Muestra un rect guardado al volver a una página."""
        if rect:
            self.rubberband.setGeometry(rect)
            self.rubberband.show()
        else:
            self.rubberband.hide()


# ─────────────────────────────────────────────────────────────────────────────
# CANVAS DE RÚBRICA
# ─────────────────────────────────────────────────────────────────────────────

class CanvasRubrica(QWidget):
    """
    Widget de dibujo a mano. Los trazos se pintan en azul marino (#1a3a6b).
    exportar_png() devuelve PNG con fondo transparente listo para pyhanko.
    """
    ANCHO  = 500
    ALTO   = 150
    COLOR  = QColor(26, 58, 107)   # azul marino
    GROSOR = 3

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(self.ANCHO, self.ALTO)
        self.setCursor(QCursor(Qt.CursorShape.CrossCursor))
        self._trazos      = []          # [[QPoint, ...], ...]
        self._trazo_actual = None

    # ── Pintura ──────────────────────────────────────────────────────────────
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), Qt.GlobalColor.white)
        pen = QPen(self.COLOR, self.GROSOR,
                   Qt.PenStyle.SolidLine,
                   Qt.PenCapStyle.RoundCap,
                   Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        todos = self._trazos + ([self._trazo_actual] if self._trazo_actual else [])
        for trazo in todos:
            for i in range(1, len(trazo)):
                p.drawLine(trazo[i - 1], trazo[i])

    # ── Ratón ─────────────────────────────────────────────────────────────────
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._trazo_actual = [event.pos()]

    def mouseMoveEvent(self, event):
        if self._trazo_actual is not None:
            self._trazo_actual.append(event.pos())
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._trazo_actual:
            self._trazos.append(self._trazo_actual)
            self._trazo_actual = None

    # ── Utilidades ────────────────────────────────────────────────────────────
    def limpiar(self):
        self._trazos       = []
        self._trazo_actual = None
        self.update()

    def esta_vacio(self) -> bool:
        return len(self._trazos) == 0

    def exportar_png(self) -> bytes:
        """
        Exporta la rúbrica como PNG con fondo transparente.
        Trazos oscuros → azul marino (#1a3a6b), fondo blanco → transparente.
        Usa numpy para la conversión pixel a pixel.
        """
        # Capturar el widget como pixmap
        pixmap = self.grab()
        buf    = io.BytesIO()
        ba     = pixmap.toImage()

        # Convertir QImage → bytes PNG → PIL
        # Serializar el pixmap a PNG via QBuffer (en memoria, sin disco)
        from PySide6.QtCore import QBuffer, QIODevice
        qbuf = QBuffer()
        qbuf.open(QIODevice.OpenModeFlag.WriteOnly)
        pixmap.save(qbuf, 'PNG')
        png_bytes = bytes(qbuf.data())
        qbuf.close()

        # Recolorear con numpy: oscuro → azul marino, claro → transparente
        img = PILImage.open(io.BytesIO(png_bytes)).convert('RGBA')
        arr = np.array(img)
        lum = arr[:, :, :3].mean(axis=2)
        mask_trazo       = lum < 220
        arr[mask_trazo]  = [26, 58, 107, 255]   # azul marino opaco
        arr[~mask_trazo] = [0, 0, 0, 0]          # transparente
        resultado = PILImage.fromarray(arr)

        out = io.BytesIO()
        resultado.save(out, 'PNG')
        return out.getvalue()


# ─────────────────────────────────────────────────────────────────────────────
# DIÁLOGO: RÚBRICA
# ─────────────────────────────────────────────────────────────────────────────

class DialogoRubrica(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Rúbrica (opcional)')
        self.setModal(True)
        self.rubrica_bytes = None   # resultado: None = sin rúbrica

        self.canvas = CanvasRubrica()

        btn_limpiar = QPushButton('Limpiar')
        btn_sin     = QPushButton('Sin rúbrica')
        btn_ok      = QPushButton('Usar esta rúbrica')
        btn_ok.setDefault(True)

        btn_limpiar.clicked.connect(self.canvas.limpiar)
        btn_sin.clicked.connect(self._sin_rubrica)
        btn_ok.clicked.connect(self._confirmar)

        info = QLabel(
            'Dibuja tu firma con el ratón. Se añadirá como imagen con fondo '
            'transparente encima del texto de firma.'
        )
        info.setWordWrap(True)

        barra = QHBoxLayout()
        barra.addWidget(btn_limpiar)
        barra.addStretch()
        barra.addWidget(btn_sin)
        barra.addWidget(btn_ok)

        layout = QVBoxLayout(self)
        layout.addWidget(info)
        layout.addWidget(self.canvas)
        layout.addLayout(barra)

    def _sin_rubrica(self):
        self.rubrica_bytes = None
        self.accept()

    def _confirmar(self):
        if self.canvas.esta_vacio():
            QMessageBox.warning(self, 'Rúbrica vacía',
                                'Dibuja tu rúbrica o pulsa "Sin rúbrica".')
            return
        self.rubrica_bytes = self.canvas.exportar_png()
        self.accept()


# ─────────────────────────────────────────────────────────────────────────────
# DIÁLOGO: AUTENTICACIÓN (cert + PIN)
# ─────────────────────────────────────────────────────────────────────────────

class DialogoAuth(QDialog):
    def __init__(self, certs: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Certificado y PIN del DNIe')
        self.setModal(True)
        self.cert_elegido = None
        self.pin_valor    = ''

        # Lista de certificados
        self.lista = QListWidget()
        for cert in certs:
            item = QListWidgetItem(f"🔏 {cert['label']}  —  {cert['subject']}")
            item.setData(Qt.ItemDataRole.UserRole, cert)
            self.lista.addItem(item)
        if self.lista.count():
            self.lista.setCurrentRow(0)

        info_cert = QLabel(
            'Para firmar documentos selecciona <b>CertFirmaDigital</b>.'
        )

        # PIN
        self.pin_edit = QLineEdit()
        self.pin_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.pin_edit.setPlaceholderText('PIN del DNIe')
        self.pin_edit.setMaxLength(16)
        self.pin_edit.returnPressed.connect(self._firmar)

        info_pin = QLabel('El PIN no sale del equipo — conexión local únicamente.')

        btn_firmar = QPushButton('✍  Firmar PDF')
        btn_firmar.setDefault(True)
        btn_firmar.clicked.connect(self._firmar)

        btn_cancelar = QPushButton('Cancelar')
        btn_cancelar.clicked.connect(self.reject)

        barra = QHBoxLayout()
        barra.addStretch()
        barra.addWidget(btn_cancelar)
        barra.addWidget(btn_firmar)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel('<b>Selecciona el certificado:</b>'))
        layout.addWidget(info_cert)
        layout.addWidget(self.lista)
        layout.addSpacing(8)
        layout.addWidget(QLabel('<b>PIN:</b>'))
        layout.addWidget(self.pin_edit)
        layout.addWidget(info_pin)
        layout.addLayout(barra)

        self.resize(480, 320)
        self.pin_edit.setFocus()

    def _firmar(self):
        if not self.lista.currentItem():
            QMessageBox.warning(self, 'Sin certificado',
                                'Selecciona un certificado.')
            return
        if not self.pin_edit.text():
            QMessageBox.warning(self, 'PIN vacío', 'Introduce el PIN del DNIe.')
            return
        self.cert_elegido = self.lista.currentItem().data(
            Qt.ItemDataRole.UserRole
        )
        self.pin_valor = self.pin_edit.text()
        self.accept()


# ─────────────────────────────────────────────────────────────────────────────
# VENTANA PRINCIPAL
# ─────────────────────────────────────────────────────────────────────────────

class VentanaPrincipal(QMainWindow):

    def __init__(self, args):
        super().__init__()
        self.args          = args
        self.pdf_bytes     = None
        self.pdf_path      = None
        self.output_path   = None
        self.total_paginas = 0
        self.paginas_info  = []       # [(w_pts, h_pts), ...]
        self.pagina_actual = 1
        self.coords        = {}       # {num: (x1,y1,x2,y2)} en puntos PDF
        self.rects_display = {}       # {num: QRect} en píxeles display
        self.pixmap_actual = None     # QPixmap escalado mostrado
        self.hilo_firma    = None

        self.setWindowTitle('rubrika — Firma digital de PDF')
        self.resize(900, 700)
        self._init_ui()

    # ── Construcción de la UI ─────────────────────────────────────────────────
    def _init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        self.layout_principal = QVBoxLayout(central)

        # ── Barra de carga ──
        self.barra_carga = QWidget()
        lay_carga = QVBoxLayout(self.barra_carga)
        lay_carga.setAlignment(Qt.AlignmentFlag.AlignCenter)

        lbl_bienvenida = QLabel('<h2>rubrika</h2><p>Firma digital de PDFs con DNIe</p>')
        lbl_bienvenida.setAlignment(Qt.AlignmentFlag.AlignCenter)
        btn_abrir = QPushButton('📂  Abrir PDF...')
        btn_abrir.setFixedWidth(200)
        btn_abrir.clicked.connect(self.abrir_pdf)

        lay_carga.addWidget(lbl_bienvenida)
        lay_carga.addSpacing(16)
        lay_carga.addWidget(btn_abrir, alignment=Qt.AlignmentFlag.AlignCenter)
        self.layout_principal.addWidget(self.barra_carga)

        # ── Área de visor (oculta hasta cargar PDF) ──
        self.area_visor = QWidget()
        self.area_visor.hide()
        lay_visor = QVBoxLayout(self.area_visor)
        lay_visor.setContentsMargins(0, 0, 0, 0)

        # Info página + texto firma
        self.lbl_pagina = QLabel()
        self.lbl_texto  = QLabel()
        self.lbl_texto.setStyleSheet('color: palette(mid);')
        barra_info = QHBoxLayout()
        barra_info.addWidget(self.lbl_pagina)
        barra_info.addStretch()
        barra_info.addWidget(self.lbl_texto)

        # Scroll area con el visor
        self.visor = VisorPagina()
        self.visor.rectSeleccionado.connect(self._rect_seleccionado)
        scroll = QScrollArea()
        scroll.setWidget(self.visor)
        scroll.setWidgetResizable(False)
        scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Botones de navegación
        self.btn_anterior  = QPushButton('◀  Anterior')
        self.btn_limpiar   = QPushButton('✕  Limpiar')
        self.btn_omitir    = QPushButton('Omitir página')
        self.btn_confirmar = QPushButton('✔  Confirmar y siguiente')
        self.btn_todas     = QPushButton('⬛  Todas las páginas')

        self.btn_confirmar.setEnabled(False)
        self.btn_todas.setEnabled(False)

        self.btn_anterior.clicked.connect(self.pagina_anterior)
        self.btn_limpiar.clicked.connect(self._limpiar_rect)
        self.btn_omitir.clicked.connect(self.omitir_pagina)
        self.btn_confirmar.clicked.connect(self.confirmar_pagina)
        self.btn_todas.clicked.connect(self.confirmar_todas)

        barra_btns = QHBoxLayout()
        for btn in (self.btn_anterior, self.btn_limpiar,
                    self.btn_omitir, self.btn_confirmar, self.btn_todas):
            barra_btns.addWidget(btn)

        # Resumen páginas confirmadas
        self.lbl_resumen = QLabel()
        self.lbl_resumen.hide()

        lay_visor.addLayout(barra_info)
        lay_visor.addWidget(scroll, stretch=1)
        lay_visor.addLayout(barra_btns)
        lay_visor.addWidget(self.lbl_resumen)

        self.layout_principal.addWidget(self.area_visor)

        # Barra de estado
        self.setStatusBar(QStatusBar())

    # ── Abrir PDF ─────────────────────────────────────────────────────────────
    def abrir_pdf(self):
        ruta, _ = QFileDialog.getOpenFileName(
            self, 'Selecciona un PDF', '', 'PDF (*.pdf)'
        )
        if not ruta:
            return
        try:
            with open(ruta, 'rb') as f:
                self.pdf_bytes = f.read()
            self.pdf_path    = ruta
            self.output_path = utils.nombre_salida(ruta)
            self.total_paginas, self.paginas_info = utils.info_paginas(
                self.pdf_bytes
            )
            self.coords        = {}
            self.rects_display = {}
            self.pagina_actual = 1

            nombre   = args.nombre or 'Firmante'
            self.texto_firma = utils.texto_firma_default(nombre)
            self.lbl_texto.setText(f'Texto: {self.texto_firma.splitlines()[0]}…')

            self.barra_carga.hide()
            self.area_visor.show()
            self.mostrar_pagina(1)
            self.statusBar().showMessage(
                f'{os.path.basename(ruta)}  →  {os.path.basename(self.output_path)}'
            )
        except Exception as e:
            QMessageBox.critical(self, 'Error al abrir', str(e))

    # ── Renderizado de página ─────────────────────────────────────────────────
    def mostrar_pagina(self, n: int):
        self.pagina_actual = n
        png  = utils.renderizar_pagina(self.pdf_bytes, n, dpi=150)
        pmap = QPixmap()
        pmap.loadFromData(png)

        # Escalar al ancho disponible (sin distorsionar)
        ancho_max = min(pmap.width(), self.width() - 40)
        if pmap.width() > ancho_max:
            pmap = pmap.scaledToWidth(
                ancho_max, Qt.TransformationMode.SmoothTransformation
            )

        self.pixmap_actual = pmap
        self.visor.setPixmap(pmap)
        self.visor.setFixedSize(pmap.size())

        self.lbl_pagina.setText(
            f'Página {n} / {self.total_paginas}'
        )
        self.visor.restaurar_rect(self.rects_display.get(n))
        self._actualizar_botones()

    # ── Rubber band → coordenadas PDF ─────────────────────────────────────────
    def _rect_seleccionado(self, rect: QRect):
        self.rects_display[self.pagina_actual] = rect
        self._actualizar_botones()

    def _rect_a_pdf(self, rect: QRect, num_pagina: int):
        """
        Convierte QRect en píxeles display a coordenadas PDF en puntos.
        Origen PDF: esquina inferior izquierda (Y invertida).
        """
        pmap        = self.pixmap_actual
        pdf_w, pdf_h = self.paginas_info[num_pagina - 1]
        sx = pdf_w / pmap.width()
        sy = pdf_h / pmap.height()

        x1 = rect.left()   * sx
        x2 = rect.right()  * sx
        # Invertir Y: PDF origin = bottom-left
        y1 = pdf_h - rect.bottom() * sy
        y2 = pdf_h - rect.top()    * sy
        return (x1, y1, x2, y2)

    def _limpiar_rect(self):
        self.rects_display.pop(self.pagina_actual, None)
        self.coords.pop(self.pagina_actual, None)
        self.visor.limpiar()
        self._actualizar_botones()
        self._actualizar_resumen()

    # ── Navegación y confirmación ─────────────────────────────────────────────
    def confirmar_pagina(self):
        rect = self.rects_display.get(self.pagina_actual)
        if rect:
            self.coords[self.pagina_actual] = self._rect_a_pdf(
                rect, self.pagina_actual
            )
        self._actualizar_resumen()
        self._avanzar()

    def omitir_pagina(self):
        self.coords.pop(self.pagina_actual, None)
        self._avanzar()

    def confirmar_todas(self):
        rect = self.rects_display.get(self.pagina_actual)
        if not rect:
            return
        for p in range(1, self.total_paginas + 1):
            self.rects_display[p] = rect
            self.coords[p]        = self._rect_a_pdf(rect, p)
        self._actualizar_resumen()
        self._iniciar_flujo_firma()

    def pagina_anterior(self):
        if self.pagina_actual > 1:
            self.mostrar_pagina(self.pagina_actual - 1)

    def _avanzar(self):
        if self.pagina_actual < self.total_paginas:
            self.mostrar_pagina(self.pagina_actual + 1)
        else:
            self._iniciar_flujo_firma()

    def _actualizar_botones(self):
        tiene_rect = self.pagina_actual in self.rects_display
        self.btn_confirmar.setEnabled(tiene_rect)
        self.btn_todas.setEnabled(tiene_rect)
        self.btn_anterior.setEnabled(self.pagina_actual > 1)

    def _actualizar_resumen(self):
        paginas = sorted(self.coords.keys())
        if paginas:
            txt = '✔  Páginas con firma: ' + ', '.join(
                f'pág. {p}' for p in paginas
            )
            self.lbl_resumen.setText(txt)
            self.lbl_resumen.show()
        else:
            self.lbl_resumen.hide()

    # ── Flujo de firma ────────────────────────────────────────────────────────
    def _iniciar_flujo_firma(self):
        if not self.coords:
            QMessageBox.warning(self, 'Sin páginas',
                                'No has posicionado ninguna firma.')
            return

        # Paso 1: rúbrica
        dlg = DialogoRubrica(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        rubrica_bytes = dlg.rubrica_bytes

        # Paso 2: autenticación
        if self.args.dnie:
            try:
                certs = utils.listar_certificados(self.args.pkcs11_lib)
            except Exception as e:
                QMessageBox.critical(self, 'DNIe no detectado', str(e))
                return

            dlg_auth = DialogoAuth(certs, self)
            if dlg_auth.exec() != QDialog.DialogCode.Accepted:
                return

            cert    = dlg_auth.cert_elegido
            pin     = dlg_auth.pin_valor
            nombre  = cert.get('nombre') or self.args.nombre or 'Firmante'
        else:
            cert   = None
            pin    = self.args.password or ''
            nombre = self.args.nombre or 'Firmante'

        # Sustituir nombre en el texto si procede
        texto = self.texto_firma.replace('Firmante', nombre)

        # Paso 3: firmar en hilo separado
        self.setEnabled(False)
        self.statusBar().showMessage('Firmando, por favor espera…')

        kwargs = dict(
            pdf_bytes     = self.pdf_bytes,
            output_path   = self.output_path,
            coords        = dict(self.coords),
            texto_firma   = texto,
            font_size     = self.args.font_size,
            rubrica_bytes = rubrica_bytes,
            dnie          = self.args.dnie,
            pkcs11_lib    = self.args.pkcs11_lib,
            pin           = pin,
            p12_path      = self.args.p12,
            p12_pass      = self.args.password,
        )
        if self.args.dnie and cert:
            kwargs['cert_label'] = cert['label']
            kwargs['key_label']  = cert['key_label']

        self.hilo_firma = HiloFirma(**kwargs)
        self.hilo_firma.terminado.connect(self._firma_terminada)
        self.hilo_firma.start()

    def _firma_terminada(self, ok: bool, mensaje: str):
        self.setEnabled(True)
        if ok:
            self.statusBar().showMessage('Firmado correctamente.')
            res = QMessageBox.information(
                self, '✅ Firmado',
                f'{mensaje}\n\nGuardado en:\n{self.output_path}',
                QMessageBox.StandardButton.Open |
                QMessageBox.StandardButton.Ok,
                QMessageBox.StandardButton.Ok,
            )
            if res == QMessageBox.StandardButton.Open:
                import subprocess
                subprocess.Popen(['xdg-open', self.output_path])
        else:
            self.statusBar().showMessage('Error al firmar.')
            QMessageBox.critical(self, '❌ Error al firmar', mensaje)


# ─────────────────────────────────────────────────────────────────────────────
# ARRANQUE
# ─────────────────────────────────────────────────────────────────────────────

def main():
    global args

    parser = argparse.ArgumentParser(
        description='rubrika — Firma PDFs con DNIe o .p12'
    )
    g = parser.add_mutually_exclusive_group()
    g.add_argument('--dnie', action='store_true', default=True,
                   help='Usar DNIe vía PKCS#11 (por defecto)')
    g.add_argument('--p12',  metavar='ARCHIVO',
                   help='Certificado .p12 / .pfx')
    parser.add_argument('--pkcs11-lib', default=utils.DEFAULT_PKCS11_LIB,
                        dest='pkcs11_lib', metavar='RUTA')
    parser.add_argument('--slot',      type=int, default=0)
    parser.add_argument('--password',  metavar='PASS')
    parser.add_argument('--nombre',    metavar='NOMBRE')
    parser.add_argument('--font-size', type=int, default=9,
                        dest='font_size')
    args = parser.parse_args()

    if args.p12:
        args.dnie = False

    # Verificar dependencias críticas
    try:
        import fitz       # noqa
    except ImportError:
        print('❌  Falta pymupdf:  pip install pymupdf')
        sys.exit(1)
    try:
        from pyhanko.sign import signers  # noqa
    except ImportError:
        print('❌  Falta pyhanko:  pip install pyhanko')
        sys.exit(1)

    app = QApplication(sys.argv)
    app.setApplicationName('rubrika')
    app.setOrganizationName('rubrika')

    # Tema y paleta: usa siempre lo que tenga configurado el sistema.
    # En KDE respeta Breeze, modo oscuro, Kvantum, etc. sin intervención.

    ventana = VentanaPrincipal(args)
    ventana.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
