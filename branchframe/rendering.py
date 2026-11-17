"""Compose normalized clip jobs into fades, overlay tracks, and a soundtrack."""

from .editing import schedule

FORMATS = "mov,matroska,webm,mp3,wav,ogg,aac,flac"


def assemble(store, payload, output, ffmpeg, run):
    timeline = payload["timeline"]
    timing = schedule(timeline)
    if not timing["base"]:
        raise ValueError("Add a clip on video track 0 before exporting")
    clips = timeline["clips"]
    duration = timing["duration"]
    args = [ffmpeg, "-v", "error", "-nostdin", "-y", "-filter_complex_threads", "1"]
    for key in payload["keys"]:
        args += ["-i", str(store.get(key))]
    music = timeline.get("audio", [])
    from .worker import probe_media

    for a in music:
        source = store.get("assets/" + payload["project"] + "/" + a["asset"])
        probe = probe_media(source)
        if not any(s.get("codec_type") == "audio" for s in probe["streams"]):
            raise ValueError("Soundtrack source has no audio")
        if a["end"] > float(probe.get("format", {}).get("duration", 0)) + 0.1:
            raise ValueError("Audio trim end exceeds source duration")
        args += [
            "-protocol_whitelist",
            "file,pipe",
            "-format_whitelist",
            FORMATS,
            "-i",
            str(store.get("assets/" + payload["project"] + "/" + a["asset"])),
        ]
    filters = []
    indices = {c["id"]: i for i, c in enumerate(clips)}
    for i, c in enumerate(clips):
        filters.append(
            f"[{i}:v]format=yuv420p,settb=AVTB,setpts=PTS-STARTPTS,fps=30[v{i}]"
        )
        if not c.get("track", 0):
            filters.append(f"[{i}:a]asetpts=PTS-STARTPTS[a{i}]")
    first = indices[timing["base"][0]["clip"]["id"]]
    v = f"v{first}"
    a = f"a{first}"
    elapsed = timing["base"][0]["duration"]
    for step, entry in enumerate(timing["base"][1:], 1):
        i = indices[entry["clip"]["id"]]
        nv = f"basev{step}"
        na = f"basea{step}"
        overlap = entry["overlap"]
        if overlap:
            filters.append(f"[{v}]settb=AVTB,setpts=PTS-STARTPTS,fps=30[prev{step}]")
            filters.append(
                f"[prev{step}][v{i}]xfade=transition=fade:duration={overlap}:offset={elapsed - overlap}[{nv}]"
            )
            filters.append(f"[{a}][a{i}]acrossfade=d={overlap}:c1=tri:c2=tri[{na}]")
        else:
            filters.append(f"[{v}][{a}][v{i}][a{i}]concat=n=2:v=1:a=1[{nv}][{na}]")
        v, a = nv, na
        elapsed += entry["duration"] - overlap
    mixes = [a]
    for k, c in enumerate(timing["overlays"]):
        i = indices[c["id"]]
        at = c.get("at", 0)
        length = c["end"] - c["start"]
        w = max(2, int(c.get("width", 0.3) * 1280) // 2 * 2)
        h = max(2, int(c.get("height", 0.3) * 720) // 2 * 2)
        filters.append(f"[v{i}]scale={w}:{h},setpts=PTS-STARTPTS+{at}/TB[ov{k}]")
        filters.append(
            f"[{v}][ov{k}]overlay=x={int(c.get('x', 0.65) * 1280)}:y={int(c.get('y', 0.05) * 720)}:eof_action=pass:enable='between(t,{at},{at + length})'[composite{k}]"
        )
        v = f"composite{k}"
        filters.append(
            f"[{i}:a]asetpts=PTS-STARTPTS,adelay={int(at * 1000)}:all=1[oa{k}]"
        )
        mixes.append(f"oa{k}")
    for k, c in enumerate(music):
        i = len(clips) + k
        length = c["end"] - c["start"]
        f = f"[{i}:a]atrim=start={c['start']}:end={c['end']},asetpts=PTS-STARTPTS,aresample=48000,volume={c['volume']}"
        if c.get("fade_in", 0):
            f += f",afade=t=in:st=0:d={c['fade_in']}"
        if c.get("fade_out", 0):
            f += f",afade=t=out:st={max(0, length - c['fade_out'])}:d={c['fade_out']}"
        f += f",adelay={int(c['at'] * 1000)}:all=1[music{k}]"
        filters.append(f)
        mixes.append(f"music{k}")
    filters.append(
        "".join("[" + x + "]" for x in mixes)
        + f"amix=inputs={len(mixes)}:duration=first:normalize=0,alimiter=limit=0.98[outa]"
    )
    args += [
        "-filter_complex",
        ";".join(filters),
        "-map",
        "[" + v + "]",
        "-map",
        "[outa]",
        "-t",
        str(duration),
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-ac",
        "2",
        "-ar",
        "48000",
        "-movflags",
        "+faststart",
        str(output),
    ]
    run(args)
