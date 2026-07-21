"""Registers every function blueprint (docs/BLUEPRINT.md Part 4).

Route-level auth is enforced in shared/auth.py via the AD B2C client
principal header forwarded by Static Web Apps / API Management; this app's
own auth level stays ANONYMOUS so APIM/SWA remains the single front door.
"""
import azure.functions as func

from api_audit.blueprint import bp as api_audit_bp
from api_caregiver.blueprint import bp as api_caregiver_bp
from api_claims.blueprint import bp as api_claims_bp
from api_dashboard.blueprint import bp as api_dashboard_bp
from api_schedule.blueprint import bp as api_schedule_bp
from compute_risk.blueprint import bp as compute_risk_bp
from ingest_vitals.blueprint import bp as ingest_vitals_bp
from integrations.blueprint import bp as integrations_bp
from med_reminders.blueprint import bp as med_reminders_bp
from process_metrics.blueprint import bp as process_metrics_bp
from recommend.blueprint import bp as recommend_bp

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

app.register_functions(ingest_vitals_bp)
app.register_functions(process_metrics_bp)
app.register_functions(compute_risk_bp)
app.register_functions(recommend_bp)
app.register_functions(med_reminders_bp)
app.register_functions(api_dashboard_bp)
app.register_functions(api_schedule_bp)
app.register_functions(api_claims_bp)
app.register_functions(api_caregiver_bp)
app.register_functions(api_audit_bp)
app.register_functions(integrations_bp)
