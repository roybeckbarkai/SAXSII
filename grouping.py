"""Group images by parts of their file names, e.g. to average every frame of a position.

A name such as  hirsch_00405_2026-10-18_22-39-28_p08_f00  is split into fields on "_".
The user chooses which fields must be identical inside a group ("keep" indices):
keep the p-field -> one group per position (averaging over f), keep the f-field ->
one group per frame number (averaging over p). Instead of fields a regular expression
can be given; files whose capture groups (or whole match) are equal form a group.
"""
import re

from image_io import curve_name
from integration_methods import UserError

SEP = "_"
AVG = "avg"


def _natural(s):
    """Sort key that orders p2 before p10."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def _compile(pattern, what):
    try:
        return re.compile(pattern)
    except re.error as e:
        raise UserError(f"Invalid {what}: {e}")


def group_name(stems, key):
    """Name for the average of `stems`: fields shared by all members are kept, fields that
    differ become 'avg' (hirsch_00405_<date>_<time>_p08_f00..f09 -> hirsch_00405_avg_avg_p08_avg)."""
    split = [s.split(SEP) for s in stems]
    if len({len(t) for t in split}) == 1:
        toks = [col[0] if len(set(col)) == 1 else AVG for col in zip(*split)]
        if AVG in toks:
            return SEP.join(toks)
    return f"{key or stems[0]}{SEP}{AVG}"


def group_images(ids, keep=(), regex="", include=""):
    """Return (groups, unmatched). groups: [{key, name, members}] in natural key order."""
    stems = {i: curve_name(i) for i in ids}
    if include:
        rx = _compile(include, "filter")
        ids = [i for i in ids if rx.search(stems[i])]
    rx = _compile(regex, "regular expression") if regex else None
    keep = sorted({int(k) for k in keep})

    groups, unmatched = {}, []
    for i in ids:
        stem = stems[i]
        if rx is not None:
            m = rx.search(stem)
            if not m:
                unmatched.append(i)
                continue
            key = m.groups() or (m.group(0),)
            key = tuple("" if k is None else k for k in key)
        else:
            toks = stem.split(SEP)
            if keep and keep[-1] >= len(toks):
                unmatched.append(i)
                continue
            key = tuple(toks[k] for k in keep)
        groups.setdefault(key, []).append(i)

    out = []
    for key, members in groups.items():
        k = SEP.join(key)
        out.append({"key": k or "all", "members": members, "name": group_name([stems[m] for m in members], k)})
    out.sort(key=lambda g: _natural(g["key"]))
    return out, unmatched
