# One worker, many threads: keeps request concurrency for this I/O-bound app while
# giving the in-process rate limiter a single shared store (no Redis dependency).
web: gunicorn app:app --bind 0.0.0.0:${PORT:-8789} --workers 1 --threads 8 --timeout 120
