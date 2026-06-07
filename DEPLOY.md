# Guía de despliegue — Baroccio Rojas Digital (AWS Lightsail)

Cómo poner la app en producción en **AWS Lightsail** con dominio propio
(`barocciorojasdigital.com`) y **despliegue automático**: cada `git push` a la
rama `main` actualiza el sitio solo.

**Stack:** Ubuntu (Lightsail) + Python/Gunicorn + Nginx + PostgreSQL + SSL (Let's Encrypt) + GitHub Actions.

---

## Visión general del flujo

```
Tu computadora  ──git push──►  GitHub  ──(GitHub Actions, SSH)──►  Lightsail
                                                                   ├─ git pull
                                                                   ├─ pip install
                                                                   ├─ flask db upgrade
                                                                   └─ reinicia Gunicorn
                                                           Nginx + SSL sirve el sitio
                                                           en https://barocciorojasdigital.com
```

---

## Paso 0 · Crear la instancia en Lightsail

1. Entra a <https://lightsail.aws.amazon.com> → **Create instance**.
2. **Región:** elige la más cercana a tus usuarios (p. ej. *Ohio* o *Virginia* para México/EE. UU.).
3. **Plataforma:** Linux/Unix.
4. **Blueprint:** pestaña **OS Only → Ubuntu 24.04 LTS** (no elijas "Apps + OS").
5. **Plan:** el de **$5/mes** (1 GB RAM, incluye IP pública IPv4). Con los créditos de AWS los primeros meses salen gratis.
6. Nombra la instancia `baroccio` y **Create instance**.

### IP estática (importante)

Para que la IP no cambie al reiniciar:
**Lightsail → Networking → Create static IP → adjúntala a la instancia `baroccio`.**
Anota esa **IP estática**: es la que usarás en SSH y en el DNS.

### Abrir el firewall

En la instancia → pestaña **Networking → IPv4 Firewall**, añade reglas:

| Aplicación | Protocolo | Puerto |
|------------|-----------|--------|
| HTTP       | TCP       | 80     |
| HTTPS      | TCP       | 443    |

(El puerto 22 / SSH ya viene abierto.)

---

## Paso 1 · Conectarte por SSH

La forma más rápida: botón naranja **"Connect using SSH"** en la consola de Lightsail
(abre una terminal en el navegador, sin configurar nada).

Para conectarte desde tu Mac:
**Lightsail → Account → SSH keys → descarga la llave por defecto** (`LightsailDefaultKey.pem`), y:

```bash
chmod 400 ~/Downloads/LightsailDefaultKey.pem
ssh -i ~/Downloads/LightsailDefaultKey.pem ubuntu@TU_IP_ESTATICA
```

> El usuario en Lightsail es **`ubuntu`** (no root). Tiene `sudo` sin contraseña.

---

## Paso 2 · Subir el proyecto a GitHub

Desde **tu computadora**, en la carpeta del proyecto:

```bash
git init
git add .
git commit -m "Versión inicial de la app"
git branch -M main
git remote add origin https://github.com/TU_USUARIO/baroccio.git
git push -u origin main
```

> El `.gitignore` ya evita subir `.env`, la base de datos local y los archivos subidos.

---

## Paso 3 · Preparar el servidor

Ya conectado por SSH como `ubuntu`:

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3 python3-venv python3-pip git nginx postgresql certbot python3-certbot-nginx
```

Clona el repositorio en `/var/www/baroccio` y dáselo a tu usuario:

```bash
sudo mkdir -p /var/www
sudo git clone https://github.com/TU_USUARIO/baroccio.git /var/www/baroccio
sudo chown -R ubuntu:ubuntu /var/www/baroccio
cd /var/www/baroccio
python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

---

## Paso 4 · Base de datos PostgreSQL

```bash
sudo -u postgres psql <<'SQL'
CREATE USER baroccio WITH PASSWORD 'PON_UNA_CONTRASEÑA_FUERTE';
CREATE DATABASE baroccio OWNER baroccio;
SQL
```

---

## Paso 5 · Variables de entorno (`.env`)

Genera una clave secreta:

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

Crea `/var/www/baroccio/.env` (`nano /var/www/baroccio/.env`):

```env
SECRET_KEY=la-clave-que-acabas-de-generar
DATABASE_URL=postgresql://baroccio:PON_UNA_CONTRASEÑA_FUERTE@localhost:5432/baroccio
UPLOAD_DIR=/var/www/baroccio/uploads
FLASK_DEBUG=0
AUTO_INIT_DB=0
```

Crea el esquema y los datos iniciales:

```bash
cd /var/www/baroccio
set -a; source .env; set +a
export FLASK_APP=app.py
venv/bin/flask db upgrade     # crea las tablas
venv/bin/flask seed           # carga abogados y clientes de ejemplo
mkdir -p uploads
```

---

## Paso 6 · Gunicorn como servicio (systemd)

```bash
sudo cp /var/www/baroccio/deploy/baroccio.service /etc/systemd/system/baroccio.service
sudo systemctl daemon-reload
sudo systemctl enable --now baroccio
sudo systemctl status baroccio       # debe verse "active (running)"
```

---

## Paso 7 · Nginx (proxy inverso)

```bash
sudo cp /var/www/baroccio/deploy/nginx.conf /etc/nginx/sites-available/baroccio
sudo ln -s /etc/nginx/sites-available/baroccio /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
```

Prueba: abre `http://TU_IP_ESTATICA` — ya debería verse la app.

---

## Paso 8 · Apuntar el dominio (DNS en Hostinger)

El dominio sigue registrado en Hostinger; solo cambias a dónde apunta.
En hPanel → **Dominios → barocciorojasdigital.com → Administrar registros DNS**:

| Tipo | Nombre | Valor (apunta a)     | TTL  |
|------|--------|----------------------|------|
| A    | `@`    | `TU_IP_ESTATICA`     | 3600 |
| A    | `www`  | `TU_IP_ESTATICA`     | 3600 |

Borra cualquier registro `A`/`CNAME` previo de `@` y `www`. Propaga en minutos a un par de horas.

---

## Paso 9 · Certificado SSL (HTTPS gratis)

Cuando el dominio ya resuelva a tu IP de Lightsail:

```bash
sudo certbot --nginx -d barocciorojasdigital.com -d www.barocciorojasdigital.com
```

Certbot configura HTTPS y la renovación automática.
Listo: **https://barocciorojasdigital.com**

---

## Paso 10 · Despliegue automático con GitHub Actions

**a) Llave SSH solo para deploy** (en tu computadora):

```bash
ssh-keygen -t ed25519 -f deploy_key -N ""
```

Añade la **pública** al servidor (conéctate por SSH y pega el contenido de `deploy_key.pub`):

```bash
echo "CONTENIDO_DE_deploy_key.pub" >> ~/.ssh/authorized_keys
```

**b) Secrets en GitHub** (repo → Settings → Secrets and variables → Actions):

| Secret        | Valor                                       |
|---------------|---------------------------------------------|
| `VPS_HOST`    | la IP estática de Lightsail                 |
| `VPS_USER`    | `ubuntu`                                     |
| `VPS_PORT`    | `22`                                         |
| `VPS_SSH_KEY` | el contenido del archivo `deploy_key` (privada) |

**c)** El workflow ya está en `.github/workflows/deploy.yml`. A partir de ahora, cada
`git push` a `main` ejecuta `deploy/deploy.sh` en el servidor automáticamente.

> El usuario `ubuntu` ya tiene `sudo` sin contraseña en Lightsail, así que
> `sudo systemctl restart baroccio` del `deploy.sh` funciona sin configurar nada más.

---

## El día a día

1. Editas el código en tu computadora.
2. `git add . && git commit -m "lo que cambiaste" && git push`
3. GitHub Actions despliega solo. En ~1 minuto está en línea.

### Si cambiaste los modelos (models.py)

Genera la migración **en tu computadora** antes del push, para aplicar el cambio de
esquema en producción sin perder datos:

```bash
export FLASK_APP=app.py
python -m flask db migrate -m "describe el cambio"
python -m flask db upgrade        # prueba local
git add migrations && git commit -m "migración: ..." && git push
```

El `deploy.sh` ejecuta `flask db upgrade` en el servidor automáticamente.

---

## Comandos útiles (en el servidor)

```bash
sudo systemctl status baroccio          # estado del servicio
sudo journalctl -u baroccio -n 50       # últimos logs de la app
sudo systemctl restart baroccio         # reiniciar manualmente
sudo nginx -t && sudo systemctl reload nginx
```

### Problemas comunes

- **502 Bad Gateway** → Gunicorn está caído: `sudo journalctl -u baroccio -n 50`.
- **La página no carga por dominio/IP** → revisa el firewall de Lightsail (puertos 80/443 abiertos).
- **Falta SECRET_KEY al arrancar** → revisa que `/var/www/baroccio/.env` exista y tenga `SECRET_KEY`.
- **No conecta a la base de datos** → verifica `DATABASE_URL` y `sudo systemctl status postgresql`.
- **Los archivos subidos desaparecen** → confirma que `UPLOAD_DIR` apunta a una carpeta persistente con permisos de `ubuntu`.

> **Tip de costo:** vigila tus créditos en **AWS Billing**. Cuando se acaben, la instancia de
> $5/mes se cobra normal. Crea un **presupuesto/alerta** en AWS Budgets para no llevarte sorpresas.
