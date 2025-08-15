# SPDX-License-Identifier: Apache-2.0
# Copyright 2023-2025 Arm Limited

.PHONY: test
test:
	@tox

.PHONY: install
install:
	@pip3 install .

.PHONY: lint
lint:
	@pre-commit run --show-diff-on-failure --all-files
