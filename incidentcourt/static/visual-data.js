'use strict';

/** Derive display data from saved query-backed rows, never from narrative text. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.IncidentVisualData = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  const list = value => Array.isArray(value) ? value : [];
  const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
  const finite = value => typeof value === 'number' && Number.isFinite(value) ? value : null;
  // Epoch seconds outside JavaScript Date's range cannot be labeled truthfully.
  const timestamp = value => finite(value) !== null && Math.abs(value) <= 8640000000000 ? value : null;
  const count = value => Number.isSafeInteger(value) && value >= 0 ? value : null;
  const label = value => typeof value === 'string' && value.trim().length > 0 && value.length <= 200 ? value : null;
  const own = (value, name) => Object.prototype.hasOwnProperty.call(value, name);

  function buildVisualData(report) {
    const comparisons = [], coverage = [], edges = [], warnings = [];
    const services = new Set(), comparisonKeys = new Set(), edgeKeys = new Set(), coverageKeys = new Set();
    const source = object(report) ? report : {};
    const queryIds = new Set(list(source.queries).filter(object).map(item => label(item.id)).filter(Boolean));
    const warn = message => { if (!warnings.includes(message)) warnings.push(message); };

    for (const evidence of list(source.evidence).slice(0, 500)) {
      if (!object(evidence) || !label(evidence.id) || !queryIds.has(evidence.query_id)) {
        warn('Some observations lack a matching source query and are not visualized.');
        continue;
      }
      const provenance = { evidenceId: evidence.id, queryId: evidence.query_id };
      let index = 0;
      for (const row of list(evidence.rows).slice(0, 1000)) {
        const rowIndex = index++;
        if (!object(row)) continue;
        const trace = evidence.kind === 'traces' && (own(row, 'baseline_ms') || own(row, 'current_ms'));
        const metric = ['metrics', 'counterevidence'].includes(evidence.kind) && (own(row, 'baseline') || own(row, 'current'));
        if (trace || metric) {
          const service = label(row.service) || label(evidence.service);
          const name = trace ? 'Mean span duration' : label(row.name);
          if (service && name) {
            const value = {
              id: `${evidence.id}:${rowIndex}`, service, metric: name, kind: evidence.kind,
              baseline: finite(trace ? row.baseline_ms : row.baseline),
              current: finite(trace ? row.current_ms : row.current),
              baselineN: count(row.baseline_n), currentN: count(row.current_n),
              unit: trace ? 'ms' : 'source units',
              changed: typeof row.changed === 'boolean' ? row.changed : null,
              ...provenance
            };
            // Repeated references to the same measurement do not become extra samples.
            const key = JSON.stringify([service, name, trace ? 'traces' : 'metrics', value.baseline,
              value.current, value.baselineN, value.currentN, value.queryId]);
            if (!comparisonKeys.has(key)) {
              comparisonKeys.add(key);
              if (comparisons.length < 100) { comparisons.push(value); services.add(service); }
              else warn('Metric comparison display is limited to 100 recorded measurements.');
            }
          }
        }

        if (['metrics', 'logs', 'traces'].includes(evidence.kind) && own(row, 'observations')) {
          const observations = count(row.observations), first = timestamp(row.first_ts), last = timestamp(row.last_ts);
          // An explicit zero-observation receipt is useful missing-data evidence.
          if (observations !== null && (observations === 0 && first === null && last === null ||
              observations > 0 && first !== null && last !== null && first <= last)) {
            const key = JSON.stringify([evidence.kind, first, last, observations]);
            if (!coverageKeys.has(key)) {
              coverageKeys.add(key);
              if (coverage.length < 12) coverage.push({kind: evidence.kind, first, last, observations, ...provenance});
              else warn('Coverage display is limited to 12 recorded ranges.');
            }
          } else warn('Some coverage rows have unusable counts or time bounds and are not drawn.');
        }

        if (evidence.kind === 'traces' && own(row, 'caller') && own(row, 'callee')) {
          const caller = label(row.caller), callee = label(row.callee), spans = count(row.spans);
          const duration = finite(row.child_mean_ms);
          if (!caller || !callee || spans === null || spans === 0) continue;
          const childMeanMs = duration !== null && duration >= 0 ? duration : null;
          const key = JSON.stringify([caller, callee, spans, childMeanMs, evidence.query_id]);
          if (edgeKeys.has(key)) continue;
          edgeKeys.add(key);
          if (edges.length < 80) {
            edges.push({caller, callee, spans, childMeanMs, ...provenance});
            services.add(caller); services.add(callee);
          } else warn('Dependency display is limited to 80 recorded edges.');
        }
      }
    }

    let window = null;
    const recorded = source.analysis_window;
    if (object(recorded)) {
      const start = timestamp(recorded.start_ts), end = timestamp(recorded.end_ts);
      const baselineEnd = timestamp(recorded.baseline_end_ts), comparisonStart = timestamp(recorded.comparison_start_ts);
      if (start !== null && end !== null && baselineEnd !== null && comparisonStart !== null &&
          start < baselineEnd && baselineEnd <= comparisonStart && comparisonStart < end &&
          queryIds.has(recorded.query_id)) {
        window = {start, end, baselineEnd, comparisonStart, inferredExtent: false, queryId: recorded.query_id};
      } else warn('Recorded analysis windows are invalid; only observed telemetry extent is shown.');
    }
    if (!window) {
      const bounds = coverage.filter(item => item.first !== null && item.last !== null);
      if (bounds.length) window = {
        start: Math.min(...bounds.map(item => item.first)), end: Math.max(...bounds.map(item => item.last)),
        baselineEnd: null, comparisonStart: null, inferredExtent: true, queryId: null
      };
    }
    return {comparisons, coverage, window, edges, services: [...services].sort(), warnings};
  }

  return {buildVisualData};
});
