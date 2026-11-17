
# --- Appended by cmsrun_step.sh: sets the job's input, events and output. ---
# Python 2.6 compatible (CMSSW 4_2). Values come from the environment.
import os as _os

_step = _os.environ["RUNSTEP_STEP"]
process.source.fileNames = cms.untracked.vstring(_os.environ["RUNSTEP_INPUT"])
process.maxEvents = cms.untracked.PSet(input=cms.untracked.int32(int(_os.environ["RUNSTEP_EVENTS"])))

# Only where the config has a thread setting (not in e.g. CMSSW_5_3 configs).
if _os.environ.get("RUNSTEP_THREADS") and hasattr(getattr(process, "options", None), "numberOfThreads"):
    process.options.numberOfThreads = cms.untracked.uint32(int(_os.environ["RUNSTEP_THREADS"]))
    process.options.numberOfStreams = cms.untracked.uint32(0)

if _os.environ.get("RUNSTEP_LUMIS"):
    process.source.lumisToProcess = cms.untracked.VLuminosityBlockRange(*_os.environ["RUNSTEP_LUMIS"].split(","))

if hasattr(process, "RandomNumberGeneratorService"):
    for _param in process.RandomNumberGeneratorService.parameterNames_():
        _module = getattr(process.RandomNumberGeneratorService, _param)
        if hasattr(_module, "initialSeed"):
            _module.initialSeed = cms.untracked.uint32(12345 + int(_os.environ["RUNSTEP_JOB"]))

# CMSSW_3_x / 4_2 / 4_4: lumiProducer reads a luminosity DB schema that no
# longer exists; it only fills lumi-level products that aren't compared.
if _os.environ.get("CMSSW_VERSION", "").startswith(("CMSSW_3_", "CMSSW_4_2_", "CMSSW_4_4_")) \
        and hasattr(process, "lumiProducer"):
    # Sequences first, so paths only hold direct references; itervalues():
    # values() of 4_2's SortedKeysDict is broken.
    for _container in list(process.sequences_().itervalues()) + list(process.paths_().itervalues()):
        while _container.remove(process.lumiProducer):
            pass
    del process.lumiProducer
    print("Removed lumiProducer (luminosity DB unavailable for this release)")

_output = "file:%s_output.root" % _step
if _step == "nano":
    # Flat NanoAOD, written next to the config's EDM output.
    _existing = list(process.outputModules.values())[0]
    process.NANOAODoutput = cms.OutputModule("NanoAODOutputModule",
        fileName=cms.untracked.string(_output),
        outputCommands=_existing.outputCommands,
        compressionLevel=cms.untracked.int32(9),
        compressionAlgorithm=cms.untracked.string("LZMA"),
    )
    process.NANOAODoutput_step = cms.EndPath(process.NANOAODoutput)
    process.schedule.append(process.NANOAODoutput_step)
else:
    # Outputs with SelectEvents are skims, unless there is nothing else (the
    # 2011 heavy-ion rereco writes only events passing its centrality filter).
    _candidates = ([m for m in process.outputModules.values() if not hasattr(m, "SelectEvents")]
                   or list(process.outputModules.values()))
    _main = None
    # AOD before MINIAOD: taking MINIAOD causes ProductNotFound.
    for _tier in ["AOD", "MINIAOD"]:
        for _m in _candidates:
            if hasattr(_m, "dataset") and hasattr(_m.dataset, "dataTier") and _m.dataset.dataTier.value() == _tier:
                _main = _m
                break
        if _main is not None:
            break
    if _main is None:
        _non_dqm = [m for m in _candidates if m.type_() != "DQMRootOutputModule"]
        _main = _non_dqm and _non_dqm[0] or _candidates[0]
    _main.fileName = cms.untracked.string(_output)
