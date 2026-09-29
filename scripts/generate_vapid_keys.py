"""
Generate a VAPID keypair for Web Push and append it to .env.

Run once:  ./.venv/bin/python scripts/generate_vapid_keys.py

The public key is shared with browsers as `applicationServerKey`.
The private key signs push requests and must stay secret - it belongs in .env
locally and in Railway's environment variables in production.
"""

from pathlib import Path

from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid01
from py_vapid.utils import b64urlencode


def encode_public_key(vapid: Vapid01) -> str:
    """applicationServerKey must be the uncompressed EC point (65 bytes)."""
    return b64urlencode(
        vapid.public_key.public_bytes(
            serialization.Encoding.X962,
            serialization.PublicFormat.UncompressedPoint,
        )
    )


def encode_private_key(vapid: Vapid01) -> str:
    """pywebpush accepts the raw 32-byte private value, base64url encoded."""
    return b64urlencode(
        vapid.private_key.private_numbers().private_value.to_bytes(32, "big")
    )


def main() -> None:
    env_path = Path(__file__).resolve().parent.parent / ".env"

    if env_path.exists() and "VAPID_PRIVATE_KEY=" in env_path.read_text():
        print("VAPID keys already present in .env - not overwriting.")

        return

    vapid = Vapid01()
    vapid.generate_keys()

    public = encode_public_key(vapid)
    private = encode_private_key(vapid)

    # Confirm pywebpush can load what we're about to store.
    reloaded = Vapid01.from_string(private_key=private)

    if encode_public_key(reloaded) != public:
        raise SystemExit("Generated private key did not round-trip; aborting.")

    with env_path.open("a") as handle:
        handle.write("\n# Web Push (VAPID) - private key must stay secret\n")
        handle.write(f"VAPID_PUBLIC_KEY={public}\n")
        handle.write(f"VAPID_PRIVATE_KEY={private}\n")
        handle.write("VAPID_SUBJECT=mailto:admin@example.com\n")

    print("Round-trip OK. Appended VAPID keys to .env")
    print(f"Public key (safe to share): {public}")


if __name__ == "__main__":
    main()
