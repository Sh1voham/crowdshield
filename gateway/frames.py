"""Still frames and MJPEG video for calibration and "reality lock".

Never opens the camera device PipeGen is using. Frame sources, in order:
  upload    the last image an operator uploaded on /calibrate
  vms       PipeGen VMS mode MJPEG (run.vms.sh serves tcp://127.0.0.1:7010)
  video     the video file the pipeline plays (cv2, seeking to the run's elapsed time)
  rtsp      an RTSP URL (cv2)
  image     a still image path (e.g. a synthetic frame.png)
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from pathlib import Path
from typing import AsyncIterator, Optional

log = logging.getLogger("gateway.frames")

try:
    import cv2  # type: ignore
    import numpy as np  # type: ignore
except ImportError:  # pragma: no cover - the gateway still runs without OpenCV
    cv2 = None
    np = None


def _encode(img, width: int = 960, quality: int = 80) -> Optional[bytes]:
    if cv2 is None or img is None:
        return None
    h, w = img.shape[:2]
    if w > width:
        img = cv2.resize(img, (width, int(h * width / w)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return buf.tobytes() if ok else None


class FrameSource:
    def __init__(self, cfg: dict, data_dir: Path, repo_root: Path):
        self.cfg = cfg
        self.data_dir = data_dir
        self.root = repo_root
        self.upload_path = data_dir / "uploaded_frame.jpg"
        self._cap_lock = threading.Lock()

    # -- configuration helpers ----------------------------------------------------------
    def _video_path(self) -> Optional[Path]:
        v = self.cfg.get("video")
        if not v:
            return None
        p = Path(v).expanduser()
        return p if p.exists() else None

    def video_start_wall(self) -> Optional[float]:
        """When the PipeGen video run started (written by scripts/pipegen_ctl.sh)."""
        stamp = self.root / ".cs_run" / "pipegen_started_at"
        try:
            return float(stamp.read_text().strip())
        except (OSError, ValueError):
            return None

    def describe(self) -> dict:
        return {"upload": self.upload_path.exists(), "video": str(self._video_path() or "") or None,
                "rtsp": self.cfg.get("rtsp"), "image": self.cfg.get("image"),
                "vms": self.cfg.get("vms_mjpeg"), "opencv": cv2 is not None}

    # -- still frames ---------------------------------------------------------------------
    def save_upload(self, data: bytes) -> None:
        if cv2 is not None:
            img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
            if img is None:
                raise ValueError("not an image")
            ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 92])
            data = buf.tobytes()
        elif not (data[:3] == b"\xff\xd8\xff"):
            raise ValueError("JPEG required when OpenCV is not installed")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.upload_path.write_bytes(data)

    def _read_video_frame(self, path: Path, t: Optional[float]):
        with self._cap_lock:
            cap = cv2.VideoCapture(str(path))
            try:
                if t is not None:
                    cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, t) * 1000.0)
                ok, img = cap.read()
                return img if ok else None
            finally:
                cap.release()

    def still(self, source: str = "auto", t: Optional[float] = None, full: bool = True) -> Optional[bytes]:
        order = [source] if source != "auto" else ["upload", "video", "rtsp", "image"]
        width = 4096 if full else 960
        for s in order:
            try:
                if s == "upload" and self.upload_path.exists():
                    if cv2 is None:
                        return self.upload_path.read_bytes()
                    return _encode(cv2.imread(str(self.upload_path)), width, 92)
                if cv2 is None:
                    continue
                if s == "video" and self._video_path():
                    img = self._read_video_frame(self._video_path(), t if t is not None else 1.0)
                    if img is not None:
                        return _encode(img, width, 92)
                if s == "rtsp" and self.cfg.get("rtsp"):
                    cap = cv2.VideoCapture(self.cfg["rtsp"])
                    ok, img = cap.read()
                    cap.release()
                    if ok:
                        return _encode(img, width, 92)
                if s == "image" and self.cfg.get("image"):
                    p = (self.root / self.cfg["image"]) if not Path(self.cfg["image"]).is_absolute() else Path(self.cfg["image"])
                    if p.exists():
                        return _encode(cv2.imread(str(p)), width, 92)
            except Exception as e:  # noqa: BLE001
                log.warning("frame source %s failed: %s", s, e)
        return None

    # -- MJPEG ----------------------------------------------------------------------------
    async def mjpeg(self, fps: float = 8.0, stream_t=None) -> AsyncIterator[bytes]:
        """Yield JPEG frames: PipeGen VMS MJPEG if reachable, else the video file, else the still
        frame every 2 s. `stream_t()` returns the engine's latest frame time (s since the first
        frame) or None; when given, the video follows the analysed frame exactly."""
        vms = self.cfg.get("vms_mjpeg")  # "127.0.0.1:7010"
        if vms:
            host, _, port = vms.partition(":")
            try:
                reader, writer = await asyncio.wait_for(asyncio.open_connection(host, int(port or 7010)), 1.5)
                log.info("mjpeg: relaying PipeGen VMS video from %s", vms)
                try:
                    async for jpg in _split_multipart_jpegs(reader):
                        yield jpg
                finally:
                    writer.close()
                return
            except (OSError, asyncio.TimeoutError):
                pass
        path = self._video_path()
        if cv2 is not None and path:
            loop = asyncio.get_running_loop()
            cap = cv2.VideoCapture(str(path))
            vfps = cap.get(cv2.CAP_PROP_FPS) or 25.0
            nframes = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
            duration = nframes / vfps if nframes else None
            start = self.video_start_wall() or time.time()
            pos = -1  # index of the frame last decoded
            pts0 = float(self.cfg.get("video_offset_s", 0.0))  # engine t=0 is video frame 0

            def frame_at(target: int):
                """Decode frame `target`: read forward when close, seek otherwise (seeks are slow)."""
                nonlocal pos
                if target <= pos or target - pos > 50:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, target))
                    pos = target - 1
                img = None
                while pos < target:
                    ok, img = cap.read()
                    if not ok:
                        return None
                    pos += 1
                return img

            try:
                while True:
                    t = stream_t() if stream_t else None
                    if t is None:
                        t = time.time() - start  # no analysed frame yet: pace by wall clock
                    if duration:
                        t %= duration
                    img = await loop.run_in_executor(None, frame_at, int(round((t + pts0) * vfps)))
                    if img is None:
                        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        pos = -1
                        await asyncio.sleep(0.05)
                        continue
                    jpg = await loop.run_in_executor(None, _encode, img, 960, 75)
                    if jpg:
                        yield jpg
                    await asyncio.sleep(1.0 / fps)
            finally:
                cap.release()
        while True:
            jpg = self.still("auto", full=False)
            if jpg is None:
                return
            yield jpg
            await asyncio.sleep(2.0)


async def _split_multipart_jpegs(reader: asyncio.StreamReader) -> AsyncIterator[bytes]:
    """Extract JPEG images from a raw multipart MJPEG byte stream (SOI/EOI markers)."""
    buf = b""
    while True:
        chunk = await reader.read(65536)
        if not chunk:
            return
        buf += chunk
        while True:
            s = buf.find(b"\xff\xd8")
            if s < 0:
                buf = buf[-2:]
                break
            e = buf.find(b"\xff\xd9", s + 2)
            if e < 0:
                buf = buf[s:]
                break
            yield buf[s:e + 2]
            buf = buf[e + 2:]
        if len(buf) > 8 * 1024 * 1024:
            buf = b""
