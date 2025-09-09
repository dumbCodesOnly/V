# Makefile for Trading Bot Development

.PHONY: help install dev-install format lint type-check security test clean pre-commit-install run-checks all

help: ## Show this help message
	@echo "Available commands:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

install: ## Install production dependencies
	pip install -r requirements.txt

dev-install: ## Install development dependencies
	pip install -r requirements.txt
	pip install -e .[dev]

format: ## Format code with black and isort
	@echo "Running code formatters..."
	black --line-length 88 .
	isort --profile black .
	@echo "✓ Code formatting completed"

lint: ## Run flake8 linting
	@echo "Running flake8 linting..."
	flake8 . --statistics
	@echo "✓ Linting completed"

type-check: ## Run mypy type checking
	@echo "Running mypy type checking..."
	mypy --install-types --non-interactive .
	@echo "✓ Type checking completed"

security: ## Run security scans
	@echo "Running security scans..."
	bandit -r . -x instance,attached_assets,docs -f json -o bandit-report.json
	bandit -r . -x instance,attached_assets,docs
	safety check
	@echo "✓ Security scans completed"

test-imports: ## Test critical module imports
	@echo "Testing critical imports..."
	@python -c "import config; print('✓ Config module imports successfully')"
	@python -c "from api import models; print('✓ Models import successfully')"
	@python -c "from api.app import app; print('✓ Flask app imports successfully')"
	@python -c "from main import app; print('✓ Main app entry point works')"
	@echo "✓ Import tests completed"

clean: ## Clean up generated files
	@echo "Cleaning up generated files..."
	find . -type f -name "*.pyc" -delete
	find . -type d -name "__pycache__" -delete
	find . -type d -name "*.egg-info" -exec rm -rf {} +
	find . -name ".mypy_cache" -exec rm -rf {} +
	find . -name ".pytest_cache" -exec rm -rf {} +
	rm -f bandit-report.json safety-report.json pip-audit-report.json
	@echo "✓ Cleanup completed"

pre-commit-install: ## Install pre-commit hooks
	@echo "Installing pre-commit hooks..."
	pre-commit install
	@echo "✓ Pre-commit hooks installed"

run-checks: format lint type-check security test-imports ## Run all code quality checks
	@echo "🎉 All checks completed successfully!"

all: dev-install pre-commit-install run-checks ## Full setup and validation

# Development server commands
dev: ## Run development server
	python main.py

prod: ## Run production server with gunicorn
	gunicorn --bind 0.0.0.0:5000 --reuse-port main:app

# Docker commands (if needed)
docker-build: ## Build Docker image
	docker build -t trading-bot .

docker-run: ## Run Docker container
	docker run -p 5000:5000 trading-bot