"""Camera bridge: expose Prosegur cameras as snapshot + MJPEG HTTP endpoints.

Prosegur cameras are snapshot-only (the backend stores the last image taken by
each camera). Smart-home platforms such as Google Home only show cameras that
offer a *stream*. This bridge keeps the latest snapshot of every camera in
memory and re-serves it as a continuous stream:

* MJPEG, for browsers and simple viewers;
* HLS / H.264 (optional, needs ``ffmpeg``), which Home Assistant's ``stream``
  integration can remux and therefore cast to Google Home / Nest Hub /
  Chromecast. Home Assistant only accepts H.264/H.265 stream sources.

Endpoints:
    GET /cameras                      -> JSON list of cameras and their URLs
    GET /camera/{id}/snapshot.jpg     -> latest image (JPEG)
    GET /camera/{id}/stream.mjpg      -> multipart/x-mixed-replace MJPEG stream
    GET /camera/{id}/hls/index.m3u8   -> HLS playlist (H.264), when enabled
    GET /health                       -> JSON with last refresh per camera
"""
import asyncio
import base64
import binascii
import hmac
import logging
import os
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from typing import Dict, Optional

from aiohttp import web

from pyprosegur.auth import Auth
from pyprosegur.exceptions import ProsegurException
from pyprosegur.installation import Installation

LOGGER = logging.getLogger(__name__)

BOUNDARY = "pyprosegurframe"


@dataclass
class Frame:
    """Latest image known for a camera."""

    data: Optional[bytes] = None
    updated: float = 0.0
    error: Optional[str] = None
    event: asyncio.Event = field(default_factory=asyncio.Event)


class CameraBridge:
    """Poll Prosegur cameras and serve them over HTTP."""

    def __init__(
        self,
        auth: Auth,
        contract: str,
        refresh_interval: int = 300,
        request_new: bool = True,
        capture_delay: int = 15,
        frame_interval: float = 1.0,
        username: Optional[str] = None,
        password: Optional[str] = None,
        hls: bool = False,
        ffmpeg: str = "ffmpeg",
    ):
        """Configure the bridge.

        refresh_interval: seconds between image refreshes for each camera.
        request_new: ask the camera to take a new picture before fetching
            (otherwise only the last stored image is fetched).
        capture_delay: seconds to wait between requesting and fetching a picture.
        frame_interval: seconds between frames on the MJPEG stream (the same
            image is repeated so stream consumers stay connected).
        username/password: optional HTTP Basic auth protecting the endpoints.
        hls: also encode every camera to an H.264 HLS stream using ffmpeg.
        ffmpeg: path to the ffmpeg binary.
        """
        self.auth = auth
        self.contract = contract
        self.refresh_interval = refresh_interval
        self.request_new = request_new
        self.capture_delay = capture_delay
        self.frame_interval = frame_interval
        self.username = username
        self.password = password
        self.hls = hls
        self.ffmpeg = ffmpeg
        self.hls_dir: Optional[str] = None
        self._encoders: Dict[str, asyncio.Task] = {}

        self.installation: Optional[Installation] = None
        self.frames: Dict[str, Frame] = {}
        self._poller: Optional[asyncio.Task] = None

    # ------------------------------------------------------------------ polling

    async def setup(self):
        """Retrieve installation and cameras."""
        self.installation = await Installation.retrieve(self.auth, self.contract)
        for camera in self.installation.cameras:
            self.frames[str(camera.id)] = Frame()
        LOGGER.info(
            "Found %d camera(s): %s",
            len(self.installation.cameras),
            ", ".join(f"{c.id} ({c.description})" for c in self.installation.cameras),
        )

    async def refresh(self, camera_id: str):
        """Fetch (optionally after requesting) a new image for a camera."""
        frame = self.frames[camera_id]
        try:
            if self.request_new:
                await self.installation.request_image(self.auth, camera_id)
                await asyncio.sleep(self.capture_delay)
            data = await self.installation.get_image(self.auth, camera_id)
        except (ProsegurException, ConnectionError, ConnectionRefusedError) as err:
            frame.error = repr(err)
            LOGGER.warning("Could not refresh camera %s: %r", camera_id, err)
            return

        if not data:
            frame.error = "empty image"
            return

        frame.data = data
        frame.updated = time.time()
        frame.error = None
        # wake up every stream waiting for a new image
        frame.event.set()
        frame.event = asyncio.Event()
        LOGGER.debug("Camera %s refreshed (%d bytes)", camera_id, len(data))

    async def _poll(self):
        while True:
            await asyncio.gather(*(self.refresh(cid) for cid in self.frames))
            await asyncio.sleep(self.refresh_interval)

    # ---------------------------------------------------------------------- hls

    def _ffmpeg_cmd(self, out_dir: str):
        fps = max(1, round(1 / self.frame_interval))
        return [
            self.ffmpeg, "-loglevel", "error", "-hide_banner",
            "-f", "image2pipe", "-framerate", str(fps), "-c:v", "mjpeg", "-i", "-",
            "-an", "-c:v", "libx264", "-preset", "veryfast", "-tune", "stillimage,zerolatency",
            "-pix_fmt", "yuv420p", "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
            "-g", str(2 * fps), "-keyint_min", str(2 * fps), "-sc_threshold", "0",
            "-f", "hls", "-hls_time", "2", "-hls_list_size", "5",
            "-hls_flags", "delete_segments+omit_endlist+independent_segments",
            os.path.join(out_dir, "index.m3u8"),
        ]

    async def _encode(self, camera_id: str):
        """Feed the latest frame of a camera to ffmpeg at a fixed frame rate."""
        out_dir = os.path.join(self.hls_dir, camera_id)
        os.makedirs(out_dir, exist_ok=True)
        while True:
            frame = self.frames[camera_id]
            if frame.data is None:
                await asyncio.sleep(1)
                continue
            proc = await asyncio.create_subprocess_exec(
                *self._ffmpeg_cmd(out_dir), stdin=asyncio.subprocess.PIPE
            )
            LOGGER.info("HLS encoder for camera %s started (pid %s)", camera_id, proc.pid)
            try:
                while proc.returncode is None:
                    proc.stdin.write(self.frames[camera_id].data)
                    await proc.stdin.drain()
                    await asyncio.sleep(self.frame_interval)
            except (BrokenPipeError, ConnectionResetError):
                pass
            except asyncio.CancelledError:
                proc.kill()
                await proc.wait()
                raise
            rc = await proc.wait()
            LOGGER.warning("HLS encoder for camera %s exited (%s), restarting", camera_id, rc)
            await asyncio.sleep(5)

    async def handle_hls(self, request: web.Request):
        """Serve HLS playlist and segments."""
        camera_id = request.match_info["camera"]
        name = request.match_info["file"]
        if not self.hls or camera_id not in self.frames:
            raise web.HTTPNotFound()
        if "/" in name or name.startswith(".") or not name.endswith((".m3u8", ".ts")):
            raise web.HTTPNotFound()
        path = os.path.join(self.hls_dir, camera_id, name)
        if not os.path.isfile(path):
            raise web.HTTPServiceUnavailable(text="HLS not ready yet", headers={"Retry-After": "5"})
        content_type = (
            "application/vnd.apple.mpegurl" if name.endswith(".m3u8") else "video/mp2t"
        )
        return web.FileResponse(path, headers={"Content-Type": content_type, "Cache-Control": "no-store"})

    # --------------------------------------------------------------------- http

    def _authorized(self, request: web.Request) -> bool:
        if not self.username:
            return True
        header = request.headers.get("Authorization", "")
        if not header.startswith("Basic "):
            return False
        try:
            user, _, pwd = base64.b64decode(header[6:]).decode().partition(":")
        except (binascii.Error, UnicodeDecodeError):
            return False
        return hmac.compare_digest(user, self.username) and hmac.compare_digest(
            pwd, self.password or ""
        )

    @web.middleware
    async def _auth_middleware(self, request: web.Request, handler):
        if not self._authorized(request):
            raise web.HTTPUnauthorized(
                headers={"WWW-Authenticate": 'Basic realm="pyprosegur"'}
            )
        return await handler(request)

    def _frame(self, request: web.Request) -> Frame:
        camera_id = request.match_info["camera"]
        if camera_id not in self.frames:
            raise web.HTTPNotFound(text=f"Unknown camera {camera_id}")
        frame = self.frames[camera_id]
        if frame.data is None:
            raise web.HTTPServiceUnavailable(
                text=f"No image yet for camera {camera_id}", headers={"Retry-After": "10"}
            )
        return frame

    async def handle_cameras(self, request: web.Request):
        """List cameras."""
        return web.json_response(
            [
                {
                    "id": str(c.id),
                    "description": c.description,
                    "snapshot": f"/camera/{c.id}/snapshot.jpg",
                    "stream": f"/camera/{c.id}/stream.mjpg",
                    "hls": f"/camera/{c.id}/hls/index.m3u8" if self.hls else None,
                }
                for c in self.installation.cameras
            ]
        )

    async def handle_health(self, request: web.Request):
        """Report freshness of every camera."""
        return web.json_response(
            {
                cid: {"updated": f.updated or None, "error": f.error}
                for cid, f in self.frames.items()
            }
        )

    async def handle_snapshot(self, request: web.Request):
        """Serve the latest image."""
        frame = self._frame(request)
        return web.Response(
            body=frame.data,
            content_type="image/jpeg",
            headers={"Cache-Control": "no-store"},
        )

    async def handle_stream(self, request: web.Request):
        """Serve an MJPEG stream repeating the latest image."""
        frame = self._frame(request)
        camera_id = request.match_info["camera"]

        response = web.StreamResponse(
            headers={
                "Content-Type": f"multipart/x-mixed-replace; boundary={BOUNDARY}",
                "Cache-Control": "no-store",
            }
        )
        await response.prepare(request)
        try:
            while True:
                frame = self.frames[camera_id]
                await response.write(
                    f"--{BOUNDARY}\r\nContent-Type: image/jpeg\r\n"
                    f"Content-Length: {len(frame.data)}\r\n\r\n".encode()
                    + frame.data
                    + b"\r\n"
                )
                try:
                    await asyncio.wait_for(frame.event.wait(), self.frame_interval)
                except asyncio.TimeoutError:
                    pass
        except ConnectionResetError:
            LOGGER.debug("Stream client for camera %s disconnected", camera_id)
        return response

    def app(self) -> web.Application:
        """Build the aiohttp application."""
        application = web.Application(middlewares=[self._auth_middleware])
        application.router.add_get("/cameras", self.handle_cameras)
        application.router.add_get("/health", self.handle_health)
        application.router.add_get("/camera/{camera}/snapshot.jpg", self.handle_snapshot)
        application.router.add_get("/camera/{camera}/stream.mjpg", self.handle_stream)
        application.router.add_get("/camera/{camera}/hls/{file}", self.handle_hls)
        return application

    async def run(self, host: str = "127.0.0.1", port: int = 8090):
        """Start polling and serve forever."""
        await self.setup()
        self._poller = asyncio.create_task(self._poll())
        if self.hls:
            if shutil.which(self.ffmpeg) is None:
                raise RuntimeError(f"HLS requested but '{self.ffmpeg}' was not found")
            self.hls_dir = tempfile.mkdtemp(prefix="pyprosegur-hls-")
            for camera_id in self.frames:
                self._encoders[camera_id] = asyncio.create_task(self._encode(camera_id))

        runner = web.AppRunner(self.app())
        await runner.setup()
        site = web.TCPSite(runner, host, port)
        await site.start()
        LOGGER.info("pyprosegur camera bridge listening on http://%s:%d", host, port)
        try:
            await asyncio.Event().wait()
        finally:
            self._poller.cancel()
            for task in self._encoders.values():
                task.cancel()
            await asyncio.gather(*self._encoders.values(), return_exceptions=True)
            await runner.cleanup()
            if self.hls_dir:
                shutil.rmtree(self.hls_dir, ignore_errors=True)
