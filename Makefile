# Convenience wrappers around scripts/site.py — see README.md.
SITE := ./scripts/site.py

.PHONY: help build check serve open add list deploy
.DEFAULT_GOAL := help

help:            ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk -F':.*?## ' '{printf "  make %-10s %s\n", $$1, $$2}'

build:           ## Regenerate index.html from data/
	@$(SITE) build

check:           ## Validate data/ and index.html without writing anything
	@$(SITE) check

serve: build     ## Preview at http://127.0.0.1:8000
	@$(SITE) serve

open: build      ## Preview and open a browser window
	@$(SITE) serve --open

add:             ## Add a publication: make add BIB=paper.bib PDF=... CODE=...
	@test -n "$(BIB)" || { echo "usage: make add BIB=paper.bib [PDF=url] [CODE=url]"; exit 1; }
	@$(SITE) pub add --bib "$(BIB)" $(if $(PDF),--pdf "$(PDF)") $(if $(CODE),--code "$(CODE)")

list:            ## List publications in display order
	@$(SITE) pub list

deploy: check    ## Commit and push the site
	@$(SITE) deploy -m "$(or $(M),Update site)" --push
