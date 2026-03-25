"""
TNREGINET Encumbrance Certificate (EC) Retrieval Agent
=======================================================
Phase 1 -- CAPTCHA solved manually by operator.
Everything else runs automatically.

Usage:
    python tnreginet_ec.py ^
        --district "Chennai" ^
        --zone    "Chennai" ^
        --sro     "Anna Nagar" ^
        --village "Villivakkam" ^
        --survey  "123/1A" ^
        --from    "01/01/2015" ^
        --to      "31/12/2023"

Or edit the DEFAULTS block at the bottom and run directly.
"""

import argparse
import os
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

# Ensure UTF-8 output on Windows consoles
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from playwright.sync_api import Page, sync_playwright, TimeoutError as PWTimeoutError

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
HOME_URL = (
    "https://tnreginet.gov.in/portal/webHP?"
    "requestType=ApplicationRH&actionVal=homePage"
    "&screenId=114&SubCodeVal=null&LangId=1&ActKey=null&ActType=html"
)
DOWNLOAD_DIR = Path("./downloads")
MAX_CAPTCHA_ATTEMPTS = 10
PAGE_LOAD_TIMEOUT = 60_000   # ms
DROPDOWN_TIMEOUT  = 30_000   # ms
RESULT_TIMEOUT    = 30_000   # ms


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def log(step: str, message: str, ok: bool = True) -> None:
    mark = "[OK]" if ok else "[FAIL]"
    print(f"[{step}] {message} {mark}", flush=True)


def step_log(n: str, msg: str) -> None:
    log(f"STEP {n}", msg)


def fail_log(n: str, msg: str) -> None:
    log(f"STEP {n}", msg, ok=False)


def now_ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def sanitize(s: str) -> str:
    return s.replace("/", "-").replace("\\", "-").replace(" ", "_")


def print_final(status, district, village, survey,
                file_path="", failed_step="", reason=""):
    prop = f"{district} / {village} / {survey}"
    ts = now_ts()
    if status == "SUCCESS":
        print(f'\n{{"status":"SUCCESS","property":"{prop}",'
              f'"file_path":"{file_path}","timestamp":"{ts}"}}')
    else:
        print(f'\n{{"status":"FAILED","property":"{prop}",'
              f'"failed_at_step":"{failed_step}",'
              f'"reason":"{reason}","timestamp":"{ts}"}}')


# ---------------------------------------------------------------------------
# Core automation
# ---------------------------------------------------------------------------

def run(district: str, zone: str, sro: str, village: str, survey: str,
        from_date: str, to_date: str) -> None:

    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as pw:
        # ==================================================================
        # STEP 1 -- Launch browser
        # ==================================================================
        browser = pw.chromium.launch(headless=False, args=["--start-maximized"])
        context = browser.new_context(viewport=None, accept_downloads=True)
        page: Page = context.new_page()
        step_log("1", "Browser launched")

        try:
            _execute_flow(page, context, district, zone, sro, village,
                          survey, from_date, to_date)
        except Exception as exc:
            fail_log("?", f"Unexpected error: {exc}")
            traceback.print_exc()
            try:
                page.screenshot(path="debug_unexpected_error.png")
            except Exception:
                pass
            print_final("FAILED", district, village, survey,
                        failed_step="unknown", reason=str(exc))
        finally:
            print("[STEP 24] Closing browser...", flush=True)
            context.close()
            browser.close()
            step_log("24", "Browser closed")


def _execute_flow(page: Page, context, district, zone, sro, village,
                  survey, from_date, to_date):
    # ==================================================================
    # STEP 2 -- Navigate to TNREGINET homepage
    # ==================================================================
    print("[STEP 2] Navigating to TNREGINET...", flush=True)
    page.goto(HOME_URL, wait_until="domcontentloaded", timeout=PAGE_LOAD_TIMEOUT)
    page.wait_for_load_state("networkidle", timeout=PAGE_LOAD_TIMEOUT)
    step_log("2", "Navigated to TNREGINET")

    # ==================================================================
    # STEP 2b -- Dismiss popup (mobile app promo)
    # ==================================================================
    _dismiss_popup(page)

    # ==================================================================
    # STEP 2c -- Switch to English
    # ==================================================================
    print("[STEP 2c] Switching to English...", flush=True)
    try:
        eng_link = page.query_selector("a:has-text('English')")
        if eng_link and eng_link.is_visible():
            eng_link.click()
            page.wait_for_load_state("networkidle", timeout=PAGE_LOAD_TIMEOUT)
            time.sleep(2)
            _dismiss_popup(page)
            step_log("2c", "Switched to English")
        else:
            print("[STEP 2c] English link not found -- continuing in current language", flush=True)
    except Exception as e:
        print(f"[STEP 2c] Language switch failed (non-fatal): {e}", flush=True)

    # ==================================================================
    # STEP 3 -- Navigate to EC search via menuAct() JS call
    # The purple card "View/Download EC" uses this onclick:
    #   menuAct("webHP?requestType=ApplicationRH&actionVal=openEncumbranceCertSearch
    #            &screenId=8400001&scenarioId=2&menuCode=8400010&auditUSFlag=true")
    # This is an SPA -- content loads into a div, not a full page nav.
    # ==================================================================
    print("[STEP 3] Opening EC search form via menuAct()...", flush=True)
    EC_MENU_URL = (
        "webHP?requestType=ApplicationRH"
        "&actionVal=openEncumbranceCertSearch"
        "&screenId=8400001&scenarioId=2"
        "&menuCode=8400010&auditUSFlag=true"
    )
    page.evaluate(f'menuAct("{EC_MENU_URL}");')
    # Wait for the EC form to load inside the SPA
    time.sleep(3)
    page.wait_for_load_state("networkidle", timeout=PAGE_LOAD_TIMEOUT)
    time.sleep(2)
    _dismiss_popup(page)
    step_log("3", "EC search form opened")

    # ==================================================================
    # STEP 4 -- Confirm EC form is visible + capture session token
    # ==================================================================
    print("[STEP 4] Confirming EC form...", flush=True)
    # Wait for visible form elements (Zone/District dropdowns)
    # The form has select dropdowns -- wait for at least one to appear
    try:
        page.wait_for_selector(
            "select:visible", timeout=20_000
        )
    except PWTimeoutError:
        page.screenshot(path="debug_ec_form.png")
        raise RuntimeError("EC search form did not load. See debug_ec_form.png")

    # Capture CSRF token
    csrf = ""
    try:
        tok_el = page.query_selector("input[name='_csrf']")
        if tok_el:
            csrf = tok_el.get_attribute("value") or ""
    except Exception:
        pass
    step_log("4", f"EC form visible, CSRF: {csrf[:20]}..." if csrf else
                   "EC form visible, session via cookies")

    # Take a debug screenshot of the form
    page.screenshot(path="debug_ec_form_loaded.png")

    # ==================================================================
    # Known element IDs on the EC search form:
    #   cmb_Zone            - Zone dropdown
    #   cmb_District        - District dropdown
    #   cmb_SroName         - Sub Registrar Office dropdown
    #   txt_PeriodStartDt   - EC Start Date (text, datepicker)
    #   txt_PeriodEndDt     - EC End Date (text, datepicker)
    #   cmb_Village         - Registration Village dropdown
    #   txt_SurveyNo        - Survey Number (text)
    #   txt_SubDivisionNo   - Subdivision Number (text)
    #   btn_AddSurvey       - "Add" button
    #   txt_Captcha_login   - CAPTCHA input
    #   btn_SearchDoc       - "Search" submit button
    # ==================================================================

    # ==================================================================
    # STEP 5 -- Select Zone
    # ==================================================================
    print(f"[STEP 5] Selecting Zone: {zone}...", flush=True)
    _select_by_id(page, "cmb_Zone", zone, step="5")
    step_log("5", f"Zone selected: {zone}")

    # Wait for District dropdown to populate
    print("[STEP 5->6] Waiting for District dropdown...", flush=True)
    _wait_select_populated(page, "cmb_District", timeout=DROPDOWN_TIMEOUT)

    # ==================================================================
    # STEP 6 -- Select District
    # ==================================================================
    print(f"[STEP 6] Selecting District: {district}...", flush=True)
    _select_by_id(page, "cmb_District", district, step="6")
    step_log("6", f"District selected: {district}")

    # Wait for SRO dropdown to populate
    print("[STEP 6->7] Waiting for SRO dropdown...", flush=True)
    _wait_select_populated(page, "cmb_SroName", timeout=DROPDOWN_TIMEOUT)

    # ==================================================================
    # STEP 7 -- Select Sub Registrar Office
    # ==================================================================
    print(f"[STEP 7] Selecting SRO: {sro}...", flush=True)
    _select_by_id(page, "cmb_SroName", sro, step="7")
    step_log("7", f"SRO selected: {sro}")

    # Wait for Village dropdown to populate after SRO selection
    print("[STEP 7->8] Waiting for Village dropdown...", flush=True)
    _wait_select_populated(page, "cmb_Village", timeout=DROPDOWN_TIMEOUT)

    # ==================================================================
    # STEP 8 -- Enter EC Start Date (From Date)
    # The datepicker is jQuery.datepick (Keith Wood) with format dd-M-yyyy.
    # We MUST use datepick('setDate', DateObj) to set BOTH the visible
    # value AND the internal selectedDates array. Setting el.value alone
    # leaves the internal state empty, and the server gets no dates!
    # ==================================================================
    print(f"[STEP 8] Entering EC Start Date: {from_date}...", flush=True)
    _set_datepick(page, "txt_PeriodStartDt", from_date)
    actual = page.evaluate("document.getElementById('txt_PeriodStartDt').value")
    if not actual:
        raise RuntimeError("[STEP 8] EC Start Date field is empty after setting")
    step_log("8", f"EC Start Date entered: {actual}")

    # ==================================================================
    # STEP 9 -- Enter EC End Date (To Date)
    # ==================================================================
    print(f"[STEP 9] Entering EC End Date: {to_date}...", flush=True)
    _set_datepick(page, "txt_PeriodEndDt", to_date)
    actual = page.evaluate("document.getElementById('txt_PeriodEndDt').value")
    if not actual:
        raise RuntimeError("[STEP 9] EC End Date field is empty after setting")
    step_log("9", f"EC End Date entered: {actual}")

    # ==================================================================
    # STEP 10 -- Select Registration Village
    # ==================================================================
    print(f"[STEP 10] Selecting Registration Village: {village}...", flush=True)
    _select_by_id(page, "cmb_Village", village, step="10")
    step_log("10", f"Village selected: {village}")

    # ==================================================================
    # STEP 11 -- Enter Survey Number + Subdivision Number
    # ==================================================================
    # Parse subdivision from survey if it contains "/" (e.g., "123/1A")
    actual_survey = survey
    subdiv = ""
    if "/" in survey:
        parts = survey.split("/", 1)
        actual_survey = parts[0].strip()
        subdiv = parts[1].strip()
        print(f"[STEP 11] Split input: Survey No={actual_survey}, Subdiv={subdiv}",
              flush=True)

    print(f"[STEP 11] Entering Survey No: {actual_survey}...", flush=True)
    page.click("#txt_SurveyNo")
    page.fill("#txt_SurveyNo", "")
    page.type("#txt_SurveyNo", actual_survey, delay=30)
    # Verify
    sv = page.evaluate("document.getElementById('txt_SurveyNo').value")
    if not sv:
        raise RuntimeError("[STEP 11] Survey No field is empty after typing")
    step_log("11", f"Survey No entered: {sv}")

    if subdiv:
        page.click("#txt_SubDivisionNo")
        page.fill("#txt_SubDivisionNo", "")
        page.type("#txt_SubDivisionNo", subdiv, delay=30)
        sd = page.evaluate("document.getElementById('txt_SubDivisionNo').value")
        step_log("11b", f"Subdivision No entered: {sd}")

    # ==================================================================
    # STEP 12 -- Click "Add" button to add survey to search list
    # Verify it was actually added by checking for the survey table.
    # Retry up to 3 times if it fails.
    # ==================================================================
    survey_added = False
    for add_try in range(3):
        print(f"[STEP 12] Clicking 'Add' button (attempt {add_try + 1})...",
              flush=True)
        page.click("#btn_AddSurvey")
        page.wait_for_timeout(3000)

        # Verify: check if a table row with our survey number appeared
        added = page.evaluate("""(function() {
            var tables = document.querySelectorAll('table');
            for (var i = 0; i < tables.length; i++) {
                var t = tables[i];
                if (t.offsetParent !== null &&
                    t.innerText.indexOf('""" + actual_survey + """') !== -1) {
                    return true;
                }
            }
            return false;
        })()""")

        if added:
            survey_added = True
            step_log("12", "Survey added to search list")
            break
        else:
            print(f"[STEP 12] Survey not added -- re-entering fields...",
                  flush=True)
            # Re-enter survey fields in case they got cleared
            page.click("#txt_SurveyNo")
            page.fill("#txt_SurveyNo", "")
            page.type("#txt_SurveyNo", actual_survey, delay=30)
            if subdiv:
                page.click("#txt_SubDivisionNo")
                page.fill("#txt_SubDivisionNo", "")
                page.type("#txt_SubDivisionNo", subdiv, delay=30)
            page.wait_for_timeout(1000)

    if not survey_added:
        page.screenshot(path="debug_add_failed.png")
        raise RuntimeError(
            "Survey could not be added after 3 attempts. "
            "See debug_add_failed.png"
        )

    # ==================================================================
    # STEPS 13-17 -- CAPTCHA loop
    # ==================================================================
    # Set up a dialog handler to catch JavaScript alert() messages.
    # Government sites often use alert() for validation errors like
    # "Invalid Captcha", "Please enter captcha", etc.
    _last_dialog_message = {"text": ""}

    def _on_dialog(dialog):
        _last_dialog_message["text"] = dialog.message
        print(f"[DIALOG] {dialog.type}: {dialog.message}", flush=True)
        dialog.accept()

    page.on("dialog", _on_dialog)

    attempt = 0
    submitted = False
    while attempt < MAX_CAPTCHA_ATTEMPTS:
        attempt += 1
        print(f"\n[STEP 13-17] CAPTCHA attempt {attempt}/{MAX_CAPTCHA_ATTEMPTS}...",
              flush=True)

        # Locate CAPTCHA image
        captcha_img = _find_captcha_image(page)
        if captcha_img is None:
            page.screenshot(path="debug_captcha_missing.png")
            raise RuntimeError("CAPTCHA image not found. See debug_captcha_missing.png")

        # Screenshot CAPTCHA
        captcha_path = str(DOWNLOAD_DIR / "captcha_current.png")
        captcha_img.screenshot(path=captcha_path)
        _display_captcha(captcha_path)

        print("\n" + "=" * 50, flush=True)
        print("CAPTCHA: Type the answer below and press Enter:", flush=True)
        print("=" * 50, flush=True)
        captcha_answer = input("> ").strip()

        if not captcha_answer:
            print("[STEP 15] Empty answer -- skipping", flush=True)
            continue

        # Type CAPTCHA answer directly into #txt_Captcha_login
        # Clear the field first by triple-click (select all) + delete,
        # then type the answer character by character to trigger events.
        captcha_input = page.query_selector("#txt_Captcha_login")
        captcha_input.click(click_count=3)  # triple-click to select all
        page.keyboard.press("Delete")       # clear selected text
        page.wait_for_timeout(200)
        captcha_input.type(captcha_answer, delay=80)
        page.wait_for_timeout(300)
        # Verify the value was entered
        actual_captcha = page.evaluate(
            "document.getElementById('txt_Captcha_login').value"
        )
        print(f"[STEP 16] CAPTCHA answer entered: {actual_captcha}", flush=True)
        if not actual_captcha:
            print("[STEP 16] WARNING: CAPTCHA field is empty!", flush=True)
            continue

        # ==============================================================
        # STEP 17 -- Submit form via #btn_SearchDoc
        # The onclick calls searchDocDtlsYearWise() which is an AJAX
        # request -- it loads results into the same SPA page.
        # ==============================================================
        print("[STEP 17] Submitting form...", flush=True)
        # Take a screenshot BEFORE submit for debugging
        page.screenshot(path="debug_before_submit.png")
        # Reset dialog message before submit
        _last_dialog_message["text"] = ""
        page.click("#btn_SearchDoc")
        step_log("17", "Form submitted")

        # Give a moment for any immediate alert() dialogs
        page.wait_for_timeout(2000)

        # Check if a dialog popped up (e.g., "Invalid Captcha")
        if _last_dialog_message["text"]:
            dlg = _last_dialog_message["text"].lower()
            print(f"[STEP 17] Dialog received: {_last_dialog_message['text']}", flush=True)
            if any(kw in dlg for kw in ["captcha", "invalid", "incorrect",
                                         "wrong", "code"]):
                print("[STEP 18] CAPTCHA error via dialog -- retrying...", flush=True)
                _last_dialog_message["text"] = ""
                _try_reload_captcha(page)
                continue
            elif any(kw in dlg for kw in ["no record", "not found", "no data"]):
                print("[STEP 18] No records (dialog).", flush=True)
                print_final("FAILED", district, village, survey,
                            failed_step="18", reason=_last_dialog_message["text"])
                return

        # ==============================================================
        # STEP 18 -- Wait for response after submit
        # After clicking Search, one of these happens:
        #   a) "Please Enter Valid Captcha" popup → click Ok, retry
        #   b) "Data Availability Period" popup → click Ok, results load
        #   c) Results load directly (table or "click here to download")
        #   d) "No records" message
        #
        # Strategy: poll every second for up to 30s, checking for
        # specific visible elements using Playwright selectors
        # (much more reliable than JS page content scanning).
        # ==============================================================
        print("[STEP 18] Waiting for result...", flush=True)
        result = _poll_after_submit(page, timeout=RESULT_TIMEOUT)
        page.screenshot(path="debug_after_submit.png")
        print(f"[STEP 18] Result: {result}", flush=True)

        if result == "captcha_wrong":
            print("[STEP 19] CAPTCHA incorrect -- retrying...", flush=True)
            _try_reload_captcha(page)
            continue
        elif result == "no_records":
            print("[STEP 19] No EC records found.", flush=True)
            print_final("FAILED", district, village, survey,
                        failed_step="19",
                        reason="No EC records found for this property/date range")
            return
        elif result in ("success", "data_availability_ok"):
            step_log("19", "EC results loaded successfully")
            submitted = True
            break
        else:
            # timeout -- screenshot and retry
            print(f"[STEP 19] Timeout/unknown ({result}). Retrying...",
                  flush=True)
            page.screenshot(path="debug_timeout.png")
            _try_reload_captcha(page)
            continue

    if not submitted:
        fail_log("17", f"CAPTCHA failed {MAX_CAPTCHA_ATTEMPTS} times")
        print_final("FAILED", district, village, survey,
                    failed_step="17",
                    reason=f"CAPTCHA wrong {MAX_CAPTCHA_ATTEMPTS} times")
        return

    # ==================================================================
    # STEPS 20-22 -- Download PDF
    # The results page has a "Click Here" link that either:
    #   a) triggers a file download, or
    #   b) opens PDF in a new browser tab
    # We handle both cases.
    # ==================================================================
    filename = (
        f"EC_{sanitize(district)}_{sanitize(village)}"
        f"_{sanitize(survey)}_{sanitize(from_date)}.pdf"
    )
    out_path = DOWNLOAD_DIR / filename

    print("[STEP 20] Looking for download link on results page...", flush=True)
    page.screenshot(path="debug_result_page.png")

    # Log what's on the page for debugging
    page_text = page.inner_text("body")
    print(f"[STEP 20] Page text snippet: {page_text[:300]}...", flush=True)

    # Find the "Click Here" or download link
    download_link = None
    for sel in ["a:has-text('Click Here')", "a:has-text('click here')",
                "a:has-text('Click here')", "a:has-text('Download')",
                "a[href*='.pdf' i]", "a[href*='download' i]",
                "a[href*='ecreport' i]"]:
        el = page.query_selector(sel)
        if el and el.is_visible():
            download_link = el
            print(f"[STEP 20] Found link: {sel}", flush=True)
            break

    if download_link is None:
        fail_log("20", "No download link found on results page")
        page.screenshot(path="debug_no_download_link.png")
        print_final("FAILED", district, village, survey,
                    failed_step="20",
                    reason="Download link not found. See debug_result_page.png")
        return

    # Try Method 1: expect_download (direct file download)
    print("[STEP 21] Attempting download...", flush=True)
    downloaded = False

    try:
        with page.expect_download(timeout=10_000) as dl_info:
            download_link.click()
        dl = dl_info.value
        dl.save_as(str(out_path))
        downloaded = True
        print("[STEP 21] Direct download succeeded", flush=True)
    except (PWTimeoutError, Exception) as e:
        print(f"[STEP 21] Direct download failed ({e}), checking new tab...",
              flush=True)

    # Method 2: check if a new tab opened with the PDF
    if not downloaded:
        time.sleep(3)
        all_pages = context.pages
        if len(all_pages) > 1:
            new_page = all_pages[-1]
            print(f"[STEP 21] New tab opened: {new_page.url[:100]}", flush=True)
            new_page.wait_for_load_state("networkidle", timeout=30_000)
            time.sleep(2)

            # Try to get PDF content from the new tab
            try:
                content_type = new_page.evaluate("document.contentType") or ""
                print(f"[STEP 21] New tab content type: {content_type}",
                      flush=True)
            except Exception:
                content_type = ""

            if "pdf" in content_type.lower() or ".pdf" in new_page.url.lower():
                # PDF is displayed in the new tab -- save it
                try:
                    resp = new_page.request.get(new_page.url)
                    out_path.write_bytes(resp.body())
                    downloaded = True
                    print("[STEP 21] Saved PDF from new tab", flush=True)
                except Exception as e:
                    print(f"[STEP 21] Failed to save from new tab: {e}",
                          flush=True)
            else:
                # New tab might have a download link or rendered content
                new_page.screenshot(path="debug_new_tab.png")
                print("[STEP 21] New tab is not a PDF. See debug_new_tab.png",
                      flush=True)
                # Try expect_download from the new page
                try:
                    dl_link = new_page.query_selector(
                        "a:has-text('Click Here'), a:has-text('Download'), "
                        "a[href*='.pdf' i]"
                    )
                    if dl_link:
                        with new_page.expect_download(timeout=15_000) as dl2:
                            dl_link.click()
                        dl2.value.save_as(str(out_path))
                        downloaded = True
                except Exception:
                    pass

    # Method 3: check if current page now has a PDF iframe/embed
    if not downloaded:
        for tag in ["iframe", "embed", "object"]:
            el = page.query_selector(f"{tag}[src*='.pdf' i], {tag}[src*='ec' i]")
            if el:
                src = el.get_attribute("src") or ""
                if src:
                    try:
                        resp = page.request.get(src)
                        out_path.write_bytes(resp.body())
                        downloaded = True
                        break
                    except Exception:
                        pass

    if not downloaded:
        fail_log("21", "Could not download the PDF")
        page.screenshot(path="debug_download_failed.png")
        print_final("FAILED", district, village, survey,
                    failed_step="21",
                    reason="PDF download failed. See debug_download_failed.png")
        return

    step_log("22", f"PDF saved: {filename}")
    print(f"[DONE] EC saved at: {out_path}", flush=True)
    print_final("SUCCESS", district, village, survey, file_path=str(out_path))


# ---------------------------------------------------------------------------
# Sub-routines
# ---------------------------------------------------------------------------

def _dismiss_popup(page: Page) -> None:
    """Close any modal/popup overlay (mobile app promo etc.)."""
    for sel in ["button.close", ".modal .close", "[data-dismiss='modal']",
                ".modal-header .close", "button.btn-close", "span.close"]:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                el.click()
                page.wait_for_timeout(1000)
                print("[STEP 2b] Popup dismissed [OK]", flush=True)
                return
        except Exception:
            continue
    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(500)
    except Exception:
        pass


def _select_by_id(page: Page, select_id: str, value: str, step: str) -> None:
    """
    Select an option by visible text in a <select> identified by its DOM id.
    Handles exact, whitespace-insensitive, and partial matching.
    """
    selector = f"#{select_id}"
    sel_el = page.query_selector(selector)
    if sel_el is None:
        raise RuntimeError(f"[STEP {step}] Select element #{select_id} not found")

    options = sel_el.query_selector_all("option")
    opt_texts = []
    matched_value = None

    def _norm(s):
        return s.lower().replace(" ", "").replace("-", "").replace("_", "")

    for opt in options:
        text = (opt.inner_text() or "").strip()
        val = (opt.get_attribute("value") or "").strip()
        opt_texts.append(text)

        # Exact match (case-insensitive)
        if text.lower() == value.lower() or val.lower() == value.lower():
            matched_value = val if val else text
            break
        # Whitespace-insensitive match
        if _norm(text) == _norm(value) or _norm(val) == _norm(value):
            matched_value = val if val else text
            break

    # Partial match fallback
    if matched_value is None:
        for opt in options:
            text = (opt.inner_text() or "").strip()
            val = (opt.get_attribute("value") or "").strip()
            if value.lower() in text.lower() or _norm(value) in _norm(text):
                matched_value = val if val else text
                break

    if matched_value is None:
        raise RuntimeError(
            f"[STEP {step}] Value '{value}' not found in #{select_id}. "
            f"Available (first 15): {opt_texts[:15]}"
        )

    page.select_option(selector, value=matched_value)
    # Trigger change event for AJAX-dependent cascading dropdowns
    sel_el.dispatch_event("change")
    page.wait_for_timeout(1000)


def _wait_select_populated(page: Page, select_id: str,
                            timeout: int = 30000) -> None:
    """Wait until a <select> by id has more than 1 option."""
    print(f"  Waiting for #{select_id} to populate...", flush=True)
    deadline = time.time() + timeout / 1000
    while time.time() < deadline:
        sel_el = page.query_selector(f"#{select_id}")
        if sel_el:
            opts = sel_el.query_selector_all("option")
            if len(opts) > 1:
                return
        time.sleep(0.5)
    raise RuntimeError(
        f"#{select_id} did not populate within {timeout // 1000}s"
    )


def _find_input_by_label(page: Page, label_text: str):
    """Find an <input> by its associated label text or placeholder."""
    # By label[for]
    labels = page.query_selector_all("label")
    for lbl in labels:
        text = (lbl.inner_text() or "").strip()
        if label_text.lower() in text.lower():
            for_id = lbl.get_attribute("for") or ""
            if for_id:
                inp = page.query_selector(f"#{for_id}")
                if inp:
                    return inp
            # Check next sibling input
            inp = lbl.query_selector("~ input, + input")
            if inp:
                return inp

    # By name/id/placeholder containing keyword
    keyword = label_text.lower().replace(" ", "").replace(".", "")
    inputs = page.query_selector_all("input[type='text'], input:not([type])")
    for inp in inputs:
        name = (inp.get_attribute("name") or "").lower()
        iid = (inp.get_attribute("id") or "").lower()
        ph = (inp.get_attribute("placeholder") or "").lower()
        if keyword in name or keyword in iid or keyword in ph:
            return inp

    return None


def _fill_date_field(page: Page, label_text: str, date_str: str, step: str) -> None:
    """
    Fill a date field. date_str is DD/MM/YYYY.
    The form may use a datepicker or plain text input.
    """
    inp = _find_input_by_label(page, label_text)
    if inp is None:
        raise RuntimeError(f"[STEP {step}] Cannot find '{label_text}' input field")

    input_type = (inp.get_attribute("type") or "text").lower()

    if input_type == "date":
        # HTML5 date needs YYYY-MM-DD
        parts = date_str.split("/")
        if len(parts) == 3:
            iso = f"{parts[2]}-{parts[1]}-{parts[0]}"
            inp.fill(iso)
        else:
            inp.fill(date_str)
    else:
        # Text input -- clear and type the date
        inp.click()
        inp.fill("")
        inp.type(date_str, delay=50)
        # Press Escape to close any datepicker popup
        page.keyboard.press("Escape")
        page.wait_for_timeout(500)


def _set_datepick(page: Page, field_id: str, date_str: str) -> None:
    """
    Set a date on a jQuery.datepick field using the datepick API.
    date_str must be DD/MM/YYYY format (e.g., '01/01/2000').
    The datepick plugin uses internal selectedDates array — setting
    el.value directly does NOT update this, causing the server to
    receive empty dates. We must use datepick('setDate', Date).
    """
    # Parse DD/MM/YYYY
    parts = date_str.split("/")
    if len(parts) != 3:
        raise RuntimeError(f"Date must be DD/MM/YYYY, got: {date_str}")
    day, month, year = parts[0], parts[1], parts[2]
    # JavaScript months are 0-indexed
    js_month = str(int(month) - 1)

    page.evaluate(
        f"""(function() {{
            jQuery('#{field_id}').datepick('setDate',
                new Date({year}, {js_month}, {day}));
        }})()"""
    )
    page.wait_for_timeout(500)


def _find_captcha_image(page: Page):
    """Find the CAPTCHA image element."""
    for sel in ["img[src*='captcha' i]", "img[id*='captcha' i]",
                "img[class*='captcha' i]", "img[alt*='captcha' i]",
                "img[src*='Captcha' i]", "img[src*='CAPTCHA']"]:
        el = page.query_selector(sel)
        if el:
            return el
    return None


def _fill_captcha_input(page: Page, answer: str) -> None:
    """Type the CAPTCHA answer into the CAPTCHA text input."""
    for sel in ["input[name*='captcha' i]", "input[id*='captcha' i]",
                "input[placeholder*='captcha' i]", "input[placeholder*='code' i]",
                "input[name*='Captcha']", "input[id*='Captcha']"]:
        el = page.query_selector(sel)
        if el:
            el.click()
            el.fill("")
            el.type(answer, delay=60)
            return
    # Fallback: find input nearest to the "Type the code shown" label
    inp = _find_input_by_label(page, "Type the code")
    if inp:
        inp.click()
        inp.fill("")
        inp.type(answer, delay=60)
        return
    raise RuntimeError("Cannot find CAPTCHA input field")


def _click_submit(page: Page) -> None:
    """Click the form Submit/Search button."""
    for sel in ["button[type='submit']:has-text('Search')",
                "button[type='submit']:has-text('Submit')",
                "button:has-text('Search')",
                "button:has-text('Submit')",
                "input[type='submit']",
                "input[value*='Search' i]",
                "input[value*='Submit' i]",
                "button[type='submit']"]:
        el = page.query_selector(sel)
        if el and el.is_visible():
            el.click()
            return
    raise RuntimeError("Cannot find Submit/Search button")


def _poll_after_submit(page: Page, timeout: int = 30000) -> str:
    """
    After clicking Search, poll the page for what happened.
    Uses simple Playwright text/element checks instead of complex JS.

    Flow on TNREGINET:
      1. CAPTCHA wrong  → popup "Please Enter Valid Captcha" + Ok btn
      2. CAPTCHA correct → popup "Data Availability Period..." + Ok btn
         → click Ok → results page with "Click Here" download link
      3. No records → some "no records" message

    Returns: 'success' | 'data_availability_ok' | 'captcha_wrong' |
             'no_records' | 'timeout'
    """
    deadline = time.time() + timeout / 1000

    while time.time() < deadline:
        page_text = page.inner_text("body").lower()

        # 1. Check for "Data Availability Period" popup (= CAPTCHA passed!)
        if "data availability period" in page_text:
            print("[POLL] Data Availability popup detected -- clicking Ok...",
                  flush=True)
            try:
                page.click("button:has-text('Ok')", timeout=3000)
            except Exception:
                try:
                    page.click("input[value='Ok']", timeout=2000)
                except Exception:
                    page.keyboard.press("Enter")
            # Wait for results page to load after Ok
            print("[POLL] Waiting for EC results page...", flush=True)
            time.sleep(5)
            page.wait_for_load_state("networkidle", timeout=30000)
            time.sleep(3)
            page.screenshot(path="debug_after_ok.png")
            return "data_availability_ok"

        # 2. Check for CAPTCHA error popup
        if ("please enter valid captcha" in page_text or
                "invalid captcha" in page_text or
                "wrong captcha" in page_text):
            print("[POLL] CAPTCHA error popup detected -- clicking Ok...",
                  flush=True)
            try:
                page.click("button:has-text('Ok')", timeout=3000)
            except Exception:
                try:
                    page.click("input[value='Ok']", timeout=2000)
                except Exception:
                    page.keyboard.press("Enter")
            time.sleep(1)
            return "captcha_wrong"

        # 3. Check for "no records"
        if ("no records found" in page_text or
                "no record found" in page_text or
                "no data found" in page_text):
            return "no_records"

        # 4. Check for "click here" download link (results already loaded)
        if "click here" in page_text:
            return "success"

        time.sleep(1)

    return "timeout"


def _try_reload_captcha(page: Page) -> None:
    """Try to refresh the CAPTCHA image."""
    # Look for a refresh icon next to the CAPTCHA (visible in screenshot)
    for sel in ["img[src*='refresh' i]", "a[href*='captcha' i]",
                "span[class*='refresh' i]", "i[class*='refresh' i]",
                "img[alt*='refresh' i]", "a[title*='refresh' i]",
                "img + a", "img + span"]:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                el.click()
                page.wait_for_timeout(1500)
                return
        except Exception:
            continue
    # Fallback: click captcha image itself
    captcha = _find_captcha_image(page)
    if captcha:
        captcha.click()
        page.wait_for_timeout(1500)


def _download_pdf(page: Page, context, out_path: Path) -> bool:
    """Attempt to download the EC PDF from the result page."""
    # Look for PDF link / download button
    # "Click Here" is the TNREGINET download link text
    for sel in ["a:has-text('Click Here')", "a:has-text('click here')",
                "a:has-text('Click here')",
                "a[href*='.pdf' i]", "a[href*='download' i]",
                "a[href*='ecreport' i]", "a:has-text('Download')",
                "a:has-text('Print')", "button:has-text('Download')",
                "button:has-text('Print')", "a:has-text('View EC')",
                "a:has-text('Print EC')", "input[value*='Download' i]",
                "input[value*='Print' i]"]:
        el = page.query_selector(sel)
        if el:
            print(f"[STEP 20] Found download element: {sel}", flush=True)
            try:
                with page.expect_download(timeout=30_000) as dl_info:
                    el.click()
                download = dl_info.value
                download.save_as(str(out_path))
                return True
            except PWTimeoutError:
                # Click might have opened in new tab or inline
                pass

    # Check if a new page (tab) opened with the PDF
    pages = context.pages
    if len(pages) > 1:
        new_page = pages[-1]
        content_type = new_page.evaluate("document.contentType") or ""
        if "pdf" in content_type.lower():
            url = new_page.url
            resp = page.request.get(url)
            out_path.write_bytes(resp.body())
            new_page.close()
            return True

    # Inline PDF via iframe/embed
    for tag in ["iframe", "embed", "object"]:
        el = page.query_selector(f"{tag}[src*='.pdf' i], {tag}[src*='ec' i]")
        if el:
            src = el.get_attribute("src") or ""
            if src:
                resp = page.request.get(src)
                out_path.write_bytes(resp.body())
                return True

    return False


def _display_captcha(path: str) -> None:
    """Show the CAPTCHA image to the operator."""
    import base64
    abs_path = str(Path(path).resolve())
    print(f"\n[CAPTCHA IMAGE] Saved at: {abs_path}", flush=True)

    # Try iTerm2 inline image
    try:
        data = Path(path).read_bytes()
        b64 = base64.b64encode(data).decode()
        sys.stdout.write(f"\x1b]1337;File=inline=1;width=40;height=10:{b64}\x07\n")
        sys.stdout.flush()
        return
    except Exception:
        pass

    # On Windows, open in default viewer
    if sys.platform == "win32":
        try:
            os.startfile(abs_path)
            print("  (Opened CAPTCHA image in default viewer)", flush=True)
        except Exception:
            pass
    print(f"  Open the file above to see the CAPTCHA.", flush=True)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(
        description="Retrieve EC from TNREGINET (Phase 1 -- manual CAPTCHA)"
    )
    p.add_argument("--district", required=True, help="District name")
    p.add_argument("--zone",     required=True, help="Zone name")
    p.add_argument("--sro",      required=True, help="Sub Registrar Office name")
    p.add_argument("--village",  required=True, help="Registration Village name")
    p.add_argument("--survey",   required=True, help="Survey number")
    p.add_argument("--from", dest="from_date", required=True,
                   help="EC Start Date DD/MM/YYYY")
    p.add_argument("--to",   dest="to_date",   required=True,
                   help="EC End Date DD/MM/YYYY")
    return p.parse_args()


# ---------------------------------------------------------------------------
# Smart SRO Finder
# ---------------------------------------------------------------------------

# Cache file to store discovered village -> zone/district/sro mappings
SRO_CACHE_FILE = Path("./sro_cache.json")


def _load_sro_cache() -> dict:
    """Load the SRO lookup cache from disk."""
    if SRO_CACHE_FILE.exists():
        import json
        try:
            return json.loads(SRO_CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_sro_cache(cache: dict) -> None:
    """Save the SRO lookup cache to disk."""
    import json
    SRO_CACHE_FILE.write_text(
        json.dumps(cache, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )


def smart_find_sro(village_name: str) -> dict:
    """
    Given a village name, search ALL zones -> districts -> SROs -> villages
    on the TNREGINET EC form to find the matching location hierarchy.

    Returns: {"zone": ..., "district": ..., "sro": ..., "village": ...}
    or empty dict if not found.

    Results are cached in sro_cache.json for instant lookup next time.
    """
    # Check cache first
    cache = _load_sro_cache()
    cache_key = village_name.lower().strip()
    if cache_key in cache:
        print(f"[CACHE HIT] Found {village_name} in cache", flush=True)
        return cache[cache_key]

    print(f"\n[SMART SRO] Searching all zones for village '{village_name}'...",
          flush=True)
    print("[SMART SRO] This may take 2-5 minutes (first time only).\n", flush=True)

    results = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False, args=["--start-maximized"])
        context = browser.new_context(viewport=None)
        page = context.new_page()

        try:
            # Navigate and open EC form
            page.goto(HOME_URL, wait_until="domcontentloaded", timeout=PAGE_LOAD_TIMEOUT)
            page.wait_for_load_state("networkidle", timeout=PAGE_LOAD_TIMEOUT)
            time.sleep(3)
            _dismiss_popup(page)

            # Switch to English
            eng = page.query_selector("a:has-text('English')")
            if eng and eng.is_visible():
                eng.click()
                page.wait_for_load_state("networkidle", timeout=PAGE_LOAD_TIMEOUT)
                time.sleep(2)
                _dismiss_popup(page)

            # Open EC form
            EC_MENU_URL = (
                "webHP?requestType=ApplicationRH"
                "&actionVal=openEncumbranceCertSearch"
                "&screenId=8400001&scenarioId=2"
                "&menuCode=8400010&auditUSFlag=true"
            )
            page.evaluate(f'menuAct("{EC_MENU_URL}");')
            time.sleep(5)
            page.wait_for_load_state("networkidle", timeout=PAGE_LOAD_TIMEOUT)

            # Get all zones
            zone_opts = _get_select_options(page, "cmb_Zone")
            total_zones = len([z for z in zone_opts if z[0] != "- Select -"])
            print(f"[SMART SRO] Found {total_zones} zones to search\n", flush=True)

            for z_idx, (z_text, z_val) in enumerate(zone_opts):
                if z_text == "- Select -":
                    continue

                print(f"[SMART SRO] [{z_idx}/{total_zones}] Zone: {z_text}...",
                      flush=True)
                page.select_option("#cmb_Zone", value=z_val)
                el = page.query_selector("#cmb_Zone")
                if el: el.dispatch_event("change")
                time.sleep(2)

                if not _wait_select_populated_silent(page, "cmb_District", 15):
                    continue

                dist_opts = _get_select_options(page, "cmb_District")
                for d_text, d_val in dist_opts:
                    if d_text == "- Select -":
                        continue

                    page.select_option("#cmb_District", value=d_val)
                    el = page.query_selector("#cmb_District")
                    if el: el.dispatch_event("change")
                    time.sleep(2)

                    if not _wait_select_populated_silent(page, "cmb_SroName", 10):
                        continue

                    sro_opts = _get_select_options(page, "cmb_SroName")
                    for s_text, s_val in sro_opts:
                        if s_text == "- Select -":
                            continue

                        page.select_option("#cmb_SroName", value=s_val)
                        el = page.query_selector("#cmb_SroName")
                        if el: el.dispatch_event("change")
                        time.sleep(2)

                        if not _wait_select_populated_silent(page, "cmb_Village", 10):
                            continue

                        vil_opts = _get_select_options(page, "cmb_Village")
                        for v_text, _v_val in vil_opts:
                            # Cache every village we find
                            v_key = v_text.lower().strip()
                            if v_key and v_key != "- select -":
                                entry = {
                                    "zone": z_text, "district": d_text,
                                    "sro": s_text, "village": v_text
                                }
                                cache[v_key] = entry

                            # Check if this matches our target
                            if village_name.lower() in v_text.lower():
                                match = {
                                    "zone": z_text, "district": d_text,
                                    "sro": s_text, "village": v_text
                                }
                                results.append(match)
                                print(
                                    f"  FOUND! Zone={z_text}, District={d_text}, "
                                    f"SRO={s_text}, Village={v_text}",
                                    flush=True
                                )

        except Exception as e:
            print(f"[SMART SRO] Error during search: {e}", flush=True)
        finally:
            context.close()
            browser.close()

    # Save cache (includes ALL villages discovered)
    _save_sro_cache(cache)
    print(f"\n[SMART SRO] Cached {len(cache)} villages to {SRO_CACHE_FILE}",
          flush=True)

    if len(results) == 1:
        return results[0]
    elif len(results) > 1:
        print(f"\n[SMART SRO] Multiple matches found for '{village_name}':")
        for i, r in enumerate(results):
            print(f"  [{i+1}] {r['zone']} > {r['district']} > {r['sro']} > {r['village']}")
        choice = input("Pick a number: ").strip()
        try:
            return results[int(choice) - 1]
        except (ValueError, IndexError):
            return results[0]
    else:
        print(f"[SMART SRO] Village '{village_name}' not found.", flush=True)
        return {}


def _get_select_options(page: Page, select_id: str) -> list:
    """Return [(text, value), ...] for a select element."""
    sel = page.query_selector(f"#{select_id}")
    if not sel:
        return []
    opts = sel.query_selector_all("option")
    return [
        ((o.inner_text() or "").strip(), (o.get_attribute("value") or "").strip())
        for o in opts
    ]


def _wait_select_populated_silent(page: Page, select_id: str,
                                   timeout: int = 15) -> bool:
    """Wait for a select to have >1 option. Returns True/False, no exception."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        sel = page.query_selector(f"#{select_id}")
        if sel and len(sel.query_selector_all("option")) > 1:
            return True
        time.sleep(0.5)
    return False


# ---------------------------------------------------------------------------
# Interactive Menu
# ---------------------------------------------------------------------------

def interactive_menu():
    """Interactive menu for EC retrieval."""
    print("=" * 60, flush=True)
    print("   TNREGINET EC Retriever", flush=True)
    print("   Encumbrance Certificate Download Tool", flush=True)
    print("=" * 60, flush=True)
    print()
    print("  [1] Fetch EC  (enter property details)")
    print("  [2] Smart Search  (find Zone/District/SRO by village name)")
    print("  [3] Batch Fetch  (from CSV file)")
    print("  [4] Exit")
    print()

    choice = input("Choose [1/2/3/4]: ").strip()

    if choice == "1":
        _menu_fetch_ec()
    elif choice == "2":
        _menu_smart_search()
    elif choice == "3":
        _menu_batch()
    elif choice == "4":
        print("Bye!")
        sys.exit(0)
    else:
        print("Invalid choice.")
        interactive_menu()


def _menu_fetch_ec(prefill: dict = None):
    """Interactive EC fetch with manual input."""
    print("\n--- Enter Property Details ---\n", flush=True)

    if prefill:
        print(f"  (Pre-filled from Smart Search)", flush=True)
        zone     = prefill.get("zone", "")
        district = prefill.get("district", "")
        sro      = prefill.get("sro", "")
        village  = prefill.get("village", "")
    else:
        zone     = ""
        district = ""
        sro      = ""
        village  = ""

    zone      = input(f"  Zone [{zone}]: ").strip() or zone
    district  = input(f"  District [{district}]: ").strip() or district
    sro       = input(f"  Sub Registrar Office [{sro}]: ").strip() or sro
    village   = input(f"  Village [{village}]: ").strip() or village
    survey    = input(f"  Survey No (e.g., 931/1): ").strip()
    from_date = input(f"  From Date (DD/MM/YYYY) [01/01/2000]: ").strip() or "01/01/2000"
    to_date   = input(f"  To Date (DD/MM/YYYY) [25/03/2026]: ").strip() or "25/03/2026"

    if not all([zone, district, sro, village, survey]):
        print("\nERROR: All fields are required.", flush=True)
        return

    print(f"\n--- Confirm Details ---", flush=True)
    print(f"  Zone:     {zone}", flush=True)
    print(f"  District: {district}", flush=True)
    print(f"  SRO:      {sro}", flush=True)
    print(f"  Village:  {village}", flush=True)
    print(f"  Survey:   {survey}", flush=True)
    print(f"  From:     {from_date}", flush=True)
    print(f"  To:       {to_date}", flush=True)
    print()

    confirm = input("  Start? (y/n): ").strip().lower()
    if confirm != "y":
        print("Cancelled.", flush=True)
        return

    run(
        zone=zone, district=district, sro=sro,
        village=village, survey=survey,
        from_date=from_date, to_date=to_date,
    )

    print("\n", flush=True)
    again = input("Fetch another EC? (y/n): ").strip().lower()
    if again == "y":
        interactive_menu()


def _menu_smart_search():
    """Smart SRO finder then fetch."""
    print("\n--- Smart Village Search ---\n", flush=True)
    village = input("  Enter village name: ").strip()
    if not village:
        print("No village entered.", flush=True)
        interactive_menu()
        return

    result = smart_find_sro(village)

    if result:
        print(f"\n--- Found ---", flush=True)
        print(f"  Zone:     {result['zone']}", flush=True)
        print(f"  District: {result['district']}", flush=True)
        print(f"  SRO:      {result['sro']}", flush=True)
        print(f"  Village:  {result['village']}", flush=True)
        print()
        fetch = input("  Fetch EC with these details? (y/n): ").strip().lower()
        if fetch == "y":
            _menu_fetch_ec(prefill=result)
        else:
            interactive_menu()
    else:
        print("Village not found. Try a different spelling.", flush=True)
        interactive_menu()


def _menu_batch():
    """Batch fetch from CSV file."""
    print("\n--- Batch Fetch from CSV ---\n", flush=True)
    print("  CSV format: zone,district,sro,village,survey,from_date,to_date")
    print("  Example row: Coimbatore,Coimbatore South,Madukkarai,Madukkarai,931/1,01/01/2000,07/03/2026")
    print()
    csv_path = input("  CSV file path: ").strip()
    if not csv_path or not Path(csv_path).exists():
        print(f"File not found: {csv_path}", flush=True)
        return

    import csv
    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    print(f"\n  Found {len(rows)} properties to fetch.\n", flush=True)
    confirm = input("  Start batch? (y/n): ").strip().lower()
    if confirm != "y":
        return

    for i, row in enumerate(rows):
        print(f"\n{'='*60}", flush=True)
        print(f"  Property {i+1}/{len(rows)}: {row.get('village','?')} - {row.get('survey','?')}",
              flush=True)
        print(f"{'='*60}\n", flush=True)
        try:
            run(
                zone=row["zone"], district=row["district"],
                sro=row["sro"], village=row["village"],
                survey=row["survey"],
                from_date=row["from_date"], to_date=row["to_date"],
            )
        except Exception as e:
            print(f"  ERROR: {e}", flush=True)
            continue

    print(f"\n  Batch complete! Processed {len(rows)} properties.", flush=True)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) > 1:
        # CLI mode
        args = parse_args()
        params = vars(args)
        run(
            zone      = params["zone"],
            district  = params["district"],
            sro       = params["sro"],
            village   = params["village"],
            survey    = params["survey"],
            from_date = params["from_date"],
            to_date   = params["to_date"],
        )
    else:
        # Interactive menu mode
        interactive_menu()
