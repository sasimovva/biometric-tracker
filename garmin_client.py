#!/usr/bin/env python3
"""Shared Garmin Connect authentication for the biometric tracker.

Both garmin_download.py (archive raw .fit/.tcx files) and import_garmin.py
(parse + store structured workouts into the DB/dashboards) use this.

Resumes a cached OAuth token when possible, so the full SSO login (and any MFA
prompt) is only needed on the first run; later runs reuse the token silently.
Falls back to GARMIN_EMAIL / GARMIN_PASSWORD, prompting only when interactive.
"""
import os
import sys

try:
    from garminconnect import Garmin
except ImportError:
    sys.exit("Missing dependency. Run:  pip install garminconnect")

# Where the OAuth token is cached between runs.
TOKENSTORE = os.path.expanduser(os.getenv("GARMINTOKENS", "~/.garminconnect"))


def make_client(interactive=True):
    """Return an authenticated Garmin client.

    1. Try the cached token (no password / MFA on repeat runs).
    2. Else log in with GARMIN_EMAIL / GARMIN_PASSWORD, prompting for anything
       missing only when `interactive` is True. Caches the token on success.
    """
    # 1. Resume a cached session if one exists.
    try:
        client = Garmin()
        client.login(TOKENSTORE)
        return client
    except Exception:
        pass  # fall through to a fresh login

    # 2. Fresh login.
    email = os.getenv("GARMIN_EMAIL")
    password = os.getenv("GARMIN_PASSWORD")
    if interactive:
        if not email:
            email = input("Garmin Email: ").strip()
        if not password:
            import getpass
            password = getpass.getpass("Garmin Password: ")
    if not email or not password:
        sys.exit("Set GARMIN_EMAIL and GARMIN_PASSWORD (or run interactively).")

    print("🔄 Authenticating with Garmin Connect...")
    client = Garmin(email=email, password=password)
    client.login()
    try:
        client.garth.dump(TOKENSTORE)  # cache for next time
    except Exception:
        pass  # token caching is a nicety, not required
    print("✅ Logged in to Garmin Connect.")
    return client
