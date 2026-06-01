.PHONY: install install-dev test lint format format-check run-backend run-bot migrations

install:
	python3 -m pip install -r requirements.txt

install-dev:
	python3 -m pip install -r requirements-dev.txt

test:
	python3 -B -m pytest -q -p no:cacheprovider

lint:
	python3 -m ruff check .

format:
	python3 -m ruff format .

format-check:
	python3 -m ruff format --check .

run-backend:
	python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000

run-bot:
	python3 telegram_bot/listener.py

migrations:
	./scripts/apply_migrations.sh
