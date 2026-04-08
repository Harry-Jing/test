# Configuration

User-facing configuration guide for `VRC Live Caption`.

## One File Rule

- Ordinary runtime configuration lives in `vrc-live-caption.toml`.
- Secrets live in `.env`.
- Copy `vrc-live-caption.toml.example` to `vrc-live-caption.toml` and edit that single file.
- Local FunASR and local TranslateGemma sidecars are also configured inside `vrc-live-caption.toml`; there are no separate sidecar TOML files.

## First Run

```bash
cp .env.example .env
cp vrc-live-caption.toml.example vrc-live-caption.toml
uv sync
uv run vrc-live-caption doctor
uv run vrc-live-caption run
```

## Local Dependency Install

When you want any local inference feature, install one shared local extra first:

```bash
uv sync --extra local-cpu
# or
uv sync --extra local-cu130
```

- Use `local-cpu` for CPU-only local STT and/or local translation.
- Use `local-cu130` on Windows/NVIDIA machines when you want local inference to resolve `device = "auto"` to `cuda:0`.

## OpenAI Default

Use this when you want the default cloud STT path and do not need translation yet.

`vrc-live-caption.toml`:

```toml
[stt]
provider = "openai_realtime"

[translation]
enabled = false
```

`.env`:

```dotenv
OPENAI_API_KEY=your_key_here
```

Common errors:

- `OPENAI_API_KEY not found`: add it to `.env` or export it in the shell.
- `Audio input device not found`: set `[capture].device` to a valid device index or name after running `uv run vrc-live-caption devices`.

## iFLYTEK RTASR

Use this when you want cloud STT through iFLYTEK instead of OpenAI.

`vrc-live-caption.toml`:

```toml
[stt]
provider = "iflytek_rtasr"

[stt.providers.iflytek_rtasr]
language = "autodialect"
vad_mode = "near_field"
```

`.env`:

```dotenv
IFLYTEK_APP_ID=your_app_id
IFLYTEK_API_KEY=your_api_key
IFLYTEK_API_SECRET=your_api_secret
```

Common errors:

- `IFLYTEK_* not found`: add all three secrets to `.env`.
- Connection failures during `doctor` or `run`: verify outbound network access and confirm the selected iFLYTEK account is enabled for RTASR.

## FunASR Local STT

Use this when you want local STT instead of a cloud provider.

Install the shared local extra first. See `Local Dependency Install` above.

`vrc-live-caption.toml`:

```toml
[stt]
provider = "funasr_local"

[stt.providers.funasr_local]
host = "127.0.0.1"
port = 10095
use_ssl = false

[stt.providers.funasr_local.sidecar]
device = "auto"
offline_asr_model = "paraformer-zh"
online_asr_model = "paraformer-zh-streaming"
vad_model = "fsmn-vad"
punc_model = "ct-punc"
```

Run order:

```bash
uv run vrc-live-caption local-stt serve
uv run vrc-live-caption doctor
uv run vrc-live-caption run
```

Expected `local-stt serve` output:

- `Endpoint: ws://...`
- `Device policy: ...`
- `Offline ASR model: ...`
- `Online ASR model: ...`
- `VAD model: ...`
- `Punctuation model: ...`
- `Log file: ...`
- `[ok] Local FunASR sidecar ready: ...`

Common errors:

- `FunASR dependencies are not installed`: install `local-cpu` or `local-cu130`.
- `device = "cuda"` but CUDA is unavailable: either switch `[stt.providers.funasr_local.sidecar].device` to `cpu` or install `local-cu130` on a Windows/NVIDIA machine.
- `local STT sidecar unreachable`: start `uv run vrc-live-caption local-stt serve` and make sure host and port match `[stt.providers.funasr_local]`.

## DeepL Translation

Use this when you want cloud text translation after STT.

`vrc-live-caption.toml`:

```toml
[translation]
enabled = true
provider = "deepl"
target_language = "en"
output_mode = "source_target"
```

`.env`:

```dotenv
DEEPL_AUTH_KEY=your_key_here
```

Common errors:

- `translation.target_language is required`: set `[translation].target_language`.
- `DEEPL_AUTH_KEY not found`: add it to `.env`.

## Google Cloud Translation

Use this when you want Google Cloud translation instead of DeepL.

`vrc-live-caption.toml`:

```toml
[translation]
enabled = true
provider = "google_cloud"
target_language = "en"

[translation.providers.google_cloud]
project_id = "your-project-id"
location = "global"
```

Environment prerequisites:

- Configure Google ADC on the machine.
- Set `[translation.providers.google_cloud].project_id`.

Common errors:

- `project_id is required`: fill `[translation.providers.google_cloud].project_id`.
- ADC/auth failures: run with valid Google Cloud credentials on the machine and verify the Translation API is enabled for the selected project.

## TranslateGemma Local Translation

Use this when you want local text translation.

Install the shared local extra first. See `Local Dependency Install` above.

`vrc-live-caption.toml`:

```toml
[translation]
enabled = true
provider = "translategemma_local"
source_language = "zh"
target_language = "en"
output_mode = "source_target"

[translation.providers.translategemma_local]
host = "127.0.0.1"
port = 10096
use_ssl = false

[translation.providers.translategemma_local.sidecar]
model = "google/translategemma-4b-it"
device = "auto"
dtype = "auto"
max_new_tokens = 256
```

Run order:

```bash
uv run vrc-live-caption local-translation serve
uv run vrc-live-caption doctor
uv run vrc-live-caption run
```

Expected `local-translation serve` output:

- `Endpoint: ws://...`
- `Model: ...`
- `Device policy: ...`
- `Dtype policy: ...`
- `Max new tokens: ...`
- `Log file: ...`
- `[ok] Local TranslateGemma sidecar ready: ...`

Environment prerequisites:

- Accept the Gemma license for the selected Hugging Face repo if it is gated.
- Authenticate locally with `hf auth login` or set `HF_TOKEN` when needed.

Common errors:

- `translation.source_language is required`: set `[translation].source_language`.
- `TranslateGemma dependencies are not installed`: install `local-cpu` or `local-cu130`.
- `local translation sidecar unreachable`: start `uv run vrc-live-caption local-translation serve` and make sure host and port match `[translation.providers.translategemma_local]`.
- Model download or gated access failures: accept the license and authenticate with Hugging Face before starting the sidecar.

## Where To Change What

- Audio input device: `[capture]`
- VRChat OSC target: `[osc]`
- STT backend choice: `[stt]`
- STT retry policy: `[stt.retry]`
- FunASR sidecar address: `[stt.providers.funasr_local]`
- FunASR sidecar runtime options: `[stt.providers.funasr_local.sidecar]`
- Translation feature and rendering mode: `[translation]`
- Google Cloud translation settings: `[translation.providers.google_cloud]`
- TranslateGemma sidecar address: `[translation.providers.translategemma_local]`
- TranslateGemma sidecar runtime options: `[translation.providers.translategemma_local.sidecar]`
