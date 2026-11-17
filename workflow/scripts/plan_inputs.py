import argparse
import json
import struct
import time
from concurrent.futures import ThreadPoolExecutor

# Usage: plan_inputs.py <candidates.txt> <job_inputs.txt> --num-files N --mgm URL
#            [--validated-runs-table JSON] [--lumis RANGES]
#
# Decides what each job of the first step processes. Writes one line per job:
#   <input path>[<TAB><run:lumi-run:lumi,...>]
# The lumi ranges (if any) become the job's process.source.lumisToProcess.
#
# Each candidate file's lumi sections are read (its
# LuminosityBlocks tree, a few kB per file) and intersected with the CMS list
# of validated runs for that run's year: files without any validated lumi are
# skipped, and each job is restricted to the validated lumis of its file.
# --lumis (the lumis_to_process parameter) instead applies the given ranges
# to every job as they are, without any reading or validation.
parser = argparse.ArgumentParser()
parser.add_argument("candidates")
parser.add_argument("job_inputs")
parser.add_argument("--num-files", type=int, required=True)
parser.add_argument("--mgm", required=True)
# Profile's [{"first_run", "last_run", "file"}, ...]: which list covers which runs.
parser.add_argument("--validated-runs-table", default="[]")
parser.add_argument("--lumis", default="")
args = parser.parse_args()

candidates = [line.strip() for line in open(args.candidates) if line.strip()]
if args.num_files > len(candidates):
    raise ValueError("num_files (%d) exceeds the %d candidate input files in %s"
                     % (args.num_files, len(candidates), args.candidates))


def write_jobs(lines):
    with open(args.job_inputs, "w") as f:
        f.write("\n".join(lines) + "\n")
    for line in lines:
        print("  " + line.replace("\t", "  lumis: "))


if args.lumis.strip():
    print("Using the given lumi ranges for every job: " + args.lumis.strip())
    write_jobs([path + "\t" + args.lumis.strip() for path in candidates[:args.num_files]])
    raise SystemExit(0)

import uproot  # only needed for the validated-lumi selection

from validated_runs import ValidatedRuns


validated_ranges = ValidatedRuns(json.loads(args.validated_runs_table)).ranges


def skip_header(blob, pos):
    """Position after a streamed object's byte count and version (and the
    class checksum that follows version 0, as in 2010-2011 files)."""
    version = struct.unpack(">H", blob[pos + 4:pos + 6])[0]
    return pos + 6 + (4 if version == 0 else 0)


def parse_lumi_aux(blob):
    """(run, lumi) from a streamed edm::LuminosityBlockAuxiliary: its header,
    processHistoryID_ (an edm::Hash, skipped by its byte count), then id_
    (header, run, lumi as big-endian uint32)."""
    pos = skip_header(blob, 0)
    hash_bytes = struct.unpack(">I", blob[pos:pos + 4])[0] & ~0x40000000
    pos = skip_header(blob, pos + 4 + hash_bytes)
    return struct.unpack(">II", blob[pos:pos + 8])


def file_lumis(path):
    url = args.mgm + "/" + path
    for attempt in range(1, 5):
        try:
            tree = uproot.open(url, timeout=300)["LuminosityBlocks"]
            blobs = tree["LuminosityBlockAuxiliary"].array(uproot.AsJagged(uproot.AsDtype("u1")), library="np")
            return sorted(set(parse_lumi_aux(bytes(b)) for b in blobs))
        except Exception as e:
            if attempt == 4:
                raise
            print("  retrying %s after error: %r" % (url, e))
            time.sleep(10 * attempt)


def to_ranges(lumis):
    """[(run, lumi), ...] sorted -> "run:a-run:b,..." over consecutive lumis."""
    ranges = []
    for run, lumi in lumis:
        if ranges and ranges[-1][0] == run and ranges[-1][2] == lumi - 1:
            ranges[-1][2] = lumi
        else:
            ranges.append([run, lumi, lumi])
    return ",".join("%d:%d-%d:%d" % (run, a, run, b) for run, a, b in ranges)


# Files are scanned in order, a batch at a time, until enough of them have
# validated lumis; with a large RAW record most files are never opened.
jobs = []
skipped = []
batch_size = max(16, args.num_files)
for start in range(0, len(candidates), batch_size):
    batch = candidates[start:start + batch_size]
    with ThreadPoolExecutor(16) as pool:
        results = list(pool.map(file_lumis, batch))
    for path, lumis in zip(batch, results):
        good = [(r, l) for r, l in lumis if any(a <= l <= b for a, b in validated_ranges(r))]
        if good:
            print("%s: %d of %d lumi sections validated" % (path, len(good), len(lumis)))
            jobs.append(path + "\t" + to_ranges(good))
        else:
            print("%s: skipped, none of its %d lumi sections (%s) are validated"
                  % (path, len(lumis), to_ranges(lumis)))
            skipped.append(path)
        if len(jobs) == args.num_files:
            break
    if len(jobs) == args.num_files:
        break

if len(jobs) < args.num_files:
    raise ValueError("Only %d of the %d candidate files have validated lumi sections, %d requested"
                     % (len(jobs), len(candidates), args.num_files))
print("Planned %d jobs (%d files skipped):" % (len(jobs), len(skipped)))
write_jobs(jobs)
