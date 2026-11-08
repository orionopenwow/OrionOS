"""Now-playing music via Windows' system media session (the same source as the volume flyout).

Works with Spotify, browsers (YouTube, SoundCloud…), Apple Music, VLC — anything that shows up in the Windows media
overlay. Needs the optional `winsdk` package (npm run setup:daemon:full); without it the widget simply stays hidden.
"""
from __future__ import annotations

import base64
import logging
import sys
from typing import Any, Optional

log = logging.getLogger("orion.media")

PLAYING = 4  # GlobalSystemMediaTransportControlsSessionPlaybackStatus.PLAYING


class MediaSession:
    def __init__(self) -> None:
        self.available = sys.platform == "win32"
        self._mgr: Any = None
        self._art_key: Optional[str] = None
        self._art: Optional[str] = None

    async def _manager(self) -> Any:
        if self._mgr is None:
            from winsdk.windows.media.control import (  # type: ignore
                GlobalSystemMediaTransportControlsSessionManager as Manager,
            )
            self._mgr = await Manager.request_async()
        return self._mgr

    async def _session(self) -> Any:
        if not self.available:
            return None
        try:
            return (await self._manager()).get_current_session()
        except ImportError:
            self.available = False
            log.info("Music widget needs the optional 'winsdk' package (npm run setup:daemon:full)")
        except Exception as exc:
            log.debug("media session unavailable: %s", exc)
        return None

    async def _artwork(self, props: Any, key: str) -> Optional[str]:
        if key == self._art_key:
            return self._art
        self._art_key, self._art = key, None
        try:
            from winsdk.windows.storage.streams import Buffer, DataReader, InputStreamOptions  # type: ignore

            if props.thumbnail is None:
                return None
            stream = await props.thumbnail.open_read_async()
            size = min(int(stream.size), 2_000_000)
            buf = Buffer(size)
            await stream.read_async(buf, size, InputStreamOptions.READ_AHEAD)
            reader = DataReader.from_buffer(buf)
            data = bytearray(buf.length)
            reader.read_bytes(data)
            self._art = "data:image/jpeg;base64," + base64.b64encode(bytes(data)).decode()
        except Exception as exc:
            log.debug("album art unavailable: %s", exc)
        return self._art

    async def now_playing(self) -> Optional[dict[str, Any]]:
        session = await self._session()
        if session is None:
            return None
        try:
            props = await session.try_get_media_properties_async()
            info = session.get_playback_info()
            timeline = session.get_timeline_properties()
            title = props.title or ""
            if not title:
                return None
            app = (session.source_app_user_model_id or "").split("!")[0].split("\\")[-1]
            app = app.replace(".exe", "").split(".")[-1] if app else ""
            key = f"{title}|{props.artist}"
            return {
                "title": title,
                "artist": props.artist or "",
                "album": props.album_title or "",
                "app": app,
                "playing": int(info.playback_status) == PLAYING,
                "position": timeline.position.total_seconds() if timeline else None,
                "duration": timeline.end_time.total_seconds() if timeline else None,
                "art": await self._artwork(props, key),
            }
        except Exception as exc:
            log.debug("now_playing failed: %s", exc)
            return None

    async def control(self, action: str) -> bool:
        session = await self._session()
        if session is None:
            return False
        try:
            if action == "toggle":
                return bool(await session.try_toggle_play_pause_async())
            if action == "next":
                return bool(await session.try_skip_next_async())
            if action == "previous":
                return bool(await session.try_skip_previous_async())
        except Exception as exc:
            log.debug("media control %s failed: %s", action, exc)
        return False
