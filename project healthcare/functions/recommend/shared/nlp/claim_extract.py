"""Clinical note -> insurance claim assistant (docs/BLUEPRINT.md Part 8.5).

Uses Azure AI Language for Health (Text Analytics for Health) to extract
medical entities from synthetic clinical notes, then pre-fills and validates
a Claims record. Purely extractive/logistical — never diagnostic advice.
"""
import json
import logging

from .. import config, db

REQUIRED_CLAIM_FIELDS = ["diagnosisCodes", "provider", "amount"]

_ENTITY_TO_CLAIM_MAP = {
    "Diagnosis": "diagnosisCodes",
    "MedicationName": "medications",
    "SymptomOrSign": "symptoms",
    "Dosage": "dosages",
    "TreatmentName": "procedures",
}


def extract_entities(note_text: str) -> list[dict]:
    """Calls Text Analytics for Health; returns a flat list of {text, category}."""
    if not config.LANGUAGE_ENDPOINT:
        logging.info("[nlp:noop] LANGUAGE_ENDPOINT not configured, skipping extraction")
        return []
    try:
        from azure.ai.textanalytics import TextAnalyticsClient
        from azure.core.credentials import AzureKeyCredential
        from azure.identity import DefaultAzureCredential

        credential = AzureKeyCredential(config.LANGUAGE_KEY) if config.LANGUAGE_KEY else DefaultAzureCredential()
        client = TextAnalyticsClient(endpoint=config.LANGUAGE_ENDPOINT, credential=credential)

        poller = client.begin_analyze_healthcare_entities([note_text])
        results = list(poller.result())
        entities = []
        for doc in results:
            if doc.is_error:
                continue
            for entity in doc.entities:
                entities.append({"text": entity.text, "category": entity.category, "confidence": entity.confidence_score})
        return entities
    except Exception as exc:
        logging.warning("Text Analytics for Health call failed: %s", exc)
        return []


def build_claim_fields(entities: list[dict]) -> dict:
    """Groups extracted entities into claim-shaped fields."""
    fields: dict[str, list[str]] = {}
    for entity in entities:
        claim_key = _ENTITY_TO_CLAIM_MAP.get(entity["category"])
        if not claim_key:
            continue
        fields.setdefault(claim_key, []).append(entity["text"])
    return fields


def missing_fields(fields: dict) -> list[str]:
    return [f for f in REQUIRED_CLAIM_FIELDS if f not in fields or not fields[f]]


def create_claim_from_note(patient_id: int, provider: str, note_text: str, amount: float | None = None) -> dict:
    entities = extract_entities(note_text)
    fields = build_claim_fields(entities)
    diagnosis_codes = ", ".join(fields.get("diagnosisCodes", [])) or None

    claim_fields = {
        "provider": provider,
        "amount": amount,
        "diagnosisCodes": diagnosis_codes,
        **fields,
    }
    missing = missing_fields(claim_fields)
    status = "ready" if not missing else "draft"

    claim_id = db.execute_returning_id(
        """
        INSERT INTO Claims (patientId, provider, amount, diagnosisCodes, status, extractedFields, missingFields)
        OUTPUT INSERTED.id
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (patient_id, provider, amount, diagnosis_codes, status, json.dumps(fields), json.dumps(missing)),
    )
    return {
        "id": claim_id,
        "patientId": patient_id,
        "provider": provider,
        "amount": amount,
        "diagnosisCodes": diagnosis_codes,
        "status": status,
        "extractedFields": fields,
        "missingFields": missing,
        "extractedEntities": entities,
    }
