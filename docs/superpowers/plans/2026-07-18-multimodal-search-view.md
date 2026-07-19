# Multimodal Search View Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the workbench header and remote QA tab, then rebuild the remaining search tab as an accurate multimodal retrieval workspace modeled on the reference Vue page.

**Architecture:** Keep `/index/search` as the only endpoint used by the page's retrieval UI. Move source-metadata normalization into a small pure utility, while the existing Vue SFC owns Ant Design form state, collapsible result presentation, Markdown rendering, image preview, and responsive styling.

**Tech Stack:** Vue 3, Ant Design Vue, JavaScript ES modules, `marked`, Vite.

## Global Constraints

- Preserve the existing backend proxy and `POST /index/search` request contract.
- Do not call `/query`, `/chat`, or `/chat/clear` from this page.
- Keep the current Ant Design Vue visual language; use the remote Vue page only as an information-layout reference.
- Do not modify unrelated knowledge-base management, indexing, extraction, preprocessing, or structured-data behavior.
- The current directory is not a Git repository, so commit steps are not available.

---

### Task 1: Search Result Metadata Utility

**Files:**
- Create: `web/src/utils/multimodalSearch.mjs`
- Create: `web/scripts/test-multimodal-search.mjs`

**Interfaces:**
- Produces: `parseSearchSource(item)`, `getSearchResultFileId(item)`, `getSearchResultSourceRows(item)`, and `getSearchResultType(item)`.
- Consumes: raw `/index/search` result objects.

- [ ] **Step 1: Write the failing utility test**

Test a result whose `source` contains `file_id`, `page`, `type`, `Header 1`, and `image_path`; assert that the utility emits Chinese labels `文件`, `页码`, `类型`, `章节`, and `图片`. Test malformed `source` and assert it falls back to `entity_key` without throwing.

- [ ] **Step 2: Run the test and verify RED**

Run: `node web/scripts/test-multimodal-search.mjs`

Expected: failure because `web/src/utils/multimodalSearch.mjs` does not exist.

- [ ] **Step 3: Implement the pure utility**

Implement object-safe source merging, string conversion, file-id fallback, `image` to `图像片段` type labeling, and omission of empty rows. Do not mutate the API result.

- [ ] **Step 4: Run the test and verify GREEN**

Run: `node web/scripts/test-multimodal-search.mjs`

Expected: all metadata assertions pass with exit code 0.

### Task 2: Page Structure and Retrieval Presentation

**Files:**
- Modify: `web/src/views/MultimodalKbView.vue`
- Create: `web/scripts/test-multimodal-search-view.mjs`

**Interfaces:**
- Consumes: metadata utility functions from Task 1 and existing `api.searchKb`.
- Produces: one tab labeled `多模态检索`, expandable results, source rows, and image preview.

- [ ] **Step 1: Write the failing SFC structure test**

Read the SFC as UTF-8 and assert:

```js
assert.doesNotMatch(source, /class="header-section"/)
assert.doesNotMatch(source, /key="remoteQa"/)
assert.doesNotMatch(source, /远端问答/)
assert.match(source, /key="search" tab="[^"]*多模态检索/)
assert.match(source, /getSearchResultSourceRows/)
```

- [ ] **Step 2: Run the test and verify RED**

Run: `node web/scripts/test-multimodal-search-view.mjs`

Expected: failure because the old header and `remoteQa` pane still exist.

- [ ] **Step 3: Apply the minimal template and script change**

Remove the header block. Rename the search tab. Replace the loose list cards with an Ant Design collapse whose first result is open. Add a source footer using `getSearchResultSourceRows(item)`. Remove the remote QA pane, state, handlers, watcher, and active-tab loading branch. Preserve `handleSearch()` and its `/index/search` payload.

- [ ] **Step 4: Update image rendering**

Use the existing `api.getPdfImageUrl()` rewrite and ensure rendered `<img>` elements receive `loading="lazy"` and `decoding="async"`. Keep click-to-preview behavior for the expanded result.

- [ ] **Step 5: Run the structure and utility tests**

Run:

```powershell
node web/scripts/test-multimodal-search.mjs
node web/scripts/test-multimodal-search-view.mjs
```

Expected: both scripts exit 0.

### Task 3: Responsive Styling and Full Verification

**Files:**
- Modify: `web/src/views/MultimodalKbView.vue`

**Interfaces:**
- Consumes: Task 2 template class names.
- Produces: desktop and narrow-screen form layouts without overflow or overlapping text.

- [ ] **Step 1: Add scoped styles**

Create a restrained search workspace with an unframed page layout, compact bordered condition panel, result count header, stable collapse headers, source chips, Markdown tables with horizontal scrolling, and images constrained with `object-fit: contain`.

- [ ] **Step 2: Run focused tests**

Run:

```powershell
node web/scripts/test-pagination.mjs
node web/scripts/test-multimodal-search.mjs
node web/scripts/test-multimodal-search-view.mjs
```

Expected: all scripts exit 0.

- [ ] **Step 3: Run the production build**

Run from `web`:

```powershell
.\node_modules\.bin\vite.cmd build
```

Expected: exit code 0. Existing large-chunk warnings may remain, but no build errors are allowed.

- [ ] **Step 4: Browser verification**

Open the Docker-hosted frontend, select the `钻井设计资料` knowledge base, search for a query that returns an image result, and verify desktop and narrow-screen layouts. Confirm the first result is expanded, source metadata is readable, the image URL resolves through `/api/multimodal/pdf/images`, and the removed header/remote QA tab are absent.

