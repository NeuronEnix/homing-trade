"""External research signals (Phase 6): news/sentiment/derivatives/on-chain feeds that enrich the
AI context. Every fetch is cached in SQLite (signal_cache) with fetched_at and degrades to
"signal unavailable" rather than crashing the trading loop."""
