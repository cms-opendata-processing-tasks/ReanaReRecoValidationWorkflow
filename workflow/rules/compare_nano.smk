# NanoAOD comparison: our jobs' flat NanoAOD is merged and compared event by
# event, branch by branch, with the same events of the record's NanoAOD.

rule merge_nano:
    input:
        expand(step_done("nano"), jobIndex=cfg.jobs)
    output:
        "results/nano_merged.root"
    params:
        files=" ".join("results/nano_%d.root" % i for i in cfg.jobs),
        eos_output=cfg.eos_path + "/results/nano_merged.root"
    container:
        CC7_CONTAINER
    shell:
        """
        set -e
        WORKDIR=$(pwd)
        export SCRAM_ARCH=slc7_amd64_gcc700
        source /cvmfs/cms.cern.ch/cmsset_default.sh
        scram p CMSSW CMSSW_10_6_30
        cd CMSSW_10_6_30/src/ && eval $(scramv1 runtime -sh) && cd $WORKDIR
        python workflow/scripts/haddnano.py {output} {params.files}
        mkdir -p $(dirname {params.eos_output})
        cp {output} {params.eos_output}
        """

rule histogram_results:
    input:
        merged_nano="results/nano_merged.root",
        reference_urls="reference_urls.txt"
    output:
        reference="results/reference_matched.root",
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
        source workflow/scripts/python_libs.sh uproot awkward numpy pandas scipy matplotlib xrootd fsspec-xrootd
        python3 workflow/scripts/select_reference.py {input.merged_nano} {input.reference_urls} {output.reference}
        python3 workflow/scripts/Event_comparison.py {input.merged_nano} {output.reference} {output.branch_report} {output.summary}
        python3 workflow/scripts/Plotting.py {input.merged_nano} {output.reference} {output.branch_report} {output.plots}
        mkdir -p {params.eos_results}/plots
        cp {output.reference} {output.branch_report} {output.summary} {params.eos_results}/
        cp -r {output.plots}/. {params.eos_results}/plots/
        """

rule nano_cleanup:
    input:
        "results/nano_merged.root",
        cache_stored("nano")
    output:
        "results/cleanup/nano_cleanup_{jobIndex}.txt"
    container:
        PYTHON_CONTAINER
    shell:
        """
        rm -f results/nano_{wildcards.jobIndex}.root
        mkdir -p results/cleanup
        echo "removed results/nano_{wildcards.jobIndex}.root" > {output}
        """

METRICS_UPSTREAM = expand("results/cleanup/nano_cleanup_{jobIndex}.txt", jobIndex=cfg.jobs)
