#!/bin/bash
# Copies the events one job's first step processes (its lumi ranges, at most
# <events>) into the workspace, as <output>, with cmsRun in the first step's
# release. Runs on Kubernetes, which can read CMS EOS (the /eos/cms mount).
#
#   stage_input.sh --job 1 --step reco --plan job_inputs.txt --mgm root://eoscms.cern.ch \
#       --events 500 --output inputs/job_1.root [--cache-file results/cache/job_1.txt]
set -e

CACHE_FILE=""
while [ $# -gt 0 ]; do
    case $1 in
        --job) JOB=$2 ;;
        --step) STEP=$2 ;;
        --plan) PLAN=$2 ;;
        --mgm) MGM=$2 ;;
        --events) EVENTS=$2 ;;
        --output) OUTPUT=$2 ;;
        --cache-file) CACHE_FILE=$2 ;;
        *) echo "Unknown argument $1" >&2; exit 2 ;;
    esac
    shift 2
done
: "${JOB:?--job}" "${STEP:?--step}" "${PLAN:?--plan}" "${MGM:?--mgm}" "${EVENTS:?--events}" "${OUTPUT:?--output}"

WORKDIR=$(pwd)
mkdir -p "$(dirname "$OUTPUT")"
if [ -n "$CACHE_FILE" ] && [ -f "$CACHE_FILE" ] && ! grep -q "^$STEP run " "$CACHE_FILE"; then
    echo "$STEP of job $JOB comes from the cache: not staging its input"
    touch "$OUTPUT"
    exit 0
fi

LINE=$(sed -n "${JOB}p" "$PLAN")
INPUT_PATH=$(echo "$LINE" | cut -f1)
LUMIS=$(echo "$LINE" | cut -s -f2)
if [ -z "$LUMIS" ]; then
    echo "Staging the whole file $MGM/$INPUT_PATH (no lumi ranges)"
    xrdcp -f "$MGM/$INPUT_PATH" "$OUTPUT"
    ls -l "$OUTPUT"
    exit 0
fi
if [ -f "$INPUT_PATH" ]; then SOURCE=file:$INPUT_PATH; else SOURCE=$MGM/$INPUT_PATH; fi
echo "Staging lumis $LUMIS of $SOURCE"

AREA=$(mktemp -d)
cd "$AREA"
# shellcheck source=/dev/null
source "$WORKDIR/recipe/$STEP.sh"
setup_release
eval "$(scramv1 runtime -sh)"
cat > stage_cfg.py <<CFG
import FWCore.ParameterSet.Config as cms
process = cms.Process("STAGE")
process.source = cms.Source("PoolSource",
    fileNames = cms.untracked.vstring("$SOURCE"),
    lumisToProcess = cms.untracked.VLuminosityBlockRange(*"$LUMIS".split(",")))
process.maxEvents = cms.untracked.PSet(input = cms.untracked.int32($EVENTS))
process.out = cms.OutputModule("PoolOutputModule", fileName = cms.untracked.string("file:$WORKDIR/$OUTPUT"))
process.end = cms.EndPath(process.out)
CFG
STATUS=0
cmsRun stage_cfg.py > stage.log 2>&1 || STATUS=$?
tail -20 stage.log
cd "$WORKDIR"
rm -rf "$AREA"
if [ "$STATUS" != "0" ] || [ ! -s "$OUTPUT" ]; then
    echo "[ERROR] staging the lumis failed (cmsRun status $STATUS)"
    exit 1
fi
ls -l "$OUTPUT"
