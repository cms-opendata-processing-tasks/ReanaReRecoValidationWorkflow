import os
import sys
import pandas as pd
from copy import deepcopy

metrics_path = sys.argv[1]

steps = {"reco": [], "pat": [], "nano": []}
step_metrics = {"time": deepcopy(steps), "storage": deepcopy(steps)}

for filename in os.listdir(metrics_path):
    step = filename.split("_")[0]
    filepath = os.path.join(metrics_path, filename)

    with open(filepath, "r") as f:
        metric = f.readline()

    if filename.endswith(".tsv"):
        step_metrics["time"][step].append(float(metric.split("\t")[0]))
    elif filename.endswith(".size"):
        step_metrics["storage"][step].append(int(metric[:-2]))

steps_summary = {"reco": {}, "pat": {}, "nano": {}}
summary_dict = {"time": deepcopy(steps_summary), "storage": deepcopy(steps_summary)}

prev = {"pat": "reco", "nano": "pat"}


for k,v in step_metrics['storage'].items():
    summary_dict["storage"][k]["Total Size (Bytes)"] = sum(v)
    summary_dict["storage"][k]["Total Size (GBs)"] = sum(v) / (1024**3)
    summary_dict["storage"][k]["File Count"] = len(v)

    if k == "reco":
        summary_dict["storage"][k]["Peak Storage (GBs)"] = summary_dict["storage"][k]["Total Size (GBs)"]
    else:
        summary_dict["storage"][k]["Peak Storage (GBs)"] = (summary_dict["storage"][k]["Total Size (Bytes)"] + summary_dict["storage"][prev[k]]["Total Size (Bytes)"]) / (1024**3)

for k,v in step_metrics['time'].items():
    summary_dict["time"][k]["Avg Time (s)"] = (sum(v) / len(v)) 
    summary_dict["time"][k]["Avg Time (h)"] = summary_dict["time"][k]["Avg Time (s)"] / 3600 

time_df = pd.DataFrame.from_dict(summary_dict["time"], orient="index")
storage_df = pd.DataFrame.from_dict(summary_dict["storage"], orient="index")

time_details_df = pd.DataFrame.from_dict(step_metrics["time"], orient="index", columns=list(range(1, len(step_metrics["time"]["reco"])+1)))
storage_details_df = pd.DataFrame.from_dict(step_metrics["storage"], orient="index", columns=list(range(1, len(step_metrics["time"]["reco"])+1)))


summary = pd.concat([storage_df, time_df], axis=1)

summary.to_csv("results/metrics/metrics.csv")
markdown_summary = summary.to_markdown()

with open("results/metrics/metrics.md", "w") as f:
    f.write(markdown_summary)

time_details_df.to_csv("results/metrics/time_step_details_seconds.csv")
with open("results/metrics/time_details_seconds.md", "w") as f:
    f.write(time_details_df.to_markdown())

storage_details_df.to_csv("results/metrics/storage_step_details_bytes.csv")
with open("results/metrics/storage_step_details_bytes.md", "w") as f:
    f.write(storage_details_df.to_markdown())