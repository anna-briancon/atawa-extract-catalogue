import concurrent.futures
import os
import threading
import time
import uuid
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request, send_file, send_from_directory

from extract import extract_catalogue

load_dotenv(override=True)

app = Flask(__name__)
UPLOAD_FOLDER = Path("uploads")
RESULTS_FOLDER = Path("resultats")
STATIC_FOLDER = Path("static")
UPLOAD_FOLDER.mkdir(exist_ok=True)
RESULTS_FOLDER.mkdir(exist_ok=True)

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


def run_job(job_id: str, pdf_path: Path, output_dir: Path, api_key: str) -> None:
    try:
        produits = extract_catalogue(str(pdf_path), api_key, str(output_dir))
        if jobs.get(job_id, {}).get("status") == "en cours":
            jobs[job_id]["status"] = "done"
            jobs[job_id]["produits"] = produits
    except Exception as exc:
        if jobs.get(job_id):
            jobs[job_id]["status"] = "error"
            jobs[job_id]["error"] = str(exc)


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


def pdf_default() -> Path | None:
    pdfs = sorted(STATIC_FOLDER.glob("*.pdf"))
    return pdfs[0] if pdfs else None


def elapsed(job: dict) -> int | None:
    started_at = job.get("started_at")
    if isinstance(started_at, (int, float)):
        return int(max(0, time.time() - started_at))
    return None


def start_job(pdf_path: Path) -> tuple[str | None, dict | None]:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return None, {"error": "GEMINI_API_KEY manquante dans .env"}

    job_id = str(uuid.uuid4())
    output_dir = RESULTS_FOLDER / job_id
    jobs[job_id] = {
        "status": "en cours",
        "produits": [],
        "error": None,
        "pdf_path": str(pdf_path.resolve()),
        "pdf_name": pdf_path.name,
        "started_at": time.time(),
        "timeout_seconds": JOB_TIMEOUT_SECONDS,
    }

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


@app.route("/upload", methods=["POST"])
def upload():
    uploaded = request.files.get("pdf")
    if not uploaded:
        return jsonify({"error": "Pas de fichier"}), 400

    pdf_path = UPLOAD_FOLDER / f"{uuid.uuid4()}.pdf"
    uploaded.save(pdf_path)

    job_id, error = start_job(pdf_path)
    if error:
        return jsonify(error), 400

    return jsonify({"job_id": job_id, "pdf_name": pdf_path.name})


@app.route("/use-default-pdf", methods=["POST"])
def use_default_pdf():
    pdf = pdf_default()
    if not pdf:
        return jsonify({"error": "Aucun PDF par defaut trouve dans /static"}), 404

    job_id, error = start_job(pdf)
    if error:
        return jsonify(error), 400

    return jsonify({"job_id": job_id, "pdf_name": pdf.name})


@app.route("/status/<job_id>")
def status(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"status": "inconnu"})
    return jsonify({
        "status": job.get("status", "inconnu"),
        "error": job.get("error"),
        "pdf_name": job.get("pdf_name"),
        "elapsed_seconds": elapsed(job),
        "timeout_seconds": job.get("timeout_seconds"),
        "produits_count": len(job.get("produits") or []),
    })


@app.route("/results/<job_id>")
def results(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job inconnu"}), 404
    if job.get("status") != "done":
        return jsonify({"error": "Résultats non disponibles"}), 409
    return jsonify({
        "produits": job.get("produits", []),
        "pdf_name": job.get("pdf_name"),
    })


@app.route("/images/<path:filename>")
def serve_image(filename):
    return send_from_directory(RESULTS_FOLDER, filename)


@app.route("/pdf/<job_id>")
def serve_pdf(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job inconnu"}), 404
    return send_file(job["pdf_path"], mimetype="application/pdf")


if __name__ == "__main__":
    debug = os.getenv("FLASK_DEBUG", "true").strip().lower() in {"1", "true", "yes", "on"}
    app.run(debug=debug)
