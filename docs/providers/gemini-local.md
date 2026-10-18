# Gemini and local provider configuration

All application platforms use the same model gateway and policy filters.
The local route is `local-qwen3`; the Gemini route is `gemini-flash`.
Existing free and paid OpenRouter routes remain available under their original
cost restrictions.

## Local operation

Ollama must be running on a loopback endpoint. Bootstrap never downloads models.
This workstation already has `qwen3:4b` and `bge-m3:latest` installed. These same
model names and HTTP adapter work with Ollama on macOS, Linux, and Windows.

```dotenv
ATLAS_PROFILE=local_free
ATLAS_OLLAMA_HOST=http://localhost:11434
ATLAS_LOCAL_MODEL=qwen3:4b
ATLAS_LOCAL_CONTEXT_LENGTH=8192
ATLAS_DEFAULT_MODEL=local-qwen3
ATLAS_EMBED_PROVIDER=ollama
ATLAS_EMBED_MODEL=bge-m3:latest
ATLAS_LOCAL_EMBED_MODEL=bge-m3:latest
```

The 4B chat model uses one concurrent request and an 8K context by default.
No cloud key is required. An unavailable service or missing chat model makes
the Ollama health probe fail; it does not trigger a download. `local_free`
also overrides a legacy cloud embedding setting with the configured local
embedding model. Local traffic ignores environment HTTP proxies, and the chat
adapter rejects remote hosts so SECRET requests cannot be mislabeled local.

## Gemini operation

The configured Gemini model is `gemini-3.8-flash`; override it for your project if needed.
Availability depends on the Google project, region, and quota. An API key does
not establish that requests are free. Gemini has no per-request zero-price
ceiling, so `ATLAS_GEMINI_BILLING_TIER` defaults to `paid`.

For an **unbilled** Google AI Studio project:

```dotenv
ATLAS_PROFILE=free_hybrid
ATLAS_GEMINI_MODEL=gemini-3.8-flash
ATLAS_GEMINI_BILLING_TIER=free
ATLAS_GEMINI_API_KEY=<your unbilled project key>
```

Only declare `free` after verifying that billing is disabled for that project.
Changing its billing status requires updating this setting. A billed project
stays `paid` and requires a profile and budget that permit paid inference;
free-only profiles exclude it. Prices in `config/models.yaml` were verified
on October 8, 2026 and need review when Google changes pricing.

The Gemini adapter uses the `x-goog-api-key` header and preserves function call
IDs and opaque thought signatures. Parallel tool results form one response
turn. Thought tokens count toward output usage. Image inputs are typed inline
media, not base64 text inserted into prompts.

`GET /api/v1/providers/health` probes configured adapters without generating
content. A successful metadata probe establishes model access, not that a
future generation has available quota. The local quota governor tracks ATLAS
request caps; it does not know Google's authoritative account quota. Missing
credentials mean the provider is not registered or selected. A generation
must still succeed before claiming a working cloud end-to-end path.

Model names that are absent from `config/models.yaml` fail configuration
validation. To choose a different Gemini/local model, first add verified
capabilities and prices to the catalog, then select its provider model using
the matching environment setting. Tier lists reference logical catalog IDs.

## Embedding migration

Qwen cloud embeddings and BGE-M3 both have 1024 dimensions, but represent
different vector spaces. Runtime vector collections now include a stable hash
of the embedding provider, sanitized endpoint, and model identity. Cache,
semantic memory, episodes, and knowledge all use this separation. Switching
models opens a distinct namespace and never deletes, renames, or searches the
old vectors as if compatible.

Existing SQLite documents, chunks, and episodes remain intact. Dense recall
of old content requires re-embedding those records into the new namespace;
lexical/SQL recall can continue independently. Returning to an earlier
embedding backend/model restores access to its namespaced collections. Legacy
unsuffixed collections remain on disk and need an explicit rebuild before
joining any new namespace. Reindexing must read the stored source text and
write new vectors; copying old vectors would violate the isolation.

No existing user data, credentials, or vector collections are reset as part
of provider startup. No paid inference or training job is performed by health
checks or configuration validation.

## References

- [Google model catalog](https://ai.google.dev/gemini-api/docs/models)
- [Gemini pricing](https://ai.google.dev/gemini-api/docs/pricing)
- [API key authentication](https://ai.google.dev/gemini-api/docs/api-key)
- [GenerateContent parts and function calls](https://ai.google.dev/api/generate-content)
- [Ollama tool calling](https://docs.ollama.com/capabilities/tool-calling)
