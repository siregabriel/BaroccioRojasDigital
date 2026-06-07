#!/usr/bin/env bash
# Script de despliegue que se ejecuta EN EL VPS en cada push.
# Lo invoca GitHub Actions por SSH (ver .github/workflows/deploy.yml).
set -euo pipefail

APP_DIR="/var/www/baroccio"
cd "$APP_DIR"

echo "==> Descargando últimos cambios"
git fetch --all
git reset --hard origin/main

echo "==> Instalando dependencias"
"$APP_DIR/venv/bin/pip" install -r requirements.txt --quiet

echo "==> Aplicando migraciones de base de datos"
set -a; source "$APP_DIR/.env"; set +a
"$APP_DIR/venv/bin/flask" --app app db upgrade

echo "==> Reiniciando el servicio"
sudo systemctl restart baroccio

echo "==> Despliegue completado"
