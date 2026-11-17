# Time, memory, output size and hardware of each step (results/metrics/,
# results/hardware.*), and the workflow's total time.
import time

onstart:
    os.makedirs("workflow/results/metrics", exist_ok=True)
    with open("workflow/results/metrics/workflow_start_time.txt", "w") as f:
        f.write(str(time.time()))

onsuccess:
    with open("workflow/results/metrics/workflow_start_time.txt") as f:
        start_time = float(f.read())
    with open("workflow/results/metrics/workflow_total_time.txt", "w") as f:
        f.write("Total Time (seconds): " + str(time.time() - start_time))

rule measure_metrics:
    input:
        METRICS_UPSTREAM
    output:
        "results/metrics/metrics.csv",
        "results/hardware.csv",
        "results/hardware.txt"
    container:
        PYTHON_CONTAINER
    shell:
        """
        set -e
        source workflow/scripts/python_libs.sh pandas tabulate
        python3 workflow/scripts/metrics/metrics.py results/metrics/
        python3 workflow/scripts/hardware_report.py results/metrics results/hardware
        mkdir -p {cfg.eos_path}/results/metrics
        cp results/metrics/*.csv results/metrics/*.md {cfg.eos_path}/results/metrics/
        cp results/hardware.csv results/hardware.txt {cfg.eos_path}/results/
        """
