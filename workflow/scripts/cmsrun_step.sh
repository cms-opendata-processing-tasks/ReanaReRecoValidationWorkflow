#!/bin/bash
# Runs one cmsRun step of one job in a fresh CMSSW area and copies its output
# to <out_dir>/<step>_<job>.root. Also usable by hand, e.g. on lxplus:
#
#   cmsrun_step.sh --step reco --job 1 --recipe recipe/reco.sh \
#       --input root://eoscms.cern.ch//eos/cms/store/.../file.root --events 10 \
#       [--lumis 278820:1-278820:20] [--threads 1] [--out-dir results]
#
# The recipe (release, arch, global tag, config) is a recipe/<step>.sh file
# written by resolve_recipe.py. Exits non-zero if cmsRun fails or processes no event.
set -e

THREADS=""
LUMIS=""
OUT_DIR=results
while [ $# -gt 0 ]; do
    case $1 in
        --step) STEP=$2 ;;
        --job) JOB=$2 ;;
        --recipe) RECIPE=$2 ;;
        --input) INPUT=$2 ;;
        --events) EVENTS=$2 ;;
        --lumis) LUMIS=$2 ;;
        --threads) THREADS=$2 ;;
        --out-dir) OUT_DIR=$2 ;;
        *) echo "Unknown argument $1" >&2; exit 2 ;;
    esac
    shift 2
done
: "${STEP:?--step}" "${JOB:?--job}" "${RECIPE:?--recipe}" "${INPUT:?--input}" "${EVENTS:?--events}"

WORKDIR=$(pwd)
SCRIPTS=$(cd "$(dirname "$0")" && pwd)
# A relative file: input is relative to where the script was started.
case $INPUT in
    file:/*) ;;
    file:*) INPUT=file:$WORKDIR/${INPUT#file:} ;;
esac
# shellcheck source=/dev/null
source "$WORKDIR/$RECIPE"

AREA=$(mktemp -d)
cd "$AREA"
echo "$STEP of job $JOB: $RELEASE ($SCRAM_ARCH), global tag $GLOBAL_TAG"
echo "Input: $INPUT, lumis: ${LUMIS:-all}, events: $EVENTS"
# Where it ran: kept for diagnosing float-level differences.
echo "Host: $(hostname), CPU:$(grep -m1 'model name' /proc/cpuinfo | cut -d: -f2-), $(nproc) cores"

setup_release
scram b
eval "$(scramv1 runtime -sh)"
fetch_config

export RUNSTEP_STEP=$STEP RUNSTEP_JOB=$JOB RUNSTEP_INPUT=$INPUT RUNSTEP_EVENTS=$EVENTS \
    RUNSTEP_LUMIS=$LUMIS RUNSTEP_THREADS=$THREADS
cat "$SCRIPTS/customise_step.py" >> "$CONFIG_FILE"
mkdir -p "$WORKDIR/cfg_files" "$WORKDIR/$OUT_DIR/metrics"
cp "$CONFIG_FILE" "$WORKDIR/cfg_files/${STEP}_config_${JOB}.py"

STATUS=0
cmsRun "$CONFIG_FILE" > cmsRun.log 2>&1 || STATUS=$?
OUTPUT=${STEP}_output.root
# Old releases (CMSSW_4_2) report exceptions in another format and can leave
# an output file, so every check applies: no empty output may be used.
FAILURE=""
if [ ! -s "$OUTPUT" ]; then FAILURE="no output file was created"
elif grep -q "Begin Fatal Exception" cmsRun.log; then FAILURE="cmsRun hit a fatal exception"
elif [ "$STATUS" != "0" ]; then FAILURE="cmsRun exited with status $STATUS"
elif ! grep -q "Begin processing the 1st record" cmsRun.log; then FAILURE="cmsRun processed no events"
fi
if [ -n "$FAILURE" ]; then
    echo "[ERROR] $FAILURE; the cmsRun log:"
    cat cmsRun.log
    cd "$WORKDIR" && rm -rf "$AREA"
    exit 1
fi

stat -c%s "$OUTPUT" > "$WORKDIR/$OUT_DIR/metrics/${STEP}_${JOB}.size"
cp "$OUTPUT" "$WORKDIR/$OUT_DIR/${STEP}_${JOB}.root"
sync
cd "$WORKDIR"
rm -rf "$AREA"
echo "$STEP of job $JOB done"
