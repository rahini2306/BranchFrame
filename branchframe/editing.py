"""Non-destructive multitrack timing shared by validation and FFmpeg assembly."""

import copy
import math
import re

VIDEO_OPTIONS = {
    "transition",
    "transition_duration",
    "track",
    "at",
    "x",
    "y",
    "width",
    "height",
    "volume",
}
AUDIO_FIELDS = {"id", "asset", "start", "end", "at", "volume", "fade_in", "fade_out"}


def number(value, low, high, name):
    if (
        isinstance(value, bool)
        or not isinstance(value, (float, int))
        or not math.isfinite(value)
        or not low <= value <= high
    ):
        raise ValueError(f"{name} must be between {low} and {high}")
    return value


def validate_extras(timeline):
    for c in timeline["clips"]:
        track = c.get("track", 0)
        if isinstance(track, bool) or not isinstance(track, int) or not 0 <= track <= 3:
            raise ValueError("Video track must be 0–3")
        number(c.get("volume", 1), 0, 2, "Clip volume")
        number(c.get("at", 0), 0, 3600, "Overlay position")
        for key, default in [("x", 0.65), ("y", 0.05), ("width", 0.3), ("height", 0.3)]:
            number(
                c.get(key, default), 0.01 if key in ("width", "height") else 0, 1, key
            )
        if (
            c.get("x", 0.65) + c.get("width", 0.3) > 1.001
            or c.get("y", 0.05) + c.get("height", 0.3) > 1.001
        ):
            raise ValueError("Overlay must fit inside the frame")
        if c.get("transition", "cut") not in ("cut", "fade"):
            raise ValueError("Supported transitions are cut and fade")
        number(c.get("transition_duration", 0.5), 0.05, 2, "Transition duration")
    audio = timeline.get("audio", [])
    if not isinstance(audio, list) or len(audio) > 8:
        raise ValueError("Use at most 8 audio clips")
    seen = set()
    for c in audio:
        if (
            not isinstance(c, dict)
            or set(c) - AUDIO_FIELDS
            or not {"id", "asset", "start", "end", "at", "volume"} <= set(c)
        ):
            raise ValueError("Invalid audio clip fields")
        if (
            not isinstance(c["id"], str)
            or not re.fullmatch("[a-zA-Z0-9_-]{1,64}", c["id"])
            or c["id"] in seen
        ):
            raise ValueError("Audio IDs must be unique")
        seen.add(c["id"])
        if not isinstance(c["asset"], str) or not re.fullmatch(
            "[a-f0-9]{32}", c["asset"]
        ):
            raise ValueError("Invalid audio asset")
        number(c["start"], 0, 3600, "Audio start")
        number(c["end"], 0, 3600, "Audio end")
        if c["end"] <= c["start"]:
            raise ValueError("Audio end must follow start")
        number(c["at"], 0, 3600, "Audio placement")
        number(c["volume"], 0, 2, "Audio volume")
        for key in ("fade_in", "fade_out"):
            number(c.get(key, 0), 0, min(10, c["end"] - c["start"]), key)
    return timeline


def schedule(timeline):
    base = []
    end = 0
    for c in timeline["clips"]:
        if c.get("track", 0):
            continue
        duration = c["end"] - c["start"]
        overlap = 0
        if base and c.get("transition") == "fade":
            overlap = min(
                c.get("transition_duration", 0.5),
                duration / 2,
                (base[-1]["clip"]["end"] - base[-1]["clip"]["start"]) / 2,
            )
        start = end - overlap
        base.append({"clip": c, "at": start, "duration": duration, "overlap": overlap})
        end = start + duration
    return {
        "base": base,
        "duration": end,
        "overlays": sorted(
            [c for c in timeline["clips"] if c.get("track", 0)],
            key=lambda c: c.get("track", 0),
        ),
    }


def merge_audio(base, ours, theirs, resolutions):
    """Merge independent soundtrack entries by identity; conflicting edits stay explicit."""
    b = {c["id"]: c for c in base}
    o = {c["id"]: c for c in ours}
    t = {c["id"]: c for c in theirs}
    out = []
    conflicts = []
    for cid in sorted(set(b) | set(o) | set(t)):
        bv, ov, tv = b.get(cid), o.get(cid), t.get(cid)
        path = "audio/" + cid
        if ov == tv:
            v = ov
        elif ov == bv:
            v = tv
        elif tv == bv:
            v = ov
        elif resolutions.get(path) in ("ours", "theirs"):
            v = ov if resolutions[path] == "ours" else tv
        else:
            conflicts.append({"path": path, "base": bv, "ours": ov, "theirs": tv})
            v = ov
        if v is not None:
            out.append(copy.deepcopy(v))
    return out, conflicts
