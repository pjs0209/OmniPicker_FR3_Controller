#!/usr/bin/env python3
"""OmniPicker USB-C settings UI: Fibre discovery, RS485 settings and MIT test."""
import argparse
import os


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", action="store_true", help="USB 통신 없이 UI 시뮬레이션")
    parser.add_argument("--port", type=int, default=8089)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("port는 1024~65535여야 합니다.")
    os.environ.update(SETTING_PORT=str(args.port), SETTING_DEMO="1" if args.demo else "0")
    from omnipicker_ui.setting import app
    print(f"OmniPicker_Setting · {'DEMO' if args.demo else 'USB'} · http://127.0.0.1:{args.port}", flush=True)
    app.run(host="127.0.0.1", port=args.port, threaded=True, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
