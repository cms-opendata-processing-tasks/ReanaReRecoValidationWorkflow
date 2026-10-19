"""RAW parent and its smallest files with validated data, for every CMS Open Data dataset.

For each collision dataset on CERN Open Data :
  1. its RAW parent, following DBS dataset parents until the RAW tier,
  2. the RAW parent's files with sizes; the run of a RAW file
     is in its path (.../RAW/v1/000/194/643/... = run 194643), so files of
     runs without validated lumis are dropped without any lookup,
  3. the lumi sections of the smallest remaining files,
     compared with the validated-runs list,
  4. the smallest --per-dataset files with at least --min-validated validated
     lumi sections (or events, with --unit events) are kept.

Search iterates a few times if no inital files found. Reruns don't redo already found files.

Usage 
  python3 raw_parent_files.py <out.csv>
          [--min-validated N] [--unit lumis|events] [--per-dataset K]
          [--candidates C] [--max-candidates M] [--datasets /A/B/C,...] [--workers W]

Queries done through DBS reader instead of dasgoclient for speed.

Writes results to <out.csv> 
"""

import argparse
import concurrent.futures
import csv
import json
import os
import re
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request


API = "https://opendata.cern.ch/api/records/"


def get_json(url, retries=4):
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(url, timeout=300) as resp:
                return json.load(resp)
        except Exception:
            if attempt == retries:
                raise
            time.sleep(10 * attempt)


def list_datasets(subtypes):
    """Minimal metadata of every CMS dataset record of the given subtypes.
    Search results include the full file indices (~1 MB per record), so
    pages are kept small."""
    out = []
    for subtype in subtypes:
        page = 1
        while True:
            d = get_json(API + "?type=Dataset::%s&experiment=CMS&size=25&page=%d" % (subtype, page))
            hits = d["hits"]["hits"]
            if not hits:
                break
            for h in hits:
                m = h["metadata"]
                files = []
                for index in m.get("_file_indices") or []:
                    files += [(f["uri"], f.get("size", 0)) for f in index.get("files", [])]
                files += [(f["uri"], f.get("size", 0)) for f in m.get("files") or []]
                files = sorted(set(f for f in files if f[0].endswith(".root")), key=lambda f: f[1])

                out.append({"recid": m["recid"], "title": m["title"], "subtype": subtype,
                            "validated_recids": linked_recids(m),
                            "n_files": len(files), "files": files})
            print("  %s page %d: %d records so far" % (subtype, page, len(out)), flush=True)
            total = d["hits"]["total"]
            if page * 25 >= (total["value"] if isinstance(total, dict) else total):
                break
            page += 1

    return out


def linked_recids(meta):
    """Records linked from the fields that point to the validated-runs list"""
    out = []
    for field in ("note", "abstract", "validation"):
        for link in (meta.get(field) or {}).get("links", []) or []:
            if link.get("recid") and str(link["recid"]) not in out:
                out.append(str(link["recid"]))
    return out


_lists = {}
_lists_lock = threading.Lock()


def validated_list(recid):
    """{run: [[first, last], ...]} from a validated-runs record."""
    with _lists_lock:
        if recid in _lists:
            return _lists[recid]
    meta = get_json(API + recid)["metadata"]

    if "validated runs" not in meta.get("title", "").lower():
        raise ValueError("record %s is not a list of validated runs (%s)" % (recid, meta.get("title", "")))
    uris = [f["uri"] for f in meta.get("files") or [] if f["uri"].endswith((".txt", ".json"))]

    if not uris:
        raise ValueError("validated-runs record %s has no JSON file" % recid)
    
    data = get_json(uris[0].replace("root://eospublic.cern.ch//", "https://opendata.cern.ch/"))
    lists = {int(run): ranges for run, ranges in data.items()}
    with _lists_lock:
        _lists[recid] = (meta.get("title", ""), lists)
    return _lists[recid]


def to_ranges(lumis):
    ranges = []
    for run, lumi in lumis:
        if ranges and ranges[-1][0] == run and ranges[-1][2] == lumi - 1:
            ranges[-1][2] = lumi
        else:
            ranges.append([run, lumi, lumi])
    return ",".join("%d:%d-%d:%d" % (run, a, run, b) for run, a, b in ranges)


# --- DBS -------------------------------------------------------------------------

DBS = "https://cmsweb.cern.ch/dbs/prod/global/DBSReader"
RUN_IN_PATH = re.compile(r"/(\d{3})/(\d{3})/(\d{3})/")
LUMI_CHUNK = 100    # files per bulk filelumis call
GROWTH = 4          # each further batch of candidates is this many times larger

FINAL_STATUSES = ("ok", "no validated-runs list", "no RAW parent", "RAW parent has no files",
                  "no files in validated runs", "all ")


class Dbs:
    def __init__(self, base, proxy=None, capath=None):
        self.base = base.rstrip("/")
        self.ctx = None
        
        proxy = proxy or os.environ.get("X509_USER_PROXY") or "/tmp/x509up_u%d" % os.getuid()
        self.ctx = ssl.create_default_context(
                capath=capath or os.environ.get("X509_CERT_DIR", "/etc/grid-security/certificates"))
        self.ctx.load_cert_chain(proxy)

    def call(self, request, attempts=3):
        """JSON answer of a request; transient failures are retried"""
        for attempt in range(1, attempts + 1):
            try:
                with urllib.request.urlopen(request, context=self.ctx, timeout=600) as resp:
                    return json.load(resp)
            except urllib.error.HTTPError as e:
                if e.code < 500 or attempt == attempts:
                    raise
            except Exception:
                if attempt == attempts:
                    raise
            time.sleep(5 * attempt)

    def get(self, api, **params):
        return self.call("%s/%s?%s" % (self.base, api, urllib.parse.urlencode(params, doseq=True)))

    def post(self, api, body):
        return self.call(urllib.request.Request("%s/%s" % (self.base, api), data=json.dumps(body).encode(),
                                                headers={"Content-Type": "application/json"}))


def run_of(lfn):
    m = RUN_IN_PATH.search(lfn)
    return int("".join(m.groups())) if m else None


class Finder:
    def __init__(self, dbs, args):
        self.dbs, self.args = dbs, args
        self.parents = {}
        self.raw_results = {}
        self.locks = {}
        self.lock = threading.Lock()

    def raw_parent(self, dataset):
        chain = [dataset]
        while not chain[-1].endswith("/RAW") and len(chain) < 6:
            current = chain[-1]
            if current not in self.parents:
                rows = self.dbs.get("datasetparents", dataset=current)
                self.parents[current] = sorted(r["parent_dataset"] for r in rows)
            ps = self.parents[current]

            if not ps:
                return None, chain
            raws = [p for p in ps if p.endswith("/RAW")]
            chain.append((raws or ps)[0])

        return (chain[-1] if chain[-1].endswith("/RAW") else None), chain

    def lumis_of(self, lfns):
        """{lfn: [(run, lumi, events or None)]}: bulk calls of LUMI_CHUNK
        files, per file if the bulk form isn't accepted."""
        rows = []
        for i in range(0, len(lfns), LUMI_CHUNK):
            chunk = lfns[i:i + LUMI_CHUNK]
            try:
                rows += self.dbs.post("filelumis", {"logical_file_name": chunk})
            except urllib.error.HTTPError:
                for lfn in chunk:
                    rows += self.dbs.get("filelumis", logical_file_name=lfn)
        out = {}
        for r in rows:
            nums = r["lumi_section_num"] if isinstance(r["lumi_section_num"], list) else [r["lumi_section_num"]]
            counts = r.get("event_count")
            counts = counts if isinstance(counts, list) else [counts] * len(nums)
            out.setdefault(r["logical_file_name"], []).extend(
                (int(r["run_num"]), int(n), c) for n, c in zip(nums, counts))
        return out

    def select(self, raw, vrecid):
        key = (raw, vrecid)
        with self.lock:
            lock = self.locks.setdefault(key, threading.Lock())
        with lock:
            if key not in self.raw_results:
                self.raw_results[key] = self._select(raw, vrecid)
            return self.raw_results[key]

    def _select(self, raw, vrecid):
        args = self.args
        _, lists = validated_list(vrecid)
        files = [f for f in self.dbs.get("files", dataset=raw, detail=1)
                 if f.get("is_file_valid", 1) and f.get("event_count")]
        in_validated_runs = [f for f in files if run_of(f["logical_file_name"]) in lists
                             or run_of(f["logical_file_name"]) is None]
        ordered = sorted(in_validated_runs, key=lambda f: f["file_size"])
        result = {"n_raw_files": len(files), "n_in_validated_runs": len(in_validated_runs), "files": []}

        if not ordered:
            result["status"] = "no files in validated runs" if files else "RAW parent has no files in DBS"
            return result
        
        limit = min(args.max_candidates, len(ordered))
        checked, batch = 0, args.candidates

        while checked < limit and len(result["files"]) < args.per_dataset:
            # The next batch of candidates, each batch GROWTH times larger.
            new = ordered[checked:min(checked + batch, limit)]
            self.check(new, lists, result)
            checked += len(new)
            batch *= GROWTH

        result["checked"] = checked
        n = len(result["files"])

        if n == args.per_dataset:
            result["status"] = "ok"
        elif checked == len(ordered):
            # Every file in validated runs checked: nothing more to find.
            result["status"] = "all %d files in validated runs checked: %d of %d found" % (checked, n, args.per_dataset)
        else:
            scope = "the %d smallest files in validated runs" % checked
            result["status"] = ("only %d of %d files in %s" % (n, args.per_dataset, scope) if n else
                                "no file with %d validated %s in %s" % (args.min_validated, args.unit, scope))
        return result

    def check(self, candidates, lists, result):
        """Add the candidates with enough validated data to result["files"]."""
        args = self.args
        lumis = self.lumis_of([f["logical_file_name"] for f in candidates])
        for f in candidates:
            lfn = f["logical_file_name"]
            entries = lumis.get(lfn, [])
            good = [(r, l, c) for r, l, c in entries if any(a <= l <= b for a, b in lists.get(r, []))]
            if not good:
                continue
            if all(c is not None for _, _, c in good):
                vevents, estimated = sum(c for _, _, c in good), False
            else:
                vevents, estimated = int(round(f["event_count"] * len(good) / len(entries))), True
            if (len(good) if args.unit == "lumis" else vevents) < args.min_validated:
                continue
            result["files"].append({
                "lfn": lfn, "size": f["file_size"], "events": f["event_count"], "lumis": len(entries),
                "validated_lumis": len(good), "validated_events": vevents, "events_estimated": estimated,
                "validated_ranges": to_ranges(sorted((r, l) for r, l, _ in good))})
            if len(result["files"]) == args.per_dataset:
                return

    def process(self, ds):
        entry = {"recid": ds["recid"], "dataset": ds["title"], "raw_parent": "", "files": []}
        vrecid = next((r for r in ds.get("validated_recids") or [] if _is_list(r)), None)
        if vrecid is None:
            entry["status"] = "no validated-runs list linked from the Open Data record"
            return entry
        
        entry["validated_runs_recid"] = vrecid
        try:
            raw, chain = self.raw_parent(ds["title"])
            entry["chain"] = chain
            if raw is None:
                entry["status"] = "no RAW parent in DBS"
                return entry
            entry["raw_parent"] = raw
            sel = self.select(raw, vrecid)

        except Exception as e:
            entry["status"] = "DBS lookup failed: %s" % str(e)[:200]
            return entry
        
        entry["files"] = sel["files"]
        entry.update(status=sel["status"], n_raw_files=sel["n_raw_files"], n_in_validated_runs=sel["n_in_validated_runs"],
                     checked=sel.get("checked", 0))
        return entry


def _is_list(recid):
    try:
        validated_list(recid)
        return True
    except Exception:
        return False


COLUMNS = ["recid", "dataset", "raw_parent", "file", "size_GB", "events", "lumis", "validated_lumis",
           "validated_events", "validated_ranges", "status"]


def to_rows(entry):
    """CSV rows of a dataset"""
    base = {"recid": entry["recid"], "dataset": entry["dataset"], "raw_parent": entry["raw_parent"],
            "status": entry["status"]}
    if not entry["files"]:
        return [dict(base)]
    return [dict(base, file=x["lfn"], size_GB="%.2f" % (x["size"] / 1e9),
                 events=x["events"], lumis=x["lumis"], validated_lumis=x["validated_lumis"],
                 validated_events="%s%s" % (x["validated_events"], " (est)" if x["events_estimated"] else ""),
                 validated_ranges=x["validated_ranges"]) for x in entry["files"]]


def read_csv(path):
    results = {}
    if os.path.exists(path):
        with open(path, newline="") as f:
            for row in csv.DictReader(f):
                results.setdefault(row["dataset"], []).append(row)
    return results


def write_csv(path, results):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, COLUMNS)
        w.writeheader()
        for dataset in sorted(results):
            w.writerows(results[dataset])


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("out_csv")
    p.add_argument("--min-validated", type=int, default=1, help="validated lumis (or events) a file needs (default 1)")
    p.add_argument("--unit", choices=["lumis", "events"], default="lumis")
    p.add_argument("--per-dataset", type=int, default=1, help="files per dataset (default 1)")
    p.add_argument("--candidates", type=int, default=50, help="smallest files checked first (default 50)")
    p.add_argument("--max-candidates", type=int, default=1000,
                   help="most files checked per RAW parent, in growing batches (default 1000)")
    p.add_argument("--datasets", default="", help="only these dataset names (comma-separated)")
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--dbs-url", default=DBS, help=argparse.SUPPRESS)   # for tests
    args = p.parse_args()

    
    print("Listing the Open Data collision datasets")
    datasets = list_datasets(["Collision"])
    datasets = [d for d in datasets if d["title"].count("/") == 3]
    if args.datasets:
        wanted = set(args.datasets.split(","))
        datasets = [d for d in datasets if d["title"] in wanted]

    results = read_csv(args.out_csv)
    todo = [d for d in datasets if not results.get(d["title"], [{}])[0].get("status", "").startswith(FINAL_STATUSES)]
    redo = sum(d["title"] in results for d in todo)
    print("%d datasets, %d to do (%d of them redone: not enough files or a DBS error before)"
          % (len(datasets), len(todo), redo), flush=True)

    finder = Finder(Dbs(args.dbs_url), args)
    with concurrent.futures.ThreadPoolExecutor(args.workers) as pool:
        for i, r in enumerate(pool.map(finder.process, todo), 1):
            results[r["dataset"]] = to_rows(r)
            f0 = r["files"][0] if r["files"] else None
            print("[%d/%d] %-58s -> %-38s %s" % (i, len(todo), r["dataset"][:58], (r["raw_parent"] or "-")[:38],
                  "%.2f GB, %d validated lumis" % (f0["size"] / 1e9, f0["validated_lumis"]) if f0 else r["status"]),
                  flush=True)
            if i % 20 == 0:
                write_csv(args.out_csv, results)
    write_csv(args.out_csv, results)

    statuses = {}
    for rows in results.values():
        key = rows[0]["status"].split(":")[0]
        for prefix, label in (("no file with", "no file with enough validated data (more candidates may help)"),
                              ("only", "fewer files than asked (more candidates may help)"),
                              ("all ", "not enough files with validated data among all files")):
            key = label if key.startswith(prefix) else key
        statuses[key] = statuses.get(key, 0) + 1
    print("\nSummary (%d RAW parents looked up):" % len(finder.raw_results))
    for s, n in sorted(statuses.items(), key=lambda kv: -kv[1]):
        print("  %4d  %s" % (n, s))


if __name__ == "__main__":
    main()
 