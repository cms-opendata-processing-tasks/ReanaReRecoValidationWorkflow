# Step output cache on EOS (cache_dir; empty disables it), shared between
# runs: a step already run on the same input, lumis, events, recipe and step
# scripts is restored instead of rerun (scripts/step_cache.py).

CACHE_JOB = ["results/cache/job_{jobIndex}.txt"] if cfg.cache_dir else []
# The step scripts: changing them changes every cache key.
STEP_CODE = ["workflow/scripts/run_step.sh", "workflow/scripts/cmsrun_step.sh", "workflow/scripts/customise_step.py"]

def cache_stored(step):
    """A step's output must be stored before its cleanup removes it."""
    return ["results/cache/" + step + "_stored_{jobIndex}.txt"] if cfg.cache_dir else []

CACHE_TARGETS = [expand(cache_stored(step), jobIndex=cfg.jobs) for step in cfg.steps]

if cfg.cache_dir:

    rule cache_lookup:
        input:
            JOB_INPUTS,
            STEP_CODE,
            ["recipe/" + step + ".json" for step in cfg.steps]
        output:
            expand("results/cache/job_{jobIndex}.txt", jobIndex=cfg.jobs)
        params:
            steps=",".join(cfg.steps),
            code=",".join(STEP_CODE)
        container:
            PYTHON_CONTAINER
        shell:
            """
            python3 workflow/scripts/step_cache.py lookup {cfg.cache_dir} {JOB_INPUTS} {cfg.events_per_job} 1 \\
                {params.code} {params.steps} results/cache
            """

    rule cache_store:
        input:
            done=step_done("{step}"),
            job="results/cache/job_{jobIndex}.txt"
        output:
            "results/cache/{step}_stored_{jobIndex}.txt"
        wildcard_constraints:
            step="|".join(cfg.steps)
        container:
            PYTHON_CONTAINER
        shell:
            """
            python3 workflow/scripts/step_cache.py store {cfg.cache_dir} {input.job} {wildcards.step} \\
                results/{wildcards.step}_{wildcards.jobIndex}.root {output}
            """
