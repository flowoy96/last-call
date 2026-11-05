# Contributing

Thanks for your interest! Last Call is meant to stay small. Bug fixes and focused improvements are
very welcome. For bigger features, please open an issue first so we can discuss whether they fit.

## Development

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt pytest aiosmtpd
pytest -q

# fake Jellyfin + Maintainerr API with made-up titles and generated posters
python scripts/demo_server.py 8899 &
# SMTP sink that stores mails as .eml files
python scripts/smtp_sink.py ./mails &
LASTCALL_DATA=./data uvicorn app.main:app --reload --port 8080
```

Then open http://localhost:8080, create the admin and use `http://127.0.0.1:8899` as the Jellyfin and
Maintainerr URL, `127.0.0.1:2525` without encryption as SMTP.

## Guidelines

- Use [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `docs:`, ...).
- Keep dependencies minimal; no frontend build step.
- Never commit secrets, real server URLs, or copyrighted artwork (use the generated demo posters).
- Make sure `pytest` passes and the Docker image builds.
