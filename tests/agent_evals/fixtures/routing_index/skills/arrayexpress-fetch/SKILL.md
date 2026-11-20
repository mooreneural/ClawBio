## Trigger

**Fire this skill when the user says any of:**
- "ArrayExpress"
- "E-MTAB-1234", "E-GEOD-...", "E-MEXP-...", "E-PROT-..." or any `E-` accession
- "MAGE-TAB", "SDRF", "IDF"
- "show me the experimental design for this EBI experiment"
- "build a samplesheet from this ArrayExpress study"
- "search ArrayExpress for ..."

**Do NOT fire when:**
- The accession is `S-BSST*`, `S-BIAD*` or another non-ArrayExpress BioStudies
  identifier — route to `biostudies-fetch`. (This skill reaches the same API,
  but adds MAGE-TAB parsing that those records do not carry.)
- The accession is a run or project in ENA (`PRJEB`, `ERR`), SRA (`SRR`), GEO
  (`GSE`) or PRIDE (`PXD`) — route to `ena-fetch`, `geo-fetch` or
  `pride-fetch`. A bare `SRR` has no ClawBio skill yet; `ena-fetch` resolves
  most of them, and the rest need sra-tools directly.
- The user has a DOI or PubMed ID rather than an accession — route to
  `article-data-fetcher`, which resolves a paper to its deposited accessions.
  Chain back here once it has them.
- The user wants to *run* a pipeline. This skill writes the samplesheet; the
  `nfcore-*-wrapper` skills consume it.

## Why This Exists

- **Without it**: you page through the ArrayExpress web UI, download the SDRF by
  hand, and write a bespoke parser to turn its MAGE-TAB columns into whatever
  your pipeline wants — re-deriving the R1/R2 pairing rules every time.
- **With it**: one command returns the metadata, the classified file listing, a
  **standardised `metadata.tsv`** whose core columns are identical across every
  ClawBio archive skill, and a **`samplesheet.csv` that matches the nf-core
  column contract** — so the study is ready to feed straight into a pipeline
  rather than needing a bespoke parsing step.
- **Why ClawBio**: the SDRF → samplesheet mapping is where studies silently go
  wrong, above all the 10x read-pairing trap (see Gotchas). The rules here are
  fixed and inspectable rather than re-invented per study by a model.

## Core Capabilities
