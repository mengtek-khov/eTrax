from __future__ import annotations

import json
from pathlib import Path

from etrax.core.identity_document import scan_identity_document_image, scan_identity_document_text
from etrax.standalone.custom_code_functions import StandaloneCustomCodeFunctions, load_custom_code_function_names
from etrax.standalone.runtime_config_resolver import resolve_command_menu, resolve_command_send_configs


def test_scan_identity_document_text_parses_passport_mrz() -> None:
    raw_text = "\n".join(
        [
            "KINGDOM OF CAMBODIA PASSPORT",
            "P<KHMDOE<<SOPHIA<<<<<<<<<<<<<<<<<<<<<<<<<<<<",
            "N1234567<8KHM9001019F3001012<<<<<<<<<<<<<<06",
        ]
    )

    result = scan_identity_document_text(raw_text)

    assert result.document_type == "passport"
    assert result.fields["issuing_country"] == "KHM"
    assert result.fields["document_number"] == "N1234567"
    assert result.fields["nationality"] == "KHM"
    assert result.fields["surname"] == "DOE"
    assert result.fields["given_names"] == "SOPHIA"
    assert result.fields["birth_date"] == "1990-01-01"
    assert result.fields["expiry_date"] == "2030-01-01"
    assert result.fields["sex"] == "F"


def test_scan_identity_document_text_parses_labelled_id_card_text() -> None:
    raw_text = """
    Kingdom of Cambodia Identity Card
    ID No: 123456789
    Name: SOK DARA
    Date of Birth: 15/04/1992
    Sex: Male
    Nationality: Khmer
    Valid Until: 15/04/2032
    """

    result = scan_identity_document_text(raw_text)

    assert result.document_type == "khmer_id_card"
    assert result.fields == {
        "birth_date": "1992-04-15",
        "document_number": "123456789",
        "expiry_date": "2032-04-15",
        "given_names": "SOK DARA",
        "nationality": "Khmer",
        "sex": "M",
    }
    assert result.warnings == ()


def test_scan_identity_document_text_parses_common_khmer_id_labels_and_numerals() -> None:
    raw_text = """
    ព្រះរាជាណាចក្រកម្ពុជា
    អត្តសញ្ញាណប័ណ្ណសញ្ជាតិខ្មែរ
    លេខ ០១២៣៤៥៦៧៨
    គោត្តនាម និងនាម សុខ ដារ៉ា
    ថ្ងៃខែឆ្នាំកំណើត ១៥.០៤.១៩៩២
    ភេទ ប្រុស
    សញ្ជាតិ ខ្មែរ
    មានសុពលភាពដល់ ១៥.០៤.២០៣២
    """

    result = scan_identity_document_text(raw_text)

    assert result.document_type == "khmer_id_card"
    assert result.fields["document_number"] == "012345678"
    assert result.fields["surname"] == "សុខ ដារ៉ា"
    assert result.fields["birth_date"] == "1992-04-15"
    assert result.fields["sex"] == "M"
    assert result.fields["nationality"] == "ខ្មែរ"
    assert result.fields["expiry_date"] == "2032-04-15"


def test_scan_identity_document_image_uses_injected_ocr_reader() -> None:
    result = scan_identity_document_image(
        b"fake-image",
        ocr_reader=lambda image_bytes: "Passport No: N1234567\nNationality: KHM",
    )

    assert result.document_type == "passport"
    assert result.fields["document_number"] == "N1234567"
    assert result.fields["nationality"] == "KHM"


def test_scan_identity_document_image_returns_scan_failed_for_unreadable_bytes() -> None:
    result = scan_identity_document_image(b"not-a-real-image")

    assert result.document_type == "unknown"
    assert result.fields == {}
    assert result.warnings == ("scan_failed",)


def test_custom_code_scan_identity_document_from_ocr_text_returns_context_updates() -> None:
    functions = StandaloneCustomCodeFunctions()

    updates = functions.scan_identity_document_from_ocr_text(
        context={"identity_document_ocr_text": "ID No: 123456789\nName: SOK DARA"}
    )

    assert updates["identity_document_type"] == "khmer_id_card"
    assert updates["identity_document_document_number"] == "123456789"
    assert updates["identity_document_given_names"] == "SOK DARA"
    assert updates["identity_document_scan"]["fields"]["document_number"] == "123456789"
    assert updates["identity_document_summary"] == "\n".join(
        [
            "Document type: khmer id card",
            "Document number: 123456789",
            "Name: SOK DARA",
        ]
    )


def test_custom_code_identity_document_summary_handles_unreadable_image() -> None:
    functions = StandaloneCustomCodeFunctions()

    updates = functions.scan_identity_document_from_ocr_text(context={})

    assert "could not read identity information" in updates["identity_document_summary"]
    assert updates["identity_document_warnings"] == "empty_ocr_text"


def test_identity_document_custom_code_functions_are_listed() -> None:
    names = load_custom_code_function_names()

    assert "scan_identity_document_from_ocr_text" in names
    assert "scan_identity_document_from_selfie" in names


def test_etrax_read_id_command_scans_and_sends_result() -> None:
    project_root = Path(__file__).resolve().parents[1]
    payload = json.loads(
        (project_root / "data" / "bot_processes" / "etrax_bot_v1.json").read_text(encoding="utf-8")
    )

    commands = resolve_command_menu(payload)
    read_id_pipeline = resolve_command_send_configs(
        payload,
        "eTrax Bot V1",
        commands=commands,
    )["read_id"]

    assert [type(config).__name__ for config in read_id_pipeline] == [
        "AskSelfieConfig",
        "CustomCodeConfig",
        "SendMessageConfig",
    ]
    assert read_id_pipeline[1].function_name == "scan_identity_document_from_selfie"
    assert read_id_pipeline[2].text_template == "{identity_document_summary}"
