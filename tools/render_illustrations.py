"""Render the SO-101 pixel-art clips shipped in so101_calibrator/assets/so101/ (dev tool, not packaged).

Needs MuJoCo, numpy and Pillow, and the official SO-101 model from github.com/TheRobotStudio/SO-ARM100
(Simulation/SO101/so101_new_calib.xml, whose zero pose is the calibration middle pose):

    python tools/render_illustrations.py /path/to/SO-ARM100/Simulation/SO101/so101_new_calib.xml

How a sprite is made: MuJoCo's segmentation render says, for every pixel, which body and which mesh
(printed part or STS3215 motor) it shows. Each block of pixels is reduced to its majority role, which
gives clean pixel-art edges instead of blurred downscaling; then a dot pattern, separators between links
and a 1-pixel outline are added. The result stores *roles*, not colours (see `assets.ROLES`): the UI
theme colours them at run time. Each clip is one paletted PNG sheet (frames stacked vertically) per size,
plus a preview GIF (size l) in docs/so101/ for the README.

Direction clips are checked with forward kinematics so the motion matches profiles.DIRECTION_PROMPTS.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import mujoco
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from so101_calibrator.assets import ROLES  # noqa: E402
from so101_calibrator.profiles import JOINTS  # noqa: E402
from so101_calibrator.theme import GRAPHITE  # noqa: E402

PACKAGE = ROOT / "src/so101_calibrator/assets/so101"
DOCS = ROOT / "docs/so101"
RENDER = 720  # square render size; a sprite pixel is a block x block square of it
# Each size has a pixel box the clip must fit (a terminal cell shows 1 x 2 pixels) and the finest block
# allowed; clips with wide motions get a coarser block, i.e. a smaller arm, instead of overflowing.
SIZES = {"xl": ((100, 66), 5), "l": ((66, 44), 7), "s": ((44, 30), 11)}
FPS = 12
BODY = {"shoulder_pan": "shoulder", "shoulder_lift": "upper_arm", "elbow_flex": "lower_arm",
        "wrist_flex": "wrist", "wrist_roll": "gripper", "gripper": "moving_jaw_so101_v1"}
R = {name: i for i, name in enumerate(ROLES)}
# fraction of each joint's range the "ranges" clip sweeps: big swings of the shoulder and elbow would make
# the clip tall and the arm tiny
RANGE_SHOWN = {"shoulder_pan": 0.5, "shoulder_lift": 0.2, "elbow_flex": 0.22, "wrist_flex": 0.45, "gripper": 0.7}


class Scene:
    def __init__(self, xml: Path):
        m = self.model = mujoco.MjModel.from_xml_path(str(xml))
        self.data = mujoco.MjData(m)
        m.vis.global_.offwidth = m.vis.global_.offheight = RENDER
        self.renderer = mujoco.Renderer(m, RENDER, RENDER)
        self.opt = mujoco.MjvOption()
        self.opt.geomgroup[:] = 0
        self.opt.geomgroup[2] = 1  # visual meshes only
        self.jid = {n: mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_JOINT, str(i + 1)) for i, n in enumerate(JOINTS)}
        self.bid = {n: mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_BODY, b) for n, b in BODY.items()}
        self.motor = np.array([m.geom_dataid[g] >= 0 and "sts3215" in mujoco.mj_id2name(
            m, mujoco.mjtObj.mjOBJ_MESH, m.geom_dataid[g]) for g in range(m.ngeom)])
        self.cam = mujoco.MjvCamera()
        self.side()

    # --- cameras ------------------------------------------------------------------------------------
    def side(self) -> None:
        """Three-quarter side view: the familiar SO-101 profile."""
        self.cam.lookat[:] = (0.0, 0.0, 0.16)
        self.cam.distance, self.cam.azimuth, self.cam.elevation = 0.85, 135, -15

    def close_up(self) -> None:
        """On the gripper, for wrist_roll and gripper clips."""
        self.pose({})
        self.cam.lookat[:] = self.body_pos("gripper")
        self.cam.distance, self.cam.azimuth, self.cam.elevation = 0.36, 135, -15

    # --- kinematics ---------------------------------------------------------------------------------
    def pose(self, q: dict[str, float]) -> None:
        self.data.qpos[:] = 0
        for j, v in q.items():
            self.data.qpos[self.model.jnt_qposadr[self.jid[j]]] = v
        mujoco.mj_forward(self.model, self.data)

    def body_pos(self, body: str) -> np.ndarray:
        return self.data.xpos[mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, body)].copy()

    def range(self, joint: str) -> tuple[float, float]:
        lo, hi = self.model.jnt_range[self.jid[joint]]
        return float(lo), float(hi)

    def positive_matches_prompt(self, joint: str) -> bool:
        """Does a positive MuJoCo angle move the joint the way DIRECTION_PROMPTS describes?"""
        def probe(v):
            self.pose({joint: v})
            jaw = self.body_pos("moving_jaw_so101_v1")
            return {
                "shoulder_pan": math.atan2(jaw[1], jaw[0]),  # counter-clockwise seen from above
                "shoulder_lift": self.body_pos("lower_arm")[2],  # upper arm up
                "elbow_flex": np.linalg.norm(self.body_pos("wrist") - self.body_pos("upper_arm")),  # unfold
                "wrist_flex": jaw[2] - self.body_pos("wrist")[2],  # wrist tilts up
            }[joint]

        if joint == "gripper":
            return True  # 0 = closed, positive = open
        if joint == "wrist_roll":  # clockwise seen from behind the gripper = positive about the approach axis
            self.pose({})
            approach = self.body_pos("moving_jaw_so101_v1") - self.body_pos("wrist")
            return float(np.dot(self.data.xaxis[self.jid[joint]], approach)) > 0
        return probe(0.2) > probe(0.0)

    # --- rendering ----------------------------------------------------------------------------------
    def segment(self, q: dict[str, float], highlight: set[str]) -> tuple[np.ndarray, np.ndarray]:
        """Full-resolution (role, body) maps: role 0 empty, 1 part, 2 motor (+2 when highlighted)."""
        self.pose(q)
        r = self.renderer
        r.enable_segmentation_rendering()
        r.update_scene(self.data, camera=self.cam, scene_option=self.opt)
        seg = r.render()[:, :, 0].copy()
        r.disable_segmentation_rendering()
        role = np.zeros(seg.shape, np.uint8)
        body = np.full(seg.shape, -1, np.int32)
        hit = seg >= 0
        g = seg[hit]
        body[hit] = self.model.geom_bodyid[g]
        role[hit] = np.where(self.motor[g], 2, 1)
        for joint in highlight:
            sel = hit & (body == self.bid[joint])
            role[sel] += 2
        return role, body


def reduce(role: np.ndarray, body: np.ndarray, block: int) -> tuple[np.ndarray, np.ndarray]:
    """Majority vote per block (background only wins when it covers most of the block)."""
    h, w = role.shape[0] // block, role.shape[1] // block
    rb = role[:h * block, :w * block].reshape(h, block, w, block).swapaxes(1, 2).reshape(h, w, -1)
    bb = body[:h * block, :w * block].reshape(h, block, w, block).swapaxes(1, 2).reshape(h, w, -1)
    out_r = np.zeros((h, w), np.uint8)
    out_b = np.full((h, w), -1, np.int32)
    for y in range(h):
        for x in range(w):
            fg = rb[y, x][rb[y, x] > 0]
            if fg.size < rb.shape[2] * 0.42:
                continue
            counts = np.bincount(fg, minlength=5)
            # a highlighted part keeps thin links visible: it wins when it covers a third of the block
            out_r[y, x] = 3 if counts[3] + counts[4] >= fg.size / 3 and counts[3] >= counts[4] else counts.argmax()
            bodies = bb[y, x][bb[y, x] >= 0]
            out_b[y, x] = np.bincount(bodies).argmax()
    for y in range(1, h - 1):  # drop lone pixels, fill one-pixel holes
        for x in range(1, w - 1):
            n = sum(int(out_r[yy, xx] > 0) for yy, xx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)))
            if out_r[y, x] and n == 0:
                out_r[y, x] = 0
            elif not out_r[y, x] and n == 4:
                out_r[y, x], out_b[y, x] = out_r[y, x - 1], out_b[y, x - 1]
    return out_r, out_b


def paint(r: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Role map -> sprite indices (see assets.ROLES): fills with a dot pattern, separators, outline."""
    h, w = r.shape
    fill = {1: R["part"], 2: R["motor"], 3: R["hi"], 4: R["hi_motor"]}
    dot = {1: R["part_dot"], 2: R["motor_dot"], 3: R["hi_dot"], 4: R["hi_motor_dot"]}
    out = np.zeros((h + 2, w + 2), np.uint8)
    for y in range(h):
        for x in range(w):
            role = r[y, x]
            if not role:
                continue
            idx = dot[role] if x % 2 == 0 and y % 2 == 0 else fill[role]
            for dx, dy in ((1, 0), (0, 1)):  # separator where the next pixel belongs to another link
                xx, yy = x + dx, y + dy
                if xx < w and yy < h and r[yy, xx] and b[yy, xx] != b[y, x]:
                    idx = R["separator"]
            out[y + 1, x + 1] = idx
    solid = out > 0
    ring = np.zeros_like(solid)
    ring[1:, :] |= solid[:-1, :]
    ring[:-1, :] |= solid[1:, :]
    ring[:, 1:] |= solid[:, :-1]
    ring[:, :-1] |= solid[:, 1:]
    out[ring & ~solid] = R["outline"]
    return out


def ease(t: float) -> float:
    return 0.5 - 0.5 * math.cos(math.pi * max(0.0, min(1.0, t)))


def ramp(a: dict, b: dict, n: int) -> list[dict]:
    keys = set(a) | set(b)
    return [{k: a.get(k, 0) + (b.get(k, 0) - a.get(k, 0)) * ease(i / (n - 1)) for k in keys} for i in range(n)]


def clips(sc: Scene) -> dict[str, tuple[str, list[tuple[dict, set]]]]:
    """name -> (camera, [(pose, highlighted joints), ...])"""
    out: dict[str, tuple[str, list[tuple[dict, set]]]] = {}
    n = 36
    out["idle"] = ("side", [({"shoulder_pan": 0.22 * math.sin(2 * math.pi * i / n),
                              "wrist_flex": 0.18 * math.sin(2 * math.pi * i / n + 1)}, set()) for i in range(n)])

    start = {"shoulder_pan": 0.5, "shoulder_lift": -0.7, "elbow_flex": 0.8, "wrist_flex": -0.6, "gripper": 1.1}
    out["middle"] = ("side", [(q, set()) for q in ramp(start, {}, 20)] + [({}, set(JOINTS))] * 14)

    seq = []
    for j in JOINTS:
        if j == "wrist_roll":
            continue
        lo, hi = sc.range(j)
        k = RANGE_SHOWN[j]  # enough to show the sweep, small enough to keep the arm large
        lo, hi = k * lo, k * hi
        for a, b in (({}, {j: lo}), ({j: lo}, {j: hi}), ({j: hi}, {})):
            seq += [(q, {j}) for q in ramp(a, b, 8)]
    out["ranges"] = ("side", seq)

    for j in JOINTS:
        sign = 1 if sc.positive_matches_prompt(j) else -1
        amp = sign * (0.9 if j == "gripper" else 0.45)
        moves = ramp({}, {j: amp}, 9) + [{j: amp}] * 4 + ramp({j: amp}, {}, 7) + [{}] * 3
        cam = "close" if j in ("wrist_roll", "gripper") else "side"
        out[f"direction_{j}"] = (cam, [(q, {j}) for q in moves * 2])
        print(f"{j}: positive MuJoCo angle {'matches' if sign > 0 else 'is opposite to'} the prompt")

    out["scan"] = ("side", [({}, set(JOINTS[:k])) for k in range(len(JOINTS) + 1) for _ in range(4)]
                   + [({}, set(JOINTS))] * 8)

    n = 48
    out["teleop"] = ("side", [({"shoulder_pan": 0.35 * math.sin(2 * math.pi * i / n),
                                "shoulder_lift": -0.12 + 0.12 * math.sin(2 * math.pi * i / n + 1),
                                "elbow_flex": 0.2 * math.sin(2 * math.pi * i / n + 2),
                                "wrist_flex": 0.3 * math.sin(4 * math.pi * i / n),
                                "gripper": 0.6 + 0.6 * math.sin(2 * math.pi * i / n)}, set(JOINTS))
                              for i in range(n)])
    return out


def render_clip(sc: Scene, camera: str, frames: list[tuple[dict, set]]) -> dict[str, list[np.ndarray]]:
    sc.close_up() if camera == "close" else sc.side()
    full = [sc.segment(q, hl) for q, hl in frames]
    # one crop for the whole clip, so the arm does not jitter between frames
    ys, xs = np.nonzero(np.any([r > 0 for r, _ in full], axis=0))
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    out = {}
    for size, ((box_w, box_h), finest) in SIZES.items():
        # +2 for the outline ring that paint() adds
        block = max(finest, math.ceil((x1 - x0) / (box_w - 2)), math.ceil((y1 - y0) / (box_h - 2)))
        out[size] = [paint(*reduce(r[y0:y1 + block, x0:x1 + block], b[y0:y1 + block, x0:x1 + block], block))
                     for r, b in full]
    return out


def save_sheet(path: Path, frames: list[np.ndarray]) -> None:
    sheet = Image.fromarray(np.vstack(frames), "P")
    sheet.putpalette([v for i in range(len(ROLES)) for v in (i * 20, i * 20, i * 20)])  # placeholder colours
    sheet.save(path, transparency=0, optimize=False)


def save_preview(path: Path, frames: list[np.ndarray], scale: int = 5) -> None:
    """A GIF in the Graphite theme for the README (the package itself never ships colours)."""
    colours = GRAPHITE.sprite("step")
    images = []
    for f in frames:
        rgba = np.zeros(f.shape + (4,), np.uint8)
        for idx, role in enumerate(ROLES):
            c = colours.get(role)
            if c:
                rgba[f == idx] = [int(c[i:i + 2], 16) for i in (1, 3, 5)] + [255]
        bg = Image.new("RGBA", (f.shape[1], f.shape[0]), GRAPHITE.bg)
        img = Image.alpha_composite(bg, Image.fromarray(rgba, "RGBA")).convert("RGB")
        images.append(img.resize((img.width * scale, img.height * scale), Image.NEAREST))
    images[0].save(path, save_all=True, append_images=images[1:], duration=int(1000 / FPS), loop=0)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("model", type=Path, help="SO-ARM100/Simulation/SO101/so101_new_calib.xml")
    p.add_argument("--only", nargs="*", help="render only these clips")
    args = p.parse_args()
    sc = Scene(args.model)
    PACKAGE.mkdir(parents=True, exist_ok=True)
    DOCS.mkdir(parents=True, exist_ok=True)
    index_path = PACKAGE / "index.json"
    index = json.loads(index_path.read_text()) if index_path.exists() and args.only else {}
    for name, (camera, frames) in clips(sc).items():
        if args.only and name not in args.only:
            continue
        rendered = render_clip(sc, camera, frames)
        index[name] = {"fps": FPS, "frames": len(frames)}
        for size, sprites in rendered.items():
            save_sheet(PACKAGE / f"{name}-{size}.png", sprites)
            index[name][size] = list(sprites[0].shape[::-1])  # [w, h]
        save_preview(DOCS / f"{name}.gif", rendered["l"])
        print(f"{name}: {len(frames)} frames, " + ", ".join(f"{s} {index[name][s][0]}x{index[name][s][1]}"
                                                          for s in SIZES))
    index_path.write_text(json.dumps(dict(sorted(index.items())), indent=1) + "\n")


if __name__ == "__main__":
    main()
