"""
secure_creds.py - keep the Infor Nexus login OUT of the code and out of plain-text files.

The login is stored in the Windows Credential Manager through the `keyring` package.
Windows encrypts it for the Windows user that saved it (DPAPI): another Windows user, or a
copy of the project folder on another PC, cannot read it.

  pip install keyring

  one-time setup (the password is typed hidden, it is never shown or written to a file):
      python secure_creds.py --setup
  check what is stored (the password is never printed):
      python secure_creds.py --check
  remove it:
      python secure_creds.py --delete

  in step1_login.py:
      import secure_creds
      USERNAME, PASSWORD = secure_creds.get_credentials()
"""
import argparse
import getpass
import os
import sys

SERVICE = "rpa_booking_sms_nexus"        # name of the entry in Windows Credential Manager
_USER_KEY = "__username__"               # entry that remembers which user name is stored
ENV_USER, ENV_PASS = "NEXUS_USER", "NEXUS_PASS"   # optional fallback (less safe, see below)


def _kr():
    try:
        import keyring
        return keyring
    except ImportError:
        raise RuntimeError("Missing package. Run:  pip install keyring")


def set_credentials(username: str, password: str) -> None:
    kr = _kr()
    kr.set_password(SERVICE, _USER_KEY, username)
    kr.set_password(SERVICE, username, password)


def delete_credentials() -> bool:
    kr = _kr()
    username = kr.get_password(SERVICE, _USER_KEY)
    if username is None:
        return False
    for key in (username, _USER_KEY):
        try:
            kr.delete_password(SERVICE, key)
        except Exception:
            pass
    return True


def get_credentials() -> tuple:
    """(username, password): Credential Manager first. The NEXUS_USER / NEXUS_PASS environment
    variables are only a fallback (e.g. a server); they are plain text for anyone who can read the
    process, so prefer --setup."""
    try:
        kr = _kr()
        username = kr.get_password(SERVICE, _USER_KEY)
        password = kr.get_password(SERVICE, username) if username else None
        if username and password:
            return username, password
    except RuntimeError:
        if not (os.getenv(ENV_USER) and os.getenv(ENV_PASS)):
            raise
    u, p = os.getenv(ENV_USER), os.getenv(ENV_PASS)
    if u and p:
        print("WARNING: login taken from environment variables (less safe). Run: python secure_creds.py --setup")
        return u, p
    raise RuntimeError("No Nexus login stored. Run once:  python secure_creds.py --setup")


def redact(text, password: str) -> str:
    """Use before printing / logging any text that could contain the password."""
    text = str(text)
    return text.replace(password, "********") if password else text


def _setup() -> int:
    username = input("Nexus user name: ").strip()
    if not username:
        print("No user name entered.")
        return 1
    password = getpass.getpass("Nexus password (typing is hidden): ")
    again = getpass.getpass("Type the password again: ")
    if not password or password != again:
        print("Passwords are empty or do not match - nothing saved.")
        return 1
    set_credentials(username, password)
    print(f"Saved for '{username}' in Windows Credential Manager (entry: {SERVICE}).")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Store the Nexus login encrypted (Windows Credential Manager)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--setup", action="store_true", help="save / replace the login")
    g.add_argument("--check", action="store_true", help="show which user is stored (no password)")
    g.add_argument("--delete", action="store_true", help="remove the stored login")
    a = ap.parse_args()
    try:
        if a.setup:
            return _setup()
        if a.delete:
            print("Deleted." if delete_credentials() else "Nothing stored.")
            return 0
        u, _ = get_credentials()
        print(f"Stored login found for user '{u}' (password hidden).")
        return 0
    except RuntimeError as e:
        print(e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
