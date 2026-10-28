"""Build an API inventory from vendor sources without importing/connecting them."""
import argparse
import ast
import hashlib
import json
from pathlib import Path


def fr3_inventory(path):
    raw = path.read_bytes()
    cls = next(n for n in ast.parse(raw).body if isinstance(n, ast.ClassDef) and n.name == 'RPC')
    methods = []
    for node in cls.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or node.name.startswith('_'):
            continue
        arguments = ast.unparse(node.args)
        arguments = arguments[6:] if arguments.startswith('self, ') else '' if arguments == 'self' else arguments
        methods.append(dict(name=node.name, signature=f'{node.name}({arguments})', line=node.lineno,
                            dispatch='original_sdk', hardware_validation='not_individually_verified'))
    return dict(source=str(path), sha256=hashlib.sha256(raw).hexdigest(),
                class_name='fairino.Robot.RPC', methods=methods)


def fibre_inventory(path):
    raw = path.read_bytes()
    def walk(nodes, parent=''):
        for node in nodes:
            name = '.'.join(p for p in (parent, node.get('name', '')) if p)
            kind = node.get('type')
            if kind == 'function':
                yield dict(name=name, kind='function', inputs=[{k:n[k] for k in ('name','type')} for n in node.get('inputs',[])],
                           outputs=[{k:n[k] for k in ('name','type')} for n in node.get('outputs',[])], requires=['usb'])
            elif kind in ('bool','uint8','uint32','int32','float'):
                yield dict(name=name, kind='property', type=kind, access=node.get('access','r'), requires=['usb'])
            yield from walk(node.get('members',[]), name)
    return dict(source=str(path), sha256=hashlib.sha256(raw).hexdigest(),
                schema_kind='observed_firmware_schema', features=list(walk(json.loads(raw))))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fr3-source', type=Path, required=True)
    parser.add_argument('--fibre-schema', type=Path, required=True)
    parser.add_argument('--omnipicker-header', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parents[1]/'sdk_catalog.json')
    args = parser.parse_args()
    # The OP1 public header marks these operations as transport specific. Do not
    # invent register addresses or reuse the unrelated OP3/tactile API.
    vendor = {
        'SendFrameSyncByCanfd':['can','canfd'], 'SendBroadcastFrameByCanfd':['canfd'],
        'SendMitFrameAsync':['usb'], 'GetCanfdFrameRange':['can','canfd'],
        'GetMitFrameRange':['usb'], 'UpdateFirmwareViaFlash':['usb'],
        'StartMotorCalibration':['usb'], 'Reboot':['usb'], 'SetCanNodeId':['usb'],
        'GetCanNodeId':['usb'], 'ShowDeviceInfo':['usb'],
        'createHandByUSB':['usb'], 'createHandByZlgcan':['can','canfd'],
        'createHandByZlgCanTcp':['canfd'], 'createHandSocketCan':['can','canfd'],
        'createHandByHcan':['can','canfd'],
        'SetHandGesture':['usb','can','canfd'], 'GetHandGesture':['usb','can','canfd'],
        'GetNumOfJointMotors':[], 'GetDoA':[],
    }
    header = args.omnipicker_header.read_bytes()
    for name in vendor:
        if name.encode() not in header: raise ValueError(f'Vendor method missing: {name}')
    catalog = dict(schema_version=1, fr3=fr3_inventory(args.fr3_source),
                   omnipicker_fibre=fibre_inventory(args.fibre_schema),
                   omnipicker_vendor=dict(source=str(args.omnipicker_header),sha256=hashlib.sha256(header).hexdigest(),
                       product='OmniPicker2025 / OP1',methods=[dict(name=name,requires=transports) for name,transports in vendor.items()]),
                   sources=dict(fr3='https://fairino-doc-en.readthedocs.io/3.9.8/download.html',
                                omnipicker='https://www.agibot.com/DOCS/PM/news'))
    args.output.write_text(json.dumps(catalog,ensure_ascii=False,indent=2)+'\n')
    print(f"FR3 {len(catalog['fr3']['methods'])} methods; OmniPicker {len(catalog['omnipicker_fibre']['features'])} Fibre features")


if __name__ == '__main__': main()
