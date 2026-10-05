"""Machine-to-machine job trigger for external schedulers.

Usage (Render cronjob, GitHub Actions, any cron host):

    python -m app.cron_trigger pyramid:maintain

Env:  API_URL      — e.g. https://vianomics.onrender.com
      CRON_SECRET  — must match the API's CRON_SECRET setting

POSTs /api/v1/internal/jobs/{key} — the endpoint enforces the secret
(fails closed when unset). Stdlib-only: the production image ships no
curl.
"""

import os
import sys
import urllib.request


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python -m app.cron_trigger <job_key>",
              file=sys.stderr)
        return 2
    job_key = sys.argv[1]
    base = (os.environ.get("API_URL")
            or "http://localhost:8000").rstrip("/")
    secret = os.environ.get("CRON_SECRET", "")
    if not secret:
        print("CRON_SECRET env var is required", file=sys.stderr)
        return 2
    req = urllib.request.Request(
        f"{base}/api/v1/internal/jobs/{job_key}",
        method="POST",
        headers={"X-Cron-Secret": secret})
    try:
        with urllib.request.urlopen(req, timeout=600) as res:
            print(res.read().decode())
            return 0
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}: {e.read().decode()}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
