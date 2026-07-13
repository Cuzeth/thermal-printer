"""Generate the admin TOTP secret and show it as a scannable QR.

Run once (on the Pi or anywhere), scan the QR with an authenticator app
(or type the secret in manually), and paste the TOTP_SECRET line into
.env. Re-running mints a NEW secret — the old codes stop working once
you update .env and restart.

    python3 scripts/gen_totp.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from auth import totp  # noqa: E402


def main() -> None:
    secret = totp.generate_secret()
    uri = totp.otpauth_uri(secret)

    print("\nScan this with your authenticator app:\n")
    try:
        import qrcode  # already a dependency (the Codes tab uses it)
        qr = qrcode.QRCode(border=1)
        qr.add_data(uri)
        qr.print_ascii(invert=True)
    except ImportError:
        print("  (qrcode not installed — enroll manually with the URI below)")

    print(f"\nor enter the secret manually: {secret}")
    print(f"otpauth URI: {uri}")
    print("\nThen add this line to .env and restart the service:\n")
    print(f"TOTP_SECRET={secret}\n")


if __name__ == "__main__":
    main()
