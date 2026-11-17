# shellcheck shell=bash
# REANA 0.9.4 HTCondor workarounds for run_step.sh (reana_compat: "0.9").
# Delete with rules/reana094_htcondor.smk once reana.cern.ch runs REANA 0.95.
#
# - A failed HTCondor job returns no files or log to REANA, so a failed step
#   exits 0 with FAILED in its confirm file; check_step then fails the run.
# - Each HTCondor job returns its whole directory, the workspace it was sent
#   included, and HTCondor removes jobs returning more than 1 GB: before the
#   job ends, every file it didn't write is deleted from its directory.
# See docs/known-issues.md.

compat_begin() {
    # Transferred files get the transfer time: a second either side of the
    # marker keeps them older than it and the job's own files newer.
    sleep 1
    touch .reana_job_start
    sleep 1
}

compat_fail() {   # compat_fail <message>
    echo "FAILED: $1" > "$CONFIRM"
}

compat_end() {
    [ -f .reana_job_start ] || return 0
    find . -type f ! -newer .reana_job_start ! -name '_condor_*' \
        ! -name '.job.ad' ! -name '.machine.ad' ! -name '.chirp.config' ! -name '.update.ad' -delete
    rm -f .reana_job_start
    # What the job returns, largest last. Over 1 GB the step is marked failed
    # and its big files dropped, so its log and this list still come back.
    mkdir -p step_logs results
    local sizes=step_logs/${STEP}_${JOB}.sizes
    du -ak . 2>/dev/null | sort -n | tail -25 > "$sizes"
    if [ "$(du -sk . | cut -f1)" -gt 900000 ]; then
        echo "FAILED: $STEP left $(du -sh . | cut -f1) to return, over HTCondor's 1 GB, see $sizes" > "$CONFIRM"
        find . -type f -size +20M -delete
    fi
}
