/** Thin JSON-lines bridge to the unchanged, pinned Meterless H-MEM reference. */
import fs from 'node:fs';
import { HMEM } from '../vendor/meterless-hmem/src/index.ts';

const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const engine = new HMEM({persistDir: input.directory, actor: 'incidentcourt'});
let result: unknown;
if (input.action === 'remember') {
  const p = input.payload;
  const existing = engine.store.all().find(m => m.missionId === p.incident_id && m.content === p.content);
  result = existing ?? engine.add({
    content: p.content, type: 'factual', layer: 'long_term', confidence: p.confidence,
    source: `investigation:${p.incident_id}`,
    missionId: p.incident_id,
    tags: ['verified-investigation', `split:${p.split}`, ...p.services],
    provenance: {origin: 'plan_completion', learnedAt: Date.now(), label: p.verifier,
      refs: p.evidence_ids.map((id: string) => `${p.incident_id}:${id}`)},
  });
} else if (input.action === 'retrieve') {
  const p = input.payload;
  // Eligibility is applied BEFORE the official ranker sees any records. The
  // queried incident and holdout investigations can never enter prompt context.
  const eligible = engine.store.all().filter(m => m.missionId !== p.exclude_incident_id
    && m.tags.includes('verified-investigation') && !m.tags.includes('split:holdout')
    && (!p.services?.length || p.services.some((service: string) => m.tags.includes(service))));
  const view = new HMEM({actor:'incidentcourt-recall'});
  view.store.restoreAll(eligible, engine.store.snapshotAll().seq);
  const recalled = view.query(p.query, {topN:p.limit});
  for (const item of recalled.memories) {
    engine.store.touch(item.memory.id);
    engine.ledger.log(item.memory.id, 'read', 'incidentcourt', {details:{
      query:p.query, excludedIncident:p.exclude_incident_id, relevance:item.relevance,
    }});
  }
  engine.store.persist();
  result = {...recalled, trace:{...recalled.trace, engine:'Meterless H-MEM reference',
    revision:'1742dec56a66e58eef9dda2d2b865823758ca8cd',
    excludedIncident:p.exclude_incident_id, eligibleServiceScope:p.services ?? [], eligibleCount:eligible.length,
    embedding:'upstream deterministic 64-dimensional FNV-1a lexical embedding (not neural)',
  }};
} else if (input.action === 'status') {
  result = {engine:'Meterless H-MEM reference',
    revision:'1742dec56a66e58eef9dda2d2b865823758ca8cd',
    memories:engine.store.all().length, ledger:engine.ledger.stats()};
} else if (input.action === 'audit') {
  result = engine.ledger.all();
} else { throw new Error('Unknown bridge action'); }
process.stdout.write(JSON.stringify(result));
