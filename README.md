# MovieHub

A movie and TV series discovery platform with content management built with FastAPI and Jinja2.

## Features

### Content Management
- **Movies**: Full CRUD with posters, backdrops, metadata
- **TV Series**: Series with seasons and episodes
- **Taxonomies**: Genres, categories, languages, countries
- **Status management**: Draft, Published, Archived
- **Exclusive content** flagging

### Public Features
- Browse movies and series
- Filter by genre, category, language
- Search across all content
- Exclusive content section
- Responsive dark theme

### Member Features
- User registration and authentication
- Personal favorites
- Watchlist
- Profile management
- Role-based access control

### Admin Panel
- Movies and series management
- Season and episode management
- Taxonomy administration
- User role management

## Tech Stack

- **Backend**: Python 3.12, FastAPI, SQLAlchemy 2.x async
- **Database**: PostgreSQL (production) / SQLite (dev)
- **Frontend**: Jinja2, HTMX, Tailwind CSS
- **Auth**: Argon2, session-based with CSRF protection
- **Migrations**: Alembic

## Quick Start

```bash
# Install dependencies
uv sync

# Set up environment
cp .env.example .env

# Run migrations
uv run alembic upgrade head

# Create admin user
uv run python -m app.scripts.create_admin

# Start server
uv run uvicorn app.main:app --reload
```

Visit http://localhost:8000

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MOVIEHUB_ENVIRONMENT` | `development` | Environment mode |
| `MOVIEHUB_SECRET_KEY` | (dev placeholder) | **Required in production** |
| `MOVIEHUB_DATABASE_URL` | SQLite | PostgreSQL URL for production |
| `MOVIEHUB_SITE_NAME` | `MovieHub` | Site name |
| `MOVIEHUB_MAX_UPLOAD_BYTES` | 10485760 | Max file upload (10 MB) |

## Project Structure

```
app/
  core/           # Config, database, security
  models/         # SQLAlchemy models
  routers/        # Route handlers
    admin/        # Admin panel
    auth/         # Authentication
    member/       # Member features
    public/       # Public pages
  services/       # Business logic
  dependencies/   # FastAPI dependencies
templates/        # Jinja2 templates
static/           # Static assets
migrations/       # Alembic migrations
tests/            # pytest tests
```

## Testing

```bash
uv run pytest
```

## Production Deployment

See `docs/DEPLOYMENT.md` for complete deployment guide including:
- Production configuration
- PostgreSQL setup
- nginx configuration
- systemd service
- Docker deployment
- Security checklist

## Security

- CSRF protection on all forms
- Argon2 password hashing
- Login attempt throttling
- Role-based access control
- File upload validation

## License

[Your license here]
