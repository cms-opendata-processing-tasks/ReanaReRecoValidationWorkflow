#!/usr/bin/env python
# Flattens selected AOD collections into a NanoAOD-style "Events" tree
# (nX counter + X_field branches), so AOD-level outputs can go through the
# same Event_comparison.py / Plotting.py as NanoAOD ones.
#
# Runs with FWLite inside a CMSSW environment (cmsenv). Kept Python 2.6
# compatible (CMSSW_5_3, Run 1) as well as Python 3 (recent releases).
#
# Usage: aod_dump.py <files.txt> <output.root> [events.txt]
#   files.txt   one input file per line (path or root:// URL)
#   events.txt  optional "run:event" per line; only these events are kept
import sys
import time
from array import array

# REANA only shows a step's log once it ends, so progress also goes to
# <output>.progress in the workspace, readable while the step runs.
progress_path = sys.argv[2] + ".progress"


def mark(message):
    f = open(progress_path, "a")
    f.write("%s %s\n" % (time.strftime("%H:%M:%S"), message))
    f.close()


mark("started")

# ROOT parses the command line on first use; our arguments are hidden from it
# meanwhile (PyConfig.IgnoreCommandLineOptions doesn't exist in old PyROOT,
# e.g. CMSSW_3_9).
script_args, sys.argv = sys.argv, sys.argv[:1]
import ROOT
if hasattr(ROOT.PyConfig, "IgnoreCommandLineOptions"):
    ROOT.PyConfig.IgnoreCommandLineOptions = True
ROOT.gROOT.SetBatch(True)
sys.argv = script_args
if hasattr(ROOT, "FWLiteEnabler"):
    ROOT.FWLiteEnabler.enable()
else:
    ROOT.gSystem.Load("libFWCoreFWLite")
    ROOT.AutoLibraryLoader.enable()
# The autoloader doesn't reliably find every dictionary in old releases
# (CMSSW_5_3: no edm::Wrapper for std::vector<reco::PFJet>), so load the
# AOD format libraries explicitly. Missing ones are skipped.
for lib in ["libDataFormatsTrackReco", "libDataFormatsVertexReco", "libDataFormatsEgammaCandidates",
            "libDataFormatsEgammaReco", "libDataFormatsMuonReco", "libDataFormatsJetReco",
            "libDataFormatsMETReco", "libDataFormatsParticleFlowCandidate", "libDataFormatsTauReco",
            "libDataFormatsBTauReco", "libDataFormatsBeamSpot", "libDataFormatsHeavyIonEvent",
            "libDataFormatsHcalRecHit"]:
    ROOT.gSystem.Load(lib)
from DataFormats.FWLite import Events, Handle

files_txt = sys.argv[1]
output_root = sys.argv[2]
events_txt = len(sys.argv) > 3 and sys.argv[3] or None

input_files = [l.strip() for l in open(files_txt) if l.strip()]
wanted = None
if events_txt:
    wanted = set()
    for line in open(events_txt):
        if line.strip():
            run, event = line.strip().split(":")
            wanted.add((int(run), int(event)))

# Collection labels changed between eras; the first one present is used.
# "module:instance" for products with an instance name.
LABELS = {
    "Track": ["generalTracks", "hiSelectedTracks"],      # hi*: heavy-ion reco
    "PV": ["offlinePrimaryVertices", "hiSelectedVertex"],
    "Electron": ["gedGsfElectrons", "gsfElectrons"],
    "Photon": ["gedPhotons", "photons"],
    "Muon": ["muons"],
    "Jet": ["ak4PFJets", "ak5PFJets"],
    "PFMET": ["pfMet"],
    "CaloMET": ["caloMet", "met"],
    "PFCand": ["particleFlow"],
    "CaloJet": ["ak4CaloJets", "ak5CaloJets", "iterativeConePu5CaloJets"],
    "Tau": ["hpsPFTauProducer"],
    "Conversion": ["allConversions", "conversions"],
    # ECAL superclusters: Run 1 hybrid (barrel) / multi5x5 (endcap), Run 2 PF.
    "SCEB": ["correctedHybridSuperClusters", "particleFlowSuperClusterECAL:particleFlowSuperClusterECALBarrel"],
    "SCEE": ["correctedMulti5x5SuperClustersWithPreshower",
             "particleFlowSuperClusterECAL:particleFlowSuperClusterECALEndcapWithPreshower"],
    "BTag": ["combinedSecondaryVertexBJetTags", "pfCombinedInclusiveSecondaryVertexV2BJetTags"],
    "BeamSpot": ["offlineBeamSpot"],
    # Heavy-ion event characterisation (prompt reco, kept in the 2011 HI
    # rereco output); absent in pp files, where they're left empty.
    "Centrality": ["hiCentrality"],
    "EvtPlane": ["hiEvtPlane:recoLevel"],
    "ZDC": ["zdcreco"],
}
CLASSES = {
    "Track": "reco::Track",
    "PV": "reco::Vertex",
    "Electron": "reco::GsfElectron",
    "Photon": "reco::Photon",
    "Muon": "reco::Muon",
    "Jet": "reco::PFJet",
    "PFMET": "reco::PFMET",
    "CaloMET": "reco::CaloMET",
    "PFCand": "reco::PFCandidate",
    "CaloJet": "reco::CaloJet",
    "Tau": "reco::PFTau",
    "Conversion": "reco::Conversion",
    "SCEB": "reco::SuperCluster",
    "SCEE": "reco::SuperCluster",
    "BTag": "reco::JetTagCollection",
    "BeamSpot": "reco::BeamSpot",
    "Centrality": "reco::Centrality",
    "EvtPlane": "reco::EvtPlane",
    "ZDC": "ZDCRecHit",
}
# Old releases sometimes only know the dictionary under one spelling.
TYPE_SPELLINGS = ["std::vector<%s>", "vector<%s>"]
# Products that aren't vectors of their class: the type names to try.
JET_TAG = ("edm::AssociationVector<edm::RefToBaseProd<reco::Jet>,%s,edm::RefToBase<reco::Jet>,unsigned int,"
           "edm::helper::AssociationIdenticalKeyReference>")
TYPES = {
    "BTag": [JET_TAG % "vector<float>", JET_TAG % "std::vector<float>", "reco::JetTagCollection"],
    "BeamSpot": ["reco::BeamSpot"],
    "Centrality": ["reco::Centrality"],
    "ZDC": ["edm::SortedCollection<ZDCRecHit,edm::StrictWeakOrdering<ZDCRecHit> >"],
}


def tagged_jet_pt(tag):
    """pT of the jet a JetTag entry refers to: through RefToBase::get() in old
    PyROOT, directly on the ref in newer releases."""
    jet = tag.first
    return jet.get().pt() if hasattr(jet, "get") else jet.pt()


class TagEntry(object):
    """Entry i of a JetTagCollection as (jet ref, discriminator), read with
    key(i)/value(i): product[i].second reads garbage in CMSSW_7_6 PyROOT
    (different values on every run)."""
    def __init__(self, product, i):
        self.first, self.second = product.key(i), product.value(i)


def pair_pt(conversion):
    """Conversion pair pT: pairMomentum() is a GlobalVector in old releases
    (perp()) and a ROOT 3D vector in newer ones (Rho())."""
    momentum = conversion.pairMomentum()
    return momentum.perp() if hasattr(momentum, "perp") else momentum.Rho()
# Pileup density rho: (label, instance) per era.
RHO_LABELS = [("fixedGridRhoFastjetAll", ""), ("kt6PFJets", "rho")]

# Per-collection fields: (name, getter). Floats unless listed in INT_FIELDS.
FIELDS = {
    "Track": [("pt", lambda o: o.pt()), ("eta", lambda o: o.eta()), ("phi", lambda o: o.phi()),
              ("dxy", lambda o: o.dxy()), ("dz", lambda o: o.dz()),
              ("chi2", lambda o: o.chi2()), ("ndof", lambda o: o.ndof()),
              ("nValidHits", lambda o: o.numberOfValidHits()),
              ("algo", lambda o: o.algo()), ("qualityMask", lambda o: o.qualityMask())],
    "PV": [("x", lambda o: o.x()), ("y", lambda o: o.y()), ("z", lambda o: o.z()),
           ("chi2", lambda o: o.chi2()), ("ndof", lambda o: o.ndof()),
           ("nTracks", lambda o: o.tracksSize())],
    "Electron": [("pt", lambda o: o.pt()), ("eta", lambda o: o.eta()), ("phi", lambda o: o.phi()),
                 ("charge", lambda o: o.charge()), ("ecalEnergy", lambda o: o.ecalEnergy()),
                 ("scEnergy", lambda o: o.superCluster().energy()), ("fbrem", lambda o: o.fbrem()),
                 ("hoe", lambda o: o.hadronicOverEm()), ("trkIso03", lambda o: o.dr03TkSumPt())],
    "Photon": [("pt", lambda o: o.pt()), ("eta", lambda o: o.eta()), ("phi", lambda o: o.phi()),
               ("r9", lambda o: o.r9()), ("hoe", lambda o: o.hadronicOverEm()),
               ("sieie", lambda o: o.sigmaIetaIeta()),
               ("trkIso04", lambda o: o.trkSumPtHollowConeDR04())],
    "Muon": [("pt", lambda o: o.pt()), ("eta", lambda o: o.eta()), ("phi", lambda o: o.phi()),
             ("charge", lambda o: o.charge()), ("isGlobal", lambda o: o.isGlobalMuon()),
             ("isTracker", lambda o: o.isTrackerMuon()),
             ("nMatchedStations", lambda o: o.numberOfMatchedStations()),
             ("trkIso03", lambda o: o.isolationR03().sumPt)],
    "Jet": [("pt", lambda o: o.pt()), ("eta", lambda o: o.eta()), ("phi", lambda o: o.phi()),
            ("mass", lambda o: o.mass()), ("nConstituents", lambda o: o.numberOfDaughters()),
            ("chHEF", lambda o: o.chargedHadronEnergyFraction()),
            ("neHEF", lambda o: o.neutralHadronEnergyFraction())],
    "PFCand": [("pt", lambda o: o.pt()), ("eta", lambda o: o.eta()), ("phi", lambda o: o.phi()),
               ("pdgId", lambda o: o.pdgId())],
    "CaloJet": [("pt", lambda o: o.pt()), ("eta", lambda o: o.eta()), ("phi", lambda o: o.phi()),
                ("mass", lambda o: o.mass()), ("emEF", lambda o: o.emEnergyFraction()),
                ("n90", lambda o: o.n90()), ("nConstituents", lambda o: o.numberOfDaughters())],
    "Tau": [("pt", lambda o: o.pt()), ("eta", lambda o: o.eta()), ("phi", lambda o: o.phi()),
            ("mass", lambda o: o.mass()), ("charge", lambda o: o.charge()),
            ("decayMode", lambda o: o.decayMode())],
    "Conversion": [("nTracks", lambda o: o.nTracks()), ("pt", pair_pt),
                   ("vx", lambda o: o.conversionVertex().x()), ("vy", lambda o: o.conversionVertex().y()),
                   ("vz", lambda o: o.conversionVertex().z()),
                   ("vtxChi2", lambda o: o.conversionVertex().chi2())],
    "SCEB": [("energy", lambda o: o.energy()), ("rawEnergy", lambda o: o.rawEnergy()),
             ("eta", lambda o: o.eta()), ("phi", lambda o: o.phi()), ("nClusters", lambda o: o.clustersSize())],
    "SCEE": [("energy", lambda o: o.energy()), ("rawEnergy", lambda o: o.rawEnergy()),
             ("eta", lambda o: o.eta()), ("phi", lambda o: o.phi()), ("nClusters", lambda o: o.clustersSize())],
    # JetTagCollection entries are (jet ref, discriminator) pairs.
    "BTag": [("disc", lambda o: o.second), ("jetPt", tagged_jet_pt)],
    "EvtPlane": [("angle", lambda o: o.angle()), ("sumSin", lambda o: o.sumSin()),
                 ("sumCos", lambda o: o.sumCos())],
    "ZDC": [("energy", lambda o: o.energy()), ("time", lambda o: o.time()),
            ("flags", lambda o: o.flags()), ("rawId", lambda o: o.id().rawId())],
}
INT_FIELDS = set(["charge", "nValidHits", "nTracks", "isGlobal", "isTracker", "nMatchedStations",
                  "nConstituents", "pdgId", "algo", "qualityMask", "n90", "decayMode", "nClusters", "rawId", "flags"])
# Beam spot: one object per event, written as scalars BS_<field>.
BEAMSPOT_FIELDS = [("x", lambda o: o.x0()), ("y", lambda o: o.y0()), ("z", lambda o: o.z0()),
                   ("sigmaZ", lambda o: o.sigmaZ()), ("dxdz", lambda o: o.dxdz()), ("dydz", lambda o: o.dydz())]
# Centrality: one object per event, written as scalars Cent_<field>; the
# inputs the centrality bin (and so the rereco's event filter) is made from.
CENTRALITY_FIELDS = [("EtHFhitSum", lambda o: o.EtHFhitSum()), ("EtHFtowerSum", lambda o: o.EtHFtowerSum()),
                     ("EtHFtowerSumPlus", lambda o: o.EtHFtowerSumPlus()),
                     ("EtHFtowerSumMinus", lambda o: o.EtHFtowerSumMinus()),
                     ("EtHFtruncated", lambda o: o.EtHFtruncated()), ("EtEBSum", lambda o: o.EtEBSum()),
                     ("EtEESum", lambda o: o.EtEESum()), ("EtMidRapiditySum", lambda o: o.EtMidRapiditySum()),
                     ("multiplicityPixel", lambda o: o.multiplicityPixel()), ("Ntracks", lambda o: o.Ntracks()),
                     ("zdcSum", lambda o: o.zdcSum())]
MAX_OBJECTS = {"Track": 20000, "PFCand": 50000}
DEFAULT_MAX = 2000

out = ROOT.TFile(output_root, "RECREATE")
tree = ROOT.TTree("Events", "Events")

ev_run = array("I", [0])
ev_lumi = array("I", [0])
# array "L" is 64-bit on x86_64 (matches /l, ULong64_t) but 32-bit in 32-bit
# releases (e.g. CMSSW_3_9 slc5_ia32), where the branch is /i instead (event
# numbers of that era fit; Python 2 has no 64-bit array type).
ev_event = array("L", [0])
EVENT_LEAF = "event/l" if ev_event.itemsize == 8 else "event/i"
tree.Branch("run", ev_run, "run/i")
tree.Branch("luminosityBlock", ev_lumi, "luminosityBlock/i")
tree.Branch("event", ev_event, EVENT_LEAF)

counters = {}
field_buffers = {}
unavailable = set()
for coll in FIELDS:
    counter = array("i", [0])
    counters[coll] = counter
    tree.Branch("n" + coll, counter, "n%s/I" % coll)
    size = MAX_OBJECTS.get(coll, DEFAULT_MAX)
    for field, _ in FIELDS[coll]:
        if field in INT_FIELDS:
            buf = array("i", [0] * size)
            tree.Branch("%s_%s" % (coll, field), buf, "%s_%s[n%s]/I" % (coll, field, coll))
        else:
            buf = array("f", [0.0] * size)
            tree.Branch("%s_%s" % (coll, field), buf, "%s_%s[n%s]/F" % (coll, field, coll))
        field_buffers[(coll, field)] = buf

scalars = {}
for name in ["MET_pt", "MET_phi", "MET_sumEt", "CaloMET_pt", "CaloMET_phi", "CaloMET_sumEt", "rho"] + \
        ["BS_" + f for f, _ in BEAMSPOT_FIELDS] + ["Cent_" + f for f, _ in CENTRALITY_FIELDS]:
    buf = array("f", [0.0])
    scalars[name] = buf
    tree.Branch(name, buf, "%s/F" % name)

handles = {}
chosen_label = {}
chosen_rho = [None]
unreadable = set()


# FWLite failures for a type name it can't use or a label the file lacks:
# ValueError (AddressOf; old FWLite for a missing label) or AttributeError
# (no dictionary for edm::Wrapper<type>, e.g. a typedef in CMSSW_4_2).
LOOKUP_ERRORS = (ValueError, AttributeError)


def get_by_label(event, coll, label):
    """getByLabel that falls back to the next type name when FWLite can't use
    this one. Handles are made on first use: creating one already fails for a
    type without a usable dictionary."""
    while True:
        if coll not in handles:
            handles[coll] = list(TYPES.get(coll) or [spelling % CLASSES[coll] for spelling in TYPE_SPELLINGS])
        try:
            if isinstance(handles[coll][0], str):
                handles[coll][0] = Handle(handles[coll][0])
            handle = handles[coll][0]
            if ":" in label:
                module, instance = label.split(":", 1)
                event.getByLabel(module, instance, handle)
            else:
                event.getByLabel(label, handle)
            return handle
        except LOOKUP_ERRORS:
            handles[coll].pop(0)
            if not handles[coll]:
                raise


def get_collection(event, coll):
    """Returns the product for coll, picking the era's label on first use.
    None if the event doesn't have it; collections FWLite can't read at all
    are reported once and then left empty."""
    if coll in unreadable:
        return None
    if coll in chosen_label:
        try:
            handle = get_by_label(event, coll, chosen_label[coll])
            return handle.isValid() and handle.product() or None
        except LOOKUP_ERRORS:
            handles.pop(coll, None)
            return None
    # Old FWLite (e.g. CMSSW_3_9) raises for a label the file doesn't have
    # instead of returning an invalid handle, so a failing label only rules
    # out that label; with none left the collection is reported once.
    failed = 0
    for label in LABELS[coll]:
        try:
            handle = get_by_label(event, coll, label)
        except LOOKUP_ERRORS:
            handles.pop(coll, None)
            failed += 1
            continue
        if handle.isValid():
            chosen_label[coll] = label
            print("Using %s for %s" % (label, coll))
            return handle.product()
    if failed == len(LABELS[coll]):
        unreadable.add(coll)
        print("WARNING: no readable %s (%s, labels %s) in this file/release; it is left empty"
              % (coll, CLASSES[coll], ", ".join(LABELS[coll])))
    return None


def get_rho(event):
    """rho of the era's label; NaN if the event has none or FWLite can't
    read it (then reported once, like the collections)."""
    if "rho" in unreadable:
        return float("nan")
    candidates = chosen_rho[0] and [chosen_rho[0]] or RHO_LABELS
    for label, instance in candidates:
        # A fresh handle per try: in CMSSW_4_2's FWLite a handle is unusable
        # after a failed getByLabel (ValueError from AddressOf on reuse).
        handle = Handle("double")
        try:
            if instance:
                event.getByLabel(label, instance, handle)
            else:
                event.getByLabel(label, handle)
        except ValueError:
            unreadable.add("rho")
            print("WARNING: FWLite can't read rho in this release; it is left NaN")
            return float("nan")
        if handle.isValid():
            if chosen_rho[0] is None:
                print("Using %s%s for rho" % (label, instance and ":" + instance or ""))
            chosen_rho[0] = (label, instance)
            return handle.product()[0]
    return float("nan")


n_seen = 0
n_written = 0
mark("FWLite loaded, opening %d input files" % len(input_files))
events = Events(input_files)
for event in events:
    n_seen += 1
    if n_seen % 50 == 0:
        mark("%d events read, %d written" % (n_seen, n_written))
    eid = event.eventAuxiliary().id()
    run, lumi, evt = eid.run(), eid.luminosityBlock(), eid.event()
    if wanted is not None and (run, evt) not in wanted:
        continue

    ev_run[0] = run
    ev_lumi[0] = lumi
    ev_event[0] = evt

    for coll in FIELDS:
        product = get_collection(event, coll)
        n = 0
        if product is not None:
            n = min(product.size(), MAX_OBJECTS.get(coll, DEFAULT_MAX))
            if product.size() > n:
                print("WARNING: %s has %d objects, truncated to %d" % (coll, product.size(), n))
            for i in range(n):
                try:
                    obj = TagEntry(product, i) if coll == "BTag" else product[i]
                except (TypeError, IndexError, AttributeError):
                    # PyROOT can't index this product type in this release.
                    unreadable.add(coll)
                    print("WARNING: can't index %s (%s) in this release; it is left empty" % (coll, CLASSES[coll]))
                    n = 0
                    break
                for field, getter in FIELDS[coll]:
                    if (coll, field) in unavailable:
                        field_buffers[(coll, field)][i] = -999
                        continue
                    try:
                        value = getter(obj)
                        field_buffers[(coll, field)][i] = value
                    except OverflowError:
                        # Doesn't fit the 32-bit int branch: reported once,
                        # stored as -1.
                        field_buffers[(coll, field)][i] = -1
                        if (coll, field, "overflow") not in unavailable:
                            unavailable.add((coll, field, "overflow"))
                            print("NOTE: %s_%s value %r doesn't fit a 32-bit int, stored as -1" % (coll, field, value))
                    except (AttributeError, TypeError):
                        # The method doesn't exist in this release (e.g.
                        # Muon::numberOfMatchedStations in CMSSW_3_9), or
                        # PyROOT can't call it: the field is filled with -999
                        # for every object.
                        unavailable.add((coll, field))
                        field_buffers[(coll, field)][i] = -999
                        print("NOTE: %s_%s not available in this release, filled with -999" % (coll, field))
        counters[coll][0] = n

    for prefix, coll in (("MET", "PFMET"), ("CaloMET", "CaloMET")):
        product = get_collection(event, coll)
        if product is not None and product.size():
            met = product[0]
            scalars[prefix + "_pt"][0] = met.pt()
            scalars[prefix + "_phi"][0] = met.phi()
            scalars[prefix + "_sumEt"][0] = met.sumEt()
        else:
            for f in ("_pt", "_phi", "_sumEt"):
                scalars[prefix + f][0] = float("nan")
    scalars["rho"][0] = get_rho(event)
    beamspot = get_collection(event, "BeamSpot")
    for field, getter in BEAMSPOT_FIELDS:
        try:
            scalars["BS_" + field][0] = getter(beamspot) if beamspot is not None else float("nan")
        except (AttributeError, TypeError):
            scalars["BS_" + field][0] = float("nan")
    centrality = get_collection(event, "Centrality")
    for field, getter in CENTRALITY_FIELDS:
        value = float("nan")
        if centrality is not None and ("Centrality", field) not in unavailable:
            try:
                value = getter(centrality)
            except (AttributeError, TypeError):
                unavailable.add(("Centrality", field))
                print("NOTE: Cent_%s not available in this release, left NaN" % field)
        scalars["Cent_" + field][0] = value

    tree.Fill()
    n_written += 1
    if wanted is not None and n_written == len(wanted):
        break

out.Write()
out.Close()
mark("output written: %d events" % n_written)
print("Read %d events, wrote %d to %s" % (n_seen, n_written, output_root))
if wanted is not None and n_written < len(wanted):
    print("WARNING: %d requested events not found in the input" % (len(wanted) - n_written))
if unreadable:
    print("WARNING: collections left empty because FWLite couldn't read them: %s" % ", ".join(sorted(unreadable)))
if n_written == 0:
    sys.exit("No events written")
