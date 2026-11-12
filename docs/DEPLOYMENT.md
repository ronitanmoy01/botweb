# Deployment Guide

## Production Setup

### 1. Environment Configuration

```bash
# Required
MOVIEHUB_ENVIRONMENT=production
MOVIEHUB_SECRET_KEY=<generate-strong-random-key>
MOVIEHUB_DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/moviehub

# Optional
MOVIEHUB_SITE_NAME=MovieHub
MOVIEHUB_MAX_UPLOAD_BYTES=10485760
```

Generate secret key:
```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

### 2. Database Setup

```bash
# Create PostgreSQL database
createdb moviehub

# Run migrations
alembic upgrade head

# Create admin user
python -m app.scripts.create_admin
```

### 3. Application Server (uvicorn)

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 4
```

### 4. Reverse Proxy (nginx)

```nginx
server {
    listen 443 ssl http2;
    server_name yourdomain.com;

    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    client_max_body_size 10M;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }

    location /static/ {
        alias /path/to/botweb/static/;
        expires 30d;
    }

    location /uploads/ {
        alias /path/to/botweb/uploads/;
        expires 7d;
    }
}
```

### 5. Process Manager (systemd)

`/etc/systemd/system/moviehub.service`:

```ini
[Unit]
Description=MovieHub Application
After=network.target postgresql.service

[Service]
Type=simple
User=www-data
WorkingDirectory=/path/to/botweb
Environment="PATH=/path/to/botweb/.venv/bin"
ExecStart=/path/to/botweb/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 4
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable moviehub
sudo systemctl start moviehub
```

## Docker Deployment

`Dockerfile`:

```dockerfile
FROM python:3.12-slim

WORKDIR /app

RUN pip install uv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY . .

CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

`docker-compose.yml`:

```yaml
version: '3.8'

services:
  web:
    build: .
    ports:
      - "8000:8000"
    environment:
      - MOVIEHUB_ENVIRONMENT=production
      - MOVIEHUB_SECRET_KEY=${SECRET_KEY}
      - MOVIEHUB_DATABASE_URL=postgresql+asyncpg://user:pass@db:5432/moviehub
    volumes:
      - ./uploads:/app/uploads
    depends_on:
      - db

  db:
    image: postgres:16
    environment:
      - POSTGRES_DB=moviehub
      - POSTGRES_USER=user
      - POSTGRES_PASSWORD=pass
    volumes:
      - pgdata:/var/lib/postgresql/data

volumes:
  pgdata:
```

## Security Checklist

- [ ] Strong secret key set
- [ ] HTTPS enabled
- [ ] Firewall configured
- [ ] Database password secured
- [ ] File permissions correct (644 code, 600 .env)
- [ ] Regular backups configured
- [ ] Error monitoring set up
- [ ] Keep dependencies updated

## Backup

```bash
# Database
pg_dump moviehub > backup_$(date +%Y%m%d).sql

# Uploads
tar -czf uploads_$(date +%Y%m%d).tar.gz uploads/
```

## Monitoring

- Check logs: `journalctl -u moviehub -f`
- Monitor disk space for uploads/
- Database connection pool tuning
- Consider Redis for session storage
