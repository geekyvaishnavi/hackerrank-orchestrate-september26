# Evaluation

Run `python3 evaluation/evaluate_samples.py --dataset-dir dataset` to inventory public sample comparability without adding ID-specific behavior. Sample rows are not evaluation requests and can lack matching payment-option context; their residuals are reported explicitly.

For the full deterministic smoke test, generate two outputs with separate audit directories, validate both with `--validate-output`, compare SHA-256 hashes, and compare audit directories. Invariants are covered by the automated tests: bounded safe amount, plan chronology, protected-spend restrictions, and output completeness.
