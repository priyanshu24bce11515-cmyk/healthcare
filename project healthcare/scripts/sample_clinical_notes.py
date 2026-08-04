"""Sample synthetic clinical notes for the claim-assistant demo
(docs/BLUEPRINT.md Part 8.5, Appendix B). Feed one of these to
POST /claims { patientId, provider, noteText } to see entity extraction
pre-fill and validate a claim.
"""

SAMPLE_NOTES = [
    {
        "label": "Fever and cough, paracetamol prescribed",
        "text": (
            "Patient reports fever and cough for the past 3 days. Temperature 100.9F. "
            "Prescribed paracetamol 500mg twice daily for 5 days. Advised rest and fluids. "
            "Diagnosis: acute upper respiratory infection (J06.9)."
        ),
    },
    {
        "label": "Type 2 diabetes follow-up",
        "text": (
            "Follow-up visit for type 2 diabetes mellitus (E11.9). Patient reports occasional "
            "fatigue and increased thirst. Fasting glucose 162 mg/dL. Continuing metformin 500mg "
            "twice daily. Recommended dietary counseling and follow-up in 3 months."
        ),
    },
    {
        "label": "Hypertension check-in",
        "text": (
            "Routine check-in for essential hypertension (I10). Blood pressure 148/94 mmHg. "
            "Patient prescribed lisinopril 10mg once daily in the morning. No chest pain or "
            "shortness of breath reported."
        ),
    },
]
