import unittest

from stock_agent.capital_structure import build_capital_structure
from stock_agent.edgar_documents import ExhibitResolver
from stock_agent.schemas import EvidenceItem
from stock_agent.sec import SECCompanyFactsProvider, derive_standalone_quarter


class FilingLifecycleTests(unittest.TestCase):
    def test_8k_item_202_resolves_99_exhibit(self):
        content = b'''<html><body>Item 2.02 Results of Operations
            <a href="exhibit99-1.htm">Exhibit 99.1</a></body></html>'''
        links = ExhibitResolver().resolve_links(
            "https://www.sec.gov/Archives/edgar/data/1/a/form8k.htm", content
        )
        self.assertEqual(
            links, ["https://www.sec.gov/Archives/edgar/data/1/a/exhibit99-1.htm"]
        )


class FinancialOntologyTests(unittest.TestCase):
    def test_debt_payment_flow_is_not_debt_balance(self):
        payload = {"facts": {"us-gaap": {
            "PaymentsOfLongTermDebt": {"units": {"USD": [{
                "start": "2026-01-01", "end": "2026-06-30", "val": 500,
                "form": "10-Q", "filed": "2026-08-06", "accn": "A",
            }]}},
            "LongTermDebtNoncurrent": {"units": {"USD": [{
                "end": "2026-06-30", "val": 100, "form": "10-Q",
                "filed": "2026-08-06", "accn": "A",
            }]}},
        }}}
        rows = SECCompanyFactsProvider._normalized_rows(payload)
        ontology = SECCompanyFactsProvider._debt_ontology(rows)
        self.assertEqual(ontology["long_term_borrowings"]["value"], 100)
        self.assertEqual(ontology["financial_debt"]["value"], 100)

    def test_q2_ytd_derivation_has_formula_and_sources(self):
        result = derive_standalone_quarter(
            {"value": 250, "fact_id": "YTD"}, {"value": 100, "fact_id": "Q1"}
        )
        self.assertEqual(result["value"], 150)
        self.assertTrue(result["derived"])
        self.assertEqual(result["formula"], "6M_YTD - Q1")
        self.assertEqual(result["source_fact_ids"], ["YTD", "Q1"])


class CapitalOntologyTests(unittest.TestCase):
    def test_atm_capacity_is_not_usage(self):
        evidence = [EvidenceItem(
            "ATM", "INOD", "SEC", "424B5", "2026-08-06", "ATM", "u", "C",
            "OFFERING", "up to $300,000,000 under an at-the-market sales agreement",
            normalized_fact="up to $300,000,000 under an at-the-market sales agreement",
            accession="A", filed_at="2026-08-06",
        )]
        snapshot = build_capital_structure("INOD", {
            "shares_outstanding": {"value": 32_000_000, "accn": "Q"},
            "normalized_facts": [], "derived": {},
        }, evidence)
        self.assertEqual(snapshot.atm_authorized_capacity.value, 300_000_000)
        self.assertEqual(snapshot.atm_used_amount.status, "UNKNOWN")
        self.assertNotEqual(snapshot.atm_used_amount.value, 300_000_000)

    def test_warrant_offerable_language_is_not_outstanding(self):
        evidence = [EvidenceItem(
            "S3", "INOD", "SEC", "S-3", "2026-08-06", "Shelf", "u", "C",
            "OFFERING", "we may offer warrants", normalized_fact="we may offer warrants",
        )]
        snapshot = build_capital_structure("INOD", {"normalized_facts": [], "derived": {}}, evidence)
        self.assertEqual(snapshot.warrant_offerable.status, "KNOWN")
        self.assertTrue(snapshot.warrant_offerable.value)
        self.assertEqual(snapshot.warrant_outstanding.status, "UNKNOWN")


if __name__ == "__main__":
    unittest.main()
