import math
import random
import xml.etree.ElementTree as ET
from fractions import Fraction

from .models import Project


def time_value(value: Fraction) -> str:
    return f"{value.numerator}/{value.denominator}s" if value.denominator != 1 else f"{value.numerator}s"


def build_plan(project: Project, music: dict, media: dict[str, dict]) -> dict:
    fps = Fraction(project.fps)
    frame = 1 / fps
    total_source_frames = math.floor(Fraction(str(music["duration"])) * fps)
    ranges = project.ranges or [(0, music["duration"])]
    sections, mapped = [], []
    cursor, previous_end = 0, 0
    for a, b in ranges:
        if not (math.isfinite(a) and math.isfinite(b) and 0 <= a < b <= music["duration"] + 1e-6):
            raise ValueError("音乐区间必须在原曲范围内，且起点早于终点")
        start, end = round(Fraction(str(a)) * fps), min(total_source_frames, round(Fraction(str(b)) * fps))
        if start < previous_end or end <= start:
            raise ValueError("音乐区间不能重叠、倒序或短于一帧")
        sections.append(dict(source_frame=start, frames=end - start, offset=cursor))
        for m in project.markers:
            if m.enabled and a <= m.time < b:
                at = round(Fraction(str(m.time)) * fps)
                if start <= at < end:
                    mapped.append(dict(frame=cursor + at - start, source_frame=at, forced=m.forced))
        cursor += end - start
        previous_end = end
    if cursor <= 0:
        raise ValueError("没有可导出的音乐区间")
    # Frame collisions must not create duplicate markers or zero-length cuts.
    unique = {}
    for m in mapped:
        key = m["frame"]
        m["forced"] = m["forced"] or unique.get(key, {}).get("forced", False)
        unique[key] = m
    mapped = sorted(unique.values(), key=lambda x: x["frame"])
    cuts = {0, cursor, *(s["offset"] for s in sections)}
    next_beat = 0
    for i, m in enumerate(mapped):
        if i < next_beat and not m["forced"]:
            continue
        cuts.add(m["frame"])
        p = m["frame"] / cursor
        multiplier = 1
        if project.pace == "crescendo":
            multiplier = 2 if p < .35 else (1 if p < .7 else .5)
        elif project.pace == "valley":
            multiplier = 2 if .25 < p < .75 else .5
        elif project.pace == "rise-valley":
            multiplier = 2 if .2 < p < .55 else (1 if p < .8 else .5)
        next_beat = i + max(1, round(project.interval * multiplier))
    cuts = sorted(cuts)
    slots = [dict(offset=a, frames=b - a) for a, b in zip(cuts, cuts[1:]) if b > a]
    # A short lead-in before the first detected beat belongs to the opening shot.
    if len(slots) > 1 and slots[0]["frames"] < float(fps) * .35 and not any(m["forced"] and m["frame"] == cuts[1] for m in mapped):
        slots[1] = dict(offset=0, frames=slots[0]["frames"] + slots[1]["frames"])
        slots.pop(0)
    choices = list(project.clips)
    ids = [c.id for c in choices]
    if len(ids) != len(set(ids)):
        raise ValueError("同一素材只需导入一次；编排会自动循环使用素材")
    if project.mode == "clips" and not choices:
        raise ValueError("请先添加视频或图片素材，或选择标记 / 占位剪辑模式")
    for pinned in (project.first_id, project.last_id):
        if pinned and pinned not in ids:
            raise ValueError("固定的首尾镜头已被移除，请重新选择")
    if len(slots) == 1 and project.first_id and project.last_id and project.first_id != project.last_id:
        raise ValueError("当前只有一个镜头位置，无法同时放入不同的首尾素材")
    if project.order == "shuffle":
        random.Random(project.seed).shuffle(choices)
    middle = [c for c in choices if c.id not in (project.first_id, project.last_id)] or choices
    warnings = []
    for i, slot in enumerate(slots):
        slot["seconds"] = float(slot["frames"] * frame)
        if project.mode != "clips":
            slot.update(id=None, name=f"镜头 {i + 1:02}", source_in=0, conform="fit")
            continue
        pinned = project.first_id if i == 0 else (project.last_id if i == len(slots) - 1 else None)
        candidates = [c for c in choices if c.id == pinned] if pinned else middle[i % len(middle):] + middle[:i % len(middle)]
        selected = None
        for c in candidates:
            item = media[c.id]
            end = c.end if c.end is not None else item["duration"]
            if item["kind"] == "image" or (0 <= c.start < end <= item["duration"] + 1e-6 and end - c.start + 1e-7 >= slot["seconds"]):
                selected = c
                break
        if selected is None:
            raise ValueError(f"第 {i + 1} 个镜头需要 {slot['seconds']:.2f} 秒，候选素材可用长度不足。请减少每镜头拍数、放宽素材入出点或添加更长素材。")
        item = media[selected.id]
        slot.update(id=selected.id, name=item["name"], source_in=selected.start, conform=selected.conform)
    if not mapped:
        warnings.append("没有启用的节拍标记；当前将导出连续音乐和单个镜头。")
    return dict(fps=project.fps, frames=cursor, duration=float(cursor * frame),
                sections=sections, markers=mapped, slots=slots, warnings=warnings)


def make_fcpxml(project: Project, plan: dict, music: dict, media: dict[str, dict]) -> bytes:
    from pathlib import Path

    frame = 1 / Fraction(project.fps)
    tv = lambda frames: time_value(frames * frame)
    root = ET.Element("fcpxml", version="1.10")
    resources = ET.SubElement(root, "resources")
    ET.SubElement(resources, "format", id="r1", frameDuration=tv(1), width=str(project.width),
                  height=str(project.height), colorSpace="1-1-1 (Rec. 709)")

    def add_asset(meta, ref, fmt=None):
        attributes = dict(id=ref, name=meta["name"], start=time_value(Fraction(meta["source_start"])),
                          duration=time_value(Fraction(str(meta["duration"])).limit_denominator(48000)))
        if fmt:
            attributes.update(hasVideo="1", format=fmt, videoSources="1")
        if meta.get("has_audio"):
            attributes.update(hasAudio="1", audioSources="1", audioChannels=str(meta["audio_channels"]), audioRate=str(meta["audio_rate"]))
        asset = ET.SubElement(resources, "asset", **attributes)
        ET.SubElement(asset, "media-rep", kind="original-media", src=Path(meta["path"]).resolve().as_uri())

    add_asset(music, "r2")
    refs = {}
    for index, media_id in enumerate(dict.fromkeys(s["id"] for s in plan["slots"] if s["id"])):
        meta = media[media_id]
        fmt, ref = f"f{index + 1}", f"a{index + 1}"
        ET.SubElement(resources, "format", id=fmt, frameDuration=time_value(1 / Fraction(meta["fps"])),
                      width=str(meta["width"]), height=str(meta["height"]))
        add_asset(meta, ref, fmt)
        refs[media_id] = (ref, fmt)
    library = ET.SubElement(root, "library")
    event = ET.SubElement(library, "event", name="BeatCut · " + project.name)
    music_frames = math.floor(Fraction(str(music["duration"])) / frame)
    event_music = ET.SubElement(event, "asset-clip", ref="r2", name=music["name"] + " · 全部标记", start="0s",
                                duration=tv(music_frames), format="r1", audioRole="music")
    event_markers = {round(Fraction(str(m.time)) / frame) for m in project.markers if m.enabled and m.time < music["duration"]}
    for i, at in enumerate(sorted(t for t in event_markers if t < music_frames)):
        ET.SubElement(event_music, "marker", start=tv(at), duration=tv(1), value=f"Beat {i + 1:03}")
    sequence = ET.SubElement(ET.SubElement(event, "project", name=project.name), "sequence", format="r1",
                             duration=tv(plan["frames"]), tcStart="0s", tcFormat="NDF", audioLayout="stereo", audioRate="48k")
    spine = ET.SubElement(sequence, "spine")

    def audio_sections(parent, parent_origin=Fraction(0), connected=False):
        for section in plan["sections"]:
            attrs = dict(ref="r2", name=music["name"], offset=time_value(parent_origin + section["offset"] * frame),
                         start=tv(section["source_frame"]), duration=tv(section["frames"]), audioRole="music", format="r1")
            if connected:
                attrs["lane"] = "-1"
            clip = ET.SubElement(parent, "asset-clip", **attrs)
            for i, marker in enumerate(plan["markers"]):
                if section["offset"] <= marker["frame"] < section["offset"] + section["frames"]:
                    at = section["source_frame"] + marker["frame"] - section["offset"]
                    ET.SubElement(clip, "marker", start=tv(at), duration=tv(1),
                                  value=("CUT " if marker["forced"] else "Beat ") + f"{i + 1:03}")

    if project.mode == "markers":
        audio_sections(spine)
    else:
        for i, slot in enumerate(plan["slots"]):
            origin = Fraction(0)
            if slot["id"]:
                meta = media[slot["id"]]
                ref, fmt = refs[slot["id"]]
                origin = Fraction(meta["source_start"]) + Fraction(str(slot["source_in"]))
                clip = ET.SubElement(spine, "asset-clip", ref=ref, name=slot["name"], offset=tv(slot["offset"]),
                                     start=time_value(origin), duration=tv(slot["frames"]), format=fmt, srcEnable="video")
                ET.SubElement(clip, "adjust-conform", type=slot["conform"])
            else:
                clip = ET.SubElement(spine, "gap", name=slot["name"], offset=tv(slot["offset"]), start="0s", duration=tv(slot["frames"]))
            if i == 0:
                audio_sections(clip, origin, connected=True)
            ET.SubElement(clip, "marker", start=time_value(origin), duration=tv(1), value=f"Cut {i + 1:03}")
    ET.indent(root, space="  ")
    return b'<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n' + ET.tostring(root, encoding="utf-8")
