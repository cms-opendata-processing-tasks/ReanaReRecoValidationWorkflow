import sys
import time
from concurrent.futures import ThreadPoolExecutor

import awkward as ak
import numpy as np
import uproot

from nano_io import write_nanoaod

# Usage: select_reference.py <our nano_merged.root> <reference_urls.txt> <output.root>
#
# Builds the reference for the comparison out of exactly the events we
# reprocessed. The official NanoAOD record is ~50 files / ~75 GB, and a
# given run+lumi section sits in one arbitrary file among them, so instead
# of downloading a file and hoping it overlaps, every file's run and
# luminosityBlock branches are read remotely, the files holding our lumi
# sections are picked out, and only the matching events are read from them.
#
# reference_urls.txt holds root:// (XRootD) URLs, one per line.
merged_nano = sys.argv[1]
urls_file = sys.argv[2]
output_root = sys.argv[3]

ID_BRANCHES = ["run", "luminosityBlock", "event"]


def lumi_key(run, lumi):
    return run.astype(np.uint64) * np.uint64(1 << 32) + lumi.astype(np.uint64)


def event_key(run, lumi, event):
    return run.astype(np.uint64) * np.uint64(1 << 40) + event.astype(np.uint64)


with uproot.open(merged_nano) as f:
    ours = f["Events"].arrays(ID_BRANCHES, library="np")
our_lumis = np.unique(lumi_key(ours["run"], ours["luminosityBlock"]))
our_events = event_key(ours["run"], ours["luminosityBlock"], ours["event"])
print("Our file: %d events in %d (run, lumi) sections, runs %s"
      % (len(our_events), len(our_lumis), np.unique(ours["run"]).tolist()))
if len(our_events) == 0:
    raise ValueError(merged_nano + " has no events - did the nano step fail?")

urls = [line.strip() for line in open(urls_file) if line.strip()]


def with_retries(func, url, attempts=4):
    for attempt in range(1, attempts + 1):
        try:
            return func(url)
        except Exception as e:
            if attempt == attempts:
                raise
            print("  retrying %s after error: %r" % (url, e))
            time.sleep(10 * attempt)


def read_ids(url):
    return uproot.open(url, timeout=300)["Events"].arrays(["run", "luminosityBlock"], library="np")


def scan(url):
    ids = with_retries(read_ids, url)
    in_our_lumis = np.isin(lumi_key(ids["run"], ids["luminosityBlock"]), our_lumis)
    return url, np.flatnonzero(in_our_lumis)


print("Scanning run/lumi of %d reference files" % len(urls))
with ThreadPoolExecutor(8) as pool:
    candidates = [(url, idx) for url, idx in pool.map(scan, urls) if len(idx)]
if not candidates:
    raise ValueError("None of the %d reference files contain our (run, lumi) sections" % len(urls))

chunks = []
for url, idx in candidates:
    print("  %s: %d events in our lumi sections" % (url, len(idx)))
    start, stop = int(idx[0]), int(idx[-1]) + 1
    # All branches: the comparison covers the full NanoAOD content.
    arrays = with_retries(lambda u: uproot.open(u, timeout=300)["Events"].arrays(
        entry_start=start, entry_stop=stop), url)
    keep = np.isin(event_key(ak.to_numpy(arrays["run"]), ak.to_numpy(arrays["luminosityBlock"]),
                             ak.to_numpy(arrays["event"])), our_events)
    chunks.append(arrays[keep])

reference = ak.concatenate(chunks)
print("Matched %d of our %d events in the reference" % (len(reference), len(our_events)))
if len(reference) == 0:
    raise ValueError("Our lumi sections were found in the reference, but none of our event numbers were")

write_nanoaod(output_root, reference)
print("Wrote " + output_root)
