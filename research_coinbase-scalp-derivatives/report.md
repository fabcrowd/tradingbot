# Coinbase derivatives for a scalp bot: futures vs perpetuals

## Summary

On Coinbase you are rarely choosing between two identical menus on one venue. **Jurisdiction and plumbing come first:** U.S. retail-style access to listed crypto contracts runs through **[Coinbase Derivatives Exchange (CDE)](https://www.cftc.gov/IndustryOversight/IndustryFilings/TradingOrganizations/43304)** (a CFTC DCM) and **[FCM intermediation](https://www.cftc.gov/check)** (for example Coinbase’s own FCM path described in public materials), while the **international perpetuals** stack is aimed at **non–U.S.** eligible users. What Coinbase brands as **[U.S. “Perpetual-Style” Futures](https://help.coinbase.com/derivatives/perpetual-style-futures/overview)** are **listed futures** with a **five-year final expiration**, **hourly funding**, and **[Nodal Clear](https://help.coinbase.com/derivatives/perpetual-style-futures/margin-and-clearing)** clearing—not unexpiring offshore perpetuals. For **minute-scale** strategies, **calendar roll** is usually a **second-order** issue if you stay on the **liquid front** and away from **[last-trading microstructure](https://www.cmegroup.com/education/courses/introduction-to-futures/understanding-futures-expiration-contract-roll)**; **funding** matters when your hold **crosses** the venue’s funding or margin batch windows, and **spread/depth** usually dominate. **Practical recommendation:** if your bot already targets **CDE instruments** (e.g. nano contracts such as **[BIP in the official spec PDF](https://assets.ctfassets.net/7ca8qfn907uv/36uUBfnoAFKfWNAgfZDepf/e3f68fe0f05bfc88594267b90f29f648/nano_Bitcoin_Perps_Spec.pdf)**), you are already on the right **U.S. “perp-like” futures** set for that stack; do **not** assume you can freely swap to **international classic perps** without **eligibility** and **API** changes. Treat “futures vs perps” as **contract spec + cadence + book quality**, then measure **effective cost** on your symbols.

## Findings

### What Coinbase actually lists (CDE / “perp-style”)

Coinbase Help describes **U.S. Perpetual-Style Futures** as **five-year listed futures** that use **[hourly funding](https://help.coinbase.com/derivatives/perpetual-style-futures/overview)** so price tracks spot similarly to a perpetual, contrasted with **shorter-dated monthly/quarterly** futures. **[Margin and clearing](https://help.coinbase.com/derivatives/perpetual-style-futures/margin-and-clearing)** text states **variation margin twice daily**, **hourly funding** processed through the clearinghouse, and **Nodal Clear** as CCP with **FCM** access. The **[nano Bitcoin Perp Style Spec (PDF)](https://assets.ctfassets.net/7ca8qfn907uv/36uUBfnoAFKfWNAgfZDepf/e3f68fe0f05bfc88594267b90f29f648/nano_Bitcoin_Perps_Spec.pdf)** gives concrete economics: product code **BIP**, **1/100 BTC** per contract, **USD cash** settlement, **five-year** structure with listing rules through **December 2030** in the initial series. Developer **[FIX code sets](https://docs.cdp.coinbase.com/derivatives/fix/code-sets)** classify these as **`SecurityType = FUT` (Future)** at the wire level. So for “**futures or perps?**” on the **U.S. path**, the honest answer is: **perp-like behavior, futures legal and protocol type**.

### Scalping lens: dated futures vs perpetual-style

**Roll:** Educational material on **[futures expiration and roll](https://www.cmegroup.com/education/courses/introduction-to-futures/understanding-futures-expiration-contract-roll)** emphasizes **volume migration** and **two-leg** roll execution—most relevant when you **hold through** expiry/roll or trade **illiquid** months. For **minute** holds on the dominant contract, roll is often **operationally ignorable** if you avoid **expiry-day** quirks. **Low confidence:** no Coinbase-specific empirical study was found tying minute-hold PnL to roll.

**Funding / basis:** **[Investopedia’s perpetual futures overview](https://www.investopedia.com/what-are-perpetual-futures-7494870)** explains funding as the mechanism tying perps to spot and notes **cadence varies by exchange** (often **8h** in retail examples). Coinbase’s **U.S.** perpetual-style path uses **hourly** funding per Help (see links above)—so **boundary crossing** in a scalp bot is **venue-specific**. Basis still moves intraday; **verify** mark/index rules on Coinbase’s pages (some URLs returned **403** to automated fetch in this research pass).

**Liquidity:** Generic commentary (e.g. **[BTSE’s exchange blog](https://www.btse.com/blog/perpetual-futures-vs-quarterly-contracts/)**) positions **perpetuals** as common for short-term trading; treat that as **orientation**, not evidence for your symbol. Prefer **live depth, volume, and OI** for the exact product you trade.

### Venue map: where “true” international perps live

The **[CFTC DCM filing](https://www.cftc.gov/IndustryOversight/IndustryFilings/TradingOrganizations/43304)** anchors **Coinbase Derivatives, LLC** as the U.S. exchange (FairX/LMX lineage). Public Coinbase messaging (blog URLs in findings memo; **403** to fetcher) describes **U.S. perpetual-style** and **international** stacks separately. **Operator implication:** **U.S. persons** should assume **international perpetuals** are **not** a drop-in substitute; **non–U.S. eligible** users may compare **international perpetuals** vs **whatever listed futures** exist in their region—again from **official eligibility + API** docs.

## Source evaluation

| Source | Type | Reliability | Notes |
|--------|------|-------------|-------|
| [Coinbase Help — perpetual-style overview](https://help.coinbase.com/derivatives/perpetual-style-futures/overview) | Primary (exchange) | High | Defines product; one subagent verified via page payload. |
| [Coinbase Help — margin & clearing](https://help.coinbase.com/derivatives/perpetual-style-futures/margin-and-clearing) | Primary | High | Funding + margin cycles + Nodal/FCM. |
| [nano Bitcoin Perp Style Spec PDF](https://assets.ctfassets.net/7ca8qfn907uv/36uUBfnoAFKfWNAgfZDepf/e3f68fe0f05bfc88594267b90f29f648/nano_Bitcoin_Perps_Spec.pdf) | Primary | High | Contract size, code BIP, settlement. |
| [CDP — derivatives FIX code sets](https://docs.cdp.coinbase.com/derivatives/fix/code-sets) | Primary | High | Protocol type `FUT`. |
| [CFTC — Coinbase Derivatives DCM](https://www.cftc.gov/IndustryOversight/IndustryFilings/TradingOrganizations/43304) | Primary (regulator) | High | Legal entity and status. |
| [CME — futures roll education](https://www.cmegroup.com/education/courses/introduction-to-futures/understanding-futures-expiration-contract-roll) | Primary (exchange education) | Medium–High | Not crypto-specific; lifecycle logic. |
| [Investopedia — perpetual futures](https://www.investopedia.com/what-are-perpetual-futures-7494870) | Secondary | Medium | Good vocabulary; not venue rules. |
| [BTSE blog — perp vs quarterly](https://www.btse.com/blog/perpetual-futures-vs-quarterly-contracts/) | Exchange marketing/edu | Medium | Useful framing; promotional. |
| [Nodal Clear press note](https://www.nodalclear.com/stonex-offers-clients-access-to-coinbase-derivatives-exchange-contracts-cleared-by-nodal-clear/) | Third-party | Medium | Confirms clearing relationship; use with Coinbase Help. |

## Gaps and limitations

- **Automated WebFetch** hit **HTTP 403** on several **coinbase.com / help / CDP** URLs in one subagent environment; operational details (full fee tables, every margin tier, session hours edge cases) should be **re-read in a browser** from the official pages listed in `findings_coinbase-venue-map.md`.
- No **independent study** was found that isolates **Coinbase CDE** minute-scalp edge for “perp-style” vs **short-dated quarterly** on the same underlying.
- **Legal eligibility** is individual; this report is **not** legal or investment advice.

## Next steps

1. **Confirm your person/entity path:** U.S. FCM/CDE vs international INTX—read Coinbase’s **agreements and eligibility** for your account.
2. **For the existing scalp repo (`venue = "coinbase_perps"` / CDE symbols):** treat instruments as **listed perpetual-style futures**; validate **funding and margin batch times** against Help and align bot **hold times** or **session filters** so you are not surprised by discrete cashflows.
3. **Pick the contract by the book, not the label:** compare **bid–ask, depth, fees, and contract multiplier** (e.g. **[BIP contract size](https://assets.ctfassets.net/7ca8qfn907uv/36uUBfnoAFKfWNAgfZDepf/e3f68fe0f05bfc88594267b90f29f648/nano_Bitcoin_Perps_Spec.pdf)** vs other CDE listings) on the **front** series you actually trade.
4. **Falsifier (tape):** If **round-turn spread + realized funding** (when holds cross intervals) consistently exceeds your **per-trade edge** from signals, the issue is less “futures vs perps” than **symbol or horizon**—re-run on **microstructure logs** before changing venue.
