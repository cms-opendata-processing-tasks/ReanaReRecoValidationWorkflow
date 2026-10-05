# Shared by Stat_comparison.py and Plotting.py so the chi2 test and the
# plots always use identical binning.
# (name, branch in Events tree, n_bins, (x_min, x_max), x-axis label)
variable_configs = [
    ("nElectron", "nElectron", 10, (0, 10), "Number of electrons"),
    ("nMuon", "nMuon", 10, (0, 10), "Number of muons"),
    ("Electron_pt", "Electron_pt", 50, (0, 150), r"Electron $p_T$ [GeV]"),
    ("Muon_pt", "Muon_pt", 50, (0, 150), r"Muon $p_T$ [GeV]"),
    ("Electron_phi", "Electron_phi", 50, (-3.15, 3.15), r"Electron $\phi$"),
    ("Muon_phi", "Muon_phi", 50, (-3.15, 3.15), r"Muon $\phi$"),
    ("Electron_eta", "Electron_eta", 50, (-3, 3), r"Electron $\eta$"),
    ("Muon_eta", "Muon_eta", 50, (-3.15, 3.15), r"Muon $\eta$"),
    ("MET_phi", "MET_phi", 50, (-3.15, 3.15), r"MET $\phi$"),
    ("MET_pt", "MET_pt", 50, (0, 200), r"MET $p_T$ [GeV]"),
    ("nJet", "nJet", 20, (0, 15), "Number of jets"),
    ("Jet_pt", "Jet_pt", 50, (0, 150), r"Jet $p_T$ [GeV]"),
    ("Jet_phi", "Jet_phi", 50, (-3.15, 3.15), r"Jet $\phi$"),
    ("Jet_eta", "Jet_eta", 50, (-6, 6), r"Jet $\eta$"),
    ("nPhoton", "nPhoton", 20, (0, 10), "Number of photons"),
    ("Photon_pt", "Photon_pt", 50, (0, 150), r"Photon $p_T$ [GeV]"),
    ("Photon_phi", "Photon_phi", 50, (-3.15, 3.15), r"Photon $\phi$"),
    ("Photon_eta", "Photon_eta", 50, (-3, 3), r"Photon $\eta$"),
    ("nTau", "nTau", 20, (0, 10), "Number of taus"),
    ("Tau_pt", "Tau_pt", 50, (0, 150), r"Tau $p_T$ [GeV]"),
    ("Tau_phi", "Tau_phi", 50, (-3.15, 3.15), r"Tau $\phi$"),
    ("Tau_eta", "Tau_eta", 50, (-3, 3), r"Tau $\eta$"),
]
