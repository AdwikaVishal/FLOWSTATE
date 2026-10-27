/*
 * Northwind Accounts Payable — deterministic controlled demo.
 *
 * The page also contains a small, privacy-aware interaction recorder used by
 * FLOWSTATE's learning studio. It never records password, token, card or other
 * sensitive values: those are replaced before an event leaves the page.
 */
(function () {
  'use strict'

  const INVOICES = {
    'INV-1001': {
      id: 'INV-1001',
      supplierId: 'SUP-77',
      supplierName: 'Northwind Supply',
      amountCents: 125000,
      poCents: 125000,
      status: 'SUBMITTED',
    },
    'INV-1002': {
      id: 'INV-1002',
      supplierId: 'SUP-88',
      supplierName: 'Contoso Parts',
      amountCents: 99900,
      poCents: 80000,
      status: 'SUBMITTED',
    },
    'INV-1003': {
      id: 'INV-1003',
      supplierId: 'SUP-77',
      supplierName: 'Northwind Supply',
      amountCents: 450000,
      poCents: 450000,
      status: 'APPROVED',
    },
  }

  const app = document.getElementById('app')
  const queueView = document.getElementById('queue-view')
  const invoiceView = document.getElementById('invoice-view')
  const missingView = document.getElementById('missing-view')
  const form = document.getElementById('lookup-form')
  const supplierInput = document.getElementById('supplier-id')
  const invoiceInput = document.getElementById('invoice-id')
  const loadButton = document.getElementById('load-invoice')
  const backButton = document.getElementById('back-to-queue')
  const missingBack = document.getElementById('missing-back')
  const summary = document.getElementById('invoice-summary')
  const validation = document.getElementById('validation-result')
  const validationTitle = document.getElementById('validation-title')
  const validationDetail = document.getElementById('validation-detail')
  const approvalPanel = document.getElementById('approval-panel')
  const prepareApproval = document.getElementById('prepare-approval')
  const lookupFeedback = document.getElementById('lookup-feedback')
  const recordingBanner = document.getElementById('recording-banner')
  const pageMarker = document.getElementById('page-marker')

  const params = new URLSearchParams(window.location.search)
  const variant = params.get('variant') === 'changed' ? 'changed' : 'default'
  const recordingEnabled = params.get('capture') === '1' || params.get('capture_session') !== null

  const view = { invoice: null, validated: false, validationState: null }

  function money(cents) {
    return '$' + (cents / 100).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  }

  function show(viewName) {
    queueView.hidden = viewName !== 'queue'
    invoiceView.hidden = viewName !== 'invoice'
    missingView.hidden = viewName !== 'missing'
    app.dataset.page = viewName
    pageMarker.textContent = viewName
  }

  function applyVariant() {
    if (variant !== 'changed') return
    /*
     * Simulates a page-structure change for deterministic drift tests: ids,
     * labels, accessible names and test ids all change. A recorded workflow
     * must fail safely instead of clicking the wrong thing.
     */
    supplierInput.id = 'supplierReferenceField'
    invoiceInput.id = 'invoiceReferenceField'
    document.querySelector('label[for="supplier-id"]').htmlFor = 'supplierReferenceField'
    document.querySelector('label[for="invoice-id"]').htmlFor = 'invoiceReferenceField'
    document.querySelector('label[for="supplierReferenceField"]').textContent = 'Supplier reference'
    document.querySelector('label[for="invoiceReferenceField"]').textContent = 'Invoice reference'
    supplierInput.placeholder = 'Supplier reference'
    invoiceInput.placeholder = 'Invoice reference'
    loadButton.id = 'retrieve-invoice'
    loadButton.textContent = 'Retrieve invoice'
    loadButton.removeAttribute('data-testid')
    const validateButton = document.getElementById('validate-invoice')
    validateButton.id = 'check-invoice-totals'
    validateButton.textContent = 'Check invoice totals'
    validateButton.removeAttribute('data-testid')
    summary.removeAttribute('data-testid')
  }

  function renderSummary(invoice) {
    summary.dataset.invoiceId = invoice.id
    summary.dataset.supplierId = invoice.supplierId
    summary.dataset.status = invoice.status
    summary.dataset.amount = String(invoice.amountCents)
    summary.dataset.poAmount = String(invoice.poCents)
    document.getElementById('summary-invoice').textContent = invoice.id
    document.getElementById('summary-supplier').textContent = invoice.supplierId + ' · ' + invoice.supplierName
    document.getElementById('summary-status').textContent = invoice.status
    document.getElementById('summary-amount').textContent = money(invoice.amountCents)
    document.getElementById('summary-po').textContent = money(invoice.poCents)
  }

  function resetValidation() {
    view.validated = false
    view.validationState = null
    validation.dataset.state = ''
    validationTitle.textContent = 'Not validated yet'
    validationDetail.textContent =
      'Run validation to compare the invoice with its purchase order.'
    approvalPanel.hidden = true
    prepareApproval.disabled = true
  }

  function routeToQueue() {
    window.location.hash = '#/queue'
  }

  function routeToInvoice(invoiceId) {
    window.location.hash = '#/invoice/' + encodeURIComponent(invoiceId)
  }

  function routeToMissing(invoiceId) {
    window.location.hash = '#/missing/' + encodeURIComponent(invoiceId || '')
  }

  function renderRoute() {
    const hash = window.location.hash || '#/queue'
    const parts = hash.replace(/^#\/?/, '').split('/')
    const route = parts[0] || 'queue'

    if (route === 'invoice') {
      const invoice = INVOICES[decodeURIComponent(parts[1] || '')]
      if (invoice) {
        view.invoice = invoice
        renderSummary(invoice)
        if (!view.validated) resetValidation()
        show('invoice')
        document.getElementById('invoice-heading').textContent = 'Invoice ' + invoice.id
        return
      }
    }

    if (route === 'missing') {
      document.getElementById('missing-detail').textContent =
        'No invoice matches ' + decodeURIComponent(parts[1] || 'that identifier') + ' in this demo.'
      show('missing')
      document.querySelector('#missing-view h1').focus?.()
      return
    }

    view.invoice = null
    resetValidation()
    show('queue')
    document.getElementById('queue-heading').textContent = 'Find an invoice to review'
  }

  function loadInvoice(event) {
    event.preventDefault()
    const supplierId = supplierInput.value.trim().toUpperCase()
    const invoiceId = invoiceInput.value.trim().toUpperCase()
    lookupFeedback.classList.remove('is-error')

    if (!supplierId || !invoiceId) {
      lookupFeedback.textContent = 'Supplier ID and invoice ID are required.'
      lookupFeedback.classList.add('is-error')
      return
    }

    const invoice = INVOICES[invoiceId]
    if (!invoice) {
      lookupFeedback.textContent = 'No fixture matches ' + invoiceId + '.'
      lookupFeedback.classList.add('is-error')
      routeToMissing(invoiceId)
      return
    }

    lookupFeedback.textContent = ''
    routeToInvoice(invoice.id)
    // The hash change may be a no-op when the same invoice is already open.
    renderRoute()
  }

  function validateInvoice() {
    const invoice = view.invoice
    if (!invoice) return
    const supplierId = supplierInput.value.trim().toUpperCase()
    const mismatchedSupplier = supplierId && supplierId !== invoice.supplierId
    const mismatchedAmount = invoice.amountCents !== invoice.poCents

    view.validated = true
    if (mismatchedSupplier) {
      view.validationState = 'DISCREPANCY'
      validationTitle.textContent = 'Supplier mismatch'
      validationDetail.textContent =
        'The invoice belongs to ' + invoice.supplierId + ', not ' + supplierId + '.'
    } else if (mismatchedAmount) {
      view.validationState = 'DISCREPANCY'
      validationTitle.textContent = 'Amount discrepancy'
      validationDetail.textContent =
        'Invoice amount ' + money(invoice.amountCents) + ' does not match purchase order ' + money(invoice.poCents) + '.'
    } else {
      view.validationState = 'VALID'
      validationTitle.textContent = 'Validation passed'
      validationDetail.textContent =
        'Totals match the purchase order for ' + invoice.supplierName + ' (' + invoice.supplierId + ').'
    }
    validation.dataset.state = view.validationState
    prepareApproval.disabled = view.validationState !== 'VALID'
  }

  function prepareApprovalPanel() {
    if (view.validationState !== 'VALID') return
    approvalPanel.hidden = false
  }

  form.addEventListener('submit', loadInvoice)
  backButton.addEventListener('click', routeToQueue)
  missingBack.addEventListener('click', routeToQueue)
  document.getElementById('validate-invoice').addEventListener('click', validateInvoice)
  prepareApproval.addEventListener('click', prepareApprovalPanel)
  window.addEventListener('hashchange', renderRoute)

  // ---------------------------------------------------------------- recorder

  const SENSITIVE = /(pass(word|wd)?|secret|token|auth|api[_-]?key|card|cvv|cvc|iban|account|ssn)/i
  const recorder = {
    enabled: Boolean(recordingEnabled),
    events: [],
    sequence: 0,
    startedAt: Date.now(),
    setEnabled(value) {
      recorder.enabled = Boolean(value)
      recordingBanner.hidden = !recorder.enabled
    },
  }
  window.__flowstateRecorder = recorder
  window.__flowstateCaptured = recorder.events
  recordingBanner.hidden = !recorder.enabled

  function labelFor(element) {
    if (element.getAttribute('aria-label')) return element.getAttribute('aria-label').trim()
    if (element.id) {
      const label = document.querySelector('label[for="' + CSS.escape(element.id) + '"]')
      if (label) return label.textContent.trim()
    }
    const wrapping = element.closest('label')
    if (wrapping) return wrapping.textContent.trim()
    if (element.tagName === 'BUTTON' || element.tagName === 'A') return element.textContent.trim()
    return ''
  }

  function roleFor(element) {
    const explicit = element.getAttribute('role')
    if (explicit) return explicit
    const tag = element.tagName.toLowerCase()
    if (tag === 'button') return 'button'
    if (tag === 'a') return 'link'
    if (tag === 'select') return 'combobox'
    if (tag === 'textarea') return 'textbox'
    if (tag === 'input') {
      const type = (element.getAttribute('type') || 'text').toLowerCase()
      if (type === 'checkbox') return 'checkbox'
      if (type === 'radio') return 'radio'
      if (type === 'submit' || type === 'button' || type === 'reset') return 'button'
      return 'textbox'
    }
    return ''
  }

  function cssPath(element) {
    if (element.id) return '#' + CSS.escape(element.id)
    if (element.getAttribute('data-testid')) {
      return '[data-testid="' + element.getAttribute('data-testid') + '"]'
    }
    if (element.getAttribute('name')) {
      return element.tagName.toLowerCase() + '[name="' + element.getAttribute('name') + '"]'
    }
    const tag = element.tagName.toLowerCase()
    const parent = element.parentElement
    if (!parent) return tag
    const siblings = Array.from(parent.children).filter((item) => item.tagName === element.tagName)
    return cssPath(parent) + ' > ' + tag + ':nth-of-type(' + (siblings.indexOf(element) + 1) + ')'
  }

  function locatorFor(element) {
    const tag = element.tagName.toLowerCase()
    return {
      tag,
      test_id: element.getAttribute('data-testid') || '',
      role: roleFor(element),
      name: labelFor(element),
      label: element.id
        ? (document.querySelector('label[for="' + CSS.escape(element.id) + '"]') || {}).textContent?.trim() || ''
        : '',
      placeholder: element.getAttribute('placeholder') || '',
      text: (element.textContent || '').trim().slice(0, 160),
      css: cssPath(element),
      input_name: element.getAttribute('name') || '',
      param: element.getAttribute('data-param') || '',
    }
  }

  function snapshot() {
    const page = app.dataset.page || 'queue'
    const state = {
      page,
      invoice_id: summary.dataset.invoiceId || null,
      supplier_id: summary.dataset.supplierId || null,
      status: summary.dataset.status || null,
      amount_cents: summary.dataset.amount ? Number(summary.dataset.amount) : null,
      po_amount_cents: summary.dataset.poAmount ? Number(summary.dataset.poAmount) : null,
      validation: validation.dataset.state || null,
    }
    return {
      url: window.location.href,
      route: window.location.hash || '#/queue',
      page,
      heading: (document.querySelector('h1') || {}).textContent?.trim() || '',
      state,
    }
  }

  function describe(snap) {
    if (!snap) return ''
    const state = snap.state || {}
    if (state.validation === 'VALID') return 'Validation passed for ' + (state.invoice_id || 'the invoice')
    if (state.validation === 'DISCREPANCY') return 'Validation reported a discrepancy'
    if (state.page === 'invoice') return 'Invoice ' + (state.invoice_id || '') + ' is open'
    if (state.page === 'missing') return 'The demo reported that the invoice was not found'
    return 'Invoice queue ' + (snap.route || '')
  }

  function record(kind, element, value, pageBefore, pageAfter, extra) {
    if (!recorder.enabled) return
    const locator = locatorFor(element)
    const sensitive = Boolean(
      (element.getAttribute('type') || '').toLowerCase() === 'password' ||
        SENSITIVE.test(locator.input_name) ||
        SENSITIVE.test(locator.name) ||
        element.getAttribute('data-sensitive') === 'true',
    )
    const event = {
      client_sequence: recorder.sequence++,
      kind,
      locator,
      accessible_name: locator.name,
      value: sensitive ? null : (value ?? null),
      sensitive,
      redacted_fields: sensitive ? ['value'] : [],
      param: locator.param || locator.input_name || '',
      page_before: pageBefore,
      page_after: pageAfter,
      observed_result: describe(pageAfter),
      timestamp_offset_ms: Date.now() - recorder.startedAt,
      ...(extra || {}),
    }
    recorder.events.push(event)
    try {
      if (window.parent && window.parent !== window) {
        window.parent.postMessage({ source: 'flowstate-demo', type: 'capture-event', event }, '*')
      }
    } catch (error) {
      // A sandboxed parent is not fatal: the event remains in the local buffer.
    }
  }

  document.addEventListener(
    'click',
    function (event) {
      const element = event.target.closest('button, a, [role="button"], [data-recordable]')
      if (!element || element.disabled) return
      const before = snapshot()
      window.setTimeout(function () {
        record('CLICK', element, null, before, snapshot(), {
          submit_shaped: element.getAttribute('type') === 'submit',
        })
      }, 30)
    },
    true,
  )

  document.addEventListener(
    'change',
    function (event) {
      const element = event.target
      if (!element || !/^(INPUT|SELECT|TEXTAREA)$/.test(element.tagName)) return
      const before = snapshot()
      const kind = element.tagName === 'SELECT' ? 'SELECT' : 'TYPE'
      // A value change cannot navigate. Record it synchronously so a following
      // click cannot contaminate this event's observed page state.
      record(kind, element, element.value, before, snapshot())
    },
    true,
  )

  window.addEventListener('message', function (event) {
    const data = event.data
    if (!data || data.source !== 'flowstate-console') return
    if (data.type === 'recording') recorder.setEnabled(data.enabled)
  })

  function announceReady() {
    try {
      if (window.parent && window.parent !== window) {
        window.parent.postMessage(
          {
            source: 'flowstate-demo',
            type: 'ready',
            route: snapshot(),
            recording: recorder.enabled,
          },
          '*',
        )
      }
    } catch (error) {
      // No parent: the demo still works standalone.
    }
  }

  applyVariant()
  renderRoute()
  announceReady()
})()