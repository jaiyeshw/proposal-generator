# 📑 Proposal Generator

### *Enterprise AI-Powered Proposal & Contract Lifecycle Platform*

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg?logo=python&logoColor=white)](https://python.org)
[![Flask](https://img.shields.io/badge/Flask-3.0%2B-black.svg?logo=flask&logoColor=white)](https://palletsprojects.com/p/flask/)
[![Google Gemini](https://img.shields.io/badge/Google%20Gemini-Flash-orange.svg?logo=google&logoColor=white)](https://ai.google.dev/)
[![ReportLab](https://img.shields.io/badge/ReportLab-PDF%20Engine-red.svg)](https://www.reportlab.com/)
[![python-docx](https://img.shields.io/badge/python--docx-Word%20Engine-blue.svg)](https://python-docx.readthedocs.io/)
[![SQLite3](https://img.shields.io/badge/SQLite3-Database-lightblue.svg?logo=sqlite&logoColor=white)](https://sqlite.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

**Proposal Generator** is an enterprise-grade platform for creating, customizing, managing, and tracking professional project proposals. Built for agencies, consultancies, IT service providers, business development teams, and independent professionals, it automates the end-to-end proposal lifecycle—from initial client discovery to structured AI generation, dynamic rich-text customization, and high-fidelity document exports.

---

## 💡 What is Proposal Generator?

Creating formal client proposals often involves a time-consuming, repetitive, and fragmented workflow:

* Deciphering client requirements across multiple documents
* Writing repetitive introductory, technical, and commercial boilerplate
* Defining scopes, deliverables, SLAs, and governance structures manually
* Formatting documents across word processors and fixing layout inconsistencies
* Manually tracking proposal revisions, client statuses, and deal pipelines

**Proposal Generator** solves this with an integrated, intelligent workflow:

```text
Traditional Workflow:
Requirements → Manual Drafting → Formatting Battles → Unstructured Revisions → Static PDF → Lost Pipeline Context

Proposal Generator:
Requirements → AI Proposal Engine → 19 Structured Sections → Live Edit / Edit with AI → Drag & Drop Reorder → PDF / DOCX Export → Pipeline Tracker
```

---

## ⚡ Key Features

### 🤖 AI-Powered Proposal Generation
* **Full Enterprise Scope**: Generates comprehensive 19-section commercial proposals tailored to specific client details, scope deliverables, budget, and delivery timelines using Google Gemini.
* **Context-Aware Drafting**: Translates concise inputs (e.g. project type, required deliverables, timeline constraints) into articulate, professional executive copy, technical methodologies, risk matrices, and commercial terms.

### 📊 Proposal Tracker & Pipeline Analytics
* **Centralized Proposal Repository**: Access and manage all generated proposals with persistent storage in SQLite.
* **Unique Proposal Identifiers**: Auto-generates indexed proposal tracking codes (`PRP-XXXXXX`) for auditability.
* **Proposal Status & Deal Outcome Tracking**: Monitor proposals across lifecycle states (`In Evaluation`, `Submitted`, `Approved`, `Rejected`) and track revenue conversion outcomes (`Open`, `Won`, `Lost`).
* **Expiration & Validity Tooltips**: Dynamic calculation of proposal validity windows and expiration alerts.
* **Search & Filter**: Real-time client search and status filtering across your entire proposal pipeline.

### 📝 Structured Proposal Editor
* **Modular Section Structure**: Every proposal is organized into distinct, navigable sections rather than a single monolithic text block.
* **Interactive Table of Contents**: Real-time sticky sidebar navigation with smooth anchor scrolling across all sections.
* **Rich Text Formatting Toolbar**: In-browser rich-text editor with support for Headings (H2, H3), Bold, Italic, Underline, Strikethrough, Bulleted Lists, Numbered Lists, Blockquotes, Code blocks, and Tables.

### 🧩 Custom Proposal Sections
* **Unlimited Custom Sections**: Add specialized sections on the fly (e.g. "Case Studies", "Dedicated Team Profiles", "Hardware Specifications") via the `+ Add Section` modal.
* **Custom Titles & Content**: Fully editable section headings and formatted body copy that seamlessly integrate into the document hierarchy and table of contents.

### ↕️ Drag-and-Drop Section Reordering
* **Intuitive HTML5 Reordering**: Reorganize the narrative and flow of your proposal by simply dragging section cards into new positions.
* **Dynamic Hierarchy Update**: Table of contents numbers and document section ordering update instantly on drop without requiring proposal regeneration.

### ✏️ Manual Editing & Inline Persistence
* **Direct Content Refinement**: Edit any section's text directly in the browser with live formatting previews.
* **Instant Persistence**: Section modifications are saved to the backend database immediately upon confirmation.

### ✨ Section-Level "Edit with AI"
* **Targeted Refinement**: Polish individual proposal sections with natural language instructions (e.g. *"Make this SLA section stricter with 99.9% uptime commitments"*, *"Summarize deliverables into bullet points"*, or *"Add SOC 2 compliance language"*).
* **Live Diff & Preview**: Compare original section content against the AI-generated version with one-click **Accept & Save** or **Keep Current** controls.

### 🎨 Multi-Template Architecture
* **Executive Design Templates**: Switch document styling across built-in presentation profiles:
  * **Standard Professional**: Clean, balanced executive styling optimized for formal corporate submittals.
  * **Corporate Test Template**: Modern, high-contrast layouts tailored for technical proposals.
  * **Editorial Design**: Sophisticated typography for creative agencies and consultancies.
* **Custom DOCX Template Support**: Upload organizational Word (`.docx`) template files to apply custom company styling.

### 📄 High-Fidelity Exports (PDF & DOCX)
* **Pixel-Perfect PDF Generation**: Server-side document compilation using ReportLab flowables, complete with running headers, footers, dynamic page counts (*"Page X of Y"*), and clean section pagination.
* **Editable Microsoft Word (`.docx`)**: Export complete proposals to native Word format with clean typography, styled tables, and formatted lists for offline client collaboration.
* **Print-Optimized Output**: Dedicated print styling (`@media print`) ensures flawless paper and browser PDF printing.

### 🖼️ Inline Asset & Image Management
* **Rich Diagram & Media Support**: Insert architecture diagrams, wireframes, and project mockups directly into proposal sections with persistent local storage.

---

## 🏗️ 19-Section Enterprise Proposal Architecture

Every generated proposal adheres to a standardized, comprehensive enterprise proposal structure:

| # | Section Name | Description |
| :---: | :--- | :--- |
| **01** | **Executive Summary** | High-level synthesis of client objectives, proposed engagement, and expected ROI. |
| **02** | **About the Vendor** | Firm background, core competencies, domain experience, and credibility indicators. |
| **03** | **Understanding of Requirement** | Detailed reflection of client problem statement, goals, and technical context. |
| **04** | **Proposed Solution & Deliverables** | Architectural blueprint, methodologies, and core project deliverables. |
| **05** | **Scope of Work (In Scope)** | Exhaustive list of confirmed activities, modules, and project work streams. |
| **06** | **Out of Scope** | Clear boundaries specifying excluded items to eliminate scope creep. |
| **07** | **Deployment & Implementation Approach** | Phase-by-phase execution methodology, milestones, and rollout strategy. |
| **08** | **Prerequisites & Client Obligations** | Dependencies, stakeholder access, approvals, and inputs required from client. |
| **09** | **Infrastructure Requirements** | Hardware, cloud hosting, network, bandwidth, and environment prerequisites. |
| **10** | **Information Security & Compliance** | Data protection policies, encryption standards, NDA adherence, and compliance frameworks. |
| **11** | **Training & Change Management** | User enablement sessions, documentation handover, and adoption roadmap. |
| **12** | **Support & SLA Commitments** | Post-launch warranties, response times, incident severities, and maintenance tiers. |
| **13** | **Commercials & Pricing Breakdown** | Itemized investment tables, milestone costs, and optional engagement add-ons. |
| **14** | **License Term & Renewal** | Software licensing agreements, subscription durations, and renewal conditions. |
| **15** | **Payment Terms & Milestones** | Invoicing schedules, payment methods, net terms, and currency designations. |
| **16** | **Legal & Commercial Terms** | Intellectual property rights, confidentiality, liability limits, and dispute governance. |
| **17** | **Project Governance & Escalation** | Communication matrix, reporting rhythms, steering committee, and escalation paths. |
| **18** | **Acceptance Criteria & Sign-off** | Formal criteria for deliverable verification and authorized signatory blocks. |
| **19** | **Risks & Assumptions** | Key operational assumptions, risk identification, and proactive mitigation plans. |

---

## 🔄 End-to-End Workflow

```text
┌────────────────────────────────────────┐
│         1. User Authentication         │
│   (Secure session login via /login)    │
└───────────────────┬────────────────────┘
                    │
┌───────────────────▼────────────────────┐
│          2. Proposal Tracker           │
│   (Review active proposals & metrics)  │
└───────────────────┬────────────────────┘
                    │
┌───────────────────▼────────────────────┐
│      3. Project Discovery Inputs       │
│ (Client name, scope, budget, timeline) │
└───────────────────┬────────────────────┘
                    │
┌───────────────────▼────────────────────┐
│      4. AI Proposal Generation         │
│  (Gemini builds 19 enterprise sections)│
└───────────────────┬────────────────────┘
                    │
┌───────────────────▼────────────────────┐
│   5. Review & Interactive Editing      │
│  ├── Manual Rich Text Editing          │
│  ├── Section-Level "Edit with AI"      │
│  ├── Insert Custom Sections            │
│  └── Drag & Drop Section Reordering    │
└───────────────────┬────────────────────┘
                    │
┌───────────────────▼────────────────────┐
│         6. Export & Lifecycle          │
│  ├── Download ReportLab PDF            │
│  ├── Download Microsoft Word (.docx)   │
│  ├── Print-Optimized Formatting        │
│  └── Update Status / Deal Outcome      │
└────────────────────────────────────────┘
```

---

## 🛠️ Technology Stack

| Layer | Technologies |
| :--- | :--- |
| **Backend Framework** | [Flask](https://palletsprojects.com/p/flask/) (Python 3.10+) |
| **AI Engine** | [Google Gemini Flash](https://ai.google.dev/) via official `google-genai` SDK |
| **Document Generation** | [ReportLab](https://www.reportlab.com/) (PDF engine) & [python-docx](https://python-docx.readthedocs.io/) (Word engine) |
| **Database** | SQLite3 (`agentscan.db`) with native schema migrations |
| **Frontend** | Vanilla JavaScript, Jinja2 Templates, Linear/Vercel-inspired CSS Design System |
| **Data Analytics** | Chart.js for Proposal Tracker pipeline visualization |
| **Security & Auth** | Werkzeug PBKDF2 Password Hashing, Session Protection, RBAC Decorators |

---

## 🚀 Quick Start

### 1. Prerequisites
- **Python 3.10** or higher
- **Google Gemini API Key** ([Get your API key](https://aistudio.google.com/))

### 2. Installation

Clone the repository and install required dependencies:

```bash
git clone https://github.com/yourusername/proposal-generator.git
cd proposal-generator
pip install -r requirements.txt
```

### 3. Configuration

Create your environment configuration file:

```bash
cp .env.example .env
```

Edit `.env` and add your Google Gemini API key:

```ini
# Google Gemini API Key for AI Proposal Generation
GEMINI_API_KEY=your_gemini_api_key_here
```

### 4. Run the Application

Start the Flask server:

```bash
python app.py
```

Access the application in your browser:

```text
http://127.0.0.1:5000
```

#### Default Initial Credentials:
* **Username:** `admin@agentscan.local`
* **Password:** `admin`

---

## 📁 Repository Structure

```
├── app.py                         # Main Flask application, routes, AI handlers, and export pipelines
├── proposal_template_registry.py  # Centralized template registry & ReportLab PDF renderers
├── requirements.txt               # Application dependencies (Flask, google-genai, reportlab, python-docx)
├── .env.example                   # Environment configuration template
├── README.md                      # Platform documentation
├── templates/                     # Jinja2 SSR HTML templates
│   ├── base.html                  # Global shell, navigation, header, and script dependencies
│   ├── login.html                 # Authentication interface
│   ├── tracker.html               # Proposal Tracker dashboard & analytics
│   ├── ai_grc.html                # Two-State AI Proposal workspace (Discovery & Review)
│   ├── templates_library.html     # Proposal Template Library & custom template manager
│   └── _business_proposal_doc.html# Modular proposal document renderer, editor & controls
└── static/                        # CSS stylesheets, client-side JS, and uploads
    ├── styles.css                 # Comprehensive design system (dark/light tokens, components)
    ├── app-loader.js              # Asynchronous task loader and progress indicators
    └── uploads/                   # Stored proposal image assets and custom DOCX templates
```

---

## 🔒 Security & Best Practices

* **Server-Side API Key Protection**: Google Gemini API keys stay strictly on the server and are never exposed to the client.
* **Role-Based Access Control**: All proposal creation, modification, deletion, and export endpoints are protected by `@login_required` authentication decorators.
* **Local Data Sovereignty**: All proposals, templates, and engagement records are stored in a local SQLite database under your direct control.
* **Input Sanitization**: Rich-text HTML content and uploaded file assets are sanitized before storage and rendering.

---

## 📄 License

This project is licensed under the **MIT License** — see the [LICENSE](LICENSE) file for details.
