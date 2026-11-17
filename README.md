# RAW reprocessing validation on REANA

Reruns the official CMS processing of an Open Data record's RAW input and
checks that the output reproduces the record's files, event by event and
branch by branch. Given only a record id, the workflow finds:

- the **profile** (container image, chain of steps, comparison tier) from the
  record's year, collision type and data tier;
- the **RAW input**: the record's RAW parent dataset in DAS, its files on CMS
  EOS disk, restricted to the validated lumi sections;
- each step's **recipe** (CMSSW release, SCRAM arch, global tag, config) from
  the record and the reference files' own provenance.

```
reana-client run -w val_30520 -f reana.yaml -p record_id=30520 -p eos_outdir=/eos/user/<x>/<you>/validation
```

The result is `results/comparison_summary.txt` ("PASS: output reproduces the
official reference bit-for-bit", or the differing branches) and
`results/branch_comparison.csv`, also copied to `<eos_outdir>/<results_name>/`.

## Setup

### What you need

- **REANA access** with access to [reana.cern.ch](https://reana.cern.ch)
  and to CERN HTCondor (batch): every cmsRun step runs there.
- **CMS grid certificate** for the default input, the
  record's RAW parent found in DAS: CMS RAW files on `/eos/cms` and DAS are
  CMS-only. Without them, only curated lists of public RAW
  (`raw_file_list`, files under `/eos/opendata`) can be used.
- **An EOS directory you can write to** for the results (`eos_outdir`), for
  example under `/eos/user/<x>/<you>/`.

### Once

```
pip install reana-client
export REANA_SERVER_URL=https://reana.cern.ch
export REANA_ACCESS_TOKEN=<your token from reana.cern.ch>

# Add Kerberos token to REANA 
reana-client secrets-add --env CERN_USER=<you> --env CERN_KEYTAB=.keytab --file ~/.keytab

#Valid Grid proxy
voms-proxy-init -voms cms -rfc -valid 192:00
reana-client secrets-add --overwrite --file /tmp/x509up_u$(id -u) \
    --env VOMSPROXY_FILE=x509up_u$(id -u) --env VONAME=cms


### Running

```
reana-client run -w val_<record> -f reana.yaml -p record_id=<record> \
    -p eos_outdir=/eos/user/<x>/<you>/validation [-p num_files=3 -p eventsPerJob=500]
reana-client status -w val_<record>
reana-client ls -w val_<record> results/
```

Records with a matching profile (`workflow/profiles/`): 2010-2013 pp AOD and
RECO, 2015 AOD, 2016 UL NanoAOD, and 2011 heavy-ion RECO.


## Parameters

| Parameter | Default | Meaning |
|---|---|---|
| `record_id` | | The official derived record (NanoAOD, AOD or RECO) to reproduce. |
| `profile` | from the record | `workflow/profiles/<profile>.yaml`; only needed if no or several profiles match. |
| `raw_file_list` | DAS | A list under `workflow/raw_files/` instead of the DAS lookup (for records whose RAW parent isn't in DAS, e.g. 2011 heavy ion). |
| `num_files` | 1 | Jobs, one per input file with validated lumis. |
| `eventsPerJob` | 1 | Events per job. With `reana_compat: "0.9"`, keep a job's input well below 1 GB (2016 RAW: ~0.7 MB/event). |
| `cache_dir` | off | EOS directory caching step outputs between runs (`scripts/step_cache.py`). |
| `eos_outdir` | **required** | EOS directory you can write to (`/eos/...`) for the results. |
| `results_name` | input, record, run id | Subfolder of `eos_outdir`. |
| `htcondor_max_runtime` | `workday` | HTCondor job flavour or seconds. |
| `reana_compat` | `"0.9"` | `"0.9"`: the REANA 0.9.4 workarounds below; `"none"` on REANA 0.95. |
| **Debugging** | | |
| `lumis_to_process` | | `run:lumi[-run:lumi],...` for every job, instead of the validated lumis. |
| `recipe_override` | | Recipe fields per step, applied last, e.g. `{"reco": {"global_tag": "X", "config": "cmsDriver.py reco ..."}}`. Fields: `release`, `global_tag`, `scram_arch`, `config` (a cmsDriver or RunPromptReco.py command, URL, `root://` path or workspace file). |

## How a step's recipe is resolved

`scripts/resolve_recipe.py` writes `recipe/<step>.json` with the release,
arch, global tag and config and where each came from. 

1. **record**: the record's methodology text;
2. **provenance**: the reference files' own provenance (`reference_provenance.json`);
3. **profile**: the profile's `step_recipes` (a config command, a release per run range);
4. **override**: `recipe_override`.

It also writes `recipe/<step>.sh`, the same recipe as shell for the step
scripts (the SLC5 image has no JSON parser). Records that give no recipe
fail here, saying what is missing.

## Layout

```
workflow/
  Snakefile                 config, compat hooks, includes, rule all
  lib/config.py             parameters, profile choice, validation
  rules/inputs.smk          record metadata, RAW input, job plan, reference provenance
  rules/steps.smk           recipes, the cmsRun steps, cleanup
  rules/cache.smk           step output cache
  rules/compare_nano.smk    NanoAOD merge and comparison
  rules/compare_aod.smk     AOD flattening and comparison
  rules/metrics.smk         time, memory and size per step
  rules/reana094_htcondor.smk   REANA 0.9.4 workarounds
  scripts/run_step.sh       what a step rule runs: plan, cache, log, compat
  scripts/cmsrun_step.sh    one cmsRun step; also usable by hand on lxplus
  scripts/customise_step.py appended to the step config: input, events, output
  scripts/stage_input.sh    copies a job's events into the workspace (compat)
  scripts/hardware_report.py  hardware per step and job (results/hardware.*)
  profiles/                 per era: image, steps, comparison tier, golden JSONs
  raw_files/                curated RAW file lists
docs/known-issues.md        REANA, HTCondor, DAS and CMS issues met so far
tests/                      pytest suite and dry runs
```

## REANA 0.9.4 workarounds

reana.cern.ch runs REANA 0.9.4. With `reana_compat: "0.9"` the workflow
includes `rules/reana094_htcondor.smk` and runs steps with
`scripts/reana094_compat.sh`. Once REANA 0.95 is there, delete both and the
`include`, and the defaults in the `Snakefile` apply. Details:
`docs/known-issues.md`.

| Workaround | Why | Goes with |
|---|---|---|
| `stage_input` / `unstage_input` | HTCondor jobs get no Kerberos ticket and can't read CMS EOS. | Kerberos on HTCondor (0.95) |
| One job chain at a time (`chain_ready_<k>`), steps wait for `check_step` | A shared wrapper file lets a job run another job's command. | Per-job wrapper files (0.95) |
| Jobs delete files they didn't write (`compat_end`) | Jobs return the whole workspace; HTCondor removes jobs returning >1 GB. | Safer returned-file handling (0.95) |
| Unpacked image (Singularity) | No memory request; UL reco crashes in Docker's 2 GB. | `htcondor_request_memory` (0.95) |
| `FAILED` confirm files + `check_step` | Failed HTCondor jobs return no log. | Check on 0.95 |
| 120 s latency wait | Outputs arrive ~12 s after the job is reported done. | Check on 0.95 |

