"""Numeric renderer boundaries; no browser, provider or synthetic diagnosis."""
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]


class VisualGeometryTests(unittest.TestCase):
    def test_skip_layer_routes_do_not_cross_intermediate_service_boxes(self):
        script = r"""
const assert = require('node:assert/strict');
const report = require('./output_examples/example_1.json');
const {buildVisualData} = require('./incidentcourt/static/visual-data.js');
const {layoutGraph,routeGraphEdge} = require('./incidentcourt/static/visuals.js');
const graph = layoutGraph(buildVisualData(report).edges);
const node = name => graph.nodes.find(item=>item.name===name);
const skip = graph.edges.find(edge=>edge.caller==='frontend'&&edge.callee==='currencyservice');
assert(Math.abs(node(skip.callee).x-node(skip.caller).x)>190);
assert(routeGraphEdge(graph,skip).usesGutter);
let gutterRoutes = 0;
for (const [index,edge] of graph.edges.entries()) {
  const route = routeGraphEdge(graph,edge,index);
  if (!route.usesGutter) continue;
  gutterRoutes++;
  const obstacles = graph.nodes.filter(item=>item.name!==edge.caller&&item.name!==edge.callee);
  let start = route.origin;
  for (const segment of route.segments) {
    for (let step=0;step<=200;step++) {
      const t=step/200,u=1-t;
      const point=segment.type==='L'
        ? start.map((value,axis)=>u*value+t*segment.points[0][axis])
        : start.map((value,axis)=>u*u*u*value+3*u*u*t*segment.points[0][axis]+3*u*t*t*segment.points[1][axis]+t*t*t*segment.points[2][axis]);
      for(const obstacle of obstacles)assert(!(point[0]>obstacle.x-83&&point[0]<obstacle.x+83&&point[1]>obstacle.y-26&&point[1]<obstacle.y+26), `${edge.caller}→${edge.callee} crosses ${obstacle.name}`);
    }
    start=segment.points.at(-1);
  }
}
assert(gutterRoutes>=2);
assert.deepEqual(routeGraphEdge(graph,skip),routeGraphEdge(graph,skip));
"""
        result = subprocess.run(['node', '-e', script], cwd=ROOT, capture_output=True,
                                text=True, timeout=5, check=True)
        self.assertEqual(result.stdout, '')
        self.assertEqual(result.stderr, '')

    def test_finite_signed_extremes_missing_values_dates_and_bounded_graph(self):
        script = r"""
const assert = require('node:assert/strict');
const {comparisonScale, layoutGraph, formatTime, getDisplayRange} = require('./incidentcourt/static/visuals.js');
for (const [baseline, current] of [[0,0], [-4,8], [2,8], [null,5], [-9,-2], [-1e308,1e308], [Number.MIN_VALUE,0]]) {
  const scale = comparisonScale(baseline, current);
  assert(scale.min <= 0 && scale.max >= 0);
  for (const bar of [scale.baseline, scale.current]) if (bar) {
    assert(Number.isFinite(bar.x)); assert(Number.isFinite(bar.width));
    assert(bar.x >= 12); assert(bar.width >= 0); assert(bar.x + bar.width <= 588.000001);
  }
}
const extremes = comparisonScale(-1e308,1e308);
assert(extremes.baseline.width > 0 && extremes.current.width > 0);
assert.equal(comparisonScale(null,5).baseline, null);
assert.deepEqual(comparisonScale(null,null), {min:null,max:null,zero:null,baseline:null,current:null});
assert.equal(formatTime(1e99), 'Not recorded');
assert.equal(formatTime(-1e99,true), 'Not recorded');
assert.equal(formatTime(null,true), 'Not recorded');
assert.equal(formatTime(0,true), '1970-01-01 00:00:00 UTC');
const recordedWindow={start:10,end:100,baselineEnd:40,comparisonStart:70,inferredExtent:false};
assert.deepEqual(getDisplayRange(recordedWindow,'baseline'),{start:10,end:40,range:'baseline'});
assert.deepEqual(getDisplayRange(recordedWindow,'later'),{start:70,end:100,range:'later'});
assert.deepEqual(getDisplayRange(recordedWindow,'unknown'),{start:10,end:100,range:'full'});
assert.deepEqual(getDisplayRange({...recordedWindow,inferredExtent:true},'baseline'),{start:10,end:100,range:'full'});
assert.deepEqual(getDisplayRange({start:10,end:100},'later'),{start:10,end:100,range:'full'});
assert.equal(getDisplayRange({start:10,end:10}),null);
assert.equal(getDisplayRange(null),null);
const cycle = [{caller:'a',callee:'b',spans:3},{caller:'b',callee:'a',spans:2},{caller:'a',callee:'a',spans:1}];
assert.deepEqual(layoutGraph(cycle),layoutGraph(cycle));
assert.equal(layoutGraph(cycle).nodes.length,2);
const large = Array.from({length:80},(_,i)=>({caller:'service-'+i,callee:'target-'+i,spans:1}));
const layout = layoutGraph(large,'target-79');
assert.equal(layout.nodes.length,16); assert.equal(layout.omitted,144);
assert(layout.nodes.some(node=>node.name==='target-79'));
assert(layout.nodes.every(node=>Number.isFinite(node.x)&&Number.isFinite(node.y)));
"""
        result = subprocess.run(['node', '-e', script], cwd=ROOT, capture_output=True,
                                text=True, timeout=5, check=True)
        self.assertEqual(result.stdout, '')
        self.assertEqual(result.stderr, '')


if __name__ == '__main__':
    unittest.main()
