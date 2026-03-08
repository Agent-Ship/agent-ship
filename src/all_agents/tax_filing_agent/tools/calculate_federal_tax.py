"""US Federal Tax Calculator for Tax Year 2024."""

import json
import logging
from src.agent_framework.tools.base_tool import BaseTool

logger = logging.getLogger(__name__)


class CalculateFederalTaxTool(BaseTool):
    """Calculate US federal income tax for tax year 2024."""

    def __init__(self):
        super().__init__(
            name="calculate_federal_tax",
            description=(
                "Calculate US federal income tax for tax year 2024. "
                "Input must be a JSON string with these fields: "
                "wages (number, W-2 Box 1 total wages), "
                "federal_withheld (number, federal income tax already withheld W-2 Box 2), "
                "filing_status (string: 'single' | 'married_jointly' | 'married_separately' | 'head_of_household'), "
                "children_under_17 (integer, qualifying children under 17 for Child Tax Credit), "
                "other_income (number, freelance/1099/interest/dividend income not in W-2), "
                "retirement_contributions (number, traditional IRA contributions in 2024), "
                "student_loan_interest (number, student loan interest paid in 2024), "
                "use_standard_deduction (boolean, true=standard false=itemized), "
                "itemized_deductions (number, total itemized deductions if use_standard_deduction is false). "
                "Example: {\"wages\":75000,\"federal_withheld\":8500,\"filing_status\":\"single\","
                "\"children_under_17\":0,\"other_income\":0,\"retirement_contributions\":0,"
                "\"student_loan_interest\":0,\"use_standard_deduction\":true,\"itemized_deductions\":0}. "
                "Returns a JSON string with full 2024 federal tax breakdown including "
                "gross_income, agi, taxable_income, bracket_breakdown, tax_after_credits, "
                "effective_tax_rate, and refund_amount or amount_owed."
            ),
        )

    def run(self, input: str) -> str:  # noqa: A002
        try:
            data = json.loads(input)
        except (json.JSONDecodeError, TypeError):
            return json.dumps({"error": "Invalid JSON input", "hint": "Pass a JSON string with the required fields."})

        wages = float(data.get("wages", 0))
        federal_withheld = float(data.get("federal_withheld", 0))
        filing_status = str(data.get("filing_status", "single")).lower().replace(" ", "_")
        children = int(data.get("children_under_17", 0))
        other_income = float(data.get("other_income", 0))
        retirement = float(data.get("retirement_contributions", 0))
        student_loan = float(data.get("student_loan_interest", 0))
        use_standard = bool(data.get("use_standard_deduction", True))
        itemized = float(data.get("itemized_deductions", 0))

        # 2024 Standard Deductions
        STANDARD_DEDUCTIONS = {
            "single": 14600,
            "married_jointly": 29200,
            "married_separately": 14600,
            "head_of_household": 21900,
        }

        # 2024 Tax Brackets: (lower_bound, upper_bound, rate)
        BRACKETS = {
            "single": [
                (0, 11600, 0.10),
                (11600, 47150, 0.12),
                (47150, 100525, 0.22),
                (100525, 191950, 0.24),
                (191950, 243725, 0.32),
                (243725, 609350, 0.35),
                (609350, float("inf"), 0.37),
            ],
            "married_jointly": [
                (0, 23200, 0.10),
                (23200, 94300, 0.12),
                (94300, 201050, 0.22),
                (201050, 383900, 0.24),
                (383900, 487450, 0.32),
                (487450, 731200, 0.35),
                (731200, float("inf"), 0.37),
            ],
            "married_separately": [
                (0, 11600, 0.10),
                (11600, 47150, 0.12),
                (47150, 100525, 0.22),
                (100525, 191950, 0.24),
                (191950, 243725, 0.32),
                (243725, 365600, 0.35),
                (365600, float("inf"), 0.37),
            ],
            "head_of_household": [
                (0, 16550, 0.10),
                (16550, 63100, 0.12),
                (63100, 100500, 0.22),
                (100500, 191950, 0.24),
                (191950, 243700, 0.32),
                (243700, 609350, 0.35),
                (609350, float("inf"), 0.37),
            ],
        }

        if filing_status not in BRACKETS:
            filing_status = "single"

        # ── Income ──────────────────────────────────────────────────────────
        gross_income = wages + other_income

        # Above-the-line deductions (adjustments to income)
        ira_limit = 14000 if filing_status == "married_jointly" else 7000
        atl = min(retirement, ira_limit) + min(student_loan, 2500)
        agi = max(0.0, gross_income - atl)

        # ── Deductions ──────────────────────────────────────────────────────
        std_ded = STANDARD_DEDUCTIONS[filing_status]
        if use_standard or itemized <= std_ded:
            deduction_amount = std_ded
            deduction_type = "Standard Deduction"
        else:
            deduction_amount = itemized
            deduction_type = "Itemized Deductions"

        taxable_income = max(0.0, agi - deduction_amount)

        # ── Tax via brackets ─────────────────────────────────────────────────
        tax = 0.0
        bracket_breakdown = []
        marginal_rate = "0%"
        for lower, upper, rate in BRACKETS[filing_status]:
            if taxable_income <= lower:
                break
            income_in = min(taxable_income, upper) - lower
            bracket_tax = income_in * rate
            tax += bracket_tax
            marginal_rate = f"{int(rate * 100)}%"
            bracket_breakdown.append({
                "bracket": f"{int(rate * 100)}%",
                "range": f"${lower:,.0f}–{'∞' if upper == float('inf') else f'${upper:,.0f}'}",
                "income_in_bracket": round(income_in, 2),
                "tax_in_bracket": round(bracket_tax, 2),
            })

        # ── Child Tax Credit (2024: $2,000/child, $1,700 refundable) ─────────
        ctc_phaseout = 400000 if filing_status == "married_jointly" else 200000
        if agi <= ctc_phaseout:
            ctc = children * 2000.0
        else:
            reduction = ((agi - ctc_phaseout) // 1000) * 50
            ctc = max(0.0, children * 2000.0 - reduction)

        nonrefundable_ctc = min(ctc, tax)
        additional_ctc = min(children * 1700.0, max(0.0, ctc - nonrefundable_ctc))

        # ── Final calculation ────────────────────────────────────────────────
        tax_after_credits = max(0.0, tax - nonrefundable_ctc)
        effective_rate = round((tax_after_credits / gross_income * 100), 2) if gross_income > 0 else 0.0
        balance = federal_withheld + additional_ctc - tax_after_credits

        return json.dumps({
            "gross_income": round(gross_income, 2),
            "above_the_line_deductions": round(atl, 2),
            "agi": round(agi, 2),
            "deduction_type": deduction_type,
            "deduction_amount": round(deduction_amount, 2),
            "taxable_income": round(taxable_income, 2),
            "tax_before_credits": round(tax, 2),
            "bracket_breakdown": bracket_breakdown,
            "child_tax_credit": round(nonrefundable_ctc, 2),
            "additional_child_tax_credit": round(additional_ctc, 2),
            "tax_after_credits": round(tax_after_credits, 2),
            "federal_withheld": round(federal_withheld, 2),
            "effective_tax_rate": effective_rate,
            "marginal_tax_rate": marginal_rate,
            "result_type": "refund" if balance >= 0 else "owe",
            "refund_amount": round(balance, 2) if balance >= 0 else 0.0,
            "amount_owed": round(-balance, 2) if balance < 0 else 0.0,
            "filing_status": filing_status,
            "tax_year": 2024,
        })
