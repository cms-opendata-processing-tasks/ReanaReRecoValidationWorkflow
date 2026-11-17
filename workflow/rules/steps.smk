# The cmsRun steps of the profile's chain (e.g. reco -> pat -> nano), one job
# per input file, run by scripts/run_step.sh from the recipes.

# Each step's release, arch, global tag and config, with their sources:
# recipe/<step>.json, and recipe/<step>.sh for the scripts.
RECIPES = ["recipe/" + step + ".json" for step in cfg.steps] + ["recipe/" + step + ".sh" for step in cfg.steps]
# A profile release table per run range needs the jobs' runs.
RECIPE_NEEDS_RUNS = any(r.get("releases") for r in cfg.step_recipes.values())

rule resolve_recipe:
    input:
        metadata="record_metadata.json",
        provenance=PROVENANCE,
        job_inputs=JOB_INPUTS if RECIPE_NEEDS_RUNS else []
    output:
        RECIPES
    params:
        steps=",".join(cfg.steps),
        step_recipes=json.dumps(cfg.step_recipes),
        override=json.dumps(cfg.recipe_override),
        job_inputs="--job-inputs " + JOB_INPUTS if RECIPE_NEEDS_RUNS else ""
    container:
        PYTHON_CONTAINER
    shell:
        """
        python3 workflow/scripts/resolve_recipe.py {input.metadata} {input.provenance} recipe {params.steps} \\
            --step-recipes '{params.step_recipes}' --override '{params.override}' {params.job_inputs}
        """

def step_inputs(step):
    """What a step reads: the plan for the first step, else the step before."""
    before = cfg.step_before(step)
    return step_done(before) if before else [JOB_INPUTS]

def input_args(step):
    """run_step.sh's input arguments ({wildcards.jobIndex} is filled in by the shell directive)."""
    before = cfg.step_before(step)
    if before:
        return "--input file:results/" + before + "_{wildcards.jobIndex}.root"
    return "--plan " + JOB_INPUTS + " --mgm " + cfg.eos_mgm_url + " " + first_step_input()

def cleanup_inputs(step):
    """A step's output is removed once the next step has used it. Steps two
    apart: the next step only starts once the output before it is gone."""
    i = cfg.steps.index(step)
    return ["results/cleanup/" + cfg.steps[i - 2] + "_cleanup_{jobIndex}.txt"] if i >= 2 else []

for _step in cfg.steps:
    rule:
        name: _step
        input:
            step_inputs(_step),
            cleanup_inputs(_step),
            extra_step_inputs(_step),
            CACHE_JOB,
            "recipe/" + _step + ".sh"
        output:
            temporary("results/" + _step + "_{jobIndex}_confirm.txt")
        params:
            step=_step
        container:
            STEP_CONTAINER
        resources:
            **STEP_RESOURCES
        shell:
            "bash workflow/scripts/run_step.sh --step {params.step} --job {wildcards.jobIndex}"
            " --events " + str(cfg.events_per_job) + " " + input_args(_step)
            + (" --cache-file results/cache/job_{wildcards.jobIndex}.txt" if cfg.cache_dir else "")
            + " " + RUN_STEP_COMPAT_ARGS

# Removes a step's output once the next step is done with it (and it's cached).
for _step in cfg.steps[:-1]:
    _next = cfg.steps[cfg.steps.index(_step) + 1]
    rule:
        name: _step + "_cleanup"
        input:
            step_done(_next),
            cache_stored(_step)
        output:
            "results/cleanup/" + _step + "_cleanup_{jobIndex}.txt"
        params:
            step=_step
        container:
            PYTHON_CONTAINER
        shell:
            """
            rm -f results/{params.step}_{wildcards.jobIndex}.root
            mkdir -p results/cleanup
            echo "removed results/{params.step}_{wildcards.jobIndex}.root" > {output}
            """

STEP_CLEANUPS = [expand("results/cleanup/" + s + "_cleanup_{jobIndex}.txt", jobIndex=cfg.jobs) for s in cfg.steps[:-1]]
