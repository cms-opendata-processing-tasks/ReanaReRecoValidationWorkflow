# shellcheck shell=bash
# Sourced by the light rules: installs Python packages into a temporary
# directory (not the workspace, which HTCondor jobs are sent) and puts it on
# PYTHONPATH.   source workflow/scripts/python_libs.sh uproot awkward ...
PYTHON_LIBS=$(mktemp -d)
pip install --quiet --no-cache-dir --break-system-packages --target "$PYTHON_LIBS" "$@"
export PYTHONPATH=$PYTHON_LIBS${PYTHONPATH:+:$PYTHONPATH}
