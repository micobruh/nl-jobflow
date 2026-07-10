PYTHON ?= python

.PHONY: preflight doctor next scan prune jobs accepted general-cv general-cvs reports test

preflight:
	$(PYTHON) jobflow.py preflight

doctor:
	$(PYTHON) jobflow.py doctor

next:
	$(PYTHON) jobflow.py next

scan:
	$(PYTHON) jobflow.py scan

prune:
	$(PYTHON) jobflow.py prune

jobs:
	$(PYTHON) jobflow.py jobs --status active

accepted:
	$(PYTHON) jobflow.py jobs --status active --workflow-status accepted

general-cv:
	@test -n "$(TITLE)" || (echo 'usage: make general-cv TITLE="Data Scientist"' >&2; exit 2)
	$(PYTHON) jobflow.py general-cv --title "$(TITLE)"

general-cvs:
	$(PYTHON) jobflow.py general-cvs

reports:
	$(PYTHON) jobflow.py jobs --status active
	$(PYTHON) jobflow.py lead-report
	$(PYTHON) jobflow.py marketplace-report
	$(PYTHON) jobflow.py source-health
	$(PYTHON) jobflow.py outcome-report

test:
	$(PYTHON) -m unittest discover -s tests -q
