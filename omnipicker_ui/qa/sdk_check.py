"""Standalone SDK verification. Hardware defaults to read-only; no WebApp."""
import argparse
import json
import time
from pathlib import Path

from omnipicker_ui import OmniPickerFR3


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ip", default="192.168.57.2")
    parser.add_argument("--slave-id", type=int, default=1)
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--motion", action="store_true", help="그리퍼 소폭 이동 후 복귀")
    parser.add_argument("--parallel-demo", action="store_true", help="데모에서만 관절 동시 제어")
    parser.add_argument("--confirmed-clear", action="store_true")
    args = parser.parse_args()
    if args.parallel_demo and not args.demo:
        parser.error("관절 동시 구동 검증은 데모에서만 실행합니다.")
    if args.motion and not args.demo and not args.confirmed_clear:
        parser.error("실장비 구동은 --confirmed-clear가 필요합니다.")
    result = dict(demo=args.demo, ip=args.ip, commands=[])
    sdk = OmniPickerFR3(args.ip, slave_id=args.slave_id, demo=args.demo)
    try:
        sdk.connect()
        result['before'] = sdk.snapshot()
        if args.motion:
            state = sdk.probe_gripper()
            if not state['connected'] or state['error_code'] != 0 or state['motion_state'] != 0:
                raise RuntimeError("오류 없는 도달 상태에서만 구동 검증합니다.")
            start = state['actual_position']
            target = start+13 if start <= 242 else start-13
            sdk.arm()
            for position in (target, start):
                if args.parallel_demo:
                    reply = sdk.move_together(axis=0, delta=.5 if position==target else -.5,
                                              position=position, speed=20, torque=20)
                else:
                    reply = sdk.grip(position,20,20)
                result['commands'].append(reply)
        result['after'] = sdk.snapshot()
        result['success'] = True
    except Exception as exc:
        result.update(success=False, error=str(exc), after=sdk.snapshot())
    finally:
        sdk.disarm()
        sdk.close()
    folder = Path(__file__).resolve().parent/'live_results'
    folder.mkdir(exist_ok=True)
    path = folder/f"{time.strftime('%Y%m%d_%H%M%S')}_sdk{'_demo' if args.demo else ''}.json"
    path.write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps(result,ensure_ascii=False,indent=2))
    if not result['success']: raise SystemExit(1)


if __name__ == '__main__': main()
