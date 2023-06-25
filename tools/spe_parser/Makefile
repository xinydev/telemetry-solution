# SPDX-License-Identifier: Apache-2.0
#
# Copyright (C) Arm Ltd. 2023

.PHONY: test
test:
	@tox

.PHONY: install
install:
	@pip3 install .

.PHONY: lint
lint:
	@pre-commit run --show-diff-on-failure --all-files
