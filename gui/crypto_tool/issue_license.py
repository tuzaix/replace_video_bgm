"""
Issue license file by signing machine code and metadata with RSA private key.

This tool creates a license.dat that contains:
- JSON license data (hardware_id, expires_at, features, issuer, issued_at)
- PKCS#1 v1.5 signature over the JSON (SHA-256)

The file format is:
    <json>\n---SIGNATURE_BELOW---\n<signature-bytes>

Dependencies: PyCryptodome (Crypto.*)
Install: python -m pip install pycryptodome

Usage examples:
    python -m gui.crypto_tool.issue_license --machine "ENCODED_OR_PLAIN_ID" \
        --days 365 --priv "gui/secretkey/private.pem" --out "license.dat" \
        --feature PRO_VERSION --feature EXPERIMENTAL

    # Read machine code from file
    python -m gui.crypto_tool.issue_license --machine-file ".\\machine.txt" --days 90 --debug
"""

from __future__ import annotations

import json
import datetime
import argparse
import sys
from pathlib import Path

try:
    from Crypto.PublicKey import RSA
    from Crypto.Signature import pkcs1_15
    from Crypto.Hash import SHA256
    _pycrypto_available = True
except Exception:
    RSA = None  # type: ignore
    pkcs1_15 = None  # type: ignore
    SHA256 = None  # type: ignore
    _pycrypto_available = False

SEPARATOR = b"---SIGNATURE_BELOW---"


def _require_pycrypto() -> None:
    """Ensure PyCryptodome (Crypto) is available or raise a friendly error."""
    if not _pycrypto_available:
        raise RuntimeError("PyCryptodome is not installed. Please run: pip install pycryptodome")


def load_private_key(private_key_path: Path, passphrase: str | None = None):
    """Load RSA private key from PEM file.

    Parameters
    ----------
    private_key_path : Path
        Path to the RSA private key (PEM format).
    passphrase : str | None
        If the PEM is encrypted, provide the passphrase.

    Returns
    -------
    Crypto.PublicKey.RSA.RsaKey
        Loaded private key object.

    Raises
    ------
    RuntimeError
        If PyCryptodome is missing or the key cannot be loaded.
    """
    _require_pycrypto()
    try:
        key_bytes = private_key_path.read_bytes()
        return RSA.import_key(key_bytes, passphrase=passphrase)
    except Exception as e:
        raise RuntimeError(f"Failed to load private key '{private_key_path}': {e}")


def build_license_data(hardware_id: str, duration_days: int, features: list[str], issuer: str | None = None) -> dict:
    """Construct license data with expiration and metadata.

    Parameters
    ----------
    hardware_id : str
        Machine code string provided by user.
    duration_days : int
        License duration in days.
    features : list[str]
        List of enabled feature flags in the license.
    issuer : str | None
        Optional issuer name.

    Returns
    -------
    dict
        License data dictionary ready for JSON serialization.
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    expires_at = now + datetime.timedelta(days=int(duration_days))
    data = {
        "hardware_id": hardware_id,
        "issued_at": now.isoformat(),
        "expires_at": expires_at.isoformat(),
        "features": features or [],
    }
    if issuer:
        data["issuer"] = issuer
    return data


def serialize_license(data: dict) -> bytes:
    """Serialize license data to canonical JSON bytes.

    Ensures deterministic formatting for signature and verification.

    Returns
    -------
    bytes
        UTF-8 encoded JSON with compact separators and sorted keys.
    """
    data_str = json.dumps(data, separators=(",", ":"), sort_keys=True, ensure_ascii=False)
    return data_str.encode("utf-8")


def sign_license(private_key, data_bytes: bytes) -> bytes:
    """Sign the license JSON bytes with RSA (PKCS#1 v1.5, SHA-256).

    Parameters
    ----------
    private_key : Crypto.PublicKey.RSA.RsaKey
        RSA private key object.
    data_bytes : bytes
        Canonical JSON bytes of the license data.

    Returns
    -------
    bytes
        Raw signature bytes.
    """
    _require_pycrypto()
    try:
        hash_obj = SHA256.new(data_bytes)
        signer = pkcs1_15.new(private_key)
        return signer.sign(hash_obj)
    except Exception as e:
        raise RuntimeError(f"Signing failed: {e}")


def write_license_file(output_path: Path, data_bytes: bytes, signature: bytes) -> None:
    """Write license data and signature to a file with a clear separator.

    File format:
        <json>\n---SIGNATURE_BELOW---\n<signature>
    """
    try:
        with output_path.open("wb") as f:
            f.write(data_bytes)
            f.write(b"\n" + SEPARATOR + b"\n")
            f.write(signature)
    except Exception as e:
        raise RuntimeError(f"Failed to write license file '{output_path}': {e}")


def _parse_features(args: argparse.Namespace) -> list[str]:
    """Parse features from CLI args supporting --feature multiple or --features CSV."""
    items: list[str] = []
    if getattr(args, "feature", None):
        items.extend(args.feature)
    if getattr(args, "features", None):
        # split by comma and strip
        for token in str(args.features).split(","):
            t = token.strip()
            if t:
                items.append(t)
    # de-duplicate preserving order
    seen = set()
    result: list[str] = []
    for x in items:
        if x not in seen:
            seen.add(x)
            result.append(x)
    return result


def main(argv: list[str] | None = None) -> int:
    """CLI entry to issue a license.dat via RSA signature.

    Parameters
    ----------
    argv : list[str] | None
        Command-line arguments; defaults to sys.argv[1:].

    Returns
    -------
    int
        Exit code (0 on success, non-zero on error).
    """
    parser = argparse.ArgumentParser(
        prog="issue_license",
        description="Issue license.dat by signing JSON with RSA private key",
    )
    parser.add_argument("--machine", type=str, help="User machine code string")
    parser.add_argument("--machine-file", type=str, help="Path to file containing machine code")
    parser.add_argument("--days", type=int, default=365, help="License duration in days (default: 365)")
    parser.add_argument("--issuer", type=str, help="Issuer name (optional)")
    parser.add_argument("--priv", type=str, help="Path to private.pem (PEM)")
    parser.add_argument("--pass", dest="passphrase", type=str, help="Private key passphrase (if encrypted)")
    parser.add_argument("--out", type=str, default="license.dat", help="Output license file path")
    parser.add_argument("--feature", action="append", help="Feature flag (can be repeated)")
    parser.add_argument("--features", type=str, help="Comma-separated feature flags")
    parser.add_argument("--debug", action="store_true", help="Verbose errors")

    args = parser.parse_args(argv)

    # Determine machine code
    machine_code = args.machine
    if not machine_code and args.machine_file:
        p = Path(args.machine_file)
        try:
            machine_code = p.read_text(encoding="utf-8", errors="ignore").strip()
        except Exception as e:
            print(f"[ERROR] Failed to read machine code file: {e}", file=sys.stderr)
            return 2
    if not machine_code:
        print("[ERROR] Please provide --machine or --machine-file", file=sys.stderr)
        return 2

    # Determine private key path (default to gui/secretkey/private.pem)
    default_priv = Path(__file__).resolve().parent.parent / "secretkey" / "private.pem"
    priv_path = Path(args.priv) if args.priv else default_priv

    # Build license data
    features = _parse_features(args)
    data = build_license_data(hardware_id=machine_code, duration_days=args.days, features=features, issuer=args.issuer)
    data_bytes = serialize_license(data)

    # Load key and sign
    try:
        priv = load_private_key(priv_path, passphrase=args.passphrase)
        signature = sign_license(priv, data_bytes)
    except Exception as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        if args.debug:
            import traceback
            traceback.print_exc()
        return 3

    # Write output
    out_path = Path(args.out)
    try:
        write_license_file(out_path, data_bytes, signature)
    except Exception as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        if args.debug:
            import traceback
            traceback.print_exc()
        return 4

    print(f"许可证 '{out_path}' 已生成，请将其发回给用户。")
    return 0


if __name__ == "__main__":
    sys.exit(main())