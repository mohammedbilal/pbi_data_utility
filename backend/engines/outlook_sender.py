"""Outlook COM email sender.

Delivery for the email-generation feature. Because the manual-upload flow only
accepts ``.msg`` and there is no monitored mailbox yet, we send through the
locally-installed **Outlook Classic** profile: the mail lands in the recipient's
inbox, from where the user saves it as ``.msg`` and uploads it to the app. When
a dedicated mailbox is created later, only the recipient in Settings changes.

Notes / gotchas:
* Runs on the engine's background thread, so COM must be initialised per-thread
  via ``pythoncom.CoInitialize()`` (and uninitialised in ``finally``).
* Requires ``pywin32`` and a configured Outlook Classic profile on the machine
  running the utility. If either is missing, ``send_via_outlook`` raises
  ``OutlookUnavailable`` with a clear message the engine surfaces as a log line.
* A locked-down Outlook may show a "a program is trying to send mail on your
  behalf" guard prompt the first time; the user clicks Allow.
"""
from __future__ import annotations

import os
from typing import Optional

_OL_MAIL_ITEM = 0   # olMailItem
_OL_SAVE_MSG = 3    # olMSG


class OutlookUnavailable(RuntimeError):
    """Outlook / pywin32 not available on this machine."""


def outlook_available() -> bool:
    try:
        import win32com.client  # noqa: F401
        import pythoncom  # noqa: F401
        return True
    except Exception:
        return False


def send_via_outlook(subject: str, html_body: str, recipient: str,
                     save_dir: Optional[str] = None) -> str:
    """Create a mail item, optionally save a .msg copy, then Send it.

    Returns a short status string for logging. Raises OutlookUnavailable if the
    COM stack is missing, or RuntimeError on send failure.
    """
    if not recipient:
        raise RuntimeError("No email recipient configured (Settings → Email).")

    try:
        import pythoncom
        import win32com.client
    except Exception as exc:  # pragma: no cover - environment dependent
        raise OutlookUnavailable(
            "Outlook automation unavailable (pywin32 not installed). "
            f"Details: {exc}"
        )

    pythoncom.CoInitialize()
    try:
        try:
            outlook = win32com.client.Dispatch("Outlook.Application")
        except Exception as exc:
            raise OutlookUnavailable(
                "Could not start Outlook. Ensure Outlook Classic is installed "
                f"and a mail profile is configured. Details: {exc}"
            )

        mail = outlook.CreateItem(_OL_MAIL_ITEM)
        mail.To = recipient
        mail.Subject = subject
        mail.HTMLBody = html_body

        saved_note = ""
        if save_dir:
            try:
                os.makedirs(save_dir, exist_ok=True)
                safe = "".join(c if c.isalnum() or c in " -_." else "_"
                               for c in subject)[:80].strip() or "email"
                path = os.path.join(save_dir, f"{safe}.msg")
                mail.SaveAs(path, _OL_SAVE_MSG)
                saved_note = f" (copy saved: {path})"
            except Exception as exc:
                saved_note = f" (save copy failed: {exc})"

        mail.Send()
        return f"sent to {recipient}{saved_note}"
    finally:
        pythoncom.CoUninitialize()
