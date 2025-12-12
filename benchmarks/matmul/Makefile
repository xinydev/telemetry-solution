CC ?= gcc
CFLAGS ?= -O3 -std=c11 -Wall -Wextra -pedantic
LDFLAGS ?=

TARGETS := matmul_baseline matmul_ikj matmul_ikj_blocked
SOURCES := $(addsuffix .c,$(TARGETS))

all: $(TARGETS)

$(TARGETS): %: %.c
	$(CC) $(CFLAGS) $< -o $@ $(LDFLAGS)

clean:
	rm -f $(TARGETS)

.PHONY: all clean
