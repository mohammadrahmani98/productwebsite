# Peptide Design CRDT Ledger

This project implements a training‑free [CRDT](https://en.wikipedia.org/wiki/Conflict-free_replicated_data_type)
ledger for recording peptide‑design decisions.  Each event is immutable and
addressed by the BLAKE2s hash of its canonical JSON representation.  Optional
message authentication codes (MACs) may be attached using a shared secret.

## Files

- `ledger_cli.py` – command line interface
- `rules_catalog.json` – example rule catalog
- `ledger_a.jsonl`, `ledger_b.jsonl` – sample ledgers

## Install

Requires **Python 3.8+** only.  No external packages are used.

## Quickstart

Initialize a ledger and append an event:

```bash
python ledger_cli.py init myledger.jsonl
python ledger_cli.py append myledger.jsonl \
    --peptide P123 --decision accept --reason RC307,RC205 \
    --metrics '{"dG":-7.1,"clash":9.0}' \
    --replica labX --author "labX/alex"
```

List events:

```bash
python ledger_cli.py list myledger.jsonl
```

Merge ledgers and verify integrity:

```bash
python ledger_cli.py merge merged.jsonl ledger_a.jsonl ledger_b.jsonl
python ledger_cli.py verify merged.jsonl
```

Replay with a rule catalog to obtain final peptide decisions:

```bash
python ledger_cli.py replay merged.jsonl --rules rules_catalog.json
```

Generate a Graphviz representation of the event DAG:

```bash
python ledger_cli.py graph merged.jsonl > graph.dot
# dot -Tpng graph.dot -o graph.png   # requires Graphviz
```

## Event Schema

Each line in a ledger JSONL file is an event with the following structure:

```json
{
  "peptide_id": "P123",
  "decision": "accept",
  "reason_codes": ["RC307", "RC205"],
  "metrics": {"dG": -7.1, "clash": 9.0},
  "clock": [1, "labX"],
  "parents": ["<parent_event_id>"] ,
  "author": "labX/alex",
  "ts": 1680000000.0,
  "event_id": "<hash>",
  "sig": "<mac>"   // optional
}
```

The `event_id` is the BLAKE2s hash of the canonical JSON representation of all
fields except `event_id` and `sig`.  If a key is supplied, `sig` is the BLAKE2s
MAC of the same canonical JSON.

## Rule Catalog

Rules specify numeric predicates that each reason code must satisfy.  Example
(`rules_catalog.json`):

```json
{
  "RC307": {
    "description": "Binding energy threshold",
    "all_of": [ {"metric": "dG", "op": "<=", "value": -6.0} ]
  },
  "RC205": {
    "description": "Clash threshold",
    "all_of": [ {"metric": "clash", "op": "<", "value": 10.0} ]
  }
}
```

During replay, an event is valid only if all of its reason codes exist in the
catalog and their predicates evaluate to true.  The last valid event for a
peptide determines its final decision.
