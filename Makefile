SHELL=/bin/zsh

all: lint test

lint: ruff-check ruff-format

test:
	uv run pytest

ruff-format:
	uv tool run ruff format

ruff-check:
	uv tool run ruff check --fix
