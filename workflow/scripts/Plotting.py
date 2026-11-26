import os
import sys

import awkward as ak
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import uproot

from variables import variable_configs

# Usage: Plotting.py <our nano_merged.root> <reference_matched.root> <branch_comparison.csv> <plot dir>
#
# - <plot dir>/<variable>.png: overlay + ratio for the variables in
#   variables.py, titled with that branch's result from Event_comparison.py.
# - <plot dir>/residuals/<branch>.png: per-object (ours - reference)
#   residuals for every float branch Event_comparison.py flagged DIFFERENT.
merged_nano = sys.argv[1]
reference_root = sys.argv[2]
branch_report = sys.argv[3]
plot_dir = sys.argv[4]

MAX_RESIDUAL_PLOTS = 40

os.makedirs(os.path.join(plot_dir, "residuals"), exist_ok=True)

report = pd.read_csv(branch_report).set_index("branch")


def load_variable(tree, branch_name):
    arr = tree[branch_name].array()
    if arr.ndim > 1:
        arr = ak.flatten(arr)
    return ak.to_numpy(arr)


def normalised_hist(values, bins, x_range):
    counts, edges = np.histogram(values, bins=bins, range=x_range)
    total = counts.sum()
    if total == 0:
        return np.zeros(len(counts)), np.zeros(len(counts)), edges
    return counts / total, np.sqrt(counts) / total, edges


def plot_comparison(var, ours, ref, bins, x_range, xlabel):
    h_ours, err_ours, edges = normalised_hist(ours, bins, x_range)
    h_ref, err_ref, _ = normalised_hist(ref, bins, x_range)
    centers = 0.5 * (edges[:-1] + edges[1:])

    fig, (ax, rax) = plt.subplots(
        2, 1, figsize=(8, 7), sharex=True,
        gridspec_kw={"height_ratios": [3, 1], "hspace": 0.05},
    )

    ax.stairs(h_ref, edges, color="#0b57d0", linewidth=1.5,
              label="Reference (official NanoAOD), N=" + str(len(ref)))
    ax.fill_between(edges, np.append(h_ref - err_ref, 0), np.append(h_ref + err_ref, 0),
                    step="post", color="#0b57d0", alpha=0.2, linewidth=0)
    ax.errorbar(centers, h_ours, yerr=err_ours, fmt="o", color="black", markersize=4,
                label="Reprocessed (this workflow), N=" + str(len(ours)))

    if var in report.index:
        row = report.loc[var]
        stats = "%s: %.2f%% of values identical" % (row["status"], 100 * row["frac_identical"])
        ax.set_title(stats, fontsize=11, loc="right",
                     color="#b3261e" if row["status"] == "DIFFERENT" else "black")

    ax.set_title(var, fontsize=13, fontweight="bold", loc="left")
    ax.set_ylabel("Normalised entries")
    ax.legend(fontsize=9)
    ax.grid(axis="y", linestyle="--", alpha=0.5)

    # No error bars on the ratio: both histograms hold the same events, so the
    # ratio has no statistical uncertainty; any point off 1 is a real difference.
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = np.where(h_ref > 0, h_ours / h_ref, np.nan)
    # Ratios above the axis, and entries where the reference bin is empty, are
    # drawn as red triangles at the top edge instead of disappearing.
    ratio[(h_ref == 0) & (h_ours > 0)] = np.inf
    ratio_max = 2.0
    above = ratio > ratio_max
    rax.plot(centers[~above], ratio[~above], "o", color="black", markersize=3)
    rax.plot(centers[above], np.full(above.sum(), ratio_max * 0.97), "^", color="#b3261e", markersize=5)
    rax.axhline(1.0, color="#0b57d0", linewidth=1)
    rax.set_ylim(-0.05, ratio_max + 0.05)    # margin: ratios of exactly 0 or 2 stay fully visible
    rax.set_ylabel("Ours / Ref")
    rax.set_xlabel(xlabel)
    rax.grid(axis="y", linestyle="--", alpha=0.5)

    fig.savefig(os.path.join(plot_dir, var + ".png"), dpi=120, bbox_inches="tight")
    plt.close(fig)


def plot_residuals(branch, ours_tree, ref_tree, i_ours, i_ref):
    a = ours_tree[branch].array()[i_ours]
    b = ref_tree[branch].array()[i_ref]
    if a.ndim > 1:
        same_len = ak.to_numpy(ak.num(a) == ak.num(b))
        a, b = ak.flatten(a[same_len]), ak.flatten(b[same_len])
    diff = ak.to_numpy(a).astype(np.float64) - ak.to_numpy(b).astype(np.float64)
    diff = diff[np.isfinite(diff)]
    nonzero = diff[diff != 0]

    fig, ax = plt.subplots(figsize=(8, 5))
    if len(nonzero):
        lim = np.percentile(np.abs(nonzero), 99) or np.abs(nonzero).max()
        ax.hist(diff, bins=60, range=(-lim, lim), color="#0b57d0", alpha=0.8)
    ax.set_yscale("log")
    ax.set_title(branch, fontsize=13, fontweight="bold", loc="left")
    ax.set_title("%d of %d differ, mean %.3g, rms %.3g" % (
        len(nonzero), len(diff), diff.mean() if len(diff) else 0,
        np.sqrt(np.mean(diff ** 2)) if len(diff) else 0), fontsize=10, loc="right")
    ax.set_xlabel("ours - reference (same object)")
    ax.set_ylabel("Objects")
    fig.savefig(os.path.join(plot_dir, "residuals", branch + ".png"), dpi=120, bbox_inches="tight")
    plt.close(fig)


def event_key(tree):
    ids = tree.arrays(["run", "event"], library="np")
    return ids["run"].astype(np.uint64) * np.uint64(1 << 40) + ids["event"].astype(np.uint64)


with uproot.open(merged_nano) as f_ours, uproot.open(reference_root) as f_ref:
    ours_tree = f_ours["Events"]
    ref_tree = f_ref["Events"]
    available = set(ours_tree.keys()) & set(ref_tree.keys())
    for var, branch, bins, x_range, xlabel in variable_configs:
        if branch not in available:
            # e.g. taus are not in the AOD-level dumps
            print("Skipping " + var + ": not in both files")
            continue
        print("Plotting " + var)
        plot_comparison(var, load_variable(ours_tree, branch), load_variable(ref_tree, branch),
                        bins, x_range, xlabel)

    _, i_ours, i_ref = np.intersect1d(event_key(ours_tree), event_key(ref_tree), return_indices=True)
    different = report[(report["status"] == "DIFFERENT") & (report["n_differing"] > 0)
                       & report["type"].str.startswith("float")]
    for branch in different.index[:MAX_RESIDUAL_PLOTS]:
        print("Plotting residuals of " + branch)
        plot_residuals(branch, ours_tree, ref_tree, i_ours, i_ref)
