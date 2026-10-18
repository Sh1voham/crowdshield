#!/usr/bin/env bash
# scripts/ui_demo.sh {live|video|replay NAME|synthetic NAME|sim} — same as scripts/demo.sh
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/demo.sh" "$@"
