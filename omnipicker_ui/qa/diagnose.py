"""Read-only FR3/RS485 diagnostics. Never sends writes or motion commands."""
import argparse
import json
import xmlrpc.client
import time


class ReadTransport(xmlrpc.client.Transport):
    def make_connection(self, host):
        connection = super().make_connection(host)
        connection.timeout = 2
        return connection


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ip", required=True)
    args = parser.parse_args()
    result = {}
    queries = [(name, name, arguments) for name, arguments in [
        ("GetSoftwareVersion", ()),
        ("GetExDevProtocol", ()), ("GetProgramState", ()), ("GetLoadedProgram", ()),
        ("GetActualJointPosDegree", (1,)), ("GetActualToolFlangePose", (1,))]]
    for key, method, arguments in queries:
        try:
            with xmlrpc.client.ServerProxy(f"http://{args.ip}:20003", transport=ReadTransport()) as proxy:
                result[key] = getattr(proxy, method)(*arguments)
        except Exception as exc:
            result[key] = str(exc)
    for address,count in [(0,1),*[(address,1) for address in range(20,25)]]:
        try:
            with xmlrpc.client.ServerProxy(f"http://{args.ip}:20003",transport=ReadTransport()) as proxy:
                code=proxy.ModbusRegRead(3,address,count,1,0)
                time.sleep(.15)
                result[f"registers_{address}"]={"read":code,"data":proxy.ModbusRegGetData(count,0)}
        except Exception as exc:
            result[f"registers_{address}"]=str(exc)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
