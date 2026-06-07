"""Modelos de base de datos para el Portal Ejecutivo de Baroccio Rojas Digital."""
from datetime import datetime, date
from sqlalchemy import MetaData
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

# Convención de nombres para constraints. Necesaria para que las migraciones
# (Alembic + modo batch) funcionen también en SQLite, donde toda constraint
# alterada debe tener nombre.
_convencion = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
db = SQLAlchemy(metadata=MetaData(naming_convention=_convencion))


class Usuario(db.Model):
    __tablename__ = "usuarios"
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    rol = db.Column(db.String(20), default="cliente", nullable=False)  # cliente | abogado
    creado = db.Column(db.DateTime, default=datetime.utcnow)

    # Abogado responsable (solo aplica a clientes): auto-referencia a otro Usuario
    abogado_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"))
    responsable = db.relationship("Usuario", remote_side=[id], backref="clientes_asignados")

    @property
    def es_abogado(self):
        return self.rol == "abogado"

    casos = db.relationship("Caso", backref="cliente", lazy=True)
    documentos = db.relationship("Documento", backref="cliente", lazy=True)
    # Mensaje tiene dos FK a usuarios (usuario_id y abogado_id): hay que indicar cuál.
    mensajes = db.relationship("Mensaje", backref="cliente", lazy=True,
                               foreign_keys="Mensaje.usuario_id")
    facturas = db.relationship("Factura", backref="cliente", lazy=True)
    metodos_pago = db.relationship("MetodoPago", backref="cliente", lazy=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password, method='pbkdf2:sha256')

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Caso(db.Model):
    __tablename__ = "casos"
    id = db.Column(db.Integer, primary_key=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=False)
    referencia = db.Column(db.String(40), nullable=False)
    titulo = db.Column(db.String(200), nullable=False)
    tipo = db.Column(db.String(80))  # Litigio, Corporativo, Fiscal, etc.
    estado = db.Column(db.String(40), default="Activo")  # Activo, En revisión, Cerrado
    abogado = db.Column(db.String(120))
    descripcion = db.Column(db.Text)
    actualizado = db.Column(db.Date, default=date.today)


class Documento(db.Model):
    __tablename__ = "documentos"
    id = db.Column(db.Integer, primary_key=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=False)
    caso_id = db.Column(db.Integer, db.ForeignKey("casos.id"))
    nombre = db.Column(db.String(200), nullable=False)
    archivo = db.Column(db.String(255))  # nombre del archivo guardado en disco
    tipo = db.Column(db.String(40))  # PDF, DOCX, etc.
    tamano = db.Column(db.String(40))
    subido = db.Column(db.Date, default=date.today)

    caso = db.relationship("Caso", backref="documentos")


class Mensaje(db.Model):
    __tablename__ = "mensajes"
    id = db.Column(db.Integer, primary_key=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=False)  # cliente del hilo
    # Abogado al que va dirigido el mensaje (lo elige el cliente) o que lo responde
    abogado_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"))
    destinatario = db.relationship("Usuario", foreign_keys=[abogado_id])
    # Mensaje al que responde explícitamente (opcional)
    responde_a_id = db.Column(db.Integer, db.ForeignKey("mensajes.id"))
    responde_a = db.relationship("Mensaje", remote_side=[id])
    remitente = db.Column(db.String(120))  # nombre del abogado o "Tú"
    es_cliente = db.Column(db.Boolean, default=False)
    asunto = db.Column(db.String(200))
    cuerpo = db.Column(db.Text)
    leido = db.Column(db.Boolean, default=False)
    enviado = db.Column(db.DateTime, default=datetime.utcnow)


class Factura(db.Model):
    __tablename__ = "facturas"
    id = db.Column(db.Integer, primary_key=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=False)
    referencia = db.Column(db.String(40), nullable=False)
    descripcion = db.Column(db.String(200))
    monto = db.Column(db.Float, nullable=False)
    estado = db.Column(db.String(20), default="Pendiente")  # Pagado, Pendiente, Atrasado
    fecha = db.Column(db.Date, default=date.today)
    vencimiento = db.Column(db.Date)


class MetodoPago(db.Model):
    __tablename__ = "metodos_pago"
    id = db.Column(db.Integer, primary_key=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=False)
    tipo = db.Column(db.String(20))  # tarjeta, banco
    descripcion = db.Column(db.String(120))  # "Visa terminada en 4242"
    detalle = db.Column(db.String(120))  # "Expira 12/25" o "Cuenta terminada en 8901"
    principal = db.Column(db.Boolean, default=False)


class Cita(db.Model):
    """Cita presencial. El abogado crea franjas (fecha+hora) y el cliente
    solicita una franja disponible; el abogado la confirma."""
    __tablename__ = "citas"
    id = db.Column(db.Integer, primary_key=True)
    abogado_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=False)
    usuario_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"))  # cliente que la reserva
    fecha = db.Column(db.Date, nullable=False)
    hora = db.Column(db.String(5))  # "HH:MM"
    # disponible | ocupada | solicitada | confirmada
    estado = db.Column(db.String(20), default="disponible", nullable=False)
    nota = db.Column(db.String(255))  # motivo de la cita (lo escribe el cliente)
    creado = db.Column(db.DateTime, default=datetime.utcnow)

    abogado = db.relationship("Usuario", foreign_keys=[abogado_id])
    cliente = db.relationship("Usuario", foreign_keys=[usuario_id])


class Notificacion(db.Model):
    """Aviso para un cliente cuando el despacho genera actividad en una sección."""
    __tablename__ = "notificaciones"
    id = db.Column(db.Integer, primary_key=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=False)  # cliente destinatario
    seccion = db.Column(db.String(20), nullable=False)  # casos | documentos | mensajes | facturas
    mensaje = db.Column(db.String(255))
    leido = db.Column(db.Boolean, default=False)
    creado = db.Column(db.DateTime, default=datetime.utcnow)
