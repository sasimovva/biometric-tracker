#!/usr/bin/env python3
"""
Download your recent Garmin Connect activities to local disk using the official API OAuth flow.

Why not Playwright? Garmin gates programmatic logins behind Cloudflare and
(usually) MFA, and its web DOM changes often. The `garminconnect` library
authenticates with the same OAuth/SSO flow the official app uses, gets past
Cloudflare, and exposes a direct download endpoint. Far more robust.

Setup
-----
    pip install garminconnect

    export GARMIN_EMAIL="you@example.com"
    export GARMIN_PASSWORD="your-password"

Run
---
    python3 garmin_download.py                 # last 20 activities, .fit
    python3 garmin_download.py --count 50 --format TCX --out ./activities

Notes
-----
- Credentials come from environment variables or safe command line prompts.
- ORIGINAL = the raw .fit (full per-second HR stream; best for time-in-zone).
  It downloads as a .zip containing the .fit. TCX is plain XML and easier to
  parse if you don't want to deal with the FIT binary format.
- The OAuth token is cached in GARMINTOKENS so you only do the full login
  (and any MFA prompt) once; later runs reuse the token silently.
"""

import argparse
import os
import sys
import zipfile
from pathlib import Path

try:
    from garminconnect import Garmin
except ImportError:
    sys.exit("Missing dependency. Run:  pip install garminconnect")

TOKENSTORE = os.path.expanduser(os.getenv("GARMINTOKENS", "~/.garminconnect"))

FORMATS = {
    "ORIGINAL": Garmin.ActivityDownloadFormat.ORIGINAL,  # .fit (zipped) — full fidelity
    "TCX": Garmin.ActivityDownloadFormat.TCX,             # XML, easy to parse
    "GPX": Garmin.ActivityDownloadFormat.GPX,
    "CSV": Garmin.ActivityDownloadFormat.CSV,
}
EXT = {"ORIGINAL": "zip", "TCX": "tcx", "GPX": "gpx", "CSV": "csv"}


def make_client() -> Garmin:
    """Resume a cached session if possible, else log in fresh and cache it."""
    email = os.getenv("GARMIN_EMAIL")
    password = os.getenv("GARMIN_PASSWORD")

    # Try the cached token first — no password / MFA needed on repeat runs.
    try:
        client = Garmin()
        client.login(TOKENSTORE)
        return client
    except Exception:
        pass  # fall through to a fresh login

    if not email:
        email = input("Garmin Email: ").strip()
    if not password:
        import getpass
        password = getpass.getpass("Garmin Password: ")

    if not email or not password:
        sys.exit("Set GARMIN_EMAIL and GARMIN_PASSWORD or fill the prompt.")

    # prompt_mfa is called only if your account has multi-factor auth enabled.
    client = Garmin(email=email, password=password,
                    prompt_mfa=lambda: input("MFA code: ").strip())
    client.login()
    try:
        client.garth.dump(TOKENSTORE)  # cache for next time
    except Exception:
        pass  # token caching is a nicety, not required
    return client


def main() -> None:
    p = argparse.ArgumentParser(description="Download recent Garmin activities.")
    p.add_argument("--count", type=int, default=20, help="how many recent activities (default 20)")
    p.add_argument("--format", choices=FORMATS, default="ORIGINAL", help="export format (default ORIGINAL=.fit)")
    p.add_argument("--out", default="./garmin_activities", help="output directory")
    args = p.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    client = make_client()

    # get_activities(start_index, limit) -> most recent first
    try:
        activities = client.get_activities(0, args.count)
    except Exception as e:
        sys.exit(f"❌ Failed to get activities: {e}")
        
    print(f"Found {len(activities)} activities; downloading as {args.format} into {out}/")

    ext = EXT[args.format]
    fmt = FORMATS[args.format]

    for act in activities:
        aid = act["activityId"]
        name = (act.get("activityName") or "activity").replace("/", "-").replace(" ", "_")
        start = (act.get("startTimeLocal") or "").split(" ")[0]  # YYYY-MM-DD
        fname = out / f"{start}_{aid}_{name}.{ext}"

        if fname.exists():
            print(f"  skip  {fname.name} (already downloaded)")
            continue

        try:
            data = client.download_activity(aid, dl_fmt=fmt)
            fname.write_bytes(data)
            print(f"  saved {fname.name}  ({len(data):,} bytes)")

            # ORIGINAL comes as a zip; unzip the .fit so it's ready to parse.
            if args.format == "ORIGINAL":
                try:
                    with zipfile.ZipFile(fname) as z:
                        z.extractall(out)
                    fname.unlink()
                except zipfile.BadZipFile:
                    pass  # some activities return a bare file
        except Exception as e:
            print(f"  ❌ failed to download {aid}: {e}")

    print("Done.")


if __name__ == "__main__":
    main()
