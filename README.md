# rubrika

Firma digital de PDFs con el **DNIe español** (o certificado `.p12`), con interfaz nativa Qt/KDE.

**Sin Java. Sin navegador. Sin servidores externos.** Interfaz nativa que respeta el tema de tu escritorio.

> Con Java y AutoFirma la experiencia en Linux es... una aventura. Este proyecto nació exactamente de esa frustración.

Construido con Python, [pyhanko](https://pyhanko.readthedocs.io), [pymupdf](https://pymupdf.readthedocs.io) y [PySide6](https://doc.qt.io/qtforpython/).  
Desarrollado con la asistencia de [Claude Sonnet](https://claude.ai) (Anthropic).

---

## Características

- 🔏 Firma digital **PAdES** válida bajo el marco **eIDAS** — legalmente reconocida en España
- 📄 Firma **múltiples páginas** o todas a la vez con un solo clic
- 🖱️ **Posicionamiento visual** de la firma arrastrando el ratón sobre el PDF
- ✍️ Posibilidad de añadir una **rúbrica dibujada a mano** (con fondo transparente)
- 🎨 **Interfaz nativa Qt/KDE** — respeta Breeze, modo oscuro y el tema del sistema
- 🔗 Firmas incrementales: cada nueva firma preserva las anteriores
- ☕ **Sin Java** — porque ya sufrimos bastante

---

## Instalación en Arch / Manjaro

### Desde el AUR (recomendado)

```bash
pamac build rubrika
```

Esto instala automáticamente todas las dependencias, incluyendo `rubrika-certificates` con los certificados raíz del DNIe y la FNMT.

### Manual

```bash
# 1. Certificados del DNIe y la FNMT
git clone https://aur.archlinux.org/rubrika-certificates.git
cd rubrika-certificates && makepkg -si && cd ..

# 2. Aplicación
git clone https://aur.archlinux.org/rubrika.git
cd rubrika && makepkg -si
```

### Dependencias del sistema

```bash
# Drivers del lector de tarjeta
pamac install opensc ccid

# Activar el demonio PC/SC
sudo systemctl enable --now pcsclite
```

---

## Requisitos previos

### Hardware
- DNIe español (versión 3.0 o superior, con chip activo)
- Lector de tarjeta compatible con el estándar PC/SC (la mayoría de lectores USB lo son)

### Certificados del DNIe y la FNMT

El paquete `rubrika-certificates` los instala automáticamente.
Si prefieres instalarlos manualmente, descárgalos desde la fuente oficial:

👉 [https://www.sede.fnmt.gob.es/descargas/certificados-raiz-de-la-fnmt](https://www.sede.fnmt.gob.es/descargas/certificados-raiz-de-la-fnmt)

> Los paquetes AUR de certificados (`ca-certificates-dnie`, `ca-certificates-fnmt`) son conocidos
> por fallar con errores de checksum cuando la FNMT actualiza sus archivos sin avisar.
> `rubrika-certificates` resuelve esto verificando las huellas SHA-1 del certificado X.509
> directamente contra las publicadas en la web oficial, en lugar de los checksums del contenedor.

---

## Uso

```bash
rubrika
```

O búscalo en el menú de aplicaciones de KDE.

También funciona desde línea de comandos con opciones:

```bash
rubrika                          # DNIe (por defecto)
rubrika --p12 certificado.p12    # certificado .p12 / .pfx
rubrika --pkcs11-lib /ruta/lib   # librería PKCS#11 alternativa
rubrika --nombre "Ana García"    # nombre si no se lee del certificado
rubrika --font-size 8            # tamaño del texto de firma
```

### Librería PKCS#11 según distribución

| Distribución | Ruta |
|---|---|
| Arch / Manjaro | `/usr/lib/opensc-pkcs11.so` |
| Ubuntu / Debian | `/usr/lib/x86_64-linux-gnu/opensc-pkcs11.so` |
| Fedora | `/usr/lib64/opensc-pkcs11.so` |

---

## Verificar que el DNIe funciona

```bash
# El lector debe aparecer
opensc-tool --list-readers

# Con el DNIe insertado, los certificados deben ser visibles
pkcs11-tool --module /usr/lib/opensc-pkcs11.so --list-objects
```

---

## Desarrollo

```bash
git clone https://github.com/vrieraj/rubrika
cd rubrika
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

---

## Otras distribuciones

Por ahora el soporte oficial es Arch / Manjaro vía AUR.
La migración a Debian/Ubuntu y Fedora está en el roadmap.
Si quieres colaborar, abre un issue o un PR.

---

## Seguridad

- La interfaz es completamente **local** — no abre puertos ni conexiones de red
- El PDF nunca sale del equipo
- El PIN del DNIe se usa directamente para abrir la sesión PKCS#11 y no se almacena
- La firma generada es **PAdES**, válida legalmente en España bajo **eIDAS**

---

## Licencia

[GNU General Public License v3.0](LICENSE)

Este software es libre: puedes redistribuirlo y/o modificarlo bajo los términos de la GNU GPL v3 o posterior.
