"""Workflow parameters: parsing, profile choice and validation.

The Snakefile calls load_settings(config) once; rules only read the
returned Settings. Plain Python (3.8, the REANA engine's)
"""
import glob
import http.client
import json
import os
import time
import urllib.request

import yaml

OPENDATA_API = "https://opendata.cern.ch/api/records/"
REANA_COMPAT_VERSIONS = ("0.9", "none")
COMPARISON_TIERS = ("nanoaod", "aod")
# Recipe fields a recipe_override may set, per step.
OVERRIDE_FIELDS = ("release", "global_tag", "scram_arch", "config")
# reana-client parses the Snakefile with no parameters to validate it: then
# there is no record to choose the profile by.
FALLBACK_PROFILE = "run2_ul_nanoaod"


def fetch_record(recid, attempts=3):
    """The record's metadata from the Open Data API (all of it: the API can't
    select fields, and file indices make some records 16 MB)."""
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(OPENDATA_API + str(recid), timeout=120) as resp:
                return json.load(resp)["metadata"]
        except (OSError, ValueError, http.client.HTTPException) as error:
            if attempt == attempts - 1:
                raise
            print("Open Data API: %r, retrying" % error)
            time.sleep(5)


def load_profiles(profiles_dir):
    profiles = {}
    for path in sorted(glob.glob(os.path.join(profiles_dir, "*.yaml"))):
        with open(path) as f:
            profiles[os.path.splitext(os.path.basename(path))[0]] = yaml.safe_load(f)
    return profiles


def profile_for_record(meta, profiles):
    """The profile whose "records" block matches the record's year
    (date_created), collision type and data tier."""
    year = int(meta["date_created"][0])
    collision = meta["collision_information"]["type"]
    tier = meta["title"].rsplit("/", 1)[-1]
    matches = [name for name, p in profiles.items()
               if any(year in s["years"] and collision in s["collisions"] and tier in s["tiers"]
                      for s in p.get("records", []))]
    if len(matches) != 1:
        raise ValueError("Record %s (%s, %d, %s) matches profiles %s: pass the profile parameter"
                         % (meta.get("recid", "?"), meta["title"], year, collision, matches))
    return matches[0]


def mgm_for(path):
    """The EOS instance serving a bare /eos/... path."""
    return "root://eospublic.cern.ch" if path.startswith("/eos/opendata/") else "root://eoscms.cern.ch"


def parse_override(value):
    """recipe_override: {step: {field: value}}, as a mapping or a JSON string."""
    if not value:
        return {}
    override = json.loads(value) if isinstance(value, str) else dict(value)
    for step, fields in override.items():
        unknown = set(fields) - set(OVERRIDE_FIELDS)
        if unknown:
            raise ValueError("recipe_override for %s: unknown fields %s (allowed: %s)"
                             % (step, sorted(unknown), ", ".join(OVERRIDE_FIELDS)))
    return override


def text(config, key, default=""):
    value = config.get(key, default)
    return default if value is None else str(value).strip()


class Settings(object):
    """Everything the rules need, resolved from the parameters and profile."""

    def __init__(self, config, workflow_dir="workflow", workspace=".", fetch=fetch_record):
        self.record_id = text(config, "record_id")
        self.profile, self.record_title = self._choose_profile(config, workflow_dir, fetch)
        with open(os.path.join(workflow_dir, "profiles", self.profile + ".yaml")) as f:
            profile = yaml.safe_load(f)
        self.cmssw_image = profile["cmssw_image"]
        self.steps = list(profile["steps"])
        self.comparison_tier = profile["comparison_tier"]
        self.validated_runs_table = profile.get("validated_runs", [])
        self.step_recipes = profile.get("step_recipes", {})
        self.first_step, self.final_step = self.steps[0], self.steps[-1]
        if self.comparison_tier not in COMPARISON_TIERS:
            raise ValueError("comparison_tier must be one of %s (got '%s')"
                             % (", ".join(COMPARISON_TIERS), self.comparison_tier))
        if self.comparison_tier == "nanoaod" and self.final_step != "nano":
            raise ValueError("A nanoaod comparison needs the chain to end with the nano step")

        self.num_files = int(text(config, "num_files", "1"))
        self.events_per_job = int(text(config, "eventsPerJob", "1"))
        self.jobs = list(range(1, self.num_files + 1))

        # RAW input: a curated list under workflow/raw_files/, or else the
        # record's RAW parent dataset in DAS (resolve_raw, at run time).
        self.raw_file_list = text(config, "raw_file_list")
        self.resolve_raw = not self.raw_file_list
        if self.resolve_raw:
            self.input_files_path = "raw_input_files.txt"
            self.eos_mgm_url = mgm_for("/eos/cms/")
        else:
            self.input_files_path = os.path.join(workflow_dir, "raw_files", self.raw_file_list)
            with open(self.input_files_path) as f:
                files = [line.strip() for line in f if line.strip()]
            if self.num_files > len(files):
                raise ValueError("num_files (%d) exceeds the %d files listed in %s"
                                 % (self.num_files, len(files), self.input_files_path))
            mgms = set(mgm_for(p) for p in files)
            if len(mgms) != 1:
                raise ValueError("%s mixes EOS instances %s" % (self.input_files_path, sorted(mgms)))
            self.eos_mgm_url = mgms.pop()

        # Results go to their own subfolder of eos_outdir, by default named
        # after the input and record plus the start of the REANA workspace id.
        # Required in a real run; reana-client's validation parse has no parameters at all.
        self.eos_outdir = text(config, "eos_outdir")
        if config and not self.eos_outdir.startswith("/eos/"):
            raise ValueError("eos_outdir must be an EOS directory you can write to, e.g. "
                             "-p eos_outdir=/eos/user/<x>/<you>/validation (got '%s')" % self.eos_outdir)
        input_name = "dasraw" if self.resolve_raw else os.path.splitext(os.path.basename(self.raw_file_list))[0]
        self.results_name = text(config, "results_name") or "%s_rec%s_%s" % (
            input_name, self.record_id or "0", os.path.basename(os.path.abspath(workspace))[:8])
        self.eos_path = os.path.join(self.eos_outdir, self.results_name)

        self.cache_dir = text(config, "cache_dir")
        self.htcondor_max_runtime = text(config, "htcondor_max_runtime", "workday")
        # "0.9": the REANA 0.9.4 workarounds (rules/reana094_htcondor.smk).
        compat = config.get("reana_compat", "0.9")
        self.reana_compat = str(compat).strip() if compat else "none"
        if self.reana_compat not in REANA_COMPAT_VERSIONS:
            raise ValueError("reana_compat must be one of %s (got '%s')" % (REANA_COMPAT_VERSIONS, self.reana_compat))

        # Debugging only.
        self.lumis_to_process = text(config, "lumis_to_process")
        self.recipe_override = parse_override(config.get("recipe_override", ""))
        unknown = set(self.recipe_override) - set(self.steps)
        if unknown:
            raise ValueError("recipe_override names steps %s not in the profile's %s" % (sorted(unknown), self.steps))

    def _choose_profile(self, config, workflow_dir, fetch):
        given = text(config, "profile")
        if not self.record_id:
            return given or FALLBACK_PROFILE, ""
        if given:
            return given, ""
        meta = fetch(self.record_id)
        meta.setdefault("recid", self.record_id)
        name = profile_for_record(meta, load_profiles(os.path.join(workflow_dir, "profiles")))
        print("Profile %s for record %s (%s)" % (name, self.record_id, meta["title"]))
        return name, meta["title"]

    def step_before(self, step):
        """The step whose output this step reads ("" for the first)."""
        i = self.steps.index(step)
        return self.steps[i - 1] if i else ""


def load_settings(config, workflow_dir="workflow", workspace="."):
    return Settings(config, workflow_dir, workspace)
