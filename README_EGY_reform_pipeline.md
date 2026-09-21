# EGY IMF Reform-Event LLM Pipeline

This package is tailored to the uploaded `EGY.zip`. The archive contains 25 included/downloaded IMF reports for Egypt spanning 2005–2026.

## Reform categories

1. Public Financial Management (PFM)
2. Public Investment Management (PIM)
3. Public Procurement
4. SOE Governance / State Footprint
5. Fiscal Decentralization

Fiscal Decentralization is coded only when the evidence changes fiscal authority/resources across levels of government (expenditure assignments, own-source revenues, intergovernmental transfers, subnational borrowing/fiscal rules, or local PFM/accountability). Mere references to governorates or local governments are excluded.

## Why this is not a simple keyword or scoring exercise

The first stage uses keywords only to retrieve candidate pages. The LLM then distinguishes government actions/commitments from IMF recommendations and background descriptions. It also classifies implementation status. The LLM never assigns a subjective 0–3 reform quality score.

Repeated mentions are preserved as lifecycle observations: a reform can move from commitment to delayed/not-met to implemented across different IMF reviews.

## Install

```bash
pip install -r requirements_egy_reform.txt
```

## Step 1: Preflight without an LLM

```bash
python egy_reform_llm_pipeline.py --zip EGY.zip --out EGY_reform_output --provider none
```

This checks the archive, extracts PDF text, and creates candidate chunks. No API call is made.

## Step 2A: Run with OpenAI

Windows PowerShell:

```powershell
$env:OPENAI_API_KEY="YOUR_KEY"
python egy_reform_llm_pipeline.py --zip EGY.zip --out EGY_reform_output --provider openai --model gpt-5.6-terra
```

For a small end-to-end test first:

```powershell
python egy_reform_llm_pipeline.py --zip EGY.zip --out EGY_reform_test --provider openai --model gpt-5.6-terra --max-chunks 3
```

The script caches each LLM response. If the run stops, rerun the same command; completed chunks are reused.

## Step 2B: Optional local Ollama

```bash
python egy_reform_llm_pipeline.py --zip EGY.zip --out EGY_reform_output --provider ollama --model qwen3:14b
```

You can set a different server with `OLLAMA_URL`.

## Final outputs

- `01_document_inventory.csv` — source report metadata
- `02_candidate_chunks.jsonl` — candidate text sent to the LLM, with page markers
- `03_reform_mentions_raw.jsonl` — raw LLM JSON, preserved for audit
- `04_reform_observations.csv` — report-level reform/status observations
- `05_reform_lifecycle.csv` — repeated observations linked into reform lifecycles
- `06_country_year_category_panel.csv` — merge-ready country-year variables
- `07_reform_audit.xlsx` — audit workbook with documents, observations, lifecycle, panel, definitions, and coding rules

## Recommended validation

Before scaling to other countries, manually code a stratified sample of Egypt observations, including positive cases and false-positive candidate pages. Report precision/recall or agreement for: category, panel eligibility, policy year, and implementation status. Lock the coding rules before expanding the sample.
