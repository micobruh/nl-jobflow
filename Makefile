PYTHON ?= python
PROFILE_ARG := $(if $(PROFILE),--profile $(PROFILE),)

.PHONY: preflight doctor next scan prune jobs accepted general-cv general-cvs reports test

preflight:
	$(PYTHON) jobflow.py $(PROFILE_ARG) preflight

doctor:
	$(PYTHON) jobflow.py $(PROFILE_ARG) doctor

next:
	$(PYTHON) jobflow.py $(PROFILE_ARG) next

scan:
	$(PYTHON) jobflow.py $(PROFILE_ARG) scan

prune:
	$(PYTHON) jobflow.py $(PROFILE_ARG) prune

jobs:
	$(PYTHON) jobflow.py $(PROFILE_ARG) jobs --status active

accepted:
	$(PYTHON) jobflow.py $(PROFILE_ARG) jobs --status active --workflow-status accepted

general-cv:
	@test -n "$(TITLE)" || (echo 'usage: make general-cv TITLE="TARGET ROLE"' >&2; exit 2)
	$(PYTHON) jobflow.py $(PROFILE_ARG) general-cv --title "$(TITLE)"

general-cvs:
	$(PYTHON) jobflow.py $(PROFILE_ARG) general-cvs

reports:
	$(PYTHON) jobflow.py $(PROFILE_ARG) jobs --status active
	$(PYTHON) jobflow.py $(PROFILE_ARG) lead-report
	$(PYTHON) jobflow.py $(PROFILE_ARG) marketplace-report
	$(PYTHON) jobflow.py $(PROFILE_ARG) source-health
	$(PYTHON) jobflow.py $(PROFILE_ARG) role-gap-report
	$(PYTHON) jobflow.py $(PROFILE_ARG) outcome-report

test:
	$(PYTHON) -m unittest discover -s tests -q
