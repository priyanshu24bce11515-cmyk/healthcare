"""Timer: rolling trend safety-net + threshold sweep (docs/BLUEPRINT.md Part 6.A).

`ingest_vitals` already checks thresholds synchronously on the HTTP path;
this timer re-sweeps the last window so vitals arriving through any other
path (e.g. IoT Hub, batch import) still raise alerts, and avoids re-raising
an alert that already fired recently for the same reading.
"""
import logging
from datetime import datetime, timedelta, timezone

import azure.functions as func

from shared import db
from shared.alerts import rules as alert_rules

bp = func.Blueprint()

_SWEEP_WINDOW_MINUTES = 20
_DEDUPE_WINDOW_MINUTES = 60


@bp.timer_trigger(schedule="0 */15 * * * *", arg_name="timer", run_on_startup=False)
def process_metrics_timer(timer: func.TimerRequest) -> None:
    since = datetime.now(timezone.utc) - timedelta(minutes=_SWEEP_WINDOW_MINUTES)
    recent_vitals = db.query(
        "SELECT id, patientId, type, value FROM Vitals WHERE recordedAt >= ? ORDER BY recordedAt ASC",
        (since,),
    )

    dedupe_since = datetime.now(timezone.utc) - timedelta(minutes=_DEDUPE_WINDOW_MINUTES)
    already_alerted = {
        (a["patientId"], a["vitalType"])
        for a in db.query(
            "SELECT patientId, vitalType FROM Alerts WHERE kind = 'vital' AND raisedAt >= ?", (dedupe_since,)
        )
    }

    raised = 0
    for v in recent_vitals:
        key = (v["patientId"], v["type"])
        if key in already_alerted:
            continue
        try:
            if alert_rules.check_vital(v["patientId"], v["type"], float(v["value"])):
                raised += 1
                already_alerted.add(key)
        except Exception as exc:
            logging.warning("process_metrics: threshold check failed for vital %s: %s", v["id"], exc)

    logging.info("process_metrics: swept %d vitals, raised %d alerts", len(recent_vitals), raised)
