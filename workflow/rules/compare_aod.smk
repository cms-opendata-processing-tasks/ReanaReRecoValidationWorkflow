# AOD comparison: our AOD and the matching official AOD events are flattened
# by aod_dump.py (FWLite, in the final step's release) into NanoAOD-style
# trees, then compared like NanoAOD.

FINAL_RECIPE = "recipe/" + cfg.final_step + ".sh"

rule dump_ours:
    input:
        expand(step_done(cfg.final_step), jobIndex=cfg.jobs),
        recipe=FINAL_RECIPE
    output:
        "results/ours_flat.root"
    params:
        files=" ".join("results/%s_%d.root" % (cfg.final_step, i) for i in cfg.jobs)
    container:
        BASE_CONTAINER
    shell:
        """
        for f in {params.files}; do echo "$(pwd)/$f"; done > ours_files.txt    # FWLite opens them from its area
        bash workflow/scripts/aod_dump.sh {input.recipe} ours_files.txt {output}
        """

rule select_reference_aod:
    input:
        ours="results/ours_flat.root",
        reference_urls="reference_urls.txt"
    output:
        files="results/reference_files.txt",
        events="results/reference_events.txt"
    container:
        PYTHON_CONTAINER
    shell:
        """
        set -e
        source workflow/scripts/python_libs.sh uproot awkward numpy xrootd fsspec-xrootd
        python3 workflow/scripts/select_reference_aod.py {input.ours} {input.reference_urls} {output.files} {output.events}
        """

rule dump_reference:
    input:
        files="results/reference_files.txt",
        events="results/reference_events.txt",
        recipe=FINAL_RECIPE
    output:
        "results/reference_matched.root"
    container:
        BASE_CONTAINER
    shell:
        "bash workflow/scripts/aod_dump.sh {input.recipe} {input.files} {output} {input.events}"

rule compare_aod:
    input:
        ours="results/ours_flat.root",
        reference="results/reference_matched.root"
    output:
        branch_report="results/branch_comparison.csv",
        summary="results/comparison_summary.txt",
        plots=directory("results/plots")
    params:
        eos_results=cfg.eos_path + "/results"
    container:
        PYTHON_CONTAINER
    shell:
        """
        set -e
        source workflow/scripts/python_libs.sh uproot awkward numpy pandas scipy matplotlib
        python3 workflow/scripts/Event_comparison.py {input.ours} {input.reference} {output.branch_report} {output.summary}
        python3 workflow/scripts/Plotting.py {input.ours} {input.reference} {output.branch_report} {output.plots}
        mkdir -p {params.eos_results}/plots
        cp {input.ours} {input.reference} {output.branch_report} {output.summary} {params.eos_results}/
        cp -r {output.plots}/. {params.eos_results}/plots/
        """

METRICS_UPSTREAM = ["results/branch_comparison.csv"]
