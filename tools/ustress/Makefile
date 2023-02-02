# SPDX-License-Identifier: Apache-2.0
# Copyright (C) Arm Ltd. 2022

HEADERS = $(wildcard *.h)
SOURCES = $(wildcard *.c)
TARGETS = $(SOURCES:.c=)

CFLAGS = -std=c11 -O2 -g -Wall -pedantic
LINKER_FLAGS = -lm

# make CPU=NEOVERSE-V1 --> gcc -DCPU_NEOVERSE_V1
ifdef CPU
CPUINFO = $(shell echo $(CPU) | tr '[a-z\-]' '[A-Z_]')
else
CPUINFO = NONE
endif
CFLAGS += -DCPU_$(CPUINFO)

.PHONY: clean all

all: $(TARGETS)

clean:
	rm -f $(TARGETS)

%: %.c $(HEADERS)
	$(CC) $(CFLAGS) -o $@ $< $(LINKER_FLAGS)
