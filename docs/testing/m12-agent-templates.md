# Starter Agent Templates

| | |
|---|---|
| **Status** | Active v1.0 (M12.13) |
| **Date** | 2026-10-07 |
| **Code** | `console/src/features/agents/templates.json` · `templates.ts` · `TemplateGallery.tsx` · `TemplatePreviewPage.tsx` |
| **Tests** | `console/src/features/agents/Templates.test.tsx` (36) · `platform/tests/test_m12_templates.py` (29) · `e2e/browser.mjs` |
| **Related** | [LLD §4 Agents](../architecture/epic12/m12-lld.md) · [Product vision](../architecture/epic12/m12-product-vision.md) · [Frontend audit](frontend-product-completeness-audit.md) · [Developer API](../apis/developer-api.md) |

## 1. What a template is

A **starting point for an ordinary agent**, and nothing more. A template is a set of values for
fields the agent already has. Choosing one calls the same `POST /v1/console/agents` the blank form
calls; what comes back is an ordinary agent that edits, archives, calls and is counted exactly
like one typed in by hand.

```
Agent template  ──"Use this template"──▶  POST /v1/console/agents  ──▶  an ordinary agent
 (product data)                            (unchanged, M12.3)            (editable, yours)
```

There is **no template table, no template endpoint, no template runtime and no template
permission**. The milestone added no server code: the eight definitions are version-controlled
JSON shipped with the console, and the platform tests read that same file.

## 2. The eight templates

| Template | Category | What it is for |
|---|---|---|
| **Sales representative** | Sales | Understand a need, explain value, agree a next step |
| **Interviewer** | HR / Interview | A structured screening interview with follow-up questions |
| **Customer support** | Support | Understand a problem, give safe guidance, escalate |
| **Receptionist** | Business | Greet, establish the reason for the call, take a message |
| **Lead qualifier** | Lead generation | Need, scale, timing, who decides — then a summary |
| **Appointment assistant** | Appointments | Take an appointment **request** and preferred times |
| **FAQ assistant** | Information | Answer what it was told; say plainly what it does not know |
| **VoxVoice demo assistant** | Demo | Hear the product. The one to create first. |

Categories are metadata. There is no filtering UI, because eight cards do not need one.

## 3. What each template sets

Only fields that reach the runtime (LLD §4) — the same ones the editor shows:

| Field | Set by a template? |
|---|---|
| `name` | Yes — the template's name, editable immediately |
| `system_prompt` | Yes — the instructions, shown in full on the preview |
| `first_message` | Yes — the greeting |
| `language` | Yes, `en`, resolved at creation (see §4) |
| `llm_model` | **No** — server default |
| `tts_voice` | **No** — server default |

Nothing else exists to set. `allow_interruptions`, the endpointing delays, `tools` and the
provider selectors are not written, because the console does not write them: a setting that does
nothing must not look like it does (AR-25, finding F-1).

## 4. Why model and voice are left unset

What a provider account offers is **deployment-specific**. A template is static, so a pinned model
or voice id would be a value that happens to work on one machine and is refused on the next. Both
are therefore left `null`, the agent takes the server's default, and the editor changes them.

**Language is the one value resolved at creation time.** Templates are written for `en`, which is
what both STT profiles declare (`LocalSTT` and the Deepgram default). If a deployment's recognizer
does not declare `en`, the console sends that server's own `default_language` instead of a value
it would refuse. The preview says so when it happens.

## 5. Lifecycle and immutability

A template is a file. Nothing in the product writes to it.

* "Use this template" **copies** the values into a create request. The agent holds no reference
  back, and no field records that a template was involved (§8).
* Editing the agent cannot reach the template. Tested, including through a browser.
* Two agents from one template are unrelated: editing one leaves the other exactly as created,
  and a third still starts from the template's own values. Tested.
* `version` is in the data for a future change to the starting values. Bumping it does not touch
  agents already created — they are ordinary rows, not instances.

## 6. Organizations and roles

**Templates are global product data.** Every organization sees the same eight; none can add,
change or hide one, and no organization-specific data is attached to them.

**The agents they create are organization-owned**, like any other agent. Org A's agent is reported
as missing to Org B (S4), not merely hidden. Tested.

**Roles are the existing agent roles**, because creating from a template *is* creating an agent:

| Role | Browse templates | Read a preview | Use a template |
|---|---|---|---|
| owner · admin · developer | yes | yes | yes |
| viewer | yes | yes | **no** — and the button is not shown |

There is no template permission. A viewer who reaches the endpoint anyway still gets `403` from
the server, which remains the authorization boundary.

## 7. The surface

* **Agents page** — the organization's agents first, then a *Starter templates* section of cards
  (category, name, one-line summary). The cards are deliberately a different shape from an agent
  row: a template is not something the organization owns.
* **Preview** (`/agents/templates/:id`) — purpose, what it does in order, the starting settings,
  the greeting, and **the whole system prompt**, because that is what the user is about to own.
  Nothing is created by looking.
* **Use this template** → the agent is created → the **normal agent editor** opens on it.

One request loads all templates: they are in the bundle. The only API call the preview makes is
the `agents/options` the editor already makes.

## 8. Attribution: deliberately not recorded

The product could show *"Created from Sales representative"*. It does not. The agent row is the
frozen M11 schema, and there is no safe existing field to put it in; adding a column or a table
for a label would be a schema change for a cosmetic feature. Reconsider only if something needs it.

## 9. Testing

| Layer | Where | What it proves |
|---|---|---|
| Definitions | `Templates.test.tsx`, `test_m12_templates.py` | Eight templates, unique ids and names, every field present and within the agent limits, `en`, no pinned model or voice, no credential, each prompt states its own limits and claims no integration the platform lacks |
| Values | `Templates.test.tsx` | What "use" sends; the language fallback; that the values are copied, not shared |
| Creation | `test_m12_templates.py` | Each template creates an agent the **real** server validation accepts, which then lists, reads back, edits and archives |
| Runtime | `test_m12_templates.py` | Each of the eight completes a **real WebRTC call** through the frozen Playground path and appears in Conversations, Analytics and the Overview |
| Immutability | both | Two agents independent; the file unchanged after use |
| Isolation | `test_m12_templates.py` | Same templates for both organizations; the agents isolated (404) |
| Role | both | A viewer is not offered the action and is refused by the server |
| Browser | `e2e/browser.mjs` | Gallery → preview → use → editor → edit → persists → template unchanged, plus the gallery and preview at five widths |

The suites were verified to **fail** when a template is broken: an unsupported language and an
empty prompt turn five tests red.

## 10. Known limitations

| Limitation | Why it is acceptable |
|---|---|
| Templates are not real-provider validated | The runtime evidence uses the deterministic providers, as everything in M12 does. The templates are prompts; nothing about them is provider-specific. |
| The prompts are untested against a real language model | No model ran here. A prompt's *quality* is a judgement, not an assertion; what is tested is that it is well-formed, honest about its limits and accepted by the platform. |
| No attribution on the created agent | §8 — deliberate. |
| No filtering, search or sorting of templates | Eight cards. Add it when there are thirty. |
| Templates are English only | `en` is the only language the recognizer declares (F-4). |
| No user-defined or organization templates | Out of scope: it would need a table, an endpoint and a permission model, which is a product decision, not a milestone detail. |
| The FAQ template contains a placeholder paragraph | By design: it tells the user to replace it with the facts the agent should know, because the platform has no retrieval. |
