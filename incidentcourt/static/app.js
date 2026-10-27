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
  if (value.analysis_window !== undefined) {
    const window = value.analysis_window;
    const fields = ['start_ts', 'baseline_end_ts', 'comparison_start_ts', 'end_ts'];
    if (!object(window) || !fields.every(key => typeof window[key] === 'number' && Number.isFinite(window[key])) ||
        !(window.start_ts < window.baseline_end_ts && window.baseline_end_ts <= window.comparison_start_ts && window.comparison_start_ts < window.end_ts) ||
        !queryIds.has(window.query_id)) fail('The saved record has invalid comparison windows or an unresolved window query.');
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
  const state = { incidents: [], catalog: {cases:[],context:{}}, runs: [], status: null, report: null, current: null, filter: 'all', activeTab: 'report', visualService: '', visualRange: 'full', route: '', routeToken: 0, dirty: false, reviewVersion: 0, refreshBusy: false, starting: false, requestKeys: new Map() };
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

  function saveVisualLocation() {
    if (!state.current || state.current.state !== 'completed') return;
    const params = new URLSearchParams();
    if (state.activeTab !== 'report') params.set('view', state.activeTab);
    if (state.visualService) params.set('service', state.visualService);
    if (state.visualRange !== 'full') params.set('range', state.visualRange);
    const route = '/investigations/' + state.current.id + (params.size ? '?' + params : '');
    history.replaceState(null, '', '#' + route);
    state.route = route;
  }

  function switchTab(tab, focus = false, persist = true) {
    if (!['report', 'visuals', 'evidence', 'trail'].includes(tab)) tab = 'report';
    state.activeTab = tab;
    $('view-investigation').classList.toggle('is-visual-view', tab === 'visuals');
    $('review-jump').hidden = tab === 'visuals' || state.current?.state !== 'completed';
    for (const button of document.querySelectorAll('[data-tab]')) {
      const active = button.dataset.tab === tab;
      button.classList.toggle('active', active);
      button.setAttribute('aria-selected', String(active));
      button.tabIndex = active ? 0 : -1;
      $(`panel-${button.dataset.tab}`).hidden = !active;
      if (active && focus) button.focus();
    }
    if (persist) saveVisualLocation();
  }

  function inspectEvidence(id) {
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
  }

  function inspectQuery(id) {
    switchTab('trail');
    const card = document.getElementById(`query-${id}`);
    if (card) {
      card.querySelector('details').open = true;
      card.tabIndex = -1;
      card.focus({ preventScroll: true });
      card.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  }

  function evidenceRefs(ids) {
    const group = el('div', 'evidence-refs');
    for (const id of list(ids)) {
      const button = el('button', 'evidence-ref', id);
      button.type = 'button';
      button.setAttribute('aria-label', `Inspect evidence ${id}`);
      button.addEventListener('click', () => inspectEvidence(id));
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
    const incident = state.incidents.find(item => item.id === report.incident_id) || {};
    const imported = state.current.origin === 'imported';
    $('execution-badge').textContent = modeLabel(report.mode);
    $('saved-record-note').hidden = !imported;
    $('saved-record-detail').textContent = modeLabel(report.mode);
    $('record-source').textContent = incident.source || (imported ? 'Imported report' : state.catalog.context?.label || 'Investigation');
    const title = incidentTitle(incident);
    $('record-title').textContent = report.incident_id + (title && title !== report.incident_id ? ` · ${title}` : '');
    $('record-meta').textContent = `Run ${report.run_id} · ${date(report.created_at)}`;
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
    const visualLink = el('button', 'outline-button visual-entry', 'Explore visual evidence →');
    visualLink.type = 'button';
    visualLink.addEventListener('click', () => switchTab('visuals', true));
    main.append(visualLink);
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
    const services = window.IncidentVisualData.buildVisualData(report).services;
    if (state.visualService && !services.includes(state.visualService)) {
      state.visualService = '';
      saveVisualLocation();
    }
    window.IncidentVisuals.render($('visual-evidence'), report, {
      inspectEvidence, inspectQuery, selectedService: state.visualService, selectedRange: state.visualRange,
      onSelectionChange(service) { state.visualService = service; saveVisualLocation(); },
      onRangeChange(range) { state.visualRange = range; saveVisualLocation(); }
    });
    switchTab(state.activeTab, false, false);
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
      card.id = `query-${item.id}`;
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

  async function request(url, options = {}) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 45000);
    try {
      const response = await fetch(url, { ...options, signal: controller.signal, headers: { Accept: 'application/json', ...(options.body ? {'Content-Type':'application/json'} : {}), ...options.headers } });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) {
        const error = new Error(body.error || 'The workspace could not complete this request.');
        error.details = body; error.status = response.status; throw error;
      }
      return body;
    } catch (error) {
      if (error.name === 'AbortError') throw new Error('The request timed out. Saved work is retained.');
      throw error;
    } finally { clearTimeout(timeout); }
  }
  const post = (url, value = {}) => request(url, {method:'POST', body:JSON.stringify(value)});
  function showError(error) {
    $('error-title').textContent = 'Could not complete this action';
    $('error-message').textContent = error.message || 'Workspace unavailable.';
    $('error-banner').hidden = false;
  }
  function link(label, route, className = 'text-link') {
    const item = el('a', className, label); item.href = '#' + route; return item;
  }
  const statusName = run => run.state === 'completed' ? (run.reviewed_at ? 'Reviewed' : 'Needs review') : ({queued:'Running',running:'Running',failed:'Failed',interrupted:'Interrupted'}[run.state] || run.state);
  function chip(label) { const item = el('span','status-chip',label); item.dataset.status = label; return item; }
  function clear(node) { node.replaceChildren(); return node; }
  function heading(title, description) {
    const wrap=el('div','compact-heading');wrap.append(el('h2','',title));if(description)wrap.append(el('p','muted',description));return wrap;
  }
  function canStart() {
    return state.catalog.context?.mode === 'sample' || state.status?.model?.inference_enabled === true && state.status?.model?.hosted_only === true;
  }
  function updateGlobals() {
    $('sidebar-source').textContent=state.catalog.context?.label || 'Source not loaded';
    const count=state.catalog.cases.filter(c=>c.status==='Needs review').length;
    $('nav-review-count').textContent=count || '';
    const active=state.catalog.active;
    $('active-run-link').hidden=!active;
    if(active){$('active-run-link').href='#/investigations/'+active.id;$('active-run-link').textContent=`${active.incident_id} · ${active.stage}`;}
  }
  async function loadCatalog() {
    state.catalog=await request('/api/cases');state.incidents=state.catalog.cases;updateGlobals();
  }
  async function loadRuns() {state.runs=(await request('/api/investigations')).investigations;}
  function renderQueue() {
    const term=$('case-search').value.trim().toLowerCase(), filter=$('case-status').value;
    const cases=state.catalog.cases.filter(c=>(filter==='All statuses'||c.status===filter)&&[c.id,c.title,c.completed?.candidate].join(' ').toLowerCase().includes(term));
    const body=clear($('case-rows'));
    for(const c of cases){
      const row=el('tr');const name=el('td');name.append(link(c.id,'/incidents/'+c.id,'case-id'),el('span','row-title',incidentTitle(c)||'Incident'));
      const window=windowParts(c.start_ts,c.end_ts);name.append(el('span','row-meta',window.day+(window.time?' · '+window.time:'')));
      if(c.completed?.candidate)name.append(el('span','mobile-candidate',c.completed.candidate));
      const status=el('td');status.append(chip(c.status));
      const candidate=el('td','candidate-col');candidate.append(el('span','service-name',c.completed?.candidate||'—'));
      if(c.completed)candidate.append(el('span','row-meta',c.completed.confidence ? `Confidence: ${c.completed.confidence}` : ''));
      const last=el('td','last-run-col',c.completed?date(c.completed.created_at):'Not investigated');
      row.append(name,status,candidate,last);body.append(row);
    }
    $('queue-empty').hidden=cases.length>0;
    const total=state.catalog.cases.length, review=state.catalog.cases.filter(c=>c.status==='Needs review').length;
    $('queue-counts').textContent=`${total} incident${total===1?'':'s'} · ${review} need review`;
    $('catalog-state').textContent=state.catalog.context.catalog_error|| (state.catalog.context.cached_at?`Last refreshed ${date(state.catalog.context.cached_at)}`:'Telemetry has not been refreshed.');
    $('catalog-state').classList.toggle('warning-text',!!state.catalog.context.catalog_error);
  }
  function runList(target, runs) {
    clear(target);
    if(!runs.length){target.append(el('p','no-items','No investigations yet.'));return;}
    for(const run of runs){
      const row=el('article','run-list-row');
      const name=el('div');name.append(link(`${run.incident_id} · ${run.candidate|| (run.state==='completed'?'Assessment':'Investigation')}`,'/investigations/'+run.id,'run-list-title'),el('p','row-meta',`${date(run.created_at)} · ${run.origin==='verification'?'Earlier verified run':run.origin==='imported'?'Imported snapshot':'Workspace run'}`));
      const result=el('div','run-list-result');result.append(chip(statusName(run)),el('span','row-meta',run.state==='completed'?`${run.query_count} queries · ${run.evidence_count} evidence records`:run.stage));
      row.append(name,result);target.append(row);
    }
  }
  function renderLibrary() {
    const term=$('library-search').value.trim().toLowerCase(), filter=$('library-filter').value;
    const runs=state.runs.filter(r=>[r.incident_id,r.candidate,r.run_id].join(' ').toLowerCase().includes(term)).filter(r=>filter==='All investigations'||filter==='Imported'&&r.origin==='imported'||filter==='Running'&&['running','queued'].includes(r.state)||filter==='Failed'&&['failed','interrupted'].includes(r.state)||filter===statusName(r));
    runList($('library-list'),runs);
  }
  async function renderCase(id, token) {
    $('case-new-run').disabled=true;
    const data=await request('/api/cases/'+encodeURIComponent(id));if(token!==state.routeToken)return;
    const c=data.case;state.caseId=c.id;
    $('case-title').textContent=c.id+' · '+(incidentTitle(c)||'Incident');
    const window=windowParts(c.start_ts,c.end_ts);$('case-subtitle').textContent=`${c.source||state.catalog.context.label} · ${window.day}${window.time?' · '+window.time:''}`;
    $('case-new-run').disabled=!canStart()||!!state.catalog.active;
    $('case-run-note').textContent=state.catalog.active?'One investigation is already running in this workspace.':canStart()?'A new run preserves all earlier results.':'New AI runs are paused. You can still read and review existing investigations.';
    const overview=clear($('case-overview'));const strip=el('div','case-summary');strip.append(chip(c.status),el('span','muted',`${c.investigation_count} investigation${c.investigation_count===1?'':'s'}`));overview.append(strip);
    if(c.completed){const box=el('section','latest-assessment');box.append(heading('Latest assessment',c.completed.candidate||'No service identified'),link('Open investigation →','/investigations/'+c.completed.id,'primary-link'));overview.append(box);}
    else if(!data.investigations.length)overview.append(el('p','no-items','This case has not been investigated. Start a run when execution is available.'));
    const metadata=el('details','case-metadata');metadata.append(el('summary','','Dataset details'),el('p','muted',`Source: ${c.source||'Not available'}. Split: ${splitLabel(c.split)}. These records come from controlled experiments.`));overview.append(metadata);
    runList($('case-history'),data.investigations);
  }
  function showRun(run, initial) {
    state.current=run;
    $('run-origin').textContent=(run.origin==='imported'?'Imported snapshot · not linked to a case':run.origin==='verification'?'Earlier verified investigation':'Workspace investigation')+' · '+date(run.created_at);
    const completed=run.state==='completed';
    $('run-progress').hidden=!['queued','running'].includes(run.state);
    $('run-failure').hidden=!['failed','interrupted'].includes(run.state);
    $('report-workspace').hidden=!completed;$('review-panel').hidden=!completed;$('review-jump').hidden=!completed;
    if(!completed){
      $('execution-badge').textContent=statusName(run);
      $('run-stage').textContent=run.stage;$('run-query-count').textContent=`${run.query_count} queries completed`;
      $('failure-title').textContent=run.state==='interrupted'?'Investigation interrupted':'Investigation failed';
      $('failure-message').textContent=run.error||'';$('failure-case-link').href='#/incidents/'+run.incident_id;
    } else if(initial || !state.report || state.report.run_id!==run.report.run_id){
      state.report=run.report;state.filter='all';updateFilter();renderReport();
      $('review-note').disabled=false;$('reviewed').disabled=false;$('review-note').value=run.review.note;$('reviewed').checked=run.review.reviewed;state.reviewVersion=run.review.version;state.dirty=false;
      $('review-feedback').textContent='';$('save-review').disabled=true;
    }
    $('review-state').replaceChildren();$('review-state').textContent=run.reviewed_at?'Reviewed':'Needs review';$('review-state').dataset.status=run.reviewed_at?'Reviewed':'Needs review';
  }
  async function renderInvestigation(id, token) {
    const run=await request('/api/investigations/'+encodeURIComponent(id));if(token!==state.routeToken)return;
    showRun(run,true);
    clear($('breadcrumbs')).append(link('Incidents','/incidents'),el('span','breadcrumb-separator','/'),run.linked?link(run.incident_id,'/incidents/'+run.incident_id):el('span','',run.incident_id),el('span','breadcrumb-separator','/'),el('span','','Investigation'));
  }
  function renderSettings() {
    const target=clear($('settings-content'));
    for(const [name,value,detail] of [
      ['Telemetry',state.catalog.context.label||'Not loaded',state.status?.data?.detail||'Source not configured.'],
      ['Execution',state.status?.model?.requested_model ? modelLabel(state.status.model.requested_model):'Not configured',canStart()?'Execution is available for this workspace.':'New AI runs are paused. Saved results and review notes remain available.'],
      ['Memory','H-MEM',state.status?.memory?.detail||'Memory status unavailable.']
    ]){const section=el('section','setting-row');section.append(el('h2','',name));const body=el('div');body.append(el('h3','',value),el('p','muted',detail));if(name==='Execution'&&state.status?.model?.requested_model)body.append(el('code','setting-model',state.status.model.requested_model));section.append(body);target.append(section);}
    const earlier=state.status?.last_verified;
    if(earlier){const details=el('details','case-metadata');details.append(el('summary','','Earlier live verification'),el('p','muted',`${date(earlier.verified_at)} · ${earlier.gateway_calls} hosted calls · ${earlier.remote_selects} SELECTs. This is historical proof, not a current connection check.`));target.append(details);}
  }
  async function route() {
    const requested=location.hash.slice(1)||'/incidents';
    if(state.dirty && requested!==state.route && !window.confirm('Discard unsaved review changes?')){history.replaceState(null,'','#'+state.route);return;}
    state.dirty=false;state.route=requested;state.current=null;state.report=null;const token=++state.routeToken;
    const [routePath, queryString = ''] = requested.split('?', 2);
    const params = new URLSearchParams(queryString);
    state.activeTab = ['visuals', 'evidence', 'trail'].includes(params.get('view')) ? params.get('view') : 'report';
    state.visualService = (params.get('service') || '').slice(0, 200);
    state.visualRange = ['baseline', 'later'].includes(params.get('range')) ? params.get('range') : 'full';
    $('error-banner').hidden=true;
    for(const view of document.querySelectorAll('.workspace-view'))view.hidden=true;
    $('page-loading').hidden=false;
    const parts=routePath.split('/').filter(Boolean);const section=parts[0]||'incidents';
    for(const name of ['incidents','investigations','settings']){const active=name===section;$('nav-'+name).classList.toggle('active',active);if(active)$('nav-'+name).setAttribute('aria-current','page');else $('nav-'+name).removeAttribute('aria-current');}
    $('execution-badge').textContent=state.catalog.context.label||'';
    clear($('breadcrumbs')).append(el('span','',section==='settings'?'Settings':section==='investigations'?'Investigations':'Incidents'));
    try{
      if(section==='incidents'&&parts.length===1){$('view-incidents').hidden=false;renderQueue();}
      else if(section==='incidents'&&/^IC-\d{3}$/.test(parts[1]||'')){await renderCase(parts[1],token);if(token!==state.routeToken)return;$('view-case').hidden=false;clear($('breadcrumbs')).append(link('Incidents','/incidents'),el('span','breadcrumb-separator','/'),el('span','',parts[1]));}
      else if(section==='investigations'&&parts.length===1){await loadRuns();if(token!==state.routeToken)return;$('view-library').hidden=false;renderLibrary();}
      else if(section==='investigations'&&/^[a-f0-9]{20}$/.test(parts[1]||'')){await renderInvestigation(parts[1],token);if(token!==state.routeToken)return;$('view-investigation').hidden=false;}
      else if(section==='settings'&&parts.length===1){$('view-settings').hidden=false;renderSettings();}
      else throw new Error('This workspace page does not exist. Use Incidents or Investigations to continue.');
    }catch(error){if(token===state.routeToken)showError(error);}
    finally{if(token===state.routeToken)$('page-loading').hidden=true;}
  }
  async function refreshCatalog() {
    if(state.refreshBusy)return;state.refreshBusy=true;$('refresh-cases').disabled=true;$('refresh-cases').textContent='Refreshing…';
    try{state.catalog=await post('/api/cases/refresh');state.incidents=state.catalog.cases;updateGlobals();if(state.report)updateRecordMetadata();if(state.route==='/incidents')renderQueue();}
    catch(error){await loadCatalog().catch(()=>{});if(state.route==='/incidents')renderQueue();showError(error);}
    finally{state.refreshBusy=false;$('refresh-cases').disabled=false;$('refresh-cases').textContent='Refresh telemetry';}
  }
  async function startRun() {
    if(state.starting||!canStart())return;state.starting=true;$('case-new-run').disabled=true;
    const incident=state.caseId;
    if(!state.requestKeys.has(incident))state.requestKeys.set(incident,crypto.randomUUID());
    try{
      const run=await post('/api/investigations',{incident_id:incident,idempotency_key:state.requestKeys.get(incident)});
      state.requestKeys.delete(incident);await loadCatalog();location.hash='/investigations/'+run.id;
    }catch(error){if(error.details?.investigation_id){location.hash='/investigations/'+error.details.investigation_id;}else showError(error);}
    finally{state.starting=false;$('case-new-run').disabled=!canStart()||!!state.catalog.active;}
  }
  async function saveReview() {
    if(!state.current||!state.dirty)return;
    const identity=state.current.id, token=state.routeToken;
    $('save-review').disabled=true;$('review-note').disabled=true;$('reviewed').disabled=true;
    try{
      const review=await post('/api/investigations/'+identity+'/review',{note:$('review-note').value,reviewed:$('reviewed').checked,version:state.reviewVersion});
      if(token===state.routeToken&&state.current?.id===identity){
        state.current.review=review;state.current.reviewed_at=review.reviewed?'reviewed':null;state.reviewVersion=review.version;state.dirty=false;
        $('review-state').textContent=review.reviewed?'Reviewed':'Needs review';$('review-state').dataset.status=$('review-state').textContent;$('review-feedback').textContent='Saved.';
      }
      await loadCatalog();
    }catch(error){if(token===state.routeToken){showError(error);$('review-feedback').textContent='Not saved. Your text is still here.';}}
    finally{if(token===state.routeToken&&state.current?.id===identity){$('review-note').disabled=false;$('reviewed').disabled=false;$('save-review').disabled=!state.dirty;}}
  }
  let polling=false;
  async function poll() {
    if(polling||document.hidden)return;polling=true;
    try{
      if(state.current&&['queued','running'].includes(state.current.state)){
        const identity=state.current.id, token=state.routeToken;
        const run=await request('/api/investigations/'+identity);
        if(token===state.routeToken&&state.current?.id===identity)showRun(run,false);
      }
      if(state.catalog.active||state.current&&['queued','running'].includes(state.current.state)){
        await loadCatalog();if(state.route==='/incidents')renderQueue();
        else if(state.route==='/investigations'){await loadRuns();renderLibrary();}
        else if(state.route.startsWith('/incidents/'))await renderCase(state.caseId,state.routeToken);
      }
    }catch(error){if(state.current&&['queued','running'].includes(state.current.state))$('run-query-count').textContent='Connection interrupted. Checking again; the run will not be restarted.';}
    finally{polling=false;}
  }
  $('case-search').addEventListener('input',renderQueue);$('case-status').addEventListener('change',renderQueue);
  $('library-search').addEventListener('input',renderLibrary);$('library-filter').addEventListener('change',renderLibrary);
  $('refresh-cases').addEventListener('click',refreshCatalog);$('case-new-run').addEventListener('click',startRun);
  $('dismiss-error').addEventListener('click',()=>{$('error-banner').hidden=true;});
  $('import-button').addEventListener('click',()=>{$('saved-record-input').click();});
  $('saved-record-input').addEventListener('change',async event=>{
    const file=event.target.files?.[0];if(!file)return;$('import-button').disabled=true;
    try{if(file.size>5*1024*1024)throw new Error('Import a JSON report no larger than 5 MiB.');let value;try{value=JSON.parse(await file.text());}catch{throw new Error('Choose a valid investigation JSON report.');}validateSavedRecord(value);const run=await post('/api/investigations/import',value);location.hash='/investigations/'+run.id;}
    catch(error){showError(error);}finally{event.target.value='';$('import-button').disabled=false;}
  });
  $('review-note').addEventListener('input',()=>{state.dirty=true;$('save-review').disabled=false;$('review-feedback').textContent='Unsaved changes';});
  $('reviewed').addEventListener('change',()=>{state.dirty=true;$('save-review').disabled=false;$('review-feedback').textContent='Unsaved changes';});
  $('save-review').addEventListener('click',saveReview);$('review-jump').addEventListener('click',()=>{$('review-panel').scrollIntoView({behavior:'smooth',block:'start'});$('review-note').focus({preventScroll:true});});
  $('check-connections').addEventListener('click',async()=>{
    $('check-connections').disabled=true;$('check-connections').textContent='Checking…';
    try{const result=await post('/api/connections/check');const target=clear($('connection-results'));target.hidden=false;target.append(el('h2','','Connection check'),el('p','',`Telemetry: ${result.data.state}. Nightshift: ${result.model.state}${result.model.model_advertised===false?' (configured model not advertised)':''}.`),el('p','muted',`Checked ${date(result.checked_at)}. No inference was sent.`));}
    catch(error){showError(error);}finally{$('check-connections').disabled=false;$('check-connections').textContent='Check connections';}
  });
  for(const tab of document.querySelectorAll('[data-tab]')){
    tab.addEventListener('click',()=>switchTab(tab.dataset.tab));
    tab.addEventListener('keydown',event=>{const tabs=[...document.querySelectorAll('[data-tab]')];let i=tabs.indexOf(tab);if(event.key==='ArrowRight')i=(i+1)%tabs.length;else if(event.key==='ArrowLeft')i=(i+tabs.length-1)%tabs.length;else if(event.key==='Home')i=0;else if(event.key==='End')i=tabs.length-1;else return;event.preventDefault();switchTab(tabs[i].dataset.tab,true);});
  }
  for(const filter of document.querySelectorAll('[data-filter]'))filter.addEventListener('click',()=>{state.filter=filter.dataset.filter;updateFilter();renderEvidence();});
  $('export-button').addEventListener('click',()=>{if(!state.report)return;const url=URL.createObjectURL(new Blob([JSON.stringify(state.report,null,2)],{type:'application/json'}));const a=el('a');a.href=url;a.download=`incidentcourt-${state.report.incident_id}-${state.report.run_id}.json`;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);});
  window.addEventListener('hashchange',route);window.addEventListener('beforeunload',event=>{if(state.dirty){event.preventDefault();event.returnValue='';}});
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)poll();});setInterval(poll,2000);
  async function initialize(){
    const [settings,catalog]=await Promise.allSettled([request('/api/status'),loadCatalog()]);
    if(settings.status==='fulfilled')state.status=settings.value;
    if(catalog.status==='rejected')showError(catalog.reason);
    updateGlobals();await route();
    if(!state.catalog.context.cached_at)refreshCatalog();
  }
  initialize();
})();
