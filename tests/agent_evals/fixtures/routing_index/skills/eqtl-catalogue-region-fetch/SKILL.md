## Overview

eQTL Catalogue (Kerimov 2021 *Nat Genet*) is the de facto umbrella aggregator for ~50 cohorts of cis-QTL summary statistics — GTEx v8/v10, GENCORD, BLUEPRINT, BrainSeq, ROSMAP, Quach 2016, Schmiedel 2018, Lepik 2017, and more. Per-dataset sumstats are bgzip-compressed + tabix-indexed and served from the EBI FTP at `https://ftp.ebi.ac.uk/pub/databases/spot/eQTL/sumstats/<QTS>/<QTD>/<QTD>.all.tsv.gz`. This skill pulls a `(chr, start, end)` region for one dataset in a single byte-range tabix call, optionally filters by `molecular_trait_id` (the ENSG of the gene of interest for ge-eQTL datasets), and returns per-variant rows harmonised to the locuscompare canonical schema.

## Trigger

**Fire when** the user (or upstream agent step) wants:

- A regional slice of cis-eQTL summary statistics (β, SE, p-value) for variants around a gene's TSS, from one (study × tissue × quant_method) in eQTL Catalogue.
- Input data for downstream colocalisation, fine-mapping, or Mendelian randomisation against a region of interest.
- Provenance-rich, harmonised eQTL summary stats with allele orientation preserved (ALT-effect β).

**Do NOT fire when** the user wants:

- A **point lookup of one variant in one tissue**: query the GTEx Portal REST API (`https://gtexportal.org/api/v2/`) directly for single-variant queries.
- **All eQTLs for a gene across all tissues**: this skill returns one (study × tissue × quant_method) at a time. Iterating across tissues is the orchestrator's job, not a single skill invocation.
- **Protein QTLs beyond one plasma dataset**: the catalogue carries a single protein dataset, QTD000584 (Sun 2018 INTERVAL plasma SomaScan data, which the catalogue reprocessed and added in release 6 as its first protein QTL dataset: 3,215 aptamers, cis only, n = 3,301, `quant_method` `aptamer`; 1 of the 758 datasets in the bundled table; Sun BB, Maranville JC, Peters JE, et al. Genomic atlas of the human plasma proteome. Nature. 2018;558(7708):73-79. doi:10.1038/s41586-018-0175-2. PMID: 29875488), and this skill fetches it like any other: over the 1 Mb SORT1 window it returns rows for 11 aptamers covering 10 genes (gotcha 6), so pass `gene_id` or `molecular_trait_id` to keep one. For UKB-PPP plasma cis-pQTL, use the `ukb-ppp-region-fetch` skill (Sun 2023 Nature, Synapse-backed); other proteomic cohorts, such as deCODE, are not in the catalogue.
- **trans-eQTL data**: eQTL Catalogue's cis-window is ±1 Mb of TSS; trans-eQTL signals are at distant variants and require a different upstream (e.g., eQTLGen for blood trans).
- **Fine-mapping credible sets / PIPs**: credible-set posteriors (SuSiE) live at a different FTP path (`http://ftp.ebi.ac.uk/pub/databases/spot/eQTL/susie/`) and require a separate skill. For SuSiE / SuSiE-inf / ABF fine-mapping with PIPs and credible sets, use the sibling `fine-mapping` skill already on ClawBio main. The nominal-pass `.all.tsv.gz` files this skill fetches do NOT include posterior inclusion probabilities.

## Scope

**One skill, one task.** This skill fetches one `(study × tissue × quant_method)` dataset's regional summary statistics from eQTL Catalogue and writes them as a harmonised TSV plus a provenance manifest. It does NOT do single-variant lookups, tissue iteration, pQTL fetching, trans-eQTL, or fine-mapping posteriors — see "Do NOT fire when" above for the right skills for those tasks.

## Workflow
