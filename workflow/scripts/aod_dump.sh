#!/bin/bash
# Flattens AOD/RECO events into NanoAOD-style trees with aod_dump.py (FWLite),
# in the release of the given recipe.
#
#   aod_dump.sh <recipe.sh> <files.txt> <output.root> [<events.txt>]
set -e
RECIPE=$1
FILES=$2
OUTPUT=$3
EVENTS=${4:-}

WORKDIR=$(pwd)
AREA=$(mktemp -d)
cd "$AREA"
# shellcheck source=/dev/null
source "$WORKDIR/$RECIPE"
setup_release
eval "$(scramv1 runtime -sh)"
PYTHON=$(command -v python || command -v python3)
# shellcheck disable=SC2086  # EVENTS is optional
$PYTHON "$WORKDIR/workflow/scripts/aod_dump.py" "$WORKDIR/$FILES" "$WORKDIR/$OUTPUT" ${EVENTS:+"$WORKDIR/$EVENTS"}
cd "$WORKDIR"
rm -rf "$AREA"
