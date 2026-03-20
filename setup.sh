#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# Video Production Manager - Server Setup Script
# For Ubuntu 24.04 LTS (fresh minimal install)
# ============================================================

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}============================================${NC}"
echo -e "${BLUE}  Video Production Manager - Setup Script${NC}"
echo -e "${BLUE}============================================${NC}"
echo ""
echo -e "${YELLOW}All questions will be asked upfront before any${NC}"
echo -e "${YELLOW}installation begins. Please answer each prompt.${NC}"
echo ""

# ============================================================
# PHASE 1: Gather ALL configuration (no installs yet)
# ============================================================

# --- Domain ---
read -rp "$(echo -e ${GREEN})[1/10] Domain name (e.g., vpm.example.com): $(echo -e ${NC})" DOMAIN
while [[ -z "$DOMAIN" ]]; do
    read -rp "  Domain cannot be empty. Enter domain: " DOMAIN
done

# --- Admin username ---
read -rp "$(echo -e ${GREEN})[2/10] Admin username: $(echo -e ${NC})" ADMIN_USER
while [[ -z "$ADMIN_USER" ]]; do
    read -rp "  Username cannot be empty. Enter username: " ADMIN_USER
done

# --- Admin email ---
read -rp "$(echo -e ${GREEN})[3/10] Admin email: $(echo -e ${NC})" ADMIN_EMAIL
while [[ -z "$ADMIN_EMAIL" ]]; do
    read -rp "  Email cannot be empty. Enter email: " ADMIN_EMAIL
done

# --- Admin password ---
while true; do
    read -rsp "$(echo -e ${GREEN})[4/10] Admin password: $(echo -e ${NC})" ADMIN_PASS
    echo ""
    if [[ ${#ADMIN_PASS} -lt 8 ]]; then
        echo -e "${RED}  Password must be at least 8 characters.${NC}"
        continue
    fi
    read -rsp "  Confirm password: " ADMIN_PASS_CONFIRM
    echo ""
    if [[ "$ADMIN_PASS" != "$ADMIN_PASS_CONFIRM" ]]; then
        echo -e "${RED}  Passwords do not match. Try again.${NC}"
    else
        break
    fi
done

# --- Database backend ---
echo -e "${GREEN}[5/10] Database backend:${NC}"
echo "  1) SQLite  (simple, good for small deployments)"
echo "  2) PostgreSQL  (recommended for production)"
read -rp "  Choose [1/2, default: 1]: " DB_CHOICE
DB_CHOICE=${DB_CHOICE:-1}

DB_BACKEND="sqlite"
DB_NAME=""
DB_USER=""
DB_PASS=""
DB_HOST=""
DB_PORT=""

if [[ "$DB_CHOICE" == "2" ]]; then
    DB_BACKEND="postgres"
    read -rp "  PostgreSQL database name [vpm]: " DB_NAME
    DB_NAME=${DB_NAME:-vpm}
    read -rp "  PostgreSQL user [vpm]: " DB_USER
    DB_USER=${DB_USER:-vpm}
    read -rsp "  PostgreSQL password: " DB_PASS
    echo ""
    read -rp "  PostgreSQL host [localhost]: " DB_HOST
    DB_HOST=${DB_HOST:-localhost}
    read -rp "  PostgreSQL port [5432]: " DB_PORT
    DB_PORT=${DB_PORT:-5432}
fi

# --- SSL ---
read -rp "$(echo -e ${GREEN})[6/10] Set up Let's Encrypt SSL? (y/n) [y]: $(echo -e ${NC})" SETUP_SSL
SETUP_SSL=${SETUP_SSL:-y}

LE_EMAIL=""
if [[ "$SETUP_SSL" == "y" || "$SETUP_SSL" == "Y" ]]; then
    read -rp "  Email for Let's Encrypt notifications [$ADMIN_EMAIL]: " LE_EMAIL
    LE_EMAIL=${LE_EMAIL:-$ADMIN_EMAIL}
fi

# --- Default recording settings ---
read -rp "$(echo -e ${GREEN})[7/10] Default max recording time in seconds (0=unlimited) [300]: $(echo -e ${NC})" DEFAULT_MAX_REC
DEFAULT_MAX_REC=${DEFAULT_MAX_REC:-300}

read -rp "$(echo -e ${GREEN})[8/10] Default max recordings per project (0=unlimited) [0]: $(echo -e ${NC})" DEFAULT_MAX_PER_PROJECT
DEFAULT_MAX_PER_PROJECT=${DEFAULT_MAX_PER_PROJECT:-0}

# --- Install path ---
read -rp "$(echo -e ${GREEN})[9/10] Installation path [/var/www/vpm]: $(echo -e ${NC})" INSTALL_PATH
INSTALL_PATH=${INSTALL_PATH:-/var/www/vpm}

# --- Workers ---
CPU_COUNT=$(nproc 2>/dev/null || echo 2)
DEFAULT_WORKERS=$(( CPU_COUNT * 2 + 1 ))
read -rp "$(echo -e ${GREEN})[10/10] Gunicorn workers [$DEFAULT_WORKERS]: $(echo -e ${NC})" WORKERS
WORKERS=${WORKERS:-$DEFAULT_WORKERS}

# ============================================================
# PHASE 1.5: Confirm
# ============================================================

echo ""
echo -e "${BLUE}============================================${NC}"
echo -e "${BLUE}  Configuration Summary${NC}"
echo -e "${BLUE}============================================${NC}"
echo -e "  Domain:           ${GREEN}$DOMAIN${NC}"
echo -e "  Admin user:       ${GREEN}$ADMIN_USER${NC}"
echo -e "  Admin email:      ${GREEN}$ADMIN_EMAIL${NC}"
echo -e "  Database:         ${GREEN}$DB_BACKEND${NC}"
echo -e "  SSL:              ${GREEN}$SETUP_SSL${NC}"
echo -e "  Max rec time:     ${GREEN}${DEFAULT_MAX_REC}s${NC}"
echo -e "  Max recs/project: ${GREEN}$DEFAULT_MAX_PER_PROJECT${NC}"
echo -e "  Install path:     ${GREEN}$INSTALL_PATH${NC}"
echo -e "  Workers:          ${GREEN}$WORKERS${NC}"
echo ""
read -rp "$(echo -e ${YELLOW})Proceed with installation? (y/n): $(echo -e ${NC})" CONFIRM
if [[ "$CONFIRM" != "y" && "$CONFIRM" != "Y" ]]; then
    echo "Aborted."
    exit 1
fi

echo ""
echo -e "${BLUE}============================================${NC}"
echo -e "${BLUE}  Starting installation...${NC}"
echo -e "${BLUE}  No more questions will be asked.${NC}"
echo -e "${BLUE}============================================${NC}"
echo ""

# ============================================================
# PHASE 2: Install (fully unattended from here)
# ============================================================

export DEBIAN_FRONTEND=noninteractive

# --- 2.1: System packages ---
echo -e "${GREEN}[Step 1/10] Installing system packages...${NC}"
apt-get update -qq
apt-get install -y -qq \
    python3 python3-venv python3-pip python3-dev \
    nginx redis-server ffmpeg \
    build-essential libffi-dev \
    $(if [[ "$DB_BACKEND" == "postgres" ]]; then echo "postgresql postgresql-contrib libpq-dev"; fi) \
    > /dev/null

# --- 2.2: Create directory structure ---
echo -e "${GREEN}[Step 2/10] Setting up application directory...${NC}"
mkdir -p "$INSTALL_PATH"

# Copy project files (script should be run from project root)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ "$SCRIPT_DIR" != "$INSTALL_PATH" ]]; then
    cp -r "$SCRIPT_DIR"/* "$INSTALL_PATH/" 2>/dev/null || true
    cp -r "$SCRIPT_DIR"/.gitignore "$INSTALL_PATH/" 2>/dev/null || true
fi
cd "$INSTALL_PATH"

# --- 2.3: Python virtual environment ---
echo -e "${GREEN}[Step 3/10] Creating Python virtual environment...${NC}"
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -q
if [[ "$DB_BACKEND" == "postgres" ]]; then
    pip install psycopg2-binary -q
fi

# --- 2.4: Generate .env ---
echo -e "${GREEN}[Step 4/10] Generating configuration...${NC}"
SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(50))")
cat > .env << ENVEOF
SECRET_KEY=$SECRET_KEY
DEBUG=False
ALLOWED_HOSTS=$DOMAIN
DOMAIN=$DOMAIN
DB_BACKEND=$DB_BACKEND
DB_NAME=$DB_NAME
DB_USER=$DB_USER
DB_PASSWORD=$DB_PASS
DB_HOST=$DB_HOST
DB_PORT=$DB_PORT
DEFAULT_MAX_RECORDING_SECONDS=$DEFAULT_MAX_REC
DEFAULT_MAX_RECORDINGS_PER_PROJECT=$DEFAULT_MAX_PER_PROJECT
REDIS_URL=redis://127.0.0.1:6379/0
ENVEOF

chmod 600 .env

# --- 2.5: Database setup ---
echo -e "${GREEN}[Step 5/10] Setting up database...${NC}"
if [[ "$DB_BACKEND" == "postgres" ]]; then
    sudo -u postgres psql -c "CREATE USER $DB_USER WITH PASSWORD '$DB_PASS';" 2>/dev/null || true
    sudo -u postgres psql -c "CREATE DATABASE $DB_NAME OWNER $DB_USER;" 2>/dev/null || true
    sudo -u postgres psql -c "ALTER USER $DB_USER WITH PASSWORD '$DB_PASS';" 2>/dev/null || true
fi

# --- 2.6: Django setup ---
echo -e "${GREEN}[Step 6/10] Running Django setup...${NC}"
python manage.py migrate --noinput
python manage.py collectstatic --noinput --verbosity 0

# Create superuser
python manage.py shell -c "
from django.contrib.auth import get_user_model
User = get_user_model()
if not User.objects.filter(username='$ADMIN_USER').exists():
    u = User.objects.create_superuser(
        username='$ADMIN_USER',
        email='$ADMIN_EMAIL',
        password='$ADMIN_PASS',
        is_staff=True
    )
    print(f'Superuser {u.username} created.')
else:
    print('Admin user already exists.')
"

# Create default SiteSettings
python manage.py shell -c "
from accounts.models import SiteSettings
s = SiteSettings.load()
s.max_recordings_per_project = $DEFAULT_MAX_PER_PROJECT
s.save()
print('Site settings configured.')
"

# --- 2.7: nginx configuration ---
echo -e "${GREEN}[Step 7/10] Configuring nginx...${NC}"
export DOMAIN INSTALL_PATH
envsubst '${DOMAIN} ${INSTALL_PATH}' < deploy/nginx.conf.template > /etc/nginx/sites-available/vpm
ln -sf /etc/nginx/sites-available/vpm /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default

# Test nginx config
nginx -t

# --- 2.8: SSL setup ---
if [[ "$SETUP_SSL" == "y" || "$SETUP_SSL" == "Y" ]]; then
    echo -e "${GREEN}[Step 8/10] Setting up SSL with Let's Encrypt...${NC}"
    apt-get install -y -qq certbot python3-certbot-nginx > /dev/null
    systemctl reload nginx
    certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "$LE_EMAIL" --redirect
    # Auto-renewal is set up by certbot automatically via systemd timer
else
    echo -e "${YELLOW}[Step 8/10] Skipping SSL setup.${NC}"
    # Modify nginx to listen on port 80 only (no SSL)
    cat > /etc/nginx/sites-available/vpm << 'NGINX_NO_SSL'
upstream gunicorn_backend {
    server 127.0.0.1:8000;
}
upstream daphne_backend {
    server 127.0.0.1:8001;
}
server {
    listen 80;
    server_name _;
    client_max_body_size 500M;
    location /static/ {
        alias INSTALL_PATH_PLACEHOLDER/staticfiles/;
        expires 30d;
    }
    location /protected-media/ {
        internal;
        alias INSTALL_PATH_PLACEHOLDER/media/;
    }
    location /media/thumbnails/ {
        alias INSTALL_PATH_PLACEHOLDER/media/thumbnails/;
        expires 7d;
    }
    location /ws/ {
        proxy_pass http://daphne_backend;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 86400;
    }
    location / {
        proxy_pass http://gunicorn_backend;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_connect_timeout 300;
        proxy_send_timeout 300;
        proxy_read_timeout 300;
    }
    add_header X-Frame-Options "DENY" always;
    add_header X-Content-Type-Options "nosniff" always;
}
NGINX_NO_SSL
    sed -i "s|INSTALL_PATH_PLACEHOLDER|$INSTALL_PATH|g" /etc/nginx/sites-available/vpm
fi

# --- 2.9: systemd services ---
echo -e "${GREEN}[Step 9/10] Configuring system services...${NC}"

# Gunicorn service
cp deploy/gunicorn.service /etc/systemd/system/vpm-gunicorn.service
sed -i "s|/var/www/vpm|$INSTALL_PATH|g" /etc/systemd/system/vpm-gunicorn.service
sed -i "s|--workers 3|--workers $WORKERS|g" /etc/systemd/system/vpm-gunicorn.service

# Daphne service
cp deploy/daphne.service /etc/systemd/system/vpm-daphne.service
sed -i "s|/var/www/vpm|$INSTALL_PATH|g" /etc/systemd/system/vpm-daphne.service

# Set permissions
echo -e "${GREEN}[Step 10/10] Setting permissions and starting services...${NC}"
mkdir -p "$INSTALL_PATH/media/videos" "$INSTALL_PATH/media/thumbnails" "$INSTALL_PATH/media/temp"
chown -R www-data:www-data "$INSTALL_PATH"
chmod -R 755 "$INSTALL_PATH"
chmod 600 "$INSTALL_PATH/.env"

# Enable and start services
systemctl daemon-reload
systemctl enable redis-server nginx vpm-gunicorn vpm-daphne
systemctl restart redis-server
systemctl restart nginx
systemctl restart vpm-gunicorn
systemctl restart vpm-daphne

# --- 2.11: Firewall ---
echo -e "${GREEN}[Step 11] Configuring firewall...${NC}"
if command -v ufw &> /dev/null; then
    ufw allow 22/tcp comment 'SSH'       > /dev/null 2>&1 || true
    ufw allow 80/tcp comment 'HTTP'      > /dev/null 2>&1 || true
    ufw allow 443/tcp comment 'HTTPS'    > /dev/null 2>&1 || true
    echo "y" | ufw enable               > /dev/null 2>&1 || true
    echo "  Firewall configured (SSH, HTTP, HTTPS allowed)."
else
    apt-get install -y -qq ufw > /dev/null
    ufw allow 22/tcp comment 'SSH'       > /dev/null 2>&1 || true
    ufw allow 80/tcp comment 'HTTP'      > /dev/null 2>&1 || true
    ufw allow 443/tcp comment 'HTTPS'    > /dev/null 2>&1 || true
    echo "y" | ufw enable               > /dev/null 2>&1 || true
    echo "  Firewall configured (SSH, HTTP, HTTPS allowed)."
fi

# --- 2.12: Automatic security updates ---
echo -e "${GREEN}[Step 12] Enabling automatic security updates...${NC}"
apt-get install -y -qq unattended-upgrades > /dev/null
dpkg-reconfigure -f noninteractive unattended-upgrades 2>/dev/null || true

# --- 2.13: Redis security (bind localhost only, no external access) ---
if [[ -f /etc/redis/redis.conf ]]; then
    sed -i 's/^# *bind 127.0.0.1/bind 127.0.0.1/' /etc/redis/redis.conf
    systemctl restart redis-server
fi

# ============================================================
# Done!
# ============================================================

echo ""
echo -e "${BLUE}============================================${NC}"
echo -e "${GREEN}  Installation Complete!${NC}"
echo -e "${BLUE}============================================${NC}"
echo ""
if [[ "$SETUP_SSL" == "y" || "$SETUP_SSL" == "Y" ]]; then
    echo -e "  URL:      ${GREEN}https://$DOMAIN${NC}"
else
    echo -e "  URL:      ${GREEN}http://$DOMAIN${NC}"
fi
echo -e "  Admin:    ${GREEN}$ADMIN_USER${NC}"
echo -e "  Path:     ${GREEN}$INSTALL_PATH${NC}"
echo ""
echo -e "  ${YELLOW}Services:${NC}"
echo -e "    systemctl status vpm-gunicorn"
echo -e "    systemctl status vpm-daphne"
echo -e "    systemctl status nginx"
echo -e "    systemctl status redis-server"
echo ""
echo -e "  ${YELLOW}Logs:${NC}"
echo -e "    journalctl -u vpm-gunicorn -f"
echo -e "    journalctl -u vpm-daphne -f"
echo ""
echo -e "  ${YELLOW}Admin dashboard:${NC}"
if [[ "$SETUP_SSL" == "y" || "$SETUP_SSL" == "Y" ]]; then
    echo -e "    https://$DOMAIN/accounts/dashboard/"
else
    echo -e "    http://$DOMAIN/accounts/dashboard/"
fi
echo ""
