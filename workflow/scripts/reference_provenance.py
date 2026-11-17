import json
import re
import sys
import urllib.request

import uproot

# Usage: reference_provenance.py <reference_urls.txt> <provenance.json> [max_files] [record_metadata.json]
#
# The release, global tag and SCRAM arch each production step of the
# official reference was really made with, read from the reference files' own provenance (EDM
# files: AOD, RECO, MiniAOD). Open Data records can state a wrong recipe,
# e.g. /VBF1Parked/Run2012D-22Jan2013-v1/AOD says CMSSW_5_3_7_patch5 /
# FT_R_53_V18 while its files were made with CMSSW_5_3_7_patch6 / FT_R_53_V21.
#
#   MetaData/ProcessConfiguration (or ProcessHistory in newer files):
#                  process names and their releases
#   ParameterSets: each process's configuration, which references its
#                  GlobalTag ES source (PoolDBESSource@GlobalTag=+Q(<id>)),
#                  a separate entry holding globaltag=+S(<hex>), and its
#                  modules, whose FileInPath paths name the SCRAM arch
#
# Flat NanoAOD files have no provenance: then the files of the parent
# dataset the record links (its MiniAOD, which holds the RECO and PAT
# history) are read instead, given the record metadata.
#
# Writes {"processes": {"RECO": {"release": ..., "globaltag": ..., "scram_arch": ...}, ...},
#         "files": [...], "notes": [...]}.
urls_file, out_json = sys.argv[1], sys.argv[2]
max_files = int(sys.argv[3]) if len(sys.argv) > 3 else 2
metadata_json = sys.argv[4] if len(sys.argv) > 4 else ""


def hexstr(h):
    return bytes.fromhex(h.decode()).decode(errors="replace")


def name_release_pairs(raw):
    """(process name, release) pairs of streamed ProcessConfiguration or
    ProcessHistory data: names are short length-prefixed identifiers,
    releases quoted "CMSSW_..." strings, stored as a run of names followed
    by a run of releases in the same order."""
    rel = [(m.start(), m.end(), m.group(1).decode()) for m in re.finditer(rb'"(CMSSW_[0-9A-Za-z_]+)"', raw)]
    toks = [(a, "rel", r) for a, _, r in rel]
    i = 0
    while i < len(raw):
        n = raw[i]
        chunk = raw[i + 1:i + 1 + n]
        if (2 <= n <= 40 and len(chunk) == n and re.fullmatch(rb"[A-Za-z][A-Za-z0-9]*", chunk)
                and not any(a <= i < b for a, b, _ in rel)):
            toks.append((i, "name", chunk.decode()))
            i += 1 + n
        else:
            i += 1
    runs = []
    for _, kind, val in sorted(toks):
        if runs and runs[-1][0] == kind:
            runs[-1][1].append(val)
        else:
            runs.append((kind, [val]))
    pairs = []
    for (k1, names), (k2, releases) in zip(runs, runs[1:]):
        if k1 == "name" and k2 == "rel" and len(names) == len(releases):
            pairs += list(zip(names, releases))
    return pairs


# SCRAM architecture in the FileInPath parameters' resolved paths (e.g.
# ".../external/slc7_amd64_gcc820/data/..."). The same release can exist for
# several compilers, whose output differs at float-rounding level: the 2016
# UL RECO ran CMSSW_10_6_8_patch1 on slc7_amd64_gcc820, its MiniAOD on gcc700.
ARCH_PATTERN = re.compile(rb"/((?:slc|cc|el|cs)\d+_[a-z0-9]+_gcc\d+)/")


def new_process():
    return {"releases": set(), "globaltags": set(), "scram_archs": set()}


def processes_of(f):
    """{process name: {"releases", "globaltags", "scram_archs"}} of one EDM file."""
    md, ps = f["MetaData"], f["ParameterSets"]
    branch = "ProcessConfiguration" if "ProcessConfiguration" in md.keys() else "ProcessHistory"
    raw = b"".join(bytes(x) for x in md[branch].array(uproot.AsJagged(uproot.AsDtype("u1")), library="np"))
    procs = {}
    for name, rel in name_release_pairs(raw):
        procs.setdefault(name, new_process())["releases"].add(rel)

    entries = [bytes(x) for x in ps["IdToParameterSetsBlobs"].array(uproot.AsJagged(uproot.AsDtype("u1")), library="np")]
    # Each entry is a streamed pair<ParameterSetID, blob>: the 16-byte ID
    # (length byte 0x10) at offset 12.
    by_id = {e[13:29]: e for e in entries if len(e) > 29 and e[12] == 0x10}
    for e in entries:
        m = re.search(rb"@process_name=\+S\(([0-9A-F]+)\)", e)
        if not m:
            continue
        name = hexstr(m.group(1))
        refs = re.findall(rb"PoolDBESSource@GlobalTag=\+[PQ]\(([0-9a-f]{32})\)", e)
        for ref in refs:
            ident = bytes.fromhex(ref.decode())
            for g in entries:
                if ident in g:
                    for tag in re.findall(rb"globaltag=\+S\(([0-9A-F]+)\)", g):
                        procs.setdefault(name, new_process())["globaltags"].add(hexstr(tag))
        # The process's modules and ES modules are entries referenced by ID.
        for ref in set(re.findall(rb"=\+[PQ]\(([0-9a-f]{32})\)", e)):
            module = by_id.get(bytes.fromhex(ref.decode()), b"")
            for arch in ARCH_PATTERN.findall(module):
                procs.setdefault(name, new_process())["scram_archs"].add(arch.decode())
    return procs


# The processes the workflow reruns; others (HLT, LHC, Merge...) can
# legitimately differ between the runs in a file and aren't reported.
STEP_PROCESSES = ("RECO", "PAT", "NANO")

def has_provenance(f):
    return ("MetaData" in f and "ParameterSets" in f
            and {"ProcessConfiguration", "ProcessHistory"} & set(f["MetaData"].keys()))


def read_files(urls, result, merged):
    for url in urls:
        try:
            f = uproot.open(url, timeout=300)
            if not has_provenance(f):
                result["notes"].append("%s: no EDM provenance (e.g. flat NanoAOD)" % url)
                continue
            procs = processes_of(f)
        except Exception as e:
            result["notes"].append("%s: could not read provenance: %r" % (url, e))
            continue
        result["files"].append(url)
        for name, p in procs.items():
            m = merged.setdefault(name, new_process())
            for key in m:
                m[key] |= p[key]


def parent_files(metadata_json, n):
    """Files of the parent datasets the record links (e.g. a NanoAOD
    record's MiniAOD), which carry the provenance flat files lack."""
    meta = json.load(open(metadata_json))
    meta = meta.get("metadata", meta)
    urls = []
    for rel in meta.get("relations") or []:
        if not rel.get("recid"):
            continue
        with urllib.request.urlopen("https://opendata.cern.ch/api/records/%s" % rel["recid"], timeout=300) as resp:
            parent = json.load(resp)["metadata"]
        files = [f["uri"] for idx in parent.get("_file_indices") or [] for f in idx.get("files", [])]
        files += [f["uri"] for f in parent.get("files") or [] if f["uri"].endswith(".root")]
        urls += files[:n]
        print("Reading provenance from the parent dataset %s" % parent.get("title", rel["recid"]))
    return urls


urls = [l.strip() for l in open(urls_file) if l.strip().endswith(".root")][:max_files]
result = {"processes": {}, "files": [], "notes": []}
merged = {}
read_files(urls, result, merged)
if not result["files"] and metadata_json:
    read_files(parent_files(metadata_json, max_files), result, merged)

for name, m in merged.items():
    entry = {"release": "", "globaltag": "", "scram_arch": "", "all_releases": sorted(m["releases"]),
             "all_globaltags": sorted(m["globaltags"]), "all_scram_archs": sorted(m["scram_archs"])}
    # Use a value only when the files agree on one; otherwise leave it to the
    # record and say so (e.g. prompt reco whose release changed with the run).
    if len(m["releases"]) == 1:
        entry["release"] = next(iter(m["releases"]))
    elif m["releases"] and name in STEP_PROCESSES:
        result["notes"].append("%s: reference files disagree on the release: %s" % (name, sorted(m["releases"])))
    if len(m["globaltags"]) == 1:
        entry["globaltag"] = next(iter(m["globaltags"]))
    elif m["globaltags"] and name in STEP_PROCESSES:
        result["notes"].append("%s: reference files disagree on the global tag: %s" % (name, sorted(m["globaltags"])))
    if len(m["scram_archs"]) == 1:
        entry["scram_arch"] = next(iter(m["scram_archs"]))
    elif m["scram_archs"] and name in STEP_PROCESSES:
        result["notes"].append("%s: reference files disagree on the SCRAM arch: %s" % (name, sorted(m["scram_archs"])))
    result["processes"][name] = entry

json.dump(result, open(out_json, "w"), indent=1)
for name, e in sorted(result["processes"].items()):
    if name not in STEP_PROCESSES:
        continue
    print("%-10s release %-30s global tag %-25s arch %s" % (name, e["release"] or e["all_releases"],
                                                            e["globaltag"] or e["all_globaltags"],
                                                            e["scram_arch"] or e["all_scram_archs"]))
for n in result["notes"]:
    print("NOTE:", n)
