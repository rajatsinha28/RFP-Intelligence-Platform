.PHONY: dev down logs test test-live lint typecheck eval

dev:
	docker compose up --build

down:
	docker compose down

logs:
	docker compose logs -f api worker

test:
	pytest --cov=app --cov-report=term-missing

test-live:
	pytest -m live -v

lint:
	ruff check app tests evaluation
	ruff format --check app tests evaluation

typecheck:
	mypy app evaluation

eval:
	python -m evaluation.run --dataset evaluation/datasets/example.json

