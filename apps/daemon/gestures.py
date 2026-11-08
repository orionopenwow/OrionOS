"""Hand gestures and presence, as pure logic on landmark lists (after-1.0 plan §6, Phase 5).

No camera, no MediaPipe and no OpenCV in here: presence.py turns each camera frame into numbers (21 hand points per
hand, three face points) and throws the frame away; this file only ever sees those numbers. That keeps every rule
testable with made-up landmark sequences.

Coordinates are MediaPipe's: x and y from 0 to 1 across the picture, y growing downwards. presence.py mirrors the
picture first, so x grows towards the user's right (like a mirror).

Hand poses (one frame):
    open      all five fingers out              thumbs_up  fist with the thumb pointing up
    point     only the index finger out         pinch      thumb tip touching the index tip
    two       index and middle out ("draw")     fist       all fingers curled
    other     anything else

Gestures (over time) only fire while a hand has been held in the gesture zone for ZONE_HOLD_S (half a second):
    pointer / click     point to move the pointer, pinch to click
    stop                open palm held still for PALM_HOLD_S: the kill switch
    swipe_left/right    open hand moved quickly sideways: switch desktop
    rectangle           draw a rectangle in the air with two fingers: new desktop
    throw               make a fist (grab), move fast and open the hand: left / right / down ("into the black hole")
    spread / squeeze    two hands moving apart / together: zoom the HUD, open or close the panel
    thumbs_up           approve the current small card (presence.py decides what "small" means)
"""
from __future__ import annotations

import math
from collections import deque
from typing import Any, Optional, Sequence

Point = Sequence[float]
Hand = Sequence[Point]          # 21 points, MediaPipe hand landmark order

WRIST = 0
THUMB_MCP, THUMB_IP, THUMB_TIP = 2, 3, 4
INDEX_MCP, INDEX_PIP, INDEX_TIP = 5, 6, 8
MIDDLE_MCP, MIDDLE_PIP, MIDDLE_TIP = 9, 10, 12
RING_MCP, RING_PIP, RING_TIP = 13, 14, 16
PINKY_MCP, PINKY_PIP, PINKY_TIP = 17, 18, 20
FINGERS = ((INDEX_TIP, INDEX_PIP), (MIDDLE_TIP, MIDDLE_PIP), (RING_TIP, RING_PIP), (PINKY_TIP, PINKY_PIP))

DEFAULT_ZONE = (0.12, 0.06, 0.88, 0.85)   # x0, y0, x1, y1 of the gesture zone in the (mirrored) picture
ZONE_HOLD_S = 0.5          # a hand must stay in the zone this long before any gesture counts
LOST_GRACE_S = 0.3         # a hand missing for a moment (motion blur) doesn't disarm
PALM_HOLD_S = 1.0          # open palm held this long = stop
PALM_STILL = 0.07          # ... and moving less than this (fraction of the picture)
THUMB_HOLD_S = 0.6
SWIPE_WINDOW_S = 0.45
SWIPE_DIST = 0.22
THROW_SPEED = 1.1          # picture widths per second when the hand opens
CLICK_GAP_S = 0.5
COOLDOWN_S = 0.8
DRAW_MAX_S = 5.0
TWO_HAND_SPREAD = 1.35
TWO_HAND_SQUEEZE = 0.72


# ============================================================================================ geometry
def dist(a: Point, b: Point) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def valid_hand(h: Any) -> bool:
    try:
        return len(h) == 21 and all(len(p) >= 2 for p in h)
    except TypeError:
        return False


def hand_size(h: Hand) -> float:
    return max(dist(h[WRIST], h[MIDDLE_MCP]), 1e-6)


def palm_center(h: Hand) -> tuple[float, float]:
    idx = (WRIST, INDEX_MCP, MIDDLE_MCP, RING_MCP, PINKY_MCP)
    return (sum(h[i][0] for i in idx) / 5.0, sum(h[i][1] for i in idx) / 5.0)


def finger_out(h: Hand, tip: int, pip: int) -> bool:
    """A finger is out when its tip is clearly further from the wrist than its middle joint (works at any angle)."""
    return dist(h[tip], h[WRIST]) > dist(h[pip], h[WRIST]) * 1.15


def thumb_out(h: Hand) -> bool:
    return dist(h[THUMB_TIP], h[INDEX_MCP]) > 0.6 * hand_size(h) and \
        dist(h[THUMB_TIP], h[WRIST]) > dist(h[THUMB_IP], h[WRIST])


def pose(h: Hand) -> str:
    if not valid_hand(h):
        return "other"
    s = hand_size(h)
    out = [finger_out(h, t, p) for t, p in FINGERS]
    thumb = thumb_out(h)
    if all(out) and thumb:
        return "open"
    if dist(h[THUMB_TIP], h[INDEX_TIP]) < 0.28 * s and not all(out[1:]):
        return "pinch"
    if out[0] and out[1] and not out[2] and not out[3]:
        return "two"
    if out[0] and not any(out[1:]):
        return "point"
    if not any(out):
        tip = h[THUMB_TIP]
        if thumb and tip[1] < h[THUMB_MCP][1] - 0.35 * s and all(tip[1] < h[t][1] for t, _ in FINGERS):
            return "thumbs_up"
        return "fist"
    return "other"


def in_zone(p: Point, zone: Sequence[float]) -> bool:
    return zone[0] <= p[0] <= zone[2] and zone[1] <= p[1] <= zone[3]


def to_screen(p: Point, zone: Sequence[float]) -> tuple[float, float]:
    """A point inside the gesture zone -> 0..1 across the screen (so small hand moves cover the whole screen)."""
    w, hgt = max(zone[2] - zone[0], 1e-6), max(zone[3] - zone[1], 1e-6)
    return (min(1.0, max(0.0, (p[0] - zone[0]) / w)), min(1.0, max(0.0, (p[1] - zone[1]) / hgt)))


def is_rectangle(path: Sequence[Point]) -> bool:
    """A closed path that hugs its bounding box and passes near all four corners (a circle misses the corners)."""
    if len(path) < 8:
        return False
    xs, ys = [p[0] for p in path], [p[1] for p in path]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    w, h = x1 - x0, y1 - y0
    if w < 0.12 or h < 0.12:
        return False
    diag = math.hypot(w, h)
    if dist(path[0], path[-1]) > 0.25 * diag:
        return False                               # not closed
    edge = 0.2 * min(w, h)
    near_edge = sum(1 for x, y in zip(xs, ys) if min(x - x0, x1 - x, y - y0, y1 - y) <= edge)
    if near_edge < 0.85 * len(path):
        return False
    corners = ((x0, y0), (x1, y0), (x1, y1), (x0, y1))
    return all(min(dist(p, c) for p in path) <= 0.1 * diag for c in corners)


def facing(face: Optional[dict[str, Point]]) -> Optional[bool]:
    """Is the face turned towards the screen? From the nose's place between the eyes. None = can't tell."""
    if not face:
        return None
    try:
        nose, a, b = face["nose"], face["left_eye"], face["right_eye"]
    except (KeyError, TypeError):
        return None
    lo, hi = min(a[0], b[0]), max(a[0], b[0])
    span = hi - lo
    if span < 0.02:
        return False                               # eyes on top of each other: looking well away
    r = (nose[0] - lo) / span
    return 0.28 <= r <= 0.72


# ============================================================================================ gestures over time
class GestureEngine:
    """Feed it one frame of hands at a time (`feed(t, hands)`); it returns the gestures that just happened."""

    def __init__(self, zone: Sequence[float] = DEFAULT_ZONE, zone_hold_s: float = ZONE_HOLD_S) -> None:
        self.zone = tuple(zone)
        self.zone_hold_s = zone_hold_s
        self.reset()

    def reset(self) -> None:
        self.in_zone_since: Optional[float] = None
        self.last_seen: Optional[float] = None
        self.cur_pose = "none"
        self.pose_since = 0.0
        self.pose_fired = False
        self.hist: deque[tuple[float, tuple[float, float], str]] = deque(maxlen=64)
        self.path: list[tuple[float, float]] = []
        self.path_since: Optional[float] = None
        self.grabbing = False
        self.last_click = -1e9
        self.cooldown_until = -1e9
        self.two_base: Optional[float] = None
        self.last_pose_seen = "none"

    # -------------------------------------------------------------------------- helpers
    def armed(self, t: float) -> bool:
        return self.in_zone_since is not None and t - self.in_zone_since >= self.zone_hold_s

    def _primary(self, hands: list[Hand]) -> Optional[Hand]:
        good = [h for h in hands if valid_hand(h)]
        return max(good, key=hand_size) if good else None

    def _lost(self, t: float) -> list[dict[str, Any]]:
        if self.last_seen is not None and t - self.last_seen <= LOST_GRACE_S:
            return []
        self.in_zone_since = None
        self.cur_pose, self.pose_fired, self.grabbing = "none", False, False
        self.path, self.path_since, self.two_base = [], None, None
        self.hist.clear()
        return []

    def _set_pose(self, t: float, p: str) -> None:
        if p != self.cur_pose:
            self.cur_pose, self.pose_since, self.pose_fired = p, t, False

    def _held(self, t: float) -> float:
        """How long the current pose has been held since the hand was armed (arming time doesn't count)."""
        start = self.pose_since
        if self.in_zone_since is not None:
            start = max(start, self.in_zone_since + self.zone_hold_s)
        return t - start

    def _span(self, since: float) -> float:
        pts = [c for (tt, c, _) in self.hist if tt >= since]
        if len(pts) < 2:
            return 0.0
        return max(dist(a, b) for a in pts for b in pts)

    # -------------------------------------------------------------------------- feed
    def feed(self, t: float, hands: Sequence[Hand]) -> list[dict[str, Any]]:
        hands = [h for h in (hands or []) if valid_hand(h)]
        if not hands:
            return self._lost(t)
        h = self._primary(hands)
        assert h is not None
        center = palm_center(h)
        self.last_seen = t
        if not in_zone(center, self.zone):
            self.in_zone_since = None
            self.cur_pose, self.pose_fired, self.grabbing = "none", False, False
            self.path, self.path_since, self.two_base = [], None, None
            self.hist.clear()
            return []
        if self.in_zone_since is None:
            self.in_zone_since = t
        if len(hands) >= 2:
            return self._two_hands(t, hands)
        self.two_base = None
        p = pose(h)
        self.hist.append((t, center, p))
        if not self.armed(t):
            self._set_pose(t, p)
            return []
        out: list[dict[str, Any]] = []
        prev = self.cur_pose
        self._set_pose(t, p)
        out += self._grab_throw(t, prev, p)
        out += self._draw(t, p, h)
        if t < self.cooldown_until:
            return out
        if p in ("point", "pinch"):
            tip = h[INDEX_TIP] if p == "point" else ((h[INDEX_TIP][0] + h[THUMB_TIP][0]) / 2,
                                                    (h[INDEX_TIP][1] + h[THUMB_TIP][1]) / 2)
            x, y = to_screen(tip, self.zone)
            out.append({"gesture": "pointer", "x": round(x, 4), "y": round(y, 4)})
            if p == "pinch" and prev == "point" and t - self.last_click >= CLICK_GAP_S:
                self.last_click = t
                out.append({"gesture": "click", "x": round(x, 4), "y": round(y, 4)})
        elif p == "open":
            swipe = self._swipe(t)
            if swipe:
                out.append(swipe)
            elif not self.pose_fired and self._held(t) >= PALM_HOLD_S and \
                    self._span(t - PALM_HOLD_S) <= PALM_STILL:
                self.pose_fired = True
                out.append({"gesture": "stop"})
        elif p == "thumbs_up":
            if not self.pose_fired and self._held(t) >= THUMB_HOLD_S and \
                    self._span(t - THUMB_HOLD_S) <= PALM_STILL:
                self.pose_fired = True
                out.append({"gesture": "thumbs_up"})
        return out

    def _swipe(self, t: float) -> Optional[dict[str, Any]]:
        pts = [(tt, c) for (tt, c, pp) in self.hist if tt >= t - SWIPE_WINDOW_S and pp == "open"]
        if len(pts) < 3:
            return None
        dx = pts[-1][1][0] - pts[0][1][0]
        dy = pts[-1][1][1] - pts[0][1][1]
        if abs(dx) >= SWIPE_DIST and abs(dy) <= 0.6 * abs(dx):
            self.cooldown_until = t + COOLDOWN_S
            self.pose_since, self.pose_fired = t, True     # a swipe is never also a palm hold
            self.hist.clear()
            return {"gesture": "swipe_right" if dx > 0 else "swipe_left"}
        return None

    def _grab_throw(self, t: float, prev: str, p: str) -> list[dict[str, Any]]:
        if p == "fist":
            self.grabbing = True
            return []
        if not self.grabbing:
            return []
        self.grabbing = False                           # the hand opened: a throw if it was moving fast
        pts = [(tt, c) for (tt, c, _) in self.hist if tt >= t - 0.25]
        if len(pts) < 2 or pts[-1][0] - pts[0][0] <= 0:
            return []
        dt = pts[-1][0] - pts[0][0]
        vx = (pts[-1][1][0] - pts[0][1][0]) / dt
        vy = (pts[-1][1][1] - pts[0][1][1]) / dt
        if math.hypot(vx, vy) < THROW_SPEED:
            return []                                   # put down gently: nothing happens
        self.cooldown_until = t + COOLDOWN_S
        self.pose_fired = True
        if abs(vx) >= abs(vy):
            return [{"gesture": "throw", "direction": "right" if vx > 0 else "left"}]
        return [{"gesture": "throw", "direction": "down" if vy > 0 else "up"}]

    def _draw(self, t: float, p: str, h: Hand) -> list[dict[str, Any]]:
        if p == "two":
            if self.path_since is None or t - self.path_since > DRAW_MAX_S:
                self.path, self.path_since = [], t
            self.path.append(tuple(h[INDEX_TIP][:2]))  # type: ignore[arg-type]
            if len(self.path) >= 12 and is_rectangle(self.path) and t >= self.cooldown_until:
                self.path, self.path_since = [], None
                self.cooldown_until = t + COOLDOWN_S
                return [{"gesture": "rectangle"}]
            return []
        self.path, self.path_since = [], None
        return []

    def _two_hands(self, t: float, hands: list[Hand]) -> list[dict[str, Any]]:
        a, b = sorted(hands, key=hand_size, reverse=True)[:2]
        ca, cb = palm_center(a), palm_center(b)
        if not (in_zone(cb, self.zone) and self.armed(t)):
            self.two_base = None
            return []
        if pose(a) == "fist" or pose(b) == "fist":
            self.two_base = None
            return []
        d = dist(ca, cb)
        if self.two_base is None:
            self.two_base = d
            return []
        if t < self.cooldown_until:
            return []
        ratio = d / max(self.two_base, 1e-6)
        if ratio >= TWO_HAND_SPREAD or ratio <= TWO_HAND_SQUEEZE:
            self.two_base = d
            self.cooldown_until = t + COOLDOWN_S
            return [{"gesture": "spread" if ratio > 1 else "squeeze"}]
        return []


# ============================================================================================ presence over time
class PresenceTracker:
    """Feed it `feed(t, face_seen, facing)` once per frame; it says when you leave, come back or look away, and
    counts the seconds you really spent at the screen."""

    def __init__(self, away_after_s: float = 30.0, back_after_s: float = 1.0, look_away_s: float = 4.0) -> None:
        self.away_after_s = away_after_s
        self.back_after_s = back_after_s
        self.look_away_s = look_away_s
        self.present: Optional[bool] = None
        self.last_face: Optional[float] = None
        self.face_since: Optional[float] = None
        self.away_look_since: Optional[float] = None
        self.looking_away = False
        self.screen_s = 0.0
        self.last_t: Optional[float] = None
        self.started: Optional[float] = None

    def feed(self, t: float, face: bool, looking: Optional[bool] = None) -> list[str]:
        out: list[str] = []
        if self.started is None:
            self.started = t
        dt = 0.0 if self.last_t is None else min(2.0, max(0.0, t - self.last_t))
        self.last_t = t
        if face:
            self.last_face = t
            self.face_since = t if self.face_since is None else self.face_since
            if self.present is None:
                self.present = True
                out.append("here")
            elif self.present is False and t - self.face_since >= self.back_after_s:
                self.present = True
                out.append("back")
            if self.present and looking is not False:
                self.screen_s += dt
            if looking is False:
                self.away_look_since = t if self.away_look_since is None else self.away_look_since
                if self.present and not self.looking_away and t - self.away_look_since >= self.look_away_s:
                    self.looking_away = True
                    out.append("look_away")
            else:
                self.away_look_since = None
                if self.looking_away:
                    self.looking_away = False
                    out.append("look_back")
        else:
            self.face_since = None
            ref = self.last_face if self.last_face is not None else self.started
            if self.present is not False and t - ref >= self.away_after_s:
                self.present = False
                self.looking_away = False
                self.away_look_since = None
                out.append("away")
        return out
