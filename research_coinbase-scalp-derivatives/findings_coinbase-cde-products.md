# Coinbase Derivatives Exchange (CDE) — product types, naming, nano contracts, clearing

**Summary:** On the U.S. **Coinbase Derivatives** path, “**US Perpetual-Style Futures**” are **CFTC-regulated listed futures** with a **five-year final expiration**, not unexpiring offshore perpetuals; they use an **hourly funding rate** and **USD (cash) settlement** so price tracks spot similarly to a perpetual. **Access and clearing** run through **Nodal Clear** as central counterparty and **Futures Commission Merchants (FCMs)** as intermediaries; **nano** contracts (e.g. **BIP** for nano Bitcoin perp-style) publish **contract size and listing/expiry rules** in exchange product PDFs linked from Coinbase Help.

---

## Venue and regulation (CDE)

- **Coinbase Derivatives, LLC** is described in exchange product materials as **registered with the U.S. CFTC as a designated contract market (DCM)** (i.e. the exchange lists and trades standardized futures under CFTC oversight).  
  Source: *nano Bitcoin Perp Style Spec* PDF (Coinbase Derivatives / Contentful-hosted specification).

---

## “Perpetual-style” vs traditional dated futures (U.S. Help Center)

- **US Perpetual-Style Futures** on Coinbase Derivatives are described as **five-year long-dated futures** that incorporate **hourly-calculated funding** so futures prices stay aligned with **spot**.  
- They are contrasted with **traditional monthly or quarterly futures** by emphasizing the **five-year expiration** plus the **hourly funding rate** (rather than only short-dated calendar expiries without that funding mechanic).  
  Source: [US Perpetual-Style Futures Overview](https://help.coinbase.com/derivatives/perpetual-style-futures/overview) (Coinbase Help; article text verified via page `__NEXT_DATA__` payload).

---

## Listing, expiration, and rolling (U.S. Help Center)

- Each such contract has a **five-year expiration**. Coinbase Help gives an example: in **July 2025**, only the **December 2030** contract is available for trading, and a **new five-year contract** is to be listed **one month ahead** of the December 2030 expiration.  
  Source: [US Perpetual-Style Futures Contract Specifications](https://help.coinbase.com/derivatives/perpetual-style-futures/contract-specifications) (Coinbase Help).

---

## Margin, funding settlement, clearing, FCMs (U.S. Help Center)

- **Initial margin** is posted at trade initiation; **variation margin** uses the **daily settlement price** and is settled **twice per day** (intraday and end-of-day) for mark-to-market.  
- **Funding** is calculated **hourly**; accrued funding is applied during **both** mid-day and end-of-day margin cycles, in parallel with variation margin, through the **clearinghouse**.  
- **Clearing:** all trades are cleared through **Nodal Clear**, which calculates and processes **variation margin** and **funding** payments. Participants must access the market through an **approved FCM**; positions are cleared **in the FCM’s name**.  
  Source: [US Perpetual-Style Futures Margin & Clearing](https://help.coinbase.com/derivatives/perpetual-style-futures/margin-and-clearing) (Coinbase Help).

---

## Nano contracts — example: nano Bitcoin “Perp Style” (official spec PDF)

The following are taken directly from the **nano Bitcoin Perp Style Spec** PDF (linked from Coinbase Help overview):

| Field | Published specification |
|--------|-------------------------|
| **Contract / product** | **nano Bitcoin Perp Style Futures** |
| **Product trading code** | **BIP** |
| **Contract size** | **1/100th of a Bitcoin** per contract |
| **Economic type** | **5-year cash-settled** futures contract that tracks spot closely using a **funding rate** and **clearing cash adjustment** |
| **Settlement** | **Financially settled in USD** |
| **Product type (label)** | **USD-settled index future** |
| **Listed expiry (initial series)** | Contracts initially expire on the **third Friday of December 2030**; **only one** contract is listed for any **five-year** period, with a rule that the **next eligible expiration month** is listed automatically effective the **first trade date of the expiration month** |
| **Position limits** | **6,500,000** contracts aggregate (per PDF) |
| **Trading hours (as stated)** | **Friday 6:00 PM – Friday 5:00 PM ET** with a **weekly one-hour break each Friday** |

Source: [nano Bitcoin Perp Style Spec (PDF)](https://assets.ctfassets.net/7ca8qfn907uv/36uUBfnoAFKfWNAgfZDepf/e3f68fe0f05bfc88594267b90f29f648/nano_Bitcoin_Perps_Spec.pdf) (linked from [US Perpetual-Style Futures Overview](https://help.coinbase.com/derivatives/perpetual-style-futures/overview)).

Coinbase Help’s overview also links parallel PDFs for **nano Ether**, **nano Solana**, and **nano XRP** perp-style specs (same Contentful `assets.ctfassets.net` host).

---

## Contract symbols and API strings (e.g. `BIP-20DEC30-CDE`)

- Official product materials above explicitly identify **BIP** as the **product trading code** for nano Bitcoin perp-style futures and describe **December 2030 / third Friday** listing rules.  
- **Full instrument strings** used by trading APIs (e.g. concatenations such as product code + expiry token + venue suffix like `-CDE`) must be taken from **your execution path’s product catalog** (FCM / Advanced Trade / REST `get_product` results). This memo does **not** parse the middle token (`20DEC30`) from primary PDF text; treat it as an **exchange-assigned instrument identifier**, not inferred here.

---

## Developer / protocol typing (FIX)

- Coinbase Developer Documentation for derivatives FIX lists **`SecurityType` = `FUT`** for **Future** (vs `OPT` for Option), i.e. protocol-level classification of these instruments as **futures**.  
  Source: [Derivatives FIX Code Sets](https://docs.cdp.coinbase.com/derivatives/fix/code-sets) (Coinbase Developer Documentation; HTML retrieved successfully).

---

## How this differs from “international” perpetual futures (orientation only)

Coinbase also documents **non-U.S. perpetual futures** (e.g. **International Exchange** perpetual product specification pages) as a **separate** retail/venue line from the **U.S. CDE + FCM + Nodal Clear** futures workflow summarized here. For scalp bot venue choice, treat **jurisdiction + clearing chain + instrument id** as the first gates before comparing funding or roll behavior.

---

## Sources

| URL | Description |
|-----|-------------|
| https://help.coinbase.com/derivatives/perpetual-style-futures/overview | Coinbase Help — defines U.S. perpetual-style futures vs traditional futures; links nano perp-style PDFs. |
| https://help.coinbase.com/derivatives/perpetual-style-futures/contract-specifications | Coinbase Help — five-year listing/expiration example and roll timing. |
| https://help.coinbase.com/derivatives/perpetual-style-futures/margin-and-clearing | Coinbase Help — initial/variation margin, twice-daily cycles, hourly funding via clearinghouse, **Nodal Clear**, **FCM** access. |
| https://assets.ctfassets.net/7ca8qfn907uv/36uUBfnoAFKfWNAgfZDepf/e3f68fe0f05bfc88594267b90f29f648/nano_Bitcoin_Perps_Spec.pdf | Official **nano Bitcoin Perp Style** contract specification (trading code **BIP**, size, settlement, hours, limits). |
| https://docs.cdp.coinbase.com/derivatives/fix/code-sets | Coinbase Developer Documentation — FIX enumerations including **`FUT` = Future**. |
| https://www.nodalclear.com/stonex-offers-clients-access-to-coinbase-derivatives-exchange-contracts-cleared-by-nodal-clear/ | Nodal Clear — third-party confirmation that **CDE contracts are cleared through Nodal Clear** (use alongside Coinbase Help for clearing). |
