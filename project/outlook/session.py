from __future__ import annotations


def create_outlook_namespace(*, outlook_profile: str | None = None):
    win32_client = _load_win32com_client_module()
    try:
        application = win32_client.Dispatch("Outlook.Application")
        namespace = application.GetNamespace("MAPI")
    except Exception as exc:  # pragma: no cover - exercised through unit fakes
        raise RuntimeError(f"Outlook application/session initialization failed: {exc}") from exc

    profile_name = (outlook_profile or "").strip()
    if not profile_name:
        return namespace

    try:
        namespace.Logon(Profile=profile_name, ShowDialog=False, NewSession=False)
        return namespace
    except Exception as exc:
        if _namespace_is_usable(namespace):
            return namespace
        raise RuntimeError(
            f"Outlook profile logon failed for '{profile_name}', and no usable existing session was available: {exc}"
        ) from exc


def _namespace_is_usable(namespace: object) -> bool:
    try:
        list(namespace.Folders)
    except Exception:
        return False
    return True


def _load_win32com_client_module():
    try:
        from win32com import client  # type: ignore
    except ImportError as exc:
        raise RuntimeError("pywin32 is required for live Outlook access.") from exc
    return client
