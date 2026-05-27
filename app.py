import concurrent.futures
import json
import os
import re
import shutil
import threading
import time
import uuid
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request, send_file, send_from_directory

from extract import extract_catalogue

load_dotenv(override=True)

FRONTEND_FOLDER = Path("frontend")
app = Flask(
    __name__,
    template_folder=str(FRONTEND_FOLDER),
    static_folder=str(FRONTEND_FOLDER),
    static_url_path="/static",
)
UPLOAD_FOLDER = Path("uploads")
RESULTS_FOLDER = Path("resultats")
UPLOAD_FOLDER.mkdir(exist_ok=True)
RESULTS_FOLDER.mkdir(exist_ok=True)

ARCHIVED_PDF = "source.pdf"
META_FILE = "meta.json"
PRODUITS_FILE = "produits.json"
JOB_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

jobs: dict[str, dict] = {}
JOB_TIMEOUT_SECONDS = int(os.getenv("EXTRACTION_JOB_TIMEOUT_SECONDS", "600"))
JOB_EXECUTOR_MAX_WORKERS = int(os.getenv("JOB_EXECUTOR_MAX_WORKERS", "2"))
job_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=max(1, JOB_EXECUTOR_MAX_WORKERS)
)

try:
    _gemini_http_timeout = int(os.getenv("GEMINI_HTTP_TIMEOUT_SECONDS", "240"))
except ValueError:
    _gemini_http_timeout = 240
if JOB_TIMEOUT_SECONDS <= _gemini_http_timeout:
    print(
        f"[app] ATTENTION : EXTRACTION_JOB_TIMEOUT_SECONDS={JOB_TIMEOUT_SECONDS}s "
        f"<= GEMINI_HTTP_TIMEOUT_SECONDS={_gemini_http_timeout}s. "
        "Aucune retry Gemini ne pourra aboutir. "
        "Augmente EXTRACTION_JOB_TIMEOUT_SECONDS dans .env."
    )


def valid_job_id(job_id: str) -> bool:
    return bool(JOB_ID_RE.match(job_id))


def job_dir(job_id: str) -> Path:
    return RESULTS_FOLDER / job_id


def write_meta(job_id: str, data: dict) -> None:
    path = job_dir(job_id) / META_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: dict = {}
    if path.is_file():
        try:
            with open(path, encoding="utf-8") as f:
                existing = json.load(f)
        except (json.JSONDecodeError, OSError):
            existing = {}
    existing.update(data)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)


def read_meta(job_id: str) -> dict | None:
    path = job_dir(job_id) / META_FILE
    if not path.is_file():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def load_produits_from_disk(job_id: str) -> dict | None:
    path = job_dir(job_id) / PRODUITS_FILE
    if not path.is_file():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None
    produits = data.get("produits") or []
    meta = read_meta(job_id) or {}
    pdf_name = meta.get("pdf_name") or data.get("source") or "PDF"
    return {"produits": produits, "pdf_name": pdf_name}


def archived_pdf_path(job_id: str) -> Path | None:
    path = job_dir(job_id) / ARCHIVED_PDF
    return path if path.is_file() else None


def resolve_pdf_path(job_id: str) -> Path | None:
    archived = archived_pdf_path(job_id)
    if archived:
        return archived
    job = jobs.get(job_id)
    if job:
        path = Path(job.get("pdf_path", ""))
        if path.is_file():
            return path
    return None


def finalize_job(job_id: str, pdf_path: Path, pdf_name: str, produits: list) -> None:
    output_dir = job_dir(job_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    archived = output_dir / ARCHIVED_PDF
    try:
        shutil.copy2(pdf_path, archived)
    except OSError as exc:
        print(f"[app] Impossible d'archiver le PDF pour {job_id} : {exc}")
    write_meta(job_id, {
        "job_id": job_id,
        "pdf_name": pdf_name,
        "finished_at": time.time(),
        "produits_count": len(produits),
        "status": "done",
        "has_pdf": archived.is_file(),
    })


def list_history_items() -> list[dict]:
    items: list[dict] = []
    if not RESULTS_FOLDER.is_dir():
        return items

    for entry in RESULTS_FOLDER.iterdir():
        if not entry.is_dir() or not valid_job_id(entry.name):
            continue

        job_id = entry.name
        produits_path = entry / PRODUITS_FILE
        if not produits_path.is_file():
            continue

        meta = read_meta(job_id) or {}
        try:
            with open(produits_path, encoding="utf-8") as f:
                pdata = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue

        produits = pdata.get("produits") or []
        count = meta.get("produits_count")
        if not isinstance(count, int):
            count = pdata.get("total_produits")
        if not isinstance(count, int):
            count = len(produits)

        pdf_name = meta.get("pdf_name") or pdata.get("source") or "PDF"
        finished_at = meta.get("finished_at")
        if not isinstance(finished_at, (int, float)):
            finished_at = produits_path.stat().st_mtime

        items.append({
            "job_id": job_id,
            "pdf_name": pdf_name,
            "produits_count": count,
            "finished_at": finished_at,
            "has_pdf": (entry / ARCHIVED_PDF).is_file(),
        })

    items.sort(key=lambda item: item["finished_at"], reverse=True)
    return items


def run_job(job_id: str, pdf_path: Path, output_dir: Path, api_key: str) -> None:
    job = jobs.get(job_id, {})
    pdf_name = job.get("pdf_name", pdf_path.name)
    try:
        produits = extract_catalogue(str(pdf_path), api_key, str(output_dir))
        if jobs.get(job_id, {}).get("status") == "en cours":
            jobs[job_id]["status"] = "done"
            jobs[job_id]["produits"] = produits
            finalize_job(job_id, pdf_path, pdf_name, produits)
    except Exception as exc:
        if jobs.get(job_id):
            jobs[job_id]["status"] = "error"
            jobs[job_id]["error"] = str(exc)
            write_meta(job_id, {
                "status": "error",
                "error": str(exc),
                "finished_at": time.time(),
            })


def job_timeout(job_id: str) -> None:
    time.sleep(JOB_TIMEOUT_SECONDS)
    job = jobs.get(job_id)
    if not job or job.get("status") != "en cours":
        return

    started_at = job.get("started_at")
    if isinstance(started_at, (int, float)):
        elapsed = int(max(0, time.time() - started_at))
    else:
        elapsed = JOB_TIMEOUT_SECONDS

    future = job.get("_future")
    if future is not None:
        try:
            future.cancel()
        except Exception:
            pass

    job["status"] = "error"
    job["error"] = (
        f"Extraction interrompue après {elapsed}s (timeout du watchdog : {JOB_TIMEOUT_SECONDS}s). "
        "Pistes : augmente EXTRACTION_JOB_TIMEOUT_SECONDS et/ou GEMINI_HTTP_TIMEOUT_SECONDS dans .env, "
        "essaie un modèle plus rapide via GEMINI_MODEL (ex: gemini-2.0-flash), "
        "ou réduis la taille du PDF."
    )
    write_meta(job_id, {
        "status": "error",
        "error": job["error"],
        "finished_at": time.time(),
    })


def pdf_default() -> Path | None:
    pdfs = sorted(FRONTEND_FOLDER.glob("*.pdf"))
    return pdfs[0] if pdfs else None


def elapsed(job: dict) -> int | None:
    started_at = job.get("started_at")
    if isinstance(started_at, (int, float)):
        return int(max(0, time.time() - started_at))
    return None


def start_job(pdf_path: Path, pdf_display_name: str | None = None) -> tuple[str | None, dict | None]:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return None, {"error": "GEMINI_API_KEY manquante dans .env"}

    job_id = str(uuid.uuid4())
    output_dir = RESULTS_FOLDER / job_id
    pdf_name = pdf_display_name or pdf_path.name
    output_dir.mkdir(parents=True, exist_ok=True)

    jobs[job_id] = {
        "status": "en cours",
        "produits": [],
        "error": None,
        "pdf_path": str(pdf_path.resolve()),
        "pdf_name": pdf_name,
        "started_at": time.time(),
        "timeout_seconds": JOB_TIMEOUT_SECONDS,
    }
    write_meta(job_id, {
        "job_id": job_id,
        "pdf_name": pdf_name,
        "started_at": jobs[job_id]["started_at"],
        "status": "en cours",
    })

    future = job_executor.submit(run_job, job_id, pdf_path, output_dir, api_key)
    jobs[job_id]["_future"] = future
    threading.Thread(target=job_timeout, args=(job_id,), daemon=True).start()
    return job_id, None


@app.route("/")
def index():
    pdf = pdf_default()
    return render_template(
        "index.html",
        default_pdf_name=pdf.name if pdf else None,
    )


@app.route("/health")
def health():
    return jsonify({"status": "ok"}), 200


@app.route("/history")
def history():
    return jsonify({"items": list_history_items()})


@app.route("/history/delete/<job_id>", methods=["POST"])
def delete_history_item(job_id: str):
    if not valid_job_id(job_id):
        return jsonify({"error": "Job inconnu"}), 404

    job = jobs.get(job_id)
    if job and job.get("status") == "en cours":
        return jsonify({"error": "Extraction en cours : suppression impossible"}), 409

    job_directory = job_dir(job_id)
    if not job_directory.exists():
        jobs.pop(job_id, None)
        return jsonify({"error": "Job introuvable"}), 404

    # Best effort: if we have a future object and it's not running anymore, cancel it.
    if job and job.get("_future") is not None:
        try:
            job["_future"].cancel()
        except Exception:
            pass

    try:
        shutil.rmtree(job_directory)
    except OSError as exc:
        return jsonify({"error": f"Suppression impossible : {exc}"}), 500

    jobs.pop(job_id, None)
    return jsonify({"ok": True})


@app.route("/upload", methods=["POST"])
def upload():
    uploaded = request.files.get("pdf")
    if not uploaded:
        return jsonify({"error": "Pas de fichier"}), 400

    original_name = Path(uploaded.filename or "document.pdf").name
    pdf_path = UPLOAD_FOLDER / f"{uuid.uuid4()}.pdf"
    uploaded.save(pdf_path)

    job_id, error = start_job(pdf_path, pdf_display_name=original_name)
    if error:
        return jsonify(error), 400

    return jsonify({"job_id": job_id, "pdf_name": original_name})


@app.route("/use-default-pdf", methods=["POST"])
def use_default_pdf():
    pdf = pdf_default()
    if not pdf:
        return jsonify({"error": "Aucun PDF par defaut trouve dans /frontend"}), 404

    job_id, error = start_job(pdf)
    if error:
        return jsonify(error), 400

    return jsonify({"job_id": job_id, "pdf_name": pdf.name})


@app.route("/status/<job_id>")
def status(job_id):
    if not valid_job_id(job_id):
        return jsonify({"status": "inconnu"}), 404

    job = jobs.get(job_id)
    if job:
        return jsonify({
            "status": job.get("status", "inconnu"),
            "error": job.get("error"),
            "pdf_name": job.get("pdf_name"),
            "elapsed_seconds": elapsed(job),
            "timeout_seconds": job.get("timeout_seconds"),
            "produits_count": len(job.get("produits") or []),
        })

    disk = load_produits_from_disk(job_id)
    if disk:
        meta = read_meta(job_id) or {}
        return jsonify({
            "status": "done",
            "error": None,
            "pdf_name": disk["pdf_name"],
            "elapsed_seconds": None,
            "timeout_seconds": None,
            "produits_count": len(disk["produits"]),
        })

    return jsonify({"status": "inconnu"})


@app.route("/results/<job_id>")
def results(job_id):
    if not valid_job_id(job_id):
        return jsonify({"error": "Job inconnu"}), 404

    job = jobs.get(job_id)
    if job:
        if job.get("status") != "done":
            return jsonify({"error": "Résultats non disponibles"}), 409
        return jsonify({
            "produits": job.get("produits", []),
            "pdf_name": job.get("pdf_name"),
            "has_pdf": resolve_pdf_path(job_id) is not None,
        })

    disk = load_produits_from_disk(job_id)
    if not disk:
        return jsonify({"error": "Job inconnu"}), 404

    return jsonify({
        "produits": disk["produits"],
        "pdf_name": disk["pdf_name"],
        "has_pdf": resolve_pdf_path(job_id) is not None,
    })


@app.route("/images/<path:filename>")
def serve_image(filename):
    return send_from_directory(RESULTS_FOLDER, filename)


@app.route("/pdf/<job_id>")
def serve_pdf(job_id):
    if not valid_job_id(job_id):
        return jsonify({"error": "Job inconnu"}), 404

    pdf_path = resolve_pdf_path(job_id)
    if not pdf_path:
        return jsonify({"error": "PDF non disponible pour cette extraction"}), 404

    return send_file(pdf_path, mimetype="application/pdf")


if __name__ == "__main__":
    debug = os.getenv("FLASK_DEBUG", "true").strip().lower() in {"1", "true", "yes", "on"}
    app.run(host="0.0.0.0", port=5000, debug=debug)
