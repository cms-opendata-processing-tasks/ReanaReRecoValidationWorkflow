import sys

import awkward as ak
import numpy as np
import pandas as pd
import uproot
from scipy.stats import wilcoxon

# Usage: Event_comparison.py <our nano_merged.root> <reference_matched.root> <branch_comparison.csv> <summary.txt>
#
# Our RAW input is byte-identical to the official one (same adler32 in DBS)
# and every step reruns the official release + global tag + configuration,
# so the expected result is bit-for-bit identical NanoAOD. Events are paired
# on (run, event) and EVERY branch present in both files is compared value
# by value, then classified as:
#
#   IDENTICAL  - every value bit-for-bit equal
#   PRECISION  - differences no larger than one unit in the last place NanoAOD
#                stores (NanoAOD truncates float mantissas, so ~1e-6 level
#                numerical noise, e.g. from ML inference or CPU differences,
#                can flip that last stored bit)
#   DIFFERENT  - anything else, incl. differing object multiplicities
#
# A reference-only boolean branch that is False in every matched event is
# ZERO_FILLED_IN_REFERENCE (a trigger path of another run's menu, filled in
# by haddnano when the official files were merged), not a failure.
#
# A distribution-level test (e.g. two-sample chi2) is not used: it assumes
# independent samples, whereas these are the same collisions.
merged_nano = sys.argv[1]
reference_root = sys.argv[2]
branch_report = sys.argv[3]
summary_file = sys.argv[4]

ID_BRANCHES = ["run", "luminosityBlock", "event"]
MIN_VALUES_FOR_WILCOXON = 10


def load(path):
    with uproot.open(path) as f:
        return f["Events"].arrays()


def event_key(arrays):
    return (ak.to_numpy(arrays["run"]).astype(np.uint64) * np.uint64(1 << 40)
            + ak.to_numpy(arrays["event"]).astype(np.uint64))


def stored_mantissa_bits(*values):
    """Number of float32 mantissa bits NanoAOD actually kept for this branch,
    inferred from the most precise value seen."""
    bits = 0
    for v in values:
        m = v.astype(np.float32).view(np.uint32) & np.uint32(0x7FFFFF)
        m = m[m != 0]
        if len(m):
            lowest_set_bit = m & (~m + np.uint32(1))
            bits = max(bits, int(23 - np.log2(lowest_set_bit).astype(int).min()))
    return bits if bits else 23


def compare_values(a, b):
    """Compare two equal-length flat arrays; returns a dict of statistics."""
    is_float = np.issubdtype(a.dtype, np.floating)
    a64 = a.astype(np.float64)
    b64 = b.astype(np.float64)
    equal = (a == b)
    if is_float:
        equal |= np.isnan(a64) & np.isnan(b64)

    stats = {
        "n_values": len(a),
        "n_differing": int((~equal).sum()),
        "frac_identical": equal.mean() if len(a) else np.nan,
        "frac_within_precision": np.nan,
        "precision_bits": np.nan,
        "max_abs_diff": np.nan,
        "mean_diff": np.nan,
        "rms_diff": np.nan,
        "wilcoxon_p": np.nan,
    }
    if not len(a):
        return stats

    finite = np.isfinite(a64) & np.isfinite(b64)
    diff = a64[finite] - b64[finite]
    if len(diff):
        stats["max_abs_diff"] = np.abs(diff).max()

    if is_float:
        bits = stored_mantissa_bits(a[finite], b[finite])
        magnitude = np.maximum(np.abs(a64), np.abs(b64))
        with np.errstate(divide="ignore"):
            one_unit = np.where(magnitude > 0, 2.0 ** (np.floor(np.log2(magnitude)) - bits), 0.0)
        within = equal | (finite & (np.abs(a64 - b64) <= one_unit * (1 + 1e-9)))
        stats["frac_within_precision"] = within.mean()
        stats["precision_bits"] = bits
        # Paired residuals (ours - reference) over the same objects: a
        # systematic shift shows up as a non-zero mean / small Wilcoxon p.
        if len(diff):
            stats["mean_diff"] = diff.mean()
            stats["rms_diff"] = np.sqrt(np.mean(diff ** 2))
            nonzero = diff[diff != 0]
            if len(nonzero) >= MIN_VALUES_FOR_WILCOXON:
                stats["wilcoxon_p"] = wilcoxon(nonzero).pvalue
    return stats


ours = load(merged_nano)
ref = load(reference_root)

_, i_ours, i_ref = np.intersect1d(event_key(ours), event_key(ref), return_indices=True)
ours_m = ours[i_ours]
ref_m = ref[i_ref]

our_branches = set(ours.fields)
ref_branches = set(ref.fields)
common = sorted(our_branches & ref_branches)

rows = []
for branch in common:
    a = ours_m[branch]
    b = ref_m[branch]
    if a.ndim > 1:
        # Jagged collection: values are compared object by object (in their
        # stored, pt-ordered position) in events where the multiplicity agrees.
        same_len = ak.to_numpy(ak.num(a) == ak.num(b))
        n_multiplicity_mismatch = int((~same_len).sum())
        a_vals = ak.to_numpy(ak.flatten(a[same_len]))
        b_vals = ak.to_numpy(ak.flatten(b[same_len]))
    else:
        n_multiplicity_mismatch = 0
        a_vals = ak.to_numpy(a)
        b_vals = ak.to_numpy(b)

    stats = compare_values(a_vals, b_vals)
    multiplicity_ok = n_multiplicity_mismatch == 0
    if multiplicity_ok and stats["n_differing"] == 0:
        status = "IDENTICAL"
    elif multiplicity_ok and stats["frac_within_precision"] == 1:
        status = "PRECISION"
    else:
        status = "DIFFERENT"

    row = {"branch": branch, "type": str(a_vals.dtype) + ("[]" if a.ndim > 1 else ""),
           "status": status, "n_events": len(a), "n_events_multiplicity_mismatch": n_multiplicity_mismatch}
    row.update(stats)
    rows.append(row)

for branch in sorted(ref_branches - our_branches):
    # Official NanoAOD files are merged across runs by haddnano, which fills
    # trigger bits a run's menu doesn't have with False. A boolean branch
    # that is False in every matched reference event is such a path, absent
    # from the menu of the run we reprocessed, not a missing output.
    values = ak.to_numpy(ref_m[branch]) if ref_m[branch].ndim == 1 else None
    zero_filled = values is not None and values.dtype == np.bool_ and len(values) and not values.any()
    rows.append({"branch": branch, "status": "ZERO_FILLED_IN_REFERENCE" if zero_filled else "MISSING_IN_OURS"})
for branch in sorted(our_branches - ref_branches):
    rows.append({"branch": branch, "status": "MISSING_IN_REFERENCE"})

order = {"DIFFERENT": 0, "MISSING_IN_OURS": 1, "MISSING_IN_REFERENCE": 2, "PRECISION": 3, "IDENTICAL": 4,
         "ZERO_FILLED_IN_REFERENCE": 5}
df = pd.DataFrame(rows)
df = df.sort_values(by=["status", "n_differing"], key=lambda col: col.map(order) if col.name == "status" else -col.fillna(0))
df.to_csv(branch_report, float_format="%.6g", index=False)

counts = df["status"].value_counts()
summary = [
    "Events in our output:        %d" % len(ours),
    "Events in reference:         %d" % len(ref),
    "Events matched (run, event): %d" % len(i_ours),
    "Our events not in reference: %d" % (len(ours) - len(i_ours)),
    "",
    "Branches compared: %d" % len(common),
]
for status in order:
    summary.append("  %-24s %d" % (status, counts.get(status, 0)))

lumi_mismatch = int(np.sum(ak.to_numpy(ours_m["luminosityBlock"]) != ak.to_numpy(ref_m["luminosityBlock"])))
if lumi_mismatch:
    summary.append("WARNING: %d matched events have a different luminosityBlock" % lumi_mismatch)

if len(i_ours) and counts.get("DIFFERENT", 0) == 0 and counts.get("MISSING_IN_OURS", 0) == 0:
    # Tier-neutral: also used for AOD comparisons (flattened by aod_dump.py).
    verdict = "PASS: output reproduces the official reference" + (
        " (up to stored float precision)" if counts.get("PRECISION", 0) else " bit-for-bit")
else:
    verdict = "FAIL: see DIFFERENT / MISSING branches below"
summary += ["", verdict]

different = df[df["status"] == "DIFFERENT"]
# A collection whose object count differs makes every one of its branches
# DIFFERENT; report that once per collection instead of once per branch.
multiplicity = different[different["n_events_multiplicity_mismatch"] > 0]
if len(multiplicity):
    summary += ["", "Collections whose object count differs:"]
    collections = sorted((b[1:] for b in common if b.startswith("n") and any(x.startswith(b[1:] + "_") for x in common)),
                         key=len, reverse=True)

    def collection_of(branch):
        return next((c for c in collections if branch.startswith(c + "_")), branch)

    by_collection = multiplicity.groupby(multiplicity["branch"].map(collection_of))
    for collection, group in by_collection:
        summary.append("  %-40s count differs in %d events (%d branches affected)"
                       % (collection, group["n_events_multiplicity_mismatch"].max(), len(group)))
valued = different[different["n_differing"] > 0]
if len(valued):
    summary += ["", "Branches with differing values (most first; compared where object counts agree):"]
    for _, r in valued.head(50).iterrows():
        summary.append("  %-40s %6d of %6d values differ, max |diff| %.4g%s"
                       % (r["branch"], r["n_differing"], r["n_values"], r["max_abs_diff"],
                          "" if np.isnan(r["wilcoxon_p"]) else ", mean shift %.3g (Wilcoxon p %.2g)"
                          % (r["mean_diff"], r["wilcoxon_p"])))
    if len(valued) > 50:
        summary.append("  ... and %d more (see %s)" % (len(valued) - 50, branch_report))
missing = df[df["status"] == "MISSING_IN_OURS"]["branch"].tolist()
if missing:
    summary += ["", "Branches in the reference but missing from our output:", "  " + ", ".join(missing)]

print("\n".join(summary))
with open(summary_file, "w") as f:
    f.write("\n".join(summary) + "\n")

if len(i_ours) == 0:
    raise ValueError("No events in common between " + merged_nano + " and " + reference_root)
