"""Generates infra/grafana/dashboards/*.json.

Usage: python3 infra/grafana/gen_dashboards.py infra/grafana/dashboards"""
import json
import sys

OUT = sys.argv[1]
PROM = {"type": "prometheus", "uid": "prometheus"}
INF = {"type": "yesoreyeram-infinity-datasource", "uid": "infinity"}
RB = "https://github.com/soumadipcodeEnthusiast/ai-driven-event-processing-system/blob/main/docs/"

_id = [0]


def nid():
    _id[0] += 1
    return _id[0]


def prom(expr, legend="", instant=False, ref="A", fmt=None):
    t = {"refId": ref, "datasource": PROM, "expr": expr, "legendFormat": legend or "__auto",
         "range": not instant, "instant": instant}
    if fmt:
        t["format"] = fmt
    return t


def thresholds(*steps):
    """steps: (color, value) with first value None."""
    return {"mode": "absolute", "steps": [{"color": c, "value": v} for c, v in steps]}


def panel(ptype, title, x, y, w, h, targets, desc="", unit=None, defaults=None, options=None,
          overrides=None, ds=PROM):
    d = {"color": {"mode": "palette-classic"}, "mappings": []}
    if unit:
        d["unit"] = unit
    if defaults:
        d.update(defaults)
    return {"id": nid(), "type": ptype, "title": title, "description": desc, "datasource": ds,
            "gridPos": {"x": x, "y": y, "w": w, "h": h}, "targets": targets,
            "fieldConfig": {"defaults": d, "overrides": overrides or []},
            "options": options or {}}


def ts(title, x, y, w, h, targets, unit="short", desc="", thr=None, overrides=None, stack=False):
    d = {"custom": {"drawStyle": "line", "lineWidth": 1, "fillOpacity": 10, "showPoints": "never",
                    "spanNulls": True,
                    "stacking": {"mode": "normal" if stack else "none", "group": "A"},
                    "thresholdsStyle": {"mode": "line+area" if thr else "off"}}}
    if thr:
        d["thresholds"] = thr
    return panel("timeseries", title, x, y, w, h, targets, desc, unit, d,
                 {"legend": {"displayMode": "table", "placement": "bottom", "calcs": ["lastNotNull", "max"]},
                  "tooltip": {"mode": "multi", "sort": "desc"}}, overrides)


def stat(title, x, y, w, h, targets, unit="short", desc="", thr=None, mappings=None, color_mode="background",
         calcs=("lastNotNull",), fields="", text_mode="auto", ds=PROM, overrides=None):
    d = {"color": {"mode": "thresholds"},
         "thresholds": thr or thresholds(("green", None))}
    if mappings:
        d["mappings"] = mappings
    return panel("stat", title, x, y, w, h, targets, desc, unit, d,
                 {"reduceOptions": {"calcs": list(calcs), "fields": fields, "values": False},
                  "colorMode": color_mode, "graphMode": "none", "justifyMode": "auto",
                  "textMode": text_mode, "orientation": "auto"}, overrides, ds=ds)


def row(title, y):
    return {"id": nid(), "type": "row", "title": title, "collapsed": False,
            "gridPos": {"x": 0, "y": y, "w": 24, "h": 1}, "panels": []}


def dashboard(uid, title, panels, refresh, tags, variables=None, time_from="now-1h", links=None, desc=""):
    return {"uid": uid, "title": title, "description": desc, "tags": tags, "timezone": "utc",
            "editable": False, "graphTooltip": 1, "schemaVersion": 39, "version": 1,
            "refresh": refresh, "time": {"from": time_from, "to": "now"},
            "timepicker": {"refresh_intervals": ["5s", "10s", "30s", "1m", "5m"]},
            "templating": {"list": variables or []}, "annotations": {"list": [{
                "builtIn": 1, "datasource": {"type": "grafana", "uid": "-- Grafana --"},
                "enable": True, "hide": True, "iconColor": "rgba(0, 211, 255, 1)",
                "name": "Annotations & Alerts", "type": "dashboard"}]},
            "links": links or [], "panels": panels, "fiscalYearStartMonth": 0, "liveNow": False,
            "weekStart": ""}


def nav_links():
    return [
        {"title": "AIOps Overview", "type": "link", "url": "/d/aiops-overview", "icon": "dashboard"},
        {"title": "Service Health", "type": "link", "url": "/d/aiops-service-health", "icon": "dashboard"},
        {"title": "SLOs", "type": "link", "url": "/d/aiops-slo", "icon": "dashboard"},
        {"title": "Runbooks", "type": "link", "url": RB + "runbooks/README.md", "icon": "doc",
         "targetBlank": True},
    ]


SEV_MAP = [{"type": "value", "options": {
    "low": {"color": "blue", "index": 0, "text": "low"},
    "medium": {"color": "yellow", "index": 1, "text": "medium"},
    "high": {"color": "orange", "index": 2, "text": "high"},
    "critical": {"color": "red", "index": 3, "text": "critical"}}}]
STATUS_MAP = [{"type": "value", "options": {
    "open": {"color": "red", "index": 0, "text": "open"},
    "acknowledged": {"color": "yellow", "index": 1, "text": "acknowledged"},
    "resolved": {"color": "green", "index": 2, "text": "resolved"}}}]
OUTCOME_COLORS = {"ok": "green", "valid": "green", "fallback": "orange", "invalid": "yellow", "error": "red"}
SEV_COLORS = {"low": "blue", "medium": "yellow", "high": "orange", "critical": "red"}


def color_by_name(colors):
    return [{"matcher": {"id": "byName", "options": k},
             "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": v}}]}
            for k, v in colors.items()]


def inf(url, columns, root="", ref="A"):
    return {"refId": ref, "datasource": INF, "type": "json", "source": "url", "parser": "backend",
            "format": "table", "url": url, "url_options": {"method": "GET", "data": ""},
            "root_selector": root, "filters": [],
            "columns": [{"selector": s, "text": s, "type": t} for s, t in columns]}


ALERT_COLS = [("alert_id", "string"), ("component_id", "string"), ("metric_name", "string"),
              ("severity", "string"), ("status", "string"), ("predicted_breach_time", "timestamp"),
              ("lead_time_minutes", "number"), ("predicted_value", "number"), ("threshold", "number"),
              ("created_at", "timestamp"), ("model_version", "string"), ("playbook_id", "string")]
PLAYBOOK_COLS = [("playbook_id", "string"), ("alert_id", "string"), ("component_id", "string"),
                 ("summary", "string"), ("root_cause_hypothesis", "string"), ("generated_by", "string"),
                 ("generated_at", "timestamp")]


def playbook_links(field="playbook_id"):
    return [
        {"title": "Open playbook in this dashboard",
         "url": "/d/aiops-overview/aiops-overview?var-playbook_id=${__data.fields.%s}"
                "&var-alert_status=${alert_status}&${__url_time_range}" % field},
        {"title": "Open playbook (Markdown, diagnostic-service)",
         "url": "http://localhost:8082/playbook/${__data.fields.%s}?format=markdown" % field,
         "targetBlank": True},
    ]


def table(title, x, y, w, h, targets, desc="", overrides=None, links=None, ds=INF, sort=None):
    d = {"custom": {"align": "auto", "cellOptions": {"type": "auto"}, "inspect": True,
                    "filterable": True},
         "thresholds": thresholds(("green", None))}
    if links:
        d["links"] = links
    opts = {"showHeader": True, "cellHeight": "sm", "footer": {"show": False, "reducer": ["sum"],
                                                              "fields": ""}}
    if sort:
        opts["sortBy"] = sort
    return panel("table", title, x, y, w, h, targets, desc, None, d, opts, overrides, ds=ds)


def col_color(name, mappings, width=None):
    props = [{"id": "mappings", "value": mappings},
             {"id": "custom.cellOptions", "value": {"type": "color-background", "mode": "basic"}}]
    if width:
        props.append({"id": "custom.width", "value": width})
    return {"matcher": {"id": "byName", "options": name}, "properties": props}


def col_width(name, width):
    return {"matcher": {"id": "byName", "options": name},
            "properties": [{"id": "custom.width", "value": width}]}


# ════════════════════════════ aiops-overview (REQ-K) ════════════════════════════
_id[0] = 0
P = []
P.append(stat("Open predictive alerts", 0, 0, 4, 4, [prom("sum(predictive_alerts_open) or vector(0)", instant=True)],
              desc="Gauge predictive_alerts_open from forecasting-service.",
              thr=thresholds(("green", None), ("orange", 1), ("red", 5))))
P.append(stat("Alerts emitted (24h) by severity", 4, 0, 6, 4,
              [prom("sum by (severity) (increase(predictive_alerts_total[24h]))", "{{severity}}", instant=True)],
              desc="predictive_alerts_total{severity} increase over 24h.", color_mode="value",
              overrides=color_by_name(SEV_COLORS)))
P.append(stat("Diagnoses (24h) by outcome", 10, 0, 5, 4,
              [prom("sum by (outcome) (increase(diagnoses_total[24h]))", "{{outcome}}", instant=True)],
              desc="ok = LLM playbook, fallback = rule-based playbook, error = no playbook.",
              color_mode="value", overrides=color_by_name(OUTCOME_COLORS)))
P.append(stat("Diagnostic fallback ratio (30m)", 15, 0, 3, 4,
              [prom("service:diagnoses_fallback:ratio_rate30m", instant=True)], unit="percentunit",
              desc="Share of playbooks generated by the rule-based fallback (LLM degraded or LLM_PROVIDER=none).",
              thr=thresholds(("green", None), ("orange", 0.25), ("red", 0.5))))
P.append(stat("Time to next predicted breach", 18, 0, 3, 4,
              [inf("http://forecasting-service:8081/alerts/summary",
                   [("min_minutes_to_breach", "number"), ("next_breach_component", "string")])],
              unit="m", desc="Minutes until the soonest predicted breach among open/acknowledged alerts, "
                             "computed live by GET /alerts/summary (negative = predicted time has passed). "
                             "Red below 10 min: act now (REQ-E target lead time). Empty = no active alerts.",
              calcs=("min",), fields="/^min_minutes_to_breach$/", ds=INF,
              thr=thresholds(("red", None), ("orange", 10), ("green", 30))))
P.append(stat("Forecaster", 21, 0, 3, 4,
              [prom("max by (forecaster, model_version) (forecaster_info)", "{{forecaster}} {{model_version}}",
                    instant=True)],
              desc="forecaster_info: TFT or statistical fallback, and model version.", text_mode="name",
              color_mode="none"))
P.append(table(
    "Live predictive alerts (refresh 5 s) — click a row to open its playbook", 0, 4, 24, 10,
    [inf("http://forecasting-service:8081/alerts?status=${alert_status}&limit=100", ALERT_COLS)],
    desc="REQ-K live feed: GET forecasting-service /alerts (newest first). Dashboard refresh is 5 s, so a new "
         "PredictiveAlert appears within 5 s of emission. Each cell links to the alert's IncidentPlaybook.",
    links=playbook_links(),
    overrides=[col_color("severity", SEV_MAP, 90), col_color("status", STATUS_MAP, 110),
               col_width("alert_id", 150), col_width("lead_time_minutes", 140),
               {"matcher": {"id": "byName", "options": "lead_time_minutes"},
                "properties": [{"id": "unit", "value": "m"}, {"id": "thresholds", "value": thresholds(
                    ("red", None), ("green", 10))}, {"id": "custom.cellOptions",
                                                    "value": {"type": "color-text"}}]}],
    sort=[{"displayName": "created_at", "desc": True}]))
P.append(table(
    "Incident playbooks", 0, 14, 24, 8,
    [inf("http://diagnostic-service:8082/playbooks", PLAYBOOK_COLS)],
    desc="GET diagnostic-service /playbooks. generated_by = '<provider>:<model>' or 'fallback:rule-based'.",
    links=playbook_links(), overrides=[col_width("playbook_id", 260), col_width("alert_id", 150),
                                       col_width("generated_by", 200)],
    sort=[{"displayName": "generated_at", "desc": True}]))
P.append(row("Selected playbook: ${playbook_id}", 22))
P.append(table(
    "Playbook ${playbook_id}", 0, 23, 24, 5,
    [inf("http://diagnostic-service:8082/playbook/${playbook_id}",
         [("playbook_id", "string"), ("alert_id", "string"), ("component_id", "string"), ("summary", "string"),
          ("root_cause_hypothesis", "string"), ("generated_by", "string"), ("generated_at", "timestamp")])],
    desc="GET diagnostic-service /playbook/{id}. Select a playbook by clicking a row in the tables above "
         "(or type an id into the playbook_id variable).",
    links=[{"title": "Open as Markdown",
            "url": "http://localhost:8082/playbook/${playbook_id}?format=markdown", "targetBlank": True}]))
P.append(table(
    "Ranked remediation steps (REQ-H: >= 3)", 0, 28, 24, 9,
    [inf("http://diagnostic-service:8082/playbook/${playbook_id}",
         [("rank", "number"), ("title", "string"), ("command", "string"), ("expected_outcome", "string")],
         root="steps")],
    desc="IncidentPlaybook.steps ordered by rank.", overrides=[col_width("rank", 60)],
    sort=[{"displayName": "rank", "desc": False}]))

overview_vars = [
    {"type": "custom", "name": "alert_status", "label": "Alert status", "query": "open,acknowledged,resolved",
     "current": {"selected": True, "text": "open", "value": "open"},
     "options": [{"selected": s == "open", "text": s, "value": s} for s in ("open", "acknowledged", "resolved")],
     "multi": False, "includeAll": False, "hide": 0},
    {"type": "textbox", "name": "playbook_id", "label": "Playbook", "query": "",
     "current": {"selected": False, "text": "", "value": ""}, "options": [], "hide": 0},
]
overview = dashboard("aiops-overview", "AIOps Overview — Predictive Alerts & Playbooks", P, "5s",
                     ["aiops", "req-k"], overview_vars, "now-6h", nav_links(),
                     "REQ-K: live PredictiveAlert feed with links to IncidentPlaybooks.")

# ════════════════════════════ service-health ════════════════════════════
_id[0] = 0
P = []
UP_MAP = [{"type": "value", "options": {"0": {"color": "red", "index": 0, "text": "DOWN"},
                                        "1": {"color": "green", "index": 1, "text": "UP"}}},
          {"type": "special", "options": {"match": "null", "result": {"color": "red", "index": 2,
                                                                        "text": "NO DATA"}}}]
P.append(row("Availability", 0))
P.append(stat("Service up", 0, 1, 14, 4,
              [prom('max by (service) (up{job=~"ingestion-service|forecasting-service|diagnostic-service|prometheus|grafana"})',
                    "{{service}}", instant=True)],
              mappings=UP_MAP, thr=thresholds(("red", None), ("green", 1)),
              desc="Prometheus scrape health per service (up)."))
P.append(table("Firing Prometheus alerts", 14, 1, 10, 4,
               [prom('ALERTS{alertstate="firing"}', instant=True, fmt="table")], ds=PROM,
               desc="Alert rules from infra/prometheus/rules. Runbooks: docs/runbooks/.",
               overrides=[col_color("severity", [{"type": "value", "options": {
                   "critical": {"color": "red", "index": 0}, "warning": {"color": "orange", "index": 1},
                   "info": {"color": "blue", "index": 2}}}])]
               + [{"matcher": {"id": "byName", "options": n},
                   "properties": [{"id": "custom.hidden", "value": True}]}
                  for n in ("Time", "Value", "__name__", "alertstate", "environment", "project")]))

P.append(row("Ingestion service (REQ-A / REQ-B)", 5))
P.append(ts("Ingestion throughput by outcome", 0, 6, 8, 8,
            [prom("outcome:ingestion_events:rate1m", "{{outcome}}")], unit="ops",
            desc="ingestion_events_total rate. invalid = routed to raw-events.DLT; error = unexpected failure.",
            overrides=color_by_name(OUTCOME_COLORS), stack=True))
P.append(ts("Kafka consumer lag (records, max)", 8, 6, 8, 8,
            [prom("service:kafka_consumer_records_lag:max", "max lag")], unit="short",
            desc="kafka_consumer_fetch_manager_records_lag_max. REQ-A: 5000 records = 500 ms at 10k ev/s.",
            thr=thresholds(("transparent", None), ("orange", 1000), ("red", 5000))))
P.append(ts("Processing latency p95 / p99", 16, 6, 8, 8,
            [prom("service:ingestion_processing_seconds:p95_5m", "p95"),
             prom("service:ingestion_processing_seconds:p99_5m", "p99", ref="B")], unit="s",
            desc="ingestion_processing_seconds histogram. REQ-A budget 500 ms.",
            thr=thresholds(("transparent", None), ("red", 0.5))))
P.append(ts("HTTP requests (actuator) by status", 0, 14, 8, 7,
            [prom('sum by (status) (rate(http_server_requests_seconds_count{service="ingestion-service"}[5m]))',
                  "{{status}}")], unit="reqps"))
P.append(ts("HTTP mean latency by uri", 8, 14, 8, 7,
            [prom('sum by (uri) (rate(http_server_requests_seconds_sum{service="ingestion-service"}[5m])) / '
                  'sum by (uri) (rate(http_server_requests_seconds_count{service="ingestion-service"}[5m]))',
                  "{{uri}}")], unit="s"))
P.append(ts("JVM heap / non-heap", 16, 14, 8, 7,
            [prom('sum by (area) (jvm_memory_used_bytes{service="ingestion-service"})', "used {{area}}"),
             prom('sum by (area) (jvm_memory_max_bytes{service="ingestion-service", area="heap"} > 0)',
                  "max {{area}}", ref="B")], unit="bytes"))

P.append(row("Forecasting service (REQ-D / REQ-E)", 21))
P.append(ts("Forecast runs by component / outcome", 0, 22, 8, 8,
            [prom("component_outcome:forecast_runs:rate5m", "{{component}} {{outcome}}")], unit="ops",
            desc="forecast_runs_total rate."))
P.append(ts("Forecast latency p95", 8, 22, 8, 8,
            [prom("service:forecast_latency_seconds:p95_5m", "p95")], unit="s",
            desc="forecast_latency_seconds. REQ-D: < 2 s for a 60-min window.",
            thr=thresholds(("transparent", None), ("red", 2))))
P.append(ts("Predictive alerts emitted by severity", 16, 22, 8, 8,
            [prom("sum by (severity) (increase(predictive_alerts_total[5m]))", "{{severity}}"),
             prom("sum(predictive_alerts_open)", "open (gauge)", ref="B")], unit="short",
            overrides=color_by_name(SEV_COLORS)))

P.append(row("Diagnostic service (REQ-G / REQ-H / REQ-I)", 30))
P.append(ts("Diagnoses by outcome", 0, 31, 6, 8,
            [prom("sum by (outcome) (increase(diagnoses_total[5m]))", "{{outcome}}")], unit="short",
            overrides=color_by_name(OUTCOME_COLORS), stack=True))
P.append(ts("Diagnosis latency p95", 6, 31, 6, 8,
            [prom("service:diagnosis_latency_seconds:p95_5m", "diagnosis p95")], unit="s",
            thr=thresholds(("transparent", None), ("red", 30))))
P.append(ts("LLM latency p95 / failures", 12, 31, 6, 8,
            [prom("provider:llm_request_seconds:p95_5m", "p95 {{provider}}"),
             prom("sum by (provider) (increase(llm_failures_total[5m]))", "failures {{provider}}", ref="B")],
            unit="s", overrides=[{"matcher": {"id": "byFrameRefID", "options": "B"},
                                  "properties": [{"id": "unit", "value": "short"},
                                                 {"id": "custom.axisPlacement", "value": "right"},
                                                 {"id": "color", "value": {"mode": "fixed", "fixedColor": "red"}}]}]))
P.append(ts("Graph context latency p95 (REQ-G)", 18, 31, 6, 8,
            [prom("service:graph_context_seconds:p95_5m", "p95")], unit="s",
            thr=thresholds(("transparent", None), ("red", 1))))
P.append(ts("Playbooks stored", 0, 39, 8, 6,
            [prom("sum(increase(playbooks_stored_total[5m]))", "stored / 5m")], unit="short"))

P.append(row("Process resources", 45))
P.append(ts("Resident memory", 0, 46, 12, 7,
            [prom('sum by (service) (process_resident_memory_bytes{service=~".+-service"})', "{{service}}")],
            unit="bytes"))
P.append(ts("CPU", 12, 46, 12, 7,
            [prom('sum by (service) (rate(process_cpu_seconds_total{service=~".+-service"}[5m]))', "{{service}}"),
             prom('max by (service) (process_cpu_usage{service="ingestion-service"})', "{{service}}", ref="B")],
            unit="percentunit", desc="Python: rate(process_cpu_seconds_total); JVM: process_cpu_usage."))
health = dashboard("aiops-service-health", "AIOps Service Health", P, "30s", ["aiops", "health"],
                   time_from="now-1h", links=nav_links(),
                   desc="Golden signals for ingestion, forecasting and diagnostic services.")

# ════════════════════════════ slo ════════════════════════════
_id[0] = 0
P = []
P.append({"id": nid(), "type": "text", "title": "About these SLOs", "gridPos": {"x": 0, "y": 0, "w": 24, "h": 3},
          "options": {"mode": "markdown", "content":
                      "SLOs are defined in [docs/SLO.md](" + RB + "SLO.md) and computed by recording rules in "
                      "`infra/prometheus/rules/slo-recording.yml`. Window: rolling 30 days. "
                      "**Error budget remaining < 0** means the SLO is breached: freeze risky changes "
                      "and prioritise reliability work (see burn-rate policy)."}})
P.append(panel("bargauge", "Error budget remaining (30d)", 0, 3, 12, 9,
               [prom("slo:error_budget:remaining_ratio", "{{slo}}", instant=True)],
               "1 - (error ratio over 30d / (1 - objective)).", "percentunit",
               {"color": {"mode": "thresholds"}, "min": -1, "max": 1,
                "thresholds": thresholds(("red", None), ("orange", 0), ("yellow", 0.25), ("green", 0.5))},
               {"displayMode": "lcd", "orientation": "horizontal", "showUnfilled": True,
                "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}}))
P.append(table("SLO compliance (30d)", 12, 3, 12, 9,
               [prom("1 - slo:sli_error:ratio_rate30d", instant=True, fmt="table", ref="A"),
                prom("slo:objective:ratio", instant=True, fmt="table", ref="B")],
               ds=PROM, desc="SLI = good / total over 30 days vs objective.",
               overrides=[{"matcher": {"id": "byRegexp", "options": "Value.*"},
                           "properties": [{"id": "unit", "value": "percentunit"},
                                          {"id": "decimals", "value": 3}]}]))
P[-1]["transformations"] = [
    {"id": "merge", "options": {}},
    {"id": "organize", "options": {"excludeByName": {"Time": True},
                                   "renameByName": {"Value #A": "SLI (30d)", "Value #B": "Objective"}}}]
P[-1]["fieldConfig"]["overrides"] = [
    {"matcher": {"id": "byRegexp", "options": "SLI.*|Objective"},
     "properties": [{"id": "unit", "value": "percentunit"}, {"id": "decimals", "value": 3}]}]
burn_thr = thresholds(("transparent", None), ("orange", 6), ("red", 14.4))
P.append(ts("Burn rate (1h window)", 0, 12, 12, 8, [prom("slo:burn_rate:ratio_rate1h", "{{slo}}")],
            unit="x", desc="1 = consuming budget exactly at the sustainable pace. Page at 14.4x (1h) / 6x (6h).",
            thr=burn_thr))
P.append(ts("Burn rate (6h window)", 12, 12, 12, 8, [prom("slo:burn_rate:ratio_rate6h", "{{slo}}")],
            unit="x", thr=burn_thr))
P.append(ts("Burn rate (3d window)", 0, 20, 12, 8, [prom("slo:burn_rate:ratio_rate3d", "{{slo}}")],
            unit="x", desc="Ticket when > 1x over 3d (and 6h).",
            thr=thresholds(("transparent", None), ("orange", 1))))
P.append(ts("Service availability (scrape up, 1h avg)", 12, 20, 12, 8,
            [prom("service:up:avg_over_time1h", "{{service}}")], unit="percentunit",
            desc="Availability SLO 99.5% / 30d per application service.",
            thr=thresholds(("transparent", None), ("red", 0.995))))
P[-1]["fieldConfig"]["defaults"]["min"] = 0.9
P[-1]["fieldConfig"]["defaults"]["max"] = 1
P.append(table("SLO burn alerts firing", 0, 28, 24, 6,
               [prom('ALERTS{alertstate="firing", slo!=""}', instant=True, fmt="table")], ds=PROM,
               overrides=[{"matcher": {"id": "byName", "options": n},
                           "properties": [{"id": "custom.hidden", "value": True}]}
                          for n in ("Time", "Value", "__name__", "alertstate", "environment", "project")]))
slo = dashboard("aiops-slo", "AIOps SLOs & Error Budgets", P, "1m", ["aiops", "slo"], time_from="now-24h",
                links=nav_links(), desc="SLO compliance, error budget and burn rates.")

for name, d in (("aiops-overview", overview), ("service-health", health), ("slo", slo)):
    with open(f"{OUT}/{name}.json", "w") as f:
        json.dump(d, f, indent=2, ensure_ascii=False)
        f.write("\n")
print("ok")
