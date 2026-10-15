"""HTTP API and static UI."""
import json
import os
import re
from pathlib import Path

from fastapi import FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response, StreamingResponse
from pydantic import BaseModel

import threading

from . import agent
from . import figures
from . import pricing
from . import reports
from . import tools
from . import catalog
from . import guide
from . import openai_provider
from . import params as P
from . import simulations as S

WEB = Path(__file__).resolve().parent.parent / "web"
EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
MAX_UPLOAD = 200_000

app = FastAPI(title="Sublattice Agent")
S.recover_interrupted()
# newer summary fields for old runs, then the catalog, without delaying startup
def _startup_jobs():
    S.upgrade_summaries()
    catalog.backfill(S.RUNS, S.SWEEPS)


threading.Thread(target=_startup_jobs, daemon=True).start()


class ChatIn(BaseModel):
    messages: list
    model: str | None = None
    mode: str | None = None
    name: str | None = None
    defaults: str | None = None
    provider: str = "anthropic"
    fallbacks: list = []
    approve: bool = False    # the user pressed Run on a large request


class GuideIn(BaseModel):
    messages: list
    model: str | None = None
    name: str | None = None
    provider: str = "anthropic"


class ParamsIn(BaseModel):
    parameters_text: str
    label: str = ""


class ContinueIn(BaseModel):
    extra_t: float | None = None
    extra_parameters: str = ""
    label: str = ""


class CombineIn(BaseModel):
    run_id: str | None = None
    run_ids: list[str] = []
    label: str = ""


class MetaIn(BaseModel):
    starred: bool | None = None
    tags: list[str] | None = None
    label: str | None = None


class FigureIn(BaseModel):
    run_ids: list[str]
    quantities: list[str] = ["m1z", "m2z", "nz"]
    t0_ns: float | None = None
    t1_ns: float | None = None
    style: str = "science"
    format: str = "pdf"
    width_in: float | None = None
    height_in: float | None = None
    title: str = ""


class RunIdsIn(BaseModel):
    run_ids: list[str]


class ReportIn(BaseModel):
    run_ids: list[str]
    title: str = ""
    notes: str = ""
    author: str = ""


class SaveIn(BaseModel):
    name: str
    parameters_text: str


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


@app.get("/athena.png")
def athena():
    return FileResponse(WEB / "athena.png")


@app.get("/gods/{name}")
def god_logo(name: str):
    path = WEB / "gods" / name
    if not re.fullmatch(r"[a-z]+\.png", name) or not path.exists():
        raise HTTPException(404, "no logo")
    return FileResponse(path)


@app.get("/api/config")
def config():
    return {"model": agent.DEFAULT_MODEL, "models": agent.MODELS,
            "has_server_key": bool(os.environ.get("ANTHROPIC_API_KEY")),
            "has_openai_key": bool(os.environ.get("OPENAI_API_KEY")),
            "defaults": P.app_defaults(), "token_budget": agent.TOKEN_BUDGET,
            "tokens_used": agent.tokens_used(), "max_parallel": S.MAX_PARALLEL, "max_sweep_runs": S.MAX_SWEEP_RUNS,
            "catalog": catalog.status(), "eur_per_usd": pricing.EUR_PER_USD,
            "confirm": {"minutes": tools.CONFIRM_MINUTES, "runs": tools.CONFIRM_RUNS}}


@app.post("/api/guide")
def guide_answer(body: GuideIn, x_api_key: str | None = Header(default=None),
                 x_openai_api_key: str | None = Header(default=None)):
    try:
        name = re.sub(r"[^\w .'-]", "", body.name or "")[:40].strip()
        key = x_openai_api_key if body.provider == "openai" else x_api_key
        return {"text": guide.answer(body.messages, key, body.model, name, body.provider)}
    except agent.AgentError as e:
        return JSONResponse({"error": str(e)}, status_code=e.status)


@app.get("/api/models")
def models(x_api_key: str | None = Header(default=None),
           x_openai_api_key: str | None = Header(default=None)):
    claude, c_source, c_error = agent.list_models(x_api_key)
    openai, o_source, o_error = openai_provider.list_models(x_openai_api_key)
    return {"models": claude + openai,
            "sources": {"anthropic": c_source, "openai": o_source},
            "errors": {"anthropic": c_error, "openai": o_error},
            "defaults": {"anthropic": agent.DEFAULT_MODEL, "openai": openai_provider.DEFAULT_MODEL}}


@app.post("/api/chat")
def chat(body: ChatIn, x_api_key: str | None = Header(default=None),
         x_openai_api_key: str | None = Header(default=None)):
    """Streams provider-neutral agent events as newline-delimited JSON."""
    if body.provider not in ("anthropic", "openai"):
        raise HTTPException(400, f"unknown provider {body.provider}")
    if body.model and not re.fullmatch(r"[A-Za-z0-9._:-]{1,100}", body.model):
        raise HTTPException(400, f"invalid model name {body.model}")
    keys = {"anthropic": x_api_key or os.environ.get("ANTHROPIC_API_KEY"),
            "openai": x_openai_api_key or os.environ.get("OPENAI_API_KEY")}
    given = {"anthropic": x_api_key, "openai": x_openai_api_key}
    tries = [{"provider": body.provider, "model": body.model}]
    for c in body.fallbacks[:3]:
        if (isinstance(c, dict) and c.get("provider") in keys and isinstance(c.get("model"), str)
                and re.fullmatch(r"[A-Za-z0-9._:-]{1,100}", c["model"])):
            tries.append({"provider": c["provider"], "model": c["model"]})
    tries = [t for t in tries if keys[t["provider"]]]
    if not tries:
        env_name = "OPENAI_API_KEY" if body.provider == "openai" else "ANTHROPIC_API_KEY"
        return JSONResponse({"error": f"No {body.provider} API key. Add one in Settings or set {env_name}."},
                            status_code=401)
    retry = {400, 401, 402, 403, 404, 429, 502}

    def stream():
        try:
            for i, t in enumerate(tries):
                events = agent.chat_stream(body.messages, api_key=given[t["provider"]], model=t["model"], mode=body.mode,
                                           name=re.sub(r"[^\w .'-]", "", body.name or "")[:40].strip(),
                                           defaults_text=(body.defaults or "")[:2000], provider=t["provider"],
                                           approved=body.approve)
                first = next(events, None)
                if first and first.get("type") == "error" and first.get("status") in retry and i < len(tries) - 1:
                    continue
                yield json.dumps({"type": "model", "provider": t["provider"], "model": t["model"]}) + "\n"
                if first:
                    yield json.dumps(first, default=str) + "\n"
                for ev in events:
                    yield json.dumps(ev, default=str) + "\n"
                return
        except Exception as e:  # keep the stream well-formed for the page
            yield json.dumps({"type": "error", "status": 500, "error": f"Unexpected error: {e}"}) + "\n"

    return StreamingResponse(stream(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---- parameter files

@app.post("/api/files")
async def upload(file: UploadFile = File(...)):
    raw = await file.read()
    if len(raw) > MAX_UPLOAD:
        raise HTTPException(413, "file larger than 200 kB")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(400, "the file is not UTF-8 text")
    name = S.save_input(file.filename or "parameters.txt", text)
    return {"name": name, "text": text, "check": P.validate(text, S.sec_per_step())}


@app.post("/api/files/save")
def save(body: SaveIn):
    return {"name": S.save_input(body.name, body.parameters_text)}


@app.get("/api/files")
def files():
    return {"files": S.list_inputs()}


@app.get("/api/files/{name}")
def get_file(name: str):
    text = S.read_input(name)
    if text is None:
        raise HTTPException(404, "file not found")
    return PlainTextResponse(text, headers={"Content-Disposition": f'attachment; filename="{S.safe_name(name)}"'})


@app.get("/api/examples")
def examples():
    return {"examples": [{"name": p.name, "text": p.read_text()} for p in sorted(EXAMPLES.glob("*.txt"))]}


@app.post("/api/validate")
def validate(body: ParamsIn):
    out = P.validate(body.parameters_text, S.sec_per_step())
    out.pop("cleaned_text", None)
    return out


# ---- runs and sweeps

@app.post("/api/runs")
def start(body: ParamsIn):
    out = S.start_run(body.parameters_text, body.label)
    if not out["ok"]:
        raise HTTPException(400, out["error"])
    return out


@app.post("/api/runs/combine")
def combine(body: CombineIn):
    out = S.combine_runs(body.run_ids, body.run_id, body.label)
    if not out["ok"]:
        raise HTTPException(400, out["error"])
    return out


@app.post("/api/runs/{run_id}/continue")
def continue_run(run_id: str, body: ContinueIn):
    out = S.continue_run(run_id, body.extra_t, body.extra_parameters, body.label)
    if not out["ok"]:
        raise HTTPException(400, out["error"])
    return out


@app.get("/api/runs")
def runs(limit: int = 50):
    return {"runs": S.list_runs(max(1, min(limit, 5000))), "sweeps": S.list_sweeps()}


@app.post("/api/runs/{run_id}/meta")
def run_meta(run_id: str, body: MetaIn):
    out = S.update_meta(run_id, body.starred, body.tags, body.label)
    if out is None:
        raise HTTPException(404, "run not found")
    return out


@app.get("/api/compare")
def compare(ids: str):
    out = S.compare([i.strip() for i in ids.split(",") if i.strip()])
    if not out["ok"]:
        raise HTTPException(400, out["error"])
    return out


@app.post("/api/figure")
def figure(body: FigureIn):
    try:
        data, mime, name = figures.render(body.run_ids, body.quantities, body.t0_ns, body.t1_ns, body.style,
                                          body.format, body.width_in, body.height_in, body.title[:120])
    except ValueError as e:
        raise HTTPException(400, str(e))
    return Response(data, media_type=mime, headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.post("/api/figures")
def save_figure(body: FigureIn):
    out = figures.save(body.run_ids, body.quantities, body.t0_ns, body.t1_ns, body.style,
                       body.width_in, body.height_in, body.title[:120])
    if not out["ok"]:
        raise HTTPException(400, out["error"])
    return out


@app.get("/api/figures/{name}")
def figure_file(name: str):
    path = figures.figure_file(name)
    if not path:
        raise HTTPException(404, "figure not found")
    meta = json.loads((path.parent / "figure.json").read_text())
    ext = name.rsplit(".", 1)[1]
    if ext in ("png", "json"):
        return FileResponse(path)
    return FileResponse(path, filename=f"{meta.get('file_stem') or name.split('.')[0]}.{ext}")


@app.post("/api/runs/delete")
def delete_runs(body: RunIdsIn):
    return S.delete_runs(body.run_ids[:2000])


@app.post("/api/reports")
def make_report(body: ReportIn):
    out = reports.build(body.run_ids, body.title, body.notes[:20000], re.sub(r"[^\w .'-]", "", body.author)[:40])
    if not out["ok"]:
        raise HTTPException(400, out["error"])
    return out


@app.get("/api/reports/{name}")
def report_file(name: str):
    path = reports.report_file(name)
    if not path:
        raise HTTPException(404, "report not found")
    if name.endswith(".html"):
        return FileResponse(path, media_type="text/html")
    if name.endswith(".json"):
        return FileResponse(path)
    return FileResponse(path, filename=name)


@app.get("/api/runs/{run_id}")
def run(run_id: str):
    info = S.run_info(run_id, full=True)
    if not info:
        raise HTTPException(404, "run not found")
    return info


@app.post("/api/runs/{run_id}/cancel")
def cancel(run_id: str):
    return S.cancel(run_id)


@app.get("/api/runs/{run_id}/series")
def run_series(run_id: str, points: int = 1200, t0_ns: float | None = None, t1_ns: float | None = None):
    out = S.run_series(run_id, points, t0_ns, t1_ns)
    if out is None:
        raise HTTPException(404, "no trajectory for this run")
    return out


@app.get("/api/runs/{run_id}/export.csv")
def run_export_csv(run_id: str):
    text = S.export_csv(run_id)
    if text is None:
        raise HTTPException(404, "no trajectory for this run")
    return Response(text, media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{run_id}_trajectory.csv"'})


@app.get("/api/runs/{run_id}/export.zip")
def run_export_zip(run_id: str):
    data = S.export_zip(run_id)
    if data is None:
        raise HTTPException(404, "no trajectory for this run")
    return Response(data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{run_id}_data.zip"'})


@app.get("/api/runs/{run_id}/files/{name}")
def run_file(run_id: str, name: str):
    path = S.run_file(run_id, name)
    if not path:
        raise HTTPException(404, "file not found")
    return FileResponse(path, filename=f"{run_id}_{name}")


@app.get("/api/sweeps/{sweep_id}")
def sweep(sweep_id: str, discard_fraction: float = 0.0):
    info = S.sweep_info(sweep_id, discard_fraction)
    if not info:
        raise HTTPException(404, "sweep not found")
    return info


@app.get("/api/sweeps/{sweep_id}/plot")
def sweep_plot(sweep_id: str):
    path = S.sweep_file(sweep_id, "sweep_neel_z.png")
    if not path:
        raise HTTPException(404, "plot not ready")
    return FileResponse(path)
