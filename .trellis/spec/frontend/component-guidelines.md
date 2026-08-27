# Component Guidelines

> How components are built in this project.

---

## Overview

<!--
Document your project's component conventions here.

Questions to answer:
- What component patterns do you use?
- How are props defined?
- How do you handle composition?
- What accessibility standards apply?
-->

(To be filled by the team)

---

## Component Structure

<!-- Standard structure of a component file -->

(To be filled by the team)

---

## Props Conventions

<!-- How props should be defined and typed -->

(To be filled by the team)

---

## Styling Patterns

### Convention: semantic tokens for the warm-light theme

- Global surfaces, borders, text, status colors, soft status backgrounds, and shadows
  are defined in `platform-ui/src/index.css`. Component inline styles reference those
  values through `var(--token-name)` instead of embedding dark-theme RGB/RGBA values.
- Primary-button text uses `--btn-primary-ink`; selected, warning, error, and success
  states pair their semantic foreground with the matching `*-soft` background.
- Recharts `stroke` and `fill` values that require literal colors import
  `CHART_GRID`, `CHART_AXIS`, `CHART_COLORS`, or `CHART_HIGHLIGHT_STROKE` from
  `platform-ui/src/theme/chart.ts`. That file mirrors the `--chart-*` tokens in
  `index.css`; palette changes update both locations together.

```tsx
// Correct: component state follows the shared light-theme contract.
const warningStyle = {
  color: "var(--warning-color)",
  background: "var(--warning-soft)",
};

<CartesianGrid stroke={CHART_GRID} />
```

Visual verification must load the chat and dashboard routes in a real browser, assert
no horizontal overflow at 1440 px, and inspect the console. A missing favicon may be
reported separately; rendering/runtime errors block the change.

Do not introduce an alternate theme provider for a one-off component; extend the
existing token set only when no semantic token represents the required state.

---

### Convention: result metric display names

- In `platform-ui/src/pages/chat/cards/ResultCard.tsx`, known engineering response
  and optimization objective keys must resolve through `OBJECTIVE_META` before
  rendering a user-facing result value.
- TOPSIS objective keys may have a `scenario:` prefix. Resolve the terminal metric
  key for known labels, but preserve the complete original key when no label exists
  so audit information is not lost.
- Apply the label formatter to the TOPSIS objective column only; design parameters
  remain their original parameter names. Cover known prefixed keys and an unknown
  key with a `ResultCard` regression test.

---

### Convention: parameter-sweep result disclosure

- `DAMPER_PARAMETER_SWEEP` results use the case `parameters` map as the table's
  `工况` label (`alpha` is displayed as `α`). The default `vfloor=0.001` remains in
  the solver input but is omitted from labels; a non-default `vfloor` must remain
  visible. Format scalar engineering results with four significant digits.
- Do not repeat raw `resultMetadata.damperParameters`, the sweep conclusion, or the
  aggregate bar chart above that table. The table and the time-history comparison
  are the authoritative comparison views.
- `TimeseriesSection` must render one chart per selected response, with one line per
  selected parameter case. A parameter-sweep comparison request must surface its
  loading error; it must not silently fall back to the single-case endpoint.
- Artifact download lists default to a `查看制品下载` disclosure button, matching the
  time-history interaction, so registered output files do not dominate the result
  card. The button exposes `aria-expanded` and its expanded content retains the
  semantic `list` / `listitem` structure.

```tsx
// Correct: cases are distinguishable and physical units are not mixed in one chart.
<td>{formatDamperParameters(case.parameters)}</td>
{selectedResponses.map(response => <ResponseComparisonChart response={response} />)}

// Wrong: repeat raw case JSON and hide a failed multi-case request behind one curve.
<InfoItem label="阻尼参数" value={JSON.stringify(metadata.damperParameters)} />
```

Regression tests in `cards.test.tsx` must assert: sweep cards omit the redundant
summary/chart, table labels expose `c / α` without the default `vfloor`, scalar table
values use four significant digits, and artifact links are absent until the disclosure
is opened.

---

## Accessibility

<!-- A11y requirements and patterns -->

(To be filled by the team)

---

## Common Mistakes

### Hard-coded colors left over from the dark theme

```tsx
// Wrong: becomes low-contrast or visually inconsistent on the warm-light surface.
backgroundColor: "rgba(26, 34, 45, 0.8)"

// Correct: follows the shared surface hierarchy.
backgroundColor: "var(--bg-tertiary)"
```

### Passing CSS variables to Recharts primitives that require resolved colors

Use the literal constants from `src/theme/chart.ts` for axes, grid lines, and series
palettes. Keep their values synchronized with `index.css` and cover the consuming page
with the existing TypeScript build plus a browser smoke test.
