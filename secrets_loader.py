"""Load shared API keys from the ~/Code monorepo (sops + age encrypted).

This project reuses the same `secrets.enc.json` as the other personal
automation agents (PEOutreach, IdeaGen, Orchestrator, ...) instead of keeping
its own copy of API keys. The file is encrypted at rest with sops + age; the
age key lives at ~/Library/Application Support/sops/age/keys.txt.

Decrypted values are merged into os.environ with `setdefault`, so anything
already set in the real environment or a local .env always wins and is never
overwritten. Decrypted secrets are kept in memory only — never written to disk.
"""
import json
import os
import subprocess

# Shared, encrypted secrets store for all personal automation agents.
SECRETS_PATH = os.path.expanduser("~/Code/secrets.enc.json")


def load_shared_secrets(path: str = SECRETS_PATH) -> dict:
    """Decrypt the shared secrets file and merge keys into os.environ.

    Returns the decrypted dict (empty if the file is absent). Existing
    environment variables take precedence and are never clobbered.
    """
    if not os.path.exists(path):
        return {}

    proc = subprocess.run(
        ["sops", "decrypt", path],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"Failed to decrypt {path}: {proc.stderr.strip()}\n"
            "Ensure `sops` is installed and SOPS_AGE_KEY_FILE points at your age key "
            "(default: ~/Library/Application Support/sops/age/keys.txt)."
        )

    secrets = json.loads(proc.stdout)
    for key, value in secrets.items():
        os.environ.setdefault(key, str(value))
    return secrets


if __name__ == "__main__":
    loaded = load_shared_secrets()
    print(f"Loaded {len(loaded)} shared secret(s): {sorted(loaded)}")
