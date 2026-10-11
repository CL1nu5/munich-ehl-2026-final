"""Static priors: how to recognise a kind of supplier in the data and what usually moves it.

Written from the data package and the brief - never updated from case outcomes, so they are the
same for every run. What the agent learns lives in the run memory (`memory/`), not here.
"""

# tag -> detection (lower-case phrases in notes/letters/quote remarks, news topics) and advice.
# `rate`: what money now is worth to such a supplier per year (cost.supplier_value).
# `levers`: {field: "+"|"-"} - moving the field this way is cheap for us to give and valued by it.
ARCHETYPES = {
    "cash_short": {
        "phrases": [
            "bank is watching",
            "pay earlier",
            "pay our invoices within 10 days",
            "payment within 10 days",
            "spoke mostly about cash",
            "shop floor was idle",
            "short hours",
        ],
        "news": ["credit_watch", "credit_downgrade", "restructuring", "short_time_work", "insolvency"],
        "rate": 0.18,
        "levers": {"payment_days": "-"},
        "advice": "Short of cash: faster payment is worth more to it than price; trade earlier payment for a "
        "lower price, never squeeze its margin openly.",
    },
    "relationship": {
        "phrases": ["partnership in return", "reference customer"],
        "news": ["succession"],
        "rate": 0.08,
        "levers": {},
        "advice": "Values the relationship and remembers how it is treated: warm tone, acknowledge past "
        "support, no threats; frame asks as fair partnership.",
    },
    "weak_costing": {
        "phrases": ["own spreadsheet", "had not followed his material", "office manager does the costing"],
        "news": [],
        "rate": 0.08,
        "levers": {},
        "advice": "Does not track its own cost drivers: verified index data can disarm it ('we did not know "
        "that'); ask for its cost breakdown.",
    },
    "open_books": {
        "phrases": ["walk us through their calculation"],
        "news": [],
        "rate": 0.08,
        "levers": {},
        "advice": "Offered to open its calculation: ask for the cost breakdown and argue element by element.",
    },
    "committee": {
        "phrases": ["pricing committee"],
        "news": ["approvals"],
        "rate": 0.07,
        "levers": {},
        "advice": "Price moves above about 2 % need a committee: ask in small steps and use non-price levers "
        "(payment, contract length, Incoterm) for the rest.",
    },
    "price_frozen": {
        "phrases": [
            "frozen all price reductions",
            "discounts not possible",
            "head-office directive",
            "list conditions",
            "prices as per current list",
            "ruled out individual price reductions",
            "individual price reductions",
            "price list 2027 applies to all",
        ],
        "news": [],
        "rate": 0.07,
        "levers": {"payment_days": "+", "contract_months": "+"},
        "advice": "Cannot move its list price: do not spend rounds on price; negotiate payment terms, "
        "call-off flexibility, contract length and Incoterm instead.",
    },
    "hedged_forward": {
        "phrases": [
            "forward until",
            "not our reality",
            "material covered until",
            "forward at fixed prices",
            "at fixed prices until",
        ],
        "news": ["energy_contract"],
        "rate": 0.07,
        "levers": {},
        "advice": "Hedged its material/energy forward: index moves reach its costs only when the hedge ends, so "
        "an index-based increase or an index clause is not justified before then - say so, citing its own "
        "letter or quote remark; our own index claims meet 'not our reality', use volume, contract length "
        "and the timing of its own hedge instead.",
    },
    "full_order_book": {
        "phrases": ["order intake is very strong", "binding 12-month commitment"],
        "news": ["full_order_book", "allocation", "big_order", "chip_shortage"],
        "rate": 0.07,
        "levers": {"contract_months": "+"},
        "advice": "Sold out: it has the leverage. Secure supply with a longer binding commitment; avoid "
        "threats and aggressive anchors.",
    },
    "spare_capacity": {
        "phrases": ["at short notice", "second shift has been on hold"],
        "news": ["free_capacity"],
        "rate": 0.09,
        "levers": {"contract_months": "+"},
        "advice": "Has idle capacity: volume and a longer contract are worth a lot to it - ask for a clearly "
        "better price in return.",
    },
    "not_strategic": {
        "phrases": ["not strategic", "reviewing small accounts", "supply from stock only"],
        "news": ["refocus"],
        "rate": 0.07,
        "levers": {},
        "advice": "We are a small account to it: modest asks, quick decisions; it may walk if pushed hard.",
    },
    "unpaid": {
        "phrases": ["invoices are overdue"],
        "news": [],
        "rate": 0.10,
        "levers": {"payment_days": "-"},
        "advice": "We paid it late in the past: commit to paying on time; do not ask for longer payment terms.",
    },
}

DEFAULT_RATE = 0.08

# General playbook from the brief: these hold for every supplier and every run.
GENERAL = [
    "Never accept the first offer; ask, and offer two or three alternative packages to learn what it values.",
    "Never reveal urgency, a budget, a missing alternative or a target price.",
    "Cite only verified data, one argument per sentence, at most three per message.",
    "Unstated terms close on the supplier's standard terms: always put every negotiable field in a sheet.",
    "A final offer is its last word: push further only when we have a real alternative.",
    "Never concede a lever (payment, contract, index clause, lead time, quality terms) outside the packages: "
    "each one is priced in, and the supplier takes whichever package is dearest for us.",
    "Suppliers move most in their first replies and turn to a final offer after a few rounds: put the "
    "strongest claims into the first message instead of saving them.",
    "Prose arguments without data draw 'which figures is that based on?': mention volumes, forecasts, market "
    "moves or rivals only through a claim. When the supplier asks which volume we commit to, code states our "
    "binding volume in figures - do not answer it vaguely yourself.",
    "What we paid before (price history) does not change a supplier's calculation; falling cost drivers, rival "
    "quotes, forecast growth and contract notice periods do.",
    "When the supplier insists on a term (an index clause, a payment term), keep it in the packages from then "
    "on; code reprices it so it costs us the same.",
]


def rate_for(tags):
    """Supplier interest rate estimate: the most cash-hungry tag wins."""
    rates = [ARCHETYPES[t]["rate"] for t in tags if t in ARCHETYPES]
    return max(rates) if rates else DEFAULT_RATE


def advice_for(tags):
    return [f"{t}: {ARCHETYPES[t]['advice']}" for t in tags if t in ARCHETYPES]
