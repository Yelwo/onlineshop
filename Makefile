PY := $(CURDIR)/.venv/bin/python

.DEFAULT_GOAL := help
.PHONY: help install up down test build-orders

help:  ## show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install:  ## install pricing + orders + test deps into .venv
	$(PY) -m pip install -e "pricing[test]" -e "orders[test]"

up:  ## start the whole stack (pricing first, then build orders against it, then start orders)
	docker compose up -d --wait pricing
	$(MAKE) build-orders
	docker compose up -d

down:  ## stop the stack
	docker compose down

build: ## build the whole project
	docker compose build --no-cache pricing
	$(MAKE) up

test:  ## run pricing + orders tests (each invoked from its own dir so `python -m`'s
       ## CWD insertion can't shadow the `pricing` editable install with the
       ## same-named source directory)
	cd $(CURDIR)/pricing && $(PY) -m pytest
	cd $(CURDIR)/orders && $(PY) -m pytest

build-orders:  ## build orders + pricing graft (index token auto-captured from the running gateway)
	@GUID=$$(docker compose logs pricing 2>/dev/null | grep -oE "GUID: [0-9a-f-]{36}" | tail -1 | awk '{print $$2}'); \
	if [ -z "$$GUID" ]; then echo "No pricing GUID in logs — start the gateway first: make up"; exit 1; fi; \
	URL="https://grft.dev/simple/$${GUID}__free"; \
	echo "graft index: $$URL"; \
	GRAFT_INDEX_URL="$$URL" docker compose build orders
