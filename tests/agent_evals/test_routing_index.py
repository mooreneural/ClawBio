"""Tests for the routing index that the routing tier of the model evaluation puts in its prompt.

They need no model and no network, and they read only the frozen fixtures in
fixtures/routing_index/, never the live catalog or SKILL.md files, so a skill PR
cannot break them. The fixtures were copied at commit 9a35f8e7: the catalog.json
entries of ten skills and, for each, a verbatim line range of its SKILL.md around
the Trigger section (arrayexpress-fetch 97-135, claw-methylation-cycle 63-96,
de-summary 55-80, dnasp 245-259, eqtl-catalogue-region-fetch 89-113, hla-typing 60-86,
nutrigx 56-87, pharmgx-reporter 33-50, pubmed-summariser 62-78, ukb-ppp-region-fetch
115-139). The entries quoted in NAMED come from SKILL.md files at the same commit.
Every skill quoted here declares MIT; the skills in FOLDER_ONLY of
tests/test_licence_policy.py are under other licences and are not copied.
"""
import importlib.util
import json
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from hypothesis import example, given, strategies as st

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures" / "routing_index"


def _load():
    path = HERE / "routing_index.py"
    spec = importlib.util.spec_from_file_location("clawbio_routing_index", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # @dataclass reads sys.modules[cls.__module__]
    spec.loader.exec_module(module)
    return module


MOD = _load()
CATALOG = json.loads((FIXTURES / "skills" / "catalog.json").read_text(encoding="utf-8"))
SKILL_MD = {s["name"]: (FIXTURES / "skills" / s["name"] / "SKILL.md").read_text(encoding="utf-8")
            for s in CATALOG["skills"]}

# The skills each fixture's entries point to: one tuple per entry, in order.
EXPECTED = {
    "arrayexpress-fetch": [("biostudies-fetch",), ("ena-fetch", "geo-fetch", "pride-fetch"),
                           ("article-data-fetcher",), ()],  # `nfcore-*-wrapper` is a pattern, not a skill
    "claw-methylation-cycle": [(), (), (), (), ()],  # "use PharmGx Reporter" gives no skill name
    "de-summary": [("rnaseq-de",), (), ()],  # its Fire list names `rnaseq-de` too, which is not an entry
    "dnasp": [()],  # one sentence in a paragraph
    "eqtl-catalogue-region-fetch": [(), (), ("ukb-ppp-region-fetch",), (), ("fine-mapping",)],
    "hla-typing": [("vcf-annotator",), ("pharmgx-reporter",)],
    "nutrigx": [],  # "## Trigger Phrases" lists only what fires it
    "pharmgx-reporter": [],  # no Trigger section
    "pubmed-summariser": [()],  # one bullet
    # Its Fire list says (see "Do NOT fire" item on trans ...): a mention, not a rule.
    "ukb-ppp-region-fetch": [("eqtl-catalogue-region-fetch",), (), (), (), ()],
}


@pytest.mark.parametrize("name", sorted(SKILL_MD))
def test_each_fixture_gives_its_entries_and_the_skills_they_point_to(name):
    assert [e.alternatives for e in MOD.parse_do_not_fire(SKILL_MD[name])] == EXPECTED[name]


def test_the_bold_line_and_the_section_after_a_list_are_not_entries():
    assert MOD.parse_do_not_fire(SKILL_MD["hla-typing"]) == [
        MOD.DoNotFire("The user asks for general variant annotation (use vcf-annotator)", ("vcf-annotator",)),
        MOD.DoNotFire("The user asks for pharmacogenomics (use pharmgx-reporter)", ("pharmgx-reporter",)),
    ]


def test_a_wrapped_bullet_is_one_entry():
    assert MOD.parse_do_not_fire(SKILL_MD["arrayexpress-fetch"])[0].text == (
        "The accession is `S-BSST*`, `S-BIAD*` or another non-ArrayExpress BioStudies identifier"
        " — route to `biostudies-fetch`. (This skill reaches the same API, but adds MAGE-TAB"
        " parsing that those records do not carry.)")


@pytest.mark.parametrize("name,text", [
    ("pubmed-summariser", "for patient interpretation, systematic reviews, full-text extraction,"
                          " or multi-source evidence synthesis."),
    ("dnasp", "for sequence alignment, read mapping, haplotype phasing, clinical advice, or significance"
              " tests other than the coalescent P-values `--n-sim` gives for D, R2 and Fs."),
])
def test_a_rule_in_a_single_bullet_or_sentence_is_one_entry(name, text):
    assert MOD.parse_do_not_fire(SKILL_MD[name]) == [MOD.DoNotFire(text)]


def test_a_bold_label_with_its_rule_on_the_same_line_is_one_entry():
    """The form skills/clawpathy-autoresearch/examples/champions/yengo_height_ldsc_h2.md uses."""
    rule = ("partitioned h² (needs baselineLD annotation files in --ref-ld-chr), genetic correlation, PRS,"
            " any trait other than height.")
    assert MOD.parse_do_not_fire(f"## Trigger\n\n**Do NOT fire when:** {rule}\n") == [MOD.DoNotFire(rule)]


def test_only_the_trigger_section_is_read():
    doc = ("## Gotchas\n\n**Do NOT fire when:**\n- before\n\n## Trigger\n\n**Do NOT fire when:**\n- inside\n\n"
           "## Scope\n\nSee \"Do NOT fire when\" above.\n\n**Do NOT fire when:**\n- after\n")
    assert [e.text for e in MOD.parse_do_not_fire(doc)] == ["inside"]


def test_a_code_fence_neither_ends_the_trigger_section_nor_holds_a_list():
    doc = ("## Trigger\n\n```bash\n# run the demo first\npython clawbio.py run x --demo\n```\n\n"
           "**Do NOT fire when:**\n- real\n\n## Example\n\n"
           "```markdown\n## Trigger\n\n**Do NOT fire when:**\n- quoted template\n```\n")
    assert [e.text for e in MOD.parse_do_not_fire(doc)] == ["real"]


# Header lines as the SKILL.md files write them.
HEADERS = [
    "**Do NOT fire when:**",  # most skills
    "**Do NOT fire this skill when:**",  # claw-methylation-cycle
    "**Do NOT fire when the user asks to:**",  # claw-amplicon-qc
    "**Do NOT fire when** the user wants:",  # eqtl-catalogue-region-fetch and four others
    "**Do NOT fire when**:",  # pathway-enricher
]


@pytest.mark.parametrize("gap", ["\n", "\n\n"])
@pytest.mark.parametrize("header", HEADERS)
def test_each_header_form_opens_a_list(header, gap):
    doc = f"## Trigger\n\n**Fire when:**\n- yes\n\n{header}{gap}- no one\n- no two\n"
    assert [e.text for e in MOD.parse_do_not_fire(doc)] == ["no one", "no two"]


# Entries from skills/*/SKILL.md at 9a35f8e7 (skill in the comment) and the skills each points to.
NAMED = [
    ("User asks about general ancestry or population structure (use claw-ancestry-pca)",
     ["claw-ancestry-pca"]),  # archaic-introgression
    ("The user asks for **single-patient pharmacogenomics** (use `pharmgx-reporter`)",
     ["pharmgx-reporter"]),  # drug-repurposing-screen
    ("User asks to annotate variants or genes (use `vcf-annotator` or `clinpgx`)",
     ["vcf-annotator", "clinpgx"]),  # bgpt-mcp
    ("The user asks for drug-gene interactions — route to `pharmgx-reporter` or `clinpgx`.",
     ["pharmgx-reporter", "clinpgx"]),  # xena-tcga-gene-query
    ("The user wants k-mer/distance trees only → use `fastreer` instead", ["fastreer"]),  # phylogenetics-builder
    ("the user wants a score *computed* (`gwas-prs` / `prs`, `just-prs-mcp`, `wgs-prs`)",
     ["gwas-prs", "prs", "just-prs-mcp", "wgs-prs"]),  # prs-applicability-gate
    ("The user wants cells counted or outlined in an image. That is `cell-detection`.", ["cell-detection"]),  # deepspot-m
    ("A **point lookup of one variant in one GWAS** - `database-lookup` or `gwas-lookup` is the right skill"
     " for single-variant queries.", ["database-lookup", "gwas-lookup"]),  # gwas-catalog-region-fetch
    ("User has single-cell FASTQs or wants `.h5ad` -> route to `nfcore-scrnaseq-wrapper`",
     ["nfcore-scrnaseq-wrapper"]),  # nfcore-rnaseq-wrapper
    ("The user asks about variant effect on splicing → use `variant-annotation` (VEP) or chain `gi-splice`"
     " ref/alt comparisons", ["variant-annotation", "gi-splice"]),  # gi-splice
    # Backticked words that are a command, a field and a tool, and a skill described rather than named.
    ("The user wants the *first* N reads (`head`), not a random sample: do that directly and say it is not"
     " random.", []),  # seqtk-downsample
    ("there is no embedding in `obsm` yet; this skill scores embeddings, it does not make them (the PCA"
     " baseline is a reference, not an output)", []),  # scrna-embedding-audit
    ("**Numeric coloc statistics only** - use `coloc.abf`, SuSiE-coloc, SharePro, or take H4 from Open"
     " Targets.", []),  # locuscompare-region-render
    ("Process shotgun metagenomic data (route to a metagenomics skill instead)", []),  # claw-amplicon-qc
]


@pytest.mark.parametrize("entry,names", NAMED)
def test_the_skills_an_entry_points_to(entry, names):
    doc = f"## Trigger\n\n**Do NOT fire when:**\n- {entry}\n"
    assert MOD.parse_do_not_fire(doc) == [MOD.DoNotFire(entry, tuple(names))]


# --- Properties -------------------------------------------------------------

HYPHENATED = st.from_regex(r"[a-z][a-z0-9]{0,5}(-[a-z0-9]{1,5}){1,2}", fullmatch=True)
ONE_WORD = st.from_regex(r"[a-z][a-z0-9]{1,7}", fullmatch=True)  # like dnasp; only counted in backticks
LETTERS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
# Free text has no backticks and no hyphens, so on its own it names no skill.
FREE_TEXT = st.tuples(st.text(LETTERS, min_size=1, max_size=8),
                      st.lists(st.text(LETTERS + "0123456789.,;:()'\"/éπ→—", min_size=1, max_size=10),
                               max_size=12)).map(lambda t: " ".join([t[0], *t[1]]))
POINTERS = ["(use {})", "— route to {}", "→ {}", "→ use {} instead", "(that is {})"]


@st.composite
def do_not_fire_entries(draw):
    text = draw(FREE_TEXT)
    names = draw(st.lists(HYPHENATED | ONE_WORD, max_size=3, unique=True))
    if names:
        shown = [n if "-" in n and draw(st.booleans()) else f"`{n}`" for n in names]
        joined = shown[0] + "".join(draw(st.sampled_from([", ", " or ", " / ", " and "])) + n for n in shown[1:])
        text += " " + draw(st.sampled_from(POINTERS)).format(joined)
    return MOD.DoNotFire(text, tuple(names))


@st.composite
def written_lists(draw):
    """A Trigger section holding a Do NOT fire list laid out as SKILL.md authors do: its lines and entries."""
    entries = draw(st.lists(do_not_fire_entries(), min_size=1, max_size=5))
    indent = " " * draw(st.integers(0, 3))
    continuation = indent + " " * draw(st.integers(1, 4))
    marker = draw(st.sampled_from(["-", "*", "+", "1."]))
    width = draw(st.integers(10, 100))
    lines = ["## Trigger", "", "**Fire when:**", "- yes", "", draw(st.sampled_from(HEADERS))]
    lines += [""] * draw(st.integers(0, 1))
    for k, entry in enumerate(entries):
        if k:
            lines += [""] * draw(st.integers(0, 1))  # a loose list
        first, *rest = textwrap.wrap(entry.text, width, break_long_words=False, break_on_hyphens=False)
        lines.append(f"{indent}{f'{k + 1}.' if marker == '1.' else marker} {first}")
        lines += [continuation + line for line in rest]
    return lines, entries


NEWLINES = st.sampled_from(["\n", "\r\n"])


@given(written_lists(), st.sampled_from([[], ["", "## Scope", "", "One task."]]), NEWLINES)
# A wrapped line that starts like an ordered-list marker is still the same entry.
@example((["## Trigger", "", "**Do NOT fire when:**", "- The user wants step 1. trim and", "  2. align (use `seq-wrangler`)"],
          [MOD.DoNotFire("The user wants step 1. trim and 2. align (use `seq-wrangler`)", ("seq-wrangler",))]),
         [], "\n")
def test_entries_written_as_a_list_parse_back_unchanged(case, after, newline):
    lines, entries = case
    assert MOD.parse_do_not_fire(newline.join(lines + after) + newline) == entries


# No "f" or "i", so a trailer can never spell "Do NOT fire" or "Trigger".
TRAILER_WORDS = st.lists(st.text("abcdeghjklmnopqrstuvwxyz", min_size=1, max_size=8),
                         min_size=1, max_size=4).map(" ".join)
TRAILERS = st.one_of(
    st.builds(lambda level, words: f"{'#' * level} {words}", st.integers(1, 6), TRAILER_WORDS),
    st.builds(lambda label, words: f"**{label}:** {words}", TRAILER_WORDS, TRAILER_WORDS),
    TRAILER_WORDS,  # a plain paragraph
    st.sampled_from(["---", "***", "| a | b |"]),
)


@given(written_lists(), TRAILERS, st.integers(0, 2), NEWLINES)
def test_text_after_the_list_never_leaks_into_its_entries(case, trailer, blanks, newline):
    lines, entries = case
    doc = newline.join(lines + [""] * blanks + [trailer, "after"]) + newline
    assert MOD.parse_do_not_fire(doc) == entries


ANY_TEXT = st.text(max_size=40)  # newlines and odd whitespace included


@st.composite
def catalogs(draw):
    """A catalog's skills, plus SKILL.md texts for some of them and for a skill outside the catalog."""
    names = draw(st.lists(HYPHENATED | ONE_WORD, max_size=6, unique=True))
    skills = [{"name": n, "cli_alias": draw(st.none() | ONE_WORD), "description": draw(ANY_TEXT),
               "trigger_keywords": draw(st.lists(ANY_TEXT, max_size=3)), "status": "planned"} for n in names]
    docs = st.one_of(ANY_TEXT, written_lists().map(lambda case: "\n".join(case[0])))
    skill_md = {n: draw(docs) for n in names + [draw(HYPHENATED)] if draw(st.booleans())}
    return skills, skill_md


@given(catalogs(), st.data())
def test_rendered_index_does_not_depend_on_catalog_order(case, data):
    skills, skill_md = case
    shuffled = data.draw(st.permutations(skills))
    assert (MOD.render_index(MOD.build_index({"skills": shuffled}, skill_md))
            == MOD.render_index(MOD.build_index({"skills": skills}, skill_md)))


@given(catalogs())
def test_every_catalog_skill_appears_exactly_once(case):
    skills, skill_md = case
    names = sorted(s["name"] for s in skills)
    index = MOD.build_index({"skills": skills}, skill_md)
    assert [e.name for e in index] == names
    text = MOD.render_index(index)
    blocks = text.split("\n\n") if text else []
    assert [block.split("\n")[0].split(" | alias: ")[0] for block in blocks] == names


# --- Index, rendering and CLI -----------------------------------------------

def test_index_has_one_entry_per_catalog_skill_with_its_catalog_fields():
    index = MOD.build_index(CATALOG, SKILL_MD)
    assert [e.name for e in index] == sorted(SKILL_MD)
    for skill, entry in zip(sorted(CATALOG["skills"], key=lambda s: s["name"]), index):
        assert (entry.cli_alias, entry.description, entry.trigger_keywords) == (
            skill["cli_alias"], skill["description"], tuple(skill["trigger_keywords"]))
        assert list(entry.do_not_fire) == MOD.parse_do_not_fire(SKILL_MD[skill["name"]])


def test_a_skill_without_skill_md_has_no_entries_and_other_skill_md_files_are_ignored():
    pharmgx = next(s for s in CATALOG["skills"] if s["name"] == "pharmgx-reporter")
    index = MOD.build_index({"skills": [pharmgx]}, {"wes-clinical-report-es": SKILL_MD["hla-typing"]})
    assert [(e.name, e.do_not_fire) for e in index] == [("pharmgx-reporter", ())]


def test_a_catalog_listing_a_skill_twice_is_refused():
    skill = CATALOG["skills"][0]
    with pytest.raises(ValueError, match=skill["name"]):
        MOD.build_index({"skills": [skill, skill]}, {})


def test_render_writes_one_block_per_skill_with_each_field_on_one_line():
    index = [
        MOD.IndexEntry("hla-typing", None, "HLA allele typing\n  from VCF data", ("HLA", "allele typing"),
                       (MOD.DoNotFire("The user asks for pharmacogenomics (use pharmgx-reporter)",
                                      ("pharmgx-reporter",)),)),
        MOD.IndexEntry("pharmgx-reporter", "pharmgx", "Pharmacogenomic report", (), ()),
    ]
    assert MOD.render_index(index) == (
        "hla-typing | alias: none\n"
        "HLA allele typing from VCF data\n"
        "keywords: HLA; allele typing\n"
        "Do NOT fire:\n"
        "- The user asks for pharmacogenomics (use pharmgx-reporter)\n"
        "\n"
        "pharmgx-reporter | alias: pharmgx\n"
        "Pharmacogenomic report\n"
    )


def test_cli_prints_the_size_of_the_index_and_opens_files_as_utf8():
    """-X warn_default_encoding makes any open() without an encoding an error,
    so a missing encoding="utf-8" fails here on Linux as well as on Windows."""
    proc = subprocess.run(
        [sys.executable, "-X", "warn_default_encoding", "-W", "error::EncodingWarning",
         str(HERE / "routing_index.py"), "--root", str(FIXTURES)],
        capture_output=True, text=True, encoding="utf-8")
    assert proc.returncode == 0, proc.stderr
    chars = len(MOD.render_index(MOD.build_index(CATALOG, SKILL_MD)))
    assert proc.stdout.splitlines() == [
        "skills: 10",
        "SKILL.md files with Do NOT fire entries: 8",
        "Do NOT fire entries: 26",
        "entries naming an alternative: 9",
        f"characters: {chars}",
        f"characters / 4: {chars // 4}",
    ]


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_no_fixture_is_dropped_by_gitignore():
    """The root .gitignore drops data/, output/, result.json and *.log at any depth;
    a fixture under such a name would be missing from every clone."""
    paths = [p.relative_to(MOD.REPO).as_posix() for p in FIXTURES.rglob("*") if p.is_file()]
    proc = subprocess.run(["git", "check-ignore", "--no-index", "--stdin"], cwd=MOD.REPO,
                          input="\n".join(paths), capture_output=True, text=True)
    assert paths and proc.stdout == ""
