#!/usr/bin/env bash
# Installs the versioned git hooks (tools/hooks/*) into this clone. Idempotent.
cd "$(dirname "$0")/.." && git config core.hooksPath tools/hooks && chmod +x tools/hooks/* && echo "hooks installed: core.hooksPath=tools/hooks"
