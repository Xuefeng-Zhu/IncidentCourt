'use strict';

/** Validate an imported completed report before any UI rendering. No execution. */
function validateSavedRecord(value) {
  const fail = message => { throw new Error(message); };
  const object = item => item !== null && typeof item === 'object' && !Array.isArray(item);
  const string = (item, maximum = 12000) => typeof item === 'string' && item.length > 0 && item.length <= maximum;
  const array = (item, maximum) => Array.isArray(item) && item.length <= maximum;
  const id = item => typeof item === 'string' && /^[A-Za-z0-9][A-Za-z0-9:_-]{0,119}$/.test(item);
  if (!object(value) || !/^IC-\d{3}$/.test(value.incident_id || '') || !/^[a-f0-9]{12}$/.test(value.run_id || '')) fail('Choose a completed IncidentCourt JSON record with valid incident and run identifiers.');
  if (!/^(local|sample|zetaris) \/ (local|nightshift)$/.test(value.mode || '')) fail('The saved record has an unsupported original execution mode.');
  const [dataMode, modelMode] = value.mode.split(' / ');
  if (!string(value.summary) || !object(value.model) || value.model.mode !== modelMode || !object(value.memory)) fail('The saved record is missing its completed assessment or execution details.');
  if (!string(value.created_at, 80) || Number.isNaN(new Date(value.created_at).getTime())) fail('The saved record has no valid execution timestamp.');
  if (value.likely_service !== null && !string(value.likely_service, 200)) fail('The saved record has an invalid service assessment.');
  if (!(typeof value.confidence === 'number' && Number.isFinite(value.confidence) && value.confidence >= 0 && value.confidence <= 1) && !string(value.confidence, 100)) fail('The saved record has an invalid confidence assessment.');
  for (const [key, maximum] of [['evidence', 500], ['queries', 100], ['steps', 100], ['timeline', 100], ['alternatives', 50], ['limitations', 100]]) {
    if (!array(value[key], maximum)) fail('The saved record is incomplete or exceeds the supported record limits.');
  }
  if (!value.evidence.length || !value.queries.length || !value.steps.length) fail('The saved record needs evidence, executed queries, and reasoning steps.');
  if (!value.limitations.every(item => string(item, 4000))) fail('The saved record has invalid limitation text.');
  const queryIds = new Set();
  for (const query of value.queries) {
    if (!object(query) || !id(query.id) || queryIds.has(query.id) || query.backend !== dataMode || !string(query.purpose, 2000) || !string(query.sql, 20000) || !/^\s*SELECT\s/i.test(query.sql) || !Number.isInteger(query.row_count) || query.row_count < 0 || query.row_count > 1000) fail('The saved record has an invalid or inconsistent executed-query receipt.');
    queryIds.add(query.id);
  }
  const evidenceIds = new Set();
  for (const evidence of value.evidence) {
    if (!object(evidence) || !id(evidence.id) || evidenceIds.has(evidence.id) || !queryIds.has(evidence.query_id) || !['metrics', 'logs', 'traces', 'counterevidence'].includes(evidence.kind) || !string(evidence.service, 200) || !string(evidence.summary) || !array(evidence.rows, 1000)) fail('The saved record has invalid evidence or an unresolved source query.');
    for (const row of evidence.rows) {
      if (!object(row) || Object.keys(row).length > 100 || Object.entries(row).some(([key, cell]) => key.length > 120 || !(cell === null || typeof cell === 'boolean' || (typeof cell === 'number' && Number.isFinite(cell)) || (typeof cell === 'string' && cell.length <= 30000)))) fail('An imported evidence row is malformed or exceeds the supported limits.');
    }
    evidenceIds.add(evidence.id);
  }
  const references = refs => array(refs, 500) && refs.every(ref => typeof ref === 'string' && evidenceIds.has(ref));
  for (const step of value.steps) {
    if (!object(step) || !string(step.role, 100) || !string(step.action, 2000) || !string(step.detail) || !references(step.evidence_ids) || (step.query_id !== null && step.query_id !== undefined && !queryIds.has(step.query_id))) fail('The saved record has an invalid reasoning step or evidence citation.');
  }
  for (const item of value.timeline) {
    if (!object(item) || !string(item.title, 2000) || !string(item.detail) || typeof item.ts !== 'number' || !Number.isFinite(item.ts) || !references(item.evidence_ids)) fail('The saved record has an invalid timeline observation.');
  }
  for (const item of value.alternatives) {
    if (!object(item) || !string(item.service, 200) || !string(item.reason) || !references(item.evidence_ids)) fail('The saved record has an invalid competing explanation.');
  }
  for (const key of ['supporting_evidence_ids', 'counterevidence_ids']) if (value[key] !== undefined && !references(value[key])) fail('A saved assessment citation does not resolve to its evidence.');
  if (value.counterevidence !== undefined && (!array(value.counterevidence, 100) || value.counterevidence.some(item => !object(item) || !string(item.summary) || !references(item.evidence_ids)))) fail('The saved record has invalid counterevidence.');
  if (modelMode === 'nightshift' && (!object(value.provider_explanation) || !string(value.provider_explanation.summary) || !references(value.provider_explanation.evidence_ids) || !value.provider_explanation.evidence_ids.length)) fail('The saved Nightshift record is missing its completed, cited explanation.');
  if (!string(value.memory.mode, 120) || (value.memory.matches !== undefined && (!array(value.memory.matches, 50) || value.memory.matches.some(match => !object(match) || !object(match.memory || match) || !id((match.memory || match).id))))) fail('The saved record has malformed historical-memory context.');
  return value;
}

function canExecuteInvestigation(status, model) {
  const provider = status?.model || {};
  if (status?.data?.mode === 'sample') return model === 'local';
  if (provider.hosted_only === true && model !== 'nightshift') return false;
  if (model === 'nightshift') return provider.mode === 'configured' && provider.inference_enabled === true;
  return model === 'local';
}

// A loaded record wins over a late archive response, including offline imports.
function selectedIncidentId(incidents, reportIncidentId, currentIncidentId) {
  if (typeof reportIncidentId === 'string' && reportIncidentId) return reportIncidentId;
  const ids = incidents.map(item => String(item.id));
  return ids.includes(currentIncidentId) ? currentIncidentId : ids[0] || '';
}

// The pure validator can be tested with Node without a DOM, server, or inference.
if (typeof module === 'object' && module.exports) module.exports = { validateSavedRecord, canExecuteInvestigation, selectedIncidentId };


if (typeof document !== 'undefined') (() => {
  const $ = id => document.getElementById(id);
  const state = { incidents: [], report: null, busy: false, filter: 'all', status: null, loaded: false, modelChosen: false, savedRecord: false, retryAction: 'run' };
  const el = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  };
  const text = value => value === null || value === undefined ? 'Not recorded' : typeof value === 'object' ? JSON.stringify(value) : String(value);
  const list = value => Array.isArray(value) ? value : [];
  const dateParts = value => {
    if (value === null || value === undefined || value === '') return null;
    const numeric = typeof value === 'number' || /^\d+(\.\d+)?$/.test(String(value));
    const number = Number(value);
    const parsed = numeric ? new Date(number > 1e12 ? number : number * 1000) : new Date(value);
    if (Number.isNaN(parsed.getTime())) return null;
    const day = new Intl.DateTimeFormat('en-GB', { day: '2-digit', month: 'short', year: 'numeric', timeZone: 'UTC' }).format(parsed);
    const time = new Intl.DateTimeFormat('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23', timeZone: 'UTC' }).format(parsed);
    return { day, time, minutes: time.slice(0, 5) };
  };
  const date = value => {
    const parts = dateParts(value);
    return parts ? `${parts.day}, ${parts.time} UTC` : 'Time not recorded';
  };
  const windowParts = (start, end) => {
    const first = dateParts(start), last = dateParts(end);
    if (!first || !last) return { day: 'Window not recorded', time: '' };
    return { day: first.day === last.day ? first.day : `${first.day} – ${last.day}`, time: `${first.time}–${last.time} UTC` };
  };
  const splitLabel = value => ({ development: 'Development', holdout: 'Holdout', history: 'History' }[value] || value || 'Unspecified');
  const incidentTitle = incident => String(incident.title || '').replace(/\s+incident\s+\d+$/i, '').trim();
  const modeLabel = value => ({ 'zetaris / nightshift': 'Zetaris + Nightshift', 'local / nightshift': 'Local data + Nightshift', 'sample / local': 'Offline sample + rules', 'local / local': 'Local data + rules', 'zetaris / local': 'Zetaris + rules', nightshift: 'Nightshift', local: 'Deterministic rules' }[value] || value || 'Not recorded');
  const modelLabel = value => String(value || '').startsWith('nvidia/nemotron-3.5-lightning') ? 'NVIDIA Nemotron 3.5 Lightning' : String(value || '').startsWith('nvidia/nemotron-3-super') ? 'NVIDIA Nemotron 3 Super' : value ? `${String(value).split('/')[0]} hosted model` : 'Model not recorded';
  const confidence = value => text(value);
  const normalizeKind = kind => ({ metric: 'metrics', log: 'logs', trace: 'traces' }[String(kind).toLowerCase()] || String(kind).toLowerCase());

  async function request(url, options = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 180000);
    try {
      const response = await fetch(url, { ...options, signal: controller.signal, headers: { 'Accept': 'application/json', ...options.headers } });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) {
        const detail = body.detail || body.error || `Request failed (${response.status}).`;
        throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
      }
      return body;
    } catch (error) {
      if (error.name === 'AbortError') throw new Error('The request timed out. Check the server and provider status before trying again.');
      throw error;
    } finally {
      clearTimeout(timer);
    }
  }

  function showError(error, title = 'Investigation could not finish', retryAction = 'run') {
    state.retryAction = retryAction;
    $('error-title').textContent = title;
    $('error-message').textContent = error.message || String(error);
    $('error-banner').hidden = false;
    $('error-banner').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  function clearReport() {
    state.report = null;
    state.savedRecord = false;
    $('saved-record-note').hidden = true;
    $('report-workspace').hidden = true;
    $('empty-state').hidden = false;
    $('error-banner').hidden = true;
    $('execution-badge').textContent = 'No record open';
  }

  function renderIncidentList() {
    const target = $('incident-list');
    if (!target) return;
    target.replaceChildren();
    for (const incident of state.incidents) {
      const selected = String(incident.id) === $('incident-select').value;
      const row = el('tr', `incident-row${selected ? ' selected' : ''}`);
      const name = el('td', 'incident-cell');
      const button = el('button', 'incident-button', incident.id);
      button.type = 'button';
      button.value = String(incident.id);
      button.setAttribute('aria-label', `Select incident ${incident.id}`);
      button.setAttribute('aria-current', String(selected));
      button.addEventListener('click', () => {
        if (state.busy) return;
        if ($('incident-select').value !== String(incident.id)) {
          $('incident-select').value = String(incident.id);
          clearReport();
          updateIncident();
        }
      });
      name.append(button);
      const title = incidentTitle(incident);
      if (title && title !== incident.id) name.append(el('span', 'incident-title', title));
      const window = windowParts(incident.start_ts, incident.end_ts);
      const observed = el('td', 'window-cell');
      observed.append(el('span', 'window-date', window.day), el('span', 'window-time', window.time));
      row.append(name, el('td', 'dataset-cell', incident.source || 'Not recorded'), observed, el('td', 'split-cell', splitLabel(incident.split)));
      target.append(row);
    }
    if (!state.incidents.length) {
      const row = el('tr', 'incident-row');
      const cell = el('td', 'no-items', 'No incidents available.');
      cell.colSpan = 4;
      row.append(cell);
      target.append(row);
    }
  }

  function renderIncidentSelect() {
    const select = $('incident-select');
    const selected = selectedIncidentId(state.incidents, state.report?.incident_id, select.value);
    select.replaceChildren();
    for (const incident of state.incidents) {
      const title = incidentTitle(incident);
      const option = el('option', '', incident.id + (title && title !== incident.id ? ` · ${title}` : ''));
      option.value = String(incident.id);
      select.append(option);
    }
    if (selected && !state.incidents.some(item => String(item.id) === selected)) {
      const option = el('option', '', `${selected} · Saved record`);
      option.value = selected;
      select.append(option);
    }
    if (!selected && !state.incidents.length) select.append(el('option', '', state.loaded ? 'No incidents imported' : 'Records unavailable'));
    select.value = selected;
    select.disabled = state.busy || !state.incidents.length;
    updateIncident();
  }

  function updateIncident() {
    const incident = state.incidents.find(item => String(item.id) === $('incident-select').value);
    const window = incident ? windowParts(incident.start_ts, incident.end_ts) : null;
    $('incident-meta').textContent = incident ? `${incident.source || 'Telemetry archive'} · ${splitLabel(incident.split)}\n${window.day} · ${window.time}` : state.report ? `Saved ${state.report.incident_id} · archive metadata unavailable.` : 'No incident records available.';
    renderIncidentList();
    updateRunAvailability();
  }

  function updateRunAvailability() {
    const incident = state.incidents.some(item => String(item.id) === $('incident-select').value);
    $('run-button').disabled = state.busy || !incident || !canExecuteInvestigation(state.status, $('model-select').value);
  }

  function updateEngineNote() {
    const provider = state.status?.model || {};
    if ($('model-select').value === 'nightshift') {
      $('engine-note').textContent = provider.inference_enabled === true
        ? 'Hosted inference enabled for authorized usage.'
        : provider.mode === 'configured'
          ? 'Hosted inference is off. Open a saved record.'
          : 'Nightshift is not configured.';
    } else $('engine-note').textContent = 'Deterministic rules; no model calls.';
  }

  function renderStatus(status) {
    state.status = status;
    const target = $('connection-list');
    target.replaceChildren();
    const names = { data: 'Zetaris', model: 'Nightshift', memory: 'H-MEM' };
    for (const key of ['data', 'model', 'memory']) {
      const item = status[key] || { mode: 'unknown', detail: 'Status is unavailable.' };
      const mode = String(item.mode || 'unknown');
      const row = el('div', 'connection-item' + (/^(verified|live|connected)$/.test(mode) ? ' live' : ''));
      row.append(el('i', 'connection-dot'));
      const label = key === 'data' ? ({ zetaris: 'Selected', sample: 'Offline sample', local: 'Local data', not_imported: 'No data' }[mode] || 'Unknown')
        : key === 'model' ? (mode === 'configured' ? (item.inference_enabled === true ? 'Enabled' : 'Usage off') : 'Not set')
        : mode === 'official-hmem-reference' ? 'Local reference' : 'Unavailable';
      const name = el('div', 'connection-name');
      name.append(el('span', 'connection-label', names[key]), el('span', 'connection-mode', label));
      const detail = (item.detail || 'No details recorded.') + (key === 'model' && item.requested_model ? ` Model: ${item.requested_model}.` : '');
      row.append(name, el('p', 'connection-detail', detail));
      target.append(row);
    }
    const model = status.model || {};
    const nightshift = $('model-select').querySelector('option[value="nightshift"]');
    const local = $('model-select').querySelector('option[value="local"]');
    const configured = model.mode === 'configured';
    const offline = status.data?.mode === 'sample';
    const enforceHosted = model.hosted_only === true && !offline;
    nightshift.disabled = !configured || offline;
    nightshift.textContent = offline ? 'Nightshift · offline' : configured ? (model.inference_enabled === true ? 'Nightshift' : 'Nightshift · off') : 'Nightshift · unavailable';
    local.disabled = enforceHosted;
    if (offline && !state.modelChosen) $('model-select').value = 'local';
    else if (enforceHosted || (configured && !state.modelChosen)) $('model-select').value = 'nightshift';
    else if (!configured && $('model-select').value === 'nightshift') $('model-select').value = 'local';
    updateEngineNote();
    updateRunAvailability();
    const previous = status.last_verified;
    $('last-verified').hidden = !previous;
    if (previous) {
      const checked = dateParts(previous.verified_at);
      $('last-verified-time').textContent = checked ? `${checked.day}, ${checked.minutes} UTC` : 'Time not recorded';
      $('last-verified-model').textContent = modelLabel(previous.physical_model);
      $('last-verified-model').title = previous.physical_model || '';
      $('last-verified-counts').textContent = `${previous.gateway_calls} model calls · ${previous.remote_selects} SQL queries`;
    }

  }

  async function initialize() {
    $('run-button').disabled = true;
    const results = await Promise.allSettled([request('/api/status'), request('/api/incidents')]);
    if (results[0].status === 'fulfilled') {
      state.status = results[0].value;
      renderStatus(state.status);
    } else {
      renderStatus({ data: { mode: 'unknown', detail: 'Could not read integration status.' } });
    }
    if (results[1].status === 'fulfilled') {
      const incidents = Array.isArray(results[1].value) ? results[1].value : list(results[1].value.incidents);
      state.incidents = incidents;
      state.loaded = true;
      renderIncidentSelect();
      updateRecordMetadata();
    } else {
      renderIncidentSelect();
      updateRecordMetadata();
      if (!state.report) showError(results[1].reason);
    }
  }

  function switchTab(tab, focus = false) {
    for (const button of document.querySelectorAll('[data-tab]')) {
      const active = button.dataset.tab === tab;
      button.classList.toggle('active', active);
      button.setAttribute('aria-selected', String(active));
      button.tabIndex = active ? 0 : -1;
      $(`panel-${button.dataset.tab}`).hidden = !active;
      if (active && focus) button.focus();
    }
  }

  function evidenceRefs(ids) {
    const group = el('div', 'evidence-refs');
    for (const id of list(ids)) {
      const button = el('button', 'evidence-ref', id);
      button.type = 'button';
      button.setAttribute('aria-label', `Inspect evidence ${id}`);
      button.addEventListener('click', () => {
        state.filter = 'all';
        updateFilter();
        renderEvidence();
        switchTab('evidence');
        const card = document.getElementById(`evidence-${id}`);
        if (card) {
          card.classList.add('highlight');
          card.tabIndex = -1;
          card.focus({ preventScroll: true });
          card.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }
      });
      group.append(button);
    }
    return group;
  }

  function sectionHeading(title, index) {
    const heading = el('div', 'section-heading');
    heading.append(el('h3', '', title));
    return heading;
  }

  function updateRecordMetadata() {
    const report = state.report;
    if (!report) return;
    const incident = state.incidents.find(item => String(item.id) === String(report.incident_id)) || {};
    const mode = report.mode || report.model?.mode || 'local';
    $('execution-badge').textContent = state.savedRecord ? `Saved record · ${modeLabel(mode)}` : `${modeLabel(mode)} · complete`;
    $('saved-record-note').hidden = !state.savedRecord;
    $('saved-record-detail').textContent = `Original run · ${modeLabel(mode)}`;
    $('record-source').textContent = incident.source || 'Investigation record';
    const title = incidentTitle(incident);
    $('record-title').textContent = (report.incident_id || 'Investigation record') + (title && title !== report.incident_id ? ` · ${title}` : '');
    $('record-meta').textContent = `Run ${report.run_id || 'not recorded'} · ${date(report.created_at)}`;
  }

  function renderReport() {
    const report = state.report;
    const mode = report.mode || report.model?.mode || 'local';
    updateRecordMetadata();
    $('evidence-count').textContent = list(report.evidence).length;
    $('query-count').textContent = list(report.queries).length;
    const statbar = $('record-statbar');
    statbar.replaceChildren();
    for (const [label, value, small] of [
      ['Candidate service', report.likely_service || 'Unresolved', true],
      ['Evidence', list(report.evidence).length, false],
      ['Queries', list(report.queries).length, false],
      ['Method', modeLabel(mode), true]
    ]) {
      const stat = el('div', 'record-stat');
      stat.append(el('span', 'stat-label', label), el('span', `stat-value${small ? ' small' : ''}`, value));
      statbar.append(stat);
    }
    const panel = $('panel-report');
    panel.replaceChildren();
    const finding = el('section', 'finding');
    const main = el('div', 'finding-main');
    main.append(el('h3', 'assessment-heading', 'Assessment'), el('p', 'assessment-summary', text(report.summary)));
    if (list(report.supporting_evidence_ids).length) main.append(evidenceRefs(report.supporting_evidence_ids));
    const aside = el('div', 'finding-aside');
    aside.append(el('span', 'stat-label', 'Confidence'), el('span', 'stat-value', confidence(report.confidence)), el('p', 'confidence-note', 'Qualitative, not calibrated.'));
    finding.append(main, aside);
    panel.append(finding);
    if (report.provider_explanation && typeof report.provider_explanation.summary === 'string') {
      const explanation = el('details', 'provider-explanation model-notes');
      const summary = el('summary', 'model-notes-heading', 'Model notes');
      summary.append(el('small', 'model-note-label', 'Unreviewed'));
      explanation.append(summary, el('p', 'model-note-caption', 'Generated by Nightshift; not independently reviewed.'), el('p', 'model-narrative', report.provider_explanation.summary), evidenceRefs(report.provider_explanation.evidence_ids));
      panel.append(explanation);
    }
    const columns = el('div', 'report-columns');
    const timelineSection = el('section', '');
    timelineSection.append(sectionHeading('Timeline', '01'));
    const timeline = el('ol', 'timeline');
    for (const point of list(report.timeline)) {
      const item = el('li', 'timeline-item');
      item.append(el('time', 'time-label', date(point.ts)), el('h4', '', point.title || 'Observation'), el('p', '', point.detail || ''), evidenceRefs(point.evidence_ids));
      timeline.append(item);
    }
    timelineSection.append(timeline.childElementCount ? timeline : el('p', 'no-items', 'No timeline observations recorded.'));
    columns.append(timelineSection);
    const alternatives = el('section', '');
    alternatives.append(sectionHeading('Other possibilities', '02'));
    for (const alternative of list(report.alternatives)) {
      const item = el('article', 'alternative');
      item.append(el('h4', '', alternative.service || alternative.title || 'Alternative explanation'), el('p', '', alternative.reason || alternative.summary || ''), evidenceRefs(alternative.evidence_ids));
      alternatives.append(item);
    }
    if (!list(report.alternatives).length) alternatives.append(el('p', 'no-items', 'No alternatives recorded.'));
    if (list(report.counterevidence).length) {
      const heading = sectionHeading('Counterevidence', '03');
      heading.classList.add('counterevidence-heading');
      alternatives.append(heading);
      for (const item of report.counterevidence) {
        const article = el('article', 'alternative');
        article.append(el('p', '', typeof item === 'string' ? item : item.summary || item.reason || ''), evidenceRefs(item.evidence_ids));
        alternatives.append(article);
      }
    }
    columns.append(alternatives);
    panel.append(columns);
    const uncertainty = el('section', 'uncertainty');
    uncertainty.append(el('h3', '', 'Limitations'));
    const limitations = el('ul', '');
    for (const limitation of list(report.limitations)) limitations.append(el('li', '', text(limitation)));
    if (!limitations.childElementCount) limitations.append(el('li', '', 'No limitations recorded.'));
    uncertainty.append(limitations);
    panel.append(uncertainty);
    const runtime = el('div', 'runtime-note');
    for (const [name, data] of [['Execution', report.model], ['Prior investigations', report.memory]]) {
      const item = el('section', '');
      item.append(el('h4', '', name), el('p', 'runtime-detail', data ? (name === 'Execution' ? `${report.model?.mode === 'nightshift' ? 'Nightshift' : 'Deterministic rules'}. ${data.detail || ''}` : `H-MEM · ${list(data.matches).length} earlier finding${list(data.matches).length === 1 ? '' : 's'}. ${data.detail || ''}`) : 'No receipt recorded.'));
      if (name === 'Execution' && data?.served_model) item.append(el('p', 'runtime-model', `Served model: ${data.served_model}`));
      if (data && list(data.matches).length) {
        for (const match of data.matches) {
          const record = match && typeof match === 'object' ? match.memory || match : match;
          item.append(el('p', 'memory-match', typeof record === 'string' ? record : `${record.missionId || record.incident_id || record.id || 'Prior record'}: ${record.summary || record.content || text(record)}`));
        }
      }
      runtime.append(item);
    }
    const runDetails = el('details', 'run-details');
    runDetails.append(el('summary', '', 'Run details'), runtime);
    panel.append(runDetails);
    renderEvidence();
    renderTrail();
    switchTab('report');
  }

  function renderEvidence() {
    const target = $('evidence-list');
    target.replaceChildren();
    const evidence = list(state.report?.evidence).filter(item => state.filter === 'all' || normalizeKind(item.kind) === state.filter);
    for (const item of evidence) {
      const card = el('article', 'evidence-card evidence-row');
      card.id = `evidence-${item.id}`;
      const header = el('div', 'evidence-card-header');
      const label = el('div', 'evidence-id', item.id || 'Evidence');
      label.append(el('span', 'signal-tag', item.kind || 'observation'));
      header.append(label, el('time', 'evidence-time', date(item.ts)));
      card.append(header, el('h4', '', item.service || 'Cross-service observation'), el('p', '', item.summary || ''));
      if (item.polarity || item.relation) card.append(el('span', 'run-tag', item.polarity || item.relation));
      const rows = list(item.rows);
      if (rows.length) {
        const details = el('details', 'evidence-rows');
        details.append(el('summary', '', `${rows.length} row${rows.length === 1 ? '' : 's'}${item.query_id ? ` · ${item.query_id}` : ''}`));
        const wrap = el('div', 'evidence-table-wrap');
        const table = el('table', 'evidence-table');
        const columns = Array.from(new Set(rows.flatMap(row => row && typeof row === 'object' ? Object.keys(row) : ['value'])));
        const thead = el('thead', '');
        const tr = el('tr', '');
        for (const column of columns) {
          const th = el('th', '', column);
          th.scope = 'col';
          tr.append(th);
        }
        thead.append(tr);
        const tbody = el('tbody', '');
        for (const row of rows) {
          const tr = el('tr', '');
          for (const column of columns) {
            const value = text(row && typeof row === 'object' ? row[column] : row);
            const td = el('td', '', value);
            td.title = value;
            tr.append(td);
          }
          tbody.append(tr);
        }
        table.append(thead, tbody);
        wrap.append(table);
        details.append(wrap);
        card.append(details);
      } else if (item.query_id) card.append(el('span', 'query-backend', `${item.query_id} · no matching rows`));
      target.append(card);
    }
    if (!evidence.length) target.append(el('p', 'no-items', 'No evidence for this signal.'));
  }

  function renderTrail() {
    const steps = $('step-list');
    steps.replaceChildren();
    list(state.report.steps).forEach((item, index) => {
      const row = el('article', `step step-row${/critic/i.test(item.role || '') ? ' critic' : ''}`);
      row.append(el('span', 'step-number', String(index + 1).padStart(2, '0')));
      const body = el('div', 'step-body');
      body.append(el('div', 'step-role', item.role || 'Investigator'), el('h4', 'step-title', item.action || 'Investigation step'), el('p', '', item.detail || ''), evidenceRefs(item.evidence_ids));
      if (item.query_id) body.append(el('span', 'query-backend', item.query_id));
      row.append(body);
      steps.append(row);
    });
    if (!steps.childElementCount) steps.append(el('p', 'no-items', 'No reasoning steps recorded.'));
    const queries = $('query-list');
    queries.replaceChildren();
    for (const item of list(state.report.queries)) {
      const card = el('article', 'query-card query-row');
      const top = el('div', 'query-top');
      const title = el('h4', 'query-title');
      title.append(el('span', 'query-id', item.id || 'Query'), document.createTextNode(item.purpose || 'Telemetry query'));
      top.append(title, el('span', 'query-count', `${item.row_count ?? '?'} row${item.row_count === 1 ? '' : 's'}`));
      const details = el('details', 'query-sql');
      details.append(el('summary', '', 'SQL'), el('pre', '', item.sql || 'No SQL recorded.'));
      card.append(top, el('p', 'query-backend', ({ zetaris: 'Zetaris', local: 'Local SQLite', sample: 'Offline sample' }[item.backend] || 'Backend not recorded')), details);
      queries.append(card);
    }
    if (!queries.childElementCount) queries.append(el('p', 'no-items', 'No query receipts recorded.'));
  }

  function updateFilter() {
    for (const button of document.querySelectorAll('[data-filter]')) {
      const active = button.dataset.filter === state.filter;
      button.classList.toggle('active', active);
      button.setAttribute('aria-pressed', String(active));
    }
  }

  async function runInvestigation() {
    if (state.busy || !$('incident-select').value || !canExecuteInvestigation(state.status, $('model-select').value)) return;
    state.busy = true;
    state.savedRecord = false;
    $('open-record-button').disabled = true;
    $('run-button').disabled = true;
    $('incident-select').disabled = true;
    $('model-select').disabled = true;
    $('run-button').querySelector('span').textContent = 'Investigating…';
    $('error-banner').hidden = true;
    $('empty-state').hidden = true;
    $('report-workspace').hidden = true;
    $('loading-state').hidden = false;
    try {
      const report = await request('/api/investigate', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ incident_id: $('incident-select').value, model: $('model-select').value }) });
      if (!report || !report.incident_id) throw new Error('The server returned an incomplete investigation record.');
      state.report = report;
      state.filter = 'all';
      updateFilter();
      renderReport();
      $('report-workspace').hidden = false;
    } catch (error) {
      state.report = null;
      $('empty-state').hidden = false;
      showError(error);
    } finally {
      state.busy = false;
      $('open-record-button').disabled = false;
      $('loading-state').hidden = true;
      $('incident-select').disabled = !state.incidents.length;
      $('model-select').disabled = false;
      updateRunAvailability();
      $('run-button').querySelector('span').textContent = 'Run investigation';
    }
  }

  $('open-record-button').addEventListener('click', () => {
    if (!state.busy) $('saved-record-input').click();
  });
  $('saved-record-input').addEventListener('change', async event => {
    const file = event.target.files?.[0];
    if (!file || state.busy) return;
    state.busy = true;
    $('open-record-button').disabled = true;
    $('run-button').disabled = true;
    $('incident-select').disabled = true;
    $('model-select').disabled = true;
    try {
      if (file.size > 5 * 1024 * 1024) throw new Error('Saved records must be no larger than 5 MiB.');
      let parsed;
      try { parsed = JSON.parse(await file.text()); }
      catch { throw new Error('The selected file is not valid JSON. Choose a completed IncidentCourt record.'); }
      const report = validateSavedRecord(parsed);
      state.report = report;
      state.savedRecord = true;
      state.filter = 'all';
      $('error-banner').hidden = true;
      $('empty-state').hidden = true;
      $('loading-state').hidden = true;
      renderIncidentSelect();
      updateFilter();
      renderReport();
      $('report-workspace').hidden = false;
      $('saved-record-note').scrollIntoView({ behavior: 'smooth', block: 'start' });
    } catch (error) {
      showError(error, 'Saved record could not be opened', 'import');
    } finally {
      state.busy = false;
      $('saved-record-input').value = '';
      $('open-record-button').disabled = false;
      updateRunAvailability();
      $('incident-select').disabled = !state.incidents.length;
      $('model-select').disabled = false;
    }
  });

  $('investigation-form').addEventListener('submit', event => { event.preventDefault(); runInvestigation(); });
  $('incident-select').addEventListener('change', () => { clearReport(); updateIncident(); });
  $('model-select').addEventListener('change', () => {
    state.modelChosen = true;
    clearReport();
    updateEngineNote();
    updateRunAvailability();
  });
  $('retry-button').addEventListener('click', () => {
    if (state.retryAction === 'import') { if (!state.busy) $('saved-record-input').click(); }
    else if (state.loaded && state.incidents.length) runInvestigation();
    else initialize();
  });
  for (const tab of document.querySelectorAll('[data-tab]')) {
    tab.addEventListener('click', () => switchTab(tab.dataset.tab));
    tab.addEventListener('keydown', event => {
      const tabs = Array.from(document.querySelectorAll('[data-tab]'));
      let index = tabs.indexOf(tab);
      if (event.key === 'ArrowRight') index = (index + 1) % tabs.length;
      else if (event.key === 'ArrowLeft') index = (index + tabs.length - 1) % tabs.length;
      else if (event.key === 'Home') index = 0;
      else if (event.key === 'End') index = tabs.length - 1;
      else return;
      event.preventDefault();
      switchTab(tabs[index].dataset.tab, true);
    });
  }
  for (const filter of document.querySelectorAll('[data-filter]')) filter.addEventListener('click', () => { state.filter = filter.dataset.filter; updateFilter(); renderEvidence(); });
  $('export-button').addEventListener('click', () => {
    if (!state.report) return;
    const blob = new Blob([JSON.stringify(state.report, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `incidentcourt-${String(state.report.incident_id).replace(/[^a-z0-9_-]/gi, '_')}-${String(state.report.run_id || 'record').replace(/[^a-z0-9_-]/gi, '_')}.json`;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
  initialize();
})();
