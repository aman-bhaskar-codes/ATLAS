# IDE dataset and local weight-training workflow

Open **IDE → Training** (`/ide/training`). This screen prepares reviewed data,
checks actual compute prerequisites, submits governed jobs, scores supplied
baseline/candidate prediction files, and can generate fresh held-out predictions
through registered local serving models. Preparation and prediction-file scoring do not
produce trained weights or prove live model inference.

## Data preparation

Use a workspace-relative UTF-8 JSONL file, at most 2 MiB and 2,000 traces. Each
row contains `group_id`, `privacy_class`, `messages`, and advertised `tools`.
Messages use ATLAS roles and normalized tool calls (`id`, `name`, `arguments`);
tool responses must match the requested ID and name. A complete trace ends in
an assistant turn. Images, hidden reasoning, and provider thought signatures
are excluded from this text/tool-call training format.

Validation blocks recognized credentials, private keys, bearer tokens, email
addresses, sensitive/secret classifications, and credential paths. This is
conservative screening, not a guarantee that all personal data was detected;
the owner must review and explicitly approve every local export. Nothing is
uploaded to a provider or automatically exported from the user’s history.

At least five independent task groups are required. A seeded hash sorts groups
into training, validation, and untouched holdout sets. Sibling traces and agent
continuations remain with their original task group. Duplicate traces across
different groups are refused. The trainer receives `train.jsonl` and
`valid.jsonl`; the final `holdout.jsonl` is not handed to the training module.

Exports live in `.atlas-training/exports/<id>`. Training artifacts and manifests
are private, excluded by a local `.gitignore`, and never replace source files.
Runtime manifests carry an HMAC using a private application-state key; changing
a verification, dataset, evaluation, or job manifest invalidates its integrity.

To export agent history, first run a real governed IDE test command. Export
requires a passing parsed test outcome, explicitly selected completed runs,
matching workspace ownership, and separate local-data approval. Provider
signatures are deliberately removed from training data. The proof reflects the
workspace verification that actually ran; it is not a claim that every old
historical project state was independently retested.

The preparation CLI performs no weight training:

```sh
python -m atlas.capabilities.ide.training.cli --workspace /path/to/project validate datasets/reviewed.jsonl
python -m atlas.capabilities.ide.training.cli --workspace /path/to/project prepare datasets/reviewed.jsonl --approve-local-export
```

CLI exports are local preparation artifacts. To use an export in the runtime’s
signed training workflow, prepare the reviewed source through the IDE service.

## Optional actual weight training

Actual MLX LoRA training requires all of the following:

- Native Apple silicon execution, not a Linux/Docker sandbox.
- `mlx_lm` installed in the same Python environment as the API.
- `python` on the governed command PATH matching that interpreter/environment.
- A compatible local checkpoint directory with model configuration,
  `tokenizer.json`, and valid safetensors weights, quantized to at most 4 bits
  and no larger than 3 GiB for this small-memory workflow.
- A validated, explicitly approved dataset and approval for the compute job.

The installed Ollama `qwen3:4b` GGUF artifact is **not** an MLX checkpoint. This
workstation currently lacks `mlx_lm` and an approved training checkpoint/data
scope. They are not installed or downloaded automatically. Missing requirements
produce `ready=false` and a training submission returns HTTP 503. Dataset
preparation and offline scoring remain available on other platforms.

The service returns a queued job ID before waiting for tool approval or
training. Execution uses the existing governed IDE command runner:
`python -I <trusted application runner> ...`. The isolated interpreter avoids
loading a trainer script shadowed by workspace code. The trusted runner sets
Hugging Face offline modes, disables external reporting, and limits training to
LoRA, batch 1, sequence length 512, four trainable layers, and a 4 GiB MLX memory
allocation guideline (not a hard operating-system RSS cap). The default wall-clock bound is 15 minutes; requests cannot exceed 30
minutes or 500 iterations. Only one job is admitted across processes sharing application training state.
An OS-held nonblocking lock prevents overlapping admission, with no lease-expiry
window while an approved job is still running.

Cancellation/shutdown requests governed command cancellation and waits at most
two seconds. Unconfirmed execution stays running and retains ownership until it
actually settles. Another process cannot invalidate a live job during recovery;
startup settles orphaned durable jobs without automatically replaying them.
Cancellation of a job owned by another runtime returns HTTP 503 and must be
requested through its owning runtime. Exit zero alone is insufficient: the service verifies a local
`adapters.safetensors` artifact before recording `trained`.

## Evaluation and promotion

Provide baseline and candidate prediction JSONL covering exactly the same
held-out `case_id`s. A prediction contains `case_id`, normalized `tool_calls`,
and `final_text`. Scoring compares tool names/arguments and the final answer
against the gold trace. Extra or changed operations, targets, credentials, or
explicit policy failures count as unapproved calls.

The gate requires a strict improvement in exact case score and zero unapproved
candidate calls. It measures this supplied held-out trace set, not general
safety or production quality. Supplied prediction files are scoring inputs;
they cannot serve as artifact promotion evidence.

For actual inference, use **Evaluate registered local models** or admin
`POST /api/v1/ide/training/evaluations/runtime`, with `workspace_id`, `export_id`,
`baseline_model`, `candidate_model`, `adapter_path`, and `approved=true`. This
requires two distinct enabled local Ollama tool-calling model registrations.
The candidate registration must explicitly bind `artifact_sha256` to the current
verified adapter and a signed completed training job for this export. There is
no fabricated tuned-model entry; an operator must separately serve and verify
that adapter before registering the binding. Missing runtime/model/artifact
requirements return HTTP 503. This workstation has no trained candidate.

Evaluation requests enforce SECRET / OFFLINE / ZERO_COST and strict model pins.
They permit at most 20 held-out cases, 16 assistant turns per case, and 120
seconds total. Actual model/provider identity and fallback flags are checked.
Gold assistant answers are excluded from prompts. Gold tool observations are
replayed only after exact predicted tool names and arguments match; predicted
tools never execute. Only signed `local_runtime` evaluations can promote.

Promotion additionally requires explicit owner approval, the same unchanged
dataset, a matching evaluated adapter hash, and a verified completed job for
that dataset. Promotion records an approved candidate; it does not fabricate a
serving model entry or activate an unsupported adapter in Ollama/Gemini.

## Runtime integration

The composition root owns `TrainingService(ide=..., agent_runs=...,
native_runtime=..., state_dir=<private application training state>,
gateway=..., model_registry=...)`. Call
`recover_interrupted()` after workspace persistence is ready and `close()`
before IDE/sandbox shutdown. Mount `routes_training.router` with the existing
API authentication layer. All mutation routes additionally require admin;
readonly keys cannot export data, execute verification, score/save evaluation,
start/cancel compute, or approve candidates.

No actual weight training, model download, paid job, dataset upload, or serving
model promotion was performed while implementing this workflow.

[MLX-LM fine-tuning implementation](https://github.com/ml-explore/mlx-lm/blob/main/mlx_lm/lora.py)
documents the underlying local LoRA command and dataset format.
