import sys
import re
import json
import urllib.request

metadata_json = sys.argv[1]
step = sys.argv[2]
cmd_file_path = sys.argv[3]
env_file_path = sys.argv[4]

STEP_TYPE = {"reco": "RECO", "pat": "PAT", "nano": "NANO"}[step]

with open(metadata_json, "r") as f:
    metadata = json.load(f)

description = metadata["methodology"]["description"]

# Real (non-simulated) collision-data records don't have a structured
# methodology.steps array like MC records do. Each step's release, global
# tag and linked configuration record are embedded as plain text inside the
# HTML description, e.g.:
#   <strong>Step RECO </strong><br/>Release: CMSSW_10_6_8_patch1<br/>
#   Global tag: 106X_dataRun2_v27<br/><a href="/record/30497">Configuration
#   file for RECO step ...</a>
step_pattern = re.compile(
    rf"Step\s+{STEP_TYPE}\s*</strong>.*?Release:\s*(CMSSW_[\w.]+).*?"
    rf"Global tag:\s*(\S+).*?/record/(\d+)",
    re.DOTALL,
)

step_match = step_pattern.search(description)
if not step_match:
    raise ValueError(f"Could not find a '{STEP_TYPE}' processing step in the record's methodology description")

release, global_tag, config_recid = step_match.groups()

set_env = (
    "export SCRAM_ARCH=slc7_amd64_gcc700 && "
    "source /cvmfs/cms.cern.ch/cmsset_default.sh && "
    f"scram p CMSSW {release} && cd {release}/src/"
)
with open(env_file_path, "w") as f:
    f.write(set_env)

with urllib.request.urlopen(f"https://opendata.cern.ch/api/records/{config_recid}") as resp:
    config_record = json.load(resp)

config_files = config_record["metadata"]["files"]
if not config_files:
    raise ValueError(f"Configuration record {config_recid} has no files")

config_uri = config_files[0]["uri"]
config_filename = f"{step}_config.py"

fetch_command = f'xrdcp -f "{config_uri}" {config_filename}'
with open(cmd_file_path, "w") as f:
    f.write(fetch_command)
