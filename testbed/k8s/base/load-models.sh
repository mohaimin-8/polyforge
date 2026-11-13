#!/bin/sh
# Loads the pinned model files of models.lock into the models volume.
#
# For every set named in MODEL_SETS: download each file unless a verified copy
# is already there, check its size and sha256 against the lock, then write
# $DEST/.ready/<set> containing the sha256 of the set's lock lines. Pods wait
# on that marker (init container `wait-model`), so a model server never starts
# on a partial, corrupted or stale file, and the check costs them nothing at
# scale-up time: the hashing happened once, here.
#
# Runs in the llama.cpp server image (Ubuntu 24.04: sh, curl, sha256sum, awk).
#   MODEL_SETS="small mid embedder" LOCK=/config/models.lock DEST=/models load-models.sh
set -eu

LOCK="${LOCK:-/config/models.lock}"
DEST="${DEST:-/models}"
SETS="${MODEL_SETS:?MODEL_SETS must name the sets to load, e.g. small mid embedder}"
TAB="$(printf '\t')"

sha_of() {
    sha256sum "$1" | cut -d' ' -f1
}

fetch() { # path sha256 bytes url
    target="$DEST/$1"
    if [ -f "$target" ] && [ "$(sha_of "$target")" = "$2" ]; then
        echo "ok (already present) $1"
        return 0
    fi
    mkdir -p "$(dirname "$target")"
    part="$target.part"
    rm -f "$part"
    curl --fail --silent --show-error --location --retry 5 --retry-delay 5 \
        --connect-timeout 30 --output "$part" "$4"
    size="$(stat -c %s "$part")"
    if [ "$size" != "$3" ]; then
        echo "FATAL size mismatch for $1: got $size bytes, lock says $3" >&2
        rm -f "$part"
        return 1
    fi
    got="$(sha_of "$part")"
    if [ "$got" != "$2" ]; then
        echo "FATAL sha256 mismatch for $1: got $got, lock says $2" >&2
        rm -f "$part"
        return 1
    fi
    mv "$part" "$target"
    echo "ok (downloaded, verified) $1"
}

mkdir -p "$DEST/.ready"
for set in $SETS; do
    lines="$(grep -v '^#' "$LOCK" | awk -F "$TAB" -v s="$set" '$1 == s')"
    if [ -z "$lines" ]; then
        echo "FATAL models.lock has no entries for set '$set'" >&2
        exit 2
    fi
    rm -f "$DEST/.ready/$set"
    printf '%s\n' "$lines" | while IFS="$TAB" read -r _set path sha bytes url; do
        fetch "$path" "$sha" "$bytes" "$url"
    done
    digest="$(printf '%s\n' "$lines" | sha256sum | cut -d' ' -f1)"
    printf '%s\n' "$digest" > "$DEST/.ready/$set.tmp"
    mv "$DEST/.ready/$set.tmp" "$DEST/.ready/$set"
    echo "set $set ready: $digest"
done
