"""Convert manufacturer Collada geometry to STL; extract URDF transforms to JSON."""
import ast
import json
import math
from pathlib import Path
import re
import struct
import xml.etree.ElementTree as ET

import numpy as np

ROOT = Path(__file__).resolve().parents[1] / "static/models"
NS = {"c": "http://www.collada.org/2005/11/COLLADASchema"}


def collada_to_stl(source, output):
    root = ET.parse(source).getroot()
    geometries = {}
    for geometry in root.findall("c:library_geometries/c:geometry", NS):
        mesh = geometry.find("c:mesh", NS)
        arrays = {}
        for node in mesh.findall("c:source", NS):
            accessor = node.find("c:technique_common/c:accessor", NS)
            arrays[node.attrib["id"]] = np.fromstring(node.find("c:float_array", NS).text, sep=" ").reshape(-1, int(accessor.attrib.get("stride", "1")))
        vertices = {node.attrib["id"]: node.find("c:input[@semantic='POSITION']", NS).attrib["source"][1:]
                    for node in mesh.findall("c:vertices", NS)}
        triangles = []
        for primitive in mesh:
            kind = primitive.tag.rsplit("}", 1)[-1]
            if kind not in ("triangles", "polylist"):
                continue
            inputs = primitive.findall("c:input", NS)
            position = next(node for node in inputs if node.attrib["semantic"] in ("VERTEX", "POSITION"))
            stride = max(int(node.attrib.get("offset", "0")) for node in inputs) + 1
            source_id = position.attrib["source"][1:]
            positions = arrays[vertices.get(source_id, source_id)][:, :3]
            indices = np.fromstring(primitive.find("c:p", NS).text, sep=" ", dtype=int).reshape(-1, stride)[:, int(position.attrib.get("offset", "0"))]
            if kind == "triangles":
                triangles.append(positions[indices.reshape(-1, 3)])
            else:
                start = 0
                for count in map(int, primitive.find("c:vcount", NS).text.split()):
                    for i in range(1, count-1):
                        triangles.append(positions[[indices[start], indices[start+i], indices[start+i+1]]][None, ...])
                    start += count
        geometries[geometry.attrib["id"]] = np.concatenate(triangles)
    result = []

    def visit(node, transform):
        transform = transform.copy()
        for child in node:
            kind = child.tag.rsplit("}", 1)[-1]
            if kind == "matrix":
                transform = transform @ np.fromstring(child.text, sep=" ").reshape(4, 4)
            elif kind == "translate":
                matrix = np.eye(4); matrix[:3, 3] = np.fromstring(child.text, sep=" ")
                transform = transform @ matrix
            elif kind == "scale":
                transform = transform @ np.diag([*np.fromstring(child.text, sep=" "), 1])
            elif kind == "rotate":
                axis_angle = np.fromstring(child.text, sep=" "); axis = axis_angle[:3]
                axis /= np.linalg.norm(axis)
                x, y, z = axis; skew = np.array([[0, -z, y], [z, 0, -x], [-y, x, 0]])
                angle = math.radians(axis_angle[3]); matrix = np.eye(4)
                matrix[:3, :3] = np.eye(3) + math.sin(angle)*skew + (1-math.cos(angle))*(skew@skew)
                transform = transform @ matrix
        for instance in node.findall("c:instance_geometry", NS):
            vertices = geometries[instance.attrib["url"][1:]]
            result.append(vertices @ transform[:3, :3].T + transform[:3, 3])
        for child in node.findall("c:node", NS):
            visit(child, transform)

    for node in root.findall("c:library_visual_scenes/c:visual_scene/c:node", NS):
        visit(node, np.eye(4))
    triangles = np.concatenate(result).astype("<f4")
    normal = np.cross(triangles[:, 1]-triangles[:, 0], triangles[:, 2]-triangles[:, 0])
    length = np.linalg.norm(normal, axis=1)
    normal /= np.maximum(length[:, None], 1e-20)
    records = np.zeros(len(triangles), dtype=[("normal", "<f4", (3,)), ("vertices", "<f4", (3, 3)), ("attribute", "<u2")])
    records["normal"], records["vertices"] = normal, triangles
    output.write_bytes(b"AGIBOT manufacturer visual CAD; converted from Collada".ljust(80, b"\0") + struct.pack("<I", len(triangles)) + records.tobytes())
    return len(triangles)


def expression(text):
    """Evaluate only arithmetic constants found in the pinned xacro origins."""
    names = {"PI": math.pi, "pi": math.pi, "PI_2": math.pi/2}
    def value(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.Name):
            return names[node.id]
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            return (-1 if isinstance(node.op, ast.USub) else 1) * value(node.operand)
        if isinstance(node, ast.BinOp):
            left, right = value(node.left), value(node.right)
            if isinstance(node.op, ast.Add): return left+right
            if isinstance(node.op, ast.Sub): return left-right
            if isinstance(node.op, ast.Mult): return left*right
            if isinstance(node.op, ast.Div): return left/right
        raise ValueError("Unsupported xacro expression")
    return re.sub(r"\$\{([^}]+)\}", lambda match: str(value(ast.parse(match[1], mode="eval").body)), text)


def origin(node):
    node = node.find("origin")
    return {key: list(map(float, expression(node.attrib.get(key, "0 0 0")).split())) if node is not None else [0, 0, 0] for key in ("xyz", "rpy")}


def name(text):
    return re.sub(r"^idx\$\{[^}]+\}_", "", text.replace("${side}", "r"))


def arm(variant):
    root = ET.parse(ROOT / variant / "robot.urdf").getroot()
    links = [dict(name=node.attrib["name"], mesh=f"/static/models/{variant}/{node.attrib['name']}.stl",
                  origin=origin(node.find("collision"))) for node in root.findall("link")]
    joints = []
    for node in root.findall("joint"):
        joints.append(dict(name=node.attrib["name"], type=node.attrib["type"], parent=node.find("parent").attrib["link"],
                           child=node.find("child").attrib["link"], origin=origin(node), axis=list(map(float, node.find("axis").attrib["xyz"].split()))))
    # The manufacturer's tool link is commented out, but its fixed joint defines the flange.
    links.append(dict(name="tool_Link", mesh=None, origin=dict(xyz=[0, 0, 0], rpy=[0, 0, 0])))
    return dict(name=variant, root="base_link", flange="tool_Link", links=links, joints=joints)


def gripper():
    root = ET.parse(ROOT / "OmniPicker/source.urdf.xacro").getroot()
    macro = root.find("{http://www.ros.org/wiki/xacro}macro")
    links = []
    for node in macro.findall("link"):
        link_name = name(node.attrib["name"])
        if "camera" in link_name: continue
        mesh = node.find("visual/geometry/mesh").attrib["filename"].rsplit("/", 1)[-1].replace(".dae", ".stl")
        links.append(dict(name=link_name, mesh="/static/models/OmniPicker/"+mesh, origin=origin(node.find("visual"))))
    link_names = {node["name"] for node in links}
    joints = []
    for node in macro.findall("joint"):
        parent, child = name(node.find("parent").attrib["link"]), name(node.find("child").attrib["link"])
        if parent not in link_names or child not in link_names: continue
        joint = dict(name=name(node.attrib["name"]), type=node.attrib["type"], parent=parent, child=child,
                     origin=origin(node), axis=list(map(float, node.find("axis").attrib["xyz"].split())))
        mimic = node.find("mimic")
        if mimic is not None:
            joint["mimic"] = dict(joint=name(mimic.attrib["joint"]), multiplier=float(mimic.attrib.get("multiplier", 1)), offset=float(mimic.attrib.get("offset", 0)))
        joints.append(joint)
    return dict(name="OmniPicker", root="gripper_r_base_link", driven="gripper_r_inner_joint1", links=links, joints=joints,
                lower=-math.pi/4, upper=0, pose_source="manufacturer URDF mimic joints; contact adaptation is not observable")


def main():
    for source in sorted((ROOT / "OmniPicker/source").glob("*.dae")):
        count = collada_to_stl(source, source.parent.parent / (source.stem+".stl"))
        print(f"{source.stem}: {count:,} triangles")
    manifest = dict(arms={variant: arm(variant) for variant in ("FR3WMS", "FR3WML")}, gripper=gripper(), units="metres")
    (ROOT / "models.json").write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    main()
