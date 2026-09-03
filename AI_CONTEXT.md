# AI_CONTEXT

## 1. Project Purpose
Proposal Generator is a Python Flask web application centered around **AI Business Proposal Generation** as its primary product workspace. It provides end-to-end management of enterprise proposal workflows, client specifications, dynamic rich-text editing, AI-assisted section rewrites, and professional PDF/Word exports.

## 2. Architecture & Hierarchy

### Primary Workspace
- **AI Proposal Workspace** (`/grc/ai`): The core application landing point and exclusive user-facing product workspace.
- **Proposal Tracker** (`/grc/assessments`): Proposal lifecycle tracking, statuses, and history.
- **Template Library** (`/templates`): Built-in and user-uploaded custom proposal templates.

### Preserved Subsystems (Hidden from UI Navigation)
- **Network Scan Agent** (`/network-scan`): Host discovery and port scanning console preserved in codebase.
- **Subnet Batches** (`/batches`): Target subnet management and batch scanning preserved in codebase.
- **Assessment Engine**: Database models and audit log links preserved in codebase.

### Removed from User Navigation
- Overview / Dashboard (`/dashboard`), Assessments (`/assessments`), Network Scan Agent (`/network-scan`), and Subnet Batches (`/batches`) navigation tabs have been safely removed from primary user-facing navigation while preserving backend implementation files, services, and database relationships.

### Data & Execution Stack
Frontend (Linear × Vercel Enterprise Design System + Flask Jinja2 SSR Templates + Vanilla CSS)  
→ App Shell Layout (`templates/base.html` + `static/styles.css` + `static/app-loader.js`)  
→ Core API (`app.py` Flask HTTP Routes & Form Handlers)  
→ Backend (`app.py` application logic + `agent.py` scan service + Google Gemini AI API)  
→ Database (SQLite `agentscan.db`)  
→ External AI / Network Services (`google-genai`, `python-nmap`)

## 3. Two-State AI Proposal Workspace

```text
               PRIMARY WORKSPACE
                  AI PROPOSAL
                       │
       ┌───────────────┴───────────────┐
       │                               │
 State A: Before Generation     State B: After Generation
       │                               │
 Left: Client/Project Info       Top: Full-Width Proposal Header
 Right: Proposal Generator       Bottom Left: Proposal TOC (19 sections)
                                 Bottom Right: Proposal Document Body
```

- **State A (Before Generation)**:
  - **Left Column**: Structured, information-dense **Client / Project Information** panel displaying dynamic client details (Client Name, Project Scope, Timeline, Budget, Currency, Scope Deliverables).
  - **Right Column**: **AI Business Proposal Generator** form (fixed purpose, single-mode operation).
  - **TOC**: Hidden completely before a proposal exists.

- **State B (After Generation - Option 1 Full-Width Header)**:
  - **Top Workspace Header**: Back button `← Proposal Tracker` + Page Title & Client Subtitle in a compact flex row.
  - **Full-Width Proposal Header**: Spans 100% content width directly below the topbar, containing proposal code, status pill, title, metadata, and action buttons (`Regenerate Proposal`, `Download PDF`, `Download Word`, `Print Proposal`).
  - **Main Workspace Grid**:
    - **Left Column (~24%)**: **Proposal Table of Contents** with 19 interactive section links (`#section-1` through `#section-19`).
    - **Right Column (~76%)**: 19-section **Generated Business Proposal** document body (`_business_proposal_doc.html`).
  - **TOC Navigation**: Smooth client-side anchor scrolling (`scrollIntoView`) with active section observer. Bypasses all loading screens (0 loading overlays).

## 4. Protected Subsystems & Critical Invariants

- **Protected AI Proposal Engine**: Existing Gemini prompts, timeout configurations, section models, PDF compilation, DOCX generator, and database persistence functions are protected existing subsystems and must be preserved without unnecessary rewrite.
- **Zero Visible GRC Text**: The user interface contains ZERO visible instances of the word "GRC".
- **Zero Functionality Loss**: Network Scan Agent, Subnet Batches, PDF/DOCX exports, and underlying database schemas remain 100% functional.
- **Task-Bound Loading**: Loader overlays are strictly tied to active asynchronous operations (AI generation POST or PDF download fetch). TOC navigation produces zero loading state.

## 5. UI Component & Repository Map

| Area | File/Directory | Responsibility |
| --- | --- | --- |
| Enterprise Base Shell | [templates/base.html](file:///c:/Users/JAIYESH/OneDrive/Documents/python%20agent/templates/base.html) | Global app shell layout, left sidebar navigation with Core Navigation & Primary Workspace divider, top header, breadcrumbs, search bar |
| Design Tokens & Styles | [static/styles.css](file:///c:/Users/JAIYESH/OneDrive/Documents/python%20agent/static/styles.css) | Modern design system tokens, sidebar hierarchy, two-state workspace grid, client info panel, workspace TOC styling |
| Loader System | [static/app-loader.js](file:///c:/Users/JAIYESH/OneDrive/Documents/python%20agent/static/app-loader.js) | Task-based global loading overlay manager; strictly bypasses TOC anchor clicks and hash links |
| AI Proposal Workspace | [templates/ai_grc.html](file:///c:/Users/JAIYESH/OneDrive/Documents/python%20agent/templates/ai_grc.html) | Two-state AI Business Proposal workspace (Client Info panel in State A, transformed TOC in State B) |
| Proposal Document & Exports | [templates/_business_proposal_doc.html](file:///c:/Users/JAIYESH/OneDrive/Documents/python%20agent/templates/_business_proposal_doc.html) | Structured 19-section enterprise proposal document component & PDF/DOCX export controls |
| Network Scan Agent UI | [templates/network_scan.html](file:///c:/Users/JAIYESH/OneDrive/Documents/python%20agent/templates/network_scan.html) | Host discovery console, local IP/subnet metadata, discovered host table |
| Subnet Batch Management | [templates/batches.html](file:///c:/Users/JAIYESH/OneDrive/Documents/python%20agent/templates/batches.html) | Subnet batch card list, scan execution links |
| Core Application & API | [app.py](file:///c:/Users/JAIYESH/OneDrive/Documents/python%20agent/app.py) | Application entry routing, login authentication redirecting to `/grc/ai`, proposal CRUD, Gemini integration |

## 6. 19 Proposal Sections

1. EXECUTIVE SUMMARY (`#section-1`)
2. ABOUT THE VENDOR (`#section-2`)
3. UNDERSTANDING OF REQUIREMENT (`#section-3`)
4. PROPOSED SOLUTION (`#section-4`)
5. SCOPE OF WORK (IN SCOPE) (`#section-5`)
6. OUT OF SCOPE (`#section-6`)
7. DEPLOYMENT & IMPLEMENTATION APPROACH (`#section-7`)
8. PREREQUISITES (`#section-8`)
9. INFRASTRUCTURE REQUIREMENTS (`#section-9`)
10. INFORMATION SECURITY & COMPLIANCE (`#section-10`)
11. TRAINING & CHANGE MANAGEMENT (`#section-11`)
12. SUPPORT & SLA (`#section-12`)
13. COMMERCIALS (`#section-13`)
14. LICENSE TERM & RENEWAL (`#section-14`)
15. PAYMENT TERMS (`#section-15`)
16. LEGAL / COMMERCIAL TERMS (`#section-16`)
17. PROJECT GOVERNANCE & ESCALATION (`#section-17`)
18. ACCEPTANCE CRITERIA (`#section-18`)
19. RISKS & ASSUMPTIONS (`#section-19`)

95. RISKS & ASSUMPTIONS (`#section-19`)

## 7. Large-Format Rich Text Proposal Editor & Dynamic Sections

- **Rich Text Editor**:
  - Manual editing uses a large-format rich-text editing modal (`#richTextEditorModal`) featuring a sticky formatting toolbar (Undo, Redo, Bold, Italic, Underline, Strikethrough, H1, H2, Paragraph, Bullet List, Numbered List, Link, Image Insert, Alignment).
  - Writing canvas (`#richEditorCanvas`) provides a full document-like editing experience with HTML content handling.
- **Image Insertion & Persistence**:
  - Supports image paste (`Ctrl+V`), drag & drop, and file upload via backend route `POST /grc/ai/proposal/upload-image` (saved to `static/uploads/proposal_images/`).
  - Images persist across Save, browser refresh, reopening, PDF export, DOCX export, and Print.
- **Dynamic Unlimited Proposal Sections**:
  - Proposal data model normalizes sections into a canonical `sections` list (`19 default sections + N user-created custom sections`).
  - Users can create new sections using `+ Add Section`, rename custom sections, delete custom sections (with confirmation), and edit them using the same rich text editor.
  - Table of Contents (TOC) and section numbering (1 to N) dynamically update across UI, TOC, PDF, DOCX, and Print.
- **Tracker UI Refinements**:
  - Proposal header displays `Contribution:` instead of `Investment:`.
  - Removed `Generated: Live Workspace` text and unnecessary separators.
  - Action buttons (`Regenerate Proposal`, `Download PDF`, `Download Word`, `Print Proposal`) are rendered as accessible icon buttons with hover tooltips (`aria-label` and `title`).

## 8. Gemini Generation Performance & Timeout Control

- **Model & Generation Config**:
  - `GEMINI_MODEL = "gemini-3.6-flash"` is maintained as the primary active model.
  - `GenerateContentConfig` uses `thinking_config=types.ThinkingConfig(thinking_budget=1024)` to constrain reasoning overhead on complex proposal JSON generation.
  - Set `temperature=0.3` and `max_output_tokens=4096` to optimize response latency and structured accuracy.
- **Transport & Proxy Setup**:
  - Request-level `types.HttpOptions(client_args={"trust_env": False}, async_client_args={"trust_env": False}, timeout=120000)` prevents Windows system proxy/environment scanning delays.
- **Retry Policy & State Preservation**:
  - `APITimeoutError` / `504 DEADLINE_EXCEEDED` raises immediately without initiating duplicate 120s retry storms.
  - On API failure, `/grc/ai` preserves all user form inputs (`proposal_inputs`) in the rendered template.

## 9. Section-Level AI Proposal Editing ("Edit with AI")

- **Coexistence of Editing Capabilities**:
  - Every proposal section displays two distinct, separate action buttons: `[ Edit ]` (large rich text editor) and `[ ✨ Edit with AI ]` (section-scoped AI rewrite flow).
  - Manual editing and AI section editing operate completely independently without replacing or merging workflows.
- **Section-Scoped Backend Endpoint**:
  - Endpoint: `POST /grc/ai/proposal/edit-section` (`edit_section_ai_proposal`).
  - Receives `section_id`, `section_title`, `current_content`, `user_instruction`, and `proposal_context`.
  - Makes a focused, lightweight call to Google Gemini (`gemini-3.6-flash`) for ONLY the selected section. Does NOT invoke full proposal generation.
- **Dedicated AI Section Editor & Preview**:
  - Clicking `Edit with AI` takes the user to a dedicated AI Section Editor card (`#aiSectionEditorContainer`).
  - Displays current section content, user instruction prompt field, active fetch-based loading indicator, and an `AI-GENERATED VERSION` preview card.
- **Explicit Confirmation & Persistence**:
  - AI rewrites remain in temporary preview until the user clicks `Accept & Save`.
  - `Accept & Save` updates the target section and persists changes to SQLite (`business_proposals` table) / Flask session via `POST /grc/ai/proposal/update`.
  - Clicking `Keep Current` or `← Back to Proposal` discards unsaved AI rewrites and restores the proposal view.
- **Export & TOC Integrity**:
  - All exports (Download PDF, Download DOCX, Print) consume the updated proposal state.
  - Proposal TOC links, section IDs, and anchor hashes remain 100% intact.


