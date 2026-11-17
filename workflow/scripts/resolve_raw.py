import json
import os
import subprocess
import sys

from validated_runs import ValidatedRuns

# Usage: resolve_raw.py <record_metadata.json> <raw_input_files.txt> <num_files> <validated_runs_table>
#
# The RAW input of the official derived record, when no RAW record or curated
# list is given: the record's dataset is followed up its DAS parents
# (NANOAOD -> MINIAOD -> AOD -> RAW for Run 2 UL, AOD -> RAW for Run 1) to
# the RAW dataset, and the files of its first validated runs among the
# record's run numbers that are on CMS EOS disk are listed, as /eos/cms
# paths. plan_inputs.py then reads their lumi sections and picks the
# validated ones; the comparison finds those lumis in the reference files.
#
# Needs dasgoclient (/cvmfs/cms.cern.ch), the /eos/cms mount (kerberos) and
# a CMS grid proxy (voms_proxy): without a working proxy, DAS answers every
# query with no records instead of an error, which is checked for below.
metadata_json, out_path = sys.argv[1], sys.argv[2]
num_files = int(sys.argv[3])
validated = ValidatedRuns(json.loads(sys.argv[4]))
EOS_PREFIX = "/eos/cms"
MAX_PARENT_HOPS = 5


def das(query):
    res = subprocess.run(["dasgoclient", "-query", query], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         universal_newlines=True, timeout=600)
    if res.returncode != 0:
        sys.exit("dasgoclient failed on '%s': %s" % (query, res.stderr.strip()[:500]))
    return [line.strip() for line in res.stdout.splitlines() if line.strip().startswith("/")]


meta = json.load(open(metadata_json))
meta = meta.get("metadata", meta)
dataset = meta["title"]
if dataset not in das("dataset=" + dataset):
    sys.exit("DAS doesn't know %s: check the grid proxy secret (VOMSPROXY_FILE must be the secret's file "
             "name) and that it is valid for the cms VO" % dataset)

raw = dataset
for _ in range(MAX_PARENT_HOPS):
    parents = das("parent dataset=" + raw)
    if not parents:
        sys.exit("%s has no parent dataset in DAS: give raw_file_list" % raw)
    raw = next((p for p in parents if p.endswith("/RAW")), parents[0])
    print("Parent: " + raw)
    if raw.endswith("/RAW"):
        break
else:
    sys.exit("No RAW dataset within %d parents of %s" % (MAX_PARENT_HOPS, dataset))

if not os.path.isdir(EOS_PREFIX + "/store"):
    sys.exit(EOS_PREFIX + " isn't mounted (the rule needs kerberos)")

# The record's validated runs, lowest first, until their files on CMS EOS
# disk are enough for num_files jobs. DAS lists every RAW file, but most are
# only on tape (see docs/known-issues.md).
runs = sorted(int(r) for r in meta.get("run_numbers") or [])
if not runs:
    sys.exit("The record lists no run numbers")
files = []
runs_off_disk = 0
for run in runs:
    if not validated.ranges(run):
        continue
    run_files = sorted(das("file dataset=%s run=%d" % (raw, run)))
    on_disk = [EOS_PREFIX + f for f in run_files if os.path.exists(EOS_PREFIX + f)]
    if not on_disk:
        runs_off_disk += 1
        continue
    print("Run %d: %d of its %d files on CMS EOS" % (run, len(on_disk), len(run_files)))
    files += on_disk
    if len(files) >= num_files:
        break
print("%d validated runs before had no files on CMS EOS" % runs_off_disk)
if len(files) < num_files:
    sys.exit("Only %d files of %s on CMS EOS are in validated runs of the record, %d requested"
             % (len(files), raw, num_files))

with open(out_path, "w") as f:
    f.write("\n".join(files) + "\n")
print("Wrote %d candidate files of %s to %s" % (len(files), raw, out_path))
