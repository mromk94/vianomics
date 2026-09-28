#!/usr/bin/env python3
"""VAIIP MT4 bridge — for terminals running under Wine/CrossOver.

Watches MQL4/Files/vaiip_push.json (written by VAIIP_Push.mq4) and
POSTs it to the API whenever it changes. Stdlib only.

    python3 mt4_bridge.py --files-dir "/path/to/MQL4/Files" \
        --api https://vianomics.onrender.com/api/v1
"""

import argparse
import json
import time
import urllib.request


def push(api: str, payload: str) -> None:
    req = urllib.request.Request(
        f"{api}/external/mt4/push",
        data=payload.encode(),
        headers={"content-type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            print(f"pushed — HTTP {r.status}")
    except Exception as e:  # noqa: BLE001
        print(f"push failed: {e}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--files-dir", required=True,
                   help="MT4 → File → Open Data Folder → MQL4/Files")
    p.add_argument("--api",
                   default="https://vianomics.onrender.com/api/v1")
    p.add_argument("--interval", type=float, default=15.0)
    a = p.parse_args()

    path = f"{a.files_dir}/vaiip_push.json"
    last = None
    print(f"watching {path}")
    while True:
        try:
            with open(path) as f:
                data = f.read()
            if data != last and json.loads(data).get("secret"):
                push(a.api, data)
                last = data
        except FileNotFoundError:
            print("waiting for EA to write vaiip_push.json…")
            time.sleep(10)
        except json.JSONDecodeError:
            print("partial write — retrying")
            time.sleep(2)
        time.sleep(a.interval)


if __name__ == "__main__":
    main()
