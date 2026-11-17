import json
import urllib.request

# The CMS lists of validated runs, shared by plan_inputs.py and
# resolve_raw.py. Python 3.6 compatible: resolve_raw.py runs in the CC7 CMSSW
# image (for dasgoclient).


def load_list(location):
    """A validated-runs JSON: an /eos/opendata path (fetched over HTTPS),
    an http(s) URL, or a local file."""
    if location.startswith("/eos/opendata/"):
        location = "https://opendata.cern.ch" + location
    if location.startswith("http"):
        with urllib.request.urlopen(location, timeout=120) as resp:
            return json.load(resp)
    with open(location) as f:
        return json.load(f)


class ValidatedRuns:
    """Validated lumi ranges per run, from the profile's table of lists
    ([{"first_run", "last_run", "file"}, ...])."""

    def __init__(self, table):
        self.table = table
        self.loaded = {}

    def ranges(self, run):
        """Validated lumi ranges of a run ([] if the run isn't validated)."""
        entry = next((e for e in self.table if e["first_run"] <= run <= e["last_run"]), None)
        if entry is None:
            raise ValueError("No list of validated runs covers run %d: add one to the profile's validated_runs" % run)
        location = entry["file"]
        if location not in self.loaded:
            print("Loading validated runs from " + location)
            self.loaded[location] = load_list(location)
        return self.loaded[location].get(str(run), [])
