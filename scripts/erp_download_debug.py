from __future__ import annotations

import argparse
from datetime import date, timedelta
from pathlib import Path

from playwright.sync_api import Playwright, TimeoutError, expect, sync_playwright


DEFAULT_REPORT_URL = "https://pdlerp.pioneerdenim.com/RptCommercialExport/DateWiseLCRegisterForDocuments"


def rolling_365_day_range(today: date | None = None) -> tuple[date, date]:
    today = today or date.today()
    return today - timedelta(days=365), today


def format_erp_date(value: date) -> str:
    return value.strftime("%d-%b-%Y")


def parse_args() -> argparse.Namespace:
    default_start, default_end = rolling_365_day_range()

    parser = argparse.ArgumentParser(
        description="Debug helper for the ERP export report download flow.",
    )
    parser.add_argument(
        "--url",
        default=DEFAULT_REPORT_URL,
        help="ERP report URL to open.",
    )
    parser.add_argument(
        "--channel",
        default="msedge",
        help="Chromium channel to launch, for example msedge.",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run headless instead of opening the browser UI.",
    )
    parser.add_argument(
        "--from-date",
        default=default_start.isoformat(),
        help="Report start date in YYYY-MM-DD format. Defaults to today minus 365 days.",
    )
    parser.add_argument(
        "--to-date",
        default=default_end.isoformat(),
        help="Report end date in YYYY-MM-DD format. Defaults to today.",
    )
    parser.add_argument(
        "--from-input-selector",
        default=".dx-texteditor-input",
        help="Selector for the start-date text input collection. Defaults to a DevExpress text input selector.",
    )
    parser.add_argument(
        "--to-input-selector",
        default=".dx-texteditor-input",
        help="Selector for the end-date text input collection. Defaults to a DevExpress text input selector.",
    )
    parser.add_argument(
        "--from-input-index",
        type=int,
        default=2,
        help="Zero-based index to use when the start-date selector matches multiple inputs.",
    )
    parser.add_argument(
        "--to-input-index",
        type=int,
        default=3,
        help="Zero-based index to use when the end-date selector matches multiple inputs.",
    )
    parser.add_argument(
        "--download-path",
        type=Path,
        default=Path("erp-download.csv"),
        help="Destination path for the downloaded report file.",
    )
    return parser.parse_args()


def pick_date(page, select_button_index: int, target: date) -> None:
    month_short = target.strftime("%b")
    year_label = target.strftime("%B %Y")
    day_text = str(target.day)

    page.get_by_role("button", name="Select").nth(select_button_index).click()
    expect(page.get_by_label(year_label)).to_be_visible(timeout=10_000)

    page.get_by_role("button", name=target.strftime("%B")).click()
    page.get_by_role("button", name="chevronleft").click()
    page.get_by_label(year_label).get_by_text(month_short).click()
    page.get_by_text(day_text, exact=True).last.click()


def fill_date_input(page, *, selector: str, index: int, target: date) -> bool:
    formatted = format_erp_date(target)
    locator = page.locator(selector)
    if locator.count() <= index:
        return False
    target_locator = locator.nth(index)
    target_locator.wait_for(state="visible", timeout=10_000)
    target_locator.click()
    target_locator.fill(formatted)
    target_locator.press("Tab")
    return True


def run(
    playwright: Playwright,
    *,
    url: str,
    channel: str,
    headless: bool,
    start_date: date,
    end_date: date,
    from_input_selector: str,
    to_input_selector: str,
    from_input_index: int,
    to_input_index: int,
    download_path: Path,
) -> None:
    browser = playwright.chromium.launch(channel=channel, headless=headless)
    context = browser.new_context(accept_downloads=True)
    page = context.new_page()

    try:
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_load_state("networkidle")

        # Prefer direct date-field input using the confirmed ERP format dd-MMM-yyyy.
        # Fall back to the date-picker flow if those inputs are not available.
        from_filled = fill_date_input(
            page,
            selector=from_input_selector,
            index=from_input_index,
            target=start_date,
        )
        to_filled = fill_date_input(
            page,
            selector=to_input_selector,
            index=to_input_index,
            target=end_date,
        )
        if not (from_filled and to_filled):
            pick_date(page, select_button_index=2, target=start_date)
            pick_date(page, select_button_index=3, target=end_date)

        page.get_by_role("button", name="Submit").click()
        page.locator(".dx-menu-item-popout").wait_for(state="visible", timeout=15_000)
        page.locator(".dx-menu-item-popout").click()

        with page.expect_download(timeout=30_000) as download_info:
            page.get_by_text("CSV", exact=True).click()

        download = download_info.value
        download_path.parent.mkdir(parents=True, exist_ok=True)
        download.save_as(str(download_path))
        print(f"Downloaded ERP report to: {download_path}")
    except TimeoutError as exc:
        _write_debug_failure_artifacts(page)
        raise SystemExit(f"Timed out while waiting for the ERP page flow: {exc}") from exc
    except Exception:
        _write_debug_failure_artifacts(page)
        raise
    finally:
        context.close()
        browser.close()


def main() -> int:
    args = parse_args()
    start_date = date.fromisoformat(args.from_date)
    end_date = date.fromisoformat(args.to_date)
    download_path = args.download_path.resolve()

    with sync_playwright() as playwright:
        run(
            playwright,
            url=args.url,
            channel=args.channel,
            headless=args.headless,
            start_date=start_date,
            end_date=end_date,
            from_input_selector=args.from_input_selector,
            to_input_selector=args.to_input_selector,
            from_input_index=args.from_input_index,
            to_input_index=args.to_input_index,
            download_path=download_path,
        )
    return 0


def _write_debug_failure_artifacts(page) -> None:
    try:
        Path("erp-debug-failure.html").write_text(page.content(), encoding="utf-8")
    except Exception:
        pass
    try:
        page.screenshot(path="erp-debug-failure.png", full_page=True)
    except Exception:
        pass


if __name__ == "__main__":
    raise SystemExit(main())
