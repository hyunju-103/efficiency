#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
EGY IMF Reform Event Extraction Pipeline
========================================

Purpose
-------
Convert IMF Egypt reports in EGY.zip (2005-2026) into an auditable reform-event
panel for efficiency research using a candidate-retrieval + LLM extraction design.

Five reform categories
----------------------
1. Public Financial Management (PFM)
2. Public Investment Management (PIM)
3. Public Procurement
4. SOE Governance / State Footprint
5. Fiscal Decentralization

Outputs
-------
01_document_inventory.csv
02_candidate_chunks.jsonl
03_reform_mentions_raw.jsonl
04_reform_observations.csv
05_reform_lifecycle.csv
06_country_year_category_panel.csv
07_reform_audit.xlsx

The script is resumable. LLM responses are cached by prompt hash.

Examples
--------
# Preprocess only (no API key required):
python egy_reform_llm_pipeline.py --zip EGY.zip --out EGY_reform_output --provider none

# OpenAI Responses API:
set OPENAI_API_KEY=YOUR_KEY
python egy_reform_llm_pipeline.py --zip EGY.zip --out EGY_reform_output --provider openai --model gpt-5.6-terra

# Local Ollama (optional):
python egy_reform_llm_pipeline.py --zip EGY.zip --out EGY_reform_output --provider ollama --model qwen3:14b

Notes
-----
- LLMs extract/classify evidence. They do NOT assign arbitrary 0-3 reform scores.
- Recommendations are retained for audit but excluded from the default reform panel.
- Repeated mentions of the same reform in later IMF reviews are treated as lifecycle
  observations, not blindly deduplicated away.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import zipfile
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import pandas as pd
import fitz  # PyMuPDF
from rapidfuzz import fuzz


# -----------------------------------------------------------------------------
# 1. Coding framework
# -----------------------------------------------------------------------------

CATEGORY_DEFINITIONS: Dict[str, Dict[str, Any]] = {
    "Public Financial Management": {
        "definition": (
            "Reforms to budget preparation and execution, expenditure controls, cash and treasury "
            "management, accounting, fiscal reporting, arrears management, internal/external audit, "
            "commitment controls, and fiscal transparency/accountability."
        ),
        "include": [
            "budget execution", "budget classification", "fiscal reporting", "treasury single account",
            "cash management", "commitment control", "arrears", "internal audit", "external audit",
            "fiscal transparency", "public accounts", "financial management information system",
            "medium-term budget framework", "public expenditure control"
        ],
        "exclude": [
            "general fiscal consolidation without an institutional reform",
            "monetary policy", "a simple change in tax rates unless it changes administration/governance"
        ],
        "keywords": [
            r"public financial management", r"\bPFM\b", r"budget execution", r"budget classification",
            r"fiscal report", r"treasury single account", r"cash management", r"commitment control",
            r"arrears", r"internal audit", r"external audit", r"fiscal transparency", r"public accounts",
            r"financial management information system", r"\bFMIS\b", r"medium[- ]term budget",
            r"expenditure control", r"budget transparency"
        ],
    },
    "Public Investment Management": {
        "definition": (
            "Reforms to the institutions governing public investment: project appraisal, selection, "
            "prioritization, budgeting, ceilings, monitoring, ex-post evaluation, capital project pipelines, "
            "and governance of PPP/public investment risks."
        ),
        "include": [
            "public investment ceiling", "project appraisal", "project selection", "project prioritization",
            "capital budget", "public investment monitoring", "project pipeline", "PPP appraisal",
            "investment project database", "ex-post project evaluation"
        ],
        "exclude": [
            "a generic statement that public investment rose/fell", "private investment reforms only"
        ],
        "keywords": [
            r"public investment", r"capital expenditure", r"capital spending", r"project appraisal",
            r"project selection", r"project priorit", r"investment ceiling", r"capital budget",
            r"investment project", r"project pipeline", r"public[- ]private partnership", r"\bPPP\b",
            r"project evaluation"
        ],
    },
    "Public Procurement": {
        "definition": (
            "Reforms to public purchasing rules and practice, including procurement laws, e-procurement, "
            "competitive tendering, bid/award disclosure, contract publication, procurement oversight, "
            "and value-for-money mechanisms."
        ),
        "include": [
            "procurement law", "e-procurement", "competitive tendering", "open bidding",
            "publication of procurement contracts", "publication of contract awards",
            "procurement transparency", "central purchasing"
        ],
        "exclude": [
            "private procurement", "a procurement-related expenditure number without institutional change"
        ],
        "keywords": [
            r"procurement", r"e[- ]procurement", r"tender", r"competitive bidding", r"open bidding",
            r"contract award", r"public contract", r"purchasing system"
        ],
    },
    "SOE Governance / State Footprint": {
        "definition": (
            "Reforms affecting state-owned enterprises and the state's commercial footprint: ownership policy, "
            "governance, disclosure, monitoring, divestment/privatization, tax or regulatory privileges, "
            "competitive neutrality, SOE procurement, and state asset management."
        ),
        "include": [
            "state ownership policy", "SOE governance", "SOE financial disclosure", "SOE monitoring",
            "divestment", "privatization", "competitive neutrality", "removal of SOE tax privileges",
            "SOE procurement disclosure", "state asset management"
        ],
        "exclude": [
            "a passing reference to an SOE with no reform action", "private firms only"
        ],
        "keywords": [
            r"state[- ]owned", r"\bSOE\b", r"public enterprise", r"state ownership",
            r"divest", r"privati[sz]", r"competitive neutrality", r"state footprint",
            r"government[- ]owned", r"state asset"
        ],
    },
    "Fiscal Decentralization": {
        "definition": (
            "Reforms that change fiscal authority or accountability across levels of government, including "
            "expenditure assignments, own-source revenues/local taxation, intergovernmental transfers, "
            "equalization/grants, subnational borrowing/debt rules, local PFM, or transfer of funded functions "
            "to governorates/municipalities/local administrations."
        ),
        "include": [
            "devolution of expenditure responsibilities", "local tax or fee authority", "own-source revenue",
            "intergovernmental transfer formula", "equalization grant", "subnational borrowing rules",
            "local government fiscal rules", "local PFM/accounting", "fiscally funded decentralization",
            "transfer of functions together with budget/resources"
        ],
        "exclude": [
            "administrative decentralization without a fiscal change",
            "mere mention of governorates/municipalities/local governments",
            "local implementation of a national program without a change in fiscal authority",
            "geographic statistics or local-government employment with no reform"
        ],
        "keywords": [
            r"fiscal decentral", r"decentralization", r"decentralisation", r"intergovernmental",
            r"subnational", r"local government", r"local administration", r"governorate",
            r"municipal", r"local revenue", r"own[- ]source revenue", r"local tax",
            r"transfer formula", r"equalization", r"equalisation", r"subnational debt",
            r"local borrowing", r"fiscal transfer"
        ],
    },
}

ACTION_SIGNAL_PATTERNS = [
    r"reform", r"implement", r"introduc", r"adopt", r"approv", r"enact", r"amend",
    r"submit", r"launch", r"publish", r"establish", r"create", r"roll[- ]out", r"digitali[sz]",
    r"structural benchmark", r"prior action", r"performance criterion", r"measure", r"commit",
    r"complete", r"met\b", r"not met", r"delay", r"postpon", r"missed", r"partial",
    r"revers", r"repeal", r"eliminat", r"remove", r"privati[sz]", r"divest"
]

ALLOWED_STATUSES = {
    "planned",
    "official_commitment",
    "legally_adopted",
    "partially_implemented",
    "implemented",
    "ongoing",
    "delayed",
    "not_met",
    "reversed",
    "unclear",
}

ALLOWED_RECORD_TYPES = {
    "government_action",
    "government_commitment",
    "imf_recommendation",
    "background_description",
    "monitoring_update",
    "unclear",
}

STATUS_RANK = {
    "planned": 1,
    "official_commitment": 2,
    "legally_adopted": 3,
    "ongoing": 4,
    "partially_implemented": 5,
    "implemented": 6,
    # delayed/not_met/reversed are outcomes, not monotonic stages; handled separately.
    "delayed": 0,
    "not_met": 0,
    "reversed": -1,
    "unclear": 0,
}


# -----------------------------------------------------------------------------
# 2. Utilities
# -----------------------------------------------------------------------------

def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8", errors="ignore")).hexdigest()


def clean_text(text: str) -> str:
    text = text.replace("\u00ad", "")
    text = re.sub(r"-\n(?=[a-z])", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def normalize_action(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\b(the|a|an|of|to|and|for|in|on|with|under|by|from)\b", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def safe_json_loads(text: str) -> Dict[str, Any]:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except Exception:
        # fall back to first full JSON object
        i = text.find("{")
        j = text.rfind("}")
        if i >= 0 and j > i:
            return json.loads(text[i : j + 1])
        raise


def write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    out = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def coerce_year(v: Any) -> Optional[int]:
    if v is None or v == "":
        return None
    m = re.search(r"\b(19|20)\d{2}\b", str(v))
    return int(m.group(0)) if m else None


def flatten_list(v: Any) -> str:
    if isinstance(v, list):
        return " | ".join(str(x) for x in v)
    return "" if v is None else str(v)


# -----------------------------------------------------------------------------
# 3. Archive and document inventory
# -----------------------------------------------------------------------------

def locate_catalogue(root: Path) -> Optional[Path]:
    matches = list(root.rglob("catalogue.json"))
    return matches[0] if matches else None


def prepare_archive(zip_path: Path, work_dir: Path) -> Path:
    extract_dir = work_dir / "extracted"
    extract_dir.mkdir(parents=True, exist_ok=True)
    marker = extract_dir / ".unzipped_ok"
    if not marker.exists():
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_dir)
        marker.write_text(datetime.now().isoformat(), encoding="utf-8")
    return extract_dir


def build_inventory(extract_root: Path) -> pd.DataFrame:
    catalogue = locate_catalogue(extract_root)
    rows: List[Dict[str, Any]] = []
    if catalogue:
        data = json.loads(catalogue.read_text(encoding="utf-8"))
        for x in data:
            if not x.get("include", False) or x.get("status") != "downloaded":
                continue
            fname = x.get("file_name")
            matches = list(extract_root.rglob(fname)) if fname else []
            if not matches:
                continue
            p = matches[0]
            rows.append({
                "country": x.get("country", "Egypt"),
                "iso3": x.get("iso3c", "EGY"),
                "report_id": x.get("report_id", p.stem),
                "report_year": coerce_year(x.get("year")),
                "publication_date": x.get("pub_date"),
                "cr_number": x.get("cr_number"),
                "doc_type": x.get("doc_type"),
                "title": x.get("title"),
                "file_name": p.name,
                "pdf_path": str(p.resolve()),
                "n_pages_catalogue": x.get("n_pages"),
            })
    else:
        for p in sorted(extract_root.rglob("*.pdf")):
            y = coerce_year(p.name)
            rows.append({
                "country": "Egypt", "iso3": "EGY", "report_id": p.stem,
                "report_year": y, "publication_date": None, "cr_number": None,
                "doc_type": "unknown", "title": p.stem, "file_name": p.name,
                "pdf_path": str(p.resolve()), "n_pages_catalogue": None,
            })
    df = pd.DataFrame(rows).sort_values(["report_year", "file_name"]).reset_index(drop=True)
    return df


# -----------------------------------------------------------------------------
# 4. PDF parsing and candidate retrieval
# -----------------------------------------------------------------------------

def keyword_hits(text: str, patterns: Sequence[str]) -> int:
    return sum(len(re.findall(p, text, flags=re.I)) for p in patterns)


def parse_pdf_pages(pdf_path: Path) -> List[Dict[str, Any]]:
    doc = fitz.open(pdf_path)
    pages = []
    for i, page in enumerate(doc):
        text = clean_text(page.get_text("text"))
        pages.append({"page": i + 1, "text": text})
    return pages


def page_category_scores(text: str) -> Dict[str, int]:
    return {
        cat: keyword_hits(text, cfg["keywords"])
        for cat, cfg in CATEGORY_DEFINITIONS.items()
    }


def build_candidate_chunks(
    inventory: pd.DataFrame,
    out_dir: Path,
    neighbor_pages: int = 1,
    max_chars: int = 60000,
) -> List[Dict[str, Any]]:
    cache_dir = out_dir / "page_cache"
    cache_dir.mkdir(exist_ok=True)
    chunks: List[Dict[str, Any]] = []

    for _, meta in inventory.iterrows():
        report_id = meta["report_id"]
        cache_file = cache_dir / f"{report_id}.json"
        if cache_file.exists():
            pages = json.loads(cache_file.read_text(encoding="utf-8"))
        else:
            pages = parse_pdf_pages(Path(meta["pdf_path"]))
            cache_file.write_text(json.dumps(pages, ensure_ascii=False), encoding="utf-8")

        selected: Dict[int, Dict[str, int]] = {}
        for p in pages:
            scores = page_category_scores(p["text"])
            cats = {k: v for k, v in scores.items() if v > 0}
            if not cats:
                continue
            action_hits = keyword_hits(p["text"], ACTION_SIGNAL_PATTERNS)
            # Keep any category hit; action signals are recorded for ranking/audit.
            for q in range(max(1, p["page"] - neighbor_pages), min(len(pages), p["page"] + neighbor_pages) + 1):
                selected.setdefault(q, {})
                for c, n in cats.items():
                    selected[q][c] = selected[q].get(c, 0) + n
                selected[q]["__action_hits__"] = selected[q].get("__action_hits__", 0) + action_hits

        if not selected:
            continue

        # Pack selected pages into relatively large report-level chunks. Pages do not need
        # to be consecutive because explicit PAGE markers preserve provenance. This greatly
        # reduces API-call overhead while retaining +/- neighbor-page context around hits.
        page_nums = sorted(selected)
        by_num = {p["page"]: p["text"] for p in pages}
        sub_pages: List[int] = []
        sub_text = ""
        for pno in page_nums:
            block = f"\n\n===== PAGE {pno} =====\n{by_num.get(pno, '')}"
            if sub_pages and len(sub_text) + len(block) > max_chars:
                chunks.append(_make_chunk(meta, report_id, sub_pages, sub_text, selected))
                sub_pages, sub_text = [], ""
            sub_pages.append(pno)
            sub_text += block
        if sub_pages:
            chunks.append(_make_chunk(meta, report_id, sub_pages, sub_text, selected))

    # Stable chunk IDs and sort.
    for i, ch in enumerate(chunks, start=1):
        ch["chunk_id"] = f"{ch['report_id']}_c{i:04d}_{ch['page_start']}_{ch['page_end']}"
    return chunks


def _make_chunk(meta: pd.Series, report_id: str, pages: List[int], text: str, selected: Dict[int, Dict[str, int]]) -> Dict[str, Any]:
    cat_hits = {c: 0 for c in CATEGORY_DEFINITIONS}
    action_hits = 0
    for p in pages:
        for k, v in selected.get(p, {}).items():
            if k == "__action_hits__":
                action_hits += v
            elif k in cat_hits:
                cat_hits[k] += v
    return {
        "country": meta["country"],
        "iso3": meta["iso3"],
        "report_id": report_id,
        "report_year": int(meta["report_year"]) if pd.notna(meta["report_year"]) else None,
        "publication_date": meta["publication_date"],
        "cr_number": meta["cr_number"],
        "doc_type": meta["doc_type"],
        "title": meta["title"],
        "file_name": meta["file_name"],
        "page_start": min(pages),
        "page_end": max(pages),
        "pages": pages,
        "category_hits": cat_hits,
        "action_hits": action_hits,
        "text": text.strip(),
    }


# -----------------------------------------------------------------------------
# 5. LLM layer
# -----------------------------------------------------------------------------

def category_prompt() -> str:
    lines = []
    for i, (cat, cfg) in enumerate(CATEGORY_DEFINITIONS.items(), start=1):
        lines.append(f"{i}. {cat}\nDefinition: {cfg['definition']}\nInclude: {', '.join(cfg['include'])}\nExclude: {', '.join(cfg['exclude'])}")
    return "\n\n".join(lines)


SYSTEM_PROMPT = f"""You are coding fiscal and structural reform events from IMF country reports for an academic research database.

Your job is evidence extraction and classification, not policy evaluation. Never assign subjective quality scores.

REFORM CATEGORIES
{category_prompt()}

CRITICAL RULES
1. Distinguish IMF/staff recommendations from actual government reforms. A recommendation alone is NOT an implemented reform.
2. Distinguish plans/commitments, legal adoption, partial implementation, full implementation, delays/non-observance, and reversals.
3. Fiscal Decentralization requires a change in fiscal authority, resources, intergovernmental transfers, subnational borrowing/rules, or local PFM/accountability. A mere mention of governorates, municipalities, or local government is NOT enough.
4. If one reform genuinely spans categories, select one primary_category and list secondary_categories. Do not invent multiple separate reforms just to populate categories.
5. Use the policy/event year stated in the evidence, not automatically the report year. If the year is not stated or cannot be inferred with high confidence, set policy_year to null and explain in temporal_note.
6. Preserve repeated monitoring observations of an existing reform. They may show that the same benchmark moved from planned -> delayed -> implemented across reviews.
7. evidence_quote must be a short verbatim excerpt that directly supports the coded claim. Do not paraphrase inside evidence_quote.
8. Never infer implementation from aspirational language such as "should", "needs to", "plans to", or "will consider".
9. Return ONLY valid JSON. No markdown.

ALLOWED implementation_status values:
planned, official_commitment, legally_adopted, partially_implemented, implemented, ongoing, delayed, not_met, reversed, unclear

ALLOWED record_type values:
government_action, government_commitment, imf_recommendation, background_description, monitoring_update, unclear

DEFAULT PANEL ELIGIBILITY
eligible_for_reform_panel should be true for government_action, government_commitment, or monitoring_update that documents a real government reform/benchmark/action. It should be false for IMF recommendations or background descriptions.
"""


def build_user_prompt(chunk: Dict[str, Any]) -> str:
    schema = {
        "events": [
            {
                "reform_name": "short stable label",
                "canonical_action": "normalized description of the reform instrument/action",
                "primary_category": "one of the five categories",
                "secondary_categories": ["zero or more other categories"],
                "record_type": "allowed value",
                "actor": "government / IMF_staff / parliament / central_bank / SOE / subnational_government / other / unclear",
                "implementation_status": "allowed value",
                "eligible_for_reform_panel": True,
                "policy_year": 2024,
                "event_date": "YYYY-MM-DD or YYYY-MM or YYYY or null",
                "date_precision": "day / month / year / report_year_proxy / unknown",
                "temporal_note": "why the year/date is assigned",
                "benchmark_type": "structural_benchmark / prior_action / performance_criterion / reform_measure / none / unclear",
                "benchmark_status": "met / not_met / partially_met / modified / reset / converted / pending / not_applicable / unclear",
                "target_date": "date text or null",
                "completion_date": "date text or null",
                "institution_responsible": "text or null",
                "evidence_quote": "short direct quote",
                "evidence_page": 12,
                "confidence": 0.95,
                "ambiguity_note": "text or null"
            }
        ]
    }
    return f"""Extract all relevant reform events or reform-monitoring observations from the text below.

DOCUMENT METADATA
Country: {chunk['country']}
Report year: {chunk['report_year']}
Publication date: {chunk['publication_date']}
Document type: {chunk['doc_type']}
Title: {chunk['title']}
IMF report number: {chunk['cr_number']}
Pages in this chunk: {chunk['page_start']}-{chunk['page_end']}

Return a JSON object with exactly this top-level structure:
{json.dumps(schema, ensure_ascii=False, indent=2)}

Additional instructions:
- Extract zero events if the pages only contain recommendations, statistics, or descriptive references with no actual reform/commitment/monitoring content. You may still include an IMF recommendation as record_type=imf_recommendation for audit if it is clearly tied to one of the five categories; eligible_for_reform_panel must then be false.
- Do not create duplicates from repeated wording on the same pages.
- For evidence_page, use the page number shown in the ===== PAGE N ===== markers.
- confidence must be between 0 and 1.

TEXT
{chunk['text']}
"""


class LLMClient:
    def __init__(self, provider: str, model: str, cache_dir: Path, temperature: float = 0.0, sleep_s: float = 0.3):
        self.provider = provider
        self.model = model
        self.cache_dir = cache_dir
        self.temperature = temperature
        self.sleep_s = sleep_s
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def extract(self, chunk: Dict[str, Any], retries: int = 3) -> Dict[str, Any]:
        user_prompt = build_user_prompt(chunk)
        prompt_material = SYSTEM_PROMPT + "\n\n" + user_prompt + "\nMODEL=" + self.model + "\nPROVIDER=" + self.provider
        key = sha256_text(prompt_material)
        cache_file = self.cache_dir / f"{key}.json"
        if cache_file.exists():
            return json.loads(cache_file.read_text(encoding="utf-8"))

        last_err = None
        for attempt in range(1, retries + 1):
            try:
                if self.provider == "openai":
                    out = self._call_openai(SYSTEM_PROMPT, user_prompt)
                elif self.provider == "ollama":
                    out = self._call_ollama(SYSTEM_PROMPT, user_prompt)
                else:
                    raise ValueError(f"Unsupported LLM provider: {self.provider}")
                data = safe_json_loads(out)
                if not isinstance(data, dict) or "events" not in data or not isinstance(data["events"], list):
                    raise ValueError("LLM output must be an object containing an events list")
                cache_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                time.sleep(self.sleep_s)
                return data
            except Exception as e:
                last_err = e
                if attempt < retries:
                    time.sleep(1.5 * attempt)
        raise RuntimeError(f"LLM extraction failed after {retries} attempts: {last_err}")

    def _call_openai(self, system: str, user: str) -> str:
        try:
            from openai import OpenAI
        except Exception as e:
            raise RuntimeError("Install the current OpenAI Python SDK: pip install -U openai") from e
        if not os.getenv("OPENAI_API_KEY"):
            raise RuntimeError("OPENAI_API_KEY is not set")
        client = OpenAI()
        # Uses the Responses API. We request plain text containing strict JSON and validate it locally.
        response = client.responses.create(
            model=self.model,
            input=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return response.output_text

    def _call_ollama(self, system: str, user: str) -> str:
        import requests
        url = os.getenv("OLLAMA_URL", "http://localhost:11434/api/chat")
        payload = {
            "model": self.model,
            "stream": False,
            "format": "json",
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "options": {"temperature": self.temperature},
        }
        r = requests.post(url, json=payload, timeout=600)
        r.raise_for_status()
        obj = r.json()
        return obj["message"]["content"]


# -----------------------------------------------------------------------------
# 6. Validation, lifecycle linking, panel construction
# -----------------------------------------------------------------------------

def validate_event(e: Dict[str, Any], chunk: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(e)
    out["primary_category"] = str(out.get("primary_category", "")).strip()
    if out["primary_category"] not in CATEGORY_DEFINITIONS:
        out["primary_category"] = ""
        out["eligible_for_reform_panel"] = False
        out["ambiguity_note"] = (str(out.get("ambiguity_note") or "") + " | Invalid primary category").strip(" |")

    status = str(out.get("implementation_status", "unclear")).strip()
    out["implementation_status"] = status if status in ALLOWED_STATUSES else "unclear"

    rtype = str(out.get("record_type", "unclear")).strip()
    out["record_type"] = rtype if rtype in ALLOWED_RECORD_TYPES else "unclear"

    # Recommendation/background never enters the panel even if the model mistakenly says true.
    if out["record_type"] in {"imf_recommendation", "background_description"}:
        out["eligible_for_reform_panel"] = False
    else:
        out["eligible_for_reform_panel"] = bool(out.get("eligible_for_reform_panel", False))

    out["policy_year"] = coerce_year(out.get("policy_year"))
    out["evidence_page"] = int(out["evidence_page"]) if str(out.get("evidence_page", "")).isdigit() else None
    if out["evidence_page"] and out["evidence_page"] not in set(chunk.get("pages", [])):
        out["ambiguity_note"] = (str(out.get("ambiguity_note") or "") + " | Evidence page not included in source chunk").strip(" |")

    try:
        c = float(out.get("confidence", 0))
    except Exception:
        c = 0.0
    out["confidence"] = min(1.0, max(0.0, c))

    # Attach source metadata.
    for k in [
        "country", "iso3", "report_id", "report_year", "publication_date", "cr_number",
        "doc_type", "title", "file_name", "chunk_id", "page_start", "page_end"
    ]:
        out[k] = chunk.get(k)

    out["secondary_categories"] = out.get("secondary_categories") or []
    if not isinstance(out["secondary_categories"], list):
        out["secondary_categories"] = [str(out["secondary_categories"])]
    out["secondary_categories"] = [x for x in out["secondary_categories"] if x in CATEGORY_DEFINITIONS and x != out["primary_category"]]

    out["canonical_action_norm"] = normalize_action(str(out.get("canonical_action") or out.get("reform_name") or ""))
    raw_key = "|".join([
        str(out.get("iso3")), str(out.get("primary_category")), out["canonical_action_norm"],
        str(out.get("policy_year")), str(out.get("evidence_page")), str(out.get("report_id"))
    ])
    out["observation_id"] = sha256_text(raw_key)[:16]
    return out


def conservative_link_lifecycles(df: pd.DataFrame, similarity_threshold: int = 91) -> pd.DataFrame:
    """Link repeated mentions conservatively within category using canonical_action similarity.

    This creates a lifecycle_id but never deletes observations. Human validation can override links.
    """
    if df.empty:
        return df
    df = df.copy().reset_index(drop=True)
    lifecycle_ids: List[str] = [""] * len(df)
    representatives: List[Tuple[int, str, str, Optional[int]]] = []  # index, category, norm_action, policy_year

    for i, row in df.iterrows():
        cat = row.get("primary_category", "")
        act = row.get("canonical_action_norm", "")
        yr = row.get("policy_year")
        best = None
        best_score = -1
        for rep_i, rep_cat, rep_act, rep_yr in representatives:
            if cat != rep_cat or not act or not rep_act:
                continue
            # Reform monitoring may occur across several years; limit to a 5-year window when years exist.
            if pd.notna(yr) and rep_yr is not None and abs(int(yr) - int(rep_yr)) > 5:
                continue
            score = fuzz.token_set_ratio(act, rep_act)
            if score > best_score:
                best_score, best = score, rep_i
        if best is not None and best_score >= similarity_threshold:
            lifecycle_ids[i] = lifecycle_ids[best]
        else:
            lid = sha256_text(f"{row.get('iso3')}|{cat}|{act}|{i}")[:14]
            lifecycle_ids[i] = lid
            representatives.append((i, cat, act, int(yr) if pd.notna(yr) else None))

    df["lifecycle_id"] = lifecycle_ids
    return df


def build_lifecycle_summary(obs: pd.DataFrame) -> pd.DataFrame:
    if obs.empty:
        return pd.DataFrame()
    eligible = obs[obs["eligible_for_reform_panel"] == True].copy()  # noqa: E712
    if eligible.empty:
        return pd.DataFrame()

    rows = []
    for lifecycle_id, g in eligible.groupby("lifecycle_id", dropna=False):
        g = g.sort_values(["publication_date", "report_year"], na_position="last")
        statuses = g["implementation_status"].dropna().astype(str).tolist()
        first = g.iloc[0]
        years = [int(y) for y in g["policy_year"].dropna().tolist()]
        rows.append({
            "lifecycle_id": lifecycle_id,
            "country": first.get("country"),
            "iso3": first.get("iso3"),
            "primary_category": first.get("primary_category"),
            "reform_name": first.get("reform_name"),
            "canonical_action": first.get("canonical_action"),
            "first_policy_year": min(years) if years else None,
            "last_policy_year": max(years) if years else None,
            "first_status": statuses[0] if statuses else None,
            "latest_status": statuses[-1] if statuses else None,
            "ever_implemented": int("implemented" in statuses),
            "ever_partial": int("partially_implemented" in statuses),
            "ever_delayed_or_not_met": int(any(st in {"delayed", "not_met"} for st in statuses)),
            "ever_reversed": int("reversed" in statuses),
            "n_observations": len(g),
            "source_reports": " | ".join(sorted(set(g["cr_number"].dropna().astype(str)))),
        })
    return pd.DataFrame(rows)

def build_country_year_panel(obs: pd.DataFrame) -> pd.DataFrame:
    if obs.empty:
        return pd.DataFrame()
    x = obs[(obs["eligible_for_reform_panel"] == True) & obs["policy_year"].notna()].copy()  # noqa: E712
    if x.empty:
        return pd.DataFrame()

    # Preserve all report-level observations in 04_reform_observations.csv, but for the
    # country-year panel use the latest observation for each reform lifecycle in each
    # policy year. This prevents repeated IMF reviews from mechanically inflating counts.
    x["publication_date_sort"] = pd.to_datetime(x["publication_date"], errors="coerce")
    x = x.sort_values(["lifecycle_id", "policy_year", "publication_date_sort", "report_year"], na_position="first")
    ly = x.groupby(["lifecycle_id", "policy_year"], as_index=False, dropna=False).tail(1).copy()

    ly["commitment_or_adoption"] = ly["implementation_status"].isin({
        "planned", "official_commitment", "legally_adopted", "ongoing"
    }).astype(int)
    ly["full_implementation"] = (ly["implementation_status"] == "implemented").astype(int)
    ly["partial_implementation"] = (ly["implementation_status"] == "partially_implemented").astype(int)
    ly["delay_or_not_met"] = ly["implementation_status"].isin({"delayed", "not_met"}).astype(int)
    ly["reversal"] = (ly["implementation_status"] == "reversed").astype(int)
    ly["not_fully_implemented"] = ly["implementation_status"].isin({
        "planned", "official_commitment", "legally_adopted", "ongoing",
        "partially_implemented", "delayed", "not_met"
    }).astype(int)

    # A stricter implementation-gap measure uses target dates/benchmark outcomes when
    # the document tells us that implementation was due. Missing target dates are not
    # silently treated as due.
    ly["target_year"] = ly["target_date"].map(coerce_year) if "target_date" in ly.columns else None
    due_by_date = ly["target_year"].notna() & (ly["target_year"] <= ly["policy_year"].astype(int))
    due_by_status = ly.get("benchmark_status", pd.Series(index=ly.index, dtype=object)).isin({"not_met", "partially_met"})
    ly["due_reform_flag"] = (due_by_date | due_by_status).astype(int)
    ly["implementation_gap_due"] = (
        (ly["due_reform_flag"] == 1) & (ly["implementation_status"] != "implemented")
    ).astype(int)

    long = (
        ly.groupby(["country", "iso3", "policy_year", "primary_category"], as_index=False)
          .agg(
            unique_reforms=("lifecycle_id", "nunique"),
            commitment_or_adoption_count=("commitment_or_adoption", "sum"),
            implementation_count=("full_implementation", "sum"),
            partial_implementation_count=("partial_implementation", "sum"),
            delay_or_not_met_count=("delay_or_not_met", "sum"),
            reversal_count=("reversal", "sum"),
            not_fully_implemented_count=("not_fully_implemented", "sum"),
            due_reform_count=("due_reform_flag", "sum"),
            implementation_gap_due_count=("implementation_gap_due", "sum"),
          )
    )
    long["implementation_any"] = (long["implementation_count"] > 0).astype(int)
    long["reform_activity_any"] = (long["unique_reforms"] > 0).astype(int)
    long["implementation_share"] = long["implementation_count"] / long["unique_reforms"].replace(0, pd.NA)
    long["due_implementation_gap_share"] = (
        long["implementation_gap_due_count"] / long["due_reform_count"].replace(0, pd.NA)
    )

    metrics = [
        "unique_reforms", "commitment_or_adoption_count", "implementation_count",
        "partial_implementation_count", "delay_or_not_met_count", "reversal_count",
        "not_fully_implemented_count", "due_reform_count", "implementation_gap_due_count",
        "implementation_any", "reform_activity_any", "implementation_share",
        "due_implementation_gap_share"
    ]
    wide_parts = []
    for metric in metrics:
        p = long.pivot_table(
            index=["country", "iso3", "policy_year"], columns="primary_category",
            values=metric, fill_value=0, aggfunc="sum"
        )
        p.columns = [f"{slug(c)}__{metric}" for c in p.columns]
        wide_parts.append(p)
    wide = pd.concat(wide_parts, axis=1).reset_index()

    overall = (
        ly.groupby(["country", "iso3", "policy_year"], as_index=False)
          .agg(
            total_unique_reforms=("lifecycle_id", "nunique"),
            total_implemented=("full_implementation", "sum"),
            total_partial=("partial_implementation", "sum"),
            total_delayed_or_not_met=("delay_or_not_met", "sum"),
            total_due_reforms=("due_reform_flag", "sum"),
            total_due_implementation_gap=("implementation_gap_due", "sum"),
          )
    )
    wide = wide.merge(overall, on=["country", "iso3", "policy_year"], how="left")
    return wide.sort_values(["iso3", "policy_year"]).reset_index(drop=True)

def slug(s: str) -> str:
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s


def make_audit_workbook(
    path: Path,
    inventory: pd.DataFrame,
    chunks: List[Dict[str, Any]],
    obs: pd.DataFrame,
    lifecycle: pd.DataFrame,
    panel: pd.DataFrame,
) -> None:
    chunk_df = pd.DataFrame([{k: v for k, v in c.items() if k != "text"} for c in chunks])
    if "category_hits" in chunk_df.columns:
        chunk_df["category_hits"] = chunk_df["category_hits"].map(json.dumps)

    rules = []
    for cat, cfg in CATEGORY_DEFINITIONS.items():
        rules.append({
            "category": cat,
            "definition": cfg["definition"],
            "include": " | ".join(cfg["include"]),
            "exclude": " | ".join(cfg["exclude"]),
        })
    rules_df = pd.DataFrame(rules)

    coding = pd.DataFrame([
        {"rule": "Recommendations", "treatment": "Retain for audit; exclude from reform panel."},
        {"rule": "LLM scores", "treatment": "No subjective 0-3 score. Construct mechanical indicators from statuses."},
        {"rule": "Policy year", "treatment": "Use event/policy year in evidence; do not default silently to report year."},
        {"rule": "Repeated reviews", "treatment": "Keep as lifecycle observations; later reviews may update status."},
        {"rule": "Fiscal decentralization", "treatment": "Require a fiscal authority/resource/transfer/borrowing/local-PFM change; geographic/local-government mentions alone are excluded."},
        {"rule": "Overlap", "treatment": "One primary category plus optional secondary categories; overall reform counts use unique lifecycle IDs."},
    ])

    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        inventory.to_excel(xw, sheet_name="Documents", index=False)
        chunk_df.to_excel(xw, sheet_name="CandidateChunks", index=False)
        obs2 = obs.copy()
        for c in obs2.columns:
            if obs2[c].map(lambda z: isinstance(z, list)).any() if len(obs2) else False:
                obs2[c] = obs2[c].map(flatten_list)
        obs2.to_excel(xw, sheet_name="ReformObservations", index=False)
        lifecycle.to_excel(xw, sheet_name="ReformLifecycle", index=False)
        panel.to_excel(xw, sheet_name="CountryYearPanel", index=False)
        rules_df.to_excel(xw, sheet_name="CategoryDefinitions", index=False)
        coding.to_excel(xw, sheet_name="CodingRules", index=False)

        # Light formatting for readability.
        from openpyxl.styles import Font, PatternFill, Alignment
        for ws in xw.book.worksheets:
            ws.freeze_panes = "A2"
            ws.auto_filter.ref = ws.dimensions
            for cell in ws[1]:
                cell.font = Font(bold=True)
                cell.fill = PatternFill("solid", fgColor="D9EAF7")
                cell.alignment = Alignment(wrap_text=True, vertical="top")
            for col in ws.columns:
                letter = col[0].column_letter
                max_len = min(max((len(str(c.value)) if c.value is not None else 0) for c in col[:200]) + 2, 45)
                ws.column_dimensions[letter].width = max(10, max_len)


# -----------------------------------------------------------------------------
# 7. Main
# -----------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description="Extract IMF fiscal/structural reform events from EGY.zip")
    ap.add_argument("--zip", dest="zip_path", required=True, help="Path to EGY.zip")
    ap.add_argument("--out", dest="out_dir", default="EGY_reform_output", help="Output directory")
    ap.add_argument("--provider", choices=["none", "openai", "ollama"], default="none")
    ap.add_argument("--model", default="gpt-5.6-terra")
    ap.add_argument("--neighbor-pages", type=int, default=1)
    ap.add_argument("--max-chars", type=int, default=60000)
    ap.add_argument("--max-chunks", type=int, default=0, help="0 = all chunks; useful for testing")
    ap.add_argument("--min-confidence", type=float, default=0.0, help="Flag/retain all; value only used in audit field")
    args = ap.parse_args()

    zip_path = Path(args.zip_path).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    if not zip_path.exists():
        raise FileNotFoundError(zip_path)

    extract_root = prepare_archive(zip_path, out_dir)
    inventory = build_inventory(extract_root)
    if inventory.empty:
        raise RuntimeError("No included/downloaded PDFs found")
    inventory.to_csv(out_dir / "01_document_inventory.csv", index=False, encoding="utf-8-sig")
    print(f"[1/6] Documents: {len(inventory)}")

    chunks = build_candidate_chunks(inventory, out_dir, neighbor_pages=args.neighbor_pages, max_chars=args.max_chars)
    write_jsonl(out_dir / "02_candidate_chunks.jsonl", chunks)
    print(f"[2/6] Candidate chunks: {len(chunks)}")

    if args.provider == "none":
        print("[STOP] provider=none: preprocessing completed; no LLM calls were made.")
        print(f"Output: {out_dir}")
        return

    llm = LLMClient(args.provider, args.model, out_dir / "llm_cache")
    selected_chunks = chunks[: args.max_chunks] if args.max_chunks > 0 else chunks
    raw_records: List[Dict[str, Any]] = []
    observations: List[Dict[str, Any]] = []

    for i, chunk in enumerate(selected_chunks, start=1):
        print(f"[3/6] LLM {i}/{len(selected_chunks)}: {chunk['chunk_id']} pages {chunk['page_start']}-{chunk['page_end']}")
        data = llm.extract(chunk)
        raw_records.append({"chunk_id": chunk["chunk_id"], "response": data})
        for event in data.get("events", []):
            try:
                observations.append(validate_event(event, chunk))
            except Exception as e:
                observations.append({
                    "chunk_id": chunk["chunk_id"], "report_id": chunk["report_id"],
                    "validation_error": str(e), "raw_event": json.dumps(event, ensure_ascii=False),
                    "eligible_for_reform_panel": False,
                })

    write_jsonl(out_dir / "03_reform_mentions_raw.jsonl", raw_records)
    obs = pd.DataFrame(observations)
    if obs.empty:
        obs = pd.DataFrame(columns=["observation_id", "eligible_for_reform_panel"])
    else:
        obs = conservative_link_lifecycles(obs)
        obs["low_confidence_flag"] = (obs["confidence"] < max(args.min_confidence, 0.75)).astype(int)
    obs.to_csv(out_dir / "04_reform_observations.csv", index=False, encoding="utf-8-sig")
    print(f"[4/6] Reform observations: {len(obs)}")

    lifecycle = build_lifecycle_summary(obs)
    lifecycle.to_csv(out_dir / "05_reform_lifecycle.csv", index=False, encoding="utf-8-sig")
    print(f"[5/6] Reform lifecycles: {len(lifecycle)}")

    panel = build_country_year_panel(obs)
    panel.to_csv(out_dir / "06_country_year_category_panel.csv", index=False, encoding="utf-8-sig")
    make_audit_workbook(out_dir / "07_reform_audit.xlsx", inventory, chunks, obs, lifecycle, panel)
    print(f"[6/6] Country-year rows: {len(panel)}")
    print(f"DONE: {out_dir}")


if __name__ == "__main__":
    main()
