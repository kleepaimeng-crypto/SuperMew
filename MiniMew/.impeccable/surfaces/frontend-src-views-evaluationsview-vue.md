---
version: 1
slug: "frontend-src-views-evaluationsview-vue"
primary_target: "frontend/src/views/EvaluationsView.vue"
related_targets: ["frontend/src/App.vue", "frontend/src/router.ts", "frontend/src/style.css"]
---

# Evaluations workspace

Mode: Operate. Scope: authenticated `/evaluations` and `/evaluations/:jobId`, Chapter 8 package C. This is an authorized extension of the incumbent Documents workspace, implemented directly in code. No comp was approved and no new visual world was commissioned. The user chose selection of CLI-prepared corpora in the browser; this surface does not prepare corpora.

## Direction contract

THESIS: Make a controlled RAG experiment inspectable from its saved execution and report. Keep structural checks, controlled Judge results and real-model semantic quality distinct.

OWN-WORLD: Inherit the pale green-white canvas, dark green primary actions, neutral sans-serif typography, fine dividers, white six-pixel form controls and shared MiniMew masthead/navigation. Use the incumbent history/sidebar plus main-content composition. No shipping illustration or new token system.

STORY: Select prepared material, import Dataset and Scenario JSON, confirm budgets, validate and create an experiment. Return through history to saved per-case answers, materials and execution counts. Compare a completed experiment with a baseline, explicitly declaring permitted variable changes. Display errors, unavailable scores and incompatible comparisons as such.

FIRST VIEWPORT: Desktop presents a 270px history column, a 32px gap and a flexible main column inside the existing workspace shell. New experiment is available in the history column. Main content begins with the current task or persisted experiment status rather than an ornamental introduction.

FORM: Code-led extension. Follow the existing document-page world and familiar native form behavior; task-specific tables, case selectors and budget fields do not establish a new global component system.

## Responsibilities and state ownership

- `App.vue` owns the authenticated shell and navigation; `router.ts` owns the guarded optional job route. The route identifies the selected experiment.
- The view delegates histories, prepared corpora, selected experiment, case detail, reports, comparisons and execution actions to `useEvaluationsStore`. These displayed values are projections of the service's saved state; a browser animation or successful submission must not announce execution completion.
- View-local state holds file selections, parsed input, budgets, baseline selection and allowed changes. Changing creation inputs invalidates the prior input check. Changing baseline or allowed changes clears the displayed comparison. Account changes clear local inputs; unmount resets the store.
- Active jobs show a request-cancel action and distinguish `取消中` from a terminal status. The page says that saved progress is periodically read; disconnecting the browser is not represented as cancellation.
- Browser file parsing checks JSON, a Scenario array and the 2 MB per-file limit. This is not a replacement for service validation; creation explicitly repeats material and budget validation.

## Controlled experiment labels

Keep `真实本地检索 · 受控生成与评分` visible in the workspace and repeat the provenance where the experiment/report is interpreted. Dataset contains questions and human scoring standards; Scenario supplies controlled strategy, answer and Judge output. The explanation states that human standards enter the scoring side and that passing this experiment does not prove real-model semantic quality.

Structural report captions explicitly say they do not equal answer semantic correctness. Judge values are 0–2; absent evaluation remains `未评价`, never a fabricated zero. Baseline comparison is a separate returned report; gate differences and rules remain inspectable. State labels come through the imported evaluation label mapping, not a second local lifecycle.

## Visual reuse and responsive behavior

The global `style.css` remains the visual authority for the shell, typography, primary button and focus behavior. Scoped page rules provide the history layout, section dividers, native inputs, case selection outline and flat report tables. Selected history uses a full pale green background, without a colored side tab. Case selection uses a complete outline. Error notices use a complete warm border and tint rather than a side stripe.

At 800px and below, history stacks above content, the divider moves below history, the history region is bounded to 190px, and the three budget fields become one column. Headings, actions and variable checkboxes wrap. Long IDs, answers and material references wrap; tables keep a local horizontal overflow container. The shared shell retains its existing 700px breakpoint. This documents the implemented two-breakpoint behavior without changing the incumbent shell.

## Evidence and finish boundary

The following real screenshot artifacts were visually inspected for this documentation pass:

- `.artifacts/evaluation-review/desktop.png`: completed experiment, selected case, baseline controls and comparison report, 1280px wide.
- `.artifacts/evaluation-review/mobile.png`: the corresponding stacked report view, 390px wide.
- `.artifacts/evaluation-review/form-desktop.png`: selected corpus, imported JSON and validated creation form, 1280px wide.
- `.artifacts/evaluation-review/form-mobile.png`: the corresponding single-column form, 390px wide.

These are rendered browser screenshots, not approved comps or shipping rasters. Visible fixture-like IDs and future corpus expiry are screenshot data; the screenshots alone do not prove live PostgreSQL, CPU/Milvus execution, provider quality, cancellation recovery or production readiness. This documentation pass read the supplied source, PRODUCT.md, Documents surface brief and screenshots; it did not start a service or run tests. Behavioral verification belongs to the package's recorded test and integration evidence.

The finish review supplied with this pass reports the duplicate eyebrow removed and the targeted repair resolved with a ship verdict; that verdict covers the reviewed repair, not an independently rescored whole product. The detector's two side-tab findings were removed or changed to complete-border treatment. Source and final screenshots inspected here show no remaining task-specific visual identity drift requiring a change.

## Preserved documentation drift

The incumbent Documents brief records a missing `DESIGN.md`; its finish sentence still names that global artifact. This extension preserves those existing files and does not treat missing global documentation as authorization to generate a token spec or repair historical guidance. PRODUCT.md's capability snapshot is explicitly historical and trails this evaluation surface; this scoped pass does not rewrite product history or promote screenshot evidence into product claims. New surface-specific guidance lives only in this brief.
