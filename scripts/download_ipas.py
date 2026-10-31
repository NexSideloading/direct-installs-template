
#!/usr/bin/env python3

import hashlib
import json
import os
import plistlib
import zipfile

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests


# ============================================================
# PATHS
# ============================================================

REPO_ROOT = Path(__file__).resolve().parent.parent

CONFIG_FILE = REPO_ROOT / "config.json"
STATE_FILE = REPO_ROOT / "ipa_state.json"
IPA_DIR = REPO_ROOT / "ipas"


# ============================================================
# CONFIGURATION
# ============================================================

USER_AGENT = "NexSideloading-Direct-Installs/1.0"

HEAD_TIMEOUT = 30
DOWNLOAD_TIMEOUT = (15, 180)

CHUNK_SIZE = 1024 * 1024


# ============================================================
# CONFIG LOADING
# ============================================================

def load_config() -> Dict[str, Any]:
    """Load repository configuration."""

    if not CONFIG_FILE.exists():
        raise FileNotFoundError(
            f"Configuration file not found: {CONFIG_FILE}"
        )

    with CONFIG_FILE.open("r", encoding="utf-8") as file:
        config = json.load(file)

    if not isinstance(config, dict):
        raise ValueError(
            "config.json must contain a JSON object"
        )

    return config


def get_ipa_sources(config: Dict[str, Any]) -> Dict[str, str]:
    """Extract and validate IPA sources from config.json."""

    sources = config.get("ipa_sources", {})

    if not isinstance(sources, dict):
        raise ValueError(
            "'ipa_sources' must be a JSON object"
        )

    validated_sources = {}

    for name, url in sources.items():

        if (
            not isinstance(name, str)
            or not name
            or name in (".", "..")
            or "/" in name
            or "\\" in name
        ):
            raise ValueError(
                f"Invalid IPA application name: {name!r}"
            )

        if (
            not isinstance(url, str)
            or not url.startswith(("http://", "https://"))
        ):
            raise ValueError(
                f"Invalid IPA source URL for {name!r}: {url!r}"
            )

        validated_sources[name] = url

    return validated_sources


# ============================================================
# STATE MANAGEMENT
# ============================================================

def load_state() -> Dict[str, Any]:
    """Load previously downloaded IPA state."""

    if not STATE_FILE.exists():
        return {}

    try:
        with STATE_FILE.open("r", encoding="utf-8") as file:
            state = json.load(file)

        if isinstance(state, dict):
            return state

        print("Warning: Invalid IPA state format. Rebuilding state.")

    except (OSError, json.JSONDecodeError) as exc:
        print(f"Warning: Failed to load IPA state: {exc}")

    return {}


def save_state(state: Dict[str, Any]) -> None:
    """Save IPA state atomically."""

    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

    temp_file = STATE_FILE.with_suffix(".json.tmp")

    try:
        with temp_file.open("w", encoding="utf-8") as file:
            json.dump(state, file, indent=2)
            file.write("\n")

        os.replace(temp_file, STATE_FILE)

    finally:
        temp_file.unlink(missing_ok=True)


# ============================================================
# FILE HASHING
# ============================================================

def get_file_hash(filepath: Path) -> str:
    """Calculate SHA-256 hash of a file."""

    sha256 = hashlib.sha256()

    with filepath.open("rb") as file:

        for chunk in iter(
            lambda: file.read(CHUNK_SIZE),
            b"",
        ):
            sha256.update(chunk)

    return sha256.hexdigest()


# ============================================================
# REMOTE METADATA
# ============================================================

def get_remote_metadata(
    name: str,
    url: str,
) -> Tuple[Optional[Dict[str, Optional[str]]], bool]:
    """
    Retrieve remote file metadata using HTTP HEAD.

    Returns:
        (metadata, not_found)

    Metadata:
        etag
        last_modified
        content_length
    """

    headers = {
        "User-Agent": USER_AGENT,
    }

    try:
        with requests.head(
            url,
            headers=headers,
            allow_redirects=True,
            timeout=HEAD_TIMEOUT,
        ) as response:

            if response.status_code == 404:
                print(f"  {name}: Remote IPA returned 404.")
                return None, True

            if response.status_code in (405, 501):
                print(f"  {name}: HEAD unsupported. Falling back to GET.")
                return None, False

            response.raise_for_status()

            return {
                "etag": response.headers.get("ETag"),
                "last_modified": response.headers.get("Last-Modified"),
                "content_length": response.headers.get("Content-Length"),
            }, False

    except requests.exceptions.RequestException as exc:

        print(f"  {name}: HEAD request failed: {exc}")
        print(f"  {name}: Will verify by downloading the IPA.")

        return None, False


def remote_matches_saved_state(
    remote: Optional[Dict[str, Optional[str]]],
    saved: Dict[str, Any],
    local_path: Path,
) -> bool:
    """
    Determine whether the remote IPA is unchanged.

    The local SHA-256 must match saved state, and the
    remote HTTP validators must also match saved metadata.
    """

    if not remote or not local_path.is_file():
        return False

    saved_hash = saved.get("hash")

    if not saved_hash:
        return False

    try:
        local_hash = get_file_hash(local_path)

        if local_hash != saved_hash:
            return False

    except OSError:
        return False

    # Check remote file size when available.
    remote_size = remote.get("content_length")

    if remote_size is not None:

        try:
            if local_path.stat().st_size != int(remote_size):
                return False

        except (ValueError, OSError):
            return False

    # Prefer ETag.
    remote_etag = remote.get("etag")
    saved_etag = saved.get("etag")

    if remote_etag and saved_etag:
        return remote_etag == saved_etag

    # Fall back to Last-Modified and Content-Length.
    remote_modified = remote.get("last_modified")
    saved_modified = saved.get("last_modified")
    saved_size = saved.get("content_length")

    if (
        remote_modified
        and saved_modified
        and remote_size
        and saved_size
    ):
        return (
            remote_modified == saved_modified
            and str(remote_size) == str(saved_size)
        )

    # No reliable validators available.
    # Download and compare actual hashes instead.
    return False


# ============================================================
# IPA VALIDATION
# ============================================================

def validate_ipa(filepath: Path) -> bool:
    """Validate that a downloaded file is a genuine IPA archive."""

    try:
        with zipfile.ZipFile(filepath, "r") as archive:

            names = archive.namelist()

            # Find the application's Info.plist.
            info_plist_path = next(
                (
                    name
                    for name in names
                    if name.startswith("Payload/")
                    and name.endswith(".app/Info.plist")
                ),
                None,
            )

            if not info_plist_path:
                print(
                    "  Invalid IPA: "
                    "Payload/*.app/Info.plist not found."
                )
                return False

            # Validate the property list.
            plist_data = archive.read(info_plist_path)

            plist = plistlib.loads(plist_data)

            if not isinstance(plist, dict):
                print("  Invalid IPA: Info.plist is not a dictionary.")
                return False

            # Verify archive integrity.
            damaged_file = archive.testzip()

            if damaged_file is not None:
                print(
                    f"  Invalid IPA: Corrupted archive member: "
                    f"{damaged_file}"
                )
                return False

        if filepath.stat().st_size == 0:
            print("  Invalid IPA: Empty file.")
            return False

        return True

    except (
        OSError,
        zipfile.BadZipFile,
        KeyError,
        plistlib.InvalidFileException,
        ValueError,
    ) as exc:

        print(f"  IPA validation failed: {exc}")
        return False


# ============================================================
# IPA DOWNLOAD
# ============================================================

def download_ipa(
    name: str,
    url: str,
    output_dir: Path,
) -> Tuple[Optional[Path], Dict[str, Optional[str]]]:
    """
    Download an IPA to a temporary file.

    Never replaces the existing IPA until the new download
    has been completely downloaded and validated.
    """

    temp_path = output_dir / (
        f".{name}.{os.getpid()}.ipa.tmp"
    )

    headers = {
        "User-Agent": USER_AGENT,
    }

    temp_path.unlink(missing_ok=True)

    try:
        print(f"  Downloading {name}...")

        with requests.get(
            url,
            headers=headers,
            stream=True,
            allow_redirects=True,
            timeout=DOWNLOAD_TIMEOUT,
        ) as response:

            if response.status_code == 404:
                print(f"  {name}: Remote IPA returned 404.")
                return None, {}

            response.raise_for_status()

            content_type = response.headers.get(
                "Content-Type",
                "",
            ).lower()

            invalid_types = (
                "text/",
                "application/json",
                "application/xml",
            )

            if content_type.startswith(invalid_types):
                print(
                    f"  {name}: Expected an IPA, received "
                    f"{content_type}."
                )
                return None, {}

            metadata = {
                "etag": response.headers.get("ETag"),
                "last_modified": response.headers.get("Last-Modified"),
                "content_length": response.headers.get("Content-Length"),
            }

            downloaded_bytes = 0

            with temp_path.open("wb") as file:

                for chunk in response.iter_content(
                    chunk_size=CHUNK_SIZE
                ):

                    if not chunk:
                        continue

                    file.write(chunk)

                    downloaded_bytes += len(chunk)

        if not temp_path.exists() or temp_path.stat().st_size == 0:
            print(f"  {name}: Downloaded file is empty.")
            return None, {}

        # Validate downloaded archive.
        if not validate_ipa(temp_path):
            print(f"  {name}: Invalid IPA. Existing file preserved.")
            return None, {}

        print(
            f"  {name}: Download successful "
            f"({downloaded_bytes:,} bytes)."
        )

        return temp_path, metadata

    except requests.exceptions.Timeout:
        print(f"  {name}: Download timed out.")

    except requests.exceptions.RequestException as exc:
        print(f"  {name}: Download failed: {exc}")

    except OSError as exc:
        print(f"  {name}: File operation failed: {exc}")

    except Exception as exc:
        print(f"  {name}: Unexpected error: {exc}")

    temp_path.unlink(missing_ok=True)

    return None, {}


# ============================================================
# MAIN
# ============================================================

def main() -> List[str]:
    """
    Check configured IPA sources for updates.

    Returns:
        List of successfully changed IPA application names.

    This return value is consumed by run_all.py to determine
    which applications need to be re-signed.
    """

    # Prepare directories.
    IPA_DIR.mkdir(parents=True, exist_ok=True)

    # Load configuration and saved state.
    config = load_config()

    ipa_sources = get_ipa_sources(config)

    state = load_state()

    changed_ipas: List[str] = []

    print("Checking upstream IPA sources for updates...")
    print("=" * 60)

    for name, url in ipa_sources.items():

        print(f"\nChecking {name}...")

        output_path = IPA_DIR / f"{name}.ipa"

        previous_state = state.get(name, {})

        if not isinstance(previous_state, dict):
            previous_state = {}

        # ----------------------------------------------------
        # 1. Check remote metadata.
        # ----------------------------------------------------

        remote_metadata, not_found = get_remote_metadata(
            name,
            url,
        )

        if not_found:
            print(f"  {name}: Keeping existing IPA.")
            continue

        # ----------------------------------------------------
        # 2. Check if remote asset is unchanged.
        # ----------------------------------------------------

        if remote_matches_saved_state(
            remote_metadata,
            previous_state,
            output_path,
        ):

            print(f"  {name}: Already up to date.")
            continue

        print(f"  {name}: Remote asset needs verification.")

        # ----------------------------------------------------
        # 3. Hash existing local IPA.
        # ----------------------------------------------------

        existing_hash = None

        if output_path.is_file():

            try:
                existing_hash = get_file_hash(output_path)

            except OSError as exc:
                print(f"  {name}: Could not hash existing IPA: {exc}")

        # ----------------------------------------------------
        # 4. Download latest IPA.
        # ----------------------------------------------------

        temp_path, download_metadata = download_ipa(
            name,
            url,
            IPA_DIR,
        )

        if temp_path is None:

            print(
                f"  {name}: Download unsuccessful. "
                "Existing IPA preserved."
            )

            continue

        try:

            # ------------------------------------------------
            # 5. Compare actual hashes.
            # ------------------------------------------------

            downloaded_hash = get_file_hash(temp_path)

            old_state_hash = previous_state.get("hash")

            file_changed = downloaded_hash != existing_hash

            # Detect cases where the IPA was previously replaced
            # but the state file was not successfully updated.
            state_outdated = (
                old_state_hash is not None
                and downloaded_hash != old_state_hash
            )

            # ------------------------------------------------
            # 6. Replace existing IPA if different.
            # ------------------------------------------------

            if file_changed:

                os.replace(
                    temp_path,
                    output_path,
                )

                print(
                    f"  {name}: Updated successfully "
                    f"({output_path.stat().st_size:,} bytes)."
                )

            else:

                temp_path.unlink(missing_ok=True)

                print(
                    f"  {name}: Downloaded IPA is identical. "
                    "No replacement needed."
                )

            # ------------------------------------------------
            # 7. Update saved state.
            # ------------------------------------------------

            etag = (
                download_metadata.get("etag")
                or (remote_metadata or {}).get("etag")
            )

            last_modified = (
                download_metadata.get("last_modified")
                or (remote_metadata or {}).get("last_modified")
            )

            content_length = (
                download_metadata.get("content_length")
                or (remote_metadata or {}).get("content_length")
                or str(output_path.stat().st_size)
            )

            state[name] = {
                "hash": downloaded_hash,
                "last_updated": str(output_path.stat().st_mtime),
                "etag": etag,
                "last_modified": last_modified,
                "content_length": content_length,
            }

            # ------------------------------------------------
            # 8. Tell run_all.py what needs re-signing.
            # ------------------------------------------------

            if file_changed or state_outdated:

                changed_ipas.append(name)

                print(
                    f"  {name}: Added to re-signing queue."
                )

        except OSError as exc:

            print(
                f"  {name}: Failed to process downloaded IPA: {exc}"
            )

            temp_path.unlink(missing_ok=True)

    # ========================================================
    # SAVE STATE
    # ========================================================

    save_state(state)

    # ========================================================
    # SUMMARY
    # ========================================================

    print("\n" + "=" * 60)

    print(
        f"IPA check completed. "
        f"Changed IPAs: {len(changed_ipas)}"
    )

    if changed_ipas:

        print("\nApplications requiring re-signing:")

        for name in changed_ipas:
            print(f"  - {name}")

    else:

        print("\nAll configured IPAs are up to date.")

    # Keep compatibility with run_all.py.
    return changed_ipas


if __name__ == "__main__":
    main()
