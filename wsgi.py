"""Punto de entrada para servidores WSGI (gunicorn).
Uso en el VPS:  gunicorn --bind 127.0.0.1:8000 wsgi:app
"""
from app import app

if __name__ == "__main__":
    app.run()
