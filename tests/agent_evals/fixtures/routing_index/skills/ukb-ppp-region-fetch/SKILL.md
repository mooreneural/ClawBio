## Overview

UKB-PPP (Sun et al. 2023 *Nature*) is the largest open-access plasma proteomic GWAS resource, measuring 2,923 unique proteins (Olink Explore 3072) in 54,219 participants (Abstract and Results), with pQTL mapping in a European-ancestry discovery cohort (n = 34,557) and a replication cohort (n = 17,806), five non-European strata (AFR 931, CSA 920, MID 308, EAS 262, AMR 97) and the full cohort analyzed together (n = 52,363). Summary statistics are released per (protein × ancestry) as REGENIE step-2 outputs and packaged as `<HGNC>_<UniProt>_<OlinkID>_v1_<Panel>.tar` archives on Synapse (`syn51364943`). Each tar contains one gzipped REGENIE file per autosome + X. This skill resolves a protein label (HGNC or UniProt) to the canonical Synapse fileID, downloads the protein's tar to a local cache, extracts the per-chromosome file, filters to a `(chr, start, end)` window, and emits a harmonised TSV slice plus a provenance manifest.

## Trigger

**Fire when** the user (or upstream agent step) wants:

- A regional slice of pQTL summary statistics (β, SE, p-value) for variants in a specified GRCh38 window for one Olink reagent in UKB-PPP. Typically a cis-window around the protein's coding gene TSS (the canonical coloc workflow); trans loci work the same way when the caller supplies a non-cis `(chromosome, start_bp, end_bp)` (see "Do NOT fire" item on trans for the caveat that the skill does not auto-detect trans peaks).
- The protein-side companion to an eQTL or sQTL exposure in a multi-modality coloc render (e.g. SORT1 eQTL liver × pQTL plasma × LDL-C GWAS).
- Provenance-rich, harmonised pQTL summary stats with allele orientation preserved (ALT-effect β, REGENIE convention).

**Do NOT fire when** the user wants:

- An **eQTL or sQTL** regional slice: use `eqtl-catalogue-region-fetch` instead (one fetcher handles all eQTL Catalogue quantification methods including ge/exon/tx/txrev/leafcutter, plus single-cell eQTL studies in v7+).
- A **single-variant pQTL lookup**: UKB-PPP's full archive is large; a per-variant lookup against the Open Targets pQTL coloc table is cheaper for one-point queries.
- The **deCODE pQTL** panel (Ferkingstad 2021): a different upstream cohort with separate access terms; this skill targets UKB-PPP only. Choose the upstream-source skill at the orchestrator level.
- The **raw Olink abundance values** linked to phenotypes: those are gated behind a UK Biobank Application via Synapse `syn52364558` and are out of scope for the public locuscompare render path.
- **trans-pQTL signals** at distant loci: UKB-PPP releases full-genome summary stats per protein, so trans signals are present in the data, but the `(chromosome, start_bp, end_bp)` window must be supplied explicitly; the skill does not auto-detect trans peaks.

## Scope

**One skill, one task.** This skill fetches one `(protein × ancestry)` pair's regional summary statistics from UKB-PPP and writes them as a harmonised TSV plus a provenance manifest. It does NOT iterate proteins, ancestries, or windows; it does NOT do pQTL fine-mapping or coloc directly; it does NOT fetch eQTL / sQTL / sceQTL (use `eqtl-catalogue-region-fetch`); it does NOT fetch the deCODE pQTL panel. The caller composes those workflows on top.

## Workflow
