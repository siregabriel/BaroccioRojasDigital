# Baroccio Rojas Digital — Portal Ejecutivo

Aplicación web para clientes de un despacho de abogados, construida en **Python + Flask** con base de datos **SQLite**. Interfaz completamente en español.

La app tiene **dos perfiles** según el rol del usuario; el mismo login redirige a cada uno:

### Portal del Cliente
- **Mis Casos** — listado y detalle de los asuntos legales del cliente.
- **Documentos** — acceso y descarga real de documentos por caso.
- **Mensajes** — mensajería con el equipo legal (envío funcional).
- **Facturación y Pagos** — resumen de cuenta, métodos de pago y facturas, con pago de facturas funcional.

### Panel de Administración (Abogados) — `/admin`
- **Resumen** — estadísticas: clientes, casos activos, facturas por cobrar, mensajes sin responder.
- **Casos** — crear, editar, cambiar estado y eliminar casos de cualquier cliente.
- **Documentos** — subida real de archivos (PDF, Word, Excel, imágenes…) asignados a cliente/caso, y borrado.
- **Mensajes** — bandeja por cliente; responder desde el despacho (los hilos sin responder aparecen primero).
- **Facturas** — emitir facturas, cambiar estado (Pendiente/Pagado/Atrasado), ver saldos.
- **Clientes** — dar de alta nuevos clientes con su contraseña inicial y ver su ficha completa.

## Puesta en marcha

```bash
# 1. (Opcional) entorno virtual
python3 -m venv venv && source venv/bin/activate

# 2. Instalar dependencias
pip install -r requirements.txt

# 3. Crear el esquema y poblar la base de datos (solo la primera vez)
python -m flask db upgrade   # crea las tablas (FLASK_APP=app.py)
python seed.py               # carga datos de ejemplo (NO borra si ya hay datos)

# 4. Arrancar el servidor
python app.py
```

Luego abrir http://localhost:5000

### Datos: cómo NO perderlos

- `python seed.py` → uso normal: **no borra nada**, solo siembra si la base está vacía.
- `python seed.py --reset` → **borra TODO** y recarga ejemplos (pide confirmación escribiendo `BORRAR`).
- Cuando cambie el modelo (nuevas columnas/tablas), aplica el cambio **sin perder datos** con:
  `python -m flask db upgrade`
- En **producción** el despliegue solo ejecuta `flask db upgrade` (nunca `seed.py`), así que los datos reales de los clientes están siempre a salvo.

## Accesos de demostración

**Abogados (panel de administración)** — cada uno ve solo sus propios clientes, casos, documentos, mensajes y facturas. Contraseña `admin1234` para los tres:
- `abogado@grupolegal.mx` — Lic. Ana Beltrán (cliente: Carlos Mendoza)
- `roberto@grupolegal.mx` — Lic. Roberto Fuentes (cliente: Sofía Vega)
- `marta@grupolegal.mx` — Lic. Marta Quintero (cliente: Diego Herrera)

**Clientes (portal):**
- `cliente@grupolegal.mx` / `demo1234` (Carlos Mendoza)
- `sofia@cliente.mx` / `demo1234` (Sofía Vega)
- `diego@cliente.mx` / `demo1234` (Diego Herrera)

> Cada cliente tiene un **abogado responsable**. Un abogado puede reasignar un cliente a otro desde la ficha del cliente; al hacerlo, ese cliente deja de aparecer en su panel.

## Despliegue en producción (AWS Lightsail + dominio)

La app está lista para producción en **AWS Lightsail** (Ubuntu) con **PostgreSQL**,
**Gunicorn**, **Nginx**, **SSL** y **despliegue automático** (cada `git push` actualiza el sitio).

👉 Sigue la guía paso a paso en **[DEPLOY.md](DEPLOY.md)**.

En producción el esquema se gestiona con **migraciones** (`flask db upgrade`), no con
auto-creación. Si cambias `models.py`, genera la migración antes del push:

```bash
export FLASK_APP=app.py
python -m flask db migrate -m "describe el cambio"
python -m flask db upgrade
```

## Estructura

```
app.py            App Flask: rutas, login, filtros, comando "flask seed"
wsgi.py           Punto de entrada para Gunicorn (producción)
models.py         Modelos SQLAlchemy (Usuario, Caso, Documento, Mensaje, Factura, MetodoPago, Notificacion, Cita)
sample_data.py    Datos de ejemplo (usado por el auto-seed y por seed.py)
seed.py           Reinicia y repuebla la base de datos local
migrations/       Migraciones de esquema (Flask-Migrate / Alembic)
templates/        Plantillas Jinja2 (cliente + admin)
static/css/       Hoja de estilos (diseño azul marino / serif)
deploy/           Configs del VPS: systemd, nginx, deploy.sh
.github/workflows/ Auto-deploy con GitHub Actions
requirements.txt  Dependencias
.env.example      Plantilla de variables de entorno
```

## Notas

- Las contraseñas se almacenan con hash (Werkzeug).
- Las acciones de "Realizar Pago" y "Pagar" actualizan el estado de la factura en la base de datos.
- Botones como "Añadir Nuevo" método de pago y "Descargar" documento son marcadores listos para conectar a la lógica real.
- Cambia `SECRET_KEY` por una variable de entorno antes de desplegar en producción.
```
# BaroccioRojasDigital
