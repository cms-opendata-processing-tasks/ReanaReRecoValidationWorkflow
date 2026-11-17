#!/bin/bash
# What a step rule runs, in the workspace: one cmsRun step of one job.
#
#   run_step.sh --step reco --job 1 --events 500 --plan job_inputs.txt --mgm root://eoscms.cern.ch
#   run_step.sh --step pat --job 1 --events 500 --input file:results/reco_1.root
#       [--input <file or URL>]      first step: instead of the plan's file (e.g. a staged copy)
#       [--cache-file results/cache/job_1.txt]   skip cmsRun if the cache has the output
#       [--reana-compat 0.9]         REANA 0.9.4 workarounds (reana094_compat.sh)
#
# The first step reads its file and lumi ranges from its line of the plan
# (plan_inputs.py). Writes results/<step>_<job>.root, its log in
# step_logs/<step>_<job>.log, time, memory and hardware in results/metrics/,
# and results/<step>_<job>_confirm.txt when done.
set -e

PLAN=""
INPUT=""
MGM=""
CACHE_FILE=""
COMPAT=""
while [ $# -gt 0 ]; do
    case $1 in
        --step) STEP=$2 ;;
        --job) JOB=$2 ;;
        --events) EVENTS=$2 ;;
        --plan) PLAN=$2 ;;
        --input) INPUT=$2 ;;
        --mgm) MGM=$2 ;;
        --cache-file) CACHE_FILE=$2 ;;
        --reana-compat) COMPAT=$2 ;;
        *) echo "Unknown argument $1" >&2; exit 2 ;;
    esac
    shift 2
done
: "${STEP:?--step}" "${JOB:?--job}" "${EVENTS:?--events}"

SCRIPTS=$(cd "$(dirname "$0")" && pwd)
CONFIRM=results/${STEP}_${JOB}_confirm.txt
STEP_LOG=step_logs/${STEP}_${JOB}.log
mkdir -p results/metrics step_logs
# A crashed cmsRun's core file is several GB.
ulimit -c 0
# Conditions (Frontier) from batch nodes go through the CERN proxy.
export FRONTIER_PROXY=${FRONTIER_PROXY:-http://ca-proxy.cern.ch:3128}

if [ "$COMPAT" = "0.9" ]; then
    # shellcheck source=reana094_compat.sh
    source "$SCRIPTS/reana094_compat.sh"
    compat_begin
fi

finish() {   # finish <exit status>
    if [ "$COMPAT" = "0.9" ]; then compat_end; fi
    exit "$1"
}

fail() {   # fail <message>
    echo "[ERROR] $1"
    if [ "$COMPAT" = "0.9" ]; then
        compat_fail "$1"
        finish 0
    fi
    finish 1
}

# Cache (cache_lookup): "skip" or "restored" means the output is taken care of.
if [ -n "$CACHE_FILE" ] && [ -f "$CACHE_FILE" ]; then
    CACHE_STATUS=$(sed -n "s/^$STEP \([a-z]*\) .*/\1/p" "$CACHE_FILE")
    if [ -n "$CACHE_STATUS" ] && [ "$CACHE_STATUS" != "run" ]; then
        echo "$STEP of job $JOB: $CACHE_STATUS from the cache, not running cmsRun"
        echo "$STEP step $CACHE_STATUS from cache" > "$CONFIRM"
        finish 0
    fi
fi

LUMIS=""
if [ -n "$PLAN" ]; then
    # This job's line of the plan: "<path>[<TAB><lumi ranges>]".
    LINE=$(sed -n "${JOB}p" "$PLAN")
    [ -n "$LINE" ] || fail "No input for job $JOB in $PLAN"
    LUMIS=$(echo "$LINE" | cut -s -f2)
    INPUT=${INPUT:-$MGM/$(echo "$LINE" | cut -f1)}
fi

# The CPU vendor can change the output (docs/known-issues.md): record it.
HOST_FILE=results/metrics/${STEP}_${JOB}.host
printf '%s\t%s\t%s\t%s\tran\n' "$(hostname)" "$(grep -m1 '^vendor_id' /proc/cpuinfo | cut -d: -f2 | xargs)" \
    "$(grep -m1 '^model name' /proc/cpuinfo | cut -d: -f2 | xargs)" "$(nproc)" > "$HOST_FILE"

# The work area goes in its own temporary directory, removed even after a failure.
WORK_TMP=$(mktemp -d)
STATUS=0
TMPDIR=$WORK_TMP /usr/bin/time -f "%e\t%M" -o "results/metrics/${STEP}_${JOB}.tsv" \
    bash "$SCRIPTS/cmsrun_step.sh" --step "$STEP" --job "$JOB" --recipe "recipe/$STEP.sh" --input "$INPUT" \
    --events "$EVENTS" --lumis "$LUMIS" --threads 1 > "$STEP_LOG" 2>&1 || STATUS=$?
rm -rf "$WORK_TMP"
cat "$STEP_LOG"

[ "$STATUS" = "0" ] || fail "$STEP of job $JOB failed with status $STATUS, see $STEP_LOG"
echo "$STEP step done" > "$CONFIRM"
finish 0
