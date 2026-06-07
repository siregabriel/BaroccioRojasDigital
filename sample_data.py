"""Datos de ejemplo. Se usan tanto desde app.py (auto-seed al arrancar)
como desde seed.py (re-poblar manualmente). Debe llamarse dentro de un
contexto de aplicación activo."""
from datetime import date, datetime, timedelta
from sqlalchemy.exc import IntegrityError
from models import (db, Usuario, Caso, Documento, Mensaje, Factura, MetodoPago,
                    Notificacion, Cita, EventoCaso)


def datos_cargados():
    """True si ya existe al menos un usuario."""
    return db.session.query(Usuario.id).first() is not None


def cargar_datos(reset=False):
    """Crea las tablas y, si está vacía (o reset=True), la puebla.
    Es seguro ante condiciones de carrera: si otro proceso siembra a la vez,
    se ignora el segundo intento."""
    if reset:
        db.drop_all()
    db.create_all()

    if datos_cargados():
        return False  # ya había datos, no se toca nada

    try:
        return _poblar()
    except IntegrityError:
        # Otro worker ya sembró los datos al mismo tiempo: no pasa nada.
        db.session.rollback()
        return False


def _poblar():
    # ============ ABOGADOS (3) ============
    ana = Usuario(nombre="Lic. Ana Beltrán", email="abogado@grupolegal.mx", rol="abogado")
    ana.set_password("admin1234")
    roberto = Usuario(nombre="Lic. Roberto Fuentes", email="roberto@grupolegal.mx", rol="abogado")
    roberto.set_password("admin1234")
    marta = Usuario(nombre="Lic. Marta Quintero", email="marta@grupolegal.mx", rol="abogado")
    marta.set_password("admin1234")
    db.session.add_all([ana, roberto, marta])
    db.session.commit()

    # ============ CLIENTE PRINCIPAL: Carlos -> Ana ============
    carlos = Usuario(nombre="Carlos Mendoza", email="cliente@grupolegal.mx",
                     rol="cliente", abogado_id=ana.id)
    carlos.set_password("demo1234")
    db.session.add(carlos)
    db.session.commit()

    casos = [
        Caso(usuario_id=carlos.id, referencia="#CAS-2023-014", titulo="Revisión de Contrato Corporativo (Fusión)",
             tipo="Corporativo", estado="Activo", abogado=ana.nombre,
             descripcion="Revisión y negociación de los términos del contrato de fusión entre las sociedades "
                         "involucradas, incluyendo cláusulas de no competencia, distribución de pasivos y "
                         "calendario de cierre.",
             actualizado=date(2023, 11, 2)),
        Caso(usuario_id=carlos.id, referencia="#CAS-2023-009", titulo="Litigio Civil - Fase Inicial",
             tipo="Litigio", estado="En revisión", abogado=ana.nombre,
             descripcion="Demanda por incumplimiento contractual. Actualmente en fase de preparación de "
                         "pruebas y desahogo de la etapa de conciliación.",
             actualizado=date(2023, 10, 18)),
        Caso(usuario_id=carlos.id, referencia="#CAS-2023-005", titulo="Constitución de Sociedad LLC",
             tipo="Corporativo", estado="Cerrado", abogado=ana.nombre,
             descripcion="Constitución de sociedad de responsabilidad limitada, redacción de estatutos y "
                         "registro ante las autoridades correspondientes. Caso concluido satisfactoriamente.",
             actualizado=date(2023, 9, 1)),
        Caso(usuario_id=carlos.id, referencia="#CAS-2023-018", titulo="Asesoría Fiscal Trimestral",
             tipo="Fiscal", estado="Activo", abogado=ana.nombre,
             descripcion="Asesoría continua en materia fiscal, revisión de obligaciones trimestrales y "
                         "optimización de la carga tributaria de la empresa.",
             actualizado=date(2023, 11, 1)),
    ]
    db.session.add_all(casos)
    db.session.commit()

    # Fechas de inicio variadas para el contador de días (relativas a hoy).
    _hoy = date.today()
    casos[0].iniciado = _hoy - timedelta(days=148)   # en curso
    casos[1].iniciado = _hoy - timedelta(days=86)     # en curso
    casos[2].iniciado = _hoy - timedelta(days=300)    # cerrado
    casos[2].cerrado_en = _hoy - timedelta(days=210)  # duró 90 días
    casos[3].iniciado = _hoy - timedelta(days=34)     # en curso
    db.session.commit()

    # Línea de tiempo de ejemplo para el caso de la fusión
    db.session.add_all([
        EventoCaso(caso_id=casos[0].id, fecha=casos[0].iniciado,
                   titulo="Inicio del expediente", descripcion="Apertura del caso y recopilación de documentación inicial."),
        EventoCaso(caso_id=casos[0].id, fecha=_hoy - timedelta(days=110),
                   titulo="Due diligence completado", descripcion="Revisión financiera y legal de las sociedades involucradas."),
        EventoCaso(caso_id=casos[0].id, fecha=_hoy - timedelta(days=40),
                   titulo="Borrador del contrato enviado", descripcion="Tercera versión del contrato remitida al cliente."),
        EventoCaso(caso_id=casos[0].id, fecha=_hoy - timedelta(days=5),
                   titulo="Negociación de cláusulas", descripcion="Ajuste de cláusulas de no competencia y calendario de cierre."),
    ])
    db.session.commit()

    db.session.add_all([
        Documento(usuario_id=carlos.id, caso_id=casos[0].id, nombre="Contrato de Fusión - Borrador v3.pdf",
                  tipo="PDF", tamano="2.4 MB", subido=date(2023, 10, 28)),
        Documento(usuario_id=carlos.id, caso_id=casos[0].id, nombre="Due Diligence - Resumen Ejecutivo.docx",
                  tipo="DOC", tamano="850 KB", subido=date(2023, 10, 15)),
        Documento(usuario_id=carlos.id, caso_id=casos[1].id, nombre="Demanda Inicial.pdf",
                  tipo="PDF", tamano="1.1 MB", subido=date(2023, 9, 30)),
        Documento(usuario_id=carlos.id, caso_id=casos[2].id, nombre="Acta Constitutiva LLC.pdf",
                  tipo="PDF", tamano="3.0 MB", subido=date(2023, 9, 1)),
        Documento(usuario_id=carlos.id, caso_id=casos[3].id, nombre="Declaración Fiscal Q3.xlsx",
                  tipo="XLS", tamano="420 KB", subido=date(2023, 10, 5)),
    ])

    ahora = datetime(2023, 11, 3, 9, 30)
    db.session.add_all([
        Mensaje(usuario_id=carlos.id, abogado_id=ana.id, remitente=ana.nombre, es_cliente=False, leido=False,
                asunto="Avance en la revisión del contrato de fusión",
                cuerpo="Estimado Carlos, hemos finalizado la tercera revisión del contrato. Le he adjuntado "
                       "el borrador actualizado en la sección de Documentos. Quedo atenta a sus comentarios.",
                enviado=ahora),
        Mensaje(usuario_id=carlos.id, abogado_id=ana.id, remitente="Tú", es_cliente=True, leido=False,
                asunto="Consulta sobre el calendario de cierre",
                cuerpo="Ana, ¿podríamos revisar las fechas del cierre esta semana?",
                enviado=ahora + timedelta(hours=2)),
    ])

    db.session.add_all([
        Factura(usuario_id=carlos.id, referencia="#INV-2023-089", descripcion="Revisión de Contrato Corporativo (Fusión)",
                monto=4250.00, estado="Atrasado", fecha=date(2023, 10, 15), vencimiento=date(2023, 11, 15)),
        Factura(usuario_id=carlos.id, referencia="#INV-2023-092", descripcion="Asesoría Fiscal Trimestral",
                monto=8200.00, estado="Pendiente", fecha=date(2023, 11, 1), vencimiento=date(2023, 12, 1)),
        Factura(usuario_id=carlos.id, referencia="#INV-2023-075", descripcion="Litigio Civil - Fase Inicial",
                monto=12000.00, estado="Pagado", fecha=date(2023, 9, 15), vencimiento=date(2023, 10, 15)),
        Factura(usuario_id=carlos.id, referencia="#INV-2023-068", descripcion="Constitución de Sociedad LLC",
                monto=3500.00, estado="Pagado", fecha=date(2023, 9, 1), vencimiento=date(2023, 10, 1)),
    ])
    db.session.add_all([
        MetodoPago(usuario_id=carlos.id, tipo="tarjeta", marca="visa", descripcion="Visa •••• 4242", detalle="Expira 12/25", principal=True),
        MetodoPago(usuario_id=carlos.id, tipo="banco", descripcion="Chase Bank", detalle="Cuenta terminada en 8901"),
    ])

    # ============ CLIENTE 2: Sofía Vega -> Roberto ============
    sofia = Usuario(nombre="Sofía Vega", email="sofia@cliente.mx", rol="cliente", abogado_id=roberto.id)
    sofia.set_password("demo1234")
    db.session.add(sofia)
    db.session.commit()
    caso_s = Caso(usuario_id=sofia.id, referencia="#CAS-2023-021", titulo="Arrendamiento Comercial - Disputa",
                  tipo="Litigio", estado="Activo", abogado=roberto.nombre,
                  descripcion="Controversia sobre cláusulas de un contrato de arrendamiento comercial y "
                              "devolución de depósito en garantía.",
                  actualizado=date(2023, 11, 4))
    db.session.add(caso_s)
    db.session.commit()
    db.session.add_all([
        Documento(usuario_id=sofia.id, caso_id=caso_s.id, nombre="Contrato de Arrendamiento.pdf",
                  tipo="PDF", tamano="1.6 MB", subido=date(2023, 10, 20)),
        Mensaje(usuario_id=sofia.id, abogado_id=roberto.id, remitente="Tú", es_cliente=True, leido=False,
                asunto="Duda sobre el depósito",
                cuerpo="Lic. Fuentes, ¿hay novedades sobre la devolución del depósito?",
                enviado=datetime(2023, 11, 4, 11, 0)),
        Factura(usuario_id=sofia.id, referencia="#INV-2023-101", descripcion="Arrendamiento Comercial - Disputa",
                monto=6800.00, estado="Pendiente", fecha=date(2023, 11, 4), vencimiento=date(2023, 12, 4)),
    ])

    # ============ CLIENTE 3: Diego Herrera -> Marta ============
    diego = Usuario(nombre="Diego Herrera", email="diego@cliente.mx", rol="cliente", abogado_id=marta.id)
    diego.set_password("demo1234")
    db.session.add(diego)
    db.session.commit()
    caso_d = Caso(usuario_id=diego.id, referencia="#CAS-2023-024", titulo="Planeación Fiscal Anual",
                  tipo="Fiscal", estado="En revisión", abogado=marta.nombre,
                  descripcion="Estrategia de planeación fiscal anual y cumplimiento de obligaciones ante el SAT.",
                  actualizado=date(2023, 11, 5))
    db.session.add(caso_d)
    db.session.commit()
    db.session.add_all([
        Documento(usuario_id=diego.id, caso_id=caso_d.id, nombre="Estados Financieros 2023.xlsx",
                  tipo="XLS", tamano="980 KB", subido=date(2023, 10, 30)),
        Mensaje(usuario_id=diego.id, abogado_id=marta.id, remitente="Tú", es_cliente=True, leido=False,
                asunto="Documentos pendientes",
                cuerpo="Lic. Quintero, le envié los estados financieros. ¿Falta algo más?",
                enviado=datetime(2023, 11, 5, 16, 0)),
        Factura(usuario_id=diego.id, referencia="#INV-2023-104", descripcion="Planeación Fiscal Anual",
                monto=9500.00, estado="Pendiente", fecha=date(2023, 11, 5), vencimiento=date(2023, 12, 5)),
    ])

    # ============ CITAS: disponibilidad de ejemplo (fechas futuras) ============
    hoy = date.today()
    db.session.add_all([
        # Disponibilidad de Ana (abogada de Carlos)
        Cita(abogado_id=ana.id, fecha=hoy + timedelta(days=2), hora="10:00", estado="disponible"),
        Cita(abogado_id=ana.id, fecha=hoy + timedelta(days=2), hora="12:30", estado="disponible"),
        Cita(abogado_id=ana.id, fecha=hoy + timedelta(days=5), hora="09:00", estado="disponible"),
        Cita(abogado_id=ana.id, fecha=hoy + timedelta(days=5), hora="16:00", estado="ocupada"),
        # Una solicitud pendiente de Carlos a Ana
        Cita(abogado_id=ana.id, usuario_id=carlos.id, fecha=hoy + timedelta(days=3), hora="11:00",
             estado="solicitada", nota="Revisar avances del contrato de fusión"),
        # Disponibilidad de Roberto (abogado de Sofía)
        Cita(abogado_id=roberto.id, fecha=hoy + timedelta(days=4), hora="13:00", estado="disponible"),
        Cita(abogado_id=roberto.id, fecha=hoy + timedelta(days=6), hora="10:30", estado="disponible"),
        # Disponibilidad de Marta (abogada de Diego)
        Cita(abogado_id=marta.id, fecha=hoy + timedelta(days=4), hora="17:00", estado="disponible"),
    ])

    # ============ NOTIFICACIONES de ejemplo para los clientes ============
    db.session.add_all([
        Notificacion(usuario_id=carlos.id, seccion="mensajes", mensaje="Nueva respuesta de Lic. Ana Beltrán"),
        Notificacion(usuario_id=carlos.id, seccion="documentos", mensaje="Nuevo documento: Contrato de Fusión - Borrador v3.pdf"),
        Notificacion(usuario_id=carlos.id, seccion="facturas", mensaje="Nueva factura #INV-2023-092"),
        Notificacion(usuario_id=sofia.id, seccion="facturas", mensaje="Nueva factura #INV-2023-101"),
        Notificacion(usuario_id=diego.id, seccion="documentos", mensaje="Nuevo documento: Estados Financieros 2023.xlsx"),
    ])

    db.session.commit()
    return True
