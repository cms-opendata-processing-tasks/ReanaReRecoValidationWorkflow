# The record's metadata, its RAW input and the plan of what each job processes.

rule fetch_metadata:
    output:
        temporary("record_metadata.json")
    container:
        OPENDATACLIENT_CONTAINER
    shell:
        "cernopendata-client get-metadata --recid {cfg.record_id} > {output}"

if cfg.resolve_raw:

    # Files of the record's RAW parent dataset in DAS that are on CMS EOS disk,
    # as /eos/cms paths. Needs the CMS grid proxy secret.
    rule resolve_raw:
        input:
            metadata="record_metadata.json"
        output:
            "raw_input_files.txt"
        params:
            validated_runs_table=json.dumps(cfg.validated_runs_table)
        container:
            CC7_CONTAINER
        resources:
            voms_proxy=True,
            kerberos=True
        shell:
            """
            set -e
            export HOME=$(pwd)    # dasgoclient keeps its maps in $HOME
            source /cvmfs/cms.cern.ch/cmsset_default.sh
            python3 workflow/scripts/resolve_raw.py {input.metadata} {output} {cfg.num_files} \\
                '{params.validated_runs_table}'
            """

# One line per job: "<path>[<TAB><lumi ranges>]", the validated lumi sections
# of the first num_files files that have any.
rule plan_inputs:
    input:
        candidates=cfg.input_files_path
    output:
        JOB_INPUTS
    params:
        validated_runs_table=json.dumps(cfg.validated_runs_table)
    container:
        PYTHON_CONTAINER
    shell:
        """
        set -e
        if [ -z "{cfg.lumis_to_process}" ]; then
            source workflow/scripts/python_libs.sh uproot xrootd fsspec-xrootd
        fi
        python3 workflow/scripts/plan_inputs.py {input.candidates} {output} \\
            --num-files {cfg.num_files} --mgm "{cfg.eos_mgm_url}" \\
            --validated-runs-table '{params.validated_runs_table}' \\
            --lumis "{cfg.lumis_to_process}"
        """

# All files of the reference record; which hold our events is only known
# once our output exists.
rule fetch_reference_urls:
    input:
        "record_metadata.json"
    output:
        temporary("reference_urls.txt")
    container:
        OPENDATACLIENT_CONTAINER
    shell:
        "cernopendata-client get-file-locations --recid {cfg.record_id} --protocol xrootd > {output}"

# What the official reference was really made with, read from its files.
rule reference_provenance:
    input:
        urls="reference_urls.txt",
        metadata="record_metadata.json"
    output:
        PROVENANCE
    container:
        PYTHON_CONTAINER
    shell:
        """
        set -e
        source workflow/scripts/python_libs.sh uproot xrootd fsspec-xrootd
        python3 workflow/scripts/reference_provenance.py {input.urls} {output} 2 {input.metadata}
        """
