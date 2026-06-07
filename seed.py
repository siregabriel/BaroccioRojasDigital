"""Carga los datos de ejemplo en la base de datos.

USO NORMAL (no borra nada; solo siembra si está vacía):
    python seed.py

REINICIO TOTAL (¡BORRA TODOS LOS DATOS! úsalo solo a propósito):
    python seed.py --reset

Nota: para aplicar cambios de estructura sin perder datos usa migraciones:
    python -m flask db upgrade
"""
import sys
from app import create_app
from sample_data import cargar_datos

app = create_app()
reset = "--reset" in sys.argv

with app.app_context():
    if reset:
        resp = input("⚠️  Esto BORRARÁ todos los datos y cargará los de ejemplo. Escribe 'BORRAR' para confirmar: ")
        if resp.strip() != "BORRAR":
            print("Cancelado. No se tocó la base de datos.")
            sys.exit(0)
        cargar_datos(reset=True)
        print("Base de datos reiniciada y poblada con datos de ejemplo.")
    else:
        creado = cargar_datos(reset=False)
        if creado:
            print("Base de datos vacía: se cargaron los datos de ejemplo.")
        else:
            print("La base de datos ya tenía datos; no se borró nada.")
            print("Si querías reiniciarla por completo, usa: python seed.py --reset")

    print("Accesos demo →  abogado@grupolegal.mx / admin1234   ·   cliente@grupolegal.mx / demo1234")
