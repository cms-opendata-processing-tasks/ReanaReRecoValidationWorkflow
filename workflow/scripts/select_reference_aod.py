import struct
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import uproot

# Usage: select_reference_aod.py <ours_flat.root> <reference_urls.txt> <reference_files.txt> <reference_events.txt>
#
# AOD-tier counterpart of select_reference.py. Official AOD/RECO records are
# EDM files (thousands of them), where reading event IDs of every event
# would be slow. Instead each file's LuminosityBlocks tree - one entry per
# lumi section - is scanned for our (run, lumi) pairs, which needs only a
# few kB per file. Outputs the files holding our lumi sections, and our
# event list ("run:event"), for aod_dump.py to read the same events from them.
ours_flat = sys.argv[1]
urls_file = sys.argv[2]
files_out = sys.argv[3]
events_out = sys.argv[4]

with uproot.open(ours_flat) as f:
    ours = f["Events"].arrays(["run", "luminosityBlock", "event"], library="np")
pairs = sorted(set(zip(ours["run"].tolist(), ours["luminosityBlock"].tolist())))
print("Our file: %d events in %d (run, lumi) sections" % (len(ours["run"]), len(pairs)))
if not pairs:
    raise ValueError(ours_flat + " has no events")
# LuminosityBlockAuxiliary stores its id as big-endian (run, lumi) uint32s.
patterns = [struct.pack(">II", run, lumi) for run, lumi in pairs]

urls = [line.strip() for line in open(urls_file) if line.strip()]


def scan(url):
    for attempt in range(1, 5):
        try:
            tree = uproot.open(url, timeout=300)["LuminosityBlocks"]
            blobs = tree["LuminosityBlockAuxiliary"].array(uproot.AsJagged(uproot.AsDtype("u1")), library="np")
            data = [bytes(b) for b in blobs]
            return url, [p for p in patterns if any(p in b for b in data)]
        except Exception as e:
            if attempt == 4:
                raise
            print("  retrying %s after error: %r" % (url, e))
            time.sleep(10 * attempt)


print("Scanning lumi sections of %d reference files" % len(urls))
with ThreadPoolExecutor(16) as pool:
    hits = [(url, found) for url, found in pool.map(scan, urls) if found]

found_patterns = set(p for _, found in hits for p in found)
missing = [pair for pair, p in zip(pairs, patterns) if p not in found_patterns]
for url, found in hits:
    print("  %s: %d of our lumi sections" % (url, len(found)))
if missing:
    print("WARNING: %d of our lumi sections are not in the reference record: %s" % (len(missing), missing))
if not hits:
    raise ValueError("None of the %d reference files contain our lumi sections" % len(urls))

with open(files_out, "w") as f:
    f.write("\n".join(url for url, _ in hits) + "\n")
with open(events_out, "w") as f:
    f.write("\n".join("%d:%d" % (r, e) for r, e in zip(ours["run"].tolist(), ours["event"].tolist())) + "\n")
print("Wrote %d reference files and %d events" % (len(hits), len(ours["run"])))
