# Migrating an app's prompts to the promptry CMS

This guide turns hardcoded prompts into **dashboard-editable** prompts that
take effect live, with zero behaviour change until you opt a prompt in. It's
written so a coding agent can execute it end-to-end. Paste it as a task.

## What you get

- Edit a prompt from the promptry dashboard → the app uses the new text on
  its next call (within the cache TTL, default 60s). No redeploy.
- Each edit is a new registry version with full diff history; reverts stick.
- Every prompt keeps an **in-code default** that is the source of truth and
  the fallback if the registry is empty or unreachable.

## Core principle: opt-in, per prompt

Nothing is forced. An app that only calls `track()` / `track_invocation()`
is unaffected. You migrate **one prompt at a time** by wrapping it with
`promptry.render_prompt(...)`. Leave the rest alone.

## The API (promptry ≥ this version)

```python
import promptry

# Register the in-code default as v1 if the prompt doesn't exist yet.
# Idempotent; never overwrites a dashboard edit. Call once at startup.
promptry.seed_prompt(name, default_template)

# Fetch the latest registry version (cached), substitute $placeholders,
# fall back to default_template on any miss. Never raises.
text = promptry.render_prompt(name, default_template, ttl=60, **variables)
```

Templates use **`string.Template`** syntax (`$var` / `${var}`), NOT
`str.format`. This is deliberate: prompt bodies often contain literal `{`
and `}` (JSON examples), which would break `.format`. Unknown placeholders
are left intact instead of raising.

## Migration steps

For each prompt you want editable:

1. **Extract the template.** Take the existing f-string and replace each
   interpolation with a `$placeholder`:

   ```python
   # before
   prompt = f"""You are the {role}. The user asked: "{question}".
   Answer using this context:
   {context}"""

   # after — module-level default
   DEFAULT_ANSWER = """You are the $role. The user asked: "$question".
   Answer using this context:
   $context"""
   ```

   Pick a stable, namespaced `name` (e.g. `"qa.answer"`). If the prompt is
   already tracked, **reuse that exact name** so the registry template and
   the per-call telemetry line up under one entry.

2. **Render at call time** instead of building the f-string:

   ```python
   prompt = promptry.render_prompt(
       "qa.answer", DEFAULT_ANSWER,
       role=role, question=question, context=context,
   )
   ```

3. **Seed on startup.** Somewhere in app init (after promptry is
   configured), register the defaults so they appear in the dashboard:

   ```python
   for name, default in MANAGED_PROMPTS.items():
       promptry.seed_prompt(name, default)
   ```

4. **Done.** The prompt is now editable at `/prompts/<name>` → Edit tab.

## Caveats / gotchas

- **`$` in prompt bodies:** any literal `$` must be escaped as `$$` in the
  template, or `string.Template` will treat it as a placeholder. Rare in
  practice; check prompts that mention currency or shell vars.
- **System messages** are separate strings — migrate them the same way with
  their own name if you want them editable.
- **JSON-returning prompts:** keep them on `$`-substitution (never
  `.format`) so the `{...}` examples survive untouched.
- **Cache TTL:** edits go live within `ttl` seconds (default 60). Lower it
  per call for faster feedback, or call `promptry.prompts.clear_cache()`.
- **Seeding never clobbers:** if a prompt already has a version (including a
  dashboard edit), `seed_prompt` is a no-op. Changing the in-code DEFAULT
  therefore won't show up until the registry entry is removed or you edit it
  in the dashboard — the registry is the live source once seeded.
- **Fallback is total:** if promptry storage is down or the name is missing,
  `render_prompt` returns the rendered in-code default. The app never breaks
  because a prompt is managed.

## Reference implementation

See `tender-comparison`'s `services/agent_conversation_service.py`
(`MANAGED_AGENT_PROMPTS`, `seed_agent_prompts`) and its `prompt_setup.py`
startup hook for a working example covering three prompts.
