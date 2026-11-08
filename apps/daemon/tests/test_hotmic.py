"""Hot mic: loudness gate, phrase cutting, voice check, wake word, and that voice can't approve."""
import numpy as np

import hotmic as hm

RATE, FRAME = hm.RATE, hm.FRAME


def tone(seconds, amp, freq=220):
    t = np.arange(int(seconds * RATE)) / RATE
    return (amp * 32767 * np.sin(2 * np.pi * freq * t)).astype(np.int16)


def frames(audio):
    return [audio[i:i + FRAME] for i in range(0, len(audio) - FRAME + 1, FRAME)]


def speechlike(f):  # stand-in VAD: anything not near-silent is "speech"
    return hm.frame_db(f) > -60


def test_loudness():
    assert hm.frame_db(np.zeros(FRAME, np.int16)) == -120.0
    assert -4 < hm.frame_db(tone(0.03, 1.0)[:FRAME]) < -2
    assert -44 < hm.frame_db(tone(0.03, 0.01)[:FRAME]) < -42


def test_segmenter_cuts_phrases_and_ignores_too_quiet_or_too_loud():
    seg = hm.Segmenter(speechlike, -40, -6)
    audio = np.concatenate([np.zeros(RATE, np.int16), tone(1.2, 0.1), np.zeros(RATE, np.int16)])
    out = [a for a in (seg.feed(f) for f in frames(audio)) if a is not None]
    assert len(out) == 1 and 1.0 < len(out[0]) / RATE < 1.6
    for amp in (0.002, 0.99):  # whisper-quiet TV across the room / someone shouting into it
        seg = hm.Segmenter(speechlike, -40, -6)
        audio = np.concatenate([tone(1.2, amp), np.zeros(RATE, np.int16)])
        assert all(seg.feed(f) is None for f in frames(audio))


def test_wake_word():
    assert hm.strip_wake("Orion, start the server.", True) == "start the server."
    assert hm.strip_wake("Hey Orion start streaming", True) == "start streaming"
    assert hm.strip_wake("start the server", True) is None
    assert hm.strip_wake("start the server", False) == "start the server"
    assert hm.strip_wake("Orionite rules", True) is None


class Rig:
    def __init__(self, tmp_path, voice_vec, heard="Orion, start the server"):
        self.handled, self.spoken, self.events = [], [], []
        self.mic = hm.HotMic(tmp_path, handle=lambda t: self.handled.append(t) or "Starting.",
                             speak=self.spoken.append, publish=self.events.append,
                             transcribe=lambda a: heard, embed=lambda a: voice_vec, is_speech=speechlike)
        self.mic.set_config(min_db=-40, max_db=-6)


def phrase():
    return frames(np.concatenate([np.zeros(RATE // 2, np.int16), tone(1.2, 0.1), np.zeros(RATE, np.int16)]))


def test_only_your_voice_runs_commands(tmp_path):
    me = np.array([1.0, 0.2, 0.0, 0.1], np.float32)
    rig = Rig(tmp_path, me)
    assert rig.mic.process_frames(phrase()) == []          # no profile yet: nothing runs
    hm.VoiceProfile(tmp_path / "voice_profile.json").save([me + d for d in (0, 0.01, -0.01, 0.02)], "test")
    assert rig.mic.process_frames(phrase()) == ["start the server"]
    assert rig.handled == ["start the server"] and rig.spoken == ["Starting."]
    stranger = Rig(tmp_path, np.array([0.0, 0.1, 1.0, -0.4], np.float32))
    assert stranger.mic.process_frames(phrase()) == []
    assert stranger.mic.last["ignored"] == "not your voice"


def test_no_wake_word_no_command(tmp_path):
    me = np.array([1.0, 0.2, 0.0, 0.1], np.float32)
    hm.VoiceProfile(tmp_path / "voice_profile.json").save([me] * 4, "test")
    rig = Rig(tmp_path, me, heard="start the server")
    assert rig.mic.process_frames(phrase()) == [] and rig.mic.last["ignored"] == "no wake word"
    rig.mic.set_config(require_wake=False)
    assert rig.mic.process_frames(phrase()) == ["start the server"]


def test_profile_threshold_is_sane(tmp_path):
    rng = np.random.default_rng(1)
    base = rng.normal(size=192).astype(np.float32)
    d = hm.VoiceProfile(tmp_path / "p.json").save([base + rng.normal(scale=0.3, size=192) for _ in range(20)], "t")
    assert 0.55 <= d["threshold"] <= 0.8
    assert oct(__import__("os").stat(tmp_path / "p.json").st_mode)[-3:] == "600"


def test_voice_cannot_approve(tmp_path):
    """Voice goes to quick commands/chat only; neither has a path to a verdict."""
    import inspect
    import main
    src = inspect.getsource(main.OrionDaemon.converse) + inspect.getsource(main.OrionDaemon._hotmic_handle)
    assert ".verdict(" not in src and "decide_approval" not in src
    from quick_commands import QuickCommands
    q = QuickCommands(config=tmp_path / "q.json")
    for said in ("approve", "approve it", "yes approve the payout", "sign it"):
        assert q.match(said) is None
