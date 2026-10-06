# Coding-agent protocol

`demo.py` can run an external coding agent for the implementation stage:

```powershell
python demo.py greenfield --agent-command python path\to\agent.py
```

It can also call Gemini directly:

```powershell
$env:GEMINI_API_KEY = 'set-this-in-your-shell'
$env:GEMINI_MODEL = 'gemini-2.0-flash'
python demo.py greenfield --gemini-agent
```

The key is read from `.env` or the environment only. It is not placed in run state,
prompts, artifacts, or logs. Gemini must return a JSON object with a `files` property
mapping repository-relative text paths to complete file contents.

The orchestrator appends the path to a JSON request file as the final argument.
The request contains the normalized requirement, scenario, revision, prior stage
artifacts, and the disposable workspace path. The agent may edit files only inside
that workspace and must exit with status zero.

For a brownfield run, prior design artifacts also contain baseline source hashes and
an API compatibility contract. The agent receives those artifacts and its applied
patch is validated against the existing test suite before the workflow can continue.

The orchestrator then:

1. Computes a unified text patch against the original repository.
2. Rejects the stage if no files changed.
3. Runs `dotnet build src/UrlShortener.Api/UrlShortener.Api.csproj`
   in the disposable workspace.
4. Sends the patch through the security stage for path, secret, and dangerous-operation
   checks.
5. Pauses for implementation approval before applying the validated text-file changes
   to the live checkout.
6. Records the applied paths, patch, validation output, and rollback snapshot.
7. Deletes the disposable workspace after collection.

The generated patch is review evidence for the applied change. Human approval remains
required before release. A production agent adapter should add model authentication,
tool allowlists, resource limits, secret redaction, stronger patch parsing, and a
reviewed-patch approval checkpoint before applying high-impact changes.

If a later stage exhausts its retry budget, or a requirement is revised, the engine
restores the implementation files from the rollback snapshot stored in the artifact.
