/**
 * BirdNET Geomodel – Inference Web Worker
 *
 * Runs ONNX Runtime Web in a dedicated thread so the UI stays responsive.
 *
 * Protocol (postMessage):
 *   Main -> Worker:  { type: "init",  modelUrl }
 *   Worker -> Main:  { type: "init",  ok, error? }
 *   Main -> Worker:  { type: "infer", id, flatInputs, batchSize, task, ... }
 *   Worker -> Main:  { type: "infer", id, data: ArrayBuffer }
 *                   | { type: "infer", id, error }
 *
 * `task` selects how the (batchSize × nSpecies) model output is reduced
 * *inside the worker* so we only transfer small arrays back to the UI
 * thread (critical for memory + responsiveness when sweeping many cells):
 *   "raw"      — return the full output (batchSize × nSpecies floats).
 *   "column"   — return one species column (batchSize floats); needs speciesIdx.
 *   "richness" — return a per-cell count of species ≥ threshold (batchSize
 *                floats); optional mask (Uint8Array) restricts to a group.
 */

/* global ort */
// Vendored locally (wasm execution-provider build only) so the app runs fully
// offline once cached by the service worker — no CDN dependency at runtime.
// Absolute URLs (not bare specifiers) so ORT's dynamic import() resolves inside
// the worker. The wasm "glue" is shipped with a .js extension (not .mjs) so it
// loads as a module on any host — some static hosts (incl. GitHub Pages) serve
// .mjs as application/octet-stream, which fails strict module MIME checking.
var ORT_BASE = new URL("vendor/ort/", self.location.href).href;
importScripts(ORT_BASE + "ort.wasm.min.js");
ort.env.wasm.wasmPaths = {
  mjs: ORT_BASE + "ort-wasm-simd-threaded.mjs.js",
  wasm: ORT_BASE + "ort-wasm-simd-threaded.wasm",
};

var session = null;

self.onmessage = async function (e) {
  var type = e.data.type, id = e.data.id;

  if (type === "init") {
    try {
      session = await ort.InferenceSession.create(e.data.modelUrl, {
        executionProviders: ["wasm"],
        graphOptimizationLevel: "all",
      });
      self.postMessage({ type: "init", ok: true });
    } catch (err) {
      self.postMessage({ type: "init", ok: false, error: err.message });
    }
    return;
  }

  if (type === "infer") {
    try {
      var flatInputs = new Float32Array(e.data.flatInputs);
      var batchSize = e.data.batchSize;
      var tensor = new ort.Tensor("float32", flatInputs, [batchSize, 3]);
      var results = await session.run({ input: tensor });
      var outKey = Object.keys(results)[0];
      var full = results[outKey].data;               // batchSize * nSpecies
      var nSpecies = full.length / batchSize;
      var task = e.data.task || "raw";
      var out, b, base, s;

      if (task === "column") {
        var idx = e.data.speciesIdx;
        out = new Float32Array(batchSize);
        for (b = 0; b < batchSize; b++) out[b] = full[b * nSpecies + idx];
      } else if (task === "richness") {
        var thr = e.data.threshold;
        var mask = e.data.mask ? new Uint8Array(e.data.mask) : null;
        // With a group mask, precompute the in-group species indices ONCE so each
        // cell counts only those (~one taxonomic class) instead of walking all
        // nSpecies — far fewer comparisons for a single-group richness sweep.
        var idxs = null;
        if (mask) {
          idxs = [];
          for (s = 0; s < nSpecies; s++) if (mask[s]) idxs.push(s);
        }
        out = new Float32Array(batchSize);
        for (b = 0; b < batchSize; b++) {
          base = b * nSpecies;
          var count = 0;
          if (idxs) {
            for (s = 0; s < idxs.length; s++) { if (full[base + idxs[s]] >= thr) count++; }
          } else {
            for (s = 0; s < nSpecies; s++) { if (full[base + s] >= thr) count++; }
          }
          out[b] = count;
        }
      } else {
        out = new Float32Array(full);                 // copy out of ORT buffer
      }
      self.postMessage({ type: "infer", id: id, data: out.buffer }, [out.buffer]);
    } catch (err) {
      self.postMessage({ type: "infer", id: id, error: err.message });
    }
  }
};
