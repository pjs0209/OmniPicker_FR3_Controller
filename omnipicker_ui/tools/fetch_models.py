"""Fetch pinned manufacturer CAD and Three.js for an entirely local 3D viewer."""
import concurrent.futures
import hashlib
import json
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / "static/models"
VENDOR = ROOT / "static/vendor"
FAIR_SHA = "fcf0c7f0d60d949d8a9a4238f929a44d07f60379"
AGI_SHA = "6d5b2159806ae0e64b432b638e9ee288d8bddc8f"
FAIR = f"https://raw.githubusercontent.com/FAIR-INNOVATION/frcobot_ros2/{FAIR_SHA}/"
AGI = f"https://raw.githubusercontent.com/AgibotTech/genie_sim_robot_model/{AGI_SHA}/"


def main():
    jobs = []
    for variant in ("FR3WMS", "FR3WML"):
        jobs.append((FAIR + f"fairino_description/urdf/{variant}.urdf", MODELS / variant / "robot.urdf"))
        for link in ("base_link", "shoulder_Link", "upperarm_Link", "forearm_Link", "wrist1_Link", "wrist2_Link", "wrist3_Link"):
            jobs.append((FAIR + f"fairino_description/meshes/{variant}/collision/{link}.STL", MODELS / variant / f"{link}.stl"))
    jobs.append((AGI + "robots/genie/g2/xacro/gripper.omnipicker.urdf.xacro", MODELS / "OmniPicker/source.urdf.xacro"))
    for link in ("base_link", "inner_link1", "inner_link2", "inner_link3", "inner_link4", "outer_link1", "outer_link2", "outer_link3", "outer_link4"):
        jobs.append((AGI + f"robots/genie/g2/meshes/gripper/omnipicker/{link}.dae", MODELS / "OmniPicker/source" / f"{link}.dae"))
    for name in ("LICENSE", "THIRD_PARTY_NOTICES"):
        jobs.append((AGI + name, MODELS / "OmniPicker" / name))
    for remote, local in [
        ("build/three.module.min.js", "three.module.min.js"),
        ("build/three.core.min.js", "three.core.min.js"),
        ("examples/jsm/controls/OrbitControls.js", "OrbitControls.js"),
        ("examples/jsm/loaders/STLLoader.js", "STLLoader.js"), ("LICENSE", "THREE-LICENSE")]:
        jobs.append(("https://cdn.jsdelivr.net/npm/three@0.180.0/" + remote, VENDOR / local))

    def fetch(job):
        url, path = job
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            request = urllib.request.Request(url, headers={"User-Agent": "OmniPickerLocalViewer/1.0"})
            with urllib.request.urlopen(request, timeout=45) as response:
                path.write_bytes(response.read())
        return dict(file=str(path.relative_to(ROOT)), source=url,
                    sha256=hashlib.sha256(path.read_bytes()).hexdigest(), bytes=path.stat().st_size)

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        provenance = list(pool.map(fetch, jobs))
    (MODELS / "sources.json").write_text(json.dumps(provenance, indent=2) + "\n")
    print(f"Downloaded/verified {len(provenance)} pinned files ({sum(p['bytes'] for p in provenance):,} bytes)")


if __name__ == "__main__":
    main()
