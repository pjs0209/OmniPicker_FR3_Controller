#!/usr/bin/env python3
"""FR3 + controller-box RS485 OmniPicker UI. Run with --demo without hardware."""
import argparse
import os


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", action="store_true", help="장비 통신 없이 UI 시뮬레이션")
    parser.add_argument("--port", type=int, default=8088)
    parser.add_argument("--ip", default=os.getenv("FR3_IP", "192.168.57.2"))
    parser.add_argument("--sdk-path", help="FAIRINO Python SDK의 linux 폴더")
    parser.add_argument("--slave-id", type=int, default=1, help="그리퍼 Modbus 슬레이브 주소")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("port는 1024~65535여야 합니다.")
    if not 1 <= args.slave_id <= 247:
        parser.error("slave-id는 1~247이어야 합니다.")
    os.environ.update(WEB_PORT=str(args.port), FR3_IP=args.ip, GRIPPER_SLAVE=str(args.slave_id), CONTROL_DEMO="1" if args.demo else "0")
    if args.sdk_path:
        os.environ["FAIRINO_SDK_PATH"] = args.sdk_path
    from omnipicker_ui.controller import app
    print(f"OmniPicker_FR3_Controller · {'DEMO' if args.demo else 'HARDWARE'} · http://127.0.0.1:{args.port}", flush=True)
    app.run(host="127.0.0.1", port=args.port, threaded=True, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
