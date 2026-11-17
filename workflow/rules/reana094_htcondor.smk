# REANA 0.9.4 (reana.cern.ch) HTCondor workarounds, on with reana_compat: "0.9".
# Delete this file, scripts/reana094_compat.sh and their include once REANA
# 0.95 runs there; the Snakefile's hook defaults then apply. What each part
# works around: README.md ("REANA 0.9.4 workarounds") and docs/known-issues.md.

# 1. HTCondor outputs reach the workspace ~12 s after the job is reported
#    finished, longer than Snakemake's 3 s default wait, which REANA can't set.
if hasattr(workflow, "_latency_wait"):
    workflow._latency_wait = 120

# 2. The image unpacked on CVMFS (Singularity), not Docker: 0.9.4 can't request
#    memory, and UL reco (4-5 GB) crashes in Docker's 2 GB.
STEP_CONTAINER = "/cvmfs/unpacked.cern.ch/" + cfg.cmssw_image + ("" if ":" in cfg.cmssw_image else ":latest")
STEP_RESOURCES = {
    "compute_backend": "htcondorcern",
    "htcondor_max_runtime": cfg.htcondor_max_runtime,
    "kerberos": True,
    "unpacked_img": True,
}

# 3. A failed HTCondor job returns nothing, so steps exit 0 with FAILED in their
#    confirm file (run_step.sh --reana-compat) and check_step fails the run.
#    Each job also deletes the files it didn't write before it ends.
RUN_STEP_COMPAT_ARGS = "--reana-compat 0.9"

def step_done(step):
    return ["results/" + step + "_{jobIndex}_confirm.txt", "results/checked/" + step + "_{jobIndex}.txt"]

rule check_step:
    input:
        "results/{step}_{jobIndex}_confirm.txt"
    output:
        "results/checked/{step}_{jobIndex}.txt"
    wildcard_constraints:
        step="|".join(cfg.steps)
    container:
        PYTHON_CONTAINER
    shell:
        """
        set -e
        if grep -q "^FAILED" {input}; then
            cat {input}
            echo "==> Log of {wildcards.step} for job {wildcards.jobIndex}:"
            cat step_logs/{wildcards.step}_{wildcards.jobIndex}.log 2>/dev/null || echo "(none: it didn't run)"
            exit 1
        fi
        mkdir -p results/checked
        cp {input} {output}
        """

# 4. HTCondor jobs get no Kerberos ticket, so they can't read CMS EOS: a
#    Kubernetes job copies each job's events into the workspace first.
STAGED_INPUT = "inputs/job_{jobIndex}.root"

def first_step_input():
    return "--input file:" + STAGED_INPUT.replace("{jobIndex}", "{wildcards.jobIndex}")

def extra_step_inputs(step):
    return [STAGED_INPUT] if step == cfg.first_step else []

rule stage_input:
    input:
        JOB_INPUTS,
        "results/chain/ready_{jobIndex}.txt",
        "recipe/" + cfg.first_step + ".sh",
        ["results/cache/job_{jobIndex}.txt"] if cfg.cache_dir else []
    output:
        STAGED_INPUT
    params:
        cache="--cache-file results/cache/job_{jobIndex}.txt" if cfg.cache_dir else ""
    container:
        BASE_CONTAINER
    resources:
        kerberos=True
    shell:
        """
        bash workflow/scripts/stage_input.sh --job {wildcards.jobIndex} --step {cfg.first_step} --plan {JOB_INPUTS} \\
            --mgm {cfg.eos_mgm_url} --events {cfg.events_per_job} --output {output} {params.cache}
        """

rule unstage_input:
    input:
        "results/" + cfg.first_step + "_{jobIndex}_confirm.txt",
        STAGED_INPUT
    output:
        "results/staged/unstaged_{jobIndex}.txt"
    container:
        PYTHON_CONTAINER
    shell:
        """
        rm -f inputs/job_{wildcards.jobIndex}.root
        mkdir -p results/staged
        echo "removed the staged input of job {wildcards.jobIndex}" > {output}
        """

# 5. One job chain at a time: REANA 0.9.4 writes each HTCondor job's command
#    into one shared workspace file, and a finishing job's returned copy of it
#    can make the job being submitted run the finished job's command. Job k's
#    staging waits for job k-1's last checked step (plain files: reana-client
#    0.9 can't send input functions).
for _job in cfg.jobs:
    rule:
        name: "chain_ready_%d" % _job
        input:
            JOB_INPUTS if _job == 1 else "results/checked/%s_%d.txt" % (cfg.final_step, _job - 1)
        output:
            "results/chain/ready_%d.txt" % _job
        container:
            PYTHON_CONTAINER
        shell:
            "mkdir -p results/chain && touch {output}"

COMPAT_TARGETS = [expand("results/staged/unstaged_{jobIndex}.txt", jobIndex=cfg.jobs)]
