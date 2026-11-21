# 0005: Sequential admission and an explicit compact model profile

Date: 2026-11-19. Status: implemented; live validation results are in Phase 1 reports.

Update 2026-11-21: the longer 30-scenario Connect/Eval workload reached 640.875
MiB for compact Qwen and approximately 291 MiB for Piper (longer read-backs and
both speakers). The current admission budgets are therefore **680 MiB** and
**320 MiB**, respectively, including explicit margins. The raw run is
`benchmarks/phase3/results/20261121T121417Z-d67271f5.json`. Earlier values below
are historical measurements/policy, not the current budgets. The 512 MiB reserve
is unchanged. Total observed process-peak upper bound remained below 2.5 GB.

## Context

The initial combined guard added isolated RSS peaks and required that entire sum
plus 512 MB to be available before any stage could start. That prevented startup
even though the existing models could run on this machine. RSS includes clean
file-backed pages and shared libraries; it is a useful process-budget metric but
does not equal the incremental reduction in system available memory.

A diagnostic on this host admitted each model sequentially with the original
512 MB reserve. Available RAM fell from 2,089 MB to 1,322 MB after loading the
LLM, while the group's RSS was already 1,312 MB. All four stages loaded, with
1,063 MB still available. A subsequent 21-turn real pipeline run succeeded.
These observations justify changing the aggregate preflight, not reducing the
reserve or ignoring a per-model refusal.

Available RAM later fell to about 1.3 GB because this is a shared 8 GB machine.
Even the first 1.5B-model load was then correctly refused. Requiring the user to
close other apps for every demonstration is a practical limitation.

## Decision

1. Admit **each next model against current available RAM**, using its measured
   requirement and the same 512 MB reserve. If a later load is refused, unwind
   startup and reap all earlier owned processes. There is no force-load switch.
2. Retain `balanced` as the default: Qwen2.5-1.5B-Instruct Q4_K_M, 4,096 context,
   one slot, Metal. Its admission requirement remains 1,313 MB.
3. Add an **explicit** `compact` profile selected with
   `EYWA_MODEL_PROFILE=compact make dev`. It uses the official
   Qwen2.5-0.5B-Instruct Q4_K_M artifact, also with 4,096 context and one slot.
   The startup log, health response, browser badge, and benchmark metadata identify
   the profile. A refusal never silently changes models.
4. Pin the compact GGUF to upstream commit
   `9217f5db79a29953eb74d5343926648285ec7e67`, SHA-256
   `74a4da8c9fdbcd15bd1f6d01d621410d31c6fc00986f5eb687824e7b93d7a9db`.
   The existing download script verifies it. No runtime downloads or cloud
   inference are introduced.
5. Measure the new model alone first. The profiling-only initial run guards the
   live reserve continuously because no measured RSS requirement exists yet.
   Production then uses the actual results: 606.14 MB isolated peak, 616.08 MB in
   the first pipeline run. The **admission budget is 640 MB**, including a chosen
   margin above those observations. This budget is a policy value, not a claimed
   benchmark result.
6. Keep the 2.5 GB combined peak-RSS target independent of admission. Benchmark
   owned processes with macOS `/usr/bin/time -l`; use gateway `RUSAGE_SELF`
   separately because parent `time` accounting can include descendants. Sum
   individual peaks as a conservative upper bound, and retain 100 ms samples.

## Alternatives and tradeoffs

- Keeping the aggregate preflight makes an otherwise working profile unavailable.
- Lowering the reserve or force-loading would hide the real constraint.
- Replacing the default 1.5B model would conceal a model-quality change. Explicit
  profiles preserve comparison and reproducible results.
- The 0.5B model has less capacity. Compact latency or task performance must be
  measured independently; do not imply equivalent reasoning or workflow accuracy.
- Profiling adds wrappers only when `EYWA_PROFILE_DIR` is set by the benchmark.
  Normal application startup does not use `/usr/bin/time`.

The model is from the [official Qwen repository](https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF),
which identifies its Apache-2.0 license. No new inference framework is added.
