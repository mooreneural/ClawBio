"""Tests for the pharmgx-reporter eval inputs under evals/.

The scenario-2 input uses the AncestryDNA layout but holds the 23 Corpasome
SNPs of demo_patient.txt. These tests keep its source note true and check that
the reporter still reads the file as AncestryDNA.
"""

import sys
from pathlib import Path

from hypothesis import HealthCheck, assume, example, given, settings, strategies as st

from clawbio.common.parsers import parse_23andme, parse_ancestry

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))

from pharmgx_reporter import parse_file  # noqa: E402

DEMO = SKILL_DIR / "demo_patient.txt"
ANCESTRY_INPUT = (SKILL_DIR / "evals" / "019e82fd-c7f1-76ed-9b4b-63bbfd454ebd"
                  / "scenario-2" / "inputs" / "ancestry_patient.txt")
LINES = ANCESTRY_INPUT.read_text(encoding="utf-8").splitlines()


def _calls(records: dict) -> dict:
    return {rsid: (rec.chrom, rec.pos, rec.genotype) for rsid, rec in records.items()}


def test_ancestry_input_states_its_source():
    notes = [line for line in LINES if line.startswith("# Source:")]
    assert len(notes) == 1, "ancestry_patient.txt needs one '# Source:' comment line"
    for fact in ("Corpasome", "CC0", "demo_patient.txt", "not an export"):
        assert fact in notes[0]


def test_ancestry_input_holds_the_same_23_snps_as_demo_patient():
    calls = _calls(parse_ancestry(ANCESTRY_INPUT))
    assert len(calls) == 23
    assert calls == _calls(parse_23andme(DEMO))


def test_reporter_reads_ancestry_input_as_ancestrydna_with_the_demo_calls():
    fmt, *read = parse_file(ANCESTRY_INPUT)
    demo_fmt, *demo_read = parse_file(DEMO)
    assert (fmt, demo_fmt) == ("ancestry", "23andme")
    assert read == demo_read


# detect_format (clawbio/common/parsers.py) scans from the top, comment lines
# included. It returns "vcf" at a line starting "##fileformat=VCF", and
# otherwise at the first line that holds "rsid" with known column names: here
# the "# rsid ... allele1 allele2" comment. parse_ancestry skips every "#"
# line. So a comment can change how this file is read only above that column
# comment, and only if it holds "rsid" or starts "##fileformat=VCF".
COLUMN_COMMENT = LINES.index("# rsid\tchromosome\tposition\tallele1\tallele2")

# The words detect_format looks for, then free text without line breaks.
_DETECTION_WORDS = st.sampled_from(
    ["rsid", "RSID", "chromosome", "genotype", "result", "allele1", "#fileformat=VCF"])
_FREE_TEXT = st.text(st.characters(codec="utf-8", exclude_characters="\r\n"), max_size=20)
_COMMENTS = st.builds(lambda words, text: "#" + " ".join(words) + text,
                      st.lists(_DETECTION_WORDS, max_size=6), _FREE_TEXT)
# Half of the positions fall in the header, where detection happens.
_POSITIONS = st.integers(0, COLUMN_COMMENT + 1) | st.integers(0, len(LINES))


def _could_change_detection(comment: str, at: int) -> bool:
    return at <= COLUMN_COMMENT and (
        "rsid" in comment.lower() or comment.startswith("##fileformat=VCF"))


@settings(deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@example(comment="# rsid chromosome genotype", at=COLUMN_COMMENT + 1)
@example(comment="##fileformat=VCFv4.2", at=COLUMN_COMMENT + 1)
@example(comment="# Source: genotype chromosome result", at=0)
# The guard must skip these two. Pinning them makes the test fail in every run
# if the guard misses either one, not only when Hypothesis draws such a comment.
@example(comment="# rsid chromosome genotype", at=0)
@example(comment="##fileformat=VCF", at=0)
@given(comment=_COMMENTS, at=_POSITIONS)
def test_a_comment_line_changes_the_read_only_where_detection_can_match_it(
        tmp_path, comment, at):
    assume(not _could_change_detection(comment, at))
    lines = list(LINES)
    lines.insert(at, comment)
    path = tmp_path / "ancestry_patient.txt"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert parse_file(path) == parse_file(ANCESTRY_INPUT)
