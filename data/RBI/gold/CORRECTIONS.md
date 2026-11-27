# Corrections to the RBI gold set

Two questions were labelled `unanswerable` ("not in corpus"), but the corpus answers both directly.
Left unchanged, the evaluation would have scored a correct answer as a failure. Both are now
`lookup` questions with the reference below; the original entries are kept here.

## Q29: What minimum capital to risk-weighted assets ratio (CRAR) must a commercial bank maintain?

- **Was:** type `unanswerable`. Answer: Not in corpus. None of the indexed Directions states a CRAR requirement; it would be in the Commercial Banks – Prudential Norms on Capital Adequacy Directions, which is not part of this corpus. The copilot should say so rather than guess.
- **Now:** type `lookup`. Answer: A commercial bank must maintain a minimum Pillar 1 Capital to Risk-weighted Assets Ratio (CRAR) of 9 per cent on an ongoing basis, excluding capital buffers.
- **Source:** 28_CB_CapitalAdequacy_2025_upd2026-10-07.pdf, page 13, para 9

## Q30: What minimum Liquidity Coverage Ratio does a commercial bank have to maintain?

- **Was:** type `unanswerable`. Answer: Not in corpus. The Interest Rate on Deposits Directions only refer to run-off rates 'under the LCR framework' in the Asset Liability Management Directions, which are not part of this corpus. The copilot should not invent a figure.
- **Now:** type `lookup`. Answer: A commercial bank must maintain a Liquidity Coverage Ratio (LCR) of at least 100 per cent on an ongoing basis, so that its stock of high-quality liquid assets is at least equal to its total net cash outflows over the next 30 days.
- **Source:** 16_CB_AssetLiabilityManagement_2025.pdf, page 44, para 102

The RBI set therefore has no unanswerable questions. Abstention on missing documents is still
tested by the tax set's two `correct_premise` questions (REF-03 and REF-04); consider adding new RBI
questions on topics verified to be absent from all 72 Directions.
