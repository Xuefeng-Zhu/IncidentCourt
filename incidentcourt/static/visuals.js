'use strict';

/* Data-bound, dependency-free views of immutable investigation evidence. */
(function (root) {
  const numeric = value => typeof value === 'number' && Number.isFinite(value);
  const rows = value => Array.isArray(value) ? value : [];
  const fmt = value => !numeric(value) ? 'Not recorded' : new Intl.NumberFormat('en-US', {maximumSignificantDigits: 4}).format(value);
  const count = value => numeric(value) ? new Intl.NumberFormat('en-US').format(value) : 'not recorded';

  function formatTime(value, full = false) {
    if (!numeric(value)) return 'Not recorded';
    const date = new Date(value * 1000);
    if (!Number.isFinite(date.getTime())) return 'Not recorded';
    return full ? date.toISOString().replace('T', ' ').replace(/(?:\.000)?Z$/, ' UTC') : new Intl.DateTimeFormat('en-GB', {hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23',timeZone:'UTC'}).format(date);
  }

  function comparisonScale(baseline, current, width = 600) {
    const values = [baseline, current].filter(numeric);
    if (!values.length) return {min:null,max:null,zero:null,baseline:null,current:null};
    let min = Math.min(0, ...values), max = Math.max(0, ...values);
    if (min === max) max = min + 1;
    // Normalize before subtraction: signed finite extremes can otherwise
    // overflow a max-minus-min range and create NaN SVG coordinates.
    const magnitude = Math.max(Math.abs(min), Math.abs(max), Number.MIN_VALUE);
    const low = min / magnitude, high = max / magnitude;
    const position = value => 12 + (value / magnitude - low) / (high - low) * (width - 24);
    const zero = position(0);
    const bar = value => !numeric(value) ? null : {x: Math.min(zero, position(value)), width: Math.abs(position(value) - zero), endpoint: position(value)};
    return {min, max, zero, baseline: bar(baseline), current: bar(current)};
  }

  function getDisplayRange(window, requested = 'full') {
    if (!window || !numeric(window.start) || !numeric(window.end) || window.start >= window.end) return null;
    const recorded = !window.inferredExtent && numeric(window.baselineEnd) && numeric(window.comparisonStart) && window.start < window.baselineEnd && window.baselineEnd <= window.comparisonStart && window.comparisonStart < window.end;
    if (recorded && requested === 'baseline') return {start:window.start,end:window.baselineEnd,range:'baseline'};
    if (recorded && requested === 'later') return {start:window.comparisonStart,end:window.end,range:'later'};
    return {start:window.start,end:window.end,range:'full'};
  }

  function layoutGraph(edges, selected = '') {
    const names = [...new Set(edges.flatMap(edge => [edge.caller, edge.callee]))].sort();
    const connected = selected ? new Set([selected, ...edges.filter(edge => edge.caller === selected || edge.callee === selected).flatMap(edge => [edge.caller, edge.callee])]) : null;
    const ordered = names.slice().sort((a, b) => (a === selected ? -1 : b === selected ? 1 : 0) || (connected ? Number(connected.has(b)) - Number(connected.has(a)) : 0) || a.localeCompare(b));
    const visible = new Set(ordered.slice(0, 16));
    const links = edges.filter(edge => visible.has(edge.caller) && visible.has(edge.callee));
    const incoming = new Map([...visible].map(name => [name, 0]));
    for (const edge of links) if (edge.caller !== edge.callee) incoming.set(edge.callee, incoming.get(edge.callee) + 1);
    const rank = new Map([...visible].map(name => [name, 0]));
    const queue = [...visible].filter(name => !incoming.get(name)).sort();
    const visited = new Set();
    while (queue.length) {
      const name = queue.shift();
      if (visited.has(name)) continue;
      visited.add(name);
      for (const edge of links.filter(item => item.caller === name && item.callee !== name)) {
        rank.set(edge.callee, Math.min(3, Math.max(rank.get(edge.callee), rank.get(name) + 1)));
        incoming.set(edge.callee, incoming.get(edge.callee) - 1);
        if (!incoming.get(edge.callee)) queue.push(edge.callee);
      }
    }
    // Cycles keep a deterministic column rather than inventing a causal order.
    const groups = new Map();
    for (const name of [...visible].sort()) {
      const column = rank.get(name);
      if (!groups.has(column)) groups.set(column, []);
      groups.get(column).push(name);
    }
    const columns = Math.max(1, ...groups.keys()) + 1;
    const width = Math.max(440, columns * 190 + 30);
    const contentHeight = Math.max(210, ...[...groups.values()].map(group => group.length * 76 + 36));
    // Keep skip-layer links in explicit routing gutters, outside every box.
    const gutter = 56;
    const height = contentHeight + gutter * 2;
    const nodes = [];
    for (const [column, group] of groups) for (let index = 0; index < group.length; index++) {
      nodes.push({name: group[index], x: 100 + column * 190, y: gutter + (index + 1) * contentHeight / (group.length + 1), related: !connected || connected.has(group[index])});
    }
    return {nodes, edges: links, width, height, omitted: names.length - visible.size};
  }

  function routeGraphEdge(layout, edge, index = 0) {
    const start = layout.nodes.find(node => node.name === edge.caller);
    const end = layout.nodes.find(node => node.name === edge.callee);
    if (!start || !end) return null;
    const forward = end.x > start.x;
    const direction = forward ? 1 : -1;
    const from = [start.x + direction * 80, start.y], to = [end.x - direction * 80, end.y];
    let segments, origin = from, usesGutter = false;
    if (edge.caller === edge.callee) {
      origin = [start.x + 80, start.y - 10];
      segments = [{type:'C',points:[[start.x+140,start.y-70],[start.x+140,start.y+70],[start.x+80,start.y+10]]}];
    } else if (start.x === end.x) {
      origin = [start.x + 80, start.y];
      segments = [{type:'C',points:[[start.x+122,start.y],[end.x+122,end.y],[end.x+80,end.y]]}];
    } else if (Math.abs(end.x - start.x) > 190) {
      usesGutter = true;
      const lane = 12 + (index % 4) * 8;
      const y = end.y < start.y || end.y === start.y && index % 2 === 0 ? lane : layout.height - lane;
      // Vertical turns stay in the 30px inter-column gaps. The horizontal
      // crossing is wholly above/below nodes, so it cannot imply an
      // intermediate service is the source of the edge.
      const exit = from[0] + direction * 14, enter = to[0] - direction * 14;
      segments = [
        {type:'C',points:[[exit,from[1]],[exit,y],[exit,y]]},
        {type:'L',points:[[enter,y]]},
        {type:'C',points:[[enter,y],[enter,to[1]],to]}
      ];
    } else {
      const offset = direction * 50;
      segments = [{type:'C',points:[[from[0]+offset,from[1]],[to[0]-offset,to[1]],to]}];
    }
    const path = `M ${origin.join(' ')} ` + segments.map(segment => `${segment.type} ${segment.points.map(point=>point.join(' ')).join(',')}`).join(' ');
    return {path,origin,segments,usesGutter};
  }

  function render(container, report, callbacks = {}) {
    if (!container || !root.IncidentVisualData) return;
    const data = root.IncidentVisualData.buildVisualData(report);
    const el = (tag, className, text) => {
      const node = document.createElement(tag);
      if (className) node.className = className;
      if (text !== undefined) node.textContent = String(text);
      return node;
    };
    const svg = (tag, attributes = {}, text) => {
      const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
      for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, String(value));
      if (text !== undefined) node.textContent = String(text);
      return node;
    };
    const button = (label, action, className = 'visual-source') => {
      const node = el('button', className, label); node.type = 'button';
      node.addEventListener('click', action); return node;
    };
    const source = item => {
      const group = el('div', 'visual-sources');
      if (item.evidenceId) group.append(button(`Evidence ${item.evidenceId}`, () => callbacks.inspectEvidence?.(item.evidenceId)));
      if (item.queryId) group.append(button(`Query ${item.queryId}`, () => callbacks.inspectQuery?.(item.queryId)));
      return group;
    };
    const section = (title, description, number) => {
      const node = el('section', 'visual-section');
      node.id = `visual-section-${number}`;
      const top = el('div', 'visual-section-heading');
      const heading = el('h3', '', title); heading.tabIndex = -1;
      top.append(el('span', 'visual-section-number', number), heading);
      node.append(top, el('p', 'visual-description', description)); return node;
    };
    const empty = text => el('p', 'visual-empty', text);
    const time = value => formatTime(value);
    const fullTime = value => formatTime(value, true);
    let selected = data.services.includes(callbacks.selectedService) ? callbacks.selectedService : '';
    if (callbacks.selectedService && !selected) callbacks.onSelectionChange?.('');
    let currentRange = getDisplayRange(data.window, callbacks.selectedRange)?.range || 'full';
    if (callbacks.selectedRange && currentRange !== callbacks.selectedRange) callbacks.onRangeChange?.(currentRange);
    let expanded = false;
    let selectedEdge = null;
    container.replaceChildren(); container.classList.add('visual-evidence');
    const intro = el('div', 'visual-intro');
    const introText = el('div');
    introText.append(el('h3', '', 'Follow the evidence'), el('p', '', 'Compare measured changes, check coverage, and inspect observed service calls.'));
    const serviceControl = el('label', 'visual-service-control');
    serviceControl.append(el('span', '', 'Focus service'));
    const select = el('select'); select.setAttribute('aria-label', 'Focus visual evidence on a service');
    select.append(el('option', '', 'All services')); select.firstChild.value = '';
    for (const service of data.services) { const option = el('option', '', service); option.value = service; select.append(option); }
    select.value = selected; serviceControl.append(select); intro.append(introText, serviceControl);
    const status = el('p', 'visual-selection-status'); status.setAttribute('aria-live', 'polite');
    const panels = el('div', 'visual-panels');
    const jumps = el('nav', 'visual-section-jumps'); jumps.setAttribute('aria-label', 'Visual evidence sections');
    for (const [label, number] of [['Comparisons','01'],['Coverage','02'],['Dependencies','03']]) {
      jumps.append(button(label, () => {
        const target = panels.querySelector(`#visual-section-${number}`);
        if (!target) return;
        target.querySelector('h3').focus({preventScroll:true});
        target.scrollIntoView({block:'start',behavior:'auto'});
      }, 'visual-section-jump'));
    }
    container.append(intro, status, jumps, panels);

    function focusService(service, focus = false) {
      const focusName = service || selected;
      selected = data.services.includes(service) ? service : ''; selectedEdge = null; expanded = false;
      select.value = selected; callbacks.onSelectionChange?.(selected); draw();
      if (focus) {
        const node = [...panels.querySelectorAll('.visual-node')].find(item => item.dataset.service === focusName);
        if (node) { node.focus({preventScroll:true}); node.scrollIntoView({block:'center',behavior:'auto'}); }
        else select.focus();
      }
    }
    select.addEventListener('change', () => focusService(select.value));

    function comparisons() {
      const node = section('Baseline vs later observations', 'Paired means from the recorded investigation. Each measurement has its own zero-inclusive scale; bar lengths are not comparable across cards.', '01');
      const values = data.comparisons.filter(item => !selected || item.service === selected);
      const key = el('div', 'visual-key');
      key.append(el('span', 'visual-key-baseline', 'Baseline'), el('span', 'visual-key-current', 'Later window'));
      node.append(key);
      if (!values.length) node.append(empty('No comparable measurement rows were recorded for this selection. Raw evidence remains available.'));
      const cards = el('div', 'visual-comparisons');
      for (const item of values.slice(0, expanded ? values.length : 8)) {
        const card = el('article', 'visual-comparison');
        const top = el('div', 'visual-comparison-heading');
        const titles = el('div'); titles.append(el('p', 'visual-service', item.service), el('h4', '', item.metric));
        top.append(titles, el('span', 'visual-kind', item.kind === 'traces' ? 'Trace duration' : item.kind === 'counterevidence' ? 'Counterevidence' : 'Metric'));
        card.append(top);
        let direction = 'Incomplete comparison';
        if (numeric(item.baseline) && numeric(item.current)) {
          const increase = item.current > item.baseline;
          const change = item.baseline === 0 ? `Δ ${fmt(item.current)}; zero baseline` : (() => {
            const relative = (item.current / Math.abs(item.baseline) - Math.sign(item.baseline)) * 100;
            return numeric(relative) ? `${increase ? '+' : ''}${fmt(relative)}%` : 'relative change exceeds display range';
          })();
          direction = item.current === item.baseline ? 'No change' : `${increase ? 'Increase' : 'Decrease'} · ${change}`;
        }
        card.append(el('p', 'visual-change', direction));
        const scale = comparisonScale(item.baseline, item.current);
        const chart = svg('svg', {viewBox:'0 0 600 76',preserveAspectRatio:'none',class:'visual-bars',role:'img','aria-label':`${item.metric} for ${item.service}: baseline ${fmt(item.baseline)}, later ${fmt(item.current)} ${item.unit}.`});
        chart.append(svg('line',{x1:scale.zero,x2:scale.zero,y1:0,y2:76,class:'visual-zero'}));
        for (const [name, y] of [['baseline',12],['current',44]]) {
          const bar = scale[name];
          if (bar) {
            chart.append(svg('rect',{x:bar.x,y,width:bar.width,height:17,rx:2,class:`visual-bar-${name}`}));
            if (bar.width === 0) chart.append(svg('circle',{cx:bar.endpoint,cy:y+8.5,r:3,class:`visual-bar-${name}`}));
          }
        }
        const numbers = el('dl', 'visual-values');
        for (const [label, value, sample] of [['Baseline',item.baseline,item.baselineN],['Later',item.current,item.currentN]]) {
          const pair = el('div'); pair.append(el('dt', '', label), el('dd', '', `${fmt(value)}${numeric(value) && item.unit === 'ms' ? ' ms' : ''}`), el('span', '', `n=${count(sample)}`)); numbers.append(pair);
        }
        if (numeric(item.baseline) || numeric(item.current)) {
          card.append(chart, numbers, el('p', 'visual-scale', `${item.baseline === 0 && item.current === 0 ? 'Both recorded means are zero.' : `Scale ${fmt(scale.min)} to ${fmt(scale.max)} ${item.unit}.`} ${item.unit === 'ms' ? 'Inclusive mean span duration.' : 'Original metric units were not recorded.'}`), source(item));
        } else card.append(numbers,el('p','visual-scale','Neither window has a recorded numeric mean. No chart is inferred.'),source(item));
        cards.append(card);
      }
      node.append(cards);
      if (values.length > 8) node.append(button(expanded ? 'Show fewer measurements' : `Show all ${values.length} measurements`, () => {expanded = !expanded; draw();}, 'visual-expand'));
      return node;
    }

    function coverage() {
      const node = section('Telemetry coverage', 'First and last recorded observations, not continuous availability. Blank intervals and missing signals cannot be treated as healthy.', '02');
      const window = data.window;
      const displayRange = getDisplayRange(window, currentRange);
      if (!displayRange) {
        node.append(empty('No usable time bounds were recorded. Coverage cannot be drawn from this report.')); return node;
      }
      const ranges = el('div', 'visual-range-controls'); ranges.setAttribute('role','group'); ranges.setAttribute('aria-label','Coverage display range');
      for (const [label, range] of [['Full window','full'],['Baseline','baseline'],['Later window','later']]) {
        if (range !== 'full' && getDisplayRange(window,range)?.range !== range) continue;
        const choice = button(label, () => {
          currentRange=range; callbacks.onRangeChange?.(range); draw();
          panels.querySelector(`[data-visual-range="${range}"]`)?.focus();
        }, 'visual-range-button');
        choice.dataset.visualRange=range;choice.setAttribute('aria-pressed',String(displayRange.range===range));ranges.append(choice);
      }
      node.append(ranges,el('p','visual-range-note','Display range only; recorded comparison means stay unchanged. Observation counts are full-record totals.'));
      const percent = value => Math.max(0, Math.min(1, (value - displayRange.start) / (displayRange.end - displayRange.start)));
      const bounds = el('div', 'visual-time-bounds'); bounds.append(el('time', '', fullTime(displayRange.start)), el('time', '', fullTime(displayRange.end))); node.append(bounds);
      if (window.inferredExtent || !numeric(window.baselineEnd) || !numeric(window.comparisonStart)) {
        node.append(el('p', 'visual-caveat', 'This older record contains telemetry bounds only. Baseline and later-window boundaries are unavailable; no healthy period is inferred.'));
      } else {
        const phases = svg('svg', {viewBox:'0 0 600 38',preserveAspectRatio:'none',class:'visual-window-bands',role:'img','aria-label':`Displayed ${displayRange.range} range: ${fullTime(displayRange.start)} to ${fullTime(displayRange.end)}. Recorded baseline and later-window bounds; comparison means are unchanged.`});
        phases.append(svg('rect',{x:0,y:4,width:percent(window.baselineEnd)*600,height:28,class:'visual-phase-baseline'}),svg('rect',{x:percent(window.comparisonStart)*600,y:4,width:(1-percent(window.comparisonStart))*600,height:28,class:'visual-phase-current'}));
        node.append(phases);
        const phaseLabels = el('div','visual-window-labels');
        if(displayRange.range==='full')phaseLabels.append(el('span','',`Baseline ends ${time(window.baselineEnd)}`),el('span','',`Later begins ${time(window.comparisonStart)}`));
        else phaseLabels.append(el('span','',`${displayRange.range==='baseline'?'Baseline':'Later'} window · ${time(displayRange.start)}–${time(displayRange.end)} UTC`));
        node.append(phaseLabels);
      }
      const coverageList = el('div', 'visual-coverage-list');
      for (const kind of ['metrics','logs','traces']) {
        const ranges = data.coverage.filter(row => row.kind === kind);
        for (const [rangeIndex, item] of (ranges.length ? ranges : [null]).entries()) {
        const row = el('article', 'visual-coverage-row');
        const heading = el('div', 'visual-coverage-heading'); heading.append(el('h4','',`${kind}${ranges.length > 1 ? ` · range ${rangeIndex + 1} of ${ranges.length}` : ''}`),el('span','',item ? `${count(item.observations)} observations · full record` : 'Not recorded')); row.append(heading);
        const chart = svg('svg',{viewBox:'0 0 600 32',preserveAspectRatio:'none',class:'visual-coverage-chart',role:'img','aria-label':item && numeric(item.first) && numeric(item.last) ? `${kind}: ${fullTime(item.first)} to ${fullTime(item.last)}. First-to-last extent only; internal gaps are unknown.` : `${kind}: no usable recorded bounds.`});
        chart.append(svg('rect',{x:0,y:8,width:600,height:16,class:'visual-coverage-track'}));
        if (item && numeric(item.first) && numeric(item.last) && item.last >= item.first && item.observations > 0) {
          if(item.last>=displayRange.start&&item.first<=displayRange.end){
            const first=Math.max(item.first,displayRange.start),last=Math.min(item.last,displayRange.end);
            const start = percent(first)*600, end = percent(last)*600;
            chart.append(svg('rect',{x:start,y:8,width:Math.max(0,end-start),height:16,class:'visual-coverage-span'}),svg('line',{x1:start,x2:start,y1:4,y2:28,class:'visual-coverage-cap'}),svg('line',{x1:end,x2:end,y1:4,y2:28,class:'visual-coverage-cap'}));
            row.append(chart,el('p','visual-coverage-times',`${time(first)}–${time(last)} UTC · displayed extent; internal gaps unknown`));
          }else row.append(chart,el('p','visual-coverage-times','Recorded extent lies outside the displayed range.'));
        } else row.append(chart,el('p','visual-coverage-times','No recorded observations with valid time bounds.'));
        if(item)row.append(source(item));
        coverageList.append(row);
        }
      }
      node.append(coverageList);
      const points = rows(report.timeline).filter(point => numeric(point.ts) && point.ts >= displayRange.start && point.ts <= displayRange.end && (!selected || rows(point.evidence_ids).some(id => rows(report.evidence).some(evidence => evidence.id === id && evidence.service === selected)))).slice(0,20);
      if (points.length) {
        const markerStrip=svg('svg',{viewBox:'0 0 600 32',preserveAspectRatio:'none',class:'visual-event-markers',role:'img','aria-label':`${points.length} recorded observations positioned on the same time scale as coverage. Exact times and sources are in the expandable list below.`});
        markerStrip.append(svg('line',{x1:0,x2:600,y1:16,y2:16,class:'visual-event-axis'}));
        for(const point of points)markerStrip.append(svg('circle',{cx:Math.max(4,Math.min(596,percent(point.ts)*600)),cy:16,r:4,class:'visual-event-marker'}));
        node.append(el('p','visual-marker-label','Recorded timeline observations'),markerStrip);
        const observations = el('details','visual-events'); observations.append(el('summary','',`${points.length} recorded timeline observations`));
        const items=el('ol','visual-event-list');
        for (const point of points) { const item=el('li'); item.append(el('time','',time(point.ts)+' UTC'),el('span','',point.title)); for (const id of rows(point.evidence_ids)) item.append(button(id,()=>callbacks.inspectEvidence?.(id))); items.append(item); }
        observations.append(items);node.append(observations);
      }
      if (window.queryId) node.append(source({queryId:window.queryId}));
      return node;
    }

    function dependencies() {
      const node = section('Observed service dependencies', 'Arrows follow sampled parent → child spans. They show observed calls, not a proven causal path; missing parent spans can hide relationships.', '03');
      if (!data.edges.length) {node.append(empty('No linked cross-service spans were recorded. No dependency graph is inferred.'));return node;}
      const layout = layoutGraph(data.edges,selected);
      const diagram = svg('svg',{viewBox:`0 0 ${layout.width} ${layout.height}`,class:'visual-dependency-diagram',role:'group','aria-label':'Observed caller-to-callee relationships. Use the service selector and relationship list below for keyboard access and exact values.'});
      const defs=svg('defs'); const arrow=svg('marker',{id:`visual-arrow-${report.run_id}`,viewBox:'0 0 10 10',refX:9,refY:5,markerWidth:6,markerHeight:6,orient:'auto-start-reverse'}); arrow.append(svg('path',{d:'M 0 0 L 10 5 L 0 10 z',class:'visual-arrowhead'})); defs.append(arrow);diagram.append(defs);
      const positions=new Map(layout.nodes.map(item=>[item.name,item]));
      const maxSpans=Math.max(1,...layout.edges.map(edge=>edge.spans));
      const edgeDetail=el('div','visual-edge-detail');edgeDetail.setAttribute('aria-live','polite');
      function showEdge(edge, reveal = true){
        selectedEdge=edge;
        edgeDetail.replaceChildren(el('h4','',`${edge.caller} → ${edge.callee}`),el('p','',`${count(edge.spans)} sampled spans · mean child duration ${fmt(edge.childMeanMs)}${numeric(edge.childMeanMs)?' ms':''}.`),source(edge));
        if (reveal) { edgeDetail.tabIndex=-1; edgeDetail.focus({preventScroll:true}); edgeDetail.scrollIntoView({block:'nearest',behavior:'auto'}); }
      }
      for (const [edgeIndex, edge] of layout.edges.entries()) {
        const path=routeGraphEdge(layout,edge,edgeIndex).path;
        const active=!selected||edge.caller===selected||edge.callee===selected;
        const group=svg('g',{class:active?'visual-link':'visual-link visual-link-muted'});
        group.append(svg('path',{d:path,class:'visual-dependency-edge','stroke-width':1.5+3*Math.sqrt(edge.spans/maxSpans),'marker-end':`url(#visual-arrow-${report.run_id})`}));
        const target=svg('path',{d:path,class:'visual-dependency-hit',tabindex:0,role:'button','aria-label':`Inspect ${edge.caller} to ${edge.callee}: ${count(edge.spans)} sampled spans`});
        target.addEventListener('click',()=>showEdge(edge));target.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();showEdge(edge);}});group.append(target);diagram.append(group);
      }
      for(const item of layout.nodes){
        const group=svg('g',{class:`visual-node${selected===item.name?' visual-node-selected':''}${!item.related?' visual-node-muted':''}`,transform:`translate(${item.x-80},${item.y-23})`,role:'button',tabindex:0,'aria-label':`Focus on ${item.name}${selected===item.name?', selected':''}`});
        group.dataset.service = item.name;
        group.append(svg('title',{},item.name),svg('rect',{width:160,height:46,rx:4}));
        const words=item.name.length>21?[item.name.slice(0,20),item.name.slice(20,40)+(item.name.length>40?'…':'')]:[item.name];
        const text=svg('text',{x:80,y:words.length>1?18:27,'text-anchor':'middle'});words.forEach((line,index)=>text.append(svg('tspan',{x:80,dy:index?15:0},line)));group.append(text);
        group.addEventListener('click',()=>focusService(selected===item.name?'':item.name,true));group.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();focusService(selected===item.name?'':item.name,true);}});diagram.append(group);
      }
      node.append(el('p','visual-diagram-key','Thicker arrows indicate more sampled spans. Select a service to highlight its immediate callers and callees.'),diagram);
      if(layout.omitted)node.append(el('p','visual-caveat',`Diagram shows ${layout.nodes.length} services; ${layout.omitted} more are outside this diagram. ${selected ? 'Relationships touching the focused service are listed below; select All services for the complete recorded list.' : 'All recorded relationships remain in the list below.'}`));
      if(selectedEdge)showEdge(selectedEdge,false);else edgeDetail.append(el('p','','Select an arrow or a relationship below to inspect its source.'));
      node.append(edgeDetail);
      const links=data.edges.filter(edge=>!selected||edge.caller===selected||edge.callee===selected);
      const list=el('div','visual-relationship-list');
      const listHeading=el('h4','visual-relationship-heading',`${links.length} recorded relationship${links.length===1?'':'s'}${selected?' touching '+selected:''}`);node.append(listHeading);
      for(const edge of links){const row=el('article','visual-relationship');row.append(button(`${edge.caller} → ${edge.callee}`,()=>showEdge(edge),'visual-relationship-name'),el('p','',`${count(edge.spans)} spans · mean child ${fmt(edge.childMeanMs)}${numeric(edge.childMeanMs)?' ms':''}`),source(edge));list.append(row);}
      if(!links.length)list.append(empty('No linked spans touch this service. The absence of a relationship does not prove isolation.'));
      node.append(list);return node;
    }

    function draw(){
      status.textContent=selected?`Showing recorded measurements for ${selected}; dependencies highlight its immediate relationships. Coverage remains incident-wide.`:'Showing all recorded services. Coverage remains incident-wide.';
      panels.replaceChildren(comparisons(),coverage(),dependencies());
      if(rows(data.warnings).length){const details=el('details','visual-warnings');details.append(el('summary','',`${data.warnings.length} visualization data note${data.warnings.length===1?'':'s'}`));const notes=el('ul');for(const warning of data.warnings)notes.append(el('li','',warning));details.append(notes);panels.append(details);}
    }
    draw();
    return {selectService:service=>focusService(service),selectedService:()=>selected};
  }

  const api={render,comparisonScale,layoutGraph,routeGraphEdge,formatTime,getDisplayRange};
  root.IncidentVisuals=api;
  if(typeof module==='object'&&module.exports)module.exports=api;
})(typeof window==='object'?window:globalThis);
