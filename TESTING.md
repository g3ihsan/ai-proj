# Testing

Install dependencies:

```bash
pip install -r requirements.txt
```

Run tests:

```bash
PYTHONPATH=. pytest -q
```

Run a JSON solve request through the file boundary:

```bash
PYTHONPATH=. python -m workforce_scheduling.cli --request-json request.json --response-json response.json
```

Run benchmark fixtures:

```bash
PYTHONPATH=. python -m workforce_scheduling.benchmark
PYTHONPATH=. python -m workforce_scheduling.benchmark --json
```

Compare benchmark fixtures without and with warm-start hints:

```bash
PYTHONPATH=. python -m workforce_scheduling.benchmark --compare-warm-start
PYTHONPATH=. python -m workforce_scheduling.benchmark --compare-warm-start --json
```

Run manual scaling benchmark fixtures:

```bash
PYTHONPATH=. python -m workforce_scheduling.benchmark --scaling
PYTHONPATH=. python -m workforce_scheduling.benchmark --scaling --compare-warm-start
```

Benchmark interpretation:

- Benchmarks are deterministic fixtures, not production capacity guarantees.
- `FEASIBLE` means CP-SAT found a valid incumbent inside the time limit but did not prove optimality.
- Use `best_bound`, absolute gap, and relative gap percent to judge remaining optimality uncertainty.
- Warm-start comparison reports observed facts only; hints can help, hurt, or be neutral.
- The 80- and 120-employee scaling cases are intended for manual runs, not normal pytest coverage.
- See `SOLVER_LIMITS.md` for the current benchmark reporting contract and operating-limit notes.
