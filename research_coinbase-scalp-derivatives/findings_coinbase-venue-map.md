# Coinbase derivatives venues — research memo (2025–2026)

## Summary

Coinbase routes crypto derivatives through **several legally distinct pipes**: (1) the **Coinbase International Exchange** stack for **non‑U.S.** customers (historically Bermuda‑licensed international derivatives, expanded over time toward a broad non‑U.S. perpetuals ecosystem), (2) the **Coinbase Derivatives Exchange (CDE)** — **Coinbase Derivatives, LLC**, a **U.S. CFTC‑registered Designated Contract Market (DCM)** formerly known as **FairX / LMX Labs** — which lists **exchange‑traded futures** (including very small contract formats and products described in public materials as “perp‑style”), and (3) **Coinbase Advanced** as the **primary retail/pro UI**, where **U.S.** users typically access CFTC‑regulated products through **Coinbase Financial Markets, Inc. (CFM)**, which is an **NFA/CFTC‑registered Futures Commission Merchant (FCM)** (intermediary), not the exchange itself. Public announcements in **2025** describe **U.S.‑eligible “perpetual” or perpetual‑like crypto derivatives** becoming available through the **U.S. regulated** path (distinct from the international perpetuals book), though exact contract economics should be read from Coinbase’s own disclosures. For a **U.S. scalp operator**, venue choice usually collapses to **CDE‑listed futures (and related formats) accessed via CFM/broker plumbing** versus **international perpetuals (generally not available to U.S. persons on the international exchange)**; **Advanced Trade** is mainly the **product surface + APIs** rather than a separate unregulated exchange.

**Automated `WebFetch` note:** `coinbase.com`, `help.coinbase.com`, and `docs.cdp.coinbase.com` returned **HTTP 403** to the research fetcher used here; those URLs are still listed as primary sources to open in a normal browser.

---

## 1) Coinbase International Exchange (INTX) — non‑U.S. perpetuals (offshore stack)

**What it is (plain English):** A **non‑United States** derivatives venue / liquidity stack Coinbase markets to **international** users and institutions, with onboarding and entity structures outside the U.S. (public reporting commonly references **Bermuda** and other non‑U.S. licensing paths).

**Who can access what (high level):**

- **U.S. persons / U.S. institutions:** Public Coinbase materials consistently describe **restrictions** on accessing the **international exchange** for **U.S.** customers (treat as **not available** unless you have a definitive, personal eligibility determination from Coinbase compliance).
- **Non‑U.S. retail / professional:** Public help/marketing pages describe **eligible non‑U.S. jurisdictions** accessing **perpetual futures** through **Coinbase Advanced**, while the **international exchange** also serves **institutional** workflows (direct/API style access depending on product and onboarding).

**Products:** The international line is commonly positioned around **perpetual futures** and related **non‑U.S.** derivatives (and has expanded in press coverage to additional thematic contracts for **non‑U.S.** audiences).

**Why it matters to “perps vs futures”:** This is the closest Coinbase comes to the **global offshore perpetual** experience, but **geofencing/eligibility** is the gating function — not fee tables.

---

## 2) Coinbase Derivatives Exchange (CDE) — U.S. DCM (FairX lineage)

**What it is (plain English):** The **actual U.S. futures exchange** legal entity: **Coinbase Derivatives, LLC**, registered with the **CFTC** as a **Designated Contract Market (DCM)**.

**Regulatory fact (fetched primary source):** The CFTC’s DCM filing page states the organization was historically **LMX Labs, LLC**, doing business as **FairX**, later **Coinbase Derivatives**, legally renamed **Coinbase Derivatives, LLC** (as of **12/21/23** in the CFTC remarks field), and notes clearing via **Nodal Clear** (a **DCO**).

**Who can access what:** A U.S. DCM is accessed by market participants through the **broker/FCM/clearing** chain. In practice, **retail** access is commonly **not** “retail logs into the exchange directly” but “retail trades the exchange’s contracts **through** registered intermediaries,” including **Coinbase’s own FCM** and third‑party FCMs/brokers listed in Coinbase’s public partner materials.

**Products:** **Exchange‑traded futures** on crypto underlyings (plus other asset classes in public marketing). Some contracts are publicly described with **“perp‑style”** branding; legally, they still live on a **CFTC‑regulated futures** exchange and should be understood as **listed futures with the exchange’s contract specifications**, not “offshore perpetuals.”

**Why it matters to this repo’s scalp bot:** Your codebase’s “CDE / `coinbase_perps` naming” operational path is aligned with **U.S. futures exchange + FCM/API execution**, not the Bermuda international perpetuals venue.

---

## 3) Coinbase Advanced Trade — UI + APIs; U.S. derivatives sit on top of CFM + listed markets

**What it is (plain English):** **Advanced** is Coinbase’s **pro trading** surface (web/app) and related **APIs** for spot and derivatives features, depending on region and entitlements.

**U.S. derivatives plumbing:** For **U.S.** customers, regulated derivatives access is typically intermediated by **Coinbase Financial Markets, Inc. (CFM)**, which is an **FCM** (customer funds/margin, onboarding, and exchange connectivity are framed under futures regulation). CFM is **not** the same legal entity as the **DCM** (CDE).

**International perpetuals plumbing (non‑U.S.):** Public materials describe **non‑U.S. eligible** users trading **perpetuals** on Advanced with infrastructure tied to the **international** derivatives stack.

**Why it matters:** If you are choosing **futures vs perps** *inside Coinbase*, the first fork is **jurisdiction**, then **contract type** (listed futures vs international perpetuals), then **integration surface** (CDE/FCM market data & orders vs international exchange APIs).

---

## 4) Perpetuals vs futures on Coinbase — 2025–2026 snapshot (conceptual)

| Topic | International (INTX path) | U.S. (CDE + CFM path) |
| --- | --- | --- |
| **Canonical “perp” experience** | Positioned as **perpetual futures** for eligible **non‑U.S.** users | **2025 public launch messaging** describes **U.S.** access to **regulated perpetual / perpetual‑style** crypto contracts via Coinbase’s **U.S.** regulated futures channel (verify details in Coinbase legal/disclosures) |
| **Canonical “futures” experience** | May exist depending on product mix, but marketing emphasis is often perpetuals | **Dated futures** and **micro/nano** formats listed on **CDE**; access via **FCM/broker** chain |
| **Exchange vs intermediary** | International exchange + international entities | **CDE = exchange (DCM)**; **CFM = intermediary (FCM)** |

**Operator takeaway:** “Perps vs futures” is not just a fee question — it is **eligibility + contract specification + clearing/margin regime + API venue**.

---

## 5) Sources (full URLs)

### Fetched successfully (used verbatim facts from these pages)

- CFTC — **Designated Contract Markets (DCM) filing** for **Coinbase Derivatives, LLC** (status, FairX/LMX history, legal rename, clearing remarks):  
  `https://www.cftc.gov/IndustryOversight/IndustryFilings/TradingOrganizations/43304`
- CFTC — **“Check registration”** consumer guidance (context for FCM/DCM roles and NFA BASIC):  
  `https://www.cftc.gov/check`

### Coinbase official (could not be fetched by automated `WebFetch` here — HTTP 403 — but are the primary operator references)

- Coinbase — **International Exchange** landing:  
  `https://www.coinbase.com/international-exchange`
- Coinbase — **Derivatives / CDE** hub:  
  `https://www.coinbase.com/derivatives`
- Coinbase — **FCM** page (U.S. intermediation context):  
  `https://www.coinbase.com/fcm`
- Coinbase — blog: **“Perpetual futures have arrived in the U.S.”** (U.S. perpetuals launch messaging):  
  `https://www.coinbase.com/blog/perpetual-futures-have-arrived-in-the-us`
- Coinbase — blog (related pre‑launch framing):  
  `https://www.coinbase.com/blog/coming-july-21-us-perpetual-style-futures`
- Coinbase Help — **International derivatives / Advanced** getting started:  
  `https://help.coinbase.com/coinbase/derivatives/intx-derivatives-get-started`
- Coinbase Help — **About Coinbase Derivatives** (general):  
  `https://help.coinbase.com/en/derivatives/general/about`
- Coinbase Learn — **Getting started with futures: Coinbase Derivatives**:  
  `https://www.coinbase.com/learn/futures/getting-started-with-futures-coinbase-derivatives`
- Coinbase Developer Platform docs — **Advanced Trade — futures** guide:  
  `https://docs.cdp.coinbase.com/coinbase-app/advanced-trade-apis/guides/futures`

### Third reference (tertiary — useful for corporate chronology, not a legal authority)

- Wikipedia — **Coinbase** (narrative timeline mentioning Bermuda international derivatives expansion):  
  `https://en.wikipedia.org/wiki/Coinbase`

---

## 6) Limitations

- This memo is **not legal advice** and not a substitute for Coinbase’s **agreements, disclosures, and geo‑eligibility tooling**.
- Several **official Coinbase** pages could not be machine‑fetched due to **403**, so operational details (exact contract specs, margin, leverage, hours, funding cadence) must be confirmed directly from Coinbase documents at the URLs above.
