"""Portal Ejecutivo - Baroccio Rojas Digital
Aplicación web en Flask con dos perfiles:
  - CLIENTE: ve sus casos, documentos, mensajes y facturas.
  - ABOGADO (admin): gestiona casos, sube documentos, responde mensajes,
    emite facturas y da de alta clientes.
"""
import os
import uuid
from functools import wraps
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
import io
import sys
from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, abort, g, send_from_directory, send_file
)
from flask_migrate import Migrate
from flask_wtf import CSRFProtect
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from werkzeug.utils import secure_filename
from werkzeug.middleware.proxy_fix import ProxyFix

from sqlalchemy import func
from models import (db, Usuario, Caso, Documento, Mensaje, Factura, MetodoPago,
                    Notificacion, Cita, EventoCaso)

load_dotenv()  # carga variables desde un archivo .env si existe (desarrollo)

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
# Zona horaria de la aplicación (México por defecto). Independiente del servidor.
try:
    APP_TZ = ZoneInfo(os.environ.get("APP_TZ", "America/Mexico_City"))
except Exception:
    APP_TZ = timezone.utc
# Carpeta de subidas configurable (en el VPS conviene un volumen fuera del repo)
UPLOAD_DIR = os.environ.get("UPLOAD_DIR", os.path.join(BASE_DIR, "uploads"))
migrate = Migrate()
csrf = CSRFProtect()
EXT_PERMITIDAS = {"pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx",
                  "png", "jpg", "jpeg", "txt", "csv", "zip"}
TIPO_POR_EXT = {"pdf": "PDF", "doc": "DOC", "docx": "DOCX", "xls": "XLS",
                "xlsx": "XLS", "ppt": "PPT", "pptx": "PPT", "png": "IMG",
                "jpg": "IMG", "jpeg": "IMG", "txt": "TXT", "csv": "CSV", "zip": "ZIP"}


# ----------------- Decoradores de acceso -----------------
def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not g.get("usuario"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper


def cliente_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not g.get("usuario"):
            return redirect(url_for("login"))
        if g.usuario.es_abogado:
            return redirect(url_for("admin_dashboard"))
        return f(*args, **kwargs)
    return wrapper


def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not g.get("usuario"):
            return redirect(url_for("login"))
        if not g.usuario.es_abogado:
            abort(403)
        return f(*args, **kwargs)
    return wrapper


def _ids_mis_clientes(abogado):
    """IDs de los clientes asignados al abogado en sesión."""
    return [c.id for c in Usuario.query.filter_by(rol="cliente", abogado_id=abogado.id).all()]


def _cliente_mio_o_404(cid):
    """Devuelve el cliente si pertenece al abogado en sesión; si no, 403/404."""
    cliente = Usuario.query.filter_by(id=cid, rol="cliente").first()
    if not cliente:
        abort(404)
    if cliente.abogado_id != g.usuario.id:
        abort(403)
    return cliente


def _comprobante_pdf(factura):
    """Genera en memoria un PDF de comprobante de pago para una factura."""
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import cm
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    w, h = letter
    navy = (0.086, 0.137, 0.247)

    logo = os.path.join(BASE_DIR, "static", "baroccio-rojas-logo.png")
    if os.path.exists(logo):
        try:
            c.drawImage(logo, 2 * cm, h - 4.2 * cm, width=2.6 * cm, height=2.6 * cm,
                        mask="auto", preserveAspectRatio=True)
        except Exception:
            pass

    c.setFillColorRGB(*navy)
    c.setFont("Helvetica-Bold", 17)
    c.drawString(5 * cm, h - 2.6 * cm, "Baroccio, Rojas & Co.")
    c.setFont("Helvetica", 10)
    c.setFillColorRGB(0.4, 0.43, 0.48)
    c.drawString(5 * cm, h - 3.2 * cm, "Comprobante de Pago")

    c.setStrokeColorRGB(0.9, 0.89, 0.86)
    c.line(2 * cm, h - 4.8 * cm, w - 2 * cm, h - 4.8 * cm)

    mx = ZoneInfo("America/Mexico_City")
    pagada = factura.pagada_en.replace(tzinfo=timezone.utc).astimezone(mx) if factura.pagada_en else None
    filas = [
        ("Factura", factura.referencia),
        ("Cliente", factura.cliente.nombre),
        ("Descripción", factura.descripcion or "—"),
        ("Monto pagado", "${:,.2f} USD".format(factura.monto)),
        ("Método de pago", factura.metodo_pago.descripcion if factura.metodo_pago else "—"),
        ("Fecha de pago", pagada.strftime("%d/%m/%Y %H:%M") if pagada else "—"),
        ("Estado", "PAGADO"),
    ]
    y = h - 6 * cm
    for etiqueta, valor in filas:
        c.setFont("Helvetica", 10)
        c.setFillColorRGB(0.4, 0.43, 0.48)
        c.drawString(2 * cm, y, etiqueta)
        c.setFont("Helvetica-Bold", 11)
        c.setFillColorRGB(0.12, 0.15, 0.2)
        c.drawString(7 * cm, y, str(valor))
        y -= 0.9 * cm

    c.setFont("Helvetica-Oblique", 8)
    c.setFillColorRGB(0.55, 0.57, 0.6)
    c.drawString(2 * cm, 2 * cm, "Este comprobante es un acuse de pago generado por el portal del despacho.")
    c.showPage()
    c.save()
    buf.seek(0)
    return buf


def _enviar_correo(destino, asunto, cuerpo):
    """Envía un correo por SMTP si está configurado; si no, lo registra en consola
    (útil en pruebas: ahí verás el enlace de recuperación). Variables de entorno:
    SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, SMTP_FROM."""
    host = os.environ.get("SMTP_HOST")
    if not host:
        print(f"[CORREO no enviado · SMTP sin configurar]\nPara: {destino}\nAsunto: {asunto}\n{cuerpo}\n",
              file=sys.stderr)
        return False
    import smtplib
    from email.message import EmailMessage
    msg = EmailMessage()
    msg["Subject"] = asunto
    msg["From"] = os.environ.get("SMTP_FROM", os.environ.get("SMTP_USER", "no-reply@localhost"))
    msg["To"] = destino
    msg.set_content(cuerpo)
    try:
        with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", 587))) as s:
            s.starttls()
            if os.environ.get("SMTP_USER"):
                s.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASS", ""))
            s.send_message(msg)
        return True
    except Exception as e:  # pragma: no cover
        print(f"[CORREO error] {e}", file=sys.stderr)
        return False


def _saludo_por_hora():
    """Devuelve el saludo adecuado según la hora actual (zona horaria de México)."""
    h = datetime.now(APP_TZ).hour
    if 5 <= h < 12:
        return "Buen día"
    if 12 <= h < 19:
        return "Buenas tardes"
    return "Buenas noches"


def _parent_valido(raw_id, usuario_id):
    """Devuelve el id del mensaje a responder solo si existe y pertenece al hilo
    de ese cliente; si no, None."""
    if not raw_id:
        return None
    try:
        pid = int(raw_id)
    except (TypeError, ValueError):
        return None
    padre = db.session.get(Mensaje, pid)
    if padre and padre.usuario_id == usuario_id:
        return pid
    return None


def _notificar(cliente_id, seccion, mensaje):
    """Crea una notificación para un cliente. NO hace commit (lo hace la ruta)."""
    db.session.add(Notificacion(usuario_id=cliente_id, seccion=seccion, mensaje=mensaje, leido=False))


def _avisar_cliente(cliente_id, seccion, resumen, asunto):
    """Notificación in-app (badge) + correo al cliente (si el SMTP está configurado).
    Se llama dentro de una petición; el correo se envía con _enviar_correo."""
    _notificar(cliente_id, seccion, resumen)
    cli = db.session.get(Usuario, cliente_id)
    if cli and cli.email:
        try:
            enlace = url_for("login", _external=True)
        except Exception:
            enlace = os.environ.get("PORTAL_URL", "https://barocciorojasdigital.com")
        cuerpo = (f"Estimado(a) {cli.nombre}:\n\n{resumen}.\n\n"
                  f"Ingrese a su portal para ver los detalles:\n{enlace}\n\n"
                  f"Atentamente,\nBaroccio, Rojas & Co. — Grupo Legal\n"
                  f"(Este es un mensaje automático, por favor no responda a este correo.)")
        _enviar_correo(cli.email, asunto, cuerpo)


def _marcar_seccion_vista(seccion):
    """Marca como leídas las notificaciones de una sección para el cliente en sesión."""
    n = (Notificacion.query
         .filter_by(usuario_id=g.usuario.id, seccion=seccion, leido=False)
         .update({"leido": True}))
    if n:
        db.session.commit()


def _ext_ok(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in EXT_PERMITIDAS


def _tamano_legible(num_bytes):
    for unidad in ["B", "KB", "MB", "GB"]:
        if num_bytes < 1024:
            return f"{num_bytes:.0f} {unidad}" if unidad == "B" else f"{num_bytes:.1f} {unidad}"
        num_bytes /= 1024
    return f"{num_bytes:.1f} TB"


def create_app():
    app = Flask(__name__)

    # Base de datos: DATABASE_URL en producción; SQLite local por defecto.
    default_db = "sqlite:///" + os.path.join(BASE_DIR, "grupo_legal.db")
    db_url = os.environ.get("DATABASE_URL", default_db)
    # Normaliza el esquema antiguo "postgres://" que algunos proveedores usan.
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)
    app.config["SQLALCHEMY_DATABASE_URI"] = db_url

    # "Producción" = base de datos real (no SQLite). Ahí la SECRET_KEY es obligatoria.
    es_produccion = not db_url.startswith("sqlite")
    secret = os.environ.get("SECRET_KEY")
    if not secret:
        if es_produccion:
            raise RuntimeError("Falta SECRET_KEY: define la variable de entorno en producción.")
        secret = "dev-clave-insegura-solo-local"
    app.config["SECRET_KEY"] = secret
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024  # 25 MB por archivo
    # Cookies de sesión endurecidas (Secure solo en producción con HTTPS).
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=es_produccion,
        WTF_CSRF_TIME_LIMIT=None,  # el token vive lo que dure la sesión
    )
    os.makedirs(UPLOAD_DIR, exist_ok=True)

    # Detrás de Nginx: respeta cabeceras X-Forwarded-* (esquema https, IP real).
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    db.init_app(app)
    csrf.init_app(app)
    # render_as_batch: permite ALTER TABLE en SQLite (dev) y no estorba en Postgres.
    migrate.init_app(app, db, render_as_batch=True)

    # Auto-crear/poblar SOLO en SQLite (desarrollo). En producción (Postgres)
    # el esquema se gestiona con migraciones: `flask db upgrade` + `flask seed`.
    # No se ejecuta durante comandos de migración (`flask db ...`) para no chocar
    # con Alembic creando las tablas antes de tiempo.
    es_comando_db = "db" in sys.argv
    auto_init = os.environ.get("AUTO_INIT_DB", "1") != "0"
    if auto_init and db_url.startswith("sqlite") and not es_comando_db:
        with app.app_context():
            # En desarrollo (SQLite) aplica las migraciones automáticamente al
            # arrancar, para que el esquema esté siempre al día sin comandos manuales.
            try:
                from flask_migrate import upgrade as _mig_upgrade
                _mig_upgrade()
            except Exception as e:
                print(f"[dev] No se aplicaron migraciones automáticamente: {e}", file=sys.stderr)
            from sample_data import cargar_datos
            cargar_datos()

    # ----- Comandos de línea (para el VPS) -----
    @app.cli.command("seed")
    def seed_cmd():
        """Puebla la base de datos con datos de ejemplo (si está vacía)."""
        from sample_data import cargar_datos
        creado = cargar_datos()
        print("Datos cargados." if creado else "La base de datos ya tenía datos; no se tocó.")

    # ----- Filtros de plantilla (formato en español) -----
    MESES = ["ene", "feb", "mar", "abr", "may", "jun",
             "jul", "ago", "sep", "oct", "nov", "dic"]

    @app.template_filter("fecha")
    def fmt_fecha(d):
        if not d:
            return "—"
        return f"{d.day:02d} {MESES[d.month - 1].capitalize()}, {d.year}"

    @app.template_filter("dinero")
    def fmt_dinero(valor):
        if valor is None:
            return "$0.00"
        return "${:,.2f}".format(valor)

    # Datos curiosos según la duración (días). Se elige el de mayor umbral <= días.
    DATOS_CURIOSOS = [
        (0, "apenas el comienzo del expediente"),
        (7, "lo que duró la misión Apolo 11 a la Luna, ida y vuelta"),
        (15, "lo que tarda la Luna en pasar de nueva a llena"),
        (30, "una órbita completa de la Luna alrededor de la Tierra"),
        (60, "lo que tarda el corazón humano en latir unos 7 millones de veces"),
        (90, "una estación completa del año"),
        (120, "lo que tardan las golondrinas en migrar miles de kilómetros"),
        (180, "casi lo que tarda un viaje de la Tierra a Marte"),
        (210, "lo que tardó el rover Perseverance en llegar a Marte"),
        (270, "una gestación humana completa"),
        (365, "una vuelta completa de la Tierra alrededor del Sol"),
        (500, "más que la órbita de Mercurio alrededor del Sol cinco veces"),
        (687, "lo que dura un año entero en el planeta Marte"),
        (1080, "lo que tardó Magallanes en dar la primera vuelta al mundo"),
        (1461, "un ciclo bisiesto completo de cuatro años"),
    ]

    @app.template_filter("dato_curioso")
    def fmt_dato_curioso(d):
        if d is None:
            return ""
        elegido = DATOS_CURIOSOS[0][1]
        for umbral, texto in DATOS_CURIOSOS:
            if d >= umbral:
                elegido = texto
            else:
                break
        return elegido

    @app.template_filter("equivalencia")
    def fmt_equivalencia(d):
        """Convierte un número de días en una referencia cercana y memorable."""
        if d is None:
            return ""
        if d <= 0:
            return "comienza hoy"
        anios = d / 365.25
        if anios >= 1:
            txt = f"{anios:.1f}".rstrip("0").rstrip(".")
            return f"≈ {txt} vuelta{'s' if anios >= 2 else ''} al sol"
        meses = d / 30.44
        if meses >= 2:
            return f"≈ {round(meses)} meses"
        semanas = d / 7
        if semanas >= 2:
            return f"≈ {round(semanas)} semanas"
        return f"{d} día{'s' if d != 1 else ''}"

    @app.template_filter("fechahora")
    def fmt_fechahora(dt):
        """Muestra un datetime (guardado en UTC) en hora local de México."""
        if not dt:
            return "—"
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(APP_TZ).strftime("%d/%m/%Y %H:%M")

    @app.before_request
    def cargar_usuario():
        g.usuario = None
        uid = session.get("usuario_id")
        if uid:
            g.usuario = db.session.get(Usuario, uid)

    @app.context_processor
    def inyectar_badge():
        # Badge del abogado: mensajes nuevos dirigidos a él.
        n = 0
        citas_solicitadas = 0
        # Badges del cliente: notificaciones sin leer por sección.
        notif = {"casos": 0, "documentos": 0, "mensajes": 0, "facturas": 0, "citas": 0}
        if g.get("usuario"):
            if g.usuario.es_abogado:
                n = Mensaje.query.filter_by(abogado_id=g.usuario.id,
                                            es_cliente=True, leido=False).count()
                citas_solicitadas = Cita.query.filter_by(abogado_id=g.usuario.id,
                                                         estado="solicitada").count()
            else:
                filas = (db.session.query(Notificacion.seccion, func.count())
                         .filter_by(usuario_id=g.usuario.id, leido=False)
                         .group_by(Notificacion.seccion).all())
                for sec, cnt in filas:
                    if sec in notif:
                        notif[sec] = cnt
        return {"mensajes_no_leidos": n, "notif_cliente": notif,
                "citas_solicitadas": citas_solicitadas, "saludo": _saludo_por_hora()}

    # ===================== AUTENTICACIÓN =====================
    @app.route("/")
    def index():
        if g.usuario:
            return redirect(url_for("admin_dashboard") if g.usuario.es_abogado else url_for("inicio"))
        return redirect(url_for("login"))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            usuario = Usuario.query.filter_by(email=email).first()
            if usuario and usuario.check_password(password):
                session["usuario_id"] = usuario.id
                return redirect(url_for("admin_dashboard") if usuario.es_abogado else url_for("inicio"))
            flash("Correo o contraseña incorrectos.", "error")
        return render_template("login.html")

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    # ----- Recuperación de contraseña -----
    def _serializer():
        return URLSafeTimedSerializer(app.config["SECRET_KEY"], salt="reset-password")

    @app.route("/recuperar", methods=["GET", "POST"])
    def recuperar():
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            usuario = Usuario.query.filter_by(email=email).first()
            if usuario:
                token = _serializer().dumps(usuario.id)
                enlace = url_for("restablecer", token=token, _external=True)
                _enviar_correo(
                    usuario.email, "Restablecer su contraseña",
                    f"Hola {usuario.nombre},\n\nPara restablecer su contraseña abra este enlace "
                    f"(válido por 1 hora):\n{enlace}\n\nSi no lo solicitó, ignore este mensaje.")
            # Respuesta genérica (no revela si el correo existe)
            flash("Si el correo está registrado, enviamos instrucciones para restablecer la contraseña.", "ok")
            return redirect(url_for("login"))
        return render_template("recuperar.html")

    @app.route("/restablecer/<token>", methods=["GET", "POST"])
    def restablecer(token):
        try:
            uid = _serializer().loads(token, max_age=3600)
        except SignatureExpired:
            flash("El enlace expiró. Solicite uno nuevo.", "error")
            return redirect(url_for("recuperar"))
        except BadSignature:
            flash("Enlace inválido.", "error")
            return redirect(url_for("recuperar"))
        usuario = db.session.get(Usuario, uid)
        if not usuario:
            abort(404)
        if request.method == "POST":
            p1 = request.form.get("password", "")
            p2 = request.form.get("password2", "")
            if len(p1) < 8:
                flash("La contraseña debe tener al menos 8 caracteres.", "error")
            elif p1 != p2:
                flash("Las contraseñas no coinciden.", "error")
            else:
                usuario.set_password(p1)
                db.session.commit()
                flash("Contraseña actualizada. Ya puede iniciar sesión.", "ok")
                return redirect(url_for("login"))
        return render_template("restablecer.html", token=token)

    @app.route("/perfil", methods=["GET", "POST"])
    @login_required
    def perfil():
        if request.method == "POST":
            nombre = request.form.get("nombre", "").strip()
            email = request.form.get("email", "").strip().lower()
            if not nombre or not email:
                flash("Nombre y correo son obligatorios.", "error")
                return redirect(url_for("perfil"))
            existente = Usuario.query.filter_by(email=email).first()
            if existente and existente.id != g.usuario.id:
                flash("Ese correo ya está en uso por otra cuenta.", "error")
                return redirect(url_for("perfil"))
            nombre_anterior = g.usuario.nombre
            g.usuario.nombre = nombre
            g.usuario.email = email
            # Si un abogado cambia su nombre, actualiza el mostrado en sus casos.
            if g.usuario.es_abogado and nombre != nombre_anterior:
                for c in Caso.query.join(Usuario, Caso.usuario_id == Usuario.id)\
                        .filter(Usuario.abogado_id == g.usuario.id).all():
                    if c.abogado == nombre_anterior:
                        c.abogado = nombre
            db.session.commit()
            flash("Perfil actualizado correctamente.", "ok")
            return redirect(url_for("perfil"))
        return render_template("perfil.html")

    @app.route("/cambiar-password", methods=["GET", "POST"])
    @login_required
    def cambiar_password():
        if request.method == "POST":
            actual = request.form.get("actual", "")
            p1 = request.form.get("password", "")
            p2 = request.form.get("password2", "")
            if not g.usuario.check_password(actual):
                flash("La contraseña actual no es correcta.", "error")
            elif len(p1) < 8:
                flash("La nueva contraseña debe tener al menos 8 caracteres.", "error")
            elif p1 != p2:
                flash("Las contraseñas no coinciden.", "error")
            else:
                g.usuario.set_password(p1)
                db.session.commit()
                flash("Contraseña actualizada correctamente.", "ok")
                return redirect(url_for("index"))
        return render_template("cambiar_password.html")

    @app.route("/privacidad")
    def privacidad():
        return render_template("privacidad.html", hoy=fmt_fecha(date.today()))

    # ===================== PORTAL CLIENTE =====================
    @app.route("/inicio")
    @cliente_required
    def inicio():
        uid = g.usuario.id
        hoy = date.today()
        facturas = Factura.query.filter_by(usuario_id=uid).all()
        saldo = sum(f.monto for f in facturas if f.estado in ("Pendiente", "Atrasado"))
        pendientes = [f for f in facturas if f.estado in ("Pendiente", "Atrasado") and f.vencimiento]
        proxima_factura = min(pendientes, key=lambda f: f.vencimiento) if pendientes else None

        casos = Caso.query.filter_by(usuario_id=uid).order_by(Caso.actualizado.desc()).all()
        casos_activos = [c for c in casos if c.estado != "Cerrado"]

        proxima_cita = (Cita.query.filter(Cita.usuario_id == uid,
                                          Cita.estado.in_(["confirmada", "solicitada"]),
                                          Cita.fecha >= hoy)
                        .order_by(Cita.fecha, Cita.hora).first())

        docs_recientes = (Documento.query.filter_by(usuario_id=uid)
                          .order_by(Documento.subido.desc()).limit(4).all())
        eventos = (EventoCaso.query.join(Caso).filter(Caso.usuario_id == uid)
                   .order_by(EventoCaso.fecha.desc()).limit(5).all())
        return render_template("inicio.html", saldo=saldo, proxima_factura=proxima_factura,
                               casos=casos, casos_activos=casos_activos, proxima_cita=proxima_cita,
                               docs_recientes=docs_recientes, eventos=eventos,
                               abogado=g.usuario.responsable, seccion="inicio")

    @app.route("/mis-casos")
    @cliente_required
    def casos():
        _marcar_seccion_vista("casos")
        lista = Caso.query.filter_by(usuario_id=g.usuario.id).order_by(Caso.actualizado.desc()).all()
        return render_template("casos.html", casos=lista, seccion="casos")

    @app.route("/mis-casos/<int:caso_id>")
    @cliente_required
    def caso_detalle(caso_id):
        caso = Caso.query.filter_by(id=caso_id, usuario_id=g.usuario.id).first()
        if not caso:
            abort(404)
        eventos = sorted(caso.eventos, key=lambda e: e.fecha, reverse=True)
        return render_template("caso_detalle.html", caso=caso, eventos=eventos, seccion="casos")

    @app.route("/documentos")
    @cliente_required
    def documentos():
        _marcar_seccion_vista("documentos")
        lista = Documento.query.filter_by(usuario_id=g.usuario.id).order_by(Documento.subido.desc()).all()
        casos = Caso.query.filter_by(usuario_id=g.usuario.id).order_by(Caso.referencia).all()
        return render_template("documentos.html", documentos=lista, casos=casos, seccion="documentos")

    @app.route("/documentos/subir", methods=["POST"])
    @cliente_required
    def cliente_subir_documento():
        archivo = request.files.get("archivo")
        if not archivo or archivo.filename == "":
            flash("Selecciona un archivo.", "error")
            return redirect(url_for("documentos"))
        if not _ext_ok(archivo.filename):
            flash("Tipo de archivo no permitido.", "error")
            return redirect(url_for("documentos"))
        nombre_original = secure_filename(archivo.filename)
        ext = nombre_original.rsplit(".", 1)[1].lower()
        nombre_guardado = f"{uuid.uuid4().hex}_{nombre_original}"
        ruta = os.path.join(UPLOAD_DIR, nombre_guardado)
        archivo.save(ruta)
        caso_id = request.form.get("caso_id") or None
        if caso_id:
            try:
                caso_id = int(caso_id)
                # Validar que el caso sea del cliente
                if not Caso.query.filter_by(id=caso_id, usuario_id=g.usuario.id).first():
                    caso_id = None
            except ValueError:
                caso_id = None
        doc = Documento(
            usuario_id=g.usuario.id, caso_id=caso_id,
            nombre=request.form.get("nombre", "").strip() or nombre_original,
            archivo=nombre_guardado, origen="cliente",
            tipo=TIPO_POR_EXT.get(ext, ext.upper()),
            tamano=_tamano_legible(os.path.getsize(ruta)), subido=date.today(),
        )
        db.session.add(doc)
        db.session.commit()
        flash("Documento enviado al despacho.", "ok")
        return redirect(url_for("documentos"))

    @app.route("/documentos/<int:doc_id>/eliminar", methods=["POST"])
    @cliente_required
    def cliente_eliminar_documento(doc_id):
        doc = Documento.query.filter_by(id=doc_id, usuario_id=g.usuario.id).first()
        if not doc:
            abort(404)
        # El cliente solo puede eliminar lo que él mismo subió.
        if doc.origen != "cliente":
            flash("Solo puede eliminar los documentos que usted subió.", "error")
            return redirect(url_for("documentos"))
        if doc.archivo:
            ruta = os.path.join(UPLOAD_DIR, doc.archivo)
            if os.path.exists(ruta):
                os.remove(ruta)
        db.session.delete(doc)
        db.session.commit()
        flash("Documento eliminado.", "ok")
        return redirect(url_for("documentos"))

    @app.route("/mensajes")
    @cliente_required
    def mensajes():
        _marcar_seccion_vista("mensajes")
        lista = Mensaje.query.filter_by(usuario_id=g.usuario.id).order_by(Mensaje.enviado.desc()).all()
        abogados = Usuario.query.filter_by(rol="abogado").order_by(Usuario.nombre).all()
        return render_template("mensajes.html", mensajes=lista, abogados=abogados,
                               default_abogado=g.usuario.abogado_id, seccion="mensajes")

    @app.route("/mensajes/enviar", methods=["POST"])
    @cliente_required
    def enviar_mensaje():
        asunto = request.form.get("asunto", "").strip()
        cuerpo = request.form.get("cuerpo", "").strip()
        # Abogado destinatario elegido por el cliente; si no, su abogado responsable.
        destino_id = request.form.get("abogado_id")
        try:
            destino_id = int(destino_id)
        except (TypeError, ValueError):
            destino_id = g.usuario.abogado_id
        if not Usuario.query.filter_by(id=destino_id, rol="abogado").first():
            destino_id = g.usuario.abogado_id
        responde_a_id = _parent_valido(request.form.get("responde_a_id"), g.usuario.id)
        if cuerpo:
            db.session.add(Mensaje(usuario_id=g.usuario.id, abogado_id=destino_id,
                                   remitente="Tú", es_cliente=True,
                                   asunto=asunto or "(Sin asunto)", cuerpo=cuerpo,
                                   leido=False, responde_a_id=responde_a_id))
            db.session.commit()
            flash("Mensaje enviado al despacho.", "ok")
        return redirect(url_for("mensajes"))

    @app.route("/facturacion")
    @cliente_required
    def facturacion():
        _marcar_seccion_vista("facturas")
        facturas = Factura.query.filter_by(usuario_id=g.usuario.id).order_by(Factura.fecha.desc()).all()
        metodos = MetodoPago.query.filter_by(usuario_id=g.usuario.id).all()
        saldo = sum(f.monto for f in facturas if f.estado in ("Pendiente", "Atrasado"))
        pendientes = [f for f in facturas if f.estado in ("Pendiente", "Atrasado") and f.vencimiento]
        proxima = min(pendientes, key=lambda f: f.vencimiento) if pendientes else None
        return render_template("facturacion.html", facturas=facturas, metodos=metodos,
                               saldo=saldo, proxima=proxima, seccion="facturacion")

    @app.route("/facturacion/pagar/<int:factura_id>", methods=["POST"])
    @cliente_required
    def pagar_factura(factura_id):
        factura = Factura.query.filter_by(id=factura_id, usuario_id=g.usuario.id).first()
        if not factura:
            abort(404)
        if factura.estado == "Pagado":
            flash("Esta factura ya está pagada.", "ok")
            return redirect(url_for("facturacion"))
        try:
            mid = int(request.form.get("metodo_pago_id"))
        except (TypeError, ValueError):
            flash("Selecciona un método de pago.", "error")
            return redirect(url_for("facturacion"))
        metodo = MetodoPago.query.filter_by(id=mid, usuario_id=g.usuario.id).first()
        if not metodo:
            flash("Método de pago no válido.", "error")
            return redirect(url_for("facturacion"))
        # Pago simulado (sin pasarela real): se registra y se marca pagada.
        factura.estado = "Pagado"
        factura.metodo_pago_id = metodo.id
        factura.pagada_en = datetime.utcnow()
        db.session.commit()
        flash(f"Pago de {factura.referencia} realizado con {metodo.descripcion}.", "ok")
        return redirect(url_for("facturacion"))

    @app.route("/facturacion/comprobante/<int:factura_id>")
    @cliente_required
    def comprobante_factura(factura_id):
        factura = Factura.query.filter_by(id=factura_id, usuario_id=g.usuario.id).first()
        if not factura:
            abort(404)
        if factura.estado != "Pagado":
            flash("Solo las facturas pagadas tienen comprobante.", "error")
            return redirect(url_for("facturacion"))
        pdf = _comprobante_pdf(factura)
        return send_file(pdf, mimetype="application/pdf", as_attachment=True,
                         download_name=f"Comprobante_{factura.referencia.lstrip('#')}.pdf")

    # ----- Métodos de pago (cliente) -----
    @app.route("/metodos/nuevo", methods=["POST"])
    @cliente_required
    def agregar_metodo():
        tipo = request.form.get("tipo", "tarjeta")
        ultimos4 = request.form.get("ultimos4", "").strip()
        if not (ultimos4.isdigit() and len(ultimos4) == 4):
            flash("Ingresa los últimos 4 dígitos.", "error")
            return redirect(url_for("facturacion"))
        marca = None
        if tipo == "banco":
            banco = request.form.get("banco", "").strip() or "Cuenta bancaria"
            desc, detalle = banco, f"Cuenta terminada en {ultimos4}"
        else:
            tipo = "tarjeta"
            marca = request.form.get("marca", "otra").strip().lower()
            nombres = {"visa": "Visa", "mastercard": "Mastercard", "amex": "American Express"}
            display = nombres.get(marca, "Tarjeta")
            venc = request.form.get("vencimiento", "").strip()
            desc = f"{display} •••• {ultimos4}"
            detalle = f"Expira {venc}" if venc else "Tarjeta"
        primera = MetodoPago.query.filter_by(usuario_id=g.usuario.id).count() == 0
        db.session.add(MetodoPago(usuario_id=g.usuario.id, tipo=tipo, marca=marca,
                                  descripcion=desc, detalle=detalle, principal=primera))
        db.session.commit()
        flash("Método de pago añadido.", "ok")
        return redirect(url_for("facturacion"))

    @app.route("/metodos/<int:mid>/principal", methods=["POST"])
    @cliente_required
    def metodo_principal(mid):
        metodo = MetodoPago.query.filter_by(id=mid, usuario_id=g.usuario.id).first()
        if metodo:
            for m in MetodoPago.query.filter_by(usuario_id=g.usuario.id).all():
                m.principal = (m.id == metodo.id)
            db.session.commit()
            flash(f"{metodo.descripcion} es ahora tu método principal.", "ok")
        return redirect(url_for("facturacion"))

    @app.route("/metodos/<int:mid>/eliminar", methods=["POST"])
    @cliente_required
    def eliminar_metodo(mid):
        metodo = MetodoPago.query.filter_by(id=mid, usuario_id=g.usuario.id).first()
        if metodo:
            era_principal = metodo.principal
            db.session.delete(metodo)
            db.session.commit()
            # Si era el principal, asignar otro como principal.
            if era_principal:
                otro = MetodoPago.query.filter_by(usuario_id=g.usuario.id).first()
                if otro:
                    otro.principal = True
                    db.session.commit()
            flash("Método de pago eliminado.", "ok")
        return redirect(url_for("facturacion"))

    # ----- Citas (cliente) -----
    @app.route("/citas")
    @cliente_required
    def citas():
        _marcar_seccion_vista("citas")
        hoy = date.today()
        abg = g.usuario.abogado_id
        disponibles = []
        if abg:
            disponibles = (Cita.query.filter(Cita.abogado_id == abg,
                                             Cita.estado == "disponible", Cita.fecha >= hoy)
                           .order_by(Cita.fecha, Cita.hora).all())
        mis_citas = (Cita.query.filter(Cita.usuario_id == g.usuario.id,
                                       Cita.estado.in_(["solicitada", "confirmada"]))
                     .order_by(Cita.fecha, Cita.hora).all())
        abogado = db.session.get(Usuario, abg) if abg else None
        return render_template("citas.html", disponibles=disponibles, mis_citas=mis_citas,
                               abogado=abogado, seccion="citas")

    @app.route("/citas/solicitar/<int:cid>", methods=["POST"])
    @cliente_required
    def solicitar_cita(cid):
        c = db.session.get(Cita, cid)
        if not c or c.estado != "disponible" or c.abogado_id != g.usuario.abogado_id:
            abort(404)
        c.estado = "solicitada"
        c.usuario_id = g.usuario.id
        c.nota = request.form.get("nota", "").strip()[:255]
        db.session.commit()
        flash("Solicitud de cita enviada. Su abogado la confirmará.", "ok")
        return redirect(url_for("citas"))

    # ----- Descarga de documentos (cliente: solo los suyos; abogado: todos) -----
    @app.route("/documentos/descargar/<int:doc_id>")
    @login_required
    def descargar_documento(doc_id):
        doc = db.session.get(Documento, doc_id)
        if not doc or not doc.archivo:
            abort(404)
        if g.usuario.es_abogado:
            # El abogado solo descarga documentos de sus propios clientes
            if doc.cliente.abogado_id != g.usuario.id:
                abort(403)
        elif doc.usuario_id != g.usuario.id:
            abort(403)
        # ?preview=1 sirve el archivo en línea (para verlo en el navegador sin descargar)
        preview = request.args.get("preview") == "1"
        return send_from_directory(UPLOAD_DIR, doc.archivo,
                                   as_attachment=not preview, download_name=doc.nombre)

    @app.route("/documentos/preview/<int:doc_id>")
    @login_required
    def documento_preview(doc_id):
        doc = db.session.get(Documento, doc_id)
        if not doc or not doc.archivo:
            abort(404)
        if g.usuario.es_abogado:
            if doc.cliente.abogado_id != g.usuario.id:
                abort(403)
        elif doc.usuario_id != g.usuario.id:
            abort(403)
        ruta = os.path.join(UPLOAD_DIR, doc.archivo)
        ext = doc.archivo.rsplit(".", 1)[-1].lower() if "." in doc.archivo else ""
        if ext in ("pdf", "png", "jpg", "jpeg", "gif"):
            return send_from_directory(UPLOAD_DIR, doc.archivo, as_attachment=False)
        if ext == "docx":
            try:
                import mammoth
                with open(ruta, "rb") as f:
                    cuerpo = mammoth.convert_to_html(f).value
            except Exception:
                cuerpo = "<p>No se pudo generar la vista previa de este documento.</p>"
            html = ("<!doctype html><html lang='es'><head><meta charset='utf-8'>"
                    "<style>body{font-family:Georgia,'Times New Roman',serif;max-width:800px;"
                    "margin:28px auto;padding:0 26px;color:#1f2733;line-height:1.7}"
                    "img{max-width:100%}table{border-collapse:collapse}td,th{border:1px solid #ddd;padding:6px}"
                    "h1,h2,h3{color:#16243f}</style></head><body>" + cuerpo + "</body></html>")
            return html
        # Otros formatos no se previsualizan
        return ("<!doctype html><meta charset='utf-8'>"
                "<p style='font-family:sans-serif;color:#666;padding:24px;text-align:center'>"
                "La vista previa no está disponible para este tipo de archivo. Descárguelo para abrirlo.</p>")

    # ===================== PANEL ADMIN (ABOGADO) =====================
    @app.route("/admin")
    @admin_required
    def admin_dashboard():
        ids = _ids_mis_clientes(g.usuario)
        casos_q = Caso.query.filter(Caso.usuario_id.in_(ids))
        facturas_q = Factura.query.filter(Factura.usuario_id.in_(ids))
        mensajes_q = Mensaje.query.filter(Mensaje.usuario_id.in_(ids), Mensaje.es_cliente.is_(True))
        stats = {
            "clientes": len(ids),
            "casos_activos": casos_q.filter(Caso.estado != "Cerrado").count(),
            "facturas_pend": facturas_q.filter(Factura.estado != "Pagado").count(),
            "mensajes_sin_resp": mensajes_q.filter_by(leido=False).count(),
        }
        saldo_total = sum(f.monto for f in facturas_q.filter(Factura.estado != "Pagado").all())
        casos_recientes = casos_q.order_by(Caso.actualizado.desc()).limit(5).all()
        mensajes_recientes = mensajes_q.order_by(Mensaje.enviado.desc()).limit(5).all()
        return render_template("admin/dashboard.html", stats=stats, saldo_total=saldo_total,
                               casos_recientes=casos_recientes, mensajes_recientes=mensajes_recientes,
                               seccion="dashboard")

    # ---------- Clientes ----------
    @app.route("/admin/clientes")
    @admin_required
    def admin_clientes():
        clientes = (Usuario.query.filter_by(rol="cliente", abogado_id=g.usuario.id)
                    .order_by(Usuario.nombre).all())
        return render_template("admin/clientes.html", clientes=clientes, seccion="clientes")

    @app.route("/admin/clientes/nuevo", methods=["POST"])
    @admin_required
    def admin_crear_cliente():
        nombre = request.form.get("nombre", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "").strip()
        if not (nombre and email and password):
            flash("Todos los campos son obligatorios.", "error")
        elif Usuario.query.filter_by(email=email).first():
            flash("Ya existe un usuario con ese correo.", "error")
        else:
            # El nuevo cliente queda asignado al abogado que lo crea
            u = Usuario(nombre=nombre, email=email, rol="cliente", abogado_id=g.usuario.id)
            u.set_password(password)
            db.session.add(u)
            db.session.commit()
            flash(f"Cliente {nombre} creado y asignado a usted.", "ok")
        return redirect(url_for("admin_clientes"))

    @app.route("/admin/clientes/<int:cid>")
    @admin_required
    def admin_cliente_detalle(cid):
        cliente = _cliente_mio_o_404(cid)
        abogados = Usuario.query.filter_by(rol="abogado").order_by(Usuario.nombre).all()
        return render_template("admin/cliente_detalle.html", cliente=cliente,
                               abogados=abogados, seccion="clientes")

    @app.route("/admin/clientes/<int:cid>/asignar", methods=["POST"])
    @admin_required
    def admin_reasignar_cliente(cid):
        cliente = _cliente_mio_o_404(cid)
        try:
            nuevo_id = int(request.form.get("abogado_id"))
        except (TypeError, ValueError):
            flash("Selecciona un abogado válido.", "error")
            return redirect(url_for("admin_cliente_detalle", cid=cid))
        nuevo = Usuario.query.filter_by(id=nuevo_id, rol="abogado").first()
        if not nuevo:
            flash("Abogado no válido.", "error")
            return redirect(url_for("admin_cliente_detalle", cid=cid))
        cliente.abogado_id = nuevo.id
        # Actualizar el abogado mostrado en sus casos
        for caso in cliente.casos:
            caso.abogado = nuevo.nombre
        db.session.commit()
        if nuevo.id == g.usuario.id:
            flash(f"{cliente.nombre} ahora es su cliente.", "ok")
            return redirect(url_for("admin_cliente_detalle", cid=cid))
        flash(f"{cliente.nombre} fue reasignado a {nuevo.nombre}.", "ok")
        return redirect(url_for("admin_clientes"))

    # ---------- Casos ----------
    @app.route("/admin/casos")
    @admin_required
    def admin_casos():
        ids = _ids_mis_clientes(g.usuario)
        casos = Caso.query.filter(Caso.usuario_id.in_(ids)).order_by(Caso.actualizado.desc()).all()
        clientes = (Usuario.query.filter_by(rol="cliente", abogado_id=g.usuario.id)
                    .order_by(Usuario.nombre).all())
        return render_template("admin/casos.html", casos=casos, clientes=clientes, seccion="casos")

    @app.route("/admin/casos/nuevo", methods=["POST"])
    @admin_required
    def admin_crear_caso():
        try:
            uid = int(request.form.get("usuario_id"))
        except (TypeError, ValueError):
            flash("Selecciona un cliente válido.", "error")
            return redirect(url_for("admin_casos"))
        if uid not in _ids_mis_clientes(g.usuario):
            abort(403)
        f_ini = request.form.get("iniciado")
        try:
            iniciado = datetime.strptime(f_ini, "%Y-%m-%d").date() if f_ini else date.today()
        except ValueError:
            iniciado = date.today()
        estado = request.form.get("estado", "Activo")
        caso = Caso(
            usuario_id=uid,
            referencia=request.form.get("referencia", "").strip() or _siguiente_ref_caso(),
            titulo=request.form.get("titulo", "").strip(),
            tipo=request.form.get("tipo", "").strip(),
            estado=estado,
            abogado=request.form.get("abogado", "").strip() or g.usuario.nombre,
            descripcion=request.form.get("descripcion", "").strip(),
            iniciado=iniciado,
            cerrado_en=date.today() if estado == "Cerrado" else None,
            actualizado=date.today(),
        )
        if not caso.titulo:
            flash("El título del caso es obligatorio.", "error")
        else:
            db.session.add(caso)
            _avisar_cliente(uid, "casos",
                            f"Se registró un nuevo caso a su nombre: {caso.titulo}",
                            "Nuevo caso registrado")
            db.session.commit()
            flash("Caso creado correctamente.", "ok")
        return redirect(url_for("admin_casos"))

    @app.route("/admin/casos/<int:caso_id>/editar", methods=["GET", "POST"])
    @admin_required
    def admin_editar_caso(caso_id):
        caso = db.session.get(Caso, caso_id)
        if not caso:
            abort(404)
        if caso.cliente.abogado_id != g.usuario.id:
            abort(403)
        if request.method == "POST":
            caso.titulo = request.form.get("titulo", caso.titulo).strip()
            caso.tipo = request.form.get("tipo", caso.tipo).strip()
            nuevo_estado = request.form.get("estado", caso.estado)
            # Congela/reabre la duración según el estado.
            if nuevo_estado == "Cerrado" and caso.estado != "Cerrado":
                caso.cerrado_en = date.today()
            elif nuevo_estado != "Cerrado":
                caso.cerrado_en = None
            caso.estado = nuevo_estado
            caso.abogado = request.form.get("abogado", caso.abogado).strip()
            caso.descripcion = request.form.get("descripcion", caso.descripcion).strip()
            f_ini = request.form.get("iniciado")
            if f_ini:
                try:
                    caso.iniciado = datetime.strptime(f_ini, "%Y-%m-%d").date()
                except ValueError:
                    pass
            caso.actualizado = date.today()
            _notificar(caso.usuario_id, "casos", f"Actualización en el caso {caso.referencia}")
            db.session.commit()
            flash("Caso actualizado.", "ok")
            return redirect(url_for("admin_casos"))
        eventos = sorted(caso.eventos, key=lambda e: e.fecha, reverse=True)
        return render_template("admin/caso_editar.html", caso=caso, eventos=eventos, seccion="casos")

    @app.route("/admin/casos/<int:caso_id>/evento", methods=["POST"])
    @admin_required
    def admin_agregar_evento(caso_id):
        caso = db.session.get(Caso, caso_id)
        if not caso:
            abort(404)
        if caso.cliente.abogado_id != g.usuario.id:
            abort(403)
        titulo = request.form.get("titulo", "").strip()
        if not titulo:
            flash("El título del avance es obligatorio.", "error")
            return redirect(url_for("admin_editar_caso", caso_id=caso_id))
        f = request.form.get("fecha")
        try:
            fecha = datetime.strptime(f, "%Y-%m-%d").date() if f else date.today()
        except ValueError:
            fecha = date.today()
        db.session.add(EventoCaso(caso_id=caso.id, titulo=titulo,
                                  descripcion=request.form.get("descripcion", "").strip(), fecha=fecha))
        caso.actualizado = date.today()
        _avisar_cliente(caso.usuario_id, "casos",
                        f"Hay un nuevo avance en su caso {caso.referencia}: {titulo}",
                        "Nuevo avance en su caso")
        db.session.commit()
        flash("Avance añadido a la línea de tiempo.", "ok")
        return redirect(url_for("admin_editar_caso", caso_id=caso_id))

    @app.route("/admin/eventos/<int:evento_id>/eliminar", methods=["POST"])
    @admin_required
    def admin_eliminar_evento(evento_id):
        ev = db.session.get(EventoCaso, evento_id)
        if not ev:
            abort(404)
        caso_id = ev.caso_id
        if ev.caso.cliente.abogado_id != g.usuario.id:
            abort(403)
        db.session.delete(ev)
        db.session.commit()
        flash("Avance eliminado.", "ok")
        return redirect(url_for("admin_editar_caso", caso_id=caso_id))

    @app.route("/admin/casos/<int:caso_id>/eliminar", methods=["POST"])
    @admin_required
    def admin_eliminar_caso(caso_id):
        caso = db.session.get(Caso, caso_id)
        if caso and caso.cliente.abogado_id != g.usuario.id:
            abort(403)
        if caso:
            db.session.delete(caso)
            db.session.commit()
            flash("Caso eliminado.", "ok")
        return redirect(url_for("admin_casos"))

    # ---------- Documentos ----------
    @app.route("/admin/documentos")
    @admin_required
    def admin_documentos():
        ids = _ids_mis_clientes(g.usuario)
        docs = (Documento.query.filter(Documento.usuario_id.in_(ids))
                .order_by(Documento.subido.desc()).all())
        clientes = (Usuario.query.filter_by(rol="cliente", abogado_id=g.usuario.id)
                    .order_by(Usuario.nombre).all())
        casos = Caso.query.filter(Caso.usuario_id.in_(ids)).order_by(Caso.referencia).all()
        return render_template("admin/documentos.html", documentos=docs, clientes=clientes,
                               casos=casos, seccion="documentos")

    @app.route("/admin/documentos/subir", methods=["POST"])
    @admin_required
    def admin_subir_documento():
        archivo = request.files.get("archivo")
        try:
            uid = int(request.form.get("usuario_id"))
        except (TypeError, ValueError):
            flash("Selecciona un cliente.", "error")
            return redirect(url_for("admin_documentos"))
        if uid not in _ids_mis_clientes(g.usuario):
            abort(403)
        if not archivo or archivo.filename == "":
            flash("Selecciona un archivo.", "error")
            return redirect(url_for("admin_documentos"))
        if not _ext_ok(archivo.filename):
            flash("Tipo de archivo no permitido.", "error")
            return redirect(url_for("admin_documentos"))

        nombre_original = secure_filename(archivo.filename)
        ext = nombre_original.rsplit(".", 1)[1].lower()
        nombre_guardado = f"{uuid.uuid4().hex}_{nombre_original}"
        ruta = os.path.join(UPLOAD_DIR, nombre_guardado)
        archivo.save(ruta)

        caso_id = request.form.get("caso_id") or None
        if caso_id:
            try:
                caso_id = int(caso_id)
            except ValueError:
                caso_id = None

        doc = Documento(
            usuario_id=uid, caso_id=caso_id,
            nombre=request.form.get("nombre", "").strip() or nombre_original,
            archivo=nombre_guardado, tipo=TIPO_POR_EXT.get(ext, ext.upper()),
            tamano=_tamano_legible(os.path.getsize(ruta)), subido=date.today(),
        )
        db.session.add(doc)
        _avisar_cliente(uid, "documentos",
                        f"Su abogado le compartió un nuevo documento: {doc.nombre}",
                        "Nuevo documento en su portal")
        db.session.commit()
        flash("Documento subido correctamente.", "ok")
        return redirect(url_for("admin_documentos"))

    @app.route("/admin/documentos/<int:doc_id>/eliminar", methods=["POST"])
    @admin_required
    def admin_eliminar_documento(doc_id):
        doc = db.session.get(Documento, doc_id)
        if doc and doc.cliente.abogado_id != g.usuario.id:
            abort(403)
        if doc:
            if doc.archivo:
                ruta = os.path.join(UPLOAD_DIR, doc.archivo)
                if os.path.exists(ruta):
                    os.remove(ruta)
            db.session.delete(doc)
            db.session.commit()
            flash("Documento eliminado.", "ok")
        return redirect(url_for("admin_documentos"))

    # ---------- Mensajes ----------
    @app.route("/admin/mensajes")
    @admin_required
    def admin_mensajes():
        # Bandeja: lista de mensajes que los clientes le enviaron a ESTE abogado.
        bandeja = (Mensaje.query.filter_by(abogado_id=g.usuario.id, es_cliente=True)
                   .order_by(Mensaje.enviado.desc()).all())
        # No leídos primero, luego por fecha.
        bandeja.sort(key=lambda m: (m.leido, ), reverse=False)
        return render_template("admin/mensajes.html", bandeja=bandeja, seccion="mensajes")

    @app.route("/admin/mensajes/<int:mid>")
    @admin_required
    def admin_mensaje_detalle(mid):
        m = db.session.get(Mensaje, mid)
        if not m:
            abort(404)
        if m.abogado_id != g.usuario.id:
            abort(403)
        # Al abrir, marcar ESTE mensaje como leído (baja el badge).
        if m.es_cliente and not m.leido:
            m.leido = True
            db.session.commit()
        # Hilo completo de la conversación con este cliente.
        hilo = (Mensaje.query.filter_by(usuario_id=m.usuario_id, abogado_id=g.usuario.id)
                .order_by(Mensaje.enviado.asc()).all())
        return render_template("admin/mensaje_detalle.html", mensaje=m, hilo=hilo,
                               cliente=m.cliente, seccion="mensajes")

    @app.route("/admin/mensajes/responder", methods=["POST"])
    @admin_required
    def admin_responder_mensaje():
        try:
            uid = int(request.form.get("usuario_id"))
        except (TypeError, ValueError):
            abort(400)
        # El abogado solo responde a clientes que le han escrito a él.
        tiene_hilo = Mensaje.query.filter_by(usuario_id=uid, abogado_id=g.usuario.id).first()
        if not tiene_hilo:
            abort(403)
        cuerpo = request.form.get("cuerpo", "").strip()
        asunto = request.form.get("asunto", "").strip()
        # Mensaje al que responde explícitamente (si se indicó y pertenece al hilo)
        responde_a_id = _parent_valido(request.form.get("responde_a_id"), uid)
        if cuerpo:
            db.session.add(Mensaje(usuario_id=uid, abogado_id=g.usuario.id,
                                   remitente=g.usuario.nombre, es_cliente=False,
                                   asunto=asunto or "Respuesta del despacho", cuerpo=cuerpo,
                                   leido=True, responde_a_id=responde_a_id))
            # Marcar como leídos los mensajes que el cliente le envió a este abogado.
            for m in Mensaje.query.filter_by(usuario_id=uid, abogado_id=g.usuario.id,
                                             es_cliente=True, leido=False).all():
                m.leido = True
            _avisar_cliente(uid, "mensajes",
                            f"{g.usuario.nombre} respondió a su mensaje",
                            "Nueva respuesta de su abogado")
            db.session.commit()
            flash("Respuesta enviada al cliente.", "ok")
        # Vuelve al detalle si se respondió desde ahí; si no, a la bandeja.
        volver = request.form.get("detalle_id")
        if volver:
            return redirect(url_for("admin_mensaje_detalle", mid=volver))
        return redirect(url_for("admin_mensajes"))

    # ---------- Facturas ----------
    @app.route("/admin/facturas")
    @admin_required
    def admin_facturas():
        ids = _ids_mis_clientes(g.usuario)
        facturas = (Factura.query.filter(Factura.usuario_id.in_(ids))
                    .order_by(Factura.fecha.desc()).all())
        clientes = (Usuario.query.filter_by(rol="cliente", abogado_id=g.usuario.id)
                    .order_by(Usuario.nombre).all())
        total_pend = sum(f.monto for f in facturas if f.estado != "Pagado")
        total_cobrado = sum(f.monto for f in facturas if f.estado == "Pagado")
        return render_template("admin/facturas.html", facturas=facturas, clientes=clientes,
                               total_pend=total_pend, total_cobrado=total_cobrado, seccion="facturas")

    @app.route("/admin/facturas/nueva", methods=["POST"])
    @admin_required
    def admin_crear_factura():
        try:
            uid = int(request.form.get("usuario_id"))
            monto = float(request.form.get("monto"))
        except (TypeError, ValueError):
            flash("Cliente y monto son obligatorios.", "error")
            return redirect(url_for("admin_facturas"))
        if uid not in _ids_mis_clientes(g.usuario):
            abort(403)
        venc = request.form.get("vencimiento")
        factura = Factura(
            usuario_id=uid,
            referencia=request.form.get("referencia", "").strip() or _siguiente_ref_factura(),
            descripcion=request.form.get("descripcion", "").strip(),
            monto=monto,
            estado=request.form.get("estado", "Pendiente"),
            fecha=date.today(),
            vencimiento=datetime.strptime(venc, "%Y-%m-%d").date() if venc else None,
        )
        db.session.add(factura)
        _avisar_cliente(uid, "facturas",
                        f"Se emitió una nueva factura {factura.referencia} por ${factura.monto:,.2f} USD",
                        "Nueva factura disponible")
        db.session.commit()
        flash("Factura emitida correctamente.", "ok")
        return redirect(url_for("admin_facturas"))

    @app.route("/admin/facturas/<int:factura_id>/estado", methods=["POST"])
    @admin_required
    def admin_cambiar_estado_factura(factura_id):
        factura = db.session.get(Factura, factura_id)
        if factura and factura.cliente.abogado_id != g.usuario.id:
            abort(403)
        if factura:
            factura.estado = request.form.get("estado", factura.estado)
            _notificar(factura.usuario_id, "facturas",
                       f"Factura {factura.referencia}: estado {factura.estado}")
            db.session.commit()
            flash(f"Factura {factura.referencia} actualizada.", "ok")
        return redirect(url_for("admin_facturas"))

    # ---------- Citas (despacho) ----------
    @app.route("/admin/citas")
    @admin_required
    def admin_citas():
        hoy = date.today()
        proximas = (Cita.query.filter(Cita.abogado_id == g.usuario.id, Cita.fecha >= hoy)
                    .order_by(Cita.fecha, Cita.hora).all())
        return render_template("admin/citas.html", citas=proximas, seccion="citas")

    @app.route("/admin/citas/nueva", methods=["POST"])
    @admin_required
    def admin_crear_cita():
        f = request.form.get("fecha")
        inicio = request.form.get("hora_inicio", "").strip()
        fin = request.form.get("hora_fin", "").strip()
        try:
            dur = int(request.form.get("duracion", "60"))
        except (TypeError, ValueError):
            dur = 60
        try:
            fecha = datetime.strptime(f, "%Y-%m-%d").date()
            t0 = datetime.strptime(inicio, "%H:%M")
        except (TypeError, ValueError):
            flash("Indica una fecha y una hora de inicio válidas.", "error")
            return redirect(url_for("admin_citas"))
        if fecha < date.today():
            flash("La fecha no puede ser en el pasado.", "error")
            return redirect(url_for("admin_citas"))

        # Genera una o varias franjas según el rango.
        horas = []
        if fin and dur > 0:
            try:
                t1 = datetime.strptime(fin, "%H:%M")
            except ValueError:
                t1 = t0
            t = t0
            while t <= t1:
                horas.append(t.strftime("%H:%M"))
                t += timedelta(minutes=dur)
        else:
            horas = [t0.strftime("%H:%M")]

        # Evita duplicar franjas ya existentes ese día.
        existentes = {c.hora for c in Cita.query.filter_by(abogado_id=g.usuario.id, fecha=fecha).all()}
        creadas = 0
        for hhmm in horas:
            if hhmm not in existentes:
                db.session.add(Cita(abogado_id=g.usuario.id, fecha=fecha, hora=hhmm, estado="disponible"))
                creadas += 1
        db.session.commit()
        if creadas:
            flash(f"{creadas} franja(s) de disponibilidad añadida(s).", "ok")
        else:
            flash("No se añadieron franjas (revisa el rango o ya existían).", "error")
        return redirect(url_for("admin_citas"))

    @app.route("/admin/citas/<int:cid>/accion", methods=["POST"])
    @admin_required
    def admin_cita_accion(cid):
        c = db.session.get(Cita, cid)
        if not c or c.abogado_id != g.usuario.id:
            abort(404)
        accion = request.form.get("accion")
        fstr = c.fecha.strftime("%d/%m/%Y")
        if accion == "confirmar" and c.estado == "solicitada":
            c.estado = "confirmada"
            if c.usuario_id:
                _notificar(c.usuario_id, "citas", f"Cita confirmada para el {fstr} {c.hora}")
            flash("Cita confirmada.", "ok")
        elif accion == "rechazar" and c.estado in ("solicitada", "confirmada"):
            if c.usuario_id:
                _notificar(c.usuario_id, "citas", f"Su cita del {fstr} no pudo confirmarse")
            c.estado = "disponible"
            c.usuario_id = None
            c.nota = None
            flash("La franja vuelve a estar disponible.", "ok")
        elif accion == "ocupar" and c.estado == "disponible":
            c.estado = "ocupada"
        elif accion == "liberar" and c.estado == "ocupada":
            c.estado = "disponible"
        elif accion == "eliminar":
            db.session.delete(c)
            db.session.commit()
            flash("Franja eliminada.", "ok")
            return redirect(url_for("admin_citas"))
        db.session.commit()
        return redirect(url_for("admin_citas"))

    # ----- Helpers para referencias automáticas -----
    def _siguiente_ref_caso():
        return f"#CAS-{date.today().year}-{Caso.query.count() + 1:03d}"

    def _siguiente_ref_factura():
        return f"#INV-{date.today().year}-{Factura.query.count() + 1:03d}"

    # ----- Errores -----
    @app.errorhandler(403)
    def prohibido(e):
        return render_template("404.html", titulo="Acceso denegado",
                               mensaje="No tiene permisos para ver este recurso."), 403

    @app.errorhandler(404)
    def no_encontrado(e):
        return render_template("404.html"), 404

    return app


app = create_app()


if __name__ == "__main__":
    # Solo para desarrollo local. En producción se usa gunicorn (ver wsgi.py).
    app.run(debug=os.environ.get("FLASK_DEBUG", "1") == "1", port=int(os.environ.get("PORT", 5000)))
