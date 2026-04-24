from flask import Flask, render_template, request, jsonify, send_file, send_from_directory
import os, uuid, threading, time
from pathlib import Path
from extract import extract_catalogue
from dotenv import load_dotenv
load_dotenv()

app = Flask(__name__)
UPLOAD_FOLDER = Path("uploads")
RESULTS_FOLDER = Path("resultats")
STATIC_FOLDER = Path("static")
UPLOAD_FOLDER.mkdir(exist_ok=True)
RESULTS_FOLDER.mkdir(exist_ok=True)

jobs = {}  # stocke l'état des extractions en cours
JOB_TIMEOUT_SECONDS = int(os.getenv("EXTRACTION_JOB_TIMEOUT_SECONDS", "240"))


def get_default_pdf_path():
    for pdf_path in sorted(STATIC_FOLDER.glob("*.pdf")):
        return pdf_path
    return None


def start_extraction(pdf_path: Path):
    job_id = str(uuid.uuid4())
    output_dir = RESULTS_FOLDER / job_id
    api_key = os.getenv("GEMINI_API_KEY")

    if not api_key:
        return None, {"error": "GEMINI_API_KEY manquante dans .env"}

    jobs[job_id] = {
        "status": "en cours",
        "produits": [],
        "error": None,
        "pdf_path": str(pdf_path.resolve()),
        "pdf_name": pdf_path.name,
        "started_at": time.time(),
        "timeout_seconds": JOB_TIMEOUT_SECONDS,
    }

    def run():
        try:
            produits = extract_catalogue(str(pdf_path), api_key, str(output_dir))
            # Si le watchdog a déjà expiré le job, on ne l'écrase pas.
            if jobs.get(job_id, {}).get("status") == "en cours":
                jobs[job_id]["status"] = "done"
                jobs[job_id]["produits"] = produits
        except Exception as e:
            jobs[job_id]["status"] = "error"
            jobs[job_id]["error"] = str(e)

    def watchdog():
        time.sleep(JOB_TIMEOUT_SECONDS)
        job = jobs.get(job_id)
        if not job:
            return
        if job.get("status") == "en cours":
            job["status"] = "error"
            job["error"] = (
                f"Extraction interrompue après {JOB_TIMEOUT_SECONDS}s (timeout). "
                "Réessaie avec un PDF plus petit ou baisse la charge Gemini."
            )

    threading.Thread(target=run, daemon=True).start()
    threading.Thread(target=watchdog, daemon=True).start()
    return job_id, None

@app.route("/")
def index():
    default_pdf = get_default_pdf_path()
    return render_template(
        "index.html",
        default_pdf_name=default_pdf.name if default_pdf else None,
    )

@app.route("/upload", methods=["POST"])
def upload():
    f = request.files.get("pdf")
    if not f:
        return jsonify({"error": "Pas de fichier"}), 400

    uploaded_id = str(uuid.uuid4())
    pdf_path = UPLOAD_FOLDER / f"{uploaded_id}.pdf"
    f.save(pdf_path)

    job_id, error = start_extraction(pdf_path)
    if error:
        return jsonify(error), 400

    return jsonify({"job_id": job_id, "pdf_name": pdf_path.name})


@app.route("/use-default-pdf", methods=["POST"])
def use_default_pdf():
    default_pdf = get_default_pdf_path()
    if not default_pdf:
        return jsonify({"error": "Aucun PDF par defaut trouve dans /static"}), 404

    job_id, error = start_extraction(default_pdf)
    if error:
        return jsonify(error), 400

    return jsonify({"job_id": job_id, "pdf_name": default_pdf.name})

@app.route("/status/<job_id>")
def status(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"status": "inconnu"})
    started_at = job.get("started_at")
    if isinstance(started_at, (int, float)):
        elapsed_seconds = int(max(0, time.time() - started_at))
    else:
        elapsed_seconds = None
    return jsonify({
        "status": job.get("status", "inconnu"),
        "error": job.get("error"),
        "pdf_name": job.get("pdf_name"),
        "elapsed_seconds": elapsed_seconds,
        "timeout_seconds": job.get("timeout_seconds"),
        "produits_count": len(job.get("produits", []) or []),
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
    app.run(debug=True)