# Deep research follow-up — verification log (2026-04-12)

Purpose: record what was **verified in-session** vs **still unverified** before returning to implementation todos (promotion log, emergency WS, fee calibration, etc.).

Methods used: `WebFetch`, Python `urllib` (Crossref, HTTP HEAD), local PDF download + `pypdf` text extraction, `WebSearch`, repository read of [`scalp_config.py`](../backend/server/scalp_bot/scalp_config.py) / [`scalp_fee_assumptions.py`](../backend/server/scalp_bot/scalp_fee_assumptions.py).

---

## 1. Coinbase CDE economics (fees, funding, margin)

| Item | Status | Evidence |
|------|--------|------------|
| Help: derivatives exchange fees | **Unverified (automated)** | `WebFetch` → **403** on `help.coinbase.com/en/derivatives/products/exchange-fees` |
| CDP: derivatives docs welcome | **Unverified (automated)** | `WebFetch` → **403** on `docs.cdp.coinbase.com/derivatives/introduction/welcome` |
| Learn: funding rates article | **Unverified (automated)** | `WebFetch` → **403** on `coinbase.com/learn/perpetual-futures/understanding-funding-rates-in-perpetual-futures` |
| Repo defaults for `coinbase_perps` | **Verified (code)** | `scalp_config.py`: default maker **6.5** bps/leg, taker **7.0** bps/leg when not overridden by TOML |
| Fee snapshot / drift detection | **Verified (code)** | `scalp_fee_assumptions.py`: `fee_assumption_snapshot`, `reconcile_fee_assumptions_on_startup`, comparable keys include `fee_bps_*`, `order_type`, `revision` |

**Operator action (required for “fact” on live economics):** open the Help pages that match **your exact product path** (US perps vs international vs FCM) in a normal browser, copy fee tier table + funding cadence + contract spec into `config.toml` / internal notes. Automated agents cannot currently read those pages.

---

## 2. Funding rate arbitrage paper (ScienceDirect / Elsevier)

| Item | Status | Evidence |
|------|--------|------------|
| Bibliographic identity | **Verified** | Crossref `10.1016/j.bcra.2025.100354` → title *Exploring Risk and Return Profiles of Funding Rate Arbitrage on CEX and DEX* |
| Abstract via Crossref | **Unverified** | Crossref JSON returned **no `abstract` field** for this DOI |
| Full text / headline stats | **Unverified** | ScienceDirect `WebFetch`/DOI redirect **timed out** in prior pass; not downloaded here |
| Author GitHub | **Partially verified** | `github.com/SainyTK/funding-arb-analysis` **exists** (fetch returned repo shell; no README body in capture) |

**Next step if strategy expands:** download PDF from ScienceDirect (human browser) or use institutional access; extract sample period, gross vs net, venues, leverage assumptions.

---

## 3. FIA automated trading risk controls

| Item | Status | Evidence |
|------|--------|------------|
| Press release | **Verified** | [FIA article 2024-07-18](https://www.fia.org/fia/articles/fia-releases-best-practices-automated-trading-risk-controls-and-system-safeguards) — summarizes scope (pre-trade, VCMs, post-trade, testing); links PDF |
| PDF availability | **Verified** | HTTP HEAD → **200**, `Content-Type: application/pdf`, **374,868** bytes, `Last-Modified: Thu, 18 Jul 2024` |
| Internal structure (text) | **Verified** | Downloaded to local temp; `pypdf` extract — **Table of contents** includes §1 Pre-Trade Controls (max order size, intraday position, price tolerance, cancel-on-disconnect, **kill switches**), §2 Exchange VCMs, §3 Other tools, §4 Post-trade (incl. **drop copy reconciliation**), §5 Testing |
| Intro claims | **Verified (quoted from PDF)** | e.g. pre-trade controls should be primary vs inadvertent activity; practices apply to **AI** as well as legacy stacks |

**Recommendation for build todos:** cite §1.5 Kill Switches, §4.1 Drop Copy Reconciliation when implementing `scalp_emergency_stop` and `ops-runbook`.

---

## 4. Literature: Chi (JFM 2023) and Bui & Nguyen (arXiv 2026)

### Chi et al., DOI [10.1002/fut.22425](https://doi.org/10.1002/fut.22425)

| Item | Status | Evidence |
|------|--------|------------|
| Abstract | **Verified** | Crossref API: major cryptos **2017–2021**; **basis, momentum, basis–momentum** factors — significant excess returns; **basis strongest** signal; daily stronger than weekly; monthly nonsignificant |

### Bui & Nguyen, [arXiv:2602.11708](https://arxiv.org/abs/2602.11708) (HTML v1)

| Item | Status | Evidence |
|------|--------|------------|
| Claims in abstract | **Verified** | AdaptiveTrend, **6h** bars, **150+** pairs, **2022–2024** window, reported **Sharpe 2.41**, **MDD −12.7%**, **Calmar 3.18** |
| Regime table (paper) | **Verified (author-reported)** | e.g. Bull / Sideways / Bear annualized return **68.3% / 18.7% / −4.2%**; Sharpe **3.42 / 1.87 / −0.31** |
| Ablation (paper) | **Verified (author-reported)** | Full model Sharpe **2.41** vs w/o trailing stop **1.68**; w/o monthly opt. **1.34** |

**Caveat:** All Bui & Nguyen numbers are **their backtest**, not guarantees for Fabcrowd Arceus (different universe, horizon, venue).

---

## 5. Vendor / low-priority fact-checks

No additional fetch this session. Prior thread status unchanged: QuantVPS cross-chain volume aligned with [arXiv:2501.17335](https://arxiv.org/abs/2501.17335); other blog lines remain **unverified**.

---

## Summary: return to build work

| Research todo ID | After this pass |
|------------------|-----------------|
| `research-coinbase-cde-economics` | **Open** until operator browser verification |
| `research-funding-arb-paper` | **Open** until PDF read (only if funding book) |
| `research-fia-sec-risk-controls` | **PDF verified** — ready to mine for `ops-runbook` / emergency stop rationale |
| `research-lit-chi-bui-crypto-futures` | **Abstracts + Bui HTML verified** — sufficient for literature context |
| `research-vendor-claims-low` | **Unchanged** — safe to cancel |

**Suggested build order:** (1) `wfo-fee-calibration` after manual Coinbase fee confirmation, (2) `emergency-ws` + `ops-runbook` using FIA PDF, (3) `promotion-jsonl` / `wfo-gates`, (4) `daily-loss-standby`, (5) `frontend-emergency`.

---

## Local artifacts

- Temporary download `data/_tmp_fia.pdf` used for extraction — **deleted** after this note to avoid bloating the repo.
