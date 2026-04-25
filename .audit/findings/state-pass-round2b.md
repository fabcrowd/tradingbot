# Nemesis Phase 4 — Round 2 feedback (Feynman → State)

## Ordering: reconcile task after mode switch

**Feynman concern:** Open legs cleared locally before exchange-backed rows re-imported.

**State trace:** `adopt_intx_position_from_exchange` with `existing is None` after full clear adds position + reserve — **invariant restored** if INTX API returns rows.

**Residual risk:** Reconcile fails (network) → empty positions + zero reserve while exchange has size → **next** reconcile or manual intervention. **LOW** (operational), not same class as NM-001 reserve leak.

## Convergence

Aligned with R2-S3 in pass-round2; no new finding.
