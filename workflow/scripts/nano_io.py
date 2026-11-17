import awkward as ak
import uproot


def write_nanoaod(path, arrays):
    """Write an awkward record array of NanoAOD branches back out in the flat
    NanoAOD layout (nX counter + X_field branches), so it reads exactly like
    an official file.

    uproot builds the nX counter itself from a zipped jagged collection, so
    jagged branches are grouped into one record per collection. Collections
    are identified from their counters rather than by splitting on '_',
    since some names contain underscores (e.g. Proton_multiRP).
    """
    fields = list(arrays.fields)
    jagged = [b for b in fields if arrays[b].ndim > 1]
    collections = sorted(
        {b[1:] for b in fields if b.startswith("n") and any(j.startswith(b[1:] + "_") for j in jagged)},
        key=len, reverse=True,
    )

    out = {}
    grouped = {}
    for b in fields:
        if b in jagged:
            collection = next((c for c in collections if b.startswith(c + "_")), None)
            if collection is None:
                raise ValueError("Jagged branch " + b + " has no nX counter branch")
            grouped.setdefault(collection, {})[b[len(collection) + 1:]] = arrays[b]
        elif not (b.startswith("n") and b[1:] in collections):
            out[b] = arrays[b]
    for collection, columns in grouped.items():
        out[collection] = ak.zip(columns)

    with uproot.recreate(path) as f:
        f.mktree("Events", {name: column.type for name, column in out.items()})
        f["Events"].extend(out)
