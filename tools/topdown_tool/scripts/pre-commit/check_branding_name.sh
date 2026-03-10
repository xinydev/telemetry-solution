#!/usr/bin/env bash

set -euo pipefail

if [ "$#" -eq 0 ]; then
    exit 0
fi

status=0
pattern='(?i)\barm\s+top(?:down|[- ]down)(?:\s+|-)?tool\b'
allowed='Arm Top-Down tool'

for file in "$@"; do
    if [ ! -f "$file" ]; then
        continue
    fi

    matches="$(grep -nHP "$pattern" "$file" || true)"
    if [ -z "$matches" ]; then
        continue
    fi

    invalid_matches="$(printf '%s\n' "$matches" | grep -vF "$allowed" || true)"
    if [ -z "$invalid_matches" ]; then
        continue
    fi

    if [ "$status" -eq 0 ]; then
        echo "Branding check failed: use exact official name '$allowed' for full-name references."
    fi
    printf '%s\n' "$invalid_matches"
    status=1
done

if [ "$status" -ne 0 ]; then
    echo "Allowed full-name spelling: $allowed"
fi

exit "$status"
