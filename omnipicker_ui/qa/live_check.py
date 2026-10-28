"""Explicit live gripper verification through the local UI; never commands FR3 joints."""
import argparse
import json
from pathlib import Path
import re
import time
import urllib.error
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["status", "connect", "probe", "motion"])
    parser.add_argument("--ip", default="192.168.57.2")
    parser.add_argument("--port", type=int, default=8088)
    parser.add_argument("--confirmed-clear", action="store_true")
    args = parser.parse_args()
    root = f"http://127.0.0.1:{args.port}"
    with urllib.request.urlopen(root, timeout=5) as response:
        html = response.read().decode()
    token = json.loads(re.search(r"window\.CONTROL_TOKEN\s*=\s*(\"[^\"]+\")", html)[1])
    samples = []

    def api(path, data=None):
        request = urllib.request.Request(root+"/api/"+path,
            data=None if data is None else json.dumps(data).encode(),
            headers={"Content-Type": "application/json", "X-Control-Token": token})
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                result = json.load(response)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(json.loads(exc.read()).get("error", str(exc))) from exc
        if result.get("error"):
            raise RuntimeError(result["error"])
        return result

    def status():
        result = api("status")
        if result["mode"] != "hardware" or result["ip"] != args.ip:
            raise RuntimeError("실장비 모드/IP 불일치")
        samples.append(dict(time=time.time(), robot=result["robot"], gripper=result["gripper"]))
        return result

    def wait(predicate, seconds, label):
        deadline = time.monotonic()+seconds
        while time.monotonic() < deadline:
            state = status()
            if predicate(state):
                return state
            time.sleep(.4)
        raise RuntimeError(label+": "+json.dumps(state["gripper"], ensure_ascii=False))

    try:
        state = status()
        if args.action == "connect" and not state["robot"]["connected"]:
            if not state["robot"]["connecting"]:
                api("connect", {"ip": args.ip})
            state = wait(lambda s: s["robot"]["connected"] and not s["robot"]["connecting"], 25, "FR3 연결 시간 초과")
        elif args.action == "probe":
            if not state["robot"]["connected"]:
                raise RuntimeError("먼저 connect를 실행하세요.")
            api("gripper/probe", {})
            state = wait(lambda s: s["gripper"]["feedback_valid"], 20, "RS485 피드백 시간 초과")
        elif args.action == "motion":
            if not args.confirmed_clear:
                raise RuntimeError("작업영역·무부하 확인 후 --confirmed-clear가 필요합니다.")
            grip = state["gripper"]
            if not grip["feedback_valid"] or grip["error_code"] != 0 or grip["motion_state"] != 0:
                raise RuntimeError("유효한 피드백, 오류 0, 도달 상태가 필요합니다.")
            if state["robot"]["busy"]:
                raise RuntimeError("FR3 이동 중입니다.")
            start = int(grip["actual_position"])
            target = start+13 if start <= 242 else start-13
            restore = start
            api("enable", {"confirm": "PHYSICAL_AREA_CLEAR"})
            for value in (target, restore):
                response = api("gripper", {"position": value, "speed": 10, "torque": 10})
                print(json.dumps(dict(command=value, response=response), ensure_ascii=False), flush=True)
                def reached(s):
                    g = s["gripper"]
                    if not g["feedback_valid"]:
                        return False
                    if g["error_code"] != 0 or g["motion_state"] in (2, 3):
                        raise RuntimeError("그리퍼 오류/정체: "+json.dumps(g, ensure_ascii=False))
                    return g["motion_state"] == 0 and abs(g["actual_position"]-value) <= 3
                state = wait(reached, 10, "목표 위치 미도달")
                print(json.dumps(dict(reached=state["gripper"]["actual_position"], error=state["gripper"]["error_code"]), ensure_ascii=False), flush=True)
        print(json.dumps(dict(robot=state["robot"], gripper=state["gripper"]), ensure_ascii=False, indent=2))
    finally:
        if args.action == "motion":
            try:
                api("lock", {})
            except Exception:
                pass
        directory = Path(__file__).resolve().parent / "live_results"
        directory.mkdir(exist_ok=True)
        path = directory / f"{time.strftime('%Y%m%d_%H%M%S')}_{args.action}.json"
        path.write_text(json.dumps(samples, ensure_ascii=False, indent=2))
        print("Evidence: "+str(path), flush=True)


if __name__ == "__main__":
    main()
