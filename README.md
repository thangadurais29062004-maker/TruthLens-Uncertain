# TruthLens AI 2.4 · Signal Studio

A local content-investigation application with real/AI-generated image prediction, a separate experimental video face-image detector, visual image evidence, reference comparison, annotated video timelines, claim candidates and fact-check links, embedded Content Credentials verification, accounts with private histories, cancellable subprocess jobs, and downloadable PDF/JSON reports.

**Prediction status:** Image reports now classify **Likely real photograph**, **Likely AI-generated**, or **Uncertain** using the original Community Forensics ViT-Small checkpoint. All three original/compressed/reduced-resolution scores must be ≤0.2 for likely real or ≥0.8 for likely AI; otherwise the app abstains. Very small or panoramic images also remain uncertain. This task distinguishes AI-generated imagery from photographs; it does not cover every Photoshop edit, face swap, or misleading context. Videos and articles remain inconclusive. Scores are uncalibrated.

**Measured results:** On 600 public dataset-viewer JPEG previews, original-input binary accuracy was 89.5% (9 false positives, 54 false negatives). The three-view policy decided 448 images with 92.4% accuracy and left 152 uncertain (74.7% decision coverage). Likely-real precision was 89.1%; likely-AI precision was 99.3%. These are sample measurements, not per-image confidence or general production accuracy. Compression reduced binary accuracy to 80%. Midjourney examples were a major weakness (8/32 correct at threshold 0.5). Training overlap and dataset labels have not been independently audited. See benchmarks/image-benchmark.json for full results.

## Start locally

Use Python 3.12 or 3.13. On this Windows computer, run `./setup.ps1` once, then `./start.ps1`. Setup can use Codex's bundled Python. Alternatively:

```powershell
py -3.12 -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
.venv/Scripts/python.exe server.py
```

Open http://127.0.0.1:8000 and create a local account with a password of at least 12 characters. The first local account receives existing version-1 reports. Each account has its own history. Sessions expire after 12 hours; cookies are HttpOnly and SameSite=Strict; passwords use salted scrypt. Public deployments issue Secure cookies and require HTTPS.

On macOS/Linux use `.venv/bin/python` after `python3 -m venv .venv` and `.venv/bin/pip install -r requirements.txt`.

## Working features

- Dashboard: a responsive Signal Studio workspace with real report counts, recent investigations, image/video/article shortcuts, and light/dark themes. Counts reflect the latest 200 reports in your account; they are job activity, not detection accuracy.
- Library: save folders, up to five tags, and favourites on each report. Search names, folders and tags, filter by folder, favourites, content and job status, sort reports, and select up to 20 completed reports for a ZIP containing PDFs, JSON files, and a CSV index. Bundle downloads enforce account ownership.
- Batch: queue up to 8 supported image/video files, 50 MB each and 200 MB total, for sequential processing. Follow per-file progress, stop the current job and remaining queue, and download completed reports together. Keep the tab open until all uploads finish; processing then continues on the server after closing or refreshing the browser. Reopen Batch analysis to retrieve your latest queue. One batch is admitted globally at a time. Unfinished uploads expire after 15 minutes and temporary media is removed after processing, cancellation, expiry or restart. Server restarts interrupt unfinished jobs; completed reports remain available and unfinished files must be resubmitted.
- Images: decode JPEG/PNG/WebP, inspect limited EXIF, measure detail and recompression residuals, show a residual overlay, detect faces for visual inspection, and run a whole-image AI-generation classifier across three input versions. An optional reference comparison shows pixel differences after resizing; it does not geometrically align viewpoints or crops.
- Video: inspect 24 distributed samples, face boxes and geometric track IDs, annotated thumbnails, eligible face-model scores, and three short consecutive-frame windows near large visual changes. Frame buttons inspect retained samples; the seek control verifies the selected original video checksum. IDs do not recognize people. No trained temporal, audio, or lip-sync classifier is included.
- Articles: extract English claim candidates and language highlights. Optional lookup retrieves candidate reviews; their ratings are not automatically applied to a different claim. Save a separate human assessment and evidence note for each claim; these appear in PDF reports.
- Provenance: verify embedded C2PA credentials locally against a bundled trust-anchor snapshot. Distinguish absent, trusted, valid-but-untrusted, invalid, and verification-error states. Remote manifests are not fetched; credentials do not establish factual truth.
- Evidence Explorer: expandable observations show measurements and units, significance, alternative explanations and next checks. Coverage distinguishes completed, partial, skipped and failed checks. Image previews include a reveal slider, zoom and reference/difference modes; retained thumbnails do not recover original detail. Video frame inspectors show recorded measurements and gaps between distributed samples. Seeking requires the selected original file to match the report SHA-256. Claim sources show reviewed wording and dates with explicit context checks. PDF and JSON exports include this detailed evidence.
- Reports: plain-language summaries with What we noticed / Why it matters / What to check next, short explanations, clear next steps, and expandable technical details. Private history, JSON/PDF downloads, visual evidence, source links and limitations. Latest 200 reports per account. Existing reports use the friendlier screen when reopened.
- Jobs: two subprocesses at a time, four admitted jobs, progress, cancellation, a three-minute wall-time limit, cleanup and interrupted-job recovery. A subprocess is not a full OS security sandbox.

Limit: 50 MB per submission, including both reference-comparison files. Images: 20 megapixels. Videos: two minutes, approximately 4K; MP4/MOV/WebM/AVI codec support varies. Articles: 50,000 characters.

## Included detectors

The default image model is the authors' original `OwensLab/commfor-model-224` checkpoint, pinned to revision 26afc31e6b40c312c3fd42c05a758be62446215b (MIT). TruthLens exported its single-logit ViT-Small graph locally. An independent NumPy reference check matched ONNX logits within 0.00002 on two inputs. `models/image-detector.json` pins the exported bytes and preprocessing: RGB, shorter-edge resize to 256, center crop 224, bilinear interpolation, ImageNet normalization, sigmoid synthetic-class output. Model checksums are checked during inference. No face is required for image prediction. The center crop can omit border edits.

`models/COMMUNITY-FORENSICS-NOTICE.md` includes attribution, licensing and reproduction instructions. Build-only ONNX tools were installed under the workspace work/ directory; runtime dependencies are unchanged. `tools/export_community_model.py` exports the pinned official checkpoint using a separate environment with onnx 1.19.1.

The separate video frame detector's quantized ONNX model is `onnx-community/Deep-Fake-Detector-v2-Model-ONNX`, derived from `prithivMLmods/Deep-Fake-Detector-v2-Model`. Its card declares Apache-2.0. `models/onnx.json` records the pinned revision, SHA-256, class mapping, preprocessing, source, and evaluation status. See `models/MODEL-NOTICE.md` for attribution.

Input: RGB float32 NCHW, 224x224 bilinear resize, mean/std 0.5; softmax class 1 = Deepfake. For the video detector, the application scores detected face crops with 20% padding. No detected face means no prediction, while forensic checks still run. Outputs are raw and uncalibrated.

Image and video model configurations are discovered separately. TRUTHLENS_VIDEO_ONNX_CONFIG can override the video configuration; TRUTHLENS_ONNX_CONFIG overrides the image configuration and acts as a fallback override for videos. Set `TRUTHLENS_ONNX_CONFIG` to another configuration; an empty value disables ONNX inference. `setup_models.py` re-downloads the pinned video detector weights and checks the checksum. Image weights are bundled; the original-checkpoint exporter is provided for reproduction. The bundled trust-anchor snapshot was retrieved on 2026-10-09.

## Fact-check lookup

Enable the checkbox to request lookup. Without a Google key, recent FactCheck.org titles are downloaded and matched locally. This covers recent feed entries only; claim text is not sent to the feed. With `GOOGLE_FACT_CHECK_API_KEY`, up to three extracted claim excerpts are sent to Google Fact Check Tools. The app displays publisher, date, reviewed claim, URL, and publisher rating. Verify claim equivalence and context yourself. No match remains insufficient evidence.

```powershell
$env:GOOGLE_FACT_CHECK_API_KEY = 'your-key'
./start.ps1
```

The key remains in the server environment. No Google key was provided, so authenticated live lookup was not verified; the adapter is covered by mocked tests. The public feed was retrieved successfully. Local server.py does not load .env automatically; .env is for Compose.

## Training and evaluation

`train.py` trains a logistic baseline on five forensic features from a CSV with `path,label` (0 authentic, 1 manipulated). Use at least 20 distinct images and eight per class. Paths are relative to the CSV. Identical files are rejected; normalization is fit on training examples only. Prepare source-separated datasets yourself: the random split does not prevent related-image leakage.

```powershell
.venv/Scripts/python.exe train.py dataset.csv
.venv/Scripts/python.exe evaluate.py independent-test.csv --output evaluation.json
```

Evaluation measures coverage, precision, recall, false-positive rate, ROC AUC, Brier score and calibration error on original, JPEG-quality-60, and half-resolution inputs. No-face samples are skipped with coverage reported. Metrics require both classes among eligible samples. Duplicate image bytes are rejected. The CLI writes model and configuration checksums, a manifest checksum, timestamp, and per-variant metrics. Place its output at evaluation.json, or set TRUTHLENS_EVALUATION_FILE to its absolute path. Model & methods displays coverage, false positives/negatives, precision, recall, false-positive rate, ROC AUC, Brier score and calibration error only when the active model bytes and configuration match. Dataset independence is the operator's responsibility. The bundled local public-sample benchmark uses 600 cached JPEG previews from TheKernel01/AIGC-Detection-Benchmark, revision c91d9024a5a77ef06e2ec681b53f9caf08675663. Seed 20261010 selected 60 disjoint 100-row blocks and ten rows per block. It is a clustered subset, not the full source benchmark. Source training overlap is not audited. No training or threshold fitting was performed on this sample. The third-party converted model initially tested was rejected after poor pilot results; the official checkpoint replaced it. No production-wide accuracy or calibrated probability is claimed. Benchmark images stay under work/ and are not redistributed; manifests and per-image scores are included under benchmarks/.

## Privacy and retention

Media and full article text are temporarily written under data/temporary for worker subprocesses and removed after completion, failure, or cancellation. Startup removes app-owned interrupted temporary directories. Reports retain low-resolution visual excerpts, extracted claims, filenames, hashes, measurements and URLs. Logical deletion is not a forensic disk wipe.

Batch uploads are staged temporarily under data/batch-media until their turn; they are not served by the web server. Uploaded media is local and may remain while the server is stopped, until the next startup cleanup. SQLite lives at data/reports.sqlite3, or TRUTHLENS_DATA_DIR. TRUTHLENS_RETENTION_DAYS=0 disables time expiry; a positive number expires finished records on subsequent submissions. The latest-200-per-account cap still applies.

## Deployment package

Compose provides an app container, persistent data, and Caddy HTTPS proxy. The app has 2 GB memory, two CPU, and process-count limits. Workers share the app container's permissions/environment; use a dedicated host for untrusted uploads.

1. Copy .env.example to .env and set TRUTHLENS_DOMAIN to a real domain pointing at your server; optionally add the Google key and retention.
2. Run `docker compose up --build -d`.
3. Run `docker compose exec app python create_user.py`. Public self-registration is disabled.

Only the proxy exposes ports. DNS and ports 80/443 must work. No public deployment was performed because no host/domain was supplied; Compose was not executed in this Windows environment. The server targets small deployments, not high scale. Authentication throttles ten failed attempts per peer for five minutes; reverse-proxied users share a peer. There is no email recovery or MFA.

## Verification and layout

Run `.venv/Scripts/python.exe -m unittest discover -s tests -v`. The 48 tests cover decision thresholds/abstention, crop preprocessing, inference checksums, separate video configuration, prediction exports, detailed evidence coverage/units/gaps, aligned comparison previews, decoding, invalid inputs, user ownership, bundle access/input validation, persistent batch processing/cancellation/expiry/restart cleanup, folder/tag/favourite ownership, benchmark checksums/invalid metrics, subprocess jobs, cancellation, comparisons, visual evidence, model contracts, training/evaluation, source adapters, C2PA and PDFs. Fixtures test software behavior, not detection accuracy. Actual pretrained inference, signed/unsigned credential checks and public-feed retrieval were also checked.

server.py: API and job management. truthlens/batches.py: persistent server queue and upload cleanup. summaries.py: evidence guidance. explorer.py: detailed observations and coverage. prediction.py: three-view image classification and abstention. validation.py: benchmark status. worker.py: one-job processing. truthlens/analysis.py: analysis/inference. evidence.py: visuals/faces/claims/provenance. auth.py: accounts. pdf_report.py: PDF. web/: interface. train.py/evaluate.py: ML tools. setup_models.py: model recovery. Dockerfile/compose.yaml/Caddyfile: deployment.
#   T r u t h l e n s  
 