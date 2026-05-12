# Testing

Install dependencies:

```bash
pip install -r requirements.txt
```

Run tests:

```bash
PYTHONPATH=. pytest -q
```

Run benchmark fixtures:

```bash
PYTHONPATH=. python -m workforce_scheduling.benchmark
```
