# Legacy two-ply Rust kernel evidence

The benchmark measures the complete `choose_discard` call, including Python
root filtering and explanation mapping. The native function has an internal
monotonic time guard, so an 8 ms profile returns transactionally rather than
running a full frontier after the deadline.

Run with a freshly built CPython 3.11 wheel unpacked under `/tmp/mj-kernel-test`:

```bash
PYTHONPATH=/tmp/mj-kernel-test:. \
  python3 scripts/legacy_two_ply_rust_bench.py --states 100 --seed 0
```

The current result does not pass the 8 ms completion gate. The native path is
therefore retained as opt-in and the existing legacy default is unchanged.
