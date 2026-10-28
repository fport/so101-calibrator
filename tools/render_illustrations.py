"""Render the SO-101 step animations shipped in so101_calibrator/assets/so101/ (dev tool, not packaged).

Needs MuJoCo and Pillow, and the official SO-101 model from github.com/TheRobotStudio/SO-ARM100
(Simulation/SO101/so101_new_calib.xml, whose zero pose is the calibration middle pose):

    python tools/render_illustrations.py /path/to/SO-ARM100/Simulation/SO101/so101_new_calib.xml

The joint moved in a clip is drawn orange. Direction clips are checked with forward kinematics so the
motion matches the wording of profiles.DIRECTION_PROMPTS.
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import mujoco
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from so101_calibrator.profiles import JOINTS  # noqa: E402

# The package ships small clips (a terminal shows ~48 x 48 pixels); the README uses the large ones.
OUTPUTS = {ROOT / "src/so101_calibrator/assets/so101": 128, ROOT / "docs/so101": 256}
SIZE = 256  # rendered at 3x this, cropped to the clip's bounding box
FPS = 12
JOINT_BODY = {"shoulder_pan": "shoulder", "shoulder_lift": "upper_arm", "elbow_flex": "lower_arm",
              "wrist_flex": "wrist", "wrist_roll": "gripper", "gripper": "moving_jaw_so101_v1"}
PART = (0.90, 0.89, 0.86, 1)
MOTOR = (0.16, 0.16, 0.18, 1)
HIGHLIGHT = (1.00, 0.55, 0.10, 1)
HIGHLIGHT_MOTOR = (0.70, 0.33, 0.02, 1)
OK = (0.20, 0.75, 0.35, 1)


class Scene:
    def __init__(self, xml: Path):
        self.model = mujoco.MjModel.from_xml_path(str(xml))
        self.data = mujoco.MjData(self.model)
        m = self.model
        m.vis.headlight.ambient[:] = 0.45
        m.vis.headlight.diffuse[:] = 0.55
        m.vis.headlight.specular[:] = 0.1
        m.vis.global_.offwidth = m.vis.global_.offheight = SIZE * 3
        self.renderer = mujoco.Renderer(m, SIZE * 3, SIZE * 3)
        self.opt = mujoco.MjvOption()
        self.opt.geomgroup[:] = 0
        self.opt.geomgroup[2] = 1  # visual meshes only
        self.cam = mujoco.MjvCamera()
        self.wide()
        self.jid = {name: mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_JOINT, str(i + 1)) for i, name in enumerate(JOINTS)}
        self.bid = {name: mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_BODY, b) for name, b in JOINT_BODY.items()}
        self.motor_geom = np.array([m.geom_dataid[g] >= 0 and "sts3215" in mujoco.mj_id2name(
            m, mujoco.mjtObj.mjOBJ_MESH, m.geom_dataid[g]) for g in range(m.ngeom)])
        m.geom_matid[:] = -1

    def wide(self) -> None:
        self.cam.lookat[:] = (0.0, 0.0, 0.17)
        self.cam.distance = 1.1
        self.cam.azimuth = 135
        self.cam.elevation = -20

    def close_up(self) -> None:
        """Camera on the gripper, for wrist_roll and gripper clips."""
        self.pose({})
        self.cam.lookat[:] = self.body_pos("gripper")
        self.cam.distance = 0.32
        self.cam.azimuth = 200
        self.cam.elevation = -25

    def range(self, joint: str) -> tuple[float, float]:
        lo, hi = self.model.jnt_range[self.jid[joint]]
        return float(lo), float(hi)

    def pose(self, q: dict[str, float]) -> None:
        self.data.qpos[:] = 0
        for j, v in q.items():
            self.data.qpos[self.model.jnt_qposadr[self.jid[j]]] = v
        mujoco.mj_forward(self.model, self.data)

    def colour(self, highlight: dict[str, tuple] | None = None) -> None:
        m = self.model
        for g in range(m.ngeom):
            m.geom_rgba[g] = MOTOR if self.motor_geom[g] else PART
        for joint, rgba in (highlight or {}).items():
            body = self.bid[joint]
            for g in np.flatnonzero(m.geom_bodyid == body):
                m.geom_rgba[g] = tuple(c * (0.7 if self.motor_geom[g] else 1) for c in rgba[:3]) + (1,)

    def frame(self, q: dict[str, float], highlight: dict[str, tuple] | None = None) -> Image.Image:
        self.pose(q)
        self.colour(highlight)
        r = self.renderer
        r.update_scene(self.data, camera=self.cam, scene_option=self.opt)
        rgb = r.render().copy()
        r.enable_segmentation_rendering()
        r.update_scene(self.data, camera=self.cam, scene_option=self.opt)
        seg = r.render()[:, :, 0]
        r.disable_segmentation_rendering()
        alpha = np.where(seg >= 0, 255, 0).astype(np.uint8)
        return Image.fromarray(np.dstack([rgb, alpha]), "RGBA")

    # forward-kinematics probes used to pick the direction that matches the prompt text
    def body_pos(self, body: str) -> np.ndarray:
        return self.data.xpos[mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, body)].copy()

    def positive_matches_prompt(self, joint: str) -> bool:
        def probe(v):
            self.pose({joint: v})
            jaw = self.body_pos("moving_jaw_so101_v1")
            return {
                "shoulder_pan": math.atan2(jaw[1], jaw[0]),  # CCW from above = yaw up
                "shoulder_lift": self.body_pos("lower_arm")[2],  # upper arm up
                "elbow_flex": np.linalg.norm(self.body_pos("wrist") - self.body_pos("upper_arm")),  # unfold
                "wrist_flex": jaw[2] - self.body_pos("wrist")[2],  # wrist tilts up
            }.get(joint)

        if joint == "gripper":
            return True  # joint 6: 0 = closed, positive = open
        if joint == "wrist_roll":  # clockwise seen from behind the gripper = positive about the approach axis
            self.pose({})
            approach = self.body_pos("moving_jaw_so101_v1") - self.body_pos("wrist")
            return float(np.dot(self.data.xaxis[self.jid[joint]], approach)) > 0
        return probe(0.2) > probe(0.0)


def ease(t: float) -> float:
    return 0.5 - 0.5 * math.cos(math.pi * max(0.0, min(1.0, t)))


def ramp(a: dict, b: dict, n: int) -> list[dict]:
    keys = set(a) | set(b)
    return [{k: a.get(k, 0) + (b.get(k, 0) - a.get(k, 0)) * ease(i / (n - 1)) for k in keys} for i in range(n)]


def clips(sc: Scene) -> dict[str, list[Image.Image]]:
    out: dict[str, list[Image.Image]] = {}
    hold = lambda q, n, hl=None: [sc.frame(q, hl)] * n  # noqa: E731

    # idle: a slow breathing motion, nothing highlighted
    n = 48
    out["idle"] = [sc.frame({"shoulder_pan": 0.25 * math.sin(2 * math.pi * i / n),
                             "shoulder_lift": -0.15 + 0.1 * math.sin(4 * math.pi * i / n),
                             "wrist_flex": 0.2 * math.sin(2 * math.pi * i / n + 1)}) for i in range(n)]

    # middle pose: from a random pose to zero, all joints highlighted on arrival
    start = {"shoulder_pan": 0.6, "shoulder_lift": -0.9, "elbow_flex": 0.9, "wrist_flex": -0.7, "wrist_roll": 0.8,
             "gripper": 1.2}
    out["middle"] = ([sc.frame(q) for q in ramp(start, {}, 22)] + hold({}, 12, {j: OK for j in JOINTS}))

    # ranges: each joint except wrist_roll to both stops and back
    frames = []
    for j in JOINTS:
        if j == "wrist_roll":
            continue
        lo, hi = sc.range(j)
        lo, hi = 0.85 * lo, 0.85 * hi
        hl = {j: HIGHLIGHT}
        for a, b in (({}, {j: lo}), ({j: lo}, {j: hi}), ({j: hi}, {})):
            frames += [sc.frame(q, hl) for q in ramp(a, b, 9 if a and b else 7)]
    out["ranges"] = frames

    # directions: a small move in the direction the prompt describes, twice
    for j in JOINTS:
        sc.close_up() if j in ("wrist_roll", "gripper") else sc.wide()
        sign = 1 if sc.positive_matches_prompt(j) else -1
        amp = 0.9 if j == "gripper" else 0.45
        base = {}
        hl = {j: HIGHLIGHT}
        seq = ramp(base, {j: sign * amp}, 10) + [{j: sign * amp}] * 4 + ramp({j: sign * amp}, base, 8) + [base] * 3
        out[f"direction_{j}"] = [sc.frame(q, hl) for q in seq * 2]
        print(f"{j}: positive MuJoCo direction {'matches' if sign > 0 else 'is opposite to'} the prompt")

    sc.wide()
    # motor scan: ids 1..6 light up in order
    frames = []
    for k in range(len(JOINTS) + 1):
        frames += hold({}, 4, {j: OK for j in JOINTS[:k]})
    out["scan"] = frames + hold({}, 8, {j: OK for j in JOINTS})

    # teleop: a smooth multi-joint motion
    n = 60
    out["teleop"] = [sc.frame({"shoulder_pan": 0.5 * math.sin(2 * math.pi * i / n),
                               "shoulder_lift": -0.3 + 0.3 * math.sin(2 * math.pi * i / n + 1),
                               "elbow_flex": 0.4 * math.sin(2 * math.pi * i / n + 2),
                               "wrist_flex": 0.3 * math.sin(4 * math.pi * i / n),
                               "gripper": 0.6 + 0.6 * math.sin(2 * math.pi * i / n)}, {j: HIGHLIGHT for j in JOINTS})
                     for i in range(n)]
    return out


def fit(frames: list[Image.Image]) -> list[Image.Image]:
    """Crop every frame to the union bounding box of the clip (square, with margin), then scale to SIZE."""

    boxes = [f.getchannel("A").getbbox() for f in frames]
    boxes = [b for b in boxes if b]
    x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
    x1, y1 = max(b[2] for b in boxes), max(b[3] for b in boxes)
    side = int(max(x1 - x0, y1 - y0) * 1.08)
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
    box = (cx - side // 2, cy - side // 2, cx - side // 2 + side, cy - side // 2 + side)
    return [f.crop(box).resize((SIZE, SIZE), Image.LANCZOS) for f in frames]


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("model", type=Path, help="SO-ARM100/Simulation/SO101/so101_new_calib.xml")
    args = p.parse_args()
    sc = Scene(args.model)
    for out in OUTPUTS:
        out.mkdir(parents=True, exist_ok=True)
        for path in out.glob("*.webp"):
            path.unlink()
    for name, frames in clips(sc).items():
        frames = fit(frames)
        for out, size in OUTPUTS.items():
            scaled = [f.resize((size, size), Image.LANCZOS) for f in frames]
            path = out / f"{name}.webp"
            scaled[0].save(path, save_all=True, append_images=scaled[1:], duration=int(1000 / FPS), loop=0,
                           quality=72, method=6)
            print(f"{path.relative_to(ROOT)}  {len(frames)} frames  {path.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
