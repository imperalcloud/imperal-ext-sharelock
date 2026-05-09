"""Test the structured-output validator (no regex anywhere)."""
import sys
import unittest
from pathlib import Path

# Allow `from intelligence_response import ...` from this test file.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from intelligence_response import (  # noqa: E402
    CitationSource,
    GroundedClaim,
    IntelligenceResponse,
    build_intelligence_tool_schema,
    parse_intelligence_response,
)
from intelligence_validator import (  # noqa: E402
    validate_grounded_claims,
    semantic_alignment,
    _tokenize,
)


_FAKE_CTX = {
    "case": {"id": 3812, "name": "Test Files", "analysis_status": "completed",
             "analysis_version": "v17", "active_run_id": 21498},
    "run": {"status": "completed", "files_total": 56},
    "gaps": [
        {"gap_id": "G1", "description": "EXIF metadata missing on photos",
         "severity": "BLOCKING"},
        {"gap_id": "G2", "description": "Chase PDF dates inconsistent",
         "severity": "BLOCKING"},
    ],
    "indictment": {
        "targets": [
            {"name": "Nicholas Mitchell"},
            {"name": "ITC Ventures LLC"},
        ],
    },
    "summaries": [
        {"category": "main", "executive_summary": "Wire fraud scheme",
         "summary_json": {"key_findings": ["finding 1", "finding 2"]}},
    ],
    "entities": [
        {"name": "ITC Ventures LLC", "type": "company"},
        {"name": "Nicholas Mitchell", "type": "person"},
    ],
}


class TestSchema(unittest.TestCase):

    def test_schema_top_level_required(self):
        schema = build_intelligence_tool_schema()
        self.assertEqual(schema["additionalProperties"], False)
        self.assertEqual(
            set(schema["required"]),
            {"prose", "claims", "confidence", "unknown_fields"},
        )

    def test_schema_source_has_qual2(self):
        schema = build_intelligence_tool_schema()
        source_schema = (
            schema["properties"]["claims"]["items"]["properties"]["sources"]["items"]
        )
        self.assertIn("qual2", source_schema["required"])

    def test_schema_source_family_enum_closed(self):
        schema = build_intelligence_tool_schema()
        source_schema = (
            schema["properties"]["claims"]["items"]["properties"]["sources"]["items"]
        )
        families = set(source_schema["properties"]["family"]["enum"])
        self.assertEqual(
            families,
            {"CASE", "RUN", "TAX", "INS", "GRAPH", "G", "S", "CC", "I", "E", "A"},
        )


class TestParser(unittest.TestCase):

    def test_basic_parse(self):
        r = parse_intelligence_response({
            "prose": "Hello",
            "claims": [],
            "confidence": "HIGH",
            "unknown_fields": [],
        })
        self.assertIsNotNone(r)
        self.assertEqual(r.prose, "Hello")
        self.assertEqual(r.confidence, "HIGH")
        self.assertEqual(r.claims, ())

    def test_parse_with_nested_source(self):
        r = parse_intelligence_response({
            "prose": "Mitchell is principal",
            "claims": [{
                "text": "Mitchell is principal",
                "sources": [{
                    "family": "I", "qual1": None, "num": "2",
                    "subtype": "T", "subnum": "1", "qual2": None,
                }],
            }],
            "confidence": "HIGH",
            "unknown_fields": [],
        })
        self.assertEqual(len(r.claims), 1)
        self.assertEqual(r.claims[0].sources[0].family, "I")
        self.assertEqual(r.claims[0].sources[0].num, "2")
        self.assertEqual(r.claims[0].sources[0].subtype, "T")
        self.assertEqual(r.claims[0].sources[0].subnum, "1")

    def test_invalid_family_dropped(self):
        r = parse_intelligence_response({
            "prose": "x",
            "claims": [{
                "text": "x",
                "sources": [
                    {"family": "BOGUS", "qual1": None, "num": None,
                     "subtype": None, "subnum": None, "qual2": None},
                    {"family": "G", "qual1": None, "num": "1",
                     "subtype": None, "subnum": None, "qual2": None},
                ],
            }],
            "confidence": "LOW",
            "unknown_fields": [],
        })
        # Bogus family dropped; G survived.
        self.assertEqual(len(r.claims[0].sources), 1)
        self.assertEqual(r.claims[0].sources[0].family, "G")

    def test_no_prose_returns_none(self):
        r = parse_intelligence_response({"claims": []})
        self.assertIsNone(r)

    def test_unknown_confidence_falls_back(self):
        r = parse_intelligence_response({
            "prose": "x", "claims": [], "confidence": "WHATEVER",
            "unknown_fields": [],
        })
        self.assertEqual(r.confidence, "UNKNOWN")


class TestTokenizer(unittest.TestCase):

    def test_russian_tokens(self):
        tokens = _tokenize("Главный обвиняемый Nicholas Mitchell")
        self.assertIn("главный", tokens)
        self.assertIn("nicholas", tokens)

    def test_english_tokens(self):
        tokens = _tokenize("Photos lack EXIF metadata")
        self.assertIn("photos", tokens)
        self.assertIn("metadata", tokens)

    def test_hyphenated_word(self):
        tokens = _tokenize("federal-grade analyst")
        self.assertIn("federal-grade", tokens)

    def test_short_words_dropped(self):
        tokens = _tokenize("a is to be")
        self.assertEqual(tokens, [])

    def test_pure_digits_dropped(self):
        tokens = _tokenize("there are 55 files")
        # 'there', 'are' → stopwords; '55' digit dropped; 'files' kept.
        self.assertEqual(tokens, ["files"])


class TestSemanticAlignment(unittest.TestCase):

    def test_aligned_text_passes(self):
        ctx = "Nicholas Mitchell is principal of ITC Ventures LLC"
        self.assertTrue(semantic_alignment(
            "Nicholas Mitchell controls ITC Ventures",
            ctx,
        ))

    def test_unrelated_text_fails(self):
        ctx = "Nicholas Mitchell is principal of ITC Ventures LLC"
        self.assertFalse(semantic_alignment(
            "Lorem ipsum dolor sit amet consectetur adipiscing",
            ctx,
        ))

    def test_short_claim_passes(self):
        # Less than 3 tokens — too short to verify, default True.
        self.assertTrue(semantic_alignment("ok", "anything"))


class TestValidator(unittest.TestCase):

    def test_known_source_clean(self):
        claims = (
            GroundedClaim(text="EXIF metadata missing on photos",
                          sources=(CitationSource(family="G", num="1"),)),
        )
        issues = validate_grounded_claims(claims, _FAKE_CTX)
        self.assertEqual(issues, [])

    def test_unknown_source_flagged(self):
        claims = (
            GroundedClaim(text="Bogus claim",
                          sources=(CitationSource(family="G", num="99"),)),
        )
        issues = validate_grounded_claims(claims, _FAKE_CTX)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].reason, "unknown_id")
        self.assertEqual(issues[0].source_repr, "G99")

    def test_content_mismatch_flagged(self):
        # Source resolves but claim text has zero overlap with G1's content.
        claims = (
            GroundedClaim(text="Lorem ipsum dolor sit amet consectetur",
                          sources=(CitationSource(family="G", num="1"),)),
        )
        issues = validate_grounded_claims(claims, _FAKE_CTX)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].reason, "content_mismatch")

    def test_case_family_no_content_check(self):
        # CASE family is not in _FACTUAL_FAMILIES — only unknown_id check.
        claims = (
            GroundedClaim(text="Wholly unrelated claim text",
                          sources=(CitationSource(family="CASE", qual1="status"),)),
        )
        issues = validate_grounded_claims(claims, _FAKE_CTX)
        self.assertEqual(issues, [])

    def test_multi_source_partial(self):
        claims = (
            GroundedClaim(text="EXIF metadata missing", sources=(
                CitationSource(family="G", num="1"),    # known + aligned
                CitationSource(family="G", num="99"),   # unknown
            )),
        )
        issues = validate_grounded_claims(claims, _FAKE_CTX)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].reason, "unknown_id")

    def test_empty_claims_clean(self):
        self.assertEqual(validate_grounded_claims((), _FAKE_CTX), [])
        self.assertEqual(validate_grounded_claims(None, _FAKE_CTX), [])

    def test_ins_qual1_qual2(self):
        # INS with two qualifiers — fake context has matching key.
        ctx = {"inspections": {"02_Documents/Reports": {"total": 35, "inspected_complete": 35}}}
        claims = (
            GroundedClaim(text="Reports inspected complete",
                          sources=(CitationSource(family="INS",
                                                  qual1="02_Documents",
                                                  qual2="Reports"),)),
        )
        issues = validate_grounded_claims(claims, ctx)
        self.assertEqual(issues, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
