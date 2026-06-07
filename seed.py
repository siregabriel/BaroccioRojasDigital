"""Reinicia y vuelve a poblar la base de datos con datos de ejemplo.
Ejecutar:  python seed.py

Nota: la app ya crea y puebla la base de datos automáticamente al arrancar
si está vacía, así que normalmente NO necesitas ejecutar esto. Úsalo solo
si quieres BORRAR los datos actuales y volver a empezar de cero.
"""
from app import create_app
from sample_data import cargar_datos

app = create_app()

with app.app_context():
    cargar_datos(reset=True)
    print("Base de datos reiniciada y poblada.")
    print("Acceso de demostración:  cliente@grupolegal.mx  /  demo1234")
