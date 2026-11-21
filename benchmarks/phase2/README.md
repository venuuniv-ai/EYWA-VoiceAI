# Connect validation evidence

The full real-audio Connect measurements are included in
[`../phase3/results/20261121T121417Z-d67271f5.json`](../phase3/results/20261121T121417Z-d67271f5.json)
and the [generated summary](../phase3/summary.md). The accepted model code is
identified by clean source commit `561f442633afaa261d900b385bb55909a8cc6d82`.

The final run exercises booking, changes, cancellation, retries, refusal cases,
DTMF, interruptions and injected database failure. It records 19 outgoing success
claims, zero false confirmations and zero duplicate booking records. The two
phone-codec scenarios have mean known-script WER 22.22%; the report specifies
normalization and retains actual recognized transcripts.

`tests/test_connect.py` and `tests/test_connect_gateway.py` separately verify
concurrent idempotent writes, caller authorization, key/payload conflicts, stale
write responses, fully heard read-back requirements, change-of-mind/model-error
guards, rejected schedules, commit failures, DTMF tone decoding, call transitions,
non-accumulating ingress latency, discarded hold epochs, HttpOnly capabilities,
and evaluation cancellation ownership. Dashboard tests use explicitly labelled
mocks; the real recorded browser booking uses native inference.

Ingress network perturbations are separate from the codec ASR measurement.
Neither the phone simulator nor transfer connects to a real telephone network.
