"""Gemini 336 capture through the OrbbecSDK v2 Python binding."""
import time
import numpy as np


_COLOR_FORMAT_NAMES = ("RGB", "BGR", "MJPG", "YUYV", "UYVY", "NV12", "NV21", "I420")


def _video_profiles(profile_list):
    profiles = []
    for index in range(profile_list.get_count()):
        try:
            profiles.append(profile_list.get_stream_profile_by_index(index).as_video_stream_profile())
        except Exception:
            continue
    return profiles


def _select_color_profile(profile_list, width, height, fps, ob):
    profiles = _video_profiles(profile_list)
    preferred = [getattr(ob.OBFormat, name) for name in _COLOR_FORMAT_NAMES
                 if hasattr(ob.OBFormat, name)]
    rank = {fmt: index for index, fmt in enumerate(preferred)}
    exact = [p for p in profiles if p.get_width() == width and p.get_height() == height
             and p.get_fps() == fps and p.get_format() in rank]
    if exact:
        return min(exact, key=lambda p: rank[p.get_format()])
    supported = [f"{p.get_width()}x{p.get_height()}@{p.get_fps()} {p.get_format()}"
                 for p in profiles if p.get_format() in rank]
    raise RuntimeError(
        f"Gemini 336 has no supported color profile {width}x{height}@{fps}. "
        f"Available: {', '.join(supported)}"
    )


def _frame_to_bgr(frame, ob):
    import cv2
    width, height = frame.get_width(), frame.get_height()
    fmt = frame.get_format()
    data = np.frombuffer(frame.get_data(), dtype=np.uint8)
    if fmt == ob.OBFormat.RGB:
        return cv2.cvtColor(data.reshape(height, width, 3), cv2.COLOR_RGB2BGR)
    if hasattr(ob.OBFormat, "BGR") and fmt == ob.OBFormat.BGR:
        return data.reshape(height, width, 3).copy()
    if fmt == ob.OBFormat.MJPG:
        image = cv2.imdecode(data, cv2.IMREAD_COLOR)
        if image is None:
            raise RuntimeError("Could not decode MJPG color frame")
        return image
    if fmt == ob.OBFormat.YUYV:
        return cv2.cvtColor(data.reshape(height, width, 2), cv2.COLOR_YUV2BGR_YUYV)
    if hasattr(ob.OBFormat, "UYVY") and fmt == ob.OBFormat.UYVY:
        return cv2.cvtColor(data.reshape(height, width, 2), cv2.COLOR_YUV2BGR_UYVY)
    if hasattr(ob.OBFormat, "I420") and fmt == ob.OBFormat.I420:
        return cv2.cvtColor(data.reshape(height * 3 // 2, width), cv2.COLOR_YUV2BGR_I420)
    if hasattr(ob.OBFormat, "NV12") and fmt == ob.OBFormat.NV12:
        return cv2.cvtColor(data.reshape(height * 3 // 2, width), cv2.COLOR_YUV2BGR_NV12)
    if hasattr(ob.OBFormat, "NV21") and fmt == ob.OBFormat.NV21:
        return cv2.cvtColor(data.reshape(height * 3 // 2, width), cv2.COLOR_YUV2BGR_NV21)
    raise RuntimeError(f"Unsupported Gemini 336 color format: {fmt}")


def _distortion_for_opencv(dist):
    model = str(dist.model).upper()
    if "NONE" in model:
        return [0.0] * 8, model
    # OpenCV accepts unmodified Brown-Conrady coefficients in this order.
    if "BROWN" not in model or "MODIFIED" in model or "INVERSE" in model:
        raise RuntimeError(f"Unsupported distortion model for OpenCV solvePnP: {model}")
    return [dist.k1, dist.k2, dist.p1, dist.p2,
            dist.k3, dist.k4, dist.k5, dist.k6], model


class GeminiCamera:
    def __init__(self, config):
        self.config = config
        self.pipeline = None

    def __enter__(self):
        try:
            import pyorbbecsdk as ob
            from pyorbbecsdk import Pipeline
        except (ImportError, AttributeError) as exc:
            raise RuntimeError(
                "pyorbbecsdk native module is unavailable. Cloning the source is not enough; "
                "install pyorbbecsdk2 or build/install D:/Robotics/xarm-shop-demo/pyorbbecsdk."
            ) from exc
        self.ob = ob
        ctx = ob.Context()
        devices = ctx.query_devices()
        if devices.get_count() == 0:
            raise RuntimeError("No Orbbec camera detected")
        serial = self.config.get("serial")
        if serial:
            device = devices.get_device_by_serial_number(serial)
        elif devices.get_count() == 1:
            device = devices.get_device_by_index(0)
        else:
            raise RuntimeError("Connect exactly one camera or configure camera.serial")
        if device is None:
            raise RuntimeError(f"Orbbec camera serial not found: {serial}")

        info = device.get_device_info()
        self.serial = info.get_serial_number()
        self.model = info.get_name()
        expected = self.config.get("expected_model", "Gemini 336")
        if expected.lower() not in self.model.lower():
            raise RuntimeError(f"Expected {expected}, connected device is {self.model}")

        self.pipeline = Pipeline(device)
        config = ob.Config()
        color_profiles = self.pipeline.get_stream_profile_list(ob.OBSensorType.COLOR_SENSOR)
        color = _select_color_profile(color_profiles, self.config["width"],
                                      self.config["height"], self.config["fps"], ob)
        depth_profiles = self.pipeline.get_stream_profile_list(ob.OBSensorType.DEPTH_SENSOR)
        depth = depth_profiles.get_default_video_stream_profile()
        config.enable_stream(color)
        config.enable_stream(depth)
        config.set_frame_aggregate_output_mode(ob.OBFrameAggregateOutputMode.FULL_FRAME_REQUIRE)
        self.color_profile = {
            "width": color.get_width(), "height": color.get_height(),
            "fps": color.get_fps(), "format": str(color.get_format())
        }
        self.depth_profile = {
            "width": depth.get_width(), "height": depth.get_height(),
            "fps": depth.get_fps(), "format": str(depth.get_format())
        }
        self.align = ob.AlignFilter(align_to_stream=ob.OBStreamType.COLOR_STREAM)
        self.pipeline.enable_frame_sync()
        self.pipeline.start(config)
        return self

    def capture(self):
        deadline = time.monotonic() + self.config.get("timeout_s", 10)
        count = 0
        while time.monotonic() < deadline:
            frames = self.pipeline.wait_for_frames(1000)
            if frames is None:
                continue
            aligned = self.align.process(frames)
            if aligned is None:
                continue
            aligned = aligned.as_frame_set()
            color_frame = aligned.get_color_frame()
            depth_frame = aligned.get_depth_frame()
            if color_frame is None or depth_frame is None:
                continue
            count += 1
            if count <= self.config.get("warmup_frames", 15):
                continue
            if depth_frame.get_format() != self.ob.OBFormat.Y16:
                raise RuntimeError(f"Expected Y16 depth, got {depth_frame.get_format()}")
            bgr = _frame_to_bgr(color_frame, self.ob)
            try:
                raw_depth = np.frombuffer(depth_frame.get_data(), dtype=np.uint16).reshape(
                    depth_frame.get_height(), depth_frame.get_width())
            except ValueError as exc:
                raise RuntimeError("Invalid Y16 depth buffer size") from exc
            depth_mm = raw_depth.astype(np.float32) * float(depth_frame.get_depth_scale())
            if depth_mm.shape != bgr.shape[:2]:
                raise RuntimeError(
                    f"D2C alignment mismatch: color={bgr.shape[1]}x{bgr.shape[0]}, "
                    f"depth={depth_mm.shape[1]}x{depth_mm.shape[0]}"
                )
            profile = color_frame.get_stream_profile().as_video_stream_profile()
            intr = profile.get_intrinsic()
            distortion, distortion_model = _distortion_for_opencv(profile.get_distortion())
            if min(intr.fx, intr.fy) <= 0:
                raise RuntimeError("Invalid color camera intrinsics")
            return {
                "bgr": bgr, "depth": depth_mm,
                "intrinsics": [intr.fx, intr.fy, intr.cx, intr.cy],
                "distortion": distortion, "distortion_model": distortion_model,
                "serial": self.serial, "model": self.model,
                "color_profile": self.color_profile, "depth_profile": self.depth_profile,
                "aligned_size": [bgr.shape[1], bgr.shape[0]],
                "depth_scale_mm": float(depth_frame.get_depth_scale()),
            }
        raise TimeoutError("No synchronized D2C-aligned RGB-D frames")

    def __exit__(self, *args):
        if self.pipeline is not None:
            self.pipeline.stop()
