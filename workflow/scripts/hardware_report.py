"""Which hardware each cmsRun step of each job ran on.

    hardware_report.py results/metrics results/hardware

Reads results/metrics/<step>_<job>.host (written by run_step.sh, or by
step_cache.py for outputs taken from the cache) and writes <out>.csv, one row
per step and job, and <out>.txt, a summary that flags jobs whose reco or pat
ran on AMD: CMSSW_10_6 reco there differs from the Intel-made reference
(docs/known-issues.md).
"""
import csv
import glob
import os
import sys
from collections import Counter

FIELDS = ("host", "vendor", "model", "cores", "source")
VENDORS = {"GenuineIntel": "Intel", "AuthenticAMD": "AMD"}
# Steps whose output was seen to depend on the CPU vendor.
VENDOR_SENSITIVE = ("reco", "pat")
STEP_ORDER = ("prompt", "reco", "pat", "nano")


def read_rows(metrics_dir):
    rows = []
    for path in glob.glob(os.path.join(metrics_dir, "*.host")):
        step, job = os.path.basename(path)[:-len(".host")].rsplit("_", 1)
        with open(path) as f:
            values = f.readline().rstrip("\n").split("\t")
        row = dict(zip(FIELDS, values + [""] * (len(FIELDS) - len(values))))
        row.update(step=step, job=int(job), vendor=VENDORS.get(row["vendor"], row["vendor"] or "unknown"))
        rows.append(row)
    order = {s: i for i, s in enumerate(STEP_ORDER)}
    return sorted(rows, key=lambda r: (r["job"], order.get(r["step"], len(order)), r["step"]))


def summary(rows):
    lines = ["Hardware of the cmsRun steps (%d step jobs)" % len(rows), ""]
    for step in sorted(set(r["step"] for r in rows), key=lambda s: (STEP_ORDER + (s,)).index(s)):
        counts = Counter(r["vendor"] for r in rows if r["step"] == step)
        lines.append("  %-7s %s" % (step, ", ".join("%s %d" % kv for kv in sorted(counts.items()))))
    lines.append("")
    lines.append("  %-4s %-7s %-6s %-45s %s" % ("job", "step", "vendor", "model", "source"))
    for r in rows:
        lines.append("  %-4d %-7s %-6s %-45s %s" % (r["job"], r["step"], r["vendor"], r["model"], r["source"]))
    flagged = sorted(set(r["job"] for r in rows if r["step"] in VENDOR_SENSITIVE and r["vendor"] == "AMD"))
    lines.append("")
    if flagged:
        lines.append("WARNING: reco or pat ran on AMD in jobs %s; CMSSW_10_6 reco on AMD differs from "
                     "the reference at float level (docs/known-issues.md)." % ", ".join(map(str, flagged)))
    else:
        lines.append("No reco or pat step ran on AMD.")
    return "\n".join(lines) + "\n"


def main(metrics_dir, out):
    rows = read_rows(metrics_dir)
    with open(out + ".csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=("job", "step") + FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    text = summary(rows)
    with open(out + ".txt", "w") as f:
        f.write(text)
    print(text)


if __name__ == "__main__":
    main(*sys.argv[1:3])
