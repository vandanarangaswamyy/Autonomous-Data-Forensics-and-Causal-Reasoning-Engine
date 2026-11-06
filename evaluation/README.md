# Simulator benchmarks

Three reproducible scenario definitions and six configurable fault examples accompany
the evaluator-only private registry in src/atlas_evaluation. See docs/simulator.md.
Do not provide scenario definitions, operator commands or private oracle files to agents.
This milestone verifies simulator ground truth and symptoms; agent scoring and RAGAS
integration remain unimplemented.

Source extraction corpus: `nlp/source-v1` contains authored PDF fixtures and independent
manual page/normalization/status labels. Results are in `results/source-extraction-v1.json`.
This evaluates Phase 3.2a source fidelity only; no NLP entity/relationship/resolution or
retrieval metrics are available. Run `uv run --locked python -m scripts.evaluate_source_extraction`.
