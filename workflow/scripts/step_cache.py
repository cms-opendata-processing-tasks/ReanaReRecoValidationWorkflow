import hashlib
import json
import os
import shutil
import sys
import time

# Cache of the cmsRun step outputs on EOS, shared between workflow runs, so
# an input that was already processed the same way isn't processed again. Mainly for testing wf
#
# Usage:
#   step_cache.py lookup <cache_dir> <job_inputs.txt> <events> <threads> <code_files> <steps> <out_dir>
#       For every job: computes each step's cache key, finds the last step of
#       the chain that is cached, restores its output to
#       results/<step>_<job>.root and writes <out_dir>/job_<job>.txt
#       with one "<step> <status> <key>" line per step, status being
#         skip     - not needed, a later step's output is restored
#         restored - output copied from the cache
#         run      - must run (and is stored afterwards)
#   step_cache.py store <cache_dir> <job_file> <step> <output.root> <confirm>
#       Stores a step output that was produced by running (status "run").
#
# A step's key covers what determines its output: the input file and its lumi
# ranges, the event and thread counts, the step's recipe (recipe/<step>.json:
# release, arch, global tag, config) and the step scripts (code_files,
# comma-separated). Later steps chain on the key of the step before them.

# The recipe fields that determine the output (not its sources or notes).
RECIPE_FIELDS = ("release", "scram_arch", "global_tag", "config", "set_global_tag")
# Fields of a results/metrics/<step>_<job>.host line (run_step.sh).
HOST_FIELDS = ("host", "vendor", "model", "cores", "source")


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()[:20]


def read(path):
    with open(path) as f:
        return f.read()


def cache_path(cache_dir, step, key):
    return os.path.join(cache_dir, step, key + ".root")


def recipe_text(step):
    recipe = json.load(open(os.path.join("recipe", step + ".json")))
    return json.dumps({k: recipe[k] for k in RECIPE_FIELDS}, sort_keys=True)


def lookup(cache_dir, job_inputs, events, threads, code_files, steps, out_dir):
    steps = steps.split(",")
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs("results", exist_ok=True)
    step_inputs = {step: recipe_text(step) for step in steps}
    shell_hash = sha("".join(read(path) for path in code_files.split(",")))
    jobs = [line.rstrip("\n") for line in open(job_inputs) if line.strip()]

    for job, job_line in enumerate(jobs, 1):
        keys = []
        previous = sha("|".join([job_line, str(events), str(threads), shell_hash]))
        for step in steps:
            previous = sha(previous + "|" + step + "|" + step_inputs[step])
            keys.append(previous)

        cached = [os.path.exists(cache_path(cache_dir, step, key)) for step, key in zip(steps, keys)]
        last = max([i for i, hit in enumerate(cached) if hit], default=-1)
        statuses = ["skip"] * last + (["restored"] if last >= 0 else []) + ["run"] * (len(steps) - last - 1)

        # Skipped and restored steps keep the hardware they originally ran on.
        for step, status, key in zip(steps, statuses, keys):
            if status != "run":
                write_cached_host(cache_dir, step, key, job)
        if last >= 0:
            src = cache_path(cache_dir, steps[last], keys[last])
            dest = os.path.join("results", "%s_%d.root" % (steps[last], job))
            print("Job %d: restoring %s from %s" % (job, steps[last], src))
            shutil.copyfile(src, dest)
        print("Job %d (%s): %s" % (job, job_line.replace("\t", " lumis "),
                                   ", ".join("%s %s" % s for s in zip(steps, statuses))))

        with open(os.path.join(out_dir, "job_%d.txt" % job), "w") as f:
            for step, status, key in zip(steps, statuses, keys):
                f.write("%s %s %s\n" % (step, status, key))
            f.write("input %s\n" % job_line)


def read_host(path):
    """A results/metrics/<step>_<job>.host line: host, vendor, model, cores, source."""
    if not os.path.exists(path):
        return {}
    fields = open(path).readline().rstrip("\n").split("\t")
    return dict(zip(HOST_FIELDS, fields))


def write_cached_host(cache_dir, step, key, job):
    sidecar = cache_path(cache_dir, step, key)[:-len(".root")] + ".json"
    info = json.load(open(sidecar)) if os.path.exists(sidecar) else {}
    host = info.get("hardware", {})
    os.makedirs(os.path.join("results", "metrics"), exist_ok=True)
    with open(os.path.join("results", "metrics", "%s_%d.host" % (step, job)), "w") as f:
        f.write("\t".join([host.get(k, "unknown") for k in HOST_FIELDS[:4]]
                          + ["cache " + info.get("workflow", "?")]) + "\n")


def store(cache_dir, job_file, step, output, confirm):
    entries = {}
    job_line = ""
    for line in open(job_file):
        name, rest = line.rstrip("\n").split(" ", 1)
        if name == "input":
            job_line = rest
        else:
            entries[name] = rest.split(" ")
    status, key = entries[step]
    dest = cache_path(cache_dir, step, key)
    if status != "run":
        message = "%s not stored: %s" % (step, status)
    elif os.path.exists(dest):
        message = "%s already cached as %s" % (step, dest)
    else:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        # Copy under a temporary name and rename, so a partial copy is never
        # taken for a cached output.
        tmp = "%s.tmp-%d" % (dest, os.getpid())
        shutil.copyfile(output, tmp)
        os.rename(tmp, dest)
        with open(dest[:-len(".root")] + ".json", "w") as f:
            json.dump({"step": step, "input": job_line, "stored": time.strftime("%Y-%m-%d %H:%M:%S"),
                       "workflow": os.environ.get("REANA_WORKFLOW_NAME", ""),
                       "hardware": read_host(host_file_for(output))}, f, indent=1)
        message = "%s stored as %s" % (step, dest)
    print(message)
    with open(confirm, "w") as f:
        f.write(message + "\n")


def host_file_for(output):
    """results/<step>_<job>.root -> results/metrics/<step>_<job>.host"""
    name = os.path.splitext(os.path.basename(output))[0]
    return os.path.join(os.path.dirname(output), "metrics", name + ".host")


if __name__ == "__main__":
    if sys.argv[1] == "lookup":
        lookup(*sys.argv[2:9])
    elif sys.argv[1] == "store":
        store(*sys.argv[2:7])
    else:
        raise ValueError("unknown command " + sys.argv[1])
