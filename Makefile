# SPDX-License-Identifier: AGPL-3.0-only
.PHONY: test check run-data run-control

test:
	python -m pytest

check:
	python -m compileall -q src tests
	python -m pytest

run-data:
	python -m portable_ai_gateway data-plane --host 127.0.0.1 --port 8080

run-control:
	python -m portable_ai_gateway control-plane --host 127.0.0.1 --port 8081
