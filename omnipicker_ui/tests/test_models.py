"""Validate source-backed STL geometry and nominal URDF forward kinematics."""
import json
import math
from pathlib import Path
import struct
import unittest

ROOT = Path(__file__).resolve().parents[1]
MODELS = json.loads((ROOT / "static/models/models.json").read_text())


def multiply(a, b):
    return [[sum(a[i][k]*b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def transform(xyz, rpy):
    r, p, y = rpy
    sr, cr, sp, cp, sy, cy = math.sin(r), math.cos(r), math.sin(p), math.cos(p), math.sin(y), math.cos(y)
    return [[cy*cp, cy*sp*sr-sy*cr, cy*sp*cr+sy*sr, xyz[0]],
            [sy*cp, sy*sp*sr+cy*cr, sy*sp*cr-cy*sr, xyz[1]],
            [-sp, cp*sr, cp*cr, xyz[2]], [0, 0, 0, 1]]


class ModelTests(unittest.TestCase):
    def test_model_files_are_real_nonempty_stls(self):
        for model in [*MODELS["arms"].values(), MODELS["gripper"]]:
            for link in model["links"]:
                if not link["mesh"]: continue
                file = ROOT / link["mesh"].lstrip("/")
                data = file.read_bytes()
                count = struct.unpack("<I", data[80:84])[0]
                self.assertGreater(count, 100, file)
                self.assertEqual(len(data), 84+50*count, file)

    def test_fr3wms_fk_matches_readonly_flange_observation(self):
        angles = [14.41036413211633, -112.7242951345916, -19.04441909034653,
                  -109.9809763691213, 85.93679900216584, 10.5037998917079]
        pose = transform([0, 0, 0], [0, 0, 0]); index = 0
        for joint in MODELS["arms"]["FR3WMS"]["joints"]:
            pose = multiply(pose, transform(joint["origin"]["xyz"], joint["origin"]["rpy"]))
            if joint["type"] == "revolute":
                pose = multiply(pose, transform([0, 0, 0], [0, 0, math.radians(angles[index])]))
                index += 1
        observed = [419.7415466308593, -4.826563835144043, 537.5677490234375]
        error_mm = math.sqrt(sum((pose[i][3]*1000-observed[i])**2 for i in range(3)))
        self.assertLess(error_mm, 2., "wrong robot variant, RPY convention or flange transform")

    def test_gripper_has_real_articulated_linkage(self):
        model = MODELS["gripper"]
        self.assertEqual(len(model["links"]), 9)
        self.assertEqual(len(model["joints"]), 8)
        self.assertEqual(sum("mimic" in joint for joint in model["joints"]), 7)
        self.assertIn(model["driven"], [joint["name"] for joint in model["joints"]])


if __name__ == "__main__":
    unittest.main()
