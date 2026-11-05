from studio import captions

ROLLING = """WEBVTT
Kind: captions
Language: fr

00:00:04.759 --> 00:00:06.829 align:start position:0%

Ça<00:00:04.920><c> fait</c><00:00:05.080><c> partie</c>

00:00:06.829 --> 00:00:06.839 align:start position:0%
Ça fait partie


00:00:06.839 --> 00:00:09.000 align:start position:0%
Ça fait partie
d'un<00:00:07.000><c> cours.</c>
"""

PLAIN = """WEBVTT

00:01:00.000 --> 00:01:02.000
Première ligne

01:00:03.000 --> 01:00:05.500
Deuxième ligne
"""


def test_rolling_captions_keep_each_line_once():
    cues = captions.parse_vtt(ROLLING)
    assert [c["text"] for c in cues] == ["Ça fait partie", "d'un cours."]
    assert cues[0]["start"] == 4.759
    assert cues[1]["start"] == 6.839


def test_plain_captions_and_hours():
    cues = captions.parse_vtt(PLAIN)
    assert cues[1]["start"] == 3603.0
    assert cues[1]["text"] == "Deuxième ligne"


def test_passages_stop_near_target_on_sentence_end():
    cues = [{"start": i * 10.0, "end": i * 10.0 + 10, "text": f"ligne {i}."} for i in range(7)]
    passages = captions.to_passages(cues)
    assert [p["start"] for p in passages] == [0, 30, 60]
    assert passages[0]["text"] == "ligne 0. ligne 1. ligne 2."


def test_passage_is_cut_even_without_punctuation():
    cues = [{"start": i * 10.0, "end": i * 10.0 + 10, "text": "sans point"} for i in range(9)]
    assert all(p["end"] - p["start"] <= 50 for p in captions.to_passages(cues))


def test_timestamp():
    assert captions.timestamp(75) == "01:15"
    assert captions.timestamp(3725) == "1:02:05"


def test_srt():
    srt = captions.to_srt([{"start": 1.5, "end": 3661.25, "text": "Bonjour"}])
    assert "00:00:01,500 --> 01:01:01,250" in srt
