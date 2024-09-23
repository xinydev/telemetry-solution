# SPDX-License-Identifier: Apache-2.0
# Copyright (C) Arm Ltd. 2024

SOURCES = $(wildcard *.c)
TARGETS = $(SOURCES:.c=)

CFLAGS = -std=c99 -O2 -Wall -Wextra -pedantic

all: $(TARGETS)

%: %.c
	$(CC) $(CFLAGS) -o $@ $<

clean:
	rm -f $(TARGETS)
