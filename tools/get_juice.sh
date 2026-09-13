#!/bin/sh
# Fetch juice, the (de)compiler for elf's AI5 .MES bytecode.
#
# It is NOT vendored in this repository on purpose: the upstream project ships no licence
# (GitHub reports `license: null`), which means all rights reserved and no permission to
# redistribute it. So it is cloned here at a pinned commit instead, and the pin is what makes
# a build reproducible rather than "whatever upstream looks like today".
#
#   tools/get_juice.sh          # clone at the pinned commit
#   JUICE_REF=main tools/get_juice.sh   # or track upstream yourself
set -eu

REPO="https://github.com/tomyun/juice.git"
REF="${JUICE_REF:-81c407acab1640c5a19f0e3c7b705f5c334f1fd0}"
DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/juice"

if [ -d "$DIR/.git" ]; then
    echo "juice already present in $DIR"
    git -C "$DIR" fetch --quiet origin "$REF" 2>/dev/null || git -C "$DIR" fetch --quiet origin
else
    echo "cloning juice into $DIR"
    git clone --quiet "$REPO" "$DIR"
fi

git -C "$DIR" checkout --quiet "$REF"
echo "juice at $(git -C "$DIR" rev-parse --short HEAD)"

# Racket packages juice needs. Installed per-user, so no root.
for pkg in ansi-color bitsyntax parsack; do
    raco pkg show "$pkg" >/dev/null 2>&1 || raco pkg install --auto --user "$pkg"
done
echo "racket packages ready"
