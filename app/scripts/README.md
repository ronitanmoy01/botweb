# Admin Scripts

## Add Points

Credit points to a user by email:

```bash
uv run python -m app.scripts.add_points user@example.com 100 "Contest winner"
uv run python -m app.scripts.add_points user@example.com 50 "Manual adjustment"
```

The reason is optional (defaults to "Manual credit"). The script:
- Credits the amount to the user's `points_balance` and `points_lifetime`
- Uses an idempotency key so running it twice with the same email/amount/reason is a no-op
- Returns the new lifetime total

## Import IMDb

Import movies and series from IMDb:

```bash
uv run python -m app.scripts.import_imdb https://www.imdb.com/title/tt6751668/
uv run python -m app.scripts.import_imdb tt6751668 tt0944947
```

Each title is saved as published. A title with the same name and release year that already exists is skipped, so re-running is safe.
