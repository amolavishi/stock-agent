# Executive Decision

> ⚠️ 이 문서는 실제 데이터에 근거한 PAPER 리서치이며 실제 주문이나 투자 권유가 아닙니다.

- Decision: **WAIT**
- Confidence: **53/100**
- Time Horizon: `1-2M`
- Analysis Intensity: `MAXIMUM`
- Run Status: `SUCCESS`
- Run ID: `20260810_031126_INOD_5c7a21`

# User Request

> INOD 분석
최대

- Intent: `ANALYZE`
- Focus: `없음`

# Market Snapshot

- Ticker / Current: `INOD` / `$62.93`
- 1D / 5D / 20D: `+0.96%` / `-0.52%` / `-7.62%`
- Relative Volume / ATR: `0.01x` / `8.42%`
- Stage: `STAGE_1`
- Source / Observed At: `TOSS_OPEN_API` / `2026-08-10T12:01:02.000+09:00`
- Data Quality / Mock: `OK` / `false`

# Market Regime

`RISK_ON`

# Research Mode / Fresh Delta

- Mode: `FULL_RESEARCH`
- Prior Run: `NONE`
- New Evidence: `SEC_INOD_000110465926092133, SEC_INOD_000110465926092131, SEC_INOD_000110465926092021, SEC_INOD_000110465926092010, SEC_INOD_000110465926081295, SEC_INOD_000110465926075184, SEC_INOD_000110465926074783, SEC_INOD_000195004726006175`
- Market Changes: `{}`

# Research Thesis

## Bull Case

- Net cash cushion ($240.278M cash vs $877K debt, ~11% of the $2.16B market cap) remains a placeholder swing factor: provenance still flagged, unfunded by choice not availability, and cannot fund conviction until the Q2 balance sheet lands (SEC_INOD_000110465926092021)
- FY2026 revenue-growth guidance reaffirmed via the 6/17 press-release 8-K; treated as pre-Q2-print and weightless pending the content read of the two 8/6/2026 8-Ks, so it funds nothing today (SEC_INOD_000110465926075184)
- For existing holders, WAIT and HOLD prescribe the identical retain action; both sides sit inside the agreed 55-60 band and the point estimate is immaterial to action - but this is a provisional hold-pending-evidence stance, never a funded, risk-managed position (SEC_INOD_000110465926092133)

## Bear Case

- $300M ATM with Goldman Sachs et al. (~14% of market cap) signed 8/6/2026 same-day as the CEO transition; utilization unverified, and the persisted state model still carries atm_active=false and dilution_risk=0 - the single largest known risk factor is live, unresolved, and misreported to downstream consumers (SEC_INOD_000110465926092131)
- Q2 2026 financials are unfunded in both directions: the 10-Q was never parsed for financial statements and both 8/6/2026 8-Ks were read as metadata-only, so revenue growth, gross margin, and burn are 0/unknown with null runway - the Q2 earnings release (Item 2.02) may already be in evidence unread (SEC_INOD_000110465926092021, SEC_INOD_000110465926092133, SEC_INOD_000110465926092010)
- Confirmed downtrend: price -20.2% below MA50 with RS -9.3% vs QQQ and -10.2% vs IWM in a RISK_ON tape (SOXX -1.4%); the snapshot is a closed-market artifact (Sunday 23:01 ET, volume 10,349 vs 1,297,102 average) so the STAGE_1 label is unreliable and the tape cannot fund any entry or exit signal (SEC_INOD_000110465926092133)
- Documented June insider supply: 10b5-1 Form 4 sales at $103-$111 plus a 200,000-share Form 144 (~$22M) with concurrent CFO transition (Chauhan $460K base, Espineli to CAO); Form 144 execution status and the ~1.7M share-count discrepancy remain unverified, funding a weak-bearish tilt (SEC_INOD_000110465926074783, SEC_INOD_000195004726006175, SEC_INOD_000110465926075184)

## Scorecard

| Metric | Score |
|---|---:|
| Signal Strength | 25 |
| Catalyst Quality | 15 |
| Expectation Gap | 20 |
| Surge Elasticity | 20 |
| Entry Readiness | 5 |
| Capital Structure Risk | 80 |
| Strategy Fit | 40 |

# Critic Review

- Verdict: `CHALLENGE`
- Critic Decision: `WAIT`
- Evidence Conflicts: `market.stage=STAGE_1 (basing) vs the confirmed-downtrend reading (-20.2% vs MA50, RS -9.3% vs QQQ, -10.2% vs IWM) that the defender's own bear case #3 uses to fund the bear side of the same tape., company_state carries atm_active=false and dilution_risk=0 while SEC_INOD_000110465926092131 documents a definitive $300M equity distribution agreement (~14% of the $2.16B market cap)., company_state shares_outstanding=34.38M vs 32,655,358 on the 6/16 Form 144 (SEC_INOD_000195004726006175) - an unexplained ~1.7M increase feeding dilution math., The 8/6/2026 8-Ks appear in fresh_delta.new_evidence_ids yet are graded UNCLASSIFIED/'filing metadata only' - evidence marked as newly arrived while never actually read, indicating a processing degradation rather than a data-availability gap.`

# Debate Resolution

- Research-Critic debate resolved to WAIT on substance: both sides accept label-only convergence within the 55-60 band, the read-first framing (the two 8/6/2026 8-Ks are in the index unread, so reading in-hand evidence precedes any new fetch), and consensus_ready=false as a mechanical export constraint
- Critic's CHALLENGE on the hard gate accepted in full: the state-model rebuild (atm_active=true, dilution_risk populated, share count reconciled) and the pipeline hard-fail must be demonstrated as artifacts (persisted-state diff, test evidence), not asserted
- Critic's ledger-integrity challenge accepted: closures must exist in the ledger with CLOSED status, and the thin-tape point must be reconciled to ISSUE_9b5ee831e02e or a newly registered entry; the anticipatory 'Jack S. Singhal' naming is rejected as fact until the content read confirms it
- Point estimate held at 58 within the agreed 55-60 band; the 58-vs-60 gap is calibration-only and immaterial to the retain action, and no new information exists this round to re-derive the band
- Both agents confirm consensus_ready=false; WAIT is a provisional hold-pending-evidence stance and must not be exported as a funded, risk-managed position

- Status: `DEADLOCK`
- Rounds: `10/10`
- Stress Test: `NOT_RUN`
- Open / Critical Open Issues: `48` / `0`
- Deadlock Reason: `MAX_ROUNDS_REACHED_WITH_UNRESOLVED_DISAGREEMENT`

## Thesis Change Log

- {'round': 2, 'role': 'RESEARCH', 'from_decision': 'HOLD', 'to_decision': 'HOLD', 'from_confidence': 55, 'to_confidence': 52, 'reason': 'Evidence/logic update reported by agent'}
- {'round': 2, 'role': 'CRITIC', 'from_decision': 'WAIT', 'to_decision': 'WAIT', 'from_confidence': 20, 'to_confidence': 60, 'reason': 'Evidence/logic update reported by agent'}
- {'round': 3, 'role': 'RESEARCH', 'from_decision': 'HOLD', 'to_decision': 'HOLD', 'from_confidence': 52, 'to_confidence': 45, 'reason': 'Evidence/logic update reported by agent'}
- {'round': 3, 'role': 'CRITIC', 'from_decision': 'WAIT', 'to_decision': 'WAIT', 'from_confidence': 60, 'to_confidence': 65, 'reason': 'Evidence/logic update reported by agent'}
- {'round': 4, 'role': 'RESEARCH', 'from_decision': 'HOLD', 'to_decision': 'HOLD', 'from_confidence': 45, 'to_confidence': 40, 'reason': 'Evidence/logic update reported by agent'}
- {'round': 5, 'role': 'RESEARCH', 'from_decision': 'HOLD', 'to_decision': 'WAIT', 'from_confidence': 40, 'to_confidence': 50, 'reason': 'Evidence/logic update reported by agent'}
- {'round': 5, 'role': 'CRITIC', 'from_decision': 'WAIT', 'to_decision': 'WAIT', 'from_confidence': 65, 'to_confidence': 60, 'reason': 'Evidence/logic update reported by agent'}
- {'round': 6, 'role': 'RESEARCH', 'from_decision': 'WAIT', 'to_decision': 'WAIT', 'from_confidence': 50, 'to_confidence': 58, 'reason': 'Evidence/logic update reported by agent'}

## Minority Opinion

- Critic's residual challenge retained: the 'mechanically enforced' consensus_ready=false claim is itself unverified prose until the pipeline hard-fail is demonstrated as an artifact, so the export contract remains open even beyond the evidence gaps
- Critic's round-11 acceptance criterion retained: a round that restates the READ-first plan without processed evidence artifacts (parsed 10-Q financials, content-read 8-K text, ATM utilization disclosure, landed state diff) is itself a CHALLENGE trigger
- Constructive minority shared by both sides: the 8/6 8-Ks' presence in new_evidence_ids alongside 'filing metadata only' grading indicates a pipeline read/parse degradation rather than a data-availability gap, and fixing that systemic processing failure is the highest-leverage action

# Evidence Table

| Evidence ID | Document | Published | Grade | Summary | Source |
|---|---|---|---|---|---|
| `SEC_INOD_000110465926092133` | 8-K | 2026-08-06 | UNCLASSIFIED | SEC filing metadata only: 8-K filed on 2026-08-06 | [원문](https://www.sec.gov/Archives/edgar/data/903651/000110465926092133/tm2622364d1_8k.htm) |
| `SEC_INOD_000110465926092131` | 424B5 | 2026-08-06 | C | Up to $300,000,000 Common Stock We have entered into an equity distribution agreement, dated August 6, 2026 (the “Sales Agreement”), with Goldman Sachs & Co.
LLC (“Goldman Sachs”), Craig-Hallum Capital Group LLC, Wells Fargo Securities, LLC, Maxim Group LLC, and Wedbush Securities Inc.
In accordance with the terms of the Sales Agreement, under this prospectus supplement we may offer and sell shares of our common stock having an aggregate offering price of up to $300,000,000 from time to time through or to the Sales Agents, acting as our agents or principals.
The Sales Agents will act as our sales agents, using commercially reasonable efforts to sell on our behalf all of the shares of common stock requested to be sold by us, consistent with their normal trading and sales prices, on mutually agreed terms set forth in the Sales Agreement.
The Sales Agents will be entitled to compensation at a commission rate of up to 2.0% of the gross proceeds per share sold under the Sales Agreement.
Goldman Sachs & Co.
LLC ​ Craig-Hallum ​ ​ Wells Fargo Securities ​ ​ Maxim Group LLC ​ ​ Wedbush Securities ​ The date of this prospectus supplement is August 6, 2026 TABLE OF CONTENTS ​ ​ TABLE OF CONTENTS Prospectus Supplement ​ ​ ​ PAGE ​ ABOUT THIS PROSPECTUS SUPPLEMENT ​ ​ ​ ​ S-1 ​ ​ PROSPECTUS SUPPLEMENT SUMMARY ​ ​ ​ ​ S-2 ​ ​ RISK FACTORS ​ ​ ​ ​ S-5 ​ ​ CAUTIONARY NOTE REGARDING FORWARD-LOOKING STATEMENTS ​ ​ ​ ​ S-7 ​ ​ USE OF PROCEEDS ​ ​ ​ ​ S-9 ​ ​ PLAN OF DISTRIBUTION ​ ​ ​ ​ S-10 ​ | [원문](https://www.sec.gov/Archives/edgar/data/903651/000110465926092131/tm2622086-4_424b5.htm) |
| `SEC_INOD_000110465926092021` | 10-Q | 2026-08-06 | UNCLASSIFIED | 2025-06 modernizes the accounting for internal-use software costs by increasing the operability of the recognition guidance considering different methods of software development.
The Credit Agreement contains a financial covenant that requires the Borrowers, on a consolidated basis, to maintain a fixed charge coverage ratio of not less than 1.10 to 1.00.
We undertake no obligation to update or review any guidance or other forward-looking statements, whether as a result of new information, future developments or otherwise, except as may be required by the U.S. | [원문](https://www.sec.gov/Archives/edgar/data/903651/000110465926092021/inod-20260630x10q.htm) |
| `SEC_INOD_000110465926092010` | 8-K | 2026-08-06 | UNCLASSIFIED | SEC filing metadata only: 8-K filed on 2026-08-06 | [원문](https://www.sec.gov/Archives/edgar/data/903651/000110465926092010/tm2621499d1_8k.htm) |
| `SEC_INOD_000110465926081295` | 4 | 2026-07-07 | UNCLASSIFIED | Check this box to indicate that a transaction was made pursuant to a contract, instruction or written plan for the purchase or sale of equity securities of the issuer that is intended to satisfy the affirmative defense conditions of Rule 10b5-1(c). | [원문](https://www.sec.gov/Archives/edgar/data/903651/000110465926081295/xslF345X06/tm2619666-2_4seq1.xml) |
| `SEC_INOD_000110465926075184` | 8-K | 2026-06-17 | UNCLASSIFIED | (the “Company”) issued a press release reaffirming its full-year 2026 revenue growth guidance it provided on May 7, 2026, in its first quarter earnings release and conference call.
Chauhan will be entitled to receive: (i) severance equal to 100% of the sum of (A) his base salary in effect immediately prior to his termination and (B) the greater of his most recently declared bonus or the average of his three most recently declared bonuses, payable over 12 months; (ii) continued medical and dental benefits until the earlier of the end of the maximum applicable COBRA coverage period or for the 12 months following termination (or cash payments in lieu thereof following expiration of COBRA coverage); and (iii) continued life and long-term disability insurance for 12 months following the termination.
Chauhan will be entitled to receive a separation payment consisting of: (i) a lump-sum payment, payable within 30 days following his termination, equal to 200% of the sum of his base salary as in effect immediately prior to his termination and the greater of his most recently declared bonus or the average of his three most recently declared bonuses; (ii) continued medical and dental benefits for up to 24 months following termination (or, if shorter, through the end of the applicable COBRA coverage period, with cash payments in lieu of coverage thereafter); (iii) continued life and long-term disability insurance for 24 months following termination; and (iv) accelerated vesting of outsta | [원문](https://www.sec.gov/Archives/edgar/data/903651/000110465926075184/tm2617967d1_8k.htm) |
| `SEC_INOD_000110465926074783` | 4 | 2026-06-16 | UNCLASSIFIED | Check this box to indicate that a transaction was made pursuant to a contract, instruction or written plan for the purchase or sale of equity securities of the issuer that is intended to satisfy the affirmative defense conditions of Rule 10b5-1(c).
The sale of the shares reported in Column 4 was made as part of the reporting person's long-term financial planning, including for retirement and portfolio diversification purposes.
The price reported above reflects the weighted average sale price.
The price reported above reflects the weighted average sale price.
The price reported above reflects the weighted average sale price.
The price reported above reflects the weighted average sale price.
The price reported above reflects the weighted average sale price.
The price reported above reflects the weighted average sale price.
The price reported above reflects the weighted average sale price.
The price reported above reflects the weighted average sale price.
The price reported above reflects the weighted average sale price.
The price reported above reflects the weighted average sale price.
The price reported above reflects the weighted average sale price.
The price reported above reflects the weighted average sale price.
The price reported above reflects the weighted average sale price. | [원문](https://www.sec.gov/Archives/edgar/data/903651/000110465926074783/xslF345X06/tm2618125-1_4seq1.xml) |
| `SEC_INOD_000195004726006175` | 144 | 2026-06-16 | UNCLASSIFIED | Form 144 Filer Information UNITED STATES SECURITIES AND EXCHANGE COMMISSION Washington, D.C.
20549 Form 144 NOTICE OF PROPOSED SALE OF SECURITIES PURSUANT TO RULE 144 UNDER THE SECURITIES ACT OF 1933 FORM 144 144: Filer Information Filer CIK 0001120632 Filer CCC XXXXXXXX Is this a LIVE or TEST Filing?
In addition, information shall be given as to sales by all persons whose sales are required by paragraph (e) of Rule 144 to be aggregated with sales for the account of the person filing this notice.
Relationship to Issuer Officer 144: Securities Information Title of the Class of Securities To Be Sold Name and Address of the Broker Number of Shares or Other Units To Be Sold Aggregate Market Value Number of Shares or Other Units Outstanding Approximate Date of Sale Name the Securities Exchange Common Morgan Stanley Smith Barney LLC Executive Financial Services 1 New York Plaza 8th Floor New York NY 10004 200000 22041800.00 32655358 06/16/2026 NASDAQ Furnish the following information with respect to the acquisition of the securities to be sold and with respect to the payment of all or any part of the purchase price or other consideration therefor: 144: Securities To Be Sold Title of the Class Date you Acquired Nature of Acquisition Transaction Name of Person from Whom Acquired Is this a Gift?
Date Donor Acquired Amount of Securities Acquired Date of Payment Nature of Payment * Common 06/16/2026 Exercise of Options Under a Registered Plan Issuer 65941 06/16/2026 Cash Common 06/16/20 | [원문](https://www.sec.gov/Archives/edgar/data/903651/000195004726006175/xsl144X01/primary_doc.xml) |
| `SEC_INOD_000110465926057270` | 10-Q | 2026-05-07 | UNCLASSIFIED | 2025-06 modernizes the accounting for internal-use software costs by increasing the operability of the recognition guidance considering different methods of software development.
The Credit Agreement contains a financial covenant that requires the Borrowers, on a consolidated basis, to maintain a fixed charge coverage ratio of not less than 1.10 to 1.00.
We undertake no obligation to update or review any guidance or other forward-looking statements, whether as a result of new information, future developments or otherwise, except as may be required by the U.S. | [원문](https://www.sec.gov/Archives/edgar/data/903651/000110465926057270/inod-20260331x10q.htm) |
| `SEC_INOD_000110465925107873` | 10-Q | 2025-11-06 | UNCLASSIFIED | ASU 2025 - 06 modernizes the accounting for internal - use software costs by increasing the operability of the recognition guidance considering different methods of software development.
The Company will update its assessment as additional interpretive guidance or implementing regulations are issued.
The Credit Agreement contains a financial covenant that requires the Borrowers, on a consolidated basis, to maintain a fixed charge coverage ratio of not less than 1.10 to 1.00.
We undertake no obligation to update or review any guidance or other forward-looking statements, whether as a result of new information, future developments or otherwise, except as may be required by the U.S. | [원문](https://www.sec.gov/Archives/edgar/data/903651/000110465925107873/inod-20250930x10q.htm) |
| `SEC_INOD_000141057825001550` | 10-Q | 2025-07-31 | UNCLASSIFIED | INNODATA INC._June 30, 2025 0000903651 --12-31 2025 Q2 false P3Y P3Y http://fasb.org/us-gaap/2025#SecuredOvernightFinancingRateSofrMember 0000903651 us-gaap:CommonStockMember 2024-04-01 2024-06-30 0000903651 us-gaap:CommonStockMember 2025-04-01 2025-06-30 0000903651 us-gaap:CommonStockMember 2025-01-01 2025-03-31 0000903651 us-gaap:TreasuryStockCommonMember 2025-06-30 0000903651 us-gaap:RetainedEarningsMember 2025-06-30 0000903651 us-gaap:NoncontrollingInterestMember 2025-06-30 0000903651 us-gaap:AdditionalPaidInCapitalMember 2025-06-30 0000903651 us-gaap:TreasuryStockCommonMember 2025-03-31 0000903651 us-gaap:RetainedEarningsMember 2025-03-31 0000903651 us-gaap:NoncontrollingInterestMember 2025-03-31 0000903651 us-gaap:AdditionalPaidInCapitalMember 2025-03-31 0000903651 us-gaap:TreasuryStockCommonMember 2024-12-31 0000903651 us-gaap:RetainedEarningsMember 2024-12-31 0000903651 us-gaap:NoncontrollingInterestMember 2024-12-31 0000903651 us-gaap:AdditionalPaidInCapitalMember 2024-12-31 0000903651 us-gaap:TreasuryStockCommonMember 2024-06-30 0000903651 us-gaap:RetainedEarningsMember 2024-06-30 0000903651 us-gaap:NoncontrollingInterestMember 2024-06-30 0000903651 us-gaap:AdditionalPaidInCapitalMember 2024-06-30 0000903651 us-gaap:TreasuryStockCommonMember 2024-03-31 0000903651 us-gaap:RetainedEarningsMember 2024-03-31 0000903651 us-gaap:NoncontrollingInterestMember 2024-03-31 0000903651 us-gaap:AdditionalPaidInCapitalMember 2024-03-31 0000903651 us-gaap:TreasuryStockCommonMember  | [원문](https://www.sec.gov/Archives/edgar/data/903651/000141057825001550/inod-20250630x10q.htm) |
| `SEC_INOD_000141057825001113` | 10-Q | 2025-05-09 | UNCLASSIFIED | INNODATA INC._March 31, 2025 0000903651 --12-31 2025 Q1 false P3Y P3Y http://fasb.org/us-gaap/2024#SecuredOvernightFinancingRateSofrMember 0000903651 us-gaap:CommonStockMember 2025-01-01 2025-03-31 0000903651 us-gaap:TreasuryStockCommonMember 2025-03-31 0000903651 us-gaap:RetainedEarningsMember 2025-03-31 0000903651 us-gaap:NoncontrollingInterestMember 2025-03-31 0000903651 us-gaap:AdditionalPaidInCapitalMember 2025-03-31 0000903651 us-gaap:TreasuryStockCommonMember 2024-12-31 0000903651 us-gaap:RetainedEarningsMember 2024-12-31 0000903651 us-gaap:NoncontrollingInterestMember 2024-12-31 0000903651 us-gaap:AdditionalPaidInCapitalMember 2024-12-31 0000903651 us-gaap:TreasuryStockCommonMember 2024-03-31 0000903651 us-gaap:RetainedEarningsMember 2024-03-31 0000903651 us-gaap:NoncontrollingInterestMember 2024-03-31 0000903651 us-gaap:AdditionalPaidInCapitalMember 2024-03-31 0000903651 us-gaap:TreasuryStockCommonMember 2023-12-31 0000903651 us-gaap:RetainedEarningsMember 2023-12-31 0000903651 us-gaap:NoncontrollingInterestMember 2023-12-31 0000903651 us-gaap:AdditionalPaidInCapitalMember 2023-12-31 0000903651 us-gaap:AccumulatedTranslationAdjustmentMember 2025-03-31 0000903651 us-gaap:AccumulatedOtherComprehensiveIncomeMember 2025-03-31 0000903651 us-gaap:AccumulatedGainLossNetCashFlowHedgeParentMember 2025-03-31 0000903651 us-gaap:AccumulatedDefinedBenefitPlansAdjustmentMember 2025-03-31 0000903651 us-gaap:AccumulatedTranslationAdjustmentMember 2024-12-31 0000903651 us-gaap:Accumu | [원문](https://www.sec.gov/Archives/edgar/data/903651/000141057825001113/inod-20250331x10q.htm) |
| `SEC_INOD_000141057824001800` | 10-Q | 2024-11-07 | UNCLASSIFIED | 0000903651 --12-31 2024 Q3 false 0 0 0 0 P3Y P3Y P0Y http://fasb.org/us-gaap/2024#SecuredOvernightFinancingRateSofrMember 0000903651 us-gaap:CommonStockMember 2024-07-01 2024-09-30 0000903651 us-gaap:CommonStockMember 2024-04-01 2024-06-30 0000903651 us-gaap:CommonStockMember 2023-07-01 2023-09-30 0000903651 us-gaap:CommonStockMember 2023-04-01 2023-06-30 0000903651 us-gaap:TreasuryStockCommonMember 2024-09-30 0000903651 us-gaap:RetainedEarningsMember 2024-09-30 0000903651 us-gaap:AdditionalPaidInCapitalMember 2024-09-30 0000903651 us-gaap:AccumulatedTranslationAdjustmentMember 2024-09-30 0000903651 us-gaap:AccumulatedOtherComprehensiveIncomeMember 2024-09-30 0000903651 us-gaap:AccumulatedGainLossNetCashFlowHedgeParentMember 2024-09-30 0000903651 us-gaap:AccumulatedDefinedBenefitPlansAdjustmentMember 2024-09-30 0000903651 us-gaap:TreasuryStockCommonMember 2024-06-30 0000903651 us-gaap:RetainedEarningsMember 2024-06-30 0000903651 us-gaap:AdditionalPaidInCapitalMember 2024-06-30 0000903651 us-gaap:AccumulatedTranslationAdjustmentMember 2024-06-30 0000903651 us-gaap:AccumulatedOtherComprehensiveIncomeMember 2024-06-30 0000903651 us-gaap:AccumulatedGainLossNetCashFlowHedgeParentMember 2024-06-30 0000903651 us-gaap:AccumulatedDefinedBenefitPlansAdjustmentMember 2024-06-30 0000903651 us-gaap:TreasuryStockCommonMember 2024-03-31 0000903651 us-gaap:RetainedEarningsMember 2024-03-31 0000903651 us-gaap:AdditionalPaidInCapitalMember 2024-03-31 0000903651 us-gaap:AccumulatedOtherComprehe | [원문](https://www.sec.gov/Archives/edgar/data/903651/000141057824001800/inod-20240930x10q.htm) |
| `SEC_INOD_000141057824001246` | 10-Q | 2024-08-09 | UNCLASSIFIED | 0000903651 --12-31 2024 Q2 false P3Y P3Y P0Y http://fasb.org/us-gaap/2024#SecuredOvernightFinancingRateSofrMember 0000903651 us-gaap:CommonStockMember 2024-04-01 2024-06-30 0000903651 us-gaap:CommonStockMember 2023-04-01 2023-06-30 0000903651 us-gaap:TreasuryStockCommonMember 2024-06-30 0000903651 us-gaap:RetainedEarningsMember 2024-06-30 0000903651 us-gaap:AdditionalPaidInCapitalMember 2024-06-30 0000903651 us-gaap:AccumulatedTranslationAdjustmentMember 2024-06-30 0000903651 us-gaap:AccumulatedOtherComprehensiveIncomeMember 2024-06-30 0000903651 us-gaap:AccumulatedGainLossNetCashFlowHedgeParentMember 2024-06-30 0000903651 us-gaap:AccumulatedDefinedBenefitPlansAdjustmentMember 2024-06-30 0000903651 us-gaap:TreasuryStockCommonMember 2024-03-31 0000903651 us-gaap:RetainedEarningsMember 2024-03-31 0000903651 us-gaap:AdditionalPaidInCapitalMember 2024-03-31 0000903651 us-gaap:AccumulatedTranslationAdjustmentMember 2024-03-31 0000903651 us-gaap:AccumulatedOtherComprehensiveIncomeMember 2024-03-31 0000903651 us-gaap:AccumulatedGainLossNetCashFlowHedgeParentMember 2024-03-31 0000903651 us-gaap:AccumulatedDefinedBenefitPlansAdjustmentMember 2024-03-31 0000903651 us-gaap:TreasuryStockCommonMember 2023-12-31 0000903651 us-gaap:RetainedEarningsMember 2023-12-31 0000903651 us-gaap:AdditionalPaidInCapitalMember 2023-12-31 0000903651 us-gaap:AccumulatedTranslationAdjustmentMember 2023-12-31 0000903651 us-gaap:AccumulatedOtherComprehensiveIncomeMember 2023-12-31 0000903651 us-gaap:Accumulat | [원문](https://www.sec.gov/Archives/edgar/data/903651/000141057824001246/inod-20240630x10q.htm) |
| `SEC_INOD_000141057824000611` | 10-Q | 2024-05-08 | UNCLASSIFIED | 0000903651 --12-31 2024 Q1 false P3Y P3Y 0 P0Y 0000903651 us-gaap:TreasuryStockCommonMember 2024-03-31 0000903651 us-gaap:RetainedEarningsMember 2024-03-31 0000903651 us-gaap:AdditionalPaidInCapitalMember 2024-03-31 0000903651 us-gaap:AccumulatedTranslationAdjustmentMember 2024-03-31 0000903651 us-gaap:AccumulatedOtherComprehensiveIncomeMember 2024-03-31 0000903651 us-gaap:AccumulatedGainLossNetCashFlowHedgeParentMember 2024-03-31 0000903651 us-gaap:AccumulatedDefinedBenefitPlansAdjustmentMember 2024-03-31 0000903651 us-gaap:TreasuryStockCommonMember 2023-12-31 0000903651 us-gaap:RetainedEarningsMember 2023-12-31 0000903651 us-gaap:AdditionalPaidInCapitalMember 2023-12-31 0000903651 us-gaap:AccumulatedTranslationAdjustmentMember 2023-12-31 0000903651 us-gaap:AccumulatedOtherComprehensiveIncomeMember 2023-12-31 0000903651 us-gaap:AccumulatedGainLossNetCashFlowHedgeParentMember 2023-12-31 0000903651 us-gaap:AccumulatedDefinedBenefitPlansAdjustmentMember 2023-12-31 0000903651 us-gaap:TreasuryStockCommonMember 2023-03-31 0000903651 us-gaap:RetainedEarningsMember 2023-03-31 0000903651 us-gaap:AdditionalPaidInCapitalMember 2023-03-31 0000903651 us-gaap:AccumulatedTranslationAdjustmentMember 2023-03-31 0000903651 us-gaap:AccumulatedOtherComprehensiveIncomeMember 2023-03-31 0000903651 us-gaap:AccumulatedGainLossNetCashFlowHedgeParentMember 2023-03-31 0000903651 us-gaap:AccumulatedDefinedBenefitPlansAdjustmentMember 2023-03-31 0000903651 us-gaap:TreasuryStockCommonMember 2022-12-31 00 | [원문](https://www.sec.gov/Archives/edgar/data/903651/000141057824000611/inod-20240331x10q.htm) |
| `SEC_INOD_000141057823002173` | 10-Q | 2023-11-03 | UNCLASSIFIED | 0000903651 --12-31 2023 Q3 false P3Y P2Y P0Y 0000903651 us-gaap:CommonStockMember 2023-07-01 2023-09-30 0000903651 us-gaap:CommonStockMember 2023-04-01 2023-06-30 0000903651 us-gaap:CommonStockMember 2022-07-01 2022-09-30 0000903651 us-gaap:CommonStockMember 2022-04-01 2022-06-30 0000903651 us-gaap:TreasuryStockCommonMember 2023-09-30 0000903651 us-gaap:RetainedEarningsMember 2023-09-30 0000903651 us-gaap:AdditionalPaidInCapitalMember 2023-09-30 0000903651 us-gaap:AccumulatedTranslationAdjustmentMember 2023-09-30 0000903651 us-gaap:AccumulatedOtherComprehensiveIncomeMember 2023-09-30 0000903651 us-gaap:AccumulatedGainLossNetCashFlowHedgeParentMember 2023-09-30 0000903651 us-gaap:AccumulatedDefinedBenefitPlansAdjustmentMember 2023-09-30 0000903651 us-gaap:TreasuryStockCommonMember 2023-06-30 0000903651 us-gaap:RetainedEarningsMember 2023-06-30 0000903651 us-gaap:AdditionalPaidInCapitalMember 2023-06-30 0000903651 us-gaap:AccumulatedTranslationAdjustmentMember 2023-06-30 0000903651 us-gaap:AccumulatedOtherComprehensiveIncomeMember 2023-06-30 0000903651 us-gaap:AccumulatedGainLossNetCashFlowHedgeParentMember 2023-06-30 0000903651 us-gaap:AccumulatedDefinedBenefitPlansAdjustmentMember 2023-06-30 0000903651 us-gaap:TreasuryStockCommonMember 2023-03-31 0000903651 us-gaap:RetainedEarningsMember 2023-03-31 0000903651 us-gaap:AdditionalPaidInCapitalMember 2023-03-31 0000903651 us-gaap:AccumulatedOtherComprehensiveIncomeMember 2023-03-31 0000903651 2023-03-31 0000903651 us-gaap:Treasur | [원문](https://www.sec.gov/Archives/edgar/data/903651/000141057823002173/inod-20230930x10q.htm) |
| `SEC_INOD_000141057823001711` | 10-Q | 2023-08-11 | UNCLASSIFIED | 0000903651 --12-31 2023 Q2 false P3Y P2Y 0000903651 us-gaap:CommonStockMember 2023-04-01 2023-06-30 0000903651 us-gaap:CommonStockMember 2022-04-01 2022-06-30 0000903651 us-gaap:TreasuryStockCommonMember 2023-06-30 0000903651 us-gaap:RetainedEarningsMember 2023-06-30 0000903651 us-gaap:AdditionalPaidInCapitalMember 2023-06-30 0000903651 us-gaap:AccumulatedTranslationAdjustmentMember 2023-06-30 0000903651 us-gaap:AccumulatedOtherComprehensiveIncomeMember 2023-06-30 0000903651 us-gaap:AccumulatedGainLossNetCashFlowHedgeParentMember 2023-06-30 0000903651 us-gaap:AccumulatedDefinedBenefitPlansAdjustmentMember 2023-06-30 0000903651 us-gaap:TreasuryStockCommonMember 2023-03-31 0000903651 us-gaap:RetainedEarningsMember 2023-03-31 0000903651 us-gaap:AdditionalPaidInCapitalMember 2023-03-31 0000903651 us-gaap:AccumulatedTranslationAdjustmentMember 2023-03-31 0000903651 us-gaap:AccumulatedOtherComprehensiveIncomeMember 2023-03-31 0000903651 us-gaap:AccumulatedGainLossNetCashFlowHedgeParentMember 2023-03-31 0000903651 us-gaap:AccumulatedDefinedBenefitPlansAdjustmentMember 2023-03-31 0000903651 us-gaap:TreasuryStockCommonMember 2022-12-31 0000903651 us-gaap:RetainedEarningsMember 2022-12-31 0000903651 us-gaap:AdditionalPaidInCapitalMember 2022-12-31 0000903651 us-gaap:AccumulatedTranslationAdjustmentMember 2022-12-31 0000903651 us-gaap:AccumulatedOtherComprehensiveIncomeMember 2022-12-31 0000903651 us-gaap:AccumulatedGainLossNetCashFlowHedgeParentMember 2022-12-31 0000903651 us-gaap:Accu | [원문](https://www.sec.gov/Archives/edgar/data/903651/000141057823001711/inod-20230630x10q.htm) |
| `SEC_INOD_000141057823001033` | 10-Q | 2023-05-12 | UNCLASSIFIED | 0000903651 --12-31 2023 Q1 false 27460000 27158000 0.08 0.10 P3Y P2Y P3Y 0000903651 us-gaap:TreasuryStockMember 2023-03-31 0000903651 us-gaap:RetainedEarningsMember 2023-03-31 0000903651 us-gaap:AdditionalPaidInCapitalMember 2023-03-31 0000903651 us-gaap:AccumulatedTranslationAdjustmentMember 2023-03-31 0000903651 us-gaap:AccumulatedOtherComprehensiveIncomeMember 2023-03-31 0000903651 us-gaap:AccumulatedGainLossNetCashFlowHedgeParentMember 2023-03-31 0000903651 us-gaap:AccumulatedDefinedBenefitPlansAdjustmentMember 2023-03-31 0000903651 us-gaap:TreasuryStockMember 2022-12-31 0000903651 us-gaap:RetainedEarningsMember 2022-12-31 0000903651 us-gaap:AdditionalPaidInCapitalMember 2022-12-31 0000903651 us-gaap:AccumulatedTranslationAdjustmentMember 2022-12-31 0000903651 us-gaap:AccumulatedOtherComprehensiveIncomeMember 2022-12-31 0000903651 us-gaap:AccumulatedGainLossNetCashFlowHedgeParentMember 2022-12-31 0000903651 us-gaap:AccumulatedDefinedBenefitPlansAdjustmentMember 2022-12-31 0000903651 us-gaap:TreasuryStockMember 2022-03-31 0000903651 us-gaap:RetainedEarningsMember 2022-03-31 0000903651 us-gaap:AdditionalPaidInCapitalMember 2022-03-31 0000903651 us-gaap:AccumulatedTranslationAdjustmentMember 2022-03-31 0000903651 us-gaap:AccumulatedOtherComprehensiveIncomeMember 2022-03-31 0000903651 us-gaap:AccumulatedGainLossNetCashFlowHedgeParentMember 2022-03-31 0000903651 us-gaap:AccumulatedDefinedBenefitPlansAdjustmentMember 2022-03-31 0000903651 us-gaap:TreasuryStockMember 2021-12-31  | [원문](https://www.sec.gov/Archives/edgar/data/903651/000141057823001033/inod-20230331x10q.htm) |
| `SEC_INOD_000141057822003169` | 10-Q | 2022-11-10 | UNCLASSIFIED | 0000903651 --12-31 2022 Q3 false 27331000 26971000 27239000 26459000 0.12 0.03 0.37 0.02 P3Y P2Y 0000903651 us-gaap:CommonStockMember 2022-07-01 2022-09-30 0000903651 us-gaap:CommonStockMember 2022-04-01 2022-06-30 0000903651 us-gaap:CommonStockMember 2021-07-01 2021-09-30 0000903651 us-gaap:CommonStockMember 2021-04-01 2021-06-30 0000903651 us-gaap:TreasuryStockMember 2022-09-30 0000903651 us-gaap:RetainedEarningsMember 2022-09-30 0000903651 us-gaap:AdditionalPaidInCapitalMember 2022-09-30 0000903651 us-gaap:AccumulatedTranslationAdjustmentMember 2022-09-30 0000903651 us-gaap:AccumulatedOtherComprehensiveIncomeMember 2022-09-30 0000903651 us-gaap:AccumulatedGainLossNetCashFlowHedgeParentMember 2022-09-30 0000903651 us-gaap:AccumulatedDefinedBenefitPlansAdjustmentMember 2022-09-30 0000903651 us-gaap:TreasuryStockMember 2022-06-30 0000903651 us-gaap:RetainedEarningsMember 2022-06-30 0000903651 us-gaap:AdditionalPaidInCapitalMember 2022-06-30 0000903651 us-gaap:AccumulatedTranslationAdjustmentMember 2022-06-30 0000903651 us-gaap:AccumulatedOtherComprehensiveIncomeMember 2022-06-30 0000903651 us-gaap:AccumulatedGainLossNetCashFlowHedgeParentMember 2022-06-30 0000903651 us-gaap:AccumulatedDefinedBenefitPlansAdjustmentMember 2022-06-30 0000903651 2022-06-30 0000903651 us-gaap:TreasuryStockMember 2022-03-31 0000903651 us-gaap:RetainedEarningsMember 2022-03-31 0000903651 us-gaap:AdditionalPaidInCapitalMember 2022-03-31 0000903651 us-gaap:AccumulatedOtherComprehensiveIncomeMember 202 | [원문](https://www.sec.gov/Archives/edgar/data/903651/000141057822003169/inod-20220930x10q.htm) |
| `SEC_INOD_000110465926071384` | 8-K | 2026-06-08 | UNCLASSIFIED | SEC filing metadata only: 8-K filed on 2026-06-08 | [원문](https://www.sec.gov/Archives/edgar/data/903651/000110465926071384/tm2617126d1_8k.htm) |
| `SEC_INOD_000110465926057150` | 8-K | 2026-05-07 | UNCLASSIFIED | SEC filing metadata only: 8-K filed on 2026-05-07 | [원문](https://www.sec.gov/Archives/edgar/data/903651/000110465926057150/tm2612771d1_8k.htm) |
| `SEC_INOD_000110465926033893` | 8-K | 2026-03-24 | UNCLASSIFIED | SEC filing metadata only: 8-K filed on 2026-03-24 | [원문](https://www.sec.gov/Archives/edgar/data/903651/000110465926033893/tm269624d1_8k.htm) |
| `SEC_INOD_000110465926025739` | 8-K | 2026-03-10 | UNCLASSIFIED | Singhal will be entitled to receive: (i) severance equal to 200% of the sum of (A) his base salary and (B) the greater of his most recently declared bonus or the average of his three most recently declared bonuses, payable over 24 months; (ii) continued medical and dental benefits until the earlier of the end of the maximum applicable COBRA coverage period or for the 24 months following termination (or cash payments in lieu thereof following expiration of COBRA coverage); (iii) continued life and long-term disability insurance for 24 months following the termination; and (iv) accelerated vesting of outstanding unvested equity and other incentive awards.
Singhal will be entitled to receive a separation payment consisting of: (i) a lump-sum payment, payable within 30 days following his termination, equal to 300% of the sum of his base salary and the greater of his most recently declared bonus or the average of his three most recently declared bonuses; (ii) continued medical and dental benefits for up to 36 months following termination (or, if shorter, through the end of the applicable COBRA coverage period, with cash payments in lieu of coverage thereafter); (iii) continued life and long-term disability insurance for 36 months following termination; and (iv) accelerated vesting of outstanding unvested equity and other incentive awards. | [원문](https://www.sec.gov/Archives/edgar/data/903651/000110465926025739/tm268318d1_8k.htm) |
| `SEC_INOD_000110465926020514` | 8-K | 2026-02-26 | UNCLASSIFIED | SEC filing metadata only: 8-K filed on 2026-02-26 | [원문](https://www.sec.gov/Archives/edgar/data/903651/000110465926020514/tm265812d1_8k.htm) |
| `SEC_INOD_000110465925108168` | 8-K | 2025-11-07 | UNCLASSIFIED | SEC filing metadata only: 8-K filed on 2025-11-07 | [원문](https://www.sec.gov/Archives/edgar/data/903651/000110465925108168/tm2530546d1_8k.htm) |
| `SEC_INOD_000110465925107811` | 8-K | 2025-11-06 | UNCLASSIFIED | Emerging growth company ¨ If an emerging growth company, indicate by check mark if the registrant has elected not to use the extended transition period for complying with any new or revised financial accounting standards provided pursuant to Section 13(a) of the Exchange Act. | [원문](https://www.sec.gov/Archives/edgar/data/903651/000110465925107811/tm2529621d1_8k.htm) |
| `SEC_INOD_000110465925072724` | 8-K | 2025-07-31 | UNCLASSIFIED | Emerging growth company ¨ If an emerging growth company, indicate by check mark if the registrant has elected not to use the extended transition period for complying with any new or revised financial accounting standards provided pursuant to Section 13(a) of the Exchange Act. | [원문](https://www.sec.gov/Archives/edgar/data/903651/000110465925072724/tm2521272d1_8k.htm) |
| `SEC_INOD_000195004726006076` | 144 | 2026-06-15 | UNCLASSIFIED | Form 144 Filer Information UNITED STATES SECURITIES AND EXCHANGE COMMISSION Washington, D.C.
20549 Form 144 NOTICE OF PROPOSED SALE OF SECURITIES PURSUANT TO RULE 144 UNDER THE SECURITIES ACT OF 1933 FORM 144 144: Filer Information Filer CIK 0001120632 Filer CCC XXXXXXXX Is this a LIVE or TEST Filing?
In addition, information shall be given as to sales by all persons whose sales are required by paragraph (e) of Rule 144 to be aggregated with sales for the account of the person filing this notice.
Relationship to Issuer Officer 144: Securities Information Title of the Class of Securities To Be Sold Name and Address of the Broker Number of Shares or Other Units To Be Sold Aggregate Market Value Number of Shares or Other Units Outstanding Approximate Date of Sale Name the Securities Exchange Common Morgan Stanley Smith Barney LLC Executive Financial Services 1 New York Plaza 8th Floor New York NY 10004 94059 9942619.47 32655358 06/15/2026 NASDAQ Furnish the following information with respect to the acquisition of the securities to be sold and with respect to the payment of all or any part of the purchase price or other consideration therefor: 144: Securities To Be Sold Title of the Class Date you Acquired Nature of Acquisition Transaction Name of Person from Whom Acquired Is this a Gift?
Date Donor Acquired Amount of Securities Acquired Date of Payment Nature of Payment * Common 06/15/2026 Exercise of Options Under a Registered Plan Issuer 94059 06/15/2026 Cash * If the securiti | [원문](https://www.sec.gov/Archives/edgar/data/903651/000195004726006076/xsl144X01/primary_doc.xml) |
| `SEC_INOD_000110465926071398` | 4 | 2026-06-08 | UNCLASSIFIED | Check this box to indicate that a transaction was made pursuant to a contract, instruction or written plan for the purchase or sale of equity securities of the issuer that is intended to satisfy the affirmative defense conditions of Rule 10b5-1(c). | [원문](https://www.sec.gov/Archives/edgar/data/903651/000110465926071398/xslF345X06/tm2617033-4_4seq1.xml) |
| `SEC_INOD_000110465926071396` | 4 | 2026-06-08 | UNCLASSIFIED | Check this box to indicate that a transaction was made pursuant to a contract, instruction or written plan for the purchase or sale of equity securities of the issuer that is intended to satisfy the affirmative defense conditions of Rule 10b5-1(c). | [원문](https://www.sec.gov/Archives/edgar/data/903651/000110465926071396/xslF345X06/tm2617033-3_4seq1.xml) |
| `SEC_INOD_000110465926071394` | 4 | 2026-06-08 | UNCLASSIFIED | Check this box to indicate that a transaction was made pursuant to a contract, instruction or written plan for the purchase or sale of equity securities of the issuer that is intended to satisfy the affirmative defense conditions of Rule 10b5-1(c). | [원문](https://www.sec.gov/Archives/edgar/data/903651/000110465926071394/xslF345X06/tm2617033-2_4seq1.xml) |
| `SEC_INOD_000110465926071387` | 4 | 2026-06-08 | UNCLASSIFIED | Check this box to indicate that a transaction was made pursuant to a contract, instruction or written plan for the purchase or sale of equity securities of the issuer that is intended to satisfy the affirmative defense conditions of Rule 10b5-1(c). | [원문](https://www.sec.gov/Archives/edgar/data/903651/000110465926071387/xslF345X06/tm2617033-1_4seq1.xml) |
| `SEC_INOD_000110465926069841` | 4 | 2026-06-03 | UNCLASSIFIED | Check this box to indicate that a transaction was made pursuant to a contract, instruction or written plan for the purchase or sale of equity securities of the issuer that is intended to satisfy the affirmative defense conditions of Rule 10b5-1(c).
The sale of the shares reported in Column 4 was made as part of the reporting person's personal investment and financial planning needs, including for individual retirement planning and portfolio diversification purposes.
The price reported above reflects the weighted average sale price.
The price reported above reflects the weighted average sale price.
The price reported above reflects the weighted average sale price.
The price reported above reflects the weighted average sale price.
The price reported above reflects the weighted average sale price. | [원문](https://www.sec.gov/Archives/edgar/data/903651/000110465926069841/xslF345X06/tm2616742-1_4seq1.xml) |
| `SEC_INOD_000195004726005477` | 144 | 2026-06-02 | UNCLASSIFIED | Form 144 Filer Information UNITED STATES SECURITIES AND EXCHANGE COMMISSION Washington, D.C.
20549 Form 144 NOTICE OF PROPOSED SALE OF SECURITIES PURSUANT TO RULE 144 UNDER THE SECURITIES ACT OF 1933 FORM 144 144: Filer Information Filer CIK 0001161075 Filer CCC XXXXXXXX Is this a LIVE or TEST Filing?
In addition, information shall be given as to sales by all persons whose sales are required by paragraph (e) of Rule 144 to be aggregated with sales for the account of the person filing this notice.
Relationship to Issuer Officer 144: Securities Information Title of the Class of Securities To Be Sold Name and Address of the Broker Number of Shares or Other Units To Be Sold Aggregate Market Value Number of Shares or Other Units Outstanding Approximate Date of Sale Name the Securities Exchange Common Morgan Stanley Smith Barney LLC Executive Financial Services 1 New York Plaza 8th Floor New York NY 10004 38666 4421249.37 32655358 06/02/2026 NASDAQ Furnish the following information with respect to the acquisition of the securities to be sold and with respect to the payment of all or any part of the purchase price or other consideration therefor: 144: Securities To Be Sold Title of the Class Date you Acquired Nature of Acquisition Transaction Name of Person from Whom Acquired Is this a Gift?
Date Donor Acquired Amount of Securities Acquired Date of Payment Nature of Payment * Common 06/02/2026 Exercise of options under a registered plan Issuer 26666 06/02/2026 Cash Common 12/30/2025 | [원문](https://www.sec.gov/Archives/edgar/data/903651/000195004726005477/xsl144X01/primary_doc.xml) |
| `SEC_INOD_000110465926068962` | 4 | 2026-06-01 | UNCLASSIFIED | Check this box to indicate that a transaction was made pursuant to a contract, instruction or written plan for the purchase or sale of equity securities of the issuer that is intended to satisfy the affirmative defense conditions of Rule 10b5-1(c).
The sale of the shares reported in Column 4 was made as part of the reporting person's financial planning, including for retirement and portfolio diversification purposes.
The price reported above reflects the weighted average sale price.
The price reported above reflects the weighted average sale price.
The price reported above reflects the weighted average sale price. | [원문](https://www.sec.gov/Archives/edgar/data/903651/000110465926068962/xslF345X06/tm2616104-2_4seq1.xml) |
| `SEC_INOD_000110465926068956` | 4 | 2026-06-01 | UNCLASSIFIED | Check this box to indicate that a transaction was made pursuant to a contract, instruction or written plan for the purchase or sale of equity securities of the issuer that is intended to satisfy the affirmative defense conditions of Rule 10b5-1(c).
The sale of the shares reported in Column 4 was made as part of the reporting person's long-term financial planning, including for retirement and portfolio diversification purposes.
The price reported above reflects the weighted average sale price.
The price reported above reflects the weighted average sale price.
The price reported above reflects the weighted average sale price.
The price reported above reflects the weighted average sale price.
The price reported above reflects the weighted average sale price.
The price reported above reflects the weighted average sale price.
The price reported above reflects the weighted average sale price.
The price reported above reflects the weighted average sale price. | [원문](https://www.sec.gov/Archives/edgar/data/903651/000110465926068956/xslF345X06/tm2616104-1_4seq1.xml) |
| `SEC_INOD_000110465925060634` | 8-K | 2025-06-18 | UNCLASSIFIED | Emerging growth company ¨ If an emerging growth company, indicate by check mark if the registrant has elected not to use the extended transition period for complying with any new or revised financial accounting standards provided pursuant to Section 13(a) of the Exchange Act. | [원문](https://www.sec.gov/Archives/edgar/data/903651/000110465925060634/tm2518313d1_8k.htm) |

## Claim–Evidence Links

- Q2 2026 financials are unfunded in both directions: the 10-Q (filed 8/6/2026) was never parsed for income/cash-flow statements and both 8/6/2026 8-Ks were parsed as metadata-only, so revenue growth, gross margin, and cash burn are all 0/unknown with null runway; the Q2 earnings release (Item 2.02) may be an unread exhibit already in evidence → `SEC_INOD_000110465926092021, SEC_INOD_000110465926092133, SEC_INOD_000110465926092010`
- The $300M ATM equity distribution agreement with Goldman Sachs et al. (424B5, 8/6/2026) is a live supply overhang of ~14% of the $2.16B market cap, signed the same day as the CEO transition, with utilization (shares, prices, proceeds) completely unverified → `SEC_INOD_000110465926092131`
- The persisted state model is stale on the largest known risk factor: company_state still carries atm_active=false and dilution_risk=0 while the 424B5 documents the definitive facility; this is an analytical/quality integrity failure feeding downstream consumers, not a chore, and blocks consensus certification → `SEC_INOD_000110465926092131`
- June insider cluster documents informed supply at the top of the range: 10b5-1 Form 4 sales at $103-$111 and a 200,000-share Form 144 (~$22M) with concurrent CFO transition; execution status and the ~1.7M share-count discrepancy (34.38M vs 32,655,358) are unverified, partially funding a weak-bearish tilt → `SEC_INOD_000110465926074783, SEC_INOD_000195004726006175, SEC_INOD_000110465926075184`
- The tape is a confirmed downtrend (-20.2% below MA50, RS -9.3% vs QQQ, -10.2% vs IWM) in a RISK_ON regime, but the snapshot is a stale closed-market artifact (Sunday 23:01 ET, volume 10,349 vs 1,297,102 average, data_quality=OK), making the STAGE_1 label and any support/bounce reading unreliable and the tape unfit for execution signals → `SEC_INOD_000110465926092133`
- FY2026 revenue-growth guidance was reaffirmed via the 6/17 press-release 8-K, but it predates the Q2 print and is weightless pending the coordinated-cluster content read; the no-adverse-filing inference is unknown, not weak-positive → `SEC_INOD_000110465926075184`
- The net-cash cushion pillar (cash_usd=$240.278M vs debt_usd=$877K, ~11% of market cap) is a placeholder swing factor that cannot currently swing: provenance remains flagged, unfunded by choice not availability, with the ~1.7M share-count discrepancy unexplained → `SEC_INOD_000195004726006175, SEC_INOD_000110465926092021`
- The two highest-severity triggers (Q2 deceleration -> TRIM/SELL; ATM utilization >20% -> SELL) cannot currently fire because the decisive evidence was never read or fetched; WAIT at 58 is 'hold pending evidence' in substance and must not be exported as a risk-managed position → `SEC_INOD_000110465926092021, SEC_INOD_000110465926092131`
- Label convergence on WAIT within the agreed 55-60 band is real and both sides prescribe the identical retain action, but it is an artifact of absent evidence, not analytical resolution; consensus_ready=false must be mechanically enforced on export → `SEC_INOD_000110465926092021, SEC_INOD_000110465926092131`

# Risk Engine

- Hard Filter: `PASS`
- Risk Decision: `WAIT`
- Rule Version: `risk_rules_v0.2`
- Warnings: `Critic이 Research 결론에 중대한 이의를 제기함, Reward/Risk 1.9가 기준 미만`
- Failures: `없음`

# Capital Structure

- Shares Outstanding: `34382651.0`
- ATM Capacity: `None`
- Warrants / Convertibles: `KNOWN_PRESENT` / `KNOWN_PRESENT`
- Cash / Burn / Runway: `240278000.0` / `0.0` / `None`
- Unknown Fields: `share_growth_yoy, shelf_capacity, atm_capacity, recent_atm_usage, runway_months, potential_dilution_pct, fully_diluted_share_estimate`

# TradePlan

- Entry: `$62.93`
- Preferred Range: `$59.81–$62.93`
- Stop: `$57.63`
- Target 1 / 2: `$73.00` / `$83.07`
- Reward/Risk: `1.9`

# Position Sizing

- Quantity: `141` shares (PAPER)
- Notional: `$8,873.13`
- Portfolio Weight: `8.87%`
- Limiting Rule: `LOSS_BUDGET`

# Scenario Analysis

- 실데이터·근거 기반 최근 사업 신호
- 20D 수익률 -7.62% / 상대거래량 0.01x
- 전략 적합도 40/100

# Failure Scenarios

- The pipeline exports WAIT 58 / retain guidance before the state rebuild lands and the Q2 read completes: downstream consumers act on a persisted model carrying atm_active=false with a live $300M facility; when ATM utilization later prints, holders were retained through the window the model misreported.
- The unread 8/6/2026 8-Ks contain an adverse Q2 print (deceleration, margin compression, or negative burn) that is already in evidence unread; WAIT-58 holders retain and the TRIM/SELL trigger never fires because the parse never ran, extending the decline from $62.93.
- Shares are sold under the $300M Goldman ATM at ~$63 into thin liquidity (snapshot volume 10,349 vs 1,297,102 average), amplifying price impact and converting the ~14% dilution overhang into realized supply; exit costs for holders rise exactly as the thin-tape amplifier argument warns.
- The 200,000-share Form 144 executed (or new insider sales print at ~$63), confirming informed supply alongside the CFO transition; the documented weak-bearish tilt deepens into a fundamentals-led drawdown and the 'unexplained selloff' framing collapses.
- The internally inconsistent ledger (absent issue ID, OPEN-status 'closed' update) propagates into the exported report, and the 'no certification until the gate' guarantee fails at the exact point of export because enforcement was asserted rather than demonstrated.

# Invalidation Conditions

- Content read of the 8/6 8-Ks plus 10-Q financial parse showing deceleration, margin compression, or negative operating cash burn fires the TRIM/SELL rule and flips this stance bearish
- Post-8/6 disclosure showing ATM utilization above 20% of the $300M Goldman facility triggers SELL
- Execution of the June 200K-share Form 144 or new insider sales at ~$63 deepens the supply-overhang concern and tilts toward a funded bearish stance
- Landed state-model rebuild (atm_active=true, dilution_risk populated, share count reconciled against the 6/16 Form 144) with cash_usd/debt_usd verified against the Q2 balance sheet clears the hard gate and permits re-certification
- Unread Q2 evidence showing stable or accelerating revenue with positive operating cash flow and zero ATM utilization would move the stance from WAIT toward HOLD/entry-ready at the TradePlan's 59.81-62.93 preferred zone

# Final Decision

- Proposed by Chairman: `WAIT`
- Final after Python Guard: `WAIT`
- Top Risks: `SEC CompanyFacts 통합 전 미확인, Critic이 Research 결론에 중대한 이의를 제기함, Reward/Risk 1.9가 기준 미만`

# Run Metadata

- Run ID: `20260810_031126_INOD_5c7a21`
- Requested At: `2026-08-10T03:11:26.849838+00:00`
- As Of: `2026-08-10T03:45:42.917895+00:00`
- Ticker: `INOD`
- Intent: `ANALYZE`
- Model: `deepseek/deepseek-v4-flash`
- Research Prompt: `research_v003`
- Critic Prompt: `critic_v002`
- Chairman Prompt: `chairman_v001`
- Risk Rule: `risk_rules_v0.2`
- Data Provider: `TOSS_OPEN_API`
- Run Status: `SUCCESS`
- Price As Of: `2026-08-10T12:01:02.000+09:00`
- Candle As Of: `2026-08-10T12:01:02.000+09:00`
- SEC Latest Filed At: `2026-08-06`
- CompanyFacts As Of: `2026-08-06`
- Market Regime As Of: `see context`

# API Cost

- LLM Calls: `21`
- Input / Output Tokens: `293518` / `258971`
- Reasoning Tokens: `181602`
- Cache Tokens: `401664`
- Estimated Cost: `$0.114729`
- Total LLM Latency: `2018695 ms`
