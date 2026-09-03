/**
 * Proposal Generator Global Loading System
 * Task-Based Loading Architecture managing navigation, async operations,
 * form submissions, AI generation, and document exports.
 * 
 * CORE ARCHITECTURE PRINCIPLE:
 * Loading state display is strictly tied to active asynchronous operations (Active Task Registry).
 * Fixed display timers, maximum display durations, or simulated progress do NOT determine completion.
 * LOADER VISIBLE <=> Active Tasks > 0
 * LOADER HIDDEN  <=> Active Tasks == 0
 */

(function () {
  'use strict';

  class GlobalLoaderManager {
    constructor() {
      // Active Task Registry: Map<taskId, { title, subtitle, timestamp }>
      this.activeTasks = new Map();
      this.legacyTaskCounter = 0;
      this.legacyTaskStack = [];

      this.overlayEl = null;
      this.titleEl = null;
      this.subtitleEl = null;
      this.hideTimer = null;
      this.isShowing = false;
      this.defaultTitle = 'Loading...';
      this.defaultSubtitle = 'Please wait while the system processes your request.';

      // Bind public API methods
      this.init = this.init.bind(this);
      this.startTask = this.startTask.bind(this);
      this.stopTask = this.stopTask.bind(this);
      this.failTask = this.failTask.bind(this);
      this.show = this.show.bind(this);
      this.hide = this.hide.bind(this);
      this.setMessage = this.setMessage.bind(this);
      this.reset = this.reset.bind(this);
      this.trackPromise = this.trackPromise.bind(this);
      this.runWithTask = this.runWithTask.bind(this);
      this.runWithLoader = this.runWithLoader.bind(this);
    }

    init() {
      this.overlayEl = document.getElementById('globalLoadingOverlay');
      this.titleEl = document.getElementById('globalLoadingTitle');
      this.subtitleEl = document.getElementById('globalLoadingSubtitle');

      if (!this.overlayEl) {
        console.warn('GlobalLoader: #globalLoadingOverlay element not found in DOM.');
        return;
      }

      // Check DOM readiness to clear initial page loading state
      if (document.readyState === 'complete') {
        this._updateVisibility();
      } else {
        window.addEventListener('load', () => {
          this._updateVisibility();
        });
        window.addEventListener('DOMContentLoaded', () => {
          this._updateVisibility();
        });
      }

      // Attach global listeners for errors, navigation & fetch interception
      this._attachEventListeners();
      this._interceptFetch();
    }

    /**
     * Start/Register a named task in the active task registry.
     * @param {string} taskId - Unique task identifier
     * @param {string} [title] - Contextual title
     * @param {string} [subtitle] - Contextual subtitle
     */
    startTask(taskId, title, subtitle) {
      if (!taskId) {
        this.legacyTaskCounter++;
        taskId = 'task-auto-' + this.legacyTaskCounter + '-' + Date.now();
      }

      const taskMeta = {
        id: taskId,
        title: title || this.defaultTitle,
        subtitle: subtitle !== undefined ? subtitle : this.defaultSubtitle,
        timestamp: Date.now()
      };

      this.activeTasks.set(taskId, taskMeta);
      this._updateVisibility();
      return taskId;
    }

    /**
     * Stop/Unregister a named task from the active task registry.
     * @param {string} taskId - Identifier of task that has completed
     */
    stopTask(taskId) {
      if (taskId) {
        this.activeTasks.delete(taskId);
      } else if (this.legacyTaskStack.length > 0) {
        const lastId = this.legacyTaskStack.pop();
        this.activeTasks.delete(lastId);
      } else {
        // Fallback for legacy hide() without ID: remove oldest auto task
        for (const [key] of this.activeTasks) {
          if (key.startsWith('task-auto-') || key.startsWith('legacy-')) {
            this.activeTasks.delete(key);
            break;
          }
        }
      }
      this._updateVisibility();
    }

    /**
     * Fail/Stop a task when an operation encounters an error.
     * @param {string} taskId - Identifier of failed task
     * @param {Error|string} [error] - Error context
     */
    failTask(taskId, error) {
      if (error) {
        console.error(`AgentScan Task [${taskId}] Failed:`, error);
      }
      this.stopTask(taskId);
    }

    /**
     * Legacy wrapper for startTask supporting reference counting.
     * @param {string} [title]
     * @param {string} [subtitle]
     * @returns {string} taskId
     */
    show(title, subtitle) {
      this.legacyTaskCounter++;
      const taskId = 'legacy-' + this.legacyTaskCounter + '-' + Date.now();
      this.legacyTaskStack.push(taskId);
      this.startTask(taskId, title, subtitle);
      return taskId;
    }

    /**
     * Legacy wrapper for stopTask.
     * @param {boolean} [force=false] - Force clear all tasks and immediately hide overlay
     */
    hide(force = false) {
      if (force) {
        this.reset();
        return;
      }
      this.stopTask();
    }

    /**
     * Execute an async function wrapped in a task lifecycle.
     * @param {string} taskId
     * @param {string} title
     * @param {string} subtitle
     * @param {Function} asyncFn
     */
    async runWithTask(taskId, title, subtitle, asyncFn) {
      this.startTask(taskId, title, subtitle);
      try {
        return await asyncFn();
      } catch (err) {
        this.failTask(taskId, err);
        throw err;
      } finally {
        this.stopTask(taskId);
      }
    }

    /**
     * Legacy async wrapper.
     */
    async runWithLoader(asyncFn, title, subtitle) {
      const taskId = 'run-' + Date.now() + '-' + Math.random().toString(36).substring(2, 7);
      return this.runWithTask(taskId, title, subtitle, asyncFn);
    }

    /**
     * Track a Promise with a task lifecycle.
     * @param {Promise} promise
     * @param {string} [title]
     * @param {string} [subtitle]
     * @param {string} [taskId]
     */
    trackPromise(promise, title, subtitle, taskId) {
      const id = taskId || ('promise-' + Date.now() + '-' + Math.random().toString(36).substring(2, 7));
      this.startTask(id, title, subtitle);
      return Promise.resolve(promise).finally(() => {
        this.stopTask(id);
      });
    }

    setTitle(text) {
      if (this.titleEl) {
        this.titleEl.textContent = text || this.defaultTitle;
      }
    }

    setSubtitle(text) {
      if (this.subtitleEl) {
        this.subtitleEl.textContent = text || '';
      }
    }

    setMessage(title, subtitle) {
      if (title) this.setTitle(title);
      if (subtitle !== undefined) this.setSubtitle(subtitle);
    }

    /**
     * Emergency reset: clears all active tasks and hides loader immediately.
     */
    reset() {
      this.activeTasks.clear();
      this.legacyTaskStack = [];
      if (this.hideTimer) {
        clearTimeout(this.hideTimer);
        this.hideTimer = null;
      }
      if (this.overlayEl) {
        this.overlayEl.classList.remove('is-visible');
        this.overlayEl.style.display = 'none';
      }
      this.isShowing = false;
      this.setTitle(this.defaultTitle);
      this.setSubtitle(this.defaultSubtitle);

      // Re-enable any disabled submit buttons
      document.querySelectorAll('.btn-is-loading').forEach((btn) => {
        btn.classList.remove('btn-is-loading');
        btn.removeAttribute('disabled');
      });
    }

    /**
     * Internal method: derives loader visibility and displayed message directly from active tasks.
     * Active Tasks > 0 => Visible
     * Active Tasks == 0 => Hidden immediately (with smooth CSS fadeout)
     */
    _updateVisibility() {
      if (!this.overlayEl) return;

      if (this.activeTasks.size > 0) {
        if (this.hideTimer) {
          clearTimeout(this.hideTimer);
          this.hideTimer = null;
        }

        // Get active task metadata (most recently added task)
        const taskEntries = Array.from(this.activeTasks.values());
        const currentTask = taskEntries[taskEntries.length - 1];

        this.setTitle(currentTask.title);
        this.setSubtitle(currentTask.subtitle);

        if (!this.isShowing) {
          this.isShowing = true;
          this.overlayEl.style.display = 'flex';
          void this.overlayEl.offsetHeight; // Force repaint
          this.overlayEl.classList.add('is-visible');
        }
      } else {
        // Active tasks = 0: Hide immediately
        if (this.isShowing) {
          this.overlayEl.classList.remove('is-visible');
          if (this.hideTimer) clearTimeout(this.hideTimer);
          
          this.hideTimer = setTimeout(() => {
            if (this.activeTasks.size === 0) {
              this.overlayEl.style.display = 'none';
              this.isShowing = false;
              this.setTitle(this.defaultTitle);
              this.setSubtitle(this.defaultSubtitle);
            }
          }, 180); // Short anti-flicker CSS transition time
        }
      }
    }

    _interceptFetch() {
      if (typeof window.fetch !== 'function' || window._fetchIntercepted) return;
      const origFetch = window.fetch;
      const manager = this;
      window.fetch = function (...args) {
        const opts = args[1] || {};
        if (opts.noLoader || (opts.headers && (opts.headers['X-No-Loader'] || opts.headers['x-no-loader']))) {
          return origFetch.apply(this, args);
        }
        const taskId = 'fetch-' + (typeof args[0] === 'string' ? args[0] : 'request') + '-' + Date.now();
        manager.startTask(taskId);
        return origFetch.apply(this, args).finally(() => {
          manager.stopTask(taskId);
        });
      };
      window._fetchIntercepted = true;
    }

    _attachEventListeners() {
      // 1. Reset on page restore from cache
      window.addEventListener('pageshow', (evt) => {
        if (evt.persisted) {
          this.reset();
        }
      });

      // 2. Clear loader on uncaught runtime errors to prevent stuck states
      window.addEventListener('error', (err) => {
        console.error('AgentScan Runtime Error caught:', err);
        this.reset();
      });
      window.addEventListener('unhandledrejection', (evt) => {
        console.error('AgentScan Unhandled Rejection caught:', evt.reason);
        this.reset();
      });

      // 3. Intercept link navigation clicks
      document.addEventListener('click', (evt) => {
        const link = evt.target.closest('a');
        if (!link) return;

        const href = link.getAttribute('href');
        if (!href) return;

        const urlHashIndex = href.indexOf('#');
        const isCurrentPageAnchor = (urlHashIndex !== -1) && (
          href.startsWith('#') ||
          href.substring(0, urlHashIndex) === window.location.pathname ||
          href.substring(0, urlHashIndex) === (window.location.pathname + window.location.search) ||
          link.classList.contains('toc-item')
        );

        // Ignore hash links, in-page anchor navigation, TOC items, section anchors, javascript:, mailto:, tel:, target="_blank", or download links
        if (
          href.startsWith('#') ||
          href.includes('#section-') ||
          isCurrentPageAnchor ||
          link.classList.contains('toc-item') ||
          link.classList.contains('workspace-toc-item') ||
          link.getAttribute('data-no-loader') === 'true' ||
          href.startsWith('javascript:') ||
          href.startsWith('mailto:') ||
          href.startsWith('tel:') ||
          link.getAttribute('target') === '_blank' ||
          link.hasAttribute('download') ||
          evt.ctrlKey || evt.metaKey || evt.shiftKey || evt.altKey
        ) {
          return;
        }

        let title = 'Loading page...';
        let subtitle = 'Navigating to selected view.';

        if (href.includes('/dashboard')) {
          title = 'Loading Executive Overview...';
          subtitle = 'Fetching metrics, proposal status, and active engagements.';
        } else if (href.includes('/grc/assessments')) {
          title = 'Loading Customer Assessments...';
          subtitle = 'Fetching assessments and proposal statistics.';
        } else if (href.includes('/grc/ai') || href.includes('/ai_grc')) {
          title = 'Loading AI Proposal Workspace...';
          subtitle = 'Initializing AI engine and proposal configuration.';
        } else if (href.includes('/grc/new') || href.includes('/batches/new')) {
          title = 'Preparing Setup View...';
          subtitle = 'Loading profile configuration workspace.';
        } else if (href.includes('/logout')) {
          title = 'Signing Out...';
          subtitle = 'Terminating session safely.';
        }

        this.startTask('navigation-' + href, title, subtitle);
      });

      // 4. Intercept Form Submissions Globally (Task-based handling for Downloads, AI, etc.)
      document.addEventListener('submit', (evt) => {
        const form = evt.target;
        if (!form || form.getAttribute('data-no-loader') === 'true') {
          return; // Skip inline search or local filter forms
        }

        const action = form.getAttribute('action') || window.location.pathname;
        const submitBtn = form.querySelector('button[type="submit"], input[type="submit"]');
        const customTitle = form.getAttribute('data-loading-title');
        const customSubtitle = form.getAttribute('data-loading-subtitle');

        let title = customTitle || 'Processing Request...';
        let subtitle = customSubtitle || 'Please wait while the system completes your request.';

        if (!customTitle) {
          if (action.includes('/login')) {
            title = 'Authenticating Session...';
            subtitle = 'Verifying credentials and access permissions.';
          } else if (action.includes('/grc/new') || action.includes('/new')) {
            title = 'Creating Assessment Profile...';
            subtitle = 'Saving customer assessment metadata.';
          } else if (action.includes('/delete')) {
            title = 'Deleting Record...';
            subtitle = 'Cleaning up database entries and related files.';
          } else if (action.includes('/grc/ai/proposal/download') || action.includes('/download')) {
            title = 'Generating Document...';
            subtitle = 'Compiling complete enterprise proposal export.';
          } else if (action.includes('/grc/ai')) {
            title = '⚡ Generating Business Proposal...';
            subtitle = 'AI is analyzing client requirements and generating a business proposal.';
          }
        }

        // SPECIAL HANDLING FOR FILE DOWNLOADS (PDF, DOCX, etc.)
        // Standard HTML form POST for file attachment does not cause page unload.
        // We handle download via fetch() blob to tightly bind loader lifecycle to actual file delivery.
        if (action.includes('/download') || form.getAttribute('data-download-form') === 'true') {
          evt.preventDefault();
          const taskId = 'download-task-' + Date.now();

          if (submitBtn) {
            submitBtn.classList.add('btn-is-loading');
            submitBtn.setAttribute('disabled', 'disabled');
          }

          this.startTask(taskId, title, subtitle);

          const formData = new FormData(form);
          const requestMethod = (form.getAttribute('method') || 'POST').toUpperCase();

          fetch(action, {
            method: requestMethod,
            body: formData,
            headers: {
              'X-Requested-With': 'XMLHttpRequest'
            }
          })
            .then(async (response) => {
              const contentType = (response.headers.get('Content-Type') || '').toLowerCase();
              if (!response.ok) {
                if (contentType.includes('application/json')) {
                  const errorJson = await response.json().catch(() => ({}));
                  throw new Error(errorJson.error || errorJson.message || `Download request failed with status ${response.status}`);
                } else if (contentType.includes('text/html')) {
                  throw new Error('Document endpoint returned an HTML page instead of binary file. Please verify proposal data.');
                } else {
                  const errorText = await response.text();
                  throw new Error(errorText || `Download request failed with status ${response.status}`);
                }
              }

              if (contentType.includes('text/html')) {
                throw new Error('Unexpected HTML response received from document download endpoint.');
              }
              
              const blob = await response.blob();
              let filename = 'business_proposal';
              
              const disposition = response.headers.get('Content-Disposition');
              if (disposition && disposition.includes('filename=')) {
                filename = disposition.split('filename=')[1].replace(/["']/g, '').trim();
              } else if (action.includes('pdf')) {
                filename = 'business_proposal.pdf';
              } else if (action.includes('docx')) {
                filename = 'business_proposal.docx';
              }

              // Trigger native browser download save dialog
              const blobUrl = URL.createObjectURL(blob);
              const downloadAnchor = document.createElement('a');
              downloadAnchor.href = blobUrl;
              downloadAnchor.download = filename;
              downloadAnchor.style.display = 'none';
              document.body.appendChild(downloadAnchor);
              downloadAnchor.click();
              downloadAnchor.remove();

              setTimeout(() => URL.revokeObjectURL(blobUrl), 2000);
            })
            .catch((err) => {
              console.error('Document Download Error:', err);
              alert('Document Download Failed: ' + (err.message || 'An error occurred during file generation.'));
            })
            .finally(() => {
              if (submitBtn) {
                submitBtn.classList.remove('btn-is-loading');
                submitBtn.removeAttribute('disabled');
              }
              // Unregister download task immediately: LOADER HIDDEN UPON COMPLETION
              this.stopTask(taskId);
            });

          return;
        }

        // Standard form submit: Start task and apply submit button loading state
        const formTaskId = 'form-submit-' + Date.now();
        this.startTask(formTaskId, title, subtitle);

        if (submitBtn) {
          submitBtn.classList.add('btn-is-loading');
          submitBtn.setAttribute('disabled', 'disabled');
        }
      });
    }
  }

  // Instantiate and export instance globally
  const instance = new GlobalLoaderManager();
  window.AppLoader = instance;

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', instance.init);
  } else {
    instance.init();
  }
})();

