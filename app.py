"""Portal Ejecutivo - Baroccio Rojas Digital
Aplicación web en Flask con dos perfiles:
  - CLIENTE: ve sus casos, documentos, mensajes y facturas.
  - ABOGADO (admin): gestiona casos, sube documentos, responde mensajes,
    emite facturas y da de alta clientes.
"""
import os
import uuid
from functools import wraps
from datetime import date, datetime, timedelta

from dotenv import load_dotenv
from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, abort, g, send_from_directory
)
from flask_migrate import Migrate
from werkzeug.utils import secure_filename
from werkzeug.middleware.proxy_fix import ProxyFix

from sqlalchemy import func
from models import db, Usuario, Caso, Documento, Mensaje, Factura, MetodoPago, Notificacion, Cita

load_dotenv()  # carga variables desde un archivo .env si existe (desarrollo)

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
# Carpeta de subidas configurable (en el VPS conviene un volumen fuera del repo)
UPLOAD_DIR = os.environ.get("UPLOAD_DIR", os.path.join(BASE_DIR, "uploads"))
migrate = Migrate()
EXT_PERMITIDAS = {"pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx",
                  "png", "jpg", "jpeg", "txt", "csv", "zip"}
TIPO_POR_EXT = {"pdf": "PDF", "doc": "DOC", "docx": "DOC", "xls": "XLS",
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


def _saludo_por_hora():
    """Devuelve el saludo adecuado según la hora actual."""
    h = datetime.now().hour
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
    os.makedirs(UPLOAD_DIR, exist_ok=True)

    # Detrás de Nginx: respeta cabeceras X-Forwarded-* (esquema https, IP real).
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    db.init_app(app)
    # render_as_batch: permite ALTER TABLE en SQLite (dev) y no estorba en Postgres.
    migrate.init_app(app, db, render_as_batch=True)

    # Auto-crear/poblar SOLO en SQLite (desarrollo). En producción (Postgres)
    # el esquema se gestiona con migraciones: `flask db upgrade` + `flask seed`.
    auto_init = os.environ.get("AUTO_INIT_DB", "1") != "0"
    if auto_init and db_url.startswith("sqlite"):
        with app.app_context():
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
            return redirect(url_for("admin_dashboard") if g.usuario.es_abogado else url_for("facturacion"))
        return redirect(url_for("login"))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            usuario = Usuario.query.filter_by(email=email).first()
            if usuario and usuario.check_password(password):
                session["usuario_id"] = usuario.id
                return redirect(url_for("admin_dashboard") if usuario.es_abogado else url_for("facturacion"))
            flash("Correo o contraseña incorrectos.", "error")
        return render_template("login.html")

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    # ===================== PORTAL CLIENTE =====================
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
        return render_template("caso_detalle.html", caso=caso, seccion="casos")

    @app.route("/documentos")
    @cliente_required
    def documentos():
        _marcar_seccion_vista("documentos")
        lista = Documento.query.filter_by(usuario_id=g.usuario.id).order_by(Documento.subido.desc()).all()
        return render_template("documentos.html", documentos=lista, seccion="documentos")

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
        if factura:
            factura.estado = "Pagado"
            db.session.commit()
            flash(f"Factura {factura.referencia} pagada correctamente.", "ok")
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
        return send_from_directory(UPLOAD_DIR, doc.archivo, as_attachment=True, download_name=doc.nombre)

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
        caso = Caso(
            usuario_id=uid,
            referencia=request.form.get("referencia", "").strip() or _siguiente_ref_caso(),
            titulo=request.form.get("titulo", "").strip(),
            tipo=request.form.get("tipo", "").strip(),
            estado=request.form.get("estado", "Activo"),
            abogado=request.form.get("abogado", "").strip() or g.usuario.nombre,
            descripcion=request.form.get("descripcion", "").strip(),
            actualizado=date.today(),
        )
        if not caso.titulo:
            flash("El título del caso es obligatorio.", "error")
        else:
            db.session.add(caso)
            _notificar(uid, "casos", f"Nuevo caso: {caso.titulo}")
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
            caso.estado = request.form.get("estado", caso.estado)
            caso.abogado = request.form.get("abogado", caso.abogado).strip()
            caso.descripcion = request.form.get("descripcion", caso.descripcion).strip()
            caso.actualizado = date.today()
            _notificar(caso.usuario_id, "casos", f"Actualización en el caso {caso.referencia}")
            db.session.commit()
            flash("Caso actualizado.", "ok")
            return redirect(url_for("admin_casos"))
        return render_template("admin/caso_editar.html", caso=caso, seccion="casos")

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
        _notificar(uid, "documentos", f"Nuevo documento: {doc.nombre}")
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
            _notificar(uid, "mensajes", f"Nueva respuesta de {g.usuario.nombre}")
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
        _notificar(uid, "facturas", f"Nueva factura {factura.referencia} por {factura.monto:,.2f}")
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
