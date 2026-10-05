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

# Usage: Plotting.py <our nano_merged.root> <reference.root> <comparison_report.csv> <plot dir>
merged_nano = sys.argv[1]
reference_root = sys.argv[2]
comparison_report = sys.argv[3]
plot_dir = sys.argv[4]

os.makedirs(plot_dir, exist_ok=True)

# chi2 / p-value per variable, as computed by Stat_comparison.py
report = pd.read_csv(comparison_report).set_index("variable")


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
        stats = r"$\chi^2$/ndof = %.2f / %d,  p = %.3g  [%s]" % (
            row["chi2"], row["ndof"], row["p_value"], row["flag"])
        ax.set_title(stats, fontsize=11, loc="right",
                     color="#b3261e" if row["flag"] == "MISMATCH" else "black")

    ax.set_title(var, fontsize=13, fontweight="bold", loc="left")
    ax.set_ylabel("Normalised entries")
    ax.legend(fontsize=9)
    ax.grid(axis="y", linestyle="--", alpha=0.5)

    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = np.where(h_ref > 0, h_ours / h_ref, np.nan)
        ratio_err = np.where(h_ref > 0, err_ours / h_ref, np.nan)
    rax.errorbar(centers, ratio, yerr=ratio_err, fmt="o", color="black", markersize=3)
    rax.axhline(1.0, color="#0b57d0", linewidth=1)
    rax.set_ylim(0, 2)
    rax.set_ylabel("Ours / Ref")
    rax.set_xlabel(xlabel)
    rax.grid(axis="y", linestyle="--", alpha=0.5)

    fig.savefig(os.path.join(plot_dir, var + ".png"), dpi=120, bbox_inches="tight")
    plt.close(fig)


with uproot.open(merged_nano) as f_ours, uproot.open(reference_root) as f_ref:
    ours_tree = f_ours["Events"]
    ref_tree = f_ref["Events"]
    for var, branch, bins, x_range, xlabel in variable_configs:
        print("Plotting " + var)
        plot_comparison(var, load_variable(ours_tree, branch), load_variable(ref_tree, branch),
                        bins, x_range, xlabel)
