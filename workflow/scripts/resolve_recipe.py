"""Resolves each step's recipe: CMSSW release, SCRAM arch, global tag and
config, and where each came from.

Usage: resolve_recipe.py <record_metadata.json> <reference_provenance.json> <out_dir> <steps>
           [--step-recipes JSON] [--override JSON] [--job-inputs job_inputs.txt]

Layers, a later one winning over an earlier one:
  record      the Open Data record's methodology text
  provenance  the official reference files' own provenance (reference_provenance.py)
  profile     the profile's step_recipes (config command, release per run range)
  override    the recipe_override parameter (debugging)

Writes, per step, <out_dir>/<step>.json (the recipe, with each value's source)
and <out_dir>/<step>.sh (the same recipe as shell, which run_step.sh and
stage_input.sh source: the SLC5 image has no JSON parser).
"""
import argparse
import html
import json
import os
import re
import shlex
import urllib.request

STEP_TYPE = {"prompt": "PROMPT", "reco": "RECO", "pat": "PAT", "nano": "NANO"}

# Build each release series was produced with, when the provenance names none
# (Run 1 files don't). The profile's image must match the OS in the arch.
SCRAM_ARCH_BY_RELEASE_SERIES = {
    "CMSSW_3_9_": "slc5_ia32_gcc434",  # 32-bit only
    "CMSSW_4_2_": "slc5_amd64_gcc434",
    "CMSSW_4_4_": "slc5_amd64_gcc434",
    "CMSSW_5_3_": "slc5_amd64_gcc462",
    "CMSSW_7_5_": "slc6_amd64_gcc491",
    "CMSSW_7_6_": "slc6_amd64_gcc493",
    "CMSSW_10_6_": "slc7_amd64_gcc700",
}

# Data customisation the official reprocessing added to a record's cmsDriver
# command (2010 records link no config): without it 2010 RECO differs in HF.
CUSTOMISE_BY_RELEASE_SERIES = {
    "CMSSW_4_2_": "Configuration/GlobalRuns/reco_TLR_42X.customisePPData",
}

OPENDATA_API = "https://opendata.cern.ch/api/records/"


class Recipe(object):
    """A step's recipe being resolved: each field's value and source."""

    def __init__(self, step):
        self.step = step
        self.values = {}
        self.sources = {}
        self.notes = []

    def set(self, field, value, source):
        if not value:
            return
        old = self.values.get(field)
        if old and old != value:
            self.notes.append("%s: %s (%s) instead of %s (%s)"
                              % (field, value, source, old, self.sources[field]))
        elif old == value:
            source = "%s, confirmed by %s" % (self.sources[field], source)
        self.values[field] = value
        self.sources[field] = source

    def get(self, field):
        return self.values.get(field, "")


def record_step(description, step_type):
    """(release, global tag, step block) the record's methodology text gives
    for a step, or None. Layouts:
      Run 2: <strong>Step RECO </strong><br/>Release: CMSSW_10_6_8_patch1<br/>Global tag: ...<a href="/record/N">
      Run 1: Step: RECO<br/>Release: CMSSW_5_3_7_patch5<br/>Global tag: FT_R_53_V18::All<br/><a href="/record/N">
      2010:  Step: RECO<br/>Release: ...<br/>Global tag: ...<code>cmsDriver.py reco ...</code>
    A step's block ends at the next </p>."""
    pattern = re.compile(
        r"Step:?\s*" + step_type + r"\s*(?:</strong>|<br\s*/?>).*?Release:\s*(CMSSW_[\w.]+).*?"
        r"Global tag:\s*([\w:.\-]+)(.*?)</p>", re.DOTALL)
    match = pattern.search(description)
    return match.groups() if match else None


def provenance_entry(provenance, step, process_name, cmsdriver):
    """The step's process in the reference provenance."""
    processes = provenance.get("processes", {})
    if process_name:
        return processes.get(process_name, {})
    # A given cmsDriver command may name its own process (--process RERECO).
    flag = re.search(r"--process[\s=]+(\w+)", cmsdriver or "")
    return processes.get(STEP_TYPE[step]) or processes.get(flag.group(1) if flag else "", {})


def runs_of(job_inputs):
    """Run numbers in the jobs' lumi ranges ("<path>\t<run>:<lumi>[-<run>:<lumi>],...")."""
    runs = set()
    for line in open(job_inputs):
        for run in re.findall(r"(\d+):\d+", line.split("\t", 1)[1] if "\t" in line else ""):
            runs.add(int(run))
    return runs


def release_for_runs(table, runs, step):
    """The release a profile's run-range table gives the jobs' runs."""
    releases = set()
    for run in runs:
        entry = next((e for e in table if e["first_run"] <= run <= e["last_run"]), None)
        if entry is None:
            raise ValueError("The profile's %s releases cover no range with run %d: add it" % (step, run))
        releases.add(entry["release"])
    if len(releases) > 1:
        raise ValueError("The jobs' runs %s need different %s releases %s: run them separately"
                         % (sorted(runs), step, sorted(releases)))
    return releases.pop() if releases else ""


def config_from_text(text):
    """A config given as text: a cmsDriver or RunPromptReco.py command, a
    URL, an xrootd path or a workspace file."""
    if text.startswith("cmsDriver.py"):
        return {"kind": "cmsdriver", "command": text}
    if text.startswith("RunPromptReco.py"):
        return {"kind": "promptreco", "command": text}
    if text.startswith("root://"):
        return {"kind": "xrootd", "uri": text}
    if text.startswith("http"):
        return {"kind": "url", "uri": text}
    return {"kind": "workspace_file", "path": text}


def config_record_uri(recid):
    with urllib.request.urlopen(OPENDATA_API + recid, timeout=120) as resp:
        files = json.load(resp)["metadata"]["files"]
    if not files:
        raise ValueError("Configuration record %s has no files" % recid)
    return files[0]["uri"]


def resolve(step, meta, provenance, step_recipe, override, runs):
    recipe = Recipe(step)
    step_type = STEP_TYPE[step]

    found = record_step(meta["methodology"]["description"], step_type)
    record_gt = None
    if found:
        release, record_gt, block = found
        recipe.set("release", release, "record")
        recipe.set("global_tag", record_gt, "record")
        recid = re.search(r"/record/(\d+)", block)
        cmsdriver = re.search(r"<code>\s*(cmsDriver\.py\s.*?)\s*</code>", block, re.DOTALL)
        if recid:
            recipe.set("config", {"kind": "xrootd", "uri": config_record_uri(recid.group(1))},
                       "record (config record %s)" % recid.group(1))
        elif cmsdriver:
            recipe.set("config", {"kind": "cmsdriver", "command": html.unescape(cmsdriver.group(1))},
                       "record (cmsDriver command)")

    given_config = override.get("config") or step_recipe.get("config", "")
    entry = provenance_entry(provenance, step, step_recipe.get("provenance_process", ""),
                             given_config if given_config.startswith("cmsDriver.py") else "")
    if provenance and not entry:
        recipe.notes.append("no %s process in the reference provenance" % step_type)
    recipe.set("release", entry.get("release", ""), "reference provenance")
    recipe.set("global_tag", entry.get("globaltag", ""), "reference provenance")

    if step_recipe.get("config"):
        recipe.set("config", config_from_text(step_recipe["config"]), "profile")
    if step_recipe.get("releases"):
        recipe.set("release", release_for_runs(step_recipe["releases"], runs, step), "profile (runs %s)"
                   % ",".join(str(r) for r in sorted(runs)))

    for field in ("release", "global_tag", "scram_arch"):
        recipe.set(field, override.get(field, ""), "recipe_override")
    if override.get("config"):
        recipe.set("config", config_from_text(override["config"]), "recipe_override")

    release = recipe.get("release")
    if not release or not recipe.get("global_tag"):
        raise ValueError("No %s release/global tag: the record gives no %s step and the reference provenance "
                         "has none; set them in recipe_override" % (step_type, step_type))
    if not recipe.get("config"):
        raise ValueError("No %s config: the record links none; give one in the profile's step_recipes "
                         "or recipe_override" % step_type)

    # The build the reference was made with: a release can exist for several
    # compilers, which round floats differently.
    if not recipe.get("scram_arch"):
        if entry.get("scram_arch") and entry.get("release") == release:
            recipe.set("scram_arch", entry["scram_arch"], "reference provenance")
        else:
            arch = next((a for series, a in SCRAM_ARCH_BY_RELEASE_SERIES.items() if release.startswith(series)), "")
            if not arch:
                raise ValueError("No SCRAM_ARCH known for %s: add its series to SCRAM_ARCH_BY_RELEASE_SERIES "
                                 "or set scram_arch in recipe_override" % release)
            recipe.set("scram_arch", arch, "release series table")

    # The config sets the record's global tag (or its own): set the one used.
    recipe.values["set_global_tag"] = recipe.get("global_tag") != record_gt
    recipe.values["record_global_tag"] = record_gt
    return recipe


def fetch_lines(step, config, release, global_tag):
    """Shell lines writing <step>_config.py in the step's CMSSW area (after cmsenv)."""
    out = step + "_config.py"
    kind = config["kind"]
    if kind == "xrootd":
        return ['xrdcp -f "%s" %s' % (config["uri"], out)]
    if kind == "url":
        return ['curl -sSfL -o %s "%s"' % (out, config["uri"])]
    if kind == "workspace_file":
        return ['cp "$WORKDIR/%s" %s' % (config["path"], out)]
    if kind == "promptreco":
        # The release's Tier-0 test script dumps the scenario's process as
        # RunPromptRecoCfg.py; its input LFN is replaced by the step's input.
        args = shlex.split(config["command"])[1:]
        if "--global-tag" not in args:
            args += ["--global-tag", global_tag]
        if "--lfn" not in args:
            args += ["--lfn", "file:input.root"]
        return ["python $CMSSW_RELEASE_BASE/src/Configuration/DataProcessing/test/RunPromptReco.py "
                + " ".join(shlex.quote(a) for a in args),
                "mv RunPromptRecoCfg.py %s" % out,
                # A dumped process may lack the PSets run_step sets.
                "echo 'process.maxEvents = cms.untracked.PSet(input = cms.untracked.int32(-1))' >> %s" % out,
                "echo 'if not hasattr(process, \"options\"): process.options = cms.untracked.PSet()' >> %s" % out]
    # cmsDriver: write the config under our name, without running it.
    args = shlex.split(config["command"])
    if "--python_filename" in args:
        args[args.index("--python_filename")] = "--python"
    if "--python" in args:
        del args[args.index("--python"):args.index("--python") + 2]
    if "--no_exec" not in args:
        args.append("--no_exec")
    customise = next((c for series, c in CUSTOMISE_BY_RELEASE_SERIES.items() if release.startswith(series)), None)
    if customise and "--customise" not in args:
        args += ["--customise", customise]
    # Several event contents need as many data tiers.
    if "--eventcontent" in args and "--datatier" not in args:
        contents = args[args.index("--eventcontent") + 1]
        if "," in contents:
            args += ["--datatier", contents]
    args += ["--python", out]
    return [" ".join(shlex.quote(a) for a in args)]


RUN_32BIT = '''    # 32-bit build: SCRAM and cmsRun run with a 32-bit personality (setarch,
    # or Perl's personality(PER_LINUX32) where the image has no setarch).
    run32() { if command -v setarch > /dev/null; then setarch i686 "$@"; else perl -e 'syscall(135, 8) == -1 and die "personality: $!\\n"; exec @ARGV or die "exec $ARGV[0]: $!\\n"' "$@"; fi; }
    scram() { run32 scram "$@"; }
    scramv1() { run32 scramv1 "$@"; }
    cmsRun() { run32 cmsRun "$@"; }
'''


def shell_recipe(recipe):
    v = recipe.values
    lines = fetch_lines(recipe.step, v["config"], v["release"], v["global_tag"])
    if v["set_global_tag"]:
        line = 'process.GlobalTag.globaltag = "%s"  # record said %s' % (v["global_tag"], v["record_global_tag"])
        lines.append("echo %s >> %s_config.py" % (shlex.quote(line), recipe.step))
    return "\n".join([
        "# %s recipe, generated by resolve_recipe.py from %s.json" % (recipe.step, recipe.step),
        "RELEASE=%s" % shlex.quote(v["release"]),
        "SCRAM_ARCH=%s" % shlex.quote(v["scram_arch"]),
        "GLOBAL_TAG=%s" % shlex.quote(v["global_tag"]),
        "CONFIG_FILE=%s_config.py" % recipe.step,
        "",
        "# Creates the release area and changes into its src/.",
        "setup_release() {",
        RUN_32BIT.rstrip("\n") if "_ia32_" in v["scram_arch"] else "    :",
        "    export SCRAM_ARCH",
        "    source /cvmfs/cms.cern.ch/cmsset_default.sh",
        '    scram p CMSSW "$RELEASE" && cd "$RELEASE/src/"',
        "}",
        "",
        "# Writes $CONFIG_FILE in the current directory (after cmsenv).",
        "fetch_config() {",
        "\n".join("    " + l for l in lines),
        "}",
        "",
    ])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("metadata")
    parser.add_argument("provenance")
    parser.add_argument("out_dir")
    parser.add_argument("steps")
    parser.add_argument("--step-recipes", default="{}")
    parser.add_argument("--override", default="{}")
    parser.add_argument("--job-inputs", default="")
    args = parser.parse_args()

    meta = json.load(open(args.metadata))
    meta = meta.get("metadata", meta)
    provenance = json.load(open(args.provenance)) if args.provenance else {}
    step_recipes = json.loads(args.step_recipes)
    overrides = json.loads(args.override)
    runs = runs_of(args.job_inputs) if args.job_inputs else set()
    os.makedirs(args.out_dir, exist_ok=True)
    for step in args.steps.split(","):
        recipe = resolve(step, meta, provenance, step_recipes.get(step, {}), overrides.get(step, {}), runs)
        v = recipe.values
        out = {"step": step, "release": v["release"], "scram_arch": v["scram_arch"],
               "global_tag": v["global_tag"], "config": v["config"], "set_global_tag": v["set_global_tag"],
               "sources": recipe.sources, "notes": recipe.notes}
        with open(os.path.join(args.out_dir, step + ".json"), "w") as f:
            json.dump(out, f, indent=1, sort_keys=True)
        with open(os.path.join(args.out_dir, step + ".sh"), "w") as f:
            f.write(shell_recipe(recipe))
        print("%s: %s (%s), global tag %s, config from %s" % (step, v["release"], v["scram_arch"],
                                                            v["global_tag"], recipe.sources["config"]))
        for field in ("release", "scram_arch", "global_tag"):
            print("  %-10s from %s" % (field, recipe.sources[field]))
        for note in recipe.notes:
            print("  NOTE: " + note)


if __name__ == "__main__":
    main()
