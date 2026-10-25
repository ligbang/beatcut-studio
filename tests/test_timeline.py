from fractions import Fraction
from pathlib import Path
import math
import xml.etree.ElementTree as ET

import pytest

from beatcut.media import source_timecode
from beatcut.models import ClipChoice, Marker, Project
from beatcut.timeline import build_plan, make_fcpxml


def media(id="music", duration=24, kind="music", start="0"):
    return dict(id=id, duration=duration, name='音乐 & "场景".wav', kind=kind, path="/tmp/中文 空格 & 测试.wav",
                source_start=start, fps="30000/1001", width=1920, height=1080, has_audio=True,
                audio_rate="48000", audio_channels=2)


def project(**kwargs):
    return Project(music_id="music", markers=[Marker(time=i * .5) for i in range(48)], **kwargs)


def test_last_fractional_frame_marker_stays_inside_event_audio():
    song = media(duration=1.01)
    p = Project(music_id="music", markers=[Marker(time=.25), Marker(time=1.005)])
    xml = ET.fromstring(make_fcpxml(p, build_plan(p, song, {}), song, {}))
    clip = xml.find('.//event/asset-clip')
    duration = Fraction(clip.attrib['duration'].removesuffix('s'))
    for marker in clip.findall('marker'):
        assert Fraction(marker.attrib['start'].removesuffix('s')) + Fraction(marker.attrib['duration'].removesuffix('s')) <= duration


@pytest.mark.parametrize("fps", ["24", "25", "24000/1001", "30000/1001", "60000/1001"])
def test_exact_frame_grid_and_no_overrun(fps):
    p = project(fps=fps, ranges=[(1.17, 8.23), (12.51, 23.99)], mode="placeholders")
    plan = build_plan(p, media(), {})
    assert sum(s["frames"] for s in plan["slots"]) == plan["frames"]
    assert all(s["frames"] > 0 for s in plan["slots"])
    assert len(set(m["frame"] for m in plan["markers"])) == len(plan["markers"])
    assert all(0 <= m["frame"] < plan["frames"] for m in plan["markers"])
    doc = ET.fromstring(make_fcpxml(p, plan, media(), {}))
    for clip in doc.findall(".//spine/gap"):
        assert (Fraction(clip.get("duration")[:-1]) * Fraction(fps)).denominator == 1
    assert Fraction(doc.find(".//sequence").get("duration")[:-1]) == Fraction(plan["frames"]) / Fraction(fps)


def test_trimmed_markers_use_source_time_and_connected_offset_uses_anchor_origin():
    p = project(ranges=[(2, 6), (12, 16)], clips=[ClipChoice(id="v", start=1)], mode="clips")
    assets = {"v": media("v", 30, "clip", "3600")}
    plan = build_plan(p, media(), assets)
    root = ET.fromstring(make_fcpxml(p, plan, media(), assets))
    first = root.find(".//sequence/spine/asset-clip")
    assert first.get("start") == "3601s"
    tracks = first.findall("asset-clip")
    assert [t.get("offset") for t in tracks] == ["3601s", "3605s"]
    assert [t.get("start") for t in tracks] == ["2s", "12s"]
    assert tracks[1].find("marker").get("start") == "12s"
    assert "%E4%B8%AD" in root.find("resources/asset/media-rep").get("src")


def test_forced_and_ignored_markers():
    p = project(interval=8)
    p.markers[3].forced = True
    p.markers[4].enabled = False
    plan = build_plan(p, media(), {})
    assert round(1.5 * 25) in [s["offset"] for s in plan["slots"]]
    assert 50 not in [m["frame"] for m in plan["markers"]]


def test_pin_order_and_deterministic_shuffle():
    assets = {k: media(k, 30, "clip") for k in "abcd"}
    p = project(clips=[ClipChoice(id=k) for k in assets], mode="clips", first_id="a", last_id="d", order="shuffle")
    plan = build_plan(p, media(), assets)
    assert plan == build_plan(p, media(), assets)
    assert plan["slots"][0]["id"] == "a"
    assert plan["slots"][-1]["id"] == "d"
    assert all(s["id"] in "bc" for s in plan["slots"][1:-1])


def test_short_source_rejected_instead_of_stretched():
    p = project(clips=[ClipChoice(id="v")], mode="clips", interval=8)
    with pytest.raises(ValueError, match="可用长度不足"):
        build_plan(p, media(), {"v": media("v", 1, "clip")})


@pytest.mark.parametrize("ranges", [[(10, 5)], [(0, 7), (5, 9)], [(0, 25)], [(0, float('nan'))], [(0, .001)]])
def test_invalid_ranges_fail(ranges):
    with pytest.raises(ValueError):
        build_plan(project(ranges=ranges), media(), {})


def test_same_frame_markers_merge_and_preserve_force():
    p = Project(music_id="music", markers=[Marker(time=1.000), Marker(time=1.001, forced=True)])
    plan = build_plan(p, media(), {})
    assert len(plan["markers"]) == 1 and plan["markers"][0]["forced"]


@pytest.mark.parametrize("pace", ["flat", "crescendo", "valley", "rise-valley"])
def test_pacing_covers_music_without_gaps(pace):
    plan = build_plan(project(pace=pace), media(), {})
    end = 0
    for slot in plan["slots"]:
        assert slot["offset"] == end
        end += slot["frames"]
    assert end == plan["frames"]


def test_drop_frame_source_origin():
    fps = Fraction(30000, 1001)
    assert source_timecode("01:00:00;00", fps) == Fraction(107892) / fps
    assert source_timecode("00:01:00;02", fps) == Fraction(1800) / fps
    assert source_timecode("00:10:00;00", fps) == Fraction(17982) / fps


@pytest.mark.parametrize("mode", ["markers", "placeholders", "clips"])
def test_official_apple_dtd(mode):
    from lxml import etree
    paths = list(Path("/Applications").glob("Final Cut*.app/Contents/Frameworks/Interchange.framework/Versions/A/Resources/FCPXMLv1_10.dtd"))
    if not paths:
        pytest.skip("Apple DTD not installed; see docs/TESTING.md")
    p = project(mode=mode, clips=[ClipChoice(id="v")] if mode == "clips" else [], ranges=[(2, 8), (12, 24)])
    assets = {"v": media("v", 30, "clip", "3600")}
    data = make_fcpxml(p, build_plan(p, media(), assets), media(), assets)
    dtd = etree.DTD(str(paths[0]))
    assert dtd.validate(etree.fromstring(data)), str(dtd.error_log)
