import pytest

from customs_automation.core.canonicalization import (
    CanonicalizationError,
    canonicalize_buyer_name,
    canonicalize_file_number,
    canonicalize_lc_sc_number,
    canonicalize_pi_number,
    canonicalize_ud_ip_exp_number,
)


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        ("p/26/42", "P/26/0042"),
        (" P-26-0042 ", "P/26/0042"),
        ("P\\26\\7", "P/26/0007"),
    ],
)
def test_file_number_canonicalization(raw_value: str, expected: str) -> None:
    assert canonicalize_file_number(raw_value) == expected


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        ("LC  -0038", "LC-0038"),
        ("sc-010-pdl-8", "SC-010-PDL-8"),
    ],
)
def test_lc_sc_number_canonicalization(raw_value: str, expected: str) -> None:
    assert canonicalize_lc_sc_number(raw_value) == expected


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        ("pdl-26-42", "PDL-26-0042"),
        ("PDL-26-0042-r4", "PDL-26-0042-R4"),
    ],
)
def test_pi_number_canonicalization(raw_value: str, expected: str) -> None:
    assert canonicalize_pi_number(raw_value) == expected


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        ("ip lc 0043 vintage denim studio ltd.", "IP-LC-0043-VINTAGE-DENIM-STUDIO-LTD"),
        ("exp-  9981 ;", "EXP-9981"),
    ],
)
def test_ud_ip_exp_number_canonicalization(raw_value: str, expected: str) -> None:
    assert canonicalize_ud_ip_exp_number(raw_value) == expected


def test_buyer_name_canonicalization() -> None:
    assert canonicalize_buyer_name("Designer Fashion Ltd.\\Dhaka.") == "DESIGNER FASHION LTD"


def test_file_number_rejects_invalid_inputs() -> None:
    with pytest.raises(CanonicalizationError):
        canonicalize_file_number("X/26/0042")


def test_pi_number_rejects_invalid_inputs() -> None:
    with pytest.raises(CanonicalizationError):
        canonicalize_pi_number("PDL-26")


def test_doc_number_rejects_invalid_inputs() -> None:
    with pytest.raises(CanonicalizationError):
        canonicalize_ud_ip_exp_number("UD")


def test_buyer_name_rejects_empty() -> None:
    with pytest.raises(CanonicalizationError):
        canonicalize_buyer_name("   ")
