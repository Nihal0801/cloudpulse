.PHONY: demo test lint format terraform-check

demo:
	python3 -m cloudpulse.local

test:
	python3 -m coverage run -m unittest discover -s tests -v
	python3 -m coverage report

lint:
	python3 -m ruff check .
	python3 -m ruff format --check .

format:
	python3 -m ruff check --fix .
	python3 -m ruff format .

terraform-check:
	terraform -chdir=infra fmt -check
	terraform -chdir=infra init -backend=false
	terraform -chdir=infra validate
	terraform -chdir=infra test
