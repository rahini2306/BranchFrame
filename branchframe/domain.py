"""Immutable timeline validation, DAG ancestry, and explicit three-way merge."""

import copy
import hashlib
import json
import math
import re


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def validate_timeline(value):
    if not isinstance(value, dict) or set(value) != {"clips"}:
        raise ValueError("Timeline must contain a clips array")
    clips = value["clips"]
    if not isinstance(clips, list) or len(clips) > 100:
        raise ValueError("Use at most 100 clips")
    seen = set()
    for clip in clips:
        if not isinstance(clip, dict) or set(clip) != {
            "id",
            "asset",
            "start",
            "end",
            "caption",
            "muted",
        }:
            raise ValueError("Invalid clip fields")
        if (
            not isinstance(clip["id"], str)
            or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", clip["id"])
            or clip["id"] in seen
        ):
            raise ValueError("Clip IDs must be unique")
        seen.add(clip["id"])
        if not isinstance(clip["asset"], str) or not re.fullmatch(
            r"[a-f0-9]{32}", clip["asset"]
        ):
            raise ValueError("Invalid asset")
        a, b = clip["start"], clip["end"]
        if (
            any(
                isinstance(x, bool)
                or not isinstance(x, (int, float))
                or not math.isfinite(x)
                for x in (a, b)
            )
            or not 0 <= a < b <= 3600
        ):
            raise ValueError("Clip range must be between 0 and 3600 seconds")
        if (
            not isinstance(clip["caption"], str)
            or len(clip["caption"]) > 160
            or "\x00" in clip["caption"]
        ):
            raise ValueError("Caption must have at most 160 characters")
        if not isinstance(clip["muted"], bool):
            raise ValueError("muted must be boolean")
    if sum(c["end"] - c["start"] for c in clips) > 3600:
        raise ValueError("Export duration is limited to one hour")
    return copy.deepcopy(value)


def merge(base, ours, theirs, resolutions=None):
    """Resolve by stable clip identity; treat order as one explicit conflict.

    Resolutions map conflict paths to 'ours' or 'theirs'. Delete/modify is an
    atomic clip conflict. The caller must validate the resulting asset references.
    """
    resolutions = resolutions or {}
    conflicts = []
    missing = object()

    def choose(path, b, o, t):
        if o == t:
            return copy.deepcopy(o) if o is not missing else missing
        if o == b:
            return copy.deepcopy(t) if t is not missing else missing
        if t == b:
            return copy.deepcopy(o) if o is not missing else missing
        resolution = resolutions.get(path)
        if resolution in ("ours", "theirs"):
            v = o if resolution == "ours" else t
            return copy.deepcopy(v) if v is not missing else missing
        conflicts.append(
            {
                "path": path,
                "base": None if b is missing else b,
                "ours": None if o is missing else o,
                "theirs": None if t is missing else t,
            }
        )
        return copy.deepcopy(o) if o is not missing else missing

    maps = [{c["id"]: c for c in v["clips"]} for v in (base, ours, theirs)]
    out = {}
    for cid in sorted(set().union(*maps)):
        b, o, t = (m.get(cid, missing) for m in maps)
        if any(v is missing for v in (b, o, t)):
            v = choose("clips/" + cid, b, o, t)
        else:
            v = {"id": cid}
            for key in ("asset", "start", "end", "caption", "muted"):
                v[key] = choose("clips/" + cid + "/" + key, b[key], o[key], t[key])
        if v is not missing:
            try:
                validate_timeline({"clips": [v]})
            except ValueError:
                v = choose("clips/" + cid, b, o, t)
            if v is not missing:
                out[cid] = v
    # Compare order only for identities present on all sides. Independent inserts
    # retain each branch's local ordering with deterministic ours-first insertion.
    shared = set(maps[0]) & set(maps[1]) & set(maps[2]) & set(out)
    orders = [
        [c["id"] for c in v["clips"] if c["id"] in shared] for v in (base, ours, theirs)
    ]
    order = choose("order", *orders)
    for timeline in (ours, theirs):
        ids = [c["id"] for c in timeline["clips"] if c["id"] in out]
        for pos, cid in enumerate(ids):
            if cid in order:
                continue
            previous = next((x for x in reversed(ids[:pos]) if x in order), None)
            following = next((x for x in ids[pos + 1 :] if x in order), None)
            if previous is not None:
                order.insert(order.index(previous) + 1, cid)
            elif following is not None:
                order.insert(order.index(following), cid)
            else:
                order.append(cid)
    result = {"clips": [out[cid] for cid in order]}
    if not conflicts:
        validate_timeline(result)
    return {"timeline": result, "conflicts": conflicts}


def merge_base(ours, theirs, commits):
    """Find a deterministic best common ancestor, rejecting ambiguous criss-cross."""

    def ancestors(head):
        found = set()
        pending = [head]
        while pending:
            cid = pending.pop()
            if cid in found:
                continue
            found.add(cid)
            pending.extend(commits[cid]["parents"])
        return found

    common = ancestors(ours) & ancestors(theirs)
    best = [
        cid
        for cid in common
        if not any(cid in ancestors(other) - {other} for other in common)
    ]
    if len(best) != 1:
        raise ValueError("No unique merge base; merge these branches sequentially")
    return best[0]


def diff(before, after):
    b = {c["id"]: c for c in before["clips"]}
    a = {c["id"]: c for c in after["clips"]}
    rows = []
    for cid in sorted(set(b) | set(a)):
        if cid not in b:
            rows.append({"clip": cid, "kind": "added", "after": a[cid]})
        elif cid not in a:
            rows.append({"clip": cid, "kind": "removed", "before": b[cid]})
        else:
            for key in a[cid]:
                if a[cid][key] != b[cid][key]:
                    rows.append(
                        {
                            "clip": cid,
                            "kind": key,
                            "before": b[cid][key],
                            "after": a[cid][key],
                        }
                    )
    if [c["id"] for c in before["clips"]] != [c["id"] for c in after["clips"]]:
        rows.append(
            {"clip": "timeline", "kind": "order", "before": list(b), "after": list(a)}
        )
    return rows
