"""Financial Crime Copilot — analyst case cockpit (Databricks App, FastAPI).

Reads the ML-prioritised case queue from Lakebase (a synced table fed from Unity Catalog),
writes analyst dispositions back to Lakebase (transactional), and lets investigators ask
questions in natural language through the Genie Room.
"""
import os
import threading
import time

import psycopg
from psycopg.rows import dict_row
from databricks.sdk import WorkspaceClient
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

w = WorkspaceClient()

SERVING_SCHEMA = os.getenv("SERVING_SCHEMA", "aml_serving")   # synced table (read-only)
APP_SCHEMA = os.getenv("APP_SCHEMA", "fcc_app")               # app-owned, written by analysts
GENIE_SPACE_ID = os.getenv("GENIE_SPACE_ID")
ACTIONS = ("ESCALATE", "SAR_FILED", "CLOSE_FALSE_POSITIVE")

# ---- Lakebase connection with OAuth token refresh -------------------------------------
_token = {"value": None, "at": 0.0}
_lock = threading.Lock()


def _password() -> str:
    with _lock:
        if not _token["value"] or time.time() - _token["at"] > 30 * 60:
            _token["value"] = w.postgres.generate_database_credential(
                endpoint=os.environ["LAKEBASE_ENDPOINT"]).token
            _token["at"] = time.time()
        return _token["value"]


def db():
    return psycopg.connect(
        host=os.environ["PGHOST"], port=int(os.getenv("PGPORT", "5432")),
        dbname=os.environ["PGDATABASE"], user=os.environ["PGUSER"],
        password=_password(), sslmode="require", row_factory=dict_row)


def init_schema():
    # The app's service principal creates and owns its schema. REPLICA IDENTITY FULL lets
    # Lakehouse Sync stream every disposition back into Unity Catalog as an audit history.
    with db() as conn:
        conn.execute(f"CREATE SCHEMA IF NOT EXISTS {APP_SCHEMA}")
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {APP_SCHEMA}.case_dispositions (
              disposition_id BIGSERIAL PRIMARY KEY,
              alert_id       TEXT NOT NULL,
              action         TEXT NOT NULL CHECK (action IN {ACTIONS}),
              analyst        TEXT NOT NULL,
              note           TEXT,
              sar_narrative  TEXT,
              created_at     TIMESTAMPTZ NOT NULL DEFAULT now())""")
        conn.execute(f"CREATE INDEX IF NOT EXISTS ix_disp_alert ON {APP_SCHEMA}.case_dispositions (alert_id)")
        conn.execute(f"ALTER TABLE {APP_SCHEMA}.case_dispositions REPLICA IDENTITY FULL")


app = FastAPI(title="Financial Crime Copilot")


@app.on_event("startup")
def _startup():
    init_schema()


LATEST = f"""
  LEFT JOIN LATERAL (
    SELECT action, analyst, created_at FROM {APP_SCHEMA}.case_dispositions d
    WHERE d.alert_id = q.alert_id ORDER BY created_at DESC LIMIT 1) d ON true"""


@app.get("/api/queue")
def queue(band: str | None = None, include_closed: bool = False, limit: int = 50):
    where, params = [], []
    if band:
        where.append("q.priority_band = %s"); params.append(band)
    if not include_closed:
        where.append("coalesce(d.action, '') NOT IN ('SAR_FILED', 'CLOSE_FALSE_POSITIVE')")
    sql = f"""
      SELECT q.alert_id, q.priority_rank, q.priority_band, q.priority_score, q.rule_code,
             q.alert_ts, q.trigger_amount, q.customer_name, q.kyc_risk_rating, q.is_pep,
             d.action AS status, d.analyst, d.created_at AS status_at
      FROM {SERVING_SCHEMA}.case_queue q {LATEST}
      {"WHERE " + " AND ".join(where) if where else ""}
      ORDER BY q.priority_rank LIMIT %s"""
    with db() as conn:
        return conn.execute(sql, (*params, limit)).fetchall()


@app.get("/api/case/{alert_id}")
def case(alert_id: str):
    with db() as conn:
        row = conn.execute(f"SELECT * FROM {SERVING_SCHEMA}.case_queue WHERE alert_id = %s",
                           (alert_id,)).fetchone()
        if not row:
            raise HTTPException(404, "alert not found")
        row["history"] = conn.execute(
            f"""SELECT action, analyst, note, created_at FROM {APP_SCHEMA}.case_dispositions
                WHERE alert_id = %s ORDER BY created_at""", (alert_id,)).fetchall()
        return row


class Disposition(BaseModel):
    action: str = Field(pattern="^(" + "|".join(ACTIONS) + ")$")
    note: str | None = None
    sar_narrative: str | None = None


@app.post("/api/case/{alert_id}/disposition")
def dispose(alert_id: str, d: Disposition, request: Request):
    # The analyst is the signed-in Databricks user, forwarded by the Apps proxy.
    analyst = request.headers.get("x-forwarded-email", "local-dev")
    with db() as conn:
        if not conn.execute(f"SELECT 1 FROM {SERVING_SCHEMA}.case_queue WHERE alert_id = %s",
                            (alert_id,)).fetchone():
            raise HTTPException(404, "alert not found")
        return conn.execute(
            f"""INSERT INTO {APP_SCHEMA}.case_dispositions (alert_id, action, analyst, note, sar_narrative)
                VALUES (%s, %s, %s, %s, %s) RETURNING disposition_id, alert_id, action, created_at""",
            (alert_id, d.action, analyst, d.note, d.sar_narrative)).fetchone()


@app.get("/api/kpis")
def kpis():
    with db() as conn:
        return conn.execute(f"""
          SELECT
            count(*)                                                       AS alerts_total,
            count(*) FILTER (WHERE q.priority_band = 'HIGH')               AS high_priority,
            count(*) FILTER (WHERE q.priority_band = 'HIGH' AND d.action IS NULL) AS high_untouched,
            count(*) FILTER (WHERE d.action = 'ESCALATE')                  AS escalated,
            count(*) FILTER (WHERE d.action = 'SAR_FILED')                 AS sar_filed,
            count(*) FILTER (WHERE d.action = 'CLOSE_FALSE_POSITIVE')      AS closed_false_positive
          FROM {SERVING_SCHEMA}.case_queue q {LATEST}""").fetchone()


class Question(BaseModel):
    question: str = Field(min_length=3)
    conversation_id: str | None = None


@app.post("/api/genie")
def genie(q: Question):
    if not GENIE_SPACE_ID:
        raise HTTPException(503, "Genie space not configured")
    if q.conversation_id:
        msg = w.genie.create_message_and_wait(GENIE_SPACE_ID, q.conversation_id, q.question)
    else:
        msg = w.genie.start_conversation_and_wait(GENIE_SPACE_ID, q.question)
    out = {"conversation_id": msg.conversation_id, "text": None, "sql": None, "columns": [], "rows": []}
    for att in msg.attachments or []:
        if att.text and att.text.content:
            out["text"] = att.text.content
        if att.query:
            out["sql"] = att.query.query
            out["text"] = out["text"] or att.query.description
            res = w.genie.get_message_attachment_query_result(
                GENIE_SPACE_ID, msg.conversation_id, msg.id, att.attachment_id)
            sr = res.statement_response
            if sr and sr.manifest and sr.result:
                out["columns"] = [c.name for c in sr.manifest.schema.columns]
                out["rows"] = (sr.result.data_array or [])[:50]
    return out


@app.get("/")
def index():
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "index.html"))


app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static")), name="static")
