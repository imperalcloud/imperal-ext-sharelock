"""XXE/billion-laughs hygiene for WebDAV response parsing (files.parse_dav_xml).

Storage backends may be customer-hosted (= semi-trusted): a DAV multistatus
body has no business carrying DTD/entity declarations — the shared helper
rejects them BEFORE parsing, and every DAV parse site must go through it.
"""
import os

import pytest

from files import parse_dav_xml

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

_MULTISTATUS = (
    '<?xml version="1.0"?>'
    '<d:multistatus xmlns:d="DAV:">'
    '<d:response><d:href>/remote.php/dav/files/u/Sharelock/CaseA/</d:href>'
    '<d:propstat><d:prop><d:getcontentlength>42</d:getcontentlength>'
    '<d:resourcetype/></d:prop></d:propstat></d:response>'
    '</d:multistatus>'
)


def test_parses_normal_multistatus():
    root = parse_dav_xml(_MULTISTATUS)
    ns = {"d": "DAV:"}
    assert root.tag == "{DAV:}multistatus"
    assert root.findtext("d:response/d:href", "", ns).endswith("/CaseA/")
    assert root.findtext("d:response/d:propstat/d:prop/d:getcontentlength",
                         "", ns) == "42"


def test_rejects_doctype_xxe_payload():
    payload = (
        '<?xml version="1.0"?>'
        '<!DOCTYPE d [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
        '<d:multistatus xmlns:d="DAV:"><d:response>&x;</d:response>'
        '</d:multistatus>'
    )
    with pytest.raises(ValueError, match="DTD/entity"):
        parse_dav_xml(payload)


def test_rejects_entity_declaration_alone():
    with pytest.raises(ValueError, match="DTD/entity"):
        parse_dav_xml('<!ENTITY lol "haha"><a/>')


def test_all_dav_parse_sites_use_the_guard():
    """No bare ElementTree.fromstring on DAV responses outside the helper."""
    for fname, allowed in (("files.py", 1),   # the helper body itself
                           ("panels.py", 0),
                           ("validation.py", 0)):
        with open(os.path.join(_ROOT, fname)) as f:
            src = f.read()
        n = src.count("ElementTree.fromstring(")
        assert n == allowed, (
            f"{fname}: {n} bare ElementTree.fromstring site(s) — "
            f"DAV responses must go through files.parse_dav_xml")
        if allowed == 0:
            assert "parse_dav_xml(" in src, f"{fname} must use the guard helper"
