from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import unquote


_WHITESPACE_RE = re.compile(r"\s+")
_BACK_LINK_TEXT = "Back"
_INLAND_BTB_SEARCH_LINK_TEXT = "Inland BTB LC/Contract Search/Edit"
_LOGIN_PATH_FRAGMENT = "/ords/oims/r/import/login"


def normalize_dashboard_search_key(value: str) -> str:
    return _WHITESPACE_RE.sub(" ", str(value).strip())


@dataclass(slots=True, frozen=True)
class DashboardFamilySnapshot:
    beneficiary_name: str
    irc_details: str
    erc_details: str
    lc_date: str
    last_date_of_shipment: str
    lc_expiry_date: str
    lc_value: str
    foreign_lc_numbers: list[str]
    commodity_quantities: list[str]
    source_url: str | None = None


@dataclass(slots=True, frozen=True)
class DashboardLookupAttempt:
    search_key: str
    outcome: str
    message: str | None = None


@dataclass(slots=True, frozen=True)
class DashboardLookupResult:
    outcome: str
    attempts: list[DashboardLookupAttempt]
    matched_search_key: str | None = None
    snapshot: DashboardFamilySnapshot | None = None
    message: str | None = None


class DashboardLookupProvider(Protocol):
    def lookup_family(self, *, search_keys: list[str]) -> DashboardLookupResult:
        """Resolve dashboard data for the first matching search key."""

    def close(self) -> None:
        """Release any provider resources."""


@dataclass(slots=True, frozen=True)
class EmptyDashboardLookupProvider:
    def lookup_family(self, *, search_keys: list[str]) -> DashboardLookupResult:
        raise ValueError("Dashboard verification requires --dashboard-json or --live-dashboard")

    def close(self) -> None:
        return None


@dataclass(slots=True, frozen=True)
class JsonManifestDashboardLookupProvider:
    manifest_path: Path
    _cached_records: dict[str, dict[str, object]] | None = None

    def lookup_family(self, *, search_keys: list[str]) -> DashboardLookupResult:
        records = self._load_manifest()
        attempts: list[DashboardLookupAttempt] = []
        for raw_key in search_keys:
            search_key = normalize_dashboard_search_key(raw_key)
            record = records.get(search_key)
            if record is None:
                attempts.append(
                    DashboardLookupAttempt(
                        search_key=search_key,
                        outcome="no_result",
                    )
                )
                continue

            outcome = str(record.get("outcome", "resolved")).strip() or "resolved"
            message = _optional_string(record.get("message"))
            if outcome == "no_result":
                attempts.append(DashboardLookupAttempt(search_key=search_key, outcome=outcome, message=message))
                continue
            if outcome in {"incomplete_data", "fetch_error"}:
                attempts.append(DashboardLookupAttempt(search_key=search_key, outcome=outcome, message=message))
                return DashboardLookupResult(
                    outcome=outcome,
                    attempts=attempts,
                    matched_search_key=search_key,
                    message=message,
                )
            if outcome != "resolved":
                raise ValueError(f"Unsupported dashboard manifest outcome '{outcome}' for search key {search_key}")

            attempts.append(DashboardLookupAttempt(search_key=search_key, outcome="resolved", message=message))
            return DashboardLookupResult(
                outcome="resolved",
                attempts=attempts,
                matched_search_key=search_key,
                snapshot=_build_snapshot_from_manifest_record(record),
                message=message,
            )

        return DashboardLookupResult(
            outcome="no_result",
            attempts=attempts,
            matched_search_key=attempts[-1].search_key if attempts else None,
            message="No dashboard manifest record matched the configured search keys.",
        )

    def close(self) -> None:
        return None

    def _load_manifest(self) -> dict[str, dict[str, object]]:
        if self._cached_records is not None:
            return self._cached_records

        with self.manifest_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, list):
            raise ValueError("Dashboard lookup manifest must be a JSON array")

        records: dict[str, dict[str, object]] = {}
        for index, item in enumerate(payload):
            if not isinstance(item, dict):
                raise ValueError(f"Dashboard lookup manifest row {index} must be an object")
            search_key = normalize_dashboard_search_key(_require_string(item, "search_key", index))
            records[search_key] = item
        object.__setattr__(self, "_cached_records", records)
        return records


@dataclass(slots=True)
class PlaywrightDashboardLookupProvider:
    login_url: str
    username: str | None
    password: str | None
    username_selector: str | None
    password_selector: str | None
    submit_selector: str | None
    post_login_wait_selector: str | None
    search_input_selector: str
    search_button_selector: str
    detail_ready_selector: str | None
    no_result_selector: str | None
    beneficiary_selector: str
    irc_selector: str
    erc_selector: str
    lc_date_selector: str
    last_date_of_shipment_selector: str
    lc_expiry_date_selector: str
    lc_value_selector: str
    foreign_lc_selector: str
    quantity_selector: str
    browser_channel: str | None = None
    storage_state_path: Path | None = None
    timeout_ms: int = 120_000
    headless: bool = True
    _playwright_manager: object | None = None
    _browser: object | None = None
    _context: object | None = None
    _page: object | None = None
    _page_dirty: bool = False
    _session_failure_message: str | None = None

    def lookup_family(self, *, search_keys: list[str]) -> DashboardLookupResult:
        if self._session_failure_message is not None:
            return _build_terminal_fetch_error_result(
                search_keys=search_keys,
                message=self._session_failure_message,
            )
        self._ensure_authenticated_page()
        if self._page is None:
            raise ValueError("Dashboard page could not be initialized.")

        if self._page_dirty:
            self._reset_to_fresh_search_page()

        attempts: list[DashboardLookupAttempt] = []
        for raw_key in search_keys:
            search_key = normalize_dashboard_search_key(raw_key)
            for retry_index in range(2):
                try:
                    page = self._page
                    if page is None:
                        raise ValueError("Dashboard page could not be initialized.")
                    if self._page_is_login_page(page):
                        self._recover_dashboard_session(page=page, retry_index=retry_index)
                        continue

                    input_locator = page.locator(self.search_input_selector)
                    input_locator.wait_for(state="visible", timeout=self.timeout_ms)
                    input_locator.click()
                    input_locator.fill("")
                    input_locator.fill(search_key)
                    page.locator(self.search_button_selector).click()
                    self._page_dirty = True
                    _best_effort_wait_for_network_idle(page, timeout_ms=self.timeout_ms)

                    if self._page_is_login_page(page):
                        self._recover_dashboard_session(page=page, retry_index=retry_index)
                        continue

                    if self.no_result_selector and _selector_visible(
                        page,
                        self.no_result_selector,
                        timeout_ms=min(self.timeout_ms, 5_000),
                    ):
                        attempts.append(DashboardLookupAttempt(search_key=search_key, outcome="no_result"))
                        break

                    if self.detail_ready_selector and not _selector_visible(
                        page,
                        self.detail_ready_selector,
                        timeout_ms=self.timeout_ms,
                    ):
                        if self.no_result_selector and _selector_visible(
                            page,
                            self.no_result_selector,
                            timeout_ms=min(self.timeout_ms, 5_000),
                        ):
                            attempts.append(DashboardLookupAttempt(search_key=search_key, outcome="no_result"))
                            break
                        snapshot = self._read_snapshot(page)
                        if _snapshot_is_empty(snapshot):
                            attempts.append(DashboardLookupAttempt(search_key=search_key, outcome="no_result"))
                            break
                        attempts.append(
                            DashboardLookupAttempt(
                                search_key=search_key,
                                outcome="resolved",
                                message=(
                                    f"Dashboard detail view readiness selector did not become visible for '{search_key}', "
                                    "but a non-empty dashboard snapshot was captured."
                                ),
                            )
                        )
                        return DashboardLookupResult(
                            outcome="resolved",
                            attempts=attempts,
                            matched_search_key=search_key,
                            snapshot=snapshot,
                            message=None,
                        )

                    snapshot = self._read_snapshot(page)
                    if _snapshot_is_empty(snapshot):
                        attempts.append(DashboardLookupAttempt(search_key=search_key, outcome="no_result"))
                        break

                    attempts.append(DashboardLookupAttempt(search_key=search_key, outcome="resolved"))
                    return DashboardLookupResult(
                        outcome="resolved",
                        attempts=attempts,
                        matched_search_key=search_key,
                        snapshot=snapshot,
                    )
                except Exception as exc:
                    error_message = str(exc)
                    if self._page is not None and self._page_is_login_page(self._page):
                        try:
                            self._recover_dashboard_session(
                                page=self._page,
                                retry_index=retry_index,
                                fallback_error_message=error_message,
                            )
                        except Exception as recovery_exc:
                            recovery_message = str(recovery_exc)
                            if self._session_failure_message is None:
                                error_message = self._mark_terminal_session_failure(
                                    page=self._page,
                                    fallback_error_message=recovery_message,
                                )
                            else:
                                error_message = self._session_failure_message
                        else:
                            continue
                    attempts.append(
                        DashboardLookupAttempt(
                            search_key=search_key,
                            outcome="fetch_error",
                            message=error_message,
                        )
                    )
                    return DashboardLookupResult(
                        outcome="fetch_error",
                        attempts=attempts,
                        matched_search_key=search_key,
                        message=error_message,
                    )

        return DashboardLookupResult(
            outcome="no_result",
            attempts=attempts,
            matched_search_key=attempts[-1].search_key if attempts else None,
            message="No dashboard result matched any configured search key.",
        )

    def close(self) -> None:
        if self._page is not None:
            try:
                self._page.close()
            except Exception:
                pass
            finally:
                self._page = None
        if self._context is not None:
            try:
                self._context.close()
            except Exception:
                pass
            finally:
                self._context = None
        if self._browser is not None:
            try:
                self._browser.close()
            except Exception:
                pass
            finally:
                self._browser = None
        if self._playwright_manager is not None:
            try:
                self._playwright_manager.__exit__(None, None, None)
            except Exception:
                pass
            finally:
                self._playwright_manager = None
        self._page_dirty = False

    def _ensure_authenticated_page(self) -> None:
        if self._page is not None:
            return
        sync_playwright = _load_playwright_sync_api()
        playwright_manager = sync_playwright()
        playwright = playwright_manager.__enter__()
        try:
            browser_launch_kwargs: dict[str, object] = {"headless": self.headless}
            if self.browser_channel:
                browser_launch_kwargs["channel"] = self.browser_channel
            browser = playwright.chromium.launch(**browser_launch_kwargs)
            context = browser.new_context(**self._build_context_kwargs())
            page = context.new_page()
            self._reestablish_authenticated_search_page(page)
            self._browser = browser
            self._context = context
            self._page = page
            self._playwright_manager = playwright_manager
            self._page_dirty = False
        except Exception:
            try:
                page.close()
            except Exception:
                pass
            try:
                context.close()
            except Exception:
                pass
            try:
                browser.close()
            except Exception:
                pass
            try:
                playwright_manager.__exit__(None, None, None)
            except Exception:
                pass
            raise

    def _build_context_kwargs(self) -> dict[str, object]:
        context_kwargs: dict[str, object] = {}
        if self.storage_state_path is not None:
            if not self.storage_state_path.exists():
                raise ValueError(f"Playwright storage state path does not exist: {self.storage_state_path}")
            context_kwargs["storage_state"] = str(self.storage_state_path)
        return context_kwargs

    def _reset_to_fresh_search_page(self) -> None:
        if self._page is None:
            raise ValueError("Dashboard page could not be initialized.")
        page = self._page
        if self._page_is_login_page(page):
            self._recover_dashboard_session(page=page, retry_index=0)
            return
        page.get_by_text(_BACK_LINK_TEXT, exact=True).click()
        page.wait_for_url("**/350?session=*", timeout=self.timeout_ms)
        _best_effort_wait_for_network_idle(page, timeout_ms=self.timeout_ms)
        page.get_by_text(_INLAND_BTB_SEARCH_LINK_TEXT, exact=True).click()
        page.wait_for_url("**/75?clear=75**", timeout=self.timeout_ms)
        _best_effort_wait_for_network_idle(page, timeout_ms=self.timeout_ms)
        page.locator(self.search_input_selector).wait_for(state="visible", timeout=self.timeout_ms)
        self._assert_fresh_search_page_blank(page)
        self._page_dirty = False

    def _assert_fresh_search_page_blank(self, page) -> None:
        search_value = _read_text(page, self.search_input_selector)
        snapshot = self._read_snapshot(page)
        if not search_value and _snapshot_is_empty(snapshot):
            return

        raise ValueError(
            "Dashboard reset did not return a blank search page before the next lookup."
        )

    def _recover_dashboard_session(
        self,
        *,
        page,
        retry_index: int,
        fallback_error_message: str | None = None,
    ) -> None:
        if retry_index > 0:
            raise ValueError(
                self._mark_terminal_session_failure(
                    page=page,
                    fallback_error_message=fallback_error_message,
                )
            )
        self._reestablish_authenticated_search_page(page)
        self._page_dirty = False

    def _reestablish_authenticated_search_page(self, page) -> None:
        page.goto(self.login_url, wait_until="domcontentloaded", timeout=self.timeout_ms)
        _best_effort_wait_for_network_idle(page, timeout_ms=self.timeout_ms)
        if not _selector_visible(page, self.search_input_selector):
            self._perform_login(page)
            self._raise_if_terminal_auth_failure(page)
        if self.post_login_wait_selector:
            page.locator(self.post_login_wait_selector).wait_for(state="visible", timeout=self.timeout_ms)
        elif not _selector_visible(page, self.search_input_selector, timeout_ms=self.timeout_ms):
            raise ValueError("Dashboard login did not reach a searchable authenticated page.")
        post_login_redirect_url = page.url
        page.goto(post_login_redirect_url, wait_until="domcontentloaded", timeout=self.timeout_ms)
        _best_effort_wait_for_network_idle(page, timeout_ms=self.timeout_ms)
        page.locator(self.search_input_selector).wait_for(state="visible", timeout=self.timeout_ms)
        self._assert_fresh_search_page_blank(page)

    def _raise_if_terminal_auth_failure(self, page) -> None:
        terminal_message = _extract_terminal_auth_failure_message(page)
        if terminal_message is None:
            return
        raise ValueError(self._mark_terminal_session_failure(message=terminal_message))

    def _mark_terminal_session_failure(
        self,
        *,
        page=None,
        fallback_error_message: str | None = None,
        message: str | None = None,
    ) -> str:
        terminal_message = message or _extract_terminal_auth_failure_message(page, fallback_text=fallback_error_message)
        if terminal_message is None:
            terminal_message = (
                "Bangladesh Bank dashboard session was redirected to login during lookup and could not be recovered."
            )
        self._session_failure_message = terminal_message
        return terminal_message

    def _page_is_login_page(self, page) -> bool:
        return _LOGIN_PATH_FRAGMENT in _optional_string(getattr(page, "url", ""))

    def _read_snapshot(self, page) -> DashboardFamilySnapshot:
        return DashboardFamilySnapshot(
            beneficiary_name=_read_text(page, self.beneficiary_selector),
            irc_details=_read_text(page, self.irc_selector),
            erc_details=_read_text(page, self.erc_selector),
            lc_date=_read_text(page, self.lc_date_selector),
            last_date_of_shipment=_read_text(page, self.last_date_of_shipment_selector),
            lc_expiry_date=_read_text(page, self.lc_expiry_date_selector),
            lc_value=_read_text(page, self.lc_value_selector),
            foreign_lc_numbers=_read_all_texts(page, self.foreign_lc_selector),
            commodity_quantities=_read_all_texts(page, self.quantity_selector),
            source_url=page.url,
        )

    def _perform_login(self, page) -> None:
        if not all(
            [
                self.username,
                self.password,
                self.username_selector,
                self.password_selector,
                self.submit_selector,
            ]
        ):
            raise ValueError(
                "Live dashboard login requires username/password credentials and selectors unless the configured storage state is already authenticated."
            )
        page.locator(self.username_selector).wait_for(state="visible", timeout=self.timeout_ms)
        page.locator(self.username_selector).fill(self.username or "")
        page.locator(self.password_selector).fill(self.password or "")
        page.locator(self.submit_selector).click()
        _best_effort_wait_for_network_idle(page, timeout_ms=self.timeout_ms)


def _build_snapshot_from_manifest_record(record: dict[str, object]) -> DashboardFamilySnapshot:
    return DashboardFamilySnapshot(
        beneficiary_name=_optional_string(record.get("beneficiary_name")),
        irc_details=_optional_string(record.get("irc_details")),
        erc_details=_optional_string(record.get("erc_details")),
        lc_date=_optional_string(record.get("lc_date")),
        last_date_of_shipment=_optional_string(record.get("last_date_of_shipment")),
        lc_expiry_date=_optional_string(record.get("lc_expiry_date")),
        lc_value=_optional_string(record.get("lc_value")),
        foreign_lc_numbers=_coerce_string_list(record.get("foreign_lc_numbers")),
        commodity_quantities=_coerce_string_list(record.get("commodity_quantities")),
        source_url=_optional_string(record.get("source_url")) or None,
    )


def _coerce_string_list(value: object) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("Dashboard manifest list fields must be arrays of strings")
    return [_optional_string(item) for item in value if _optional_string(item)]


def _snapshot_is_empty(snapshot: DashboardFamilySnapshot) -> bool:
    return not any(
        [
            snapshot.beneficiary_name,
            snapshot.irc_details,
            snapshot.erc_details,
            snapshot.lc_date,
            snapshot.last_date_of_shipment,
            snapshot.lc_expiry_date,
            snapshot.lc_value,
            snapshot.foreign_lc_numbers,
            snapshot.commodity_quantities,
        ]
    )


def _optional_string(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _require_string(item: dict[str, object], key: str, index: int) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Dashboard lookup manifest row {index} is missing non-empty '{key}'")
    return value


def _load_playwright_sync_api():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise ValueError("Playwright is required for live dashboard lookup") from exc
    return sync_playwright


def _best_effort_wait_for_network_idle(page, *, timeout_ms: int) -> None:
    try:
        page.wait_for_load_state("networkidle", timeout=timeout_ms)
    except Exception:
        return None


def _selector_visible(page, selector: str, *, timeout_ms: int = 2_000) -> bool:
    locator = page.locator(selector)
    if locator.count() == 0:
        return False
    try:
        locator.first.wait_for(state="visible", timeout=timeout_ms)
        return locator.first.is_visible()
    except Exception:
        return False


def _read_text(page, selector: str) -> str:
    locator = page.locator(selector)
    locator.first.wait_for(state="attached", timeout=5_000)
    try:
        tag_name = _optional_string(locator.first.evaluate("node => node.tagName")).upper()
    except Exception:
        tag_name = ""

    if tag_name in {"INPUT", "TEXTAREA", "SELECT"}:
        try:
            value = locator.first.input_value()
        except Exception:
            value = ""
        normalized_value = _optional_string(value)
        if normalized_value:
            return normalized_value

        if tag_name == "SELECT":
            try:
                selected_text = locator.first.evaluate(
                    "node => node.options[node.selectedIndex]?.text || ''"
                )
            except Exception:
                selected_text = ""
            normalized_selected_text = _optional_string(selected_text)
            if normalized_selected_text:
                return normalized_selected_text

    locator.first.wait_for(state="visible", timeout=5_000)
    text = locator.first.text_content()
    return _optional_string(text)


def _read_all_texts(page, selector: str) -> list[str]:
    locator = page.locator(selector)
    if locator.count() == 0:
        return []
    return [
        normalized
        for normalized in (_optional_string(text) for text in locator.all_inner_texts())
        if normalized
    ]


def _build_terminal_fetch_error_result(*, search_keys: list[str], message: str) -> DashboardLookupResult:
    normalized_keys = [
        normalize_dashboard_search_key(search_key)
        for search_key in search_keys
        if normalize_dashboard_search_key(search_key)
    ]
    matched_search_key = normalized_keys[0] if normalized_keys else None
    attempts = [
        DashboardLookupAttempt(
            search_key=search_key,
            outcome="fetch_error",
            message=message,
        )
        for search_key in normalized_keys
    ]
    return DashboardLookupResult(
        outcome="fetch_error",
        attempts=attempts,
        matched_search_key=matched_search_key,
        message=message,
    )


def _extract_terminal_auth_failure_message(page, *, fallback_text: str | None = None) -> str | None:
    candidates: list[str] = []
    page_url = _optional_string(getattr(page, "url", "")) if page is not None else ""
    if page_url:
        candidates.append(page_url)
    if fallback_text:
        candidates.append(fallback_text)

    for candidate in candidates:
        normalized = _decode_notification_text(candidate)
        if not normalized:
            continue
        lowered = normalized.casefold()
        if "the account is locked" in lowered:
            return "Bangladesh Bank dashboard account is locked."
        if "login attempt has been blocked" in lowered:
            return "Bangladesh Bank dashboard login attempt has been blocked. Please wait and retry later."

    return None


def _decode_notification_text(value: str) -> str:
    normalized = _optional_string(value)
    if not normalized:
        return ""
    for _ in range(2):
        decoded = unquote(normalized)
        if decoded == normalized:
            break
        normalized = decoded
    return normalized
